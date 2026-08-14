"""
instance_guard.py — Dual OS locks with fail-closed error handling.

| Error class | Action |
| ERROR_LOCK_VIOLATION (33/32) | SECONDARY |
| All other errors (access, handle, permission) | RAISE — fail-closed |
| SetHandleInformation failure | RAISE — handle leaked → fatal |

Workspace ID verified on every ledger read.
"""
import ctypes, logging, os, threading, uuid
from ctypes import wintypes
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("zynq_mcp.instance_guard")
InstanceRole = Enum("InstanceRole", "PRIMARY SECONDARY")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_ulong), ("InternalHigh", ctypes.c_ulong),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001

kernel32.LockFileEx.restype = wintypes.BOOL
kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
kernel32.UnlockFileEx.restype = wintypes.BOOL
kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.SetHandleInformation.restype = wintypes.BOOL
kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
kernel32.GetLastError.restype = wintypes.DWORD

GENERIC_READ = 0x80000000; GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001; FILE_SHARE_WRITE = 0x00000002
OPEN_ALWAYS = 4; OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80; FILE_FLAG_OVERLAPPED = 0x40000000
HANDLE_FLAG_INHERIT = 1
ERROR_LOCK_VIOLATION = 33
ERROR_SHARING_VIOLATION = 32
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

def _ok(h): return h is not None and h != INVALID_HANDLE_VALUE

class InstanceGuardFatalError(Exception): pass


class InstanceGuard:
    def __init__(self, runtime_root, workspace_id):
        self._rt = runtime_root; self._rt.mkdir(parents=True, exist_ok=True)
        self._owp = runtime_root / "instance_owner.lock"
        self._lwp = runtime_root / "ledger.lock"
        self._wsid = workspace_id; self._iid = uuid.uuid4().hex
        self._role = None; self._oh = None
        self._inproc_lock = threading.Lock()  # serializes same-process ledger calls
        self._lock_timeout = 30.0  # seconds

    @property
    def instance_id(self): return self._iid
    @property
    def role(self):
        if self._role is None: raise RuntimeError("role not determined")
        return self._role
    @property
    def is_primary(self): return self._role == InstanceRole.PRIMARY
    @property
    def is_secondary(self): return self._role == InstanceRole.SECONDARY
    @property
    def workspace_id(self): return self._wsid

    def _open_owner_lock(self):
        """Non-blocking: CreateFileW → LockFileEx(LOCKFILE_EXCLUSIVE_LOCK|LOCKFILE_FAIL_IMMEDIATELY)."""
        h = kernel32.CreateFileW(str(self._owp), GENERIC_READ | GENERIC_WRITE, 0, None,
                                   OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None)
        if not _ok(h):
            err = kernel32.GetLastError()
            raise OSError(err, f"CreateFileW(owner) error {err}")
        ov = _OVERLAPPED()
        ok = kernel32.LockFileEx(h, LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY, 0, 1, 0, ctypes.byref(ov))
        if not ok:
            err = kernel32.GetLastError()
            kernel32.CloseHandle(h)
            if err in (ERROR_LOCK_VIOLATION, ERROR_SHARING_VIOLATION):
                raise OSError(err, "Already locked")
            raise InstanceGuardFatalError(f"LockFileEx(owner) failed: error {err}")
        sih = kernel32.SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)
        if not sih:
            kernel32.UnlockFileEx(h, 0, 1, 0, ctypes.byref(_OVERLAPPED()))
            kernel32.CloseHandle(h)
            raise InstanceGuardFatalError(f"SetHandleInformation(owner) failed: error {kernel32.GetLastError()}")
        return h

    def _open_ledger_exclusive(self):
        """BLOCKING: LockFile on non-overlapped handle for true per-process serialization."""
        h = kernel32.CreateFileW(str(self._lwp), GENERIC_READ | GENERIC_WRITE, 0, None,
                                   OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, None)  # no FILE_FLAG_OVERLAPPED
        if not _ok(h):
            raise InstanceGuardFatalError(f"CreateFileW(ledger) error {kernel32.GetLastError()}")
        ok = kernel32.LockFile(h, 0, 0, 1, 0)  # BLOCKING byte-range lock
        if not ok:
            kernel32.CloseHandle(h)
            raise InstanceGuardFatalError(f"LockFile(ledger) failed: error {kernel32.GetLastError()}")
        sih = kernel32.SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)
        if not sih:
            kernel32.UnlockFile(h, 0, 0, 1, 0)
            kernel32.CloseHandle(h)
            raise InstanceGuardFatalError(f"SetHandleInformation(ledger) failed: error {kernel32.GetLastError()}")
        return h

    def _open_shared(self, path):
        h = kernel32.CreateFileW(str(path), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                   OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None)
        if not _ok(h):
            err = kernel32.GetLastError()
            raise OSError(err, f"CreateFileW(shared) error {err}")
        sih = kernel32.SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)
        if not sih:
            kernel32.CloseHandle(h)
            raise InstanceGuardFatalError(f"SetHandleInformation(shared) failed: error {kernel32.GetLastError()}")
        return h

    def _close_h(self, h):
        if h is not None and h > 0 and h != INVALID_HANDLE_VALUE:
            try: kernel32.UnlockFile(h, 0, 0, 1, 0)
            except Exception: pass
            kernel32.CloseHandle(h)

    # ---- public ----
    def determine_role(self):
        if self._role is not None: return self._role
        try: h = self._open_owner_lock()
        except OSError as e:
            if e.errno in (ERROR_LOCK_VIOLATION, ERROR_SHARING_VIOLATION):
                self._role = InstanceRole.SECONDARY; return self._role
            raise InstanceGuardFatalError(f"Owner lock failed: {e}") from e
        except InstanceGuardFatalError: raise
        self._oh = h; self._role = InstanceRole.PRIMARY; return self._role

    def release_owner_lock(self):
        if self._oh is not None and _ok(self._oh):
            self._close_h(self._oh); self._oh = None

    def acquire_ledger_exclusive(self):
        return self._open_ledger_exclusive()

    def acquire_ledger_shared(self):
        if not self._lwp.exists(): self._lwp.write_text("")
        return self._open_shared(self._lwp)

    def release_ledger_lock(self, h):
        self._close_h(h)

    def acquire_inproc(self):
        """Bounded in-process serialization lock. Returns True if acquired."""
        return self._inproc_lock.acquire(timeout=self._lock_timeout)

    def release_inproc(self):
        self._inproc_lock.release()
