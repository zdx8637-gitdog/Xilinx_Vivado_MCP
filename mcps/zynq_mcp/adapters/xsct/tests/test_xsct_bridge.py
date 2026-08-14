"""test_xsct_bridge.py — B06 Agent A unit + host_live tests for XsctBridge
and the shared Tcl templates.

Evidence levels:
  - Unit tests (no marker): fake-shell subprocess through production entry
    points — TEST_HELPER level.
  - host_live test: real xsct.bat — IMPLEMENTED_AND_TESTED (skipped if absent).
"""
import os

import pytest

from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsct_bridge import (
    DEFAULT_XSCT_TIMEOUT,
    XsctBridge,
    XsctBridgeError,
    find_xsct,
)
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError


# ---------------------------------------------------------------------------
# XsctBridge unit tests (fake Tcl shell subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_with_workspace_sends_setws(fake_xsct):
    """start(workspace=...) runs setws; the command reaches the shell."""
    await fake_xsct.start(workspace=os.path.join("tmp", "ws"))
    try:
        assert fake_xsct.ready
        res = await fake_xsct.eval("__DUMP__")
        assert res["status"] == "success"
        assert f"setws {os.path.join('tmp', 'ws')}" in res["data"]
    finally:
        await fake_xsct.stop()


@pytest.mark.asyncio
async def test_xsct_eval_simple(fake_xsct):
    """XsctBridge eval works identically to XsdbBridge."""
    await fake_xsct.start()
    res = await fake_xsct.eval("puts hi")
    assert res["status"] == "success"
    assert res["data"] == "hi"
    await fake_xsct.stop()


@pytest.mark.asyncio
async def test_xsct_eval_after_stop_returns_error(fake_xsct):
    """eval() after stop() returns an error dict, never hangs."""
    await fake_xsct.start()
    await fake_xsct.stop()
    res = await fake_xsct.eval("puts hi")
    assert res["status"] == "error"
    assert res["error"]["details"]["reason_code"] == "XSDM_PROCESS_DEAD"


@pytest.mark.asyncio
async def test_xsct_start_missing_executable_raises():
    """start() with an unresolvable executable raises XsctBridgeError."""
    bridge = XsctBridge(xsct_path=os.path.join("nonexistent", "xsct.exe"))
    with pytest.raises(XsctBridgeError):
        await bridge.start()


def test_xsct_error_type_distinct():
    """XsctBridgeError and XsdbBridgeError are distinct types."""
    assert XsctBridgeError is not XsdbBridgeError
    assert issubclass(XsctBridgeError, Exception)
    assert issubclass(XsdbBridgeError, Exception)


def test_xsct_default_timeout_is_60():
    """XsctBridge eval defaults to a longer 60s timeout (builds are slow)."""
    assert XsctBridge._default_timeout == 60.0
    assert DEFAULT_XSCT_TIMEOUT == 60.0


def test_find_xsct_env_var(monkeypatch, tmp_path):
    """XSCT_EXEC full path resolves for xsct."""
    variant = "xsct.bat" if os.name == "nt" else "xsct"
    p = tmp_path / variant
    p.write_text("", encoding="utf-8")
    monkeypatch.setenv("XSCT_EXEC", str(p))
    assert find_xsct() == str(p)


# ---------------------------------------------------------------------------
# Tcl template unit tests (pure string constructors)
# ---------------------------------------------------------------------------

class TestTemplates:
    def test_connect(self):
        assert templates.connect("localhost:3121") == "connect -url tcp:localhost:3121"

    def test_connect_default(self):
        assert templates.connect() == "connect -url tcp:localhost:3121"

    def test_targets(self):
        assert templates.targets() == "targets"

    def test_target_select(self):
        assert templates.target_select(1) == "targets 1"

    def test_get_target_properties(self):
        assert templates.get_target_properties(2) == \
            "targets 2\ntargets -target-properties"

    def test_device_info(self):
        assert templates.device_info() == "targets -target-properties"

    def test_rst(self):
        assert templates.rst() == "rst -processor"
        assert templates.rst("system") == "rst -system"

    def test_ps7_init(self):
        assert templates.ps7_init() == "ps7_init"

    def test_dow(self):
        assert templates.dow("app.elf") == "dow app.elf"

    def test_con_stop_stp(self):
        assert templates.con() == "con"
        assert templates.stop() == "stop"
        assert templates.stp() == "stp"

    def test_mrd_mwr(self):
        assert templates.mrd("0x40000000", 4) == "mrd 0x40000000 4"
        assert templates.mrd("0x40000000") == "mrd 0x40000000 1"
        assert templates.mwr("0x40000000", "0x1") == "mwr 0x40000000 0x1"

    def test_rrd_rwr(self):
        assert templates.rrd("pc") == "rrd pc"
        assert templates.rwr("pc", "0x100") == "rwr pc 0x100"

    def test_bp_operations(self):
        assert templates.bpadd("main") == "bpadd main"
        assert templates.bpremove(3) == "bpremove 3"
        assert templates.bplist() == "bplist"

    def test_backtrace(self):
        assert templates.backtrace() == "backtrace"

    def test_disconnect(self):
        assert templates.disconnect() == "disconnect"

    def test_after(self):
        assert templates.after(500) == "after 500"

    def test_setws(self):
        assert templates.setws("/tmp/ws") == "setws /tmp/ws"

    def test_import_hw(self):
        assert templates.import_hw("design.xsa") == "importhw design.xsa"

    def test_platform_create(self):
        assert templates.platform_create("hw_platform", "design.xsa") == \
            "platform create -name hw_platform -hw design.xsa -proc ps7_cortexa9_0 -os standalone"

    def test_bsp_create(self):
        assert templates.bsp_create("hw_platform") == \
            "bsp create -platform hw_platform -name bsp"

    def test_app_create(self):
        assert templates.app_create("hello", "hw_platform") == \
            "app create -name hello -platform hw_platform -template empty_application"

    def test_app_build(self):
        assert templates.app_build("hello") == "app build -name hello"


# ---------------------------------------------------------------------------
# host_live test (real xsct.bat)
# ---------------------------------------------------------------------------

@pytest.mark.host_live
@pytest.mark.asyncio
async def test_xsct_start_eval_real():
    """Real xsct: start and eval('puts hello')."""
    from mcps.zynq_mcp.adapters.xsct.xsct_bridge import XsctBridge
    if find_xsct() is None:
        pytest.skip("xsct not found on this host")
    bridge = XsctBridge()
    await bridge.start()
    try:
        res = await bridge.eval("puts hello")
        assert res["status"] == "success"
        assert "hello" in res["data"]
    finally:
        await bridge.stop()
