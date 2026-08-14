"""
process_guard.py — Process identity verification. Returns None when unverifiable (fail-closed).
get_process_identity(pid) → WorkerIdentity or None if any field is unreadable.
"""
import csv, io, os, subprocess, time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


@dataclass
class WorkerIdentity:
    pid: int
    process_start_time: float
    executable_path: str
    executable_args: Optional[list] = None
    worker_generation: int = 0
    instance_id: Optional[str] = None


_SUPERVISOR_NAMES = frozenset({
    "cmd.exe", "cmd", "powershell.exe", "powershell", "pwsh.exe", "pwsh",
    "sh", "bash", "dash", "zsh", "conhost.exe", "conhost",
})

_BACKEND_EXECUTABLE_PREFIXES = {
    "VIVADO": ("vivado",),
    "XSCT": ("rdi_xsct", "xsct", "tclsh85"),
    "XSDB": ("rdi_xsdb", "xsdb", "tclsh85"),
}


def normalize_executable_path(path) -> str:
    """Canonical executable comparison key used by every backend owner."""
    if not isinstance(path, str) or not path.strip():
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def process_identity_matches(actual: Optional[WorkerIdentity], expected,
                             *, start_tolerance_s: float = 5.0) -> bool:
    """Strict PID/start/executable match; unverifiable values fail closed."""
    if actual is None or expected is None:
        return False
    getter = expected.get if isinstance(expected, dict) else lambda k, d=None: getattr(expected, k, d)
    pid = getter("pid")
    started = getter("process_start_time")
    executable = getter("executable_path")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if isinstance(started, bool) or not isinstance(started, (int, float)) or started <= 0:
        return False
    if not normalize_executable_path(executable):
        return False
    if actual.pid != pid:
        return False
    if abs(float(actual.process_start_time) - float(started)) > start_tolerance_s:
        return False
    return normalize_executable_path(actual.executable_path) == normalize_executable_path(executable)


def backend_process_matches(identity: Optional[WorkerIdentity], backend: str) -> bool:
    """Prove that an identity is an executable for the requested backend."""
    if identity is None or backend not in _BACKEND_EXECUTABLE_PREFIXES:
        return False
    name = os.path.splitext(os.path.basename(identity.executable_path))[0].lower()
    return any(name.startswith(prefix)
               for prefix in _BACKEND_EXECUTABLE_PREFIXES[backend])


def _process_parent_map() -> dict[int, int]:
    """Return PID->PPID without using process names for ownership decisions."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == INVALID_HANDLE_VALUE:
            return {}
        result = {}
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                result[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return result

    result = {}
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return result
    for child in proc_root.iterdir():
        if not child.name.isdigit():
            continue
        try:
            stat = (child / "stat").read_text(encoding="utf-8")
            # comm may contain spaces inside parentheses; ppid is field 4.
            tail = stat.rsplit(")", 1)[1].strip().split()
            result[int(child.name)] = int(tail[1])
        except (OSError, ValueError, IndexError):
            continue
    return result


def descendant_pids(root_pid: int) -> list[int]:
    """Return the temporally valid process tree below an exact root PID.

    Windows retains the creator PID after the creator exits.  If that PID is
    later reused, a PPID-only walk can attach historical children to the new
    process instance.  Every edge therefore also proves that the child was
    created no earlier than the concrete parent instance.
    """
    if isinstance(root_pid, bool) or not isinstance(root_pid, int) or root_pid <= 0:
        return []
    root_identity = get_process_identity(root_pid)
    if root_identity is None:
        return []
    parents = _process_parent_map()
    pending = [(root_pid, root_identity.process_start_time)]
    descendants = []
    seen = {root_pid}
    while pending:
        parent, parent_started = pending.pop(0)
        children = sorted(pid for pid, ppid in parents.items()
                          if ppid == parent and pid not in seen)
        for pid in children:
            seen.add(pid)
            child_identity = get_process_identity(pid)
            if child_identity is None:
                continue
            # FILETIME is precise on Windows; one second tolerates platform
            # conversion/rounding without accepting an old process instance.
            if child_identity.process_start_time + 1.0 < parent_started:
                continue
            descendants.append(pid)
            pending.append((pid, child_identity.process_start_time))
    return descendants


def is_descendant_pid(pid: int, root_pid: int) -> bool:
    return pid in descendant_pids(root_pid)


def resolve_backend_process_identity(supervisor_pid: int, backend: str,
                                     *, timeout_s: float = 10.0):
    """Resolve the actual EDA process and optional wrapper supervisor.

    Vivado is launched directly, while Windows XSCT/XSDB normally start under
    ``cmd.exe /c <tool>.bat`` and execute ``tclsh85t.exe`` below it.  Ownership
    is proven by the exact parent tree; process-name matching is used only to
    reject shell wrappers, never to kill or claim an unrelated process.

    Returns ``(actual_identity, supervisor_identity_or_none)`` or ``(None,
    supervisor_identity_or_none)`` when the actual tool cannot be proven.
    """
    supervisor = get_process_identity(supervisor_pid)
    if supervisor is None:
        return None, None
    basename = os.path.basename(supervisor.executable_path).lower()
    if basename not in _SUPERVISOR_NAMES:
        return (supervisor, None) if backend_process_matches(
            supervisor, backend) else (None, None)

    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        candidates = []
        for pid in descendant_pids(supervisor_pid):
            ident = get_process_identity(pid)
            if ident is None:
                continue
            name = os.path.basename(ident.executable_path).lower()
            if name in _SUPERVISOR_NAMES:
                continue
            if backend_process_matches(ident, backend):
                candidates.append(ident)
        if len(candidates) == 1:
            return candidates[0], supervisor
        if len(candidates) > 1:
            # Multiple matching tool processes cannot be assigned to a single
            # Ledger worker without guessing ownership.
            return None, supervisor
        if time.monotonic() >= deadline:
            return None, supervisor
        time.sleep(0.05)


def is_pid_alive(pid):
    if not pid or pid <= 0: return False
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}",
                                "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return False
            for row in csv.reader(io.StringIO(r.stdout)):
                if len(row) < 2:
                    continue
                try:
                    if int(row[1]) == int(pid):
                        return True
                except (TypeError, ValueError):
                    continue
            return False
        else:
            import signal; os.kill(pid, 0); return True
    except Exception: return False


def get_process_identity(pid) -> Optional[WorkerIdentity]:
    """Read full process identity. Returns None if ANY field is unreadable (fail-closed)."""
    if not pid or pid <= 0: return None
    if not is_pid_alive(pid): return None
    start_time = _get_start_time(pid)
    if start_time is None: return None
    exe = _get_exe(pid)
    if exe is None: return None
    args = _get_args(pid)
    return WorkerIdentity(pid=pid, process_start_time=start_time, executable_path=exe, executable_args=args)


def verify_worker_identity(pid, expected):
    """Verify pid matches expected identity. Returns False if mismatch or unverifiable."""
    return process_identity_matches(get_process_identity(pid), expected)


def kill_process_tree_exact(pid):
    if not pid or pid <= 0: return False
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
            return r.returncode in (0, 128)
        else:
            import signal; os.killpg(pid, signal.SIGKILL); return True
    except Exception: return False


def _get_start_time(pid):
    try:
        if os.name == "nt":
            import ctypes; from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k.OpenProcess(0x0400, False, pid)
            if not h: return None
            fc = wintypes.FILETIME(); _ = wintypes.FILETIME()
            ok = k.GetProcessTimes(h, ctypes.byref(fc), ctypes.byref(_), ctypes.byref(_), ctypes.byref(_))
            k.CloseHandle(h)
            if not ok: return None
            t = (fc.dwHighDateTime << 32) + fc.dwLowDateTime
            return t / 10_000_000.0 - 11644473600.0
        else:
            return os.stat(f"/proc/{pid}").st_ctime
    except Exception: return None


def _get_exe(pid):
    try:
        if os.name == "nt":
            import ctypes; from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k.OpenProcess(0x0400 | 0x0010, False, pid)
            if not h: return None
            buf = ctypes.create_unicode_buffer(260); sz = wintypes.DWORD(260)
            ok = k.QueryFullProcessImageNameW(h, 0, ctypes.byref(buf), ctypes.byref(sz))
            k.CloseHandle(h)
            return buf.value if ok else None
        else:
            return os.readlink(f"/proc/{pid}/exe")
    except Exception: return None


def _get_args(pid):
    try:
        if os.name == "nt":
            import ctypes; from ctypes import wintypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            # Simplified: not trivially available on Windows without NtQueryInformationProcess
            return None
        else:
            return open(f"/proc/{pid}/cmdline", "rb").read().decode().split("\x00")
    except Exception: return None
