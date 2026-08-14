"""
board_package.py — Board Configuration Package validation and integrity.

Fail-closed: every validation function raises or returns structured issues with
no silent accept.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
import time
from typing import Any

from mcps.common.revision import compute_revision, sha256_file, is_sha256, canonical_json

# Package-scoped lock for freeze_package serialization.
# Uses OS-level byte-range locks (LockFileEx on Windows, fcntl.flock on POSIX)
# — same proven primitives as B02 project_lock._OsLockHandle.
# Lock files live OUTSIDE the board package directory to avoid EXTRA_FILE_IN_DIR.
#
# Crash safety: OS kernel releases the lock when the holding process dies.
# The lock file may persist on disk but the lock is released —
# next process acquires successfully even without deleting the file.
_PKG_LOCK_BASE = None


def _get_pkg_lock_dir() -> str:
    """Return the directory for package-scoped lock carrier files."""
    global _PKG_LOCK_BASE
    if _PKG_LOCK_BASE is not None:
        return _PKG_LOCK_BASE
    env = os.environ.get("ZYNQ_PKG_LOCK_DIR", "")
    if env and os.path.isdir(env):
        _PKG_LOCK_BASE = env
        return env
    from pathlib import Path
    ws = Path(__file__).resolve().parent.parent.parent
    base = ws / ".zynq_runtime" / ".pkg_locks"
    base.mkdir(parents=True, exist_ok=True)
    _PKG_LOCK_BASE = str(base)
    return _PKG_LOCK_BASE


def _pkg_lock_path(package_dir: str) -> str:
    """Deterministic lock carrier file path.
    Canonical key: expanduser→resolve(strict=False)→normpath→normcase→SHA256.
    Same logical directory maps to same lock key regardless of separator, case, or cwd."""
    from pathlib import Path
    resolved = Path(package_dir).expanduser().resolve(strict=False)
    canonical = os.path.normcase(os.path.normpath(str(resolved)))
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return os.path.join(_get_pkg_lock_dir(), f"pkg_lock_{h}")


class _PackageLock:
    """OS-level exclusive lock using byte-range locking (B02 project_lock primitives)."""
    """OS-level exclusive lock using byte-range locking (B02 project_lock primitives).

    State tracked by three fields:
      _fd               — OS handle, None only when fully clean
      _owns_lock         — True when LockFileEx/flock byte-range lock is held
      _cleanup_required  — True when handle is open but lock was never acquired
                            (e.g. lock+close double-failure in acquire)
    acquire() rejects if _cleanup_required or _fd is not None.
    cleanup() closes handle without unlocking (for lock-never-acquired case).
    release() unlocks-then-closes (for normal acquired case).
    """
    def __init__(self, package_dir: str, timeout_s: float = 30.0):
        self._path = _pkg_lock_path(package_dir)
        self._fd = None
        self._timeout = timeout_s
        self._owns_lock = False
        self._cleanup_required = False

    def acquire(self) -> None:
        """Acquire OS exclusive lock with bounded wait."""
        if self._owns_lock:
            raise RuntimeError("Lock already acquired — call release() first")
        if self._cleanup_required or self._fd is not None:
            raise RuntimeError(
                "Lock not clean — call cleanup() first: "
                f"fd={self._fd is not None} cleanup_required={self._cleanup_required}")

        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._fd = self._os_open()
            except Exception:
                self._fd = None
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"Package lock timeout after {self._timeout:.1f}s: {self._path}")
                time.sleep(0.05)
                continue
            try:
                ok = self._os_lock()
            except BaseException as lock_err:
                # Lock failed with exception — try to close the handle
                try:
                    self._os_close(self._fd)
                except Exception as close_err:
                    # BOTH failed: retain fd for explicit cleanup
                    self._cleanup_required = True
                    raise OSError(
                        f"Lock+close both failed: lock={lock_err}, close={close_err}"
                    ) from lock_err
                # close succeeded — fd is clean
                self._fd = None
                raise
            if ok:
                self._owns_lock = True
                return
            # Lock busy (LockFileEx returned 0, no exception) — close and retry
            self._os_close(self._fd)
            self._fd = None
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Package lock timeout after {self._timeout:.1f}s: {self._path}")
            time.sleep(0.05)

    def cleanup(self) -> None:
        """Close handle when lock was never acquired (cleanup_required state).
        Does NOT call UnlockFileEx — lock was never successfully acquired."""
        if self._fd is None:
            self._cleanup_required = False
            return
        try:
            self._os_close(self._fd)
        except Exception as e:
            raise OSError(f"cleanup close failed: {e}")
        self._fd = None
        self._cleanup_required = False

    def release(self) -> None:
        """Release OS lock and close handle. A/B/C/D state classification."""
        if self._fd is None and not self._cleanup_required:
            return  # idempotent no-op (already cleanly released)
        if self._cleanup_required:
            raise OSError(
                "Cannot release: lock was never acquired. Call cleanup() to close the handle.")
        if not self._owns_lock:
            raise OSError("Cannot release: no lock held.")

        unlock_err = None
        close_err = None
        fd_val = self._fd
        try:
            self._os_unlock()
        except Exception as e:
            unlock_err = e
        try:
            self._os_close(fd_val)
        except Exception as e:
            close_err = e

        if unlock_err is None and close_err is None:
            self._fd = None
            self._owns_lock = False
            return

        if unlock_err is not None and close_err is None:
            self._fd = None
            self._owns_lock = False
            raise OSError(f"Package unlock failed (handle closed): {unlock_err}")

        if unlock_err is None and close_err is not None:
            self._owns_lock = False
            self._cleanup_required = True
            raise OSError(f"Package close failed (unlock OK): {close_err}")

        self._owns_lock = False
        self._cleanup_required = True
        raise OSError(
            f"Package release both failed: unlock={unlock_err}, close={close_err}")

    # -- OS-level primitives (same pattern as B02 project_lock._OsLockHandle) --

    def _os_open(self):
        if os.name == "nt":
            import ctypes as _ct
            from ctypes import wintypes as _w
            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            k32.CreateFileW.argtypes = [_w.LPCWSTR, _w.DWORD, _w.DWORD,
                _w.LPVOID, _w.DWORD, _w.DWORD, _w.HANDLE]
            k32.CreateFileW.restype = _w.HANDLE
            h = k32.CreateFileW(str(self._path), 0xC0000000, 3, None,
                4, 0x80, None)  # GENERIC_RW, FILE_SHARE_READ|FILE_SHARE_WRITE, OPEN_ALWAYS, FILE_ATTR_NORMAL
            if h == _w.HANDLE(-1).value:
                raise OSError(f"CreateFileW failed: {_ct.get_last_error()}")
            return h
        else:
            import fcntl
            return os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)

    def _os_lock(self) -> bool:
        """Non-blocking exclusive lock. Returns True if acquired, False if already held."""
        if os.name == "nt":
            import ctypes as _ct
            from ctypes import wintypes as _w
            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            class _OV(_ct.Structure):
                _fields_ = [("Internal", _ct.c_void_p), ("InternalHigh", _ct.c_void_p),
                    ("Offset", _w.DWORD), ("OffsetHigh", _w.DWORD), ("hEvent", _w.HANDLE)]
            k32.LockFileEx.argtypes = [_w.HANDLE, _w.DWORD, _w.DWORD,
                _w.DWORD, _w.DWORD, _ct.POINTER(_OV)]
            k32.LockFileEx.restype = _w.BOOL
            flags = 0x00000002 | 0x00000001  # LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY
            ov = _OV()
            ok = k32.LockFileEx(self._fd, flags, 0, 1, 0, _ct.byref(ov))
            if not ok:
                err = _ct.get_last_error()
                if err in (33, 232):  # ERROR_LOCK_VIOLATION, ERROR_PIPE_BUSY
                    return False
                raise OSError(f"LockFileEx failed: error {err}")
            return True
        else:
            import fcntl
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                return False

    def _os_unlock(self) -> None:
        if os.name == "nt":
            import ctypes as _ct
            from ctypes import wintypes as _w
            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            class _OV(_ct.Structure):
                _fields_ = [("Internal", _ct.c_void_p), ("InternalHigh", _ct.c_void_p),
                    ("Offset", _w.DWORD), ("OffsetHigh", _w.DWORD), ("hEvent", _w.HANDLE)]
            k32.UnlockFileEx.argtypes = [_w.HANDLE, _w.DWORD, _w.DWORD,
                _w.DWORD, _ct.POINTER(_OV)]
            k32.UnlockFileEx.restype = _w.BOOL
            ov = _OV()
            ok = k32.UnlockFileEx(self._fd, 0, 1, 0, _ct.byref(ov))
            if not ok:
                raise OSError(f"UnlockFileEx failed: {_ct.get_last_error()}")
        else:
            import fcntl
            fcntl.flock(self._fd, fcntl.LOCK_UN)

    @staticmethod
    def _os_close(fd) -> None:
        if os.name == "nt":
            import ctypes as _ct
            from ctypes import wintypes as _w
            k32 = _ct.WinDLL("kernel32", use_last_error=True)
            k32.CloseHandle.argtypes = [_w.HANDLE]
            k32.CloseHandle.restype = _w.BOOL
            ok = k32.CloseHandle(fd)
            if not ok:
                raise OSError(f"CloseHandle failed: error {_ct.get_last_error()}")
        else:
            os.close(fd)

CURRENT_SCHEMA = "1.0"
VALID_MANIFEST_TYPE = "board_configuration"
MANIFEST_NAMES = ("package_manifest.json", "package_manifest.draft.json")

EXACT_CONTENT_FILES = [
    "board_profile_{board_id}.json",
    "ps7_preset.tcl",
    "board.xdc",
    "SOURCES.md",
    "README.md",
]

_REQUIRED_FIELDS = {
    "schema_version", "manifest_type", "board_id",
    "package_version", "status",
    "manifest_revision", "revision_inputs",
    "generated_at", "files",
}

_REQUIRED_REVISION_INPUTS = [
    "board_profile_sha256",
    "ps7_preset_sha256",
    "board_xdc_sha256",
    "sources_md_sha256",
    "readme_md_sha256",
]

_STRING_FIELDS = _REQUIRED_FIELDS - {"revision_inputs", "files"}

_VALID_STATUS = ("draft", "locked")
_VALID_POLARITY = ("active-low", "active-high")


class ValidationIssue(Exception):
    def __init__(self, code: str, field: str | None = None,
                 expected: Any = None, actual: Any = None):
        self.code = code
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(code)


# ── Priority order for reason_code selection ───────────────────────────

_REASON_PRIORITY = [
    "INVALID_JSON",
    "MANIFEST_SELF_REFERENCE",
    "BOARD_ID_MISMATCH",
    "MISSING_FILE_IN_MANIFEST",
    "EXTRA_FILE_IN_MANIFEST",
    "EXTRA_FILE_IN_DIR",
    "BAD_REVISION",
    "SHA_CROSS_REF_MISMATCH",
    "PRESET_SHA256_MISMATCH",
    "XDC_SHA256_MISMATCH",
    "SHA256_MISMATCH",
    "PATH_NOT_FOUND",
    "ABSOLUTE_PATH_FORBIDDEN",
    "MISSING_REQUIRED_FIELD",
    "INVALID_TYPE",
    "INVALID_SHA256",
    "BAD_PATH",
    "DUPLICATE_PATH",
    "UNSUPPORTED_SCHEMA",
    "MANIFEST_TYPE_MISMATCH",
    "MISSING_FIELD",
]


def _pick_reason_code(issues: list[ValidationIssue]) -> str:
    """Pick the highest-priority reason_code from a list of issues."""
    for code in _REASON_PRIORITY:
        for i in issues:
            if i.code == code:
                return i.code
    return issues[0].code if issues else "UNKNOWN"


def _pick_reason_code_for_package_errors(issues: list[ValidationIssue]) -> str:
    """Pick reason_code, preferring SHA/REVISION codes for ARTIFACT_STALE."""
    return _pick_reason_code(issues)


# ── Path security ──────────────────────────────────────────────────────

def _safe_resolve_path(package_dir: str, rel_path: str,
                        field_label: str = "path") -> str:
    if not isinstance(rel_path, str) or not rel_path.strip():
        raise ValidationIssue(
            "BAD_PATH", field_label,
            "(non-empty relative path)", repr(rel_path))
    if "\\" in rel_path:
        raise ValidationIssue(
            "ABSOLUTE_PATH_FORBIDDEN", field_label,
            "(POSIX forward slash)", rel_path)
    if rel_path.startswith("/"):
        raise ValidationIssue(
            "ABSOLUTE_PATH_FORBIDDEN", field_label,
            "(relative path)", rel_path)
    if len(rel_path) >= 2 and rel_path[1] == ":":
        raise ValidationIssue(
            "ABSOLUTE_PATH_FORBIDDEN", field_label,
            "(relative path)", rel_path)
    parts = [p for p in rel_path.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ValidationIssue(
            "ABSOLUTE_PATH_FORBIDDEN", field_label,
            "(no .. escape)", rel_path)
    if not parts:
        raise ValidationIssue(
            "BAD_PATH", field_label, "(non-empty path)", rel_path)
    abs_pkg = os.path.abspath(package_dir)
    resolved = os.path.normpath(os.path.join(abs_pkg, rel_path))
    if not resolved.startswith(abs_pkg + os.sep) and resolved != abs_pkg:
        raise ValidationIssue(
            "ABSOLUTE_PATH_FORBIDDEN", field_label,
            f"(within {abs_pkg})", resolved)
    return resolved


# ── Board Profile validation ───────────────────────────────────────────

def _is_int_not_bool(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_hex_string(val: str, field: str) -> list[ValidationIssue]:
    """Validate hex string like 0x10C4."""
    issues: list[ValidationIssue] = []
    if not isinstance(val, str) or not val:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", field, "non-empty hex string", repr(val)))
    elif not val.startswith("0x") or not all(
            c in "0123456789ABCDEFabcdef" for c in val[2:]):
        issues.append(ValidationIssue(
            "INVALID_TYPE", field, "hex string (0x...)", val))
    return issues


def _validate_source_catalog(sc: Any) -> list[ValidationIssue]:
    """Validate source_catalog entries."""
    issues: list[ValidationIssue] = []
    if not isinstance(sc, list) or len(sc) == 0:
        issues.append(ValidationIssue(
            "INVALID_TYPE" if isinstance(sc, list) else "MISSING_REQUIRED_FIELD",
            "source_catalog",
            "non-empty list",
            type(sc).__name__ if not isinstance(sc, list) else "(empty)"))
        return issues
    for i, entry in enumerate(sc):
        if not isinstance(entry, dict):
            issues.append(ValidationIssue(
                "INVALID_TYPE", f"source_catalog[{i}]", "dict",
                type(entry).__name__))
            continue
        for fld in ("source_id", "role"):
            v = entry.get(fld)
            if not isinstance(v, str) or not v:
                issues.append(ValidationIssue(
                    "MISSING_REQUIRED_FIELD",
                    f"source_catalog[{i}].{fld}",
                    "non-empty string", repr(v)))
        dp = entry.get("distribution_path")
        if not isinstance(dp, str) or not dp:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD",
                f"source_catalog[{i}].distribution_path",
                "non-empty string", repr(dp)))
            continue
        try:
            _safe_resolve_path(os.getcwd(), dp,
                                f"source_catalog[{i}].distribution_path")
        except ValidationIssue as vi:
            issues.append(vi)
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not is_sha256(sha):
            issues.append(ValidationIssue(
                "INVALID_SHA256", f"source_catalog[{i}].sha256",
                "sha256:<64 hex>", str(sha)[:30] if sha else "None"))
    return issues


def validate_board_profile(profile: Any,
                           is_fixture: bool | None = None) -> list[ValidationIssue]:
    """Validate board profile structure.

    Generic validator — does NOT hardcode board-specific truth (LED count=4 etc).
    Truth is established by SHA256/manifest freezing.

    If is_fixture is None, it is read from profile['fixture_only'].
    """
    issues: list[ValidationIssue] = []

    if not isinstance(profile, dict):
        issues.append(ValidationIssue(
            "INVALID_TYPE", "(root)", "dict", type(profile).__name__))
        return issues

    if is_fixture is None:
        is_fixture = profile.get("fixture_only", False)

    # ── board_id (required for all) ──
    if not isinstance(profile.get("board_id"), str) or not profile["board_id"]:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "board_id", "non-empty string",
            type(profile.get("board_id")).__name__))

    if is_fixture:
        return issues

    # ══════════════════════════════════════════════════════════════════
    # Non-fixture checks below
    # ══════════════════════════════════════════════════════════════════

    # fixture_only must be bool
    fo = profile.get("fixture_only")
    if fo is not True and fo is not False:
        issues.append(ValidationIssue(
            "INVALID_TYPE", "fixture_only", "bool", type(fo).__name__))

    for field in ("vendor", "model", "part", "vivado_part"):
        val = profile.get(field)
        if not isinstance(val, str) or not val:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD", field, "non-empty string",
                type(val).__name__ if val is not None else "None"))

    int_fields = [
        "ddr_physical_bytes", "ddr_configured_bytes", "ddr_configured_highaddr",
        "ddr_frequency_hz", "ddr_bus_width_bits",
        "qspi_physical_bytes", "qspi_linear_window_bytes", "qspi_base_address",
        "pl_oscillator_hz", "ps_clock_hz",
    ]
    for field in int_fields:
        val = profile.get(field)
        if val is None or not _is_int_not_bool(val):
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD" if val is None else "INVALID_TYPE",
                field, "int", type(val).__name__ if val is not None else "None"))

    # ddr_chip_count
    dcc = profile.get("ddr_chip_count")
    if not _is_int_not_bool(dcc) or dcc <= 0:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD" if dcc is None else "INVALID_TYPE",
            "ddr_chip_count", "int > 0", repr(dcc)))

    # uart
    uart = profile.get("uart")
    if isinstance(uart, dict):
        baud = uart.get("default_baud")
        if not _is_int_not_bool(baud):
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD", "uart.default_baud",
                "int", type(baud).__name__ if baud is not None else "None"))
        ctrl = uart.get("controller")
        if not isinstance(ctrl, str) or not ctrl:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD", "uart.controller",
                "non-empty string",
                type(ctrl).__name__ if ctrl is not None else "None"))
        mio = uart.get("mio_pins")
        if not isinstance(mio, list) or not all(_is_int_not_bool(p) for p in mio):
            issues.append(ValidationIssue(
                "INVALID_TYPE", "uart.mio_pins",
                "list[int]", repr(mio)))
        elif len(set(mio)) != len(mio):
            issues.append(ValidationIssue(
                "DUPLICATE_PATH", "uart.mio_pins",
                "(unique pins)", repr(mio)))
    else:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "uart", "dict", type(uart).__name__))

    # usb_bridge
    usb = profile.get("usb_bridge")
    if isinstance(usb, dict):
        for fld in ("chip", "family"):
            v = usb.get(fld)
            if not isinstance(v, str) or not v:
                issues.append(ValidationIssue(
                    "MISSING_REQUIRED_FIELD", f"usb_bridge.{fld}",
                    "non-empty string", repr(v)))
        issues.extend(_validate_hex_string(usb.get("vid", ""), "usb_bridge.vid"))
        issues.extend(_validate_hex_string(usb.get("pid", ""), "usb_bridge.pid"))
    else:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "usb_bridge", "dict",
            type(usb).__name__))

    # pl_leds: count must be non-negative int, count == len(pins), pins unique
    pl_leds = profile.get("pl_leds")
    if isinstance(pl_leds, dict):
        cnt = pl_leds.get("count")
        if not _is_int_not_bool(cnt) or cnt < 0:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD" if cnt is None else "INVALID_TYPE",
                "pl_leds.count", "int >= 0", repr(cnt)))
        pins = pl_leds.get("pins")
        if not isinstance(pins, list) or not all(isinstance(p, str) and p for p in pins):
            issues.append(ValidationIssue(
                "INVALID_TYPE", "pl_leds.pins", "list[str]", repr(pins)))
        elif len(set(pins)) != len(pins):
            issues.append(ValidationIssue(
                "DUPLICATE_PATH", "pl_leds.pins", "(unique pins)", repr(pins)))
        if (_is_int_not_bool(cnt) and isinstance(pins, list)
                and len(pins) != cnt):
            issues.append(ValidationIssue(
                "INVALID_TYPE", "pl_leds.pins",
                f"list of length {cnt}", f"length {len(pins)}"))
        pol = pl_leds.get("polarity")
        if pol not in _VALID_POLARITY:
            issues.append(ValidationIssue(
                "INVALID_TYPE", "pl_leds.polarity",
                str(_VALID_POLARITY), repr(pol)))
    else:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "pl_leds", "dict",
            type(pl_leds).__name__))

    # ps_leds: count must be non-negative int, count == len(mio_pins), mio unique
    ps_leds = profile.get("ps_leds")
    if isinstance(ps_leds, dict):
        cnt = ps_leds.get("count")
        if not _is_int_not_bool(cnt) or cnt < 0:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD" if cnt is None else "INVALID_TYPE",
                "ps_leds.count", "int >= 0", repr(cnt)))
        mio = ps_leds.get("mio_pins")
        if not isinstance(mio, list) or not all(_is_int_not_bool(p) for p in mio):
            issues.append(ValidationIssue(
                "INVALID_TYPE", "ps_leds.mio_pins", "list[int]", repr(mio)))
        elif len(set(mio)) != len(mio):
            issues.append(ValidationIssue(
                "DUPLICATE_PATH", "ps_leds.mio_pins", "(unique pins)", repr(mio)))
        if (_is_int_not_bool(cnt) and isinstance(mio, list)
                and len(mio) != cnt):
            issues.append(ValidationIssue(
                "INVALID_TYPE", "ps_leds.mio_pins",
                f"list of length {cnt}", f"length {len(mio)}"))
        pol = ps_leds.get("polarity")
        if pol not in _VALID_POLARITY:
            issues.append(ValidationIssue(
                "INVALID_TYPE", "ps_leds.polarity",
                str(_VALID_POLARITY), repr(pol)))
    else:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "ps_leds", "dict",
            type(ps_leds).__name__))

    # pl_resources
    plr = profile.get("pl_resources")
    if isinstance(plr, dict):
        for fld in ("luts", "ffs", "bram36", "dsp48"):
            v = plr.get(fld)
            if not _is_int_not_bool(v) or v < 0:
                issues.append(ValidationIssue(
                    "MISSING_REQUIRED_FIELD" if v is None else "INVALID_TYPE",
                    f"pl_resources.{fld}", "int >= 0", repr(v)))
    else:
        issues.append(ValidationIssue(
            "MISSING_REQUIRED_FIELD", "pl_resources", "dict",
            type(plr).__name__))

    for field in ("ps7_preset_sha256", "xdc_sha256"):
        val = profile.get(field)
        if not isinstance(val, str) or not is_sha256(val):
            issues.append(ValidationIssue(
                "INVALID_SHA256", field,
                "sha256:<64 hex>", str(val)[:30] if val else "None"))

    # source_catalog
    issues.extend(_validate_source_catalog(profile.get("source_catalog")))

    for field in ("ddr_chip", "qspi_chip", "qspi_data_mode",
                  "pl_oscillator_pin"):
        val = profile.get(field)
        if not isinstance(val, str) or not val:
            issues.append(ValidationIssue(
                "MISSING_REQUIRED_FIELD", field, "non-empty string",
                type(val).__name__ if val is not None else "None"))

    return issues


# ── Manifest discovery ────────────────────────────────────────────────

def _find_profile_name(package_dir: str) -> str:
    for name in os.listdir(package_dir):
        if name.startswith("board_profile_") and name.endswith(".json"):
            return name
    raise FileNotFoundError(
        f"No board_profile_*.json found in {package_dir}")


def find_manifest_status(package_dir: str) -> tuple[str | None, str | None, str | None]:
    """Returns (manifest_filename, status_from_manifest, error_reason_code).

    error_reason_code:
      None               → no error
      PACKAGE_NOT_LOCKED → only draft, caller needs allow_draft
      MISSING_MANIFEST   → no manifest at all
      PACKAGE_STATE_CONFLICT → both exist, or filename/status mismatch
      INVALID_JSON       → manifest exists but JSON is malformed
    """
    locked_path = os.path.join(package_dir, "package_manifest.json")
    draft_path = os.path.join(package_dir, "package_manifest.draft.json")
    has_locked = os.path.isfile(locked_path)
    has_draft = os.path.isfile(draft_path)

    if has_locked and has_draft:
        return None, None, "PACKAGE_STATE_CONFLICT"

    if has_locked:
        try:
            with open(locked_path, "r", encoding="utf-8") as f:
                m = json.load(f)
        except json.JSONDecodeError:
            return None, None, "INVALID_JSON"
        if m.get("status") != "locked":
            return None, None, "PACKAGE_STATE_CONFLICT"
        return "package_manifest.json", "locked", None

    if has_draft:
        try:
            with open(draft_path, "r", encoding="utf-8") as f:
                m = json.load(f)
        except json.JSONDecodeError:
            return None, None, "INVALID_JSON"
        if m.get("status") != "draft":
            return None, None, "PACKAGE_STATE_CONFLICT"
        return "package_manifest.draft.json", "draft", "PACKAGE_NOT_LOCKED"

    return None, None, "MISSING_MANIFEST"


def _load_manifest_from_disk(package_dir: str,
                              manifest_name: str) -> dict:
    path = os.path.join(package_dir, manifest_name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValidationIssue("INVALID_JSON", manifest_name,
                               "valid JSON", str(e))


# ── Manifest schema validation ────────────────────────────────────────

def validate_package_manifest(manifest: Any) -> list[ValidationIssue]:
    """Validate board_configuration manifest schema. Never raises."""
    issues: list[ValidationIssue] = []

    if not isinstance(manifest, dict):
        issues.append(ValidationIssue(
            "INVALID_TYPE", "(root)", "dict", type(manifest).__name__))
        return issues

    mt = manifest.get("manifest_type")
    if mt != VALID_MANIFEST_TYPE:
        issues.append(ValidationIssue(
            "MANIFEST_TYPE_MISMATCH", "manifest_type",
            VALID_MANIFEST_TYPE, str(mt)))
    sv = manifest.get("schema_version")
    if sv != CURRENT_SCHEMA:
        issues.append(ValidationIssue(
            "UNSUPPORTED_SCHEMA", "schema_version",
            CURRENT_SCHEMA, str(sv)))
    bid = manifest.get("board_id")
    if not isinstance(bid, str) or not bid:
        issues.append(ValidationIssue(
            "INVALID_TYPE", "board_id", "non-empty string",
            type(bid).__name__ if bid is not None else "None"))

    for field in _REQUIRED_FIELDS:
        if field not in manifest:
            issues.append(ValidationIssue(
                "MISSING_FIELD", field, "(required)", "(missing)"))
    for field in _STRING_FIELDS:
        val = manifest.get(field)
        if val is not None and (not isinstance(val, str) or not val):
            issues.append(ValidationIssue(
                "INVALID_TYPE", field, "non-empty string",
                type(val).__name__))

    status = manifest.get("status")
    if isinstance(status, str) and status not in _VALID_STATUS:
        issues.append(ValidationIssue(
            "INVALID_TYPE", "status",
            str(_VALID_STATUS), repr(status)))

    inputs = manifest.get("revision_inputs")
    if inputs is not None and not isinstance(inputs, dict):
        issues.append(ValidationIssue(
            "INVALID_TYPE", "revision_inputs", "dict",
            type(inputs).__name__))

    declared_rev = manifest.get("manifest_revision")
    if isinstance(declared_rev, str) and not is_sha256(declared_rev):
        issues.append(ValidationIssue(
            "INVALID_SHA256", "manifest_revision",
            "sha256:<64 hex>", declared_rev[:30]))

    if isinstance(declared_rev, str) and is_sha256(declared_rev) and isinstance(inputs, dict):
        try:
            computed = compute_revision(inputs)
            if declared_rev != computed:
                issues.append(ValidationIssue(
                    "BAD_REVISION", "manifest_revision",
                    computed, declared_rev))
        except (ValueError, TypeError) as e:
            issues.append(ValidationIssue(
                "BAD_REVISION", "revision_inputs",
                "(valid)", str(e)))

    if isinstance(inputs, dict):
        for field in _REQUIRED_REVISION_INPUTS:
            if field not in inputs:
                issues.append(ValidationIssue(
                    "MISSING_FIELD", f"revision_inputs.{field}",
                    "(required)", "(missing)"))
            else:
                v = inputs[field]
                if not isinstance(v, str) or not is_sha256(v):
                    issues.append(ValidationIssue(
                        "INVALID_SHA256", f"revision_inputs.{field}",
                        "sha256:<64 hex>", str(v)[:30] if v else "None"))

    files = manifest.get("files")
    if not isinstance(files, list):
        if files is not None:
            issues.append(ValidationIssue(
                "INVALID_TYPE", "files", "list",
                type(files).__name__))
    elif len(files) == 0:
        issues.append(ValidationIssue(
            "MISSING_FIELD", "files", "(non-empty list)", "(empty)"))
    else:
        seen = set()
        for i, entry in enumerate(files):
            if not isinstance(entry, dict):
                issues.append(ValidationIssue(
                    "INVALID_TYPE", f"files[{i}]", "dict",
                    type(entry).__name__))
                continue
            p = entry.get("path")
            s = entry.get("sha256")
            if not isinstance(p, str) or not p:
                issues.append(ValidationIssue(
                    "INVALID_TYPE", f"files[{i}].path",
                    "non-empty string", repr(p)))
                continue
            if not isinstance(s, str) or not is_sha256(s):
                issues.append(ValidationIssue(
                    "INVALID_SHA256", f"files[{i}].sha256",
                    "sha256:<64 hex>", str(s)[:30] if s else "None"))
            if p in MANIFEST_NAMES:
                issues.append(ValidationIssue(
                    "MANIFEST_SELF_REFERENCE", f"files[{i}].path",
                    "(no self-reference)", p))
            if p in seen:
                issues.append(ValidationIssue(
                    "DUPLICATE_PATH", f"files[{i}].path",
                    "(unique)", p))
            seen.add(p)
            try:
                _safe_resolve_path(os.getcwd(), p,
                                    f"files[{i}].path")
            except ValidationIssue as vi:
                issues.append(vi)
    return issues


# ── Exact file set validation ─────────────────────────────────────────

def _check_exact_file_set(manifest: dict, board_id: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    files = manifest.get("files")
    if not isinstance(files, list):
        return issues
    expected = {f.format(board_id=board_id) for f in EXACT_CONTENT_FILES}
    actual = set()
    for entry in files:
        if isinstance(entry, dict):
            p = entry.get("path")
            if isinstance(p, str) and p:
                actual.add(p)
    for m in sorted(expected - actual):
        issues.append(ValidationIssue(
            "MISSING_FILE_IN_MANIFEST", m, "(required)", "(missing)"))
    for e in sorted(actual - expected):
        issues.append(ValidationIssue(
            "EXTRA_FILE_IN_MANIFEST", e, "(not required)", "(present)"))
    return issues


# ── Cross-reference SHA validation ────────────────────────────────────

def _validate_sha_cross_refs(manifest: dict, profile: dict,
                              package_dir: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    files = manifest.get("files")
    inputs = manifest.get("revision_inputs")
    if not isinstance(files, list) or not isinstance(inputs, dict):
        return issues

    file_lookup: dict[str, str] = {}
    for entry in files:
        if isinstance(entry, dict):
            p = entry.get("path")
            s = entry.get("sha256")
            if isinstance(p, str) and isinstance(s, str) and is_sha256(s):
                file_lookup[p] = s

    sha_pairs = [
        ("board_profile_", "board_profile_sha256"),
        ("ps7_preset.tcl", "ps7_preset_sha256"),
        ("board.xdc", "board_xdc_sha256"),
        ("SOURCES.md", "sources_md_sha256"),
        ("README.md", "readme_md_sha256"),
    ]

    for file_key, rev_key in sha_pairs:
        matched_path = None
        for fp in file_lookup:
            if fp.endswith(".json") and file_key == "board_profile_":
                if fp.startswith("board_profile_"):
                    matched_path = fp; break
            elif fp == file_key:
                matched_path = fp; break

        if matched_path is None:
            issues.append(ValidationIssue(
                "MISSING_FILE_IN_MANIFEST", file_key,
                "(required)", "(missing)"))
            continue

        files_sha = file_lookup[matched_path]
        rev_sha = inputs.get(rev_key)

        if rev_sha and files_sha != rev_sha:
            issues.append(ValidationIssue(
                "SHA_CROSS_REF_MISMATCH",
                f"files.{matched_path} vs revision_inputs.{rev_key}",
                rev_sha, files_sha))

        disk_sha = None
        try:
            resolved = _safe_resolve_path(package_dir, matched_path,
                                           f"cross-ref:{matched_path}")
            disk_sha = sha256_file(resolved)
        except (ValidationIssue, FileNotFoundError) as e:
            if isinstance(e, ValidationIssue):
                issues.append(e)
            else:
                issues.append(ValidationIssue(
                    "PATH_NOT_FOUND", matched_path,
                    "(file exists)", "(missing)"))

        if disk_sha and files_sha != disk_sha:
            code = "PROFILE_SHA256_MISMATCH" if file_key == "board_profile_" else "SHA256_MISMATCH"
            issues.append(ValidationIssue(
                code, matched_path,
                files_sha, disk_sha))

    # Profile ps7_preset_sha256 vs disk
    preset_sha = profile.get("ps7_preset_sha256")
    if isinstance(preset_sha, str) and is_sha256(preset_sha):
        try:
            resolved = _safe_resolve_path(package_dir, "ps7_preset.tcl",
                                          "ps7_preset.tcl")
            actual = sha256_file(resolved)
            if actual != preset_sha:
                issues.append(ValidationIssue(
                    "PRESET_SHA256_MISMATCH", "ps7_preset_sha256",
                    preset_sha, actual))
        except (ValidationIssue, FileNotFoundError) as e:
            if isinstance(e, ValidationIssue):
                issues.append(e)
            else:
                issues.append(ValidationIssue(
                    "PATH_NOT_FOUND", "ps7_preset.tcl",
                    "(file exists)", "(missing)"))

    xdc_sha = profile.get("xdc_sha256")
    if isinstance(xdc_sha, str) and is_sha256(xdc_sha):
        try:
            resolved = _safe_resolve_path(package_dir, "board.xdc",
                                          "board.xdc")
            actual = sha256_file(resolved)
            if actual != xdc_sha:
                issues.append(ValidationIssue(
                    "XDC_SHA256_MISMATCH", "xdc_sha256",
                    xdc_sha, actual))
        except (ValidationIssue, FileNotFoundError) as e:
            if isinstance(e, ValidationIssue):
                issues.append(e)
            else:
                issues.append(ValidationIssue(
                    "PATH_NOT_FOUND", "board.xdc",
                    "(file exists)", "(missing)"))

    declared_rev = manifest.get("manifest_revision")
    if isinstance(declared_rev, str) and is_sha256(declared_rev):
        try:
            computed = compute_revision(inputs)
            if declared_rev != computed:
                issues.append(ValidationIssue(
                    "BAD_REVISION", "manifest_revision",
                    computed, declared_rev))
        except (ValueError, TypeError) as e:
            issues.append(ValidationIssue(
                "BAD_REVISION", "revision_inputs",
                "(valid)", str(e)))

    rev_to_disk = {
        "ps7_preset_sha256": "ps7_preset.tcl",
        "board_xdc_sha256": "board.xdc",
        "sources_md_sha256": "SOURCES.md",
        "readme_md_sha256": "README.md",
    }
    for rev_key, disk_rel in rev_to_disk.items():
        rev_sha = inputs.get(rev_key)
        if not isinstance(rev_sha, str) or not is_sha256(rev_sha):
            continue
        try:
            resolved = _safe_resolve_path(package_dir, disk_rel, rev_key)
            actual = sha256_file(resolved)
            if rev_sha != actual:
                issues.append(ValidationIssue(
                    "SHA256_MISMATCH",
                    f"revision_inputs.{rev_key}", rev_sha, actual))
        except (ValidationIssue, FileNotFoundError) as e:
            if isinstance(e, ValidationIssue):
                issues.append(e)
            else:
                issues.append(ValidationIssue(
                    "PATH_NOT_FOUND", disk_rel,
                    "(file exists)", "(missing)"))

    return issues


# ── Full package validation ───────────────────────────────────────────

def validate_package_full(package_dir: str, board_id: str,
                           manifest_name: str,
                           profile: dict) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    manifest = _load_manifest_from_disk(package_dir, manifest_name)

    schema_issues = validate_package_manifest(manifest)
    issues.extend(schema_issues)
    if schema_issues:
        return issues

    manifest_bid = manifest.get("board_id")
    if manifest_bid != board_id:
        issues.append(ValidationIssue(
            "BOARD_ID_MISMATCH", "manifest.board_id vs profile.board_id",
            board_id, str(manifest_bid)))
    profile_bid = profile.get("board_id")
    if profile_bid != board_id:
        issues.append(ValidationIssue(
            "BOARD_ID_MISMATCH", "profile.board_id",
            board_id, str(profile_bid)))

    issues.extend(_check_exact_file_set(manifest, board_id))
    issues.extend(_validate_sha_cross_refs(manifest, profile, package_dir))

    # Semantic cross-field validation (runs even when SHA checks pass)
    issues.extend(_validate_ddr_consistency(profile))
    issues.extend(_validate_qspi_consistency(profile))
    issues.extend(_validate_led_xdc_consistency(profile, package_dir))
    issues.extend(_validate_clock_xdc_consistency(profile, package_dir))
    issues.extend(_validate_profile_paths(profile))

    files = manifest.get("files", [])
    for entry in files:
        if not isinstance(entry, dict):
            continue
        p = entry.get("path")
        if not isinstance(p, str) or not p:
            continue
        try:
            resolved = _safe_resolve_path(package_dir, p, f"integrity:{p}")
            if not os.path.isfile(resolved):
                issues.append(ValidationIssue(
                    "PATH_NOT_FOUND", p, "(file exists)", "(missing)"))
        except ValidationIssue as vi:
            issues.append(vi)

    known = {manifest_name}
    for entry in files:
        if isinstance(entry, dict):
            p = entry.get("path")
            if isinstance(p, str) and p:
                known.add(p)
    for name in sorted(os.listdir(package_dir)):
        if name not in known:
            issues.append(ValidationIssue(
                "EXTRA_FILE_IN_DIR", name,
                "(listed in manifest)", "(not listed)"))

    return issues


def _validate_package_except(package_dir: str, board_id: str,
                              manifest_name: str, profile: dict,
                              exclude_from_extra_files: set[str]) -> list[ValidationIssue]:
    """Same as validate_package_full but excludes listed files from EXTRA_FILE_IN_DIR."""
    issues = validate_package_full(package_dir, board_id, manifest_name, profile)
    if not exclude_from_extra_files:
        return issues
    filtered = []
    for issue in issues:
        if issue.code == "EXTRA_FILE_IN_DIR" and issue.field in exclude_from_extra_files:
            continue
        filtered.append(issue)
    return filtered


# ── Semantic cross-field validators ──────────────────────────────────

def _validate_ddr_consistency(profile: dict) -> list[ValidationIssue]:
    """Check DDR physical vs configured consistency."""
    issues: list[ValidationIssue] = []
    physical = profile.get("ddr_physical_bytes")
    configured = profile.get("ddr_configured_bytes")
    highaddr = profile.get("ddr_configured_highaddr")

    if _is_int_not_bool(physical) and _is_int_not_bool(configured):
        if configured > physical:
            issues.append(ValidationIssue(
                "DDR_CAPACITY_INCONSISTENT", "ddr_configured_bytes",
                f"<= {physical}", str(configured)))
    if _is_int_not_bool(highaddr) and _is_int_not_bool(configured):
        if highaddr + 1 != configured:
            issues.append(ValidationIssue(
                "DDR_CAPACITY_INCONSISTENT", "ddr_configured_bytes",
                f"highaddr+1 = {highaddr + 1}", str(configured)))
    return issues


def _validate_qspi_consistency(profile: dict) -> list[ValidationIssue]:
    """Check QSPI linear window <= 16MB and <= physical."""
    issues: list[ValidationIssue] = []
    window = profile.get("qspi_linear_window_bytes")
    physical = profile.get("qspi_physical_bytes")
    max_window = 16777216  # 16 MB

    if _is_int_not_bool(window):
        if window > max_window:
            issues.append(ValidationIssue(
                "QSPI_WINDOW_INCONSISTENT", "qspi_linear_window_bytes",
                f"<= {max_window} (16MB)", str(window)))
        if _is_int_not_bool(physical) and window > physical:
            issues.append(ValidationIssue(
                "QSPI_WINDOW_INCONSISTENT", "qspi_linear_window_bytes",
                f"<= {physical} (physical)", str(window)))
    return issues


def _validate_led_xdc_consistency(profile: dict,
                                  package_dir: str) -> list[ValidationIssue]:
    """Check PL LED count/pins in profile match board.xdc PACKAGE_PIN entries."""
    issues: list[ValidationIssue] = []
    pl_leds = profile.get("pl_leds")
    if not isinstance(pl_leds, dict):
        return issues

    expected_pins = pl_leds.get("pins")
    expected_count = pl_leds.get("count")
    if not isinstance(expected_pins, list):
        return issues

    # Parse board.xdc for led_pins PACKAGE_PIN entries
    xdc_path = os.path.join(package_dir, "board.xdc")
    xdc_pins = _parse_led_pins_from_xdc(xdc_path)
    if xdc_pins is None:
        return issues  # can't parse — not an error from this check

    if len(xdc_pins) != len(expected_pins):
        issues.append(ValidationIssue(
            "LED_COUNT_XDC_MISMATCH", "pl_leds.pins",
            f"XDC has {len(xdc_pins)} pins, profile has {len(expected_pins)}",
            str(xdc_pins)))
    elif set(xdc_pins) != set(expected_pins):
        issues.append(ValidationIssue(
            "LED_COUNT_XDC_MISMATCH", "pl_leds.pins",
            f"profile pins {expected_pins}",
            f"XDC pins {xdc_pins}"))

    return issues


def _parse_led_pins_from_xdc(xdc_path: str) -> list[str] | None:
    """Parse PACKAGE_PIN assignments for led_pins from an XDC file.
    Returns list of package pin names like ['J16','K16','M15','M14'].
    Returns None if parsing fails.
    """
    if not os.path.isfile(xdc_path):
        return None
    try:
        with open(xdc_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    pins: dict[int, str] = {}  # index → pin name
    for m in re.finditer(
            r'set_property\s+PACKAGE_PIN\s+(\S+)\s+\[get_ports\s+\{?led_pins\[(\d+)\]',
            content):
        pin_name = m.group(1)
        idx = int(m.group(2))
        pins[idx] = pin_name

    if not pins:
        return None

    # Return in index order
    return [pins[i] for i in sorted(pins)]


def _validate_clock_xdc_consistency(profile: dict,
                                    package_dir: str) -> list[ValidationIssue]:
    """Check profile.pl_oscillator_hz matches XDC create_clock -period."""
    issues: list[ValidationIssue] = []
    osc_hz = profile.get("pl_oscillator_hz")
    if not _is_int_not_bool(osc_hz) or osc_hz <= 0:
        return issues

    xdc_path = os.path.join(package_dir, "board.xdc")
    period_ns = _parse_clock_period_from_xdc(xdc_path)
    if period_ns is None:
        # Could not parse XDC clock → fail-closed: report mismatch
        issues.append(ValidationIssue(
            "CLOCK_FREQ_XDC_MISMATCH", "pl_oscillator_hz",
            f"could not parse XDC clock period",
            str(osc_hz)))
        return issues

    if period_ns <= 0:
        issues.append(ValidationIssue(
            "CLOCK_FREQ_XDC_MISMATCH", "pl_oscillator_hz",
            f"XDC period invalid ({period_ns}ns)",
            str(osc_hz)))
        return issues

    xdc_hz = int(1e9 / period_ns)  # period=20ns → 50MHz
    if xdc_hz != osc_hz:
        issues.append(ValidationIssue(
            "CLOCK_FREQ_XDC_MISMATCH", "pl_oscillator_hz",
            f"{osc_hz} Hz (profile)",
            f"{xdc_hz} Hz (XDC {period_ns}ns)"))

    return issues


def _parse_clock_period_from_xdc(xdc_path: str) -> float | None:
    """Parse the first create_clock -period value from an XDC file.
    Returns period in nanoseconds, or None if parsing fails.
    """
    if not os.path.isfile(xdc_path):
        return None
    try:
        with open(xdc_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    m = re.search(r'create_clock\s+.*?-period\s+([\d.]+)', content)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _validate_profile_paths(profile: dict) -> list[ValidationIssue]:
    """Check profile for absolute paths, drive letters, UNC, etc."""
    issues: list[ValidationIssue] = []
    _scan_for_absolute_paths(profile, "board_profile", issues)
    return issues


def _scan_for_absolute_paths(obj, prefix: str,
                             issues: list[ValidationIssue]) -> None:
    """Recursively check string values for absolute/personal paths."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _scan_for_absolute_paths(v, f"{prefix}.{k}", issues)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_for_absolute_paths(v, f"{prefix}[{i}]", issues)
    elif isinstance(obj, str):
        # Windows absolute: D:\..., C:\...
        if re.match(r'^[A-Za-z]:\\', obj):
            issues.append(ValidationIssue(
                "ABSOLUTE_PATH_FORBIDDEN", prefix,
                "(relative or distribution-path)", obj[:80]))
        # Windows drive-relative: C:path (no backslash)
        elif re.match(r'^[A-Za-z]:[^\\/]', obj):
            issues.append(ValidationIssue(
                "ABSOLUTE_PATH_FORBIDDEN", prefix,
                "(relative or distribution-path)", obj[:80]))
        # POSIX absolute
        elif obj.startswith("/"):
            issues.append(ValidationIssue(
                "ABSOLUTE_PATH_FORBIDDEN", prefix,
                "(relative or distribution-path)", obj[:80]))
        # UNC
        elif obj.startswith("\\\\"):
            issues.append(ValidationIssue(
                "ABSOLUTE_PATH_FORBIDDEN", prefix,
                "(relative or distribution-path)", obj[:80]))


# ── Revision computation ──────────────────────────────────────────────

def compute_package_revision(package_dir: str) -> str:
    profile_name = _find_profile_name(package_dir)
    profile_path = os.path.join(package_dir, profile_name)
    inputs = {
        "board_profile_sha256": sha256_file(profile_path),
        "ps7_preset_sha256": sha256_file(os.path.join(package_dir, "ps7_preset.tcl")),
        "board_xdc_sha256": sha256_file(os.path.join(package_dir, "board.xdc")),
        "sources_md_sha256": sha256_file(os.path.join(package_dir, "SOURCES.md")),
        "readme_md_sha256": sha256_file(os.path.join(package_dir, "README.md")),
    }
    return compute_revision(inputs)


# ── Package fingerprint (for cache) ───────────────────────────────────

def compute_package_fingerprint(package_dir: str) -> str:
    """Return SHA256 fingerprint of all package contents including
    directory listing to detect extra/missing files."""
    import hashlib
    h = hashlib.sha256()

    # Include sorted directory listing (names only, no file content)
    dir_entries = sorted(os.listdir(package_dir))
    for name in dir_entries:
        h.update(name.encode())

    # Hash all expected content files
    manifest_name, __, ___ = find_manifest_status(package_dir)
    content_files = ["ps7_preset.tcl", "board.xdc", "SOURCES.md", "README.md"]
    if manifest_name:
        content_files.append(manifest_name)
    try:
        content_files.append(_find_profile_name(package_dir))
    except FileNotFoundError:
        h.update(b"MISSING_PROFILE")

    for fn in sorted(content_files):
        fp = os.path.join(package_dir, fn)
        if os.path.isfile(fp):
            h.update(fn.encode())
            with open(fp, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
        else:
            h.update(f"MISSING:{fn}".encode())

    return f"sha256:{h.hexdigest()}"


# ── Standalone helpers ────────────────────────────────────────────────

def check_package_integrity(package_dir: str) -> list[ValidationIssue]:
    manifest_name, _, reason = find_manifest_status(package_dir)
    if reason == "MISSING_MANIFEST":
        return [ValidationIssue("MISSING_MANIFEST", "(none)")]
    if reason in ("PACKAGE_STATE_CONFLICT", "INVALID_JSON"):
        return [ValidationIssue(reason, "(none)")]
    try:
        profile_name = _find_profile_name(package_dir)
        with open(os.path.join(package_dir, profile_name), "r",
                  encoding="utf-8") as f:
            profile = json.load(f)
        bid = profile.get("board_id", "")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        code = "INVALID_JSON" if isinstance(e, json.JSONDecodeError) else "PATH_NOT_FOUND"
        return [ValidationIssue(code, "board_profile")]
    return validate_package_full(package_dir, bid, manifest_name, profile)


# ── Freeze package ────────────────────────────────────────────────────

from mcps.common.artifact_schema import ManifestConflictError  # re-export


class FreezeCleanupError(Exception):
    """Locked manifest was published successfully but draft could not be
    deleted. Re-running freeze_package() will retry the cleanup and
    return 'already_exists_same'."""


def freeze_package(package_dir: str) -> str:
    """Freeze a draft Board Configuration Package to locked state.

    Serialized per-package via OS-level lock file OUTSIDE the package directory.
    This prevents concurrent directory scans from seeing each other's transient files.

    State machine:
      A: only draft → validate, publish locked, delete draft → "published"
      B: only locked → validate locked → "already_exists_same"
      C: locked + draft, same content → delete draft → "already_exists_same"
      D: locked + draft, different content → ManifestConflictError
      E: no manifest → ValueError

    Returns "published" or "already_exists_same".
    Raises ManifestConflictError if locked+draft have different content.
    Raises ValueError if no manifest exists or draft/package is invalid.
    Raises FreezeCleanupError if locked was published but draft cleanup failed.
    """
    from mcps.common.artifact_schema import atomic_publish_no_replace
    import hashlib

    _lock = _PackageLock(package_dir)
    _lock.acquire()
    try:
        return _freeze_package_impl(package_dir)
    finally:
        _lock.release()


def _freeze_package_impl(package_dir: str) -> str:
    """Core freeze logic — called under _PackageLock."""
    from mcps.common.artifact_schema import atomic_publish_no_replace
    import hashlib

    draft_path = os.path.join(package_dir, "package_manifest.draft.json")
    locked_path = os.path.join(package_dir, "package_manifest.json")
    has_draft = os.path.isfile(draft_path)
    has_locked = os.path.isfile(locked_path)

    # ── State D (both, different) / State C (both, same) / State A (draft only) ──
    if has_draft:
        # Load and validate draft
        try:
            manifest = _load_manifest_from_disk(package_dir, "package_manifest.draft.json")
        except ValidationIssue as e:
            raise ValueError(f"Draft manifest invalid: {e}") from e

        profile_name = _find_profile_name(package_dir)
        profile_path = os.path.join(package_dir, profile_name)
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)

        board_id = profile.get("board_id", "")
        if not board_id:
            raise ValueError(f"Board profile has no board_id: {profile_path}")

        profile_issues = validate_board_profile(profile)
        if profile_issues:
            codes = "; ".join(i.code for i in profile_issues)
            raise ValueError(f"Board profile validation failed: {codes}")

        # Validate draft against the OTHER manifest if locked exists.
        # The locked manifest is a legitimate file when both exist;
        # exclude it from the extra-file check.
        pkg_issues = _validate_package_except(
            package_dir, board_id, "package_manifest.draft.json", profile,
            exclude_from_extra_files={"package_manifest.json"} if has_locked else set())
        if pkg_issues:
            codes = "; ".join(i.code for i in pkg_issues)
            raise ValueError(f"Package validation failed: {codes}")

        # Build expected locked manifest
        locked_manifest = dict(manifest)
        locked_manifest["status"] = "locked"

        schema_issues = validate_package_manifest(locked_manifest)
        if schema_issues:
            codes = "; ".join(i.code for i in schema_issues)
            raise ValueError(f"Locked manifest schema invalid: {codes}")

        expected_bytes = canonical_json(locked_manifest)

        # ── Check against existing locked (state C vs D) ──
        if has_locked:
            with open(locked_path, "rb") as f:
                existing_bytes = f.read()
            existing_hash = "sha256:" + hashlib.sha256(existing_bytes).hexdigest()
            expected_hash = "sha256:" + hashlib.sha256(expected_bytes).hexdigest()

            if existing_hash != expected_hash:
                raise ManifestConflictError(
                    f"Locked manifest at {locked_path} already exists with "
                    f"different content. Package cannot be frozen to the same "
                    f"version. Create a new package directory (e.g., "
                    f"ALINX_AX7020_v1.1/) for changed content.")

            # State C: same content — retry draft deletion
            try:
                os.unlink(draft_path)
            except FileNotFoundError:
                pass  # another freeze already cleaned it up
            except OSError as e:
                raise FreezeCleanupError(
                    f"Failed to delete draft manifest at {draft_path}: {e}. "
                    f"Locked manifest at {locked_path} is valid. "
                    f"Re-run freeze_package() to retry cleanup."
                ) from e
            return "already_exists_same"

        # ── State A: draft only — publish locked ──
        try:
            result = atomic_publish_no_replace(expected_bytes, locked_path)
        except ManifestConflictError:
            raise  # re-raise from B02 directly
        except Exception as e:
            raise ValueError(
                f"Failed to publish locked manifest: {e}") from e

        # Verify locked content was written correctly
        if not os.path.isfile(locked_path):
            raise RuntimeError(
                f"Locked manifest not found at {locked_path} after publish")
        with open(locked_path, "rb") as f:
            actual_bytes = f.read()
        if actual_bytes != expected_bytes:
            raise RuntimeError(
                f"Published locked manifest at {locked_path} differs from expected")

        # Delete draft
        try:
            os.unlink(draft_path)
        except FileNotFoundError:
            pass  # another concurrent freeze already cleaned it up
        except OSError as e:
            raise FreezeCleanupError(
                f"Locked manifest published successfully at {locked_path}, "
                f"but failed to delete draft at {draft_path}: {e}. "
                f"Re-run freeze_package() to retry cleanup."
            ) from e

        return result  # "published" or "already_exists_same" from atomic_publish_no_replace

    # ── State B (locked only) / State E (nothing) ──
    if has_locked:
        # Validate the locked package
        try:
            _load_manifest_from_disk(package_dir, "package_manifest.json")
        except ValidationIssue as e:
            raise ValueError(f"Locked manifest invalid: {e}") from e

        profile_name = _find_profile_name(package_dir)
        profile_path = os.path.join(package_dir, profile_name)
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        board_id = profile.get("board_id", "")
        if not board_id:
            raise ValueError(f"Board profile has no board_id: {profile_path}")

        profile_issues = validate_board_profile(profile)
        if profile_issues:
            codes = "; ".join(i.code for i in profile_issues)
            raise ValueError(f"Board profile validation failed: {codes}")

        pkg_issues = validate_package_full(
            package_dir, board_id, "package_manifest.json", profile)
        if pkg_issues:
            codes = "; ".join(i.code for i in pkg_issues)
            raise ValueError(f"Package validation failed: {codes}")

        return "already_exists_same"

    # State E
    raise ValueError(
        f"No package manifest found in {package_dir}. "
        f"Expected package_manifest.draft.json or package_manifest.json.")
