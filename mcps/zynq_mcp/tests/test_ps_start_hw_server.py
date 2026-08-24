"""test_ps_start_hw_server.py — B12-N3 ps_start_hw_server tool.

Component tests exercise the production `start_hw_server` entry with mocked
TCP probe / exe resolution / subprocess spawn (MOCK_ONLY evidence for the
fail-closed paths), matching the pattern of the other process-free PS local
tools.  The real-spawn host_live test launches the actual hw_server.exe,
verifies the port reaches LISTENING, and cleans up only the process it
started (already_running branch when an hw_server pre-exists — never touches
it).
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time

import pytest

from mcps.zynq_mcp.domains.ps import hw_server_start
from mcps.zynq_mcp.domains.ps.hw_server_start import (
    DEFAULT_URL,
    start_hw_server,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")

_URL = "localhost:3121"


class _FakeStderr:
    def __init__(self, data: bytes = b""):
        self._data = data

    async def read(self):
        return self._data


class FakeProc:
    """Minimal asyncio.subprocess.Process double for spawn mocks."""

    def __init__(self, pid=12345, returncode=None, stderr_data=b""):
        self.pid = pid
        self.returncode = returncode
        self._stderr = _FakeStderr(stderr_data)

    @property
    def stderr(self):
        return self._stderr


# ── component tests (mocked probe / spawn; no real hw_server) ──────────────


async def test_already_running_reuse(monkeypatch):
    """Listening port → already_running:true and the spawn is never touched."""
    spawned = []

    async def _open(host, port, timeout=2.0):
        return True

    async def _spawn(exe):
        spawned.append(exe)
        raise AssertionError("spawn must not run when already listening")

    monkeypatch.setattr(hw_server_start, "tcp_port_open", _open)
    monkeypatch.setattr(hw_server_start, "spawn_hw_server", _spawn)

    result = await start_hw_server(url=_URL)

    assert result["status"] == "success", result
    assert result["data"]["already_running"] is True
    assert result["data"]["url"] == _URL
    assert spawned == [], "existing hw_server must never be touched"


async def test_exe_missing_fail_closed(monkeypatch):
    """No exe → ENV_ERROR / HW_SERVER_NOT_FOUND (fail-closed)."""
    async def _open(host, port, timeout=2.0):
        return False

    monkeypatch.setattr(hw_server_start, "tcp_port_open", _open)
    monkeypatch.setattr(hw_server_start, "resolve_exe", lambda ep: None)

    result = await start_hw_server(url=_URL)

    assert result["status"] == "error", result
    err = result["error"]
    assert err["code"] == "ENV_ERROR", result
    assert err["details"]["reason_code"] == "HW_SERVER_NOT_FOUND", result


async def test_spawn_failure(monkeypatch):
    """spawn returns None → TOOL_ERROR / HW_SERVER_START_FAILED."""
    async def _open(host, port, timeout=2.0):
        return False

    async def _spawn(exe):
        return None

    monkeypatch.setattr(hw_server_start, "tcp_port_open", _open)
    monkeypatch.setattr(hw_server_start, "resolve_exe",
                        lambda ep: r"D:\fake\hw_server.exe")
    monkeypatch.setattr(hw_server_start, "spawn_hw_server", _spawn)

    result = await start_hw_server(url=_URL)

    assert result["status"] == "error", result
    err = result["error"]
    assert err["code"] == "TOOL_ERROR", result
    assert err["details"]["reason_code"] == "HW_SERVER_START_FAILED", result


async def test_readiness_timeout(monkeypatch):
    """Port never opens within the bound → TOOL_ERROR / HW_SERVER_START_TIMEOUT."""
    async def _open(host, port, timeout=2.0):
        return False

    async def _spawn(exe):
        return FakeProc(pid=9999, returncode=None)

    monkeypatch.setattr(hw_server_start, "tcp_port_open", _open)
    monkeypatch.setattr(hw_server_start, "resolve_exe",
                        lambda ep: r"D:\fake\hw_server.exe")
    monkeypatch.setattr(hw_server_start, "spawn_hw_server", _spawn)
    # Keep the test fast while still exercising the real bounded poll loop.
    monkeypatch.setattr(hw_server_start, "READY_TIMEOUT_S", 0.6)
    monkeypatch.setattr(hw_server_start, "READY_POLL_INTERVAL_S", 0.05)

    result = await start_hw_server(url=_URL)

    assert result["status"] == "error", result
    err = result["error"]
    assert err["code"] == "TOOL_ERROR", result
    assert err["details"]["reason_code"] == "HW_SERVER_START_TIMEOUT", result
    assert err["details"]["pid"] == 9999, result


async def test_early_exit_reports_stderr(monkeypatch):
    """Process exits before listening → stderr summary in the error details."""
    async def _open(host, port, timeout=2.0):
        return False

    async def _spawn(exe):
        return FakeProc(pid=7777, returncode=1, stderr_data=b"bind: permission denied\n")

    monkeypatch.setattr(hw_server_start, "tcp_port_open", _open)
    monkeypatch.setattr(hw_server_start, "resolve_exe",
                        lambda ep: r"D:\fake\hw_server.exe")
    monkeypatch.setattr(hw_server_start, "spawn_hw_server", _spawn)

    result = await start_hw_server(url=_URL)

    assert result["status"] == "error", result
    err = result["error"]
    assert err["code"] == "TOOL_ERROR", result
    assert err["details"]["reason_code"] == "HW_SERVER_START_FAILED", result
    assert err["details"]["exit_code"] == 1, result
    assert "permission denied" in err["details"]["stderr"], result


async def test_invalid_url(monkeypatch):
    """Malformed url → INVALID_ARGUMENT / INVALID_URL."""
    result = await start_hw_server(url="not-a-valid-url")
    assert result["status"] == "error", result
    err = result["error"]
    assert err["code"] == "INVALID_ARGUMENT", result
    assert err["details"]["reason_code"] == "INVALID_URL", result


# ── registration / wiring checks (no process) ───────────────────────────────


async def test_wiring_registered_everywhere():
    """ps_start_hw_server is registered in capabilities + dispatcher + local
    process-free category (a missing wiring is caught without any process)."""
    from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
    from mcps.zynq_mcp.control.domain_runner import _PS_LOCAL_DIRECT_TOOLS
    from mcps.zynq_mcp import dispatcher

    names = {t.name for t in ALL_TOOLS}
    assert "ps_start_hw_server" in names
    assert "ps_start_hw_server" in dispatcher._PS_TOOL_NAMES
    assert "ps_start_hw_server" in dispatcher._PS_TOOL_MAP
    assert "ps_start_hw_server" in _PS_LOCAL_DIRECT_TOOLS

    tool = next(t for t in ALL_TOOLS if t.name == "ps_start_hw_server")
    props = tool.inputSchema.get("properties", {})
    assert "url" in props and "exe_path" in props
    # url/exe_path optional; session_id injected by _inject_ps_session_schema
    assert "url" not in tool.inputSchema.get("required", [])
    assert "exe_path" not in tool.inputSchema.get("required", [])
    assert "session_id" in props


# ── host_live: real hw_server.exe spawn (detached) ──────────────────────────


def _port_open(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _hw_server_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq hw_server.exe",
             "/FO", "LIST", "/NH"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    pids: set[int] = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("PID:"):
            try:
                pids.add(int(line.split(":", 1)[1].strip()))
            except ValueError:
                pass
    return pids


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   capture_output=True, text=True, timeout=20)


@pytest.mark.host_live
async def test_host_live_real_spawn_and_cleanup():
    """Real detached spawn of hw_server.exe → port LISTENING → fields correct.

    Records hw_server PIDs before the call.  If an hw_server is already
    listening the tool must take the already_running branch and the test
    skips termination (it never touches the pre-existing process).  Otherwise
    only the PID this test started is cleaned up afterwards.
    """
    exe = hw_server_start.resolve_exe(None)
    if exe is None:
        pytest.skip("hw_server.exe not found (resolve_exe returned None)")

    host, port = hw_server_start.parse_host_port(DEFAULT_URL)
    before = _hw_server_pids()

    if _port_open(host, port):
        # Pre-existing hw_server: the tool must report already_running and
        # must NOT have started anything new.
        result = await start_hw_server(url=DEFAULT_URL)
        assert result["status"] == "success", result
        assert result["data"]["already_running"] is True, result
        assert result["data"]["url"] == DEFAULT_URL, result
        return

    started_pid = None
    try:
        result = await start_hw_server(url=DEFAULT_URL)
        assert result["status"] == "success", result
        data = result["data"]
        assert data["started"] is True, result
        started_pid = data["pid"]
        assert isinstance(started_pid, int) and started_pid > 0, result
        assert data["exe"] == exe, result
        assert data["port"] == port, result
        assert data["url"] == DEFAULT_URL, result
        assert started_pid not in before, "must not reuse a pre-existing PID"

        # Real port must be LISTENING.
        assert _port_open(host, port), "port must be LISTENING after start"
    finally:
        if started_pid is not None:
            _kill_tree(started_pid)

    # Bounded wait: only this test's process must be gone and the port closed.
    deadline = time.time() + 20.0
    while time.time() < deadline:
        if started_pid not in _hw_server_pids() and not _port_open(host, port):
            break
        time.sleep(0.5)
    assert started_pid not in _hw_server_pids(), \
        f"test-started hw_server pid {started_pid} still alive after cleanup"
    assert not _port_open(host, port), "port still LISTENING after cleanup"
