"""
env_probe.py — Environment probing for Vivado/Vitis/XSCT and USB-UART.

Read-only, no side effects. No JTAG enumeration. No hw_server connection.
Vivado probe uses TemporaryDirectory as cwd to prevent workdir artifacts.

Version verification is tool-specific:
  Vivado: -mode tcl with version -short Tcl command
  Vitis:  install metadata (data/version.bat XILINX_VERSION_VITIS)
  XSCT:   -eval "puts [version]; exit"

supported = true iff tool-specific version evidence confirms 2023.1.

Public probe functions accept an optional `runner` callable for testing:

  runner: Callable[[list[str], int], tuple[str, str, int | None]]

  Receives the command-line argument list and timeout in seconds.
  Must return (stdout, stderr, exit_code). exit_code=None means
  the command could not execute or timed out.
  This is a stable public contract; production code passes None.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable


# -- Data types --

@dataclass
class ToolProbeResult:
    name: str
    found: bool = False
    executable_path: str | None = None
    version: str | None = None
    full_version: str | None = None
    build: str | None = None
    version_source: str = "unverified"
    supported: bool = False
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UartDevice:
    port: str | None = None
    vid: str | None = None
    pid: str | None = None
    friendly_name: str | None = None
    present: bool = False
    role: str | None = None
    direction: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnvReport:
    schema_version: str = "1.0"
    generated_at: str = ""
    vivado: ToolProbeResult | None = None
    vitis: ToolProbeResult | None = None
    xsct: ToolProbeResult | None = None
    ps_uart_devices: list[UartDevice] = field(default_factory=list)
    pl_uart_devices: list[UartDevice] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "vivado": self.vivado.to_dict() if self.vivado else None,
            "vitis": self.vitis.to_dict() if self.vitis else None,
            "xsct": self.xsct.to_dict() if self.xsct else None,
            "ps_uart_devices": [u.to_dict() for u in self.ps_uart_devices],
            "pl_uart_devices": [u.to_dict() for u in self.pl_uart_devices],
            "warnings": list(self.warnings),
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


# -- constants --

_DEFAULT_EDA_ROOTS = [
    r"C:\Xilinx",
    r"D:\Xilinx",
]
_PREFERRED_VERSION = "2023.1"


# -- search roots --

def _get_search_roots(explicit_roots: list[str] | None = None) -> list[str]:
    """Resolve EDA search roots in priority order."""
    if explicit_roots is not None:
        return list(explicit_roots)

    roots: list[str] = []

    env_val = os.environ.get("ZYNQ_EDA_SEARCH_ROOTS", "")
    if env_val:
        for part in env_val.replace(";", os.pathsep).split(os.pathsep):
            part = part.strip()
            if part and os.path.isdir(part):
                roots.append(part)

    path_val = os.environ.get("PATH", "")
    for part in path_val.split(os.pathsep):
        part = part.strip()
        if part and os.path.isdir(part):
            low = part.lower()
            if any(kw in low for kw in ("vivado", "vitis", "xsct", "xilinx")):
                if part not in roots:
                    roots.append(part)

    for r in _DEFAULT_EDA_ROOTS:
        if os.path.isdir(r) and r not in roots:
            roots.append(r)

    return roots


# -- subprocess with process tree cleanup --

def _run_command(
    args: list[str],
    runner: Callable | None = None,
    timeout: int = 60,
    cwd: str | None = None,
) -> tuple[str, str, int | None]:
    """Run a command. On timeout, forcibly kill the process tree.
    Returns (stdout, stderr, exit_code_or_None)."""
    if runner is not None:
        return runner(args, timeout)

    proc = None
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=cwd)
        stdout, stderr = proc.communicate(timeout=timeout)
        return stdout, stderr, proc.returncode
    except FileNotFoundError:
        return "", "", None
    except subprocess.TimeoutExpired:
        if proc is not None:
            _kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return "", "", None
    except OSError:
        return "", "", None


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants by PID.
    Uses taskkill /T /PID on Windows, SIGKILL on POSIX.
    Does NOT kill by process name — only by the exact PID.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10)
        except Exception:
            pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


# -- version parsing --

def _parse_version_output(
    name: str, output: str, fallback_ver: str | None,
) -> tuple[str | None, str | None]:
    m = re.search(r'v(\d+\.\d+(?:\.\d+)?)', output)
    version = m.group(1) if m else fallback_ver
    m = re.search(r'[Bb]uild\s+(\d+)', output)
    build = m.group(1) if m else None
    return version, build


def _parse_xsct_version(output: str) -> str | None:
    """Parse XSCT 'xsct 2023.1.0' output to normalized '2023.1'."""
    m = re.search(r'xsct\s+(\S+)', output)
    if not m:
        return None
    raw = m.group(1)
    m2 = re.match(r'^(\d+\.\d+)', raw)
    return m2.group(1) if m2 else raw


# -- Installation discovery --

def _discover_installs(
    install_subdir: str, exe_name: str, search_roots: list[str] | None,
) -> list[tuple[str, str, str]]:
    """Returns list of (version, exe_path, install_dir)."""
    roots = _get_search_roots(search_roots)
    found: list[tuple[str, str, str]] = []

    for root in roots:
        if not os.path.isdir(root):
            continue
        subdir = os.path.join(root, install_subdir)
        if not os.path.isdir(subdir):
            continue
        try:
            for entry in os.listdir(subdir):
                ver_dir = os.path.join(subdir, entry)
                if not os.path.isdir(ver_dir):
                    continue
                exe = os.path.join(ver_dir, "bin", exe_name)
                if os.path.isfile(exe):
                    if not any(e[1] == exe for e in found):
                        found.append((entry, exe, ver_dir))
        except OSError:
            continue

    path_exe = shutil.which(exe_name)
    if path_exe and os.path.isfile(path_exe):
        if not any(e[1] == path_exe for e in found):
            pv = _infer_version_from_path(path_exe, install_subdir)
            found.append((pv, path_exe, os.path.dirname(os.path.dirname(path_exe))))

    return found


def _select_install(
    found: list[tuple[str, str, str]], name: str,
) -> tuple[str | None, str | None, str | None, list[str]]:
    """Select preferred install. Returns (version, exe_path, install_dir, warnings)."""
    if not found:
        return None, None, None, []
    warnings: list[str] = []
    matching = [(v, e, d) for v, e, d in found if v == _PREFERRED_VERSION]
    if matching:
        ver, exe, d = matching[0]
        for v, e, dd in found:
            if v != _PREFERRED_VERSION:
                warnings.append(
                    f"Found {name} {v} at {e}; selected {_PREFERRED_VERSION}")
    else:
        ver, exe, d = found[0]
        warnings.append(
            f"{name} {_PREFERRED_VERSION} not found; found {ver} at {exe}")
    return ver, exe, d, warnings


def _infer_version_from_path(exe_path: str, install_subdir: str) -> str:
    norm = os.path.normpath(exe_path).replace("\\", "/")
    parts = norm.split("/")
    for i, p in enumerate(parts):
        if p == install_subdir and i + 1 < len(parts):
            return parts[i + 1]
        if p == "bin" and i >= 1:
            return parts[i - 1]
    return "unknown"


# -- Tool-specific version verification --

def _verify_vivado(exe_path: str,
                   install_dir_version: str | None = None,
                   runner: Callable | None = None) -> (
    tuple[bool, str | None, str | None, str | None, str | None, str | None]
):
    """Verify Vivado version via Tcl mode in an isolated temp directory.
    Uses -nolog -nojournal -notrace to prevent workdir artifacts.
    Temp directory and Tcl script are always cleaned up.
    Caller's cwd is never modified.

    Returns (supported, version, full_version, build, version_source, reason_code_or_None).
    Reason codes:
      - ENV_VERSION_QUERY_FAILED: command could not execute or timed out
      - ENV_VERSION_UNPARSEABLE: empty or unparseable output
      - ENV_VERSION_UNSUPPORTED: command reports version X, install dir is X, but X != 2023.1
      - ENV_VERSION_MISMATCH: command reports version X, install dir says Y, and X != Y
    """
    if not exe_path:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    tcl_path = None
    tmpdir_obj = None

    try:
        tmpdir_obj = tempfile.TemporaryDirectory(prefix="vivado_probe_")
        tmpdir = tmpdir_obj.name
        tcl_path = os.path.join(tmpdir, "vivado_ver.tcl")
        with open(tcl_path, "w") as f:
            f.write('puts "__VERSION=[version -short]"; exit\n')
        args = ["cmd.exe", "/d", "/c", exe_path,
                "-mode", "tcl",
                "-nolog", "-nojournal", "-notrace",
                "-source", tcl_path]
        stdout, stderr, exit_code = _run_command(
            args, runner=runner, timeout=120, cwd=tmpdir)
    except Exception:
        stdout, stderr, exit_code = "", "", None
    finally:
        if tcl_path is not None:
            try:
                os.unlink(tcl_path)
            except OSError:
                pass
        if tmpdir_obj is not None:
            try:
                tmpdir_obj.cleanup()
            except Exception:
                pass

    if exit_code is None:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"
    if exit_code != 0:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    combined = stdout + stderr
    if not combined.strip():
        return False, None, None, None, "unverified", "ENV_VERSION_UNPARSEABLE"

    m = re.search(r'(?:^|\n)__VERSION=(\S+)', combined)
    if not m:
        return False, None, None, None, "unverified", "ENV_VERSION_UNPARSEABLE"

    raw_version = m.group(1).strip()
    m_build = re.search(r'SW Build\s+(\d+)', combined)
    parsed_b = m_build.group(1) if m_build else None

    supported = (raw_version == _PREFERRED_VERSION)
    if supported:
        return True, raw_version, raw_version, parsed_b, "version_command", None
    # Not the preferred version — distinguish unsupported vs mismatch
    if install_dir_version is not None and raw_version == install_dir_version:
        return False, raw_version, raw_version, parsed_b, "version_command", "ENV_VERSION_UNSUPPORTED"
    return False, raw_version, raw_version, parsed_b, "version_command", "ENV_VERSION_MISMATCH"


def _verify_vitis(install_dir: str) -> (
    tuple[bool, str | None, str | None, str | None, str | None, str | None]
):
    """Verify Vitis version via install metadata data/version.bat."""
    if not install_dir:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    vb_path = os.path.join(install_dir, "data", "version.bat")
    if not os.path.isfile(vb_path):
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    try:
        with open(vb_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    m = re.search(r'XILINX_VERSION_VITIS=(\S+)', content)
    if not m:
        return False, None, None, None, "unverified", "ENV_VERSION_UNPARSEABLE"

    raw = m.group(1).strip()
    supported = (raw == _PREFERRED_VERSION)
    return supported, raw, raw, None, "install_metadata", (
        None if supported else "ENV_VERSION_MISMATCH")


def _verify_xsct(exe_path: str, runner: Callable | None = None) -> (
    tuple[bool, str | None, str | None, str | None, str | None, str | None]
):
    """Verify XSCT version via -eval 'puts [version]; exit'."""
    if not exe_path:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    args = ["cmd.exe", "/d", "/c", exe_path, "-eval",
            "puts [version]; exit"]
    stdout, stderr, exit_code = _run_command(args, runner=runner, timeout=60)

    if exit_code is None:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"
    if exit_code != 0:
        return False, None, None, None, "unverified", "ENV_VERSION_QUERY_FAILED"

    combined = stdout + stderr
    if not combined.strip():
        return False, None, None, None, "unverified", "ENV_VERSION_UNPARSEABLE"

    raw_full = combined.split("\n")[0].strip()
    normalized = _parse_xsct_version(raw_full)
    if normalized is None:
        return False, raw_full, None, None, "unverified", "ENV_VERSION_UNPARSEABLE"

    supported = (normalized == _PREFERRED_VERSION)
    if supported:
        return True, normalized, raw_full, None, "version_command", None
    return False, normalized, raw_full, None, "version_command", "ENV_VERSION_MISMATCH"


# -- Public tool probes --

def probe_vivado(
    search_roots: list[str] | None = None,
    runner: Callable | None = None,
) -> ToolProbeResult:
    found = _discover_installs(
        "Vivado", "vivado.bat" if os.name == "nt" else "vivado", search_roots)
    ver, exe, d, warnings = _select_install(found, "vivado")
    if ver is None:
        return ToolProbeResult(name="vivado", found=False,
                               error_code="ENV_ERROR",
                               reason_code="ENV_VIVADO_NOT_FOUND")
    sup, final_ver, full_ver, build, vsrc, rc = _verify_vivado(
        exe, install_dir_version=ver, runner=runner)
    return ToolProbeResult(
        name="vivado", found=True, executable_path=exe,
        version=final_ver or ver, full_version=full_ver, build=build,
        version_source=vsrc, supported=sup, warnings=warnings,
        error_code=None if sup else "ENV_ERROR",
        reason_code=rc if not sup else None,
    )


def probe_vitis(
    search_roots: list[str] | None = None,
    runner: Callable | None = None,
) -> ToolProbeResult:
    found = _discover_installs(
        "Vitis", "vitis.bat" if os.name == "nt" else "vitis", search_roots)
    ver, exe, d, warnings = _select_install(found, "vitis")
    if ver is None:
        return ToolProbeResult(name="vitis", found=False,
                               error_code="ENV_ERROR",
                               reason_code="ENV_VITIS_NOT_FOUND")
    sup, final_ver, full_ver, build, vsrc, rc = _verify_vitis(d)
    return ToolProbeResult(
        name="vitis", found=True, executable_path=exe,
        version=final_ver or ver, full_version=full_ver, build=build,
        version_source=vsrc, supported=sup, warnings=warnings,
        error_code=None if sup else "ENV_ERROR",
        reason_code=rc if not sup else None,
    )


def probe_xsct(
    search_roots: list[str] | None = None,
    runner: Callable | None = None,
) -> ToolProbeResult:
    found = _discover_installs(
        "Vitis", "xsct.bat" if os.name == "nt" else "xsct", search_roots)
    ver, exe, d, warnings = _select_install(found, "xsct")
    if ver is None:
        return ToolProbeResult(name="xsct", found=False,
                               error_code="ENV_ERROR",
                               reason_code="ENV_XSCT_NOT_FOUND")
    sup, final_ver, full_ver, build, vsrc, rc = _verify_xsct(exe, runner=runner)
    return ToolProbeResult(
        name="xsct", found=True, executable_path=exe,
        version=final_ver or ver, full_version=full_ver, build=build,
        version_source=vsrc, supported=sup, warnings=warnings,
        error_code=None if sup else "ENV_ERROR",
        reason_code=rc if not sup else None,
    )


# -- USB-UART enumeration --

def _get_active_com_ports() -> set[str]:
    r"""Read HARDWARE\DEVICEMAP\SERIALCOMM for active COM ports."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"HARDWARE\DEVICEMAP\SERIALCOMM")
        ports = set()
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
                ports.add(value)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        return ports
    except OSError:
        return set()


def _mark_uart_presence(devices: list[UartDevice],
                        active_ports: set[str]) -> list[UartDevice]:
    """Pure function: set present=True on devices whose port is in active_ports.
    Returns a new list (does not mutate input)."""
    result = []
    for d in devices:
        present = (d.port is not None and d.port in active_ports)
        result.append(UartDevice(
            port=d.port, vid=d.vid, pid=d.pid,
            friendly_name=d.friendly_name,
            present=present, role=d.role, direction=d.direction))
    return result


def probe_uart_devices(
    device_enumerator: Callable | None = None,
) -> tuple[list[UartDevice], list[UartDevice]]:
    r"""Enumerate USB-UART devices via Windows registry.

    PS UART: CP2102-GM, VID=0x10C4, PID=0xEA60, bidirectional
    PL UART: CH340 (lab fixture), VID=0x1A86, PID=0x7523, board_to_host_only

    present is determined by whether the device's COM port appears in
    HARDWARE\DEVICEMAP\SERIALCOMM (active driver enumeration).
    """
    if device_enumerator is not None:
        return device_enumerator()

    if os.name != "nt":
        return [], []

    active_ports = _get_active_com_ports()
    raw_ps: list[UartDevice] = []
    raw_pl: list[UartDevice] = []

    try:
        import winreg

        usb_key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Enum\USB")

        i = 0
        while True:
            try:
                vid_entry = winreg.EnumKey(usb_key, i)
                i += 1
            except OSError:
                break

            m = re.match(r'^VID_([0-9A-F]{4})&PID_([0-9A-F]{4})$',
                         vid_entry, re.IGNORECASE)
            if not m:
                continue
            vid_str = m.group(1).upper()
            pid_str = m.group(2).upper()

            try:
                vid_key = winreg.OpenKey(usb_key, vid_entry)
            except OSError:
                continue

            j = 0
            while True:
                try:
                    instance = winreg.EnumKey(vid_key, j)
                    j += 1
                except OSError:
                    break

                try:
                    inst_key = winreg.OpenKey(vid_key, instance)
                except OSError:
                    continue

                port = None
                try:
                    dp_key = winreg.OpenKey(inst_key, "Device Parameters")
                    try:
                        port, _ = winreg.QueryValueEx(dp_key, "PortName")
                    except OSError:
                        port = None
                    winreg.CloseKey(dp_key)
                except OSError:
                    port = None

                friendly = None
                for nf in ("FriendlyName", "DeviceDesc"):
                    try:
                        friendly, _ = winreg.QueryValueEx(inst_key, nf)
                        if friendly:
                            break
                    except OSError:
                        continue
                if friendly is None:
                    friendly = f"USB\\VID_{vid_str}&PID_{pid_str}"

                present = (port is not None and port in active_ports)

                device = UartDevice(
                    port=port, vid=f"0x{vid_str}", pid=f"0x{pid_str}",
                    friendly_name=friendly, present=present)

                if vid_str == "10C4" and pid_str == "EA60":
                    device.role = "ps_uart"
                    device.direction = "bidirectional"
                    raw_ps.append(device)
                elif vid_str == "1A86" and pid_str == "7523":
                    device.role = "pl_uart_lab_fixture"
                    device.direction = "board_to_host_only"
                    raw_pl.append(device)

                winreg.CloseKey(inst_key)

            winreg.CloseKey(vid_key)
        winreg.CloseKey(usb_key)

    except (ImportError, OSError):
        raw_ps = []
        raw_pl = []

    ps_devices = _dedup_uart(raw_ps)
    pl_devices = _dedup_uart(raw_pl)

    return ps_devices, pl_devices


def _dedup_uart(devices: list[UartDevice]) -> list[UartDevice]:
    seen = set()
    result = []
    for d in sorted(devices, key=lambda x: (0 if x.present else 1)):
        key = (d.port or d.friendly_name, d.vid, d.pid)
        if key not in seen:
            seen.add(key)
            result.append(d)
    return result


# -- Top-level probe --

def probe_all(
    search_roots: list[str] | None = None,
    runner: Callable | None = None,
    device_enumerator: Callable | None = None,
) -> EnvReport:
    report = EnvReport(generated_at=datetime.now(timezone.utc).isoformat())
    report.vivado = probe_vivado(search_roots=search_roots, runner=runner)
    report.vitis = probe_vitis(search_roots=search_roots, runner=runner)
    report.xsct = probe_xsct(search_roots=search_roots, runner=runner)
    ps, pl = probe_uart_devices(device_enumerator=device_enumerator)
    report.ps_uart_devices = list(ps)
    report.pl_uart_devices = list(pl)
    for tool in (report.vivado, report.vitis, report.xsct):
        if tool and tool.warnings:
            report.warnings.extend(tool.warnings)
    return report
