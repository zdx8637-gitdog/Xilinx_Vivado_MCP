"""test_vivado_bridge.py — B08 unit + host_live tests for VivadoTclBridge.

Evidence levels:
  - Unit tests (no marker): real fake-shell subprocess through the production
    bridge entry points (start/eval/stop) — TEST_HELPER level process.
  - host_live tests: real vivado.exe — IMPLEMENTED_AND_TESTED (require a
    Xilinx install; skipped via skipif when vivado is absent).
"""
import asyncio
import os
import sys

import pytest

from mcps.zynq_mcp.adapters.vivado.vivado_bridge import (
    DEFAULT_VIVADO_TIMEOUT,
    START_ATTEMPTS,
    VivadoTclBridge,
    VivadoBridgeError,
    find_vivado,
)
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import _vendor_subprocess_env

REAL_VIVADO = find_vivado()
needs_vivado = pytest.mark.skipif(
    REAL_VIVADO is None, reason="no vivado executable found on this host")


# ---------------------------------------------------------------------------
# Unit tests (no marker, fake vivado shell subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_missing_executable_raises():
    """start() with an unresolvable executable raises VivadoBridgeError."""
    bridge = VivadoTclBridge(
        vivado_path=os.path.join("nonexistent", "vivado.exe"))
    with pytest.raises(VivadoBridgeError):
        await bridge.start()
    assert not bridge.ready
    assert bridge.pid is None


@pytest.mark.asyncio
async def test_start_sets_pid_and_ready(fake_vivado):
    """start() launches a live child; pid is set and ready is True."""
    await fake_vivado.start()
    assert fake_vivado.ready
    assert fake_vivado.pid is not None
    await fake_vivado.stop()


@pytest.mark.asyncio
async def test_start_runs_init_commands(fake_vivado):
    """start() sends the initialization commands (msg suppression, threads)."""
    await fake_vivado.start()
    try:
        res = await fake_vivado.eval("__DUMP__")
        assert res["status"] == "success"
        assert "RECORDED set_msg_config -suppress -id {Common 17-54}" in res["data"]
        assert "RECORDED set_param general.maxThreads 4" in res["data"]
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_stop_clears_pid_and_ready(fake_vivado):
    """stop() terminates the child; pid -> None, ready -> False."""
    await fake_vivado.start()
    assert fake_vivado.pid is not None
    await fake_vivado.stop()
    assert fake_vivado.pid is None
    assert not fake_vivado.ready


@pytest.mark.asyncio
async def test_eval_simple(fake_vivado):
    """eval('puts hello') returns success with data 'hello' (prompt stripped)."""
    await fake_vivado.start()
    res = await fake_vivado.eval("puts hello")
    assert res["status"] == "success"
    assert res["data"] == "hello"
    await fake_vivado.stop()


@pytest.mark.asyncio
async def test_eval_multiline_data(fake_vivado):
    """Multi-line command output is preserved between the sentinels."""
    await fake_vivado.start()
    res = await fake_vivado.eval("puts line1\nputs line2")
    assert res["status"] == "success"
    assert res["data"] == "line1\nline2"
    await fake_vivado.stop()


@pytest.mark.asyncio
async def test_eval_sequence_increments(fake_vivado):
    """Three evals: seq increments (2 init commands ran at start) and outputs
    never cross-contaminate."""
    await fake_vivado.start()
    try:
        r1 = await fake_vivado.eval("puts one")
        r2 = await fake_vivado.eval("puts two")
        r3 = await fake_vivado.eval("puts three")
        assert [r["data"] for r in (r1, r2, r3)] == ["one", "two", "three"]
        # 2 init commands (set_msg_config, set_param) + 3 evals
        assert fake_vivado.seq == 5
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_eval_timeout_raises(fake_vivado):
    """A command that never returns triggers VivadoBridgeError + process kill."""
    await fake_vivado.start()
    try:
        with pytest.raises(VivadoBridgeError, match="timeout"):
            await fake_vivado.eval("__HANG__", timeout_s=0.5)
        assert not fake_vivado.ready
        assert fake_vivado.pid is None
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_stop_idempotent(fake_vivado):
    """Calling stop() twice must not raise."""
    await fake_vivado.start()
    await fake_vivado.stop()
    await fake_vivado.stop()
    assert fake_vivado.pid is None
    assert not fake_vivado.ready


@pytest.mark.asyncio
async def test_eval_after_stop_returns_error(fake_vivado):
    """eval() after stop() returns an error dict, never hangs."""
    await fake_vivado.start()
    await fake_vivado.stop()
    res = await fake_vivado.eval("puts hi")
    assert res["status"] == "error"
    assert res["error"]["code"] == "XSDM_EVAL_ERROR"
    assert res["error"]["details"]["reason_code"] == "XSDM_PROCESS_DEAD"


@pytest.mark.asyncio
async def test_external_kill_returns_error(fake_vivado):
    """A subprocess killed externally: eval returns error dict, no hang."""
    await fake_vivado.start()
    proc = fake_vivado._proc
    proc.kill()
    await proc.wait()
    res = await fake_vivado.eval("puts hi")
    assert res["status"] == "error"
    assert res["error"]["details"]["reason_code"] == "XSDM_PROCESS_DEAD"
    await fake_vivado.stop()


@pytest.mark.asyncio
async def test_concurrent_eval_serialized(fake_vivado):
    """A second eval queues on the single channel while the first is held."""
    await fake_vivado.start()
    try:
        async with fake_vivado._lock:
            task = asyncio.create_task(fake_vivado.eval("puts queued"))
            await asyncio.sleep(0)
            assert not task.done()
        res = await asyncio.wait_for(task, timeout=5.0)
        assert res["status"] == "success"
        assert res["data"] == "queued"
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_eval_tcl_error_returns_error(fake_vivado):
    """An 'ERROR:' line in output yields a Tcl-error result dict."""
    await fake_vivado.start()
    try:
        res = await fake_vivado.eval("__ERROR__")
        assert res["status"] == "error"
        assert res["error"]["code"] == "XSDM_EVAL_ERROR"
        assert res["error"]["details"]["reason_code"] == "XSDM_TCL_ERROR"
        assert "simulated" in res["error"]["message"]
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_eval_stderr_not_fatal(fake_vivado):
    """Vivado writes routine noise to stderr; it must NOT fail the eval."""
    await fake_vivado.start()
    try:
        res = await fake_vivado.eval("__STDERR__")
        assert res["status"] == "success"
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_banner_reprint_filtered(fake_vivado):
    """Vivado re-prints its banner before a command; banner lines are dropped."""
    await fake_vivado.start()
    try:
        res = await fake_vivado.eval("__BANNER__")
        assert res["status"] == "success"
        assert "Vivado v" not in res["data"]
        assert "SW Build" not in res["data"]
        assert "OK __BANNER__" in res["data"]
    finally:
        await fake_vivado.stop()


@pytest.mark.asyncio
async def test_restart_after_stop(fake_vivado):
    """stop() then start() yields a fresh working process."""
    await fake_vivado.start()
    first_pid = fake_vivado.pid
    await fake_vivado.stop()
    await fake_vivado.start()
    try:
        assert fake_vivado.ready
        assert fake_vivado.pid != first_pid
        res = await fake_vivado.eval("puts again")
        assert res["status"] == "success"
        assert res["data"] == "again"
    finally:
        await fake_vivado.stop()


# ---------------------------------------------------------------------------
# Executable resolution unit tests (pure env/path logic)
# ---------------------------------------------------------------------------

def _write_executable(tmp_path, name):
    variant = "vivado.exe" if os.name == "nt" else "vivado"
    p = tmp_path / variant
    p.write_text("", encoding="utf-8")
    return str(p)


def test_find_vivado_env_var(monkeypatch, tmp_path):
    """Priority 1: an existing VIVADO_EXEC full path wins."""
    fake = _write_executable(tmp_path, "vivado")
    monkeypatch.setenv("VIVADO_EXEC", fake)
    assert find_vivado() == fake


def test_find_vivado_env_var_missing_falls_through(monkeypatch, tmp_path):
    """A missing VIVADO_EXEC falls through to VIVADO_ROOT/bin."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / ("vivado.exe" if os.name == "nt" else "vivado")
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("VIVADO_EXEC", str(tmp_path / "missing.exe"))
    monkeypatch.setenv("VIVADO_ROOT", str(tmp_path))
    assert find_vivado() == str(exe)


def test_find_vivado_default_install(monkeypatch, tmp_path):
    """Priority 3: default install dir resolves when env vars are unset."""
    import mcps.zynq_mcp.adapters.vivado.vivado_bridge as mod
    monkeypatch.delenv("VIVADO_EXEC", raising=False)
    monkeypatch.delenv("VIVADO_ROOT", raising=False)
    monkeypatch.setattr(mod, "_DEFAULT_VIVADO_BIN", str(tmp_path))
    fake = _write_executable(tmp_path, "vivado")
    assert find_vivado() == fake


def test_find_vivado_path_fallback(monkeypatch, tmp_path):
    """Priority 4: shutil.which fallback when nothing else resolves."""
    import mcps.zynq_mcp.adapters.vivado.vivado_bridge as mod
    monkeypatch.delenv("VIVADO_EXEC", raising=False)
    monkeypatch.delenv("VIVADO_ROOT", raising=False)
    monkeypatch.setattr(mod, "_DEFAULT_VIVADO_BIN", str(tmp_path / "no_such"))
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/fake_vivado")
    assert find_vivado() == "/usr/bin/fake_vivado"


def test_default_timeout_is_3600():
    """VivadoTclBridge eval defaults to a long timeout (synthesis runs)."""
    assert VivadoTclBridge._default_timeout == 3600.0
    assert DEFAULT_VIVADO_TIMEOUT == 3600.0


def test_stderr_not_fatal_by_default():
    """VivadoTclBridge must not treat stderr as fatal (Tcl errors go to stdout)."""
    assert VivadoTclBridge._stderr_is_fatal is False


def test_start_attempts_are_bounded():
    """Only one clean relaunch is allowed before startup fails closed."""
    assert START_ATTEMPTS == 2


def test_startup_stdout_is_preserved_in_dead_process_error():
    bridge = VivadoTclBridge()
    result = bridge._error_dict(
        "XSDM_EVAL_ERROR", "vivado is dead", "PROCESS_DEAD",
        stdout_text="vendor bootstrap failed",
        extra_details={"exit_code": 1})

    assert "vendor bootstrap failed" in result["error"]["message"]
    assert result["error"]["details"]["exit_code"] == 1


def test_vendor_environment_restores_xilinx_windows_loader_prerequisites(
        monkeypatch):
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE", raising=False)
    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.delenv("ComSpec", raising=False)

    env = _vendor_subprocess_env()

    assert env["PROCESSOR_ARCHITECTURE"]
    assert env["SystemRoot"]
    assert env["WINDIR"] == env["SystemRoot"]
    assert env["ComSpec"].lower().endswith("system32\\cmd.exe")


@pytest.mark.asyncio
async def test_start_relaunches_once_after_pre_command_process_death(tmp_path):
    """A dead first vendor launcher is cleaned before one safe relaunch.

    The second process implements the minimum Tcl sentinel protocol needed by
    the production bridge.  No user command can run on the failed process.
    """
    state = tmp_path / "attempt.txt"
    shell = tmp_path / "flaky_vivado.py"
    shell.write_text(
        "import pathlib, sys\n"
        "state = pathlib.Path(sys.argv[1])\n"
        "attempt = int(state.read_text() or '0') + 1 if state.exists() else 1\n"
        "state.write_text(str(attempt))\n"
        "print('Vivado test banner', flush=True)\n"
        "if attempt == 1:\n"
        "    print('transient launcher exit', file=sys.stderr, flush=True)\n"
        "    raise SystemExit(23)\n"
        "for raw in sys.stdin:\n"
        "    line = raw.strip()\n"
        "    if line.startswith('puts '):\n"
        "        print(line[5:], flush=True)\n",
        encoding="utf-8",
    )
    bridge = VivadoTclBridge(
        vivado_path=[sys.executable, str(shell), str(state)])
    await bridge.start()
    try:
        assert state.read_text(encoding="utf-8") == "2"
        result = await bridge.eval("puts hello")
        assert result["status"] == "success"
        assert result["data"] == "hello"
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# host_live tests (real vivado.exe)
# ---------------------------------------------------------------------------

@pytest.mark.host_live
@needs_vivado
@pytest.mark.asyncio
async def test_vivado_start_eval_real():
    """Real vivado: start and eval('puts hello')."""
    bridge = VivadoTclBridge()
    await bridge.start()
    try:
        res = await bridge.eval("puts hello")
        assert res["status"] == "success"
        assert "hello" in res["data"]
    finally:
        await bridge.stop()
    assert bridge.pid is None
    assert not bridge.ready
