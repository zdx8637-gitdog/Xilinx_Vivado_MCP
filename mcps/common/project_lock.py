r"""
project_lock.py — Thread-safe OS-level resource lock with metadata sidecar.

Concurrency:
  heartbeat: acquires registry RLock → validate → replace authoritative lease
             → _write_meta → release RLock → return.
  release:   acquires same RLock → validate → mark "releasing" → release RLock
             → _del_meta (best-effort) → unlock OS handle → close → re-acquire RLock
             → pop from registry → done.
  heartbeat begun before release mark = heartbeat writes metadata.
  release after "releasing" mark = heartbeat rejected.

Metadata strategy (B):
  Sidecar only for exclusive writers. os.replace() used for atomic metadata
  updates only. Manifest publishing NEVER uses os.replace.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import platform
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_IS_WINDOWS = platform.system() == "Windows"

# ---- Configurable lock directory ----

_lock_dir: str | None = None
_lock = threading.RLock()

_active: dict[str, _ActiveLease] = {}
_pending: set[tuple[str, str]] = set()


def set_lock_dir(path: str) -> None:
    global _lock_dir
    p = str(Path(path).resolve(strict=False))
    os.makedirs(p, exist_ok=True)
    # Check and assign in single critical section — no TOCTOU window
    with _lock:
        if _active or _pending:
            raise RuntimeError("Cannot change lock_dir while active leases exist")
        _lock_dir = p


def _get_lock_dir() -> str:
    global _lock_dir
    with _lock:
        if _lock_dir is not None:
            return _lock_dir
        base = os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), ".local", "share"))
        d = os.path.join(base, "zynq_mcp", "locks")
        os.makedirs(d, exist_ok=True)
        _lock_dir = d
    return _lock_dir


# ---- Resource key → lock file path ----

def _canonical_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lock_file_path(h: str) -> str:
    return os.path.join(_get_lock_dir(), f"rlock_{h}")


# ---- Public data types ----

@dataclass(frozen=True)
class Lease:
    lease_id: str
    owner_session_id: str
    lock_key: str
    scope: str
    mode: str
    ttl_s: int
    acquired_at: str
    heartbeat_at: str
    pid: int


@dataclass(frozen=True)
class LockAcquireResult:
    status: str
    lease: Lease | None = None


# ---- Internal active lease ----

class _ActiveLease:
    __slots__ = (
        "lease", "canonical_key", "lock_path", "meta_path",
        "os_handle", "state",
    )

    def __init__(self, lease: Lease, canonical_key: str,
                 lock_path: str, meta_path: str, os_handle: Any) -> None:
        self.lease = lease
        self.canonical_key = canonical_key
        self.lock_path = lock_path
        self.meta_path = meta_path
        self.os_handle = os_handle
        self.state: str = "active"


# ---- Token validation ----

_NON_HEARTBEAT_FIELDS = (
    "lease_id", "owner_session_id", "lock_key", "scope", "mode",
    "ttl_s", "acquired_at", "pid",
)


def _validate_lease_token(input_lease: Lease, active: _ActiveLease) -> list[str]:
    issues = []
    al = active.lease
    for field in _NON_HEARTBEAT_FIELDS:
        iv = getattr(input_lease, field, None)
        av = getattr(al, field, None)
        if iv != av:
            issues.append(f"{field}: input={iv!r} active={av!r}")
    return issues


# ---- Windows lock primitives ----

if _IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _OV(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.LockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OV)]
    _k32.LockFileEx.restype = wintypes.BOOL
    _k32.UnlockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.POINTER(_OV)]
    _k32.UnlockFileEx.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL

    GENERIC_RW = 0xC0000000
    FILE_SHARE_ALL = 0x00000007
    OPEN_ALWAYS = 4
    FILE_ATTR_NORMAL = 0x80
    LOCK_EXCLUSIVE = 0x00000002
    LOCK_FAIL = 0x00000001

else:
    import fcntl


class _OsLockHandle:
    __slots__ = ("_h",)

    def __init__(self, path: str) -> None:
        if _IS_WINDOWS:
            h = _k32.CreateFileW(
                path, GENERIC_RW, FILE_SHARE_ALL, None,
                OPEN_ALWAYS, FILE_ATTR_NORMAL, None)
            if h == wintypes.HANDLE(-1).value:
                raise OSError(f"CreateFileW failed: {ctypes.get_last_error()}")
            self._h = h
        else:
            self._h = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)

    def acquire_os(self, mode: str, wait: bool) -> bool:
        if _IS_WINDOWS:
            flags = LOCK_EXCLUSIVE if mode == "write" else 0
            if not wait:
                flags |= LOCK_FAIL
            ov = _OV()
            ok = _k32.LockFileEx(self._h, flags, 0, 1, 0, ctypes.byref(ov))
            if not ok:
                err = ctypes.get_last_error()
                if err in (33, 232):
                    return False
                raise OSError(f"LockFileEx failed: error {err}")
            return True
        else:
            op = fcntl.LOCK_EX if mode == "write" else fcntl.LOCK_SH
            if not wait:
                op |= fcntl.LOCK_NB
            try:
                fcntl.flock(self._h, op)
                return True
            except BlockingIOError:
                return False

    def release_os(self) -> None:
        if _IS_WINDOWS:
            ov = _OV()
            _k32.UnlockFileEx(self._h, 0, 1, 0, ctypes.byref(ov))
        else:
            fcntl.flock(self._h, fcntl.LOCK_UN)

    def close(self) -> None:
        try:
            if _IS_WINDOWS:
                _k32.CloseHandle(self._h)
            else:
                os.close(self._h)
        except OSError:
            pass


# ---- Metadata I/O ----

def _write_meta(path: str, lease: Lease) -> None:
    data = {
        "lease_id": lease.lease_id,
        "owner_session_id": lease.owner_session_id,
        "lock_key": lease.lock_key,
        "scope": lease.scope, "mode": lease.mode,
        "ttl_s": lease.ttl_s,
        "acquired_at": lease.acquired_at,
        "heartbeat_at": lease.heartbeat_at,
        "pid": lease.pid,
    }
    tmp = path + ".tmp." + uuid.uuid4().hex[:8]
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _read_meta(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None


def _del_meta(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


# ---- Lease factory ----

def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + \
           f"{int(time.time() * 1e6) % 1000000:06d}Z"


def _make_lease(canonical_key: str, owner: str,
                scope: str, mode: str, ttl_s: int) -> Lease:
    now = _now_utc()
    return Lease(
        lease_id=str(uuid.uuid4()),
        owner_session_id=owner,
        lock_key=canonical_key,
        scope=scope, mode=mode, ttl_s=ttl_s,
        acquired_at=now, heartbeat_at=now, pid=os.getpid())


# ---- Validation helpers ----

def _check_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _check_float(name: str, value: float) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got bool")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{name} must be >= 0, got {value}")
        return
    if not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


# ---- Core acquire ----

def _acquire_resource(
    canonical_key: str, owner: str, scope: str,
    mode: str, ttl_s: int, wait_s: float = 0,
) -> LockAcquireResult:
    _check_nonempty("canonical_key", canonical_key)
    _check_nonempty("owner", owner)
    _check_nonempty("scope", scope)
    if mode not in ("read", "write"):
        raise ValueError(f"mode must be 'read' or 'write'")
    _check_float("wait_s", wait_s)

    if mode == "write":
        if isinstance(ttl_s, bool) or not isinstance(ttl_s, int) or ttl_s <= 0:
            raise ValueError(f"ttl_s must be a positive integer, got {ttl_s!r}")
    else:
        ttl_s = 0

    chash = _canonical_hash(canonical_key)
    lpath = _lock_file_path(chash)
    mpath = lpath + ".meta"
    os.makedirs(os.path.dirname(lpath), exist_ok=True)

    with _lock:
        for al in _active.values():
            if (al.state != "released" and
                    al.canonical_key == canonical_key and
                    al.lease.owner_session_id == owner):
                return LockAcquireResult(status="busy")
        if (canonical_key, owner) in _pending:
            return LockAcquireResult(status="busy")
        _pending.add((canonical_key, owner))

    lh = _OsLockHandle(lpath)
    deadline = time.monotonic() + wait_s if wait_s > 0 else 0

    try:
        while True:
            if lh.acquire_os(mode, wait=False):
                break
            if wait_s == 0:
                lh.close()
                return LockAcquireResult(status="busy")
            if time.monotonic() >= deadline:
                lh.close()
                return LockAcquireResult(status="timeout")
            time.sleep(0.05)

        lease = _make_lease(canonical_key, owner, scope, mode, ttl_s)
        al = _ActiveLease(lease, canonical_key, lpath, mpath, lh)

        if mode == "write":
            _write_meta(mpath, lease)

        with _lock:
            _active[lease.lease_id] = al
        return LockAcquireResult(status="acquired", lease=lease)
    except Exception:
        lh.close()
        raise
    finally:
        with _lock:
            _pending.discard((canonical_key, owner))


# ---- Release (serialized against heartbeat via registry RLock) ----

def release(lease: Lease) -> None:
    if not isinstance(lease, Lease):
        raise TypeError("lease must be Lease")

    # Phase 1: validate + mark releasing (under lock)
    with _lock:
        al = _active.get(lease.lease_id)
        if al is None or al.state == "released":
            raise RuntimeError("Cannot release: lease not found or already released")
        if al.state == "releasing":
            raise RuntimeError("Cannot release: release already in progress")
        issues = _validate_lease_token(lease, al)
        if issues:
            raise RuntimeError(f"Cannot release: {issues[0]}")
        al.state = "releasing"

    # Phase 2: outside lock — delete metadata (best-effort), free OS resources
    if al.lease.mode == "write":
        try:
            _del_meta(al.meta_path)
        except Exception:
            pass  # metadata cleanup failure does not prevent OS lock release

    try:
        al.os_handle.release_os()
    except Exception:
        pass
    al.os_handle.close()

    # Phase 3: pop from registry (under lock)
    with _lock:
        _active.pop(lease.lease_id, None)
        al.state = "released"


# ---- Heartbeat (holds registry RLock for entire operation) ----

def heartbeat(lease: Lease) -> Lease:
    if not isinstance(lease, Lease):
        raise TypeError("lease must be Lease")

    # Entire operation under registry RLock — serialized against release.
    with _lock:
        al = _active.get(lease.lease_id)
        if al is None or al.state != "active":
            raise RuntimeError(
                "Cannot heartbeat: lease not found or not active")
        if al.lease.mode == "read":
            raise RuntimeError("heartbeat not supported for read lease")
        issues = _validate_lease_token(lease, al)
        if issues:
            raise RuntimeError(f"Cannot heartbeat: {issues[0]}")

        now = _now_utc()
        new_lease = replace(al.lease, heartbeat_at=now)
        al.lease = new_lease
        _write_meta(al.meta_path, new_lease)
        return new_lease


# ---- Public project lock ----

_PROJECT_PREFIX = "project:"


def _project_canonical_key(project_path: str) -> str:
    _check_nonempty("project_path", project_path)
    p = str(Path(project_path).expanduser().resolve(strict=False))
    if _IS_WINDOWS:
        p = os.path.normcase(p)
    p = p.replace("\\", "/").rstrip("/")
    return _PROJECT_PREFIX + p


def acquire(lock_key: str, owner: str, scope: str = "vivado_project",
            ttl_s: int = 300, wait_s: float = 0) -> LockAcquireResult:
    return _acquire_resource(
        _project_canonical_key(lock_key), owner, scope, "write", ttl_s, wait_s)


def acquire_read(lock_key: str, owner: str = "",
                 scope: str = "vivado_project") -> LockAcquireResult:
    return _acquire_resource(
        _project_canonical_key(lock_key), owner or "reader", scope, "read", 0, 0)


# ---- Public JTAG lock ----

_JTAG_PREFIX = "jtag:"


def _jtag_canonical_key(hw_server_url: str, cable_serial: str) -> str:
    _check_nonempty("hw_server_url", hw_server_url)
    _check_nonempty("cable_serial", cable_serial)
    url = hw_server_url.strip().rstrip("/").lower()
    serial = cable_serial.strip()
    raw = f"{len(url)}:{url}:{len(serial)}:{serial}"
    return _JTAG_PREFIX + raw


def jtag_acquire(hw_server_url: str, cable_serial: str, owner: str,
                 ttl_s: int = 300, wait_s: float = 0) -> LockAcquireResult:
    key = _jtag_canonical_key(hw_server_url, cable_serial)
    return _acquire_resource(key, owner, "jtag", "write", ttl_s, wait_s)


def jtag_acquire_read(hw_server_url: str, cable_serial: str,
                      owner: str = "") -> LockAcquireResult:
    key = _jtag_canonical_key(hw_server_url, cable_serial)
    return _acquire_resource(key, owner or "reader", "jtag", "read", 0, 0)


# ---- Thread-safe lease enumeration (B04 Erratum E002) ----
# B04 R2 needs to release all leases held by a session without
# accessing _active private dict. These are the public entry points.

def list_leases_for_owner(owner_session_id: str) -> list:
    """Return list of Lease objects held by owner_session_id. Thread-safe.
    Order is deterministic: Project (vivado_project scope) before JTAG (jtag scope)."""
    project_leases = []
    jtag_leases = []
    with _lock:
        for al in _active.values():
            if (al.state != "released" and al.lease is not None
                    and al.lease.owner_session_id == owner_session_id):
                if al.lease.scope == "vivado_project":
                    project_leases.append(al.lease)
                elif al.lease.scope == "jtag":
                    jtag_leases.append(al.lease)
                else:
                    project_leases.append(al.lease)  # unknown → first
    return project_leases + jtag_leases


def release_lease_safe(lease: Lease) -> tuple[bool, str]:
    """Release a single lease. Returns (ok, error_or_ok_string).
    Does NOT raise — caller handles errors.
    """
    try:
        release(lease)
        return (True, "released")
    except Exception as e:
        return (False, str(e))
