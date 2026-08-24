"""hw_server_start.py — local hw_server auto-start (B12-N3).

`ps_start_hw_server` is the framework's self-sufficient path to a JTAG
hw_server: after an environment restart the old hw_server process is gone,
and the only public JTAG tool (`ps_connect_hw_server`) connects to an
*already running* instance.  This tool fills the gap with a zero-human,
idempotent local start:

  1. bounded TCP probe of ``url`` (default ``localhost:3121``) — if already
     listening, return ``{already_running: true, url}`` and never touch the
     existing process;
  2. resolve ``hw_server.exe`` (``exe_path`` override > the Vitis 2023.1
     default path) — missing exe fails closed with ``HW_SERVER_NOT_FOUND``;
  3. spawn with ``asyncio.create_subprocess_exec``; on Windows the child is
     created with ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` so it is
     detached from the MCP process/console and survives MCP shutdown (it is
     resident infrastructure);
  4. poll the real TCP port (0.5 s interval, 30 s total) for readiness —
     listening → ``{started: true, pid, exe, port, url}``; timeout →
     ``HW_SERVER_START_TIMEOUT``; early exit → stderr summary + fail-closed.

Only starts, never stops.  Stateless: the child process is deliberately NOT
tracked in the MCP resource registry (no JTAG lease is involved — the XSDB
connect step `ps_connect_hw_server` still owns that record).
"""
from __future__ import annotations

import asyncio
import os
import re
import socket
import time

from mcps.common.tool_response import success
from mcps.zynq_mcp.domains.ps import ps_error

DEFAULT_URL = "localhost:3121"
DEFAULT_HW_SERVER_EXE = r"D:\Xilinx\Vitis\2023.1\bin\unwrapped\win64.o\hw_server.exe"

TCP_PROBE_TIMEOUT_S = 2.0
READY_POLL_INTERVAL_S = 0.5
READY_TIMEOUT_S = 30.0

# Windows creation flags: DETACHED_PROCESS (0x8) | CREATE_NEW_PROCESS_GROUP
# (0x200).  The child gets no console and its own process group, so it is
# resident infrastructure that outlives the MCP server.
_WINDOWS_CREATION_FLAGS = 0x00000008 | 0x00000200

_URL_RE = re.compile(r"^(?:tcp:)?(.+?):(\d+)$")


def parse_host_port(url: str) -> tuple[str, int] | None:
    """Parse ``[tcp:]host:port`` into (host, port), or None when invalid."""
    if not isinstance(url, str):
        return None
    m = _URL_RE.match(url.strip())
    if not m:
        return None
    host, port_s = m.group(1), m.group(2)
    if not host.strip():
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    if port < 1 or port > 65535:
        return None
    return host.strip(), port


async def tcp_port_open(host: str, port: int, timeout: float = TCP_PROBE_TIMEOUT_S) -> bool:
    """Return True when a TCP connect to ``host:port`` succeeds within timeout.

    Bounded: a refused connection returns immediately, an unreachable host
    fails at ``timeout``.  Never raises.
    """
    loop = asyncio.get_running_loop()

    def _connect() -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _connect),
                                      timeout + 0.5)
    except (asyncio.TimeoutError, Exception):
        return False


def resolve_exe(exe_path: str | None) -> str | None:
    """Resolve hw_server.exe: exe_path override > Vitis 2023.1 default path.

    Returns an existing file path or None.  Never raises.
    """
    if exe_path is not None:
        if not isinstance(exe_path, str) or not exe_path.strip():
            return None
        return exe_path if os.path.isfile(exe_path) else None
    return DEFAULT_HW_SERVER_EXE if os.path.isfile(DEFAULT_HW_SERVER_EXE) else None


async def spawn_hw_server(exe_path: str) -> asyncio.subprocess.Process | None:
    """Spawn hw_server detached. Returns the process, or None on failure."""
    kwargs: dict = {
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.DEVNULL,
        "stderr": asyncio.subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _WINDOWS_CREATION_FLAGS
    try:
        return await asyncio.create_subprocess_exec(exe_path, **kwargs)
    except (FileNotFoundError, OSError, Exception):
        return None


async def _drain_stderr(proc: asyncio.subprocess.Process,
                        timeout: float = 2.0) -> str:
    """Read a bounded stderr summary from an exited process. Never raises."""
    if getattr(proc, "stderr", None) is None:
        return ""
    try:
        data = await asyncio.wait_for(proc.stderr.read(), timeout=timeout)
    except (asyncio.TimeoutError, Exception):
        return ""
    text = (data or b"").decode("utf-8", errors="replace").strip()
    return text[:500]


async def start_hw_server(bridge=None, *, url: str = DEFAULT_URL,
                          exe_path: str | None = None) -> dict:
    """Locally auto-start hw_server (idempotent, detached, bounded wait).

    ``bridge`` is injected by the CommandRunner for the uniform ps_* calling
    convention and is unused (this is a process-free local tool — no XSDB
    shell, no EDA worker).
    """
    if not isinstance(url, str) or not url.strip():
        return ps_error("INVALID_URL",
                        f"url must be a non-empty string, got {url!r}")
    parsed = parse_host_port(url)
    if parsed is None:
        return ps_error("INVALID_URL",
                        f"url must be [tcp:]host:port, got {url!r}")
    host, port = parsed

    # 1. Already running?  Never touch the existing process.
    if await tcp_port_open(host, port, TCP_PROBE_TIMEOUT_S):
        return success(data={"already_running": True, "url": url}).to_dict()

    # 2. Resolve the executable (fail closed when absent).
    exe = resolve_exe(exe_path)
    if exe is None:
        return ps_error(
            "HW_SERVER_NOT_FOUND",
            "hw_server.exe not found; provide exe_path or install Vitis "
            f"2023.1 at {DEFAULT_HW_SERVER_EXE}",
            details={"exe_path": exe_path})

    # 3. Spawn detached.
    proc = await spawn_hw_server(exe)
    if proc is None or proc.pid is None:
        return ps_error(
            "HW_SERVER_START_FAILED",
            f"failed to launch hw_server from {exe}",
            details={"exe": exe})

    # 4. Bounded readiness wait on the real port.
    deadline = time.monotonic() + READY_TIMEOUT_S
    while True:
        if proc.returncode is not None:
            stderr = await _drain_stderr(proc)
            return ps_error(
                "HW_SERVER_START_FAILED",
                f"hw_server exited before listening (exit code "
                f"{proc.returncode})" + (f": {stderr}" if stderr else ""),
                details={"exit_code": proc.returncode, "stderr": stderr,
                         "exe": exe})
        if await tcp_port_open(host, port, TCP_PROBE_TIMEOUT_S):
            return success(data={"started": True, "pid": proc.pid,
                                 "exe": exe, "port": port,
                                 "url": url}).to_dict()
        if time.monotonic() >= deadline:
            return ps_error(
                "HW_SERVER_START_TIMEOUT",
                f"hw_server (pid {proc.pid}) did not listen on tcp:{host}:{port} "
                f"within {READY_TIMEOUT_S:.0f}s",
                details={"pid": proc.pid, "exe": exe, "port": port, "url": url})
        await asyncio.sleep(READY_POLL_INTERVAL_S)
