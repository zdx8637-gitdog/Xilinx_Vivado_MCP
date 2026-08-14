"""test_xsdb_bridge.py — B06 Agent A unit + host_live tests.

Evidence levels:
  - Unit tests (no marker): real fake-shell subprocess through the production
    bridge entry points (start/eval/stop) — TEST_HELPER level process.
  - host_live tests: real xsdb.bat subprocess — IMPLEMENTED_AND_TESTED
    (require a Xilinx install; skipped via skipif when xsdb is absent).
"""
import asyncio
import os
import sys

import pytest

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import (
    XsdbBridge,
    XsdbBridgeError,
    _build_launch_cmd,
    find_xsdb,
)

REAL_XSDB = find_xsdb()
needs_xsdb = pytest.mark.skipif(
    REAL_XSDB is None, reason="no xsdb executable found on this host")


# ---------------------------------------------------------------------------
# Unit tests (no marker, fake Tcl shell subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_missing_executable_raises():
    """start() with an unresolvable executable raises XsdbBridgeError."""
    bridge = XsdbBridge(xsdb_path=os.path.join("nonexistent", "xsdb.exe"))
    with pytest.raises(XsdbBridgeError):
        await bridge.start("")
    assert not bridge.ready
    assert bridge.pid is None


@pytest.mark.asyncio
async def test_start_sets_pid_and_ready(fake_xsdb):
    """start() launches a live child; pid is set and ready is True."""
    await fake_xsdb.start("")
    assert fake_xsdb.ready
    assert fake_xsdb.pid is not None
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_stop_clears_pid_and_ready(fake_xsdb):
    """stop() terminates the child; pid -> None, ready -> False."""
    await fake_xsdb.start("")
    assert fake_xsdb.pid is not None
    await fake_xsdb.stop()
    assert fake_xsdb.pid is None
    assert not fake_xsdb.ready


@pytest.mark.asyncio
async def test_eval_simple(fake_xsdb):
    """eval('puts hello') returns success with data 'hello'."""
    await fake_xsdb.start("")
    res = await fake_xsdb.eval("puts hello")
    assert res["status"] == "success"
    assert res["data"] == "hello"
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_eval_multiline_data(fake_xsdb):
    """Multi-line command output is preserved between the sentinels."""
    await fake_xsdb.start("")
    res = await fake_xsdb.eval("puts line1\nputs line2")
    assert res["status"] == "success"
    assert res["data"] == "line1\nline2"
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_eval_sequence_increments(fake_xsdb):
    """Three evals: seq increments and outputs never cross-contaminate."""
    await fake_xsdb.start("")
    try:
        r1 = await fake_xsdb.eval("puts one")
        r2 = await fake_xsdb.eval("puts two")
        r3 = await fake_xsdb.eval("puts three")
        assert [r["data"] for r in (r1, r2, r3)] == ["one", "two", "three"]
        assert fake_xsdb.seq == 3
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_eval_timeout_raises(fake_xsdb):
    """A command that never returns triggers XsdbBridgeError + process kill."""
    await fake_xsdb.start("")
    try:
        with pytest.raises(XsdbBridgeError, match="timeout"):
            await fake_xsdb.eval("__HANG__", timeout_s=0.5)
        assert not fake_xsdb.ready
        assert fake_xsdb.pid is None
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_stop_idempotent(fake_xsdb):
    """Calling stop() twice must not raise."""
    await fake_xsdb.start("")
    await fake_xsdb.stop()
    await fake_xsdb.stop()
    assert fake_xsdb.pid is None
    assert not fake_xsdb.ready


@pytest.mark.asyncio
async def test_eval_after_stop_returns_error(fake_xsdb):
    """eval() after stop() returns an error dict, never hangs."""
    await fake_xsdb.start("")
    await fake_xsdb.stop()
    res = await fake_xsdb.eval("puts hi")
    assert res["status"] == "error"
    assert res["error"]["code"] == "XSDM_EVAL_ERROR"
    assert res["error"]["details"]["reason_code"] == "XSDM_PROCESS_DEAD"


@pytest.mark.asyncio
async def test_external_kill_returns_error(fake_xsdb):
    """A subprocess killed externally: eval returns error dict, no hang."""
    await fake_xsdb.start("")
    proc = fake_xsdb._proc
    proc.kill()
    await proc.wait()
    res = await fake_xsdb.eval("puts hi")
    assert res["status"] == "error"
    assert res["error"]["details"]["reason_code"] == "XSDM_PROCESS_DEAD"
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_concurrent_eval_serialized(fake_xsdb):
    """A second eval queues on the single channel while the first is held."""
    await fake_xsdb.start("")
    try:
        async with fake_xsdb._lock:
            task = asyncio.create_task(fake_xsdb.eval("puts queued"))
            await asyncio.sleep(0)
            assert not task.done()
        res = await asyncio.wait_for(task, timeout=5.0)
        assert res["status"] == "success"
        assert res["data"] == "queued"
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_eval_tcl_error_returns_error(fake_xsdb):
    """An 'ERROR:' line in output yields a Tcl-error result dict."""
    await fake_xsdb.start("")
    try:
        res = await fake_xsdb.eval("__ERROR__")
        assert res["status"] == "error"
        assert res["error"]["code"] == "XSDM_EVAL_ERROR"
        assert res["error"]["details"]["reason_code"] == "XSDM_TCL_ERROR"
        assert "simulated" in res["error"]["message"]
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_eval_stderr_merged_into_error(fake_xsdb):
    """Non-empty stderr is merged into an error result (fail-closed)."""
    await fake_xsdb.start("")
    try:
        res = await fake_xsdb.eval("__STDERR__")
        assert res["status"] == "error"
        assert res["error"]["details"]["reason_code"] == "XSDM_STDERR_OUTPUT"
        assert "noise" in res["error"]["message"]
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_start_connect_sets_hw_connected(fake_xsdb):
    """start(hw_server_url=...) drives connect and flips hw_connected."""
    await fake_xsdb.start("tcp:localhost:3121")
    try:
        assert fake_xsdb.hw_connected
    finally:
        await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_start_without_url_not_connected(fake_xsdb):
    """start('') leaves hw_connected False."""
    await fake_xsdb.start("")
    assert not fake_xsdb.hw_connected
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_manual_connect_sets_hw_connected(fake_xsdb):
    """eval('connect -url ...') after start('') flips hw_connected True.

    Reproduces the reported P2 gap: a manual connect through eval() (the
    domain layer's connect_hw_server path) was not reflected in hw_connected,
    breaking downstream require_connected() checks.
    """
    await fake_xsdb.start("")
    assert not fake_xsdb.hw_connected
    res = await fake_xsdb.eval("connect -url tcp:localhost:3121")
    assert res["status"] == "success"
    assert fake_xsdb.hw_connected
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_manual_disconnect_clears_hw_connected(fake_xsdb):
    """eval('disconnect') after a connected start flips hw_connected False."""
    await fake_xsdb.start("tcp:localhost:3121")
    assert fake_xsdb.hw_connected
    res = await fake_xsdb.eval("disconnect")
    assert res["status"] == "success"
    assert not fake_xsdb.hw_connected
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_non_connection_command_leaves_hw_connected_unchanged(fake_xsdb):
    """Unrelated commands never alter hw_connected."""
    await fake_xsdb.start("tcp:localhost:3121")
    assert fake_xsdb.hw_connected
    res = await fake_xsdb.eval("targets")
    assert res["status"] == "success"
    assert fake_xsdb.hw_connected
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_failed_connect_never_claims_connected(fake_xsdb):
    """A connect whose eval fails leaves hw_connected False (fail-closed).

    The eval is made to fail by appending the fake shell's __STDERR__ marker
    so the connection command never becomes evidence for a True flag.
    """
    await fake_xsdb.start("")
    assert not fake_xsdb.hw_connected
    res = await fake_xsdb.eval("connect -url tcp:localhost:3121\n__STDERR__")
    assert res["status"] == "error"
    assert not fake_xsdb.hw_connected
    await fake_xsdb.stop()


@pytest.mark.asyncio
async def test_failed_reconnect_preserves_previous_state(fake_xsdb):
    """A failed reconnect preserves the prior connected state (no guessing)."""
    await fake_xsdb.start("tcp:localhost:3121")
    assert fake_xsdb.hw_connected
    res = await fake_xsdb.eval("connect -url tcp:localhost:3121\n__STDERR__")
    assert res["status"] == "error"
    assert fake_xsdb.hw_connected
    await fake_xsdb.stop()


def test_set_hw_connected_explicit():
    """set_hw_connected() lets the domain layer manage the flag explicitly."""
    bridge = XsdbBridge()
    assert not bridge.hw_connected
    bridge.set_hw_connected(True)
    assert bridge.hw_connected
    bridge.set_hw_connected(False)
    assert not bridge.hw_connected


@pytest.mark.asyncio
async def test_restart_after_stop(fake_xsdb):
    """stop() then start() yields a fresh working process."""
    await fake_xsdb.start("")
    first_pid = fake_xsdb.pid
    await fake_xsdb.stop()
    await fake_xsdb.start("")
    try:
        assert fake_xsdb.ready
        assert fake_xsdb.pid != first_pid
        res = await fake_xsdb.eval("puts again")
        assert res["status"] == "success"
        assert res["data"] == "again"
    finally:
        await fake_xsdb.stop()


# ---------------------------------------------------------------------------
# Executable resolution unit tests (pure env/path logic)
# ---------------------------------------------------------------------------

def _write_executable(tmp_path, name):
    variant = "xsdb.bat" if os.name == "nt" else "xsdb"
    p = tmp_path / variant
    p.write_text("", encoding="utf-8")
    return str(p)


def test_find_xsdb_env_var(monkeypatch, tmp_path):
    """Priority 1: an existing XSDM_EXEC full path wins."""
    fake = _write_executable(tmp_path, "xsdb")
    monkeypatch.setenv("XSDM_EXEC", fake)
    assert find_xsdb() == fake


def test_find_xsdb_env_var_missing_falls_through(monkeypatch, tmp_path):
    """A missing XSDM_EXEC falls through to VITIS_ROOT/bin."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    xsdb_bat = bin_dir / ("xsdb.bat" if os.name == "nt" else "xsdb")
    xsdb_bat.write_text("", encoding="utf-8")
    monkeypatch.setenv("XSDM_EXEC", str(tmp_path / "missing.bat"))
    monkeypatch.setenv("VITIS_ROOT", str(tmp_path))
    assert find_xsdb() == str(xsdb_bat)


def test_find_xsdb_default_install(monkeypatch, tmp_path):
    """Priority 3: default install dir resolves when env vars are unset."""
    import mcps.zynq_mcp.adapters.xsct.xsdb_bridge as mod
    monkeypatch.delenv("XSDM_EXEC", raising=False)
    monkeypatch.delenv("XSDB_EXEC", raising=False)
    monkeypatch.delenv("VITIS_ROOT", raising=False)
    monkeypatch.setattr(mod, "_DEFAULT_VITIS_BIN", str(tmp_path))
    fake = _write_executable(tmp_path, "xsdb")
    assert find_xsdb() == fake


def test_find_xsdb_path_fallback(monkeypatch, tmp_path):
    """Priority 4: shutil.which fallback when nothing else resolves."""
    import mcps.zynq_mcp.adapters.xsct.xsdb_bridge as mod
    monkeypatch.delenv("XSDM_EXEC", raising=False)
    monkeypatch.delenv("XSDB_EXEC", raising=False)
    monkeypatch.delenv("VITIS_ROOT", raising=False)
    monkeypatch.setattr(mod, "_DEFAULT_VITIS_BIN", str(tmp_path / "no_such"))
    monkeypatch.setattr(mod.shutil, "which",
                        lambda name: "/usr/bin/fake_xsdb")
    assert find_xsdb() == "/usr/bin/fake_xsdb"


def test_build_launch_cmd_bat_wrapping():
    """Windows .bat wrappers are launched under cmd.exe /d /c."""
    if os.name == "nt":
        bat = r"D:\Xilinx\Vitis\2023.1\bin\xsdb.bat"
        assert _build_launch_cmd(bat) == ["cmd.exe", "/d", "/c", bat]
        assert _build_launch_cmd(r"D:\tools\xsdb.exe") == [r"D:\tools\xsdb.exe"]
    else:
        assert _build_launch_cmd("/usr/bin/xsdb") == ["/usr/bin/xsdb"]


def test_xsdb_default_timeout_is_30():
    """XsdbBridge eval defaults to a 30s timeout (contract §3.1)."""
    assert XsdbBridge._default_timeout == 30.0


# ---------------------------------------------------------------------------
# host_live tests (real xsdb.bat)
# ---------------------------------------------------------------------------

@pytest.mark.host_live
@needs_xsdb
@pytest.mark.asyncio
async def test_xsdb_start_stop_real():
    """Start a real xsdb, verify the process is alive, then stop it."""
    bridge = XsdbBridge()
    await bridge.start("")
    try:
        assert bridge.ready
        assert bridge.pid is not None
    finally:
        await bridge.stop()
    assert bridge.pid is None
    assert not bridge.ready


@pytest.mark.host_live
@needs_xsdb
@pytest.mark.asyncio
async def test_xsdb_eval_simple_real():
    """Real xsdb: eval('puts hello') returns 'hello'."""
    bridge = XsdbBridge()
    await bridge.start("")
    try:
        res = await bridge.eval("puts hello")
        assert res["status"] == "success"
        assert "hello" in res["data"]
    finally:
        await bridge.stop()


@pytest.mark.host_live
@needs_xsdb
@pytest.mark.asyncio
async def test_xsdb_hw_connect_real():
    """Real xsdb: connect to hw_server sets hw_connected (skips if unreachable)."""
    bridge = XsdbBridge()
    try:
        await bridge.start("localhost:3121")
    except XsdbBridgeError as exc:
        pytest.skip(f"hw_server not reachable at localhost:3121: {exc}")
    finally:
        await bridge.stop()
    assert bridge.hw_connected
