"""test_debug_session.py — ARM debug session unit + host_live tests.

Evidence levels:
- Unit tests (no marker): exercise debug_session.py logic with
  FakeXsdbBridge and mocked Agent C functions (select_target / halt_target
  / download_elf). Agent A's XsdbBridge was complete at development time;
  Agent C (jtag_target / target_control) was not, so the tests replace the
  Agent C dependencies at the debug_session module namespace — documented
  in debug_session.py's module docstring.
- host_live tests (@pytest.mark.host_live): require real xsdb + hw_server
  + a powered board on the JTAG chain AND the real Agent A/C
  implementations. Skipped (with a reason) when any prerequisite is
  missing.
"""

import shutil

import pytest

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError
from mcps.zynq_mcp.domains.ps import debug_session as ds
from mcps.zynq_mcp.domains.ps.tests.conftest import (
    FAKE_BACKTRACE_OUTPUT,
    FAKE_REGISTER_OUTPUT,
)

# ══════════════════════════════════════════════════════════════════════
# -- fixtures / test helpers --
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Reset the module-level session registry before/after every test."""
    ds._debug_sessions.clear()
    yield
    ds._debug_sessions.clear()


async def _fake_select(bridge, target_id):
    return {"status": "success", "data": {"target_id": target_id,
                                          "selected": True}}


async def _fake_halt(bridge, core=None):
    return {"status": "success", "data": {"halted": True,
                                          "already_halted": False}}


async def _fake_download(bridge, elf_path):
    return {"status": "success", "data": {"elf_path": elf_path,
                                          "downloaded": True}}


@pytest.fixture
def mock_agent_c(monkeypatch):
    """Install success-stub Agent C functions into the debug_session module.

    debug_session imports Agent C's select_target/halt_target/download_elf
    into its own namespace; patching that namespace keeps the unit tests
    independent of Agent C's (incomplete) implementation.
    """
    monkeypatch.setattr(ds, "select_target", _fake_select)
    monkeypatch.setattr(ds, "halt_target", _fake_halt)
    monkeypatch.setattr(ds, "download_elf", _fake_download)
    return ds


def _make_halted_session(elf_path: str = "app.elf") -> str:
    """Create a session via the production _create_session and mark it halted.

    Only used to set up a valid precondition for register/stack/breakpoint
    tests. Session *creation* by debug_start() is covered separately.
    """
    sid = ds._create_session(elf_path)
    ds._debug_sessions[sid]["halted"] = True
    return sid


# ══════════════════════════════════════════════════════════════════════
# -- debug_start --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_debug_start_returns_session_id(mock_agent_c, fake_bridge):
    resp = await ds.debug_start(fake_bridge, "app.elf")
    assert resp["status"] == "success"
    sid = resp["data"]["debug_session_id"]
    assert sid.startswith("debug-")
    session = ds._debug_sessions[sid]
    assert session["halted"] is True
    assert session["elf_path"] == "app.elf"
    assert session["created_at"]


@pytest.mark.asyncio
async def test_debug_start_selects_target_when_given(mock_agent_c, fake_bridge,
                                                     monkeypatch):
    calls = []

    async def _rec_select(bridge, target_id):
        calls.append(target_id)
        return {"status": "success", "data": {"target_id": target_id}}

    monkeypatch.setattr(ds, "select_target", _rec_select)
    resp = await ds.debug_start(fake_bridge, "app.elf", target_id=3)
    assert resp["status"] == "success"
    assert calls == [3]


@pytest.mark.asyncio
async def test_debug_start_propagates_download_error(mock_agent_c, fake_bridge,
                                                     monkeypatch):
    async def _fail_download(bridge, elf_path):
        return {"status": "error",
                "error": {"code": "TOOL_ERROR", "message": "elf missing",
                          "details": {"reason_code": "ELF_NOT_FOUND"}}}

    monkeypatch.setattr(ds, "download_elf", _fail_download)
    resp = await ds.debug_start(fake_bridge, "app.elf")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "TOOL_ERROR"
    assert resp["error"]["details"]["reason_code"] == "ELF_NOT_FOUND"
    assert not ds._debug_sessions  # no session created on failure


@pytest.mark.asyncio
async def test_debug_start_rejects_empty_elf_path(mock_agent_c, fake_bridge):
    resp = await ds.debug_start(fake_bridge, "   ")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    assert resp["error"]["details"]["reason_code"] == "INVALID_ELF_PATH"
    assert not ds._debug_sessions


@pytest.mark.asyncio
async def test_debug_start_normalizes_toolresponse_object_error(
        mock_agent_c, fake_bridge, monkeypatch):
    """Agent C's ps_error returns a ToolResponse *object*, not a dict.

    debug_start must normalize the object form and return a dict.
    """
    from mcps.common.tool_response import error as tool_error

    async def _obj_fail_download(bridge, elf_path):
        return tool_error("elf missing", code="TOOL_ERROR",
                          details={"reason_code": "ELF_NOT_FOUND"})

    monkeypatch.setattr(ds, "download_elf", _obj_fail_download)
    resp = await ds.debug_start(fake_bridge, "app.elf")
    assert isinstance(resp, dict)
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "TOOL_ERROR"
    assert resp["error"]["details"]["reason_code"] == "ELF_NOT_FOUND"
    assert not ds._debug_sessions


# ══════════════════════════════════════════════════════════════════════
# -- breakpoint_add --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_breakpoint_add_returns_id(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("bpadd", "7")
    resp = await ds.breakpoint_add(fake_bridge, sid, "0x00100000")
    assert resp["status"] == "success"
    assert resp["data"]["breakpoint_id"] == 7
    assert resp["data"]["location"] == "0x00100000"
    assert resp["data"]["debug_session_id"] == sid
    assert 7 in ds._debug_sessions[sid]["breakpoints"]
    assert fake_bridge._eval_history[-1] == "bpadd -addr 0x00100000"


@pytest.mark.asyncio
async def test_breakpoint_add_symbol_location(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("bpadd", "2")
    resp = await ds.breakpoint_add(fake_bridge, sid, "main")
    assert resp["status"] == "success"
    assert resp["data"]["breakpoint_id"] == 2
    assert fake_bridge._eval_history[-1] == "bpadd -sym main"


@pytest.mark.asyncio
async def test_breakpoint_add_invalid_location(fake_bridge):
    sid = _make_halted_session()
    resp = await ds.breakpoint_add(fake_bridge, sid, "0xZZZZ")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    assert resp["error"]["details"]["reason_code"] == "INVALID_LOCATION"
    assert not ds._debug_sessions[sid]["breakpoints"]
    assert fake_bridge._eval_history == []  # validation short-circuits


@pytest.mark.asyncio
async def test_breakpoint_add_invalid_session(fake_bridge):
    resp = await ds.breakpoint_add(fake_bridge, "debug-nope", "main")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "CONTEXT_INVALID"
    assert resp["error"]["details"]["reason_code"] == "INVALID_DEBUG_SESSION"
    assert fake_bridge._eval_history == []


@pytest.mark.asyncio
async def test_breakpoint_add_bridge_failure(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_error("bpadd", "breakpoint limit reached",
                          code="XSDM_EVAL_ERROR")
    resp = await ds.breakpoint_add(fake_bridge, sid, "0x00100000")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "BREAKPOINT_ADD_FAILED"
    assert resp["error"]["details"]["bridge_code"] == "XSDM_EVAL_ERROR"
    assert not ds._debug_sessions[sid]["breakpoints"]


@pytest.mark.asyncio
async def test_breakpoint_add_unparseable_output_fails_closed(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("bpadd", "no id in this output")
    resp = await ds.breakpoint_add(fake_bridge, sid, "main")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "BREAKPOINT_ADD_FAILED"
    assert not ds._debug_sessions[sid]["breakpoints"]


# ══════════════════════════════════════════════════════════════════════
# -- breakpoint_remove --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_breakpoint_remove_ok(fake_bridge):
    sid = _make_halted_session()
    ds._debug_sessions[sid]["breakpoints"].add(3)
    resp = await ds.breakpoint_remove(fake_bridge, sid, 3)
    assert resp["status"] == "success"
    assert resp["data"]["removed"] is True
    assert resp["data"]["breakpoint_id"] == 3
    assert 3 not in ds._debug_sessions[sid]["breakpoints"]
    assert fake_bridge._eval_history[-1] == "bpremove 3"


@pytest.mark.asyncio
async def test_breakpoint_remove_not_found(fake_bridge):
    sid = _make_halted_session()
    resp = await ds.breakpoint_remove(fake_bridge, sid, 99)
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "BREAKPOINT_NOT_FOUND"
    assert fake_bridge._eval_history == []  # bridge not consulted


# ══════════════════════════════════════════════════════════════════════
# -- read_register --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_read_register_returns_value(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("rrd", FAKE_REGISTER_OUTPUT)
    resp = await ds.read_register(fake_bridge, sid, "pc")
    assert resp["status"] == "success"
    assert resp["data"]["register"] == "pc"
    assert resp["data"]["value"] == "0x00100040"
    assert resp["data"]["debug_session_id"] == sid


@pytest.mark.asyncio
async def test_read_register_invalid_session(fake_bridge):
    resp = await ds.read_register(fake_bridge, "debug-nope", "pc")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "CONTEXT_INVALID"
    assert resp["error"]["details"]["reason_code"] == "INVALID_DEBUG_SESSION"
    assert fake_bridge._eval_history == []


@pytest.mark.asyncio
async def test_read_register_target_not_halted(fake_bridge):
    sid = ds._create_session("app.elf")  # _create_session defaults halted=False
    resp = await ds.read_register(fake_bridge, sid, "pc")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "TARGET_NOT_HALTED"
    assert fake_bridge._eval_history == []  # precondition short-circuits


@pytest.mark.asyncio
async def test_read_register_invalid_register(fake_bridge):
    sid = _make_halted_session()
    resp = await ds.read_register(fake_bridge, sid, "r16")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "INVALID_ARGUMENT"
    assert resp["error"]["details"]["reason_code"] == "INVALID_REGISTER"
    assert fake_bridge._eval_history == []


@pytest.mark.asyncio
async def test_read_register_bridge_failure_converts_to_error(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.fail_eval = True
    resp = await ds.read_register(fake_bridge, sid, "pc")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "REG_READ_FAILED"
    assert resp["error"]["details"]["bridge_code"] == "XSDM_EVAL_ERROR"


# ══════════════════════════════════════════════════════════════════════
# -- write_register --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_write_register_ok(fake_bridge):
    sid = _make_halted_session()
    resp = await ds.write_register(fake_bridge, sid, "r0", 0x10)
    assert resp["status"] == "success"
    assert resp["data"]["register"] == "r0"
    assert resp["data"]["value"] == "0x00000010"
    assert fake_bridge._eval_history[-1] == "rwr r0 0x00000010"


@pytest.mark.asyncio
async def test_write_register_requires_halted(fake_bridge):
    sid = ds._create_session("app.elf")
    resp = await ds.write_register(fake_bridge, sid, "r0", 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "TARGET_NOT_HALTED"
    assert fake_bridge._eval_history == []


# ══════════════════════════════════════════════════════════════════════
# -- stack_trace --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stack_trace_parses_frames(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("bt", FAKE_BACKTRACE_OUTPUT)
    resp = await ds.stack_trace(fake_bridge, sid)
    assert resp["status"] == "success"
    frames = resp["data"]["frames"]
    assert frames == [
        {"level": 0, "pc": None, "function": "main", "file": "main.c:42"},
        {"level": 1, "pc": None, "function": "_start", "file": "crt0.S:15"},
    ]
    assert resp["data"]["debug_session_id"] == sid


@pytest.mark.asyncio
async def test_stack_trace_pc_inclusive_format(fake_bridge):
    sid = _make_halted_session()
    out = "#0  0x00100040 in main () at main.c:42\n"
    fake_bridge.set_response("bt", out)
    resp = await ds.stack_trace(fake_bridge, sid)
    assert resp["status"] == "success"
    frame = resp["data"]["frames"][0]
    assert frame["pc"] == "0x00100040"
    assert frame["function"] == "main"
    assert frame["file"] == "main.c:42"


@pytest.mark.asyncio
async def test_stack_trace_no_frames_fails_closed(fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_response("bt", "some unparseable output")
    resp = await ds.stack_trace(fake_bridge, sid)
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "BACKTRACE_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- debug_close --
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_debug_close_clears_session(mock_agent_c, fake_bridge):
    sid = _make_halted_session()
    ds._debug_sessions[sid]["breakpoints"].add(1)
    resp = await ds.debug_close(fake_bridge, sid)
    assert resp["status"] == "success"
    assert resp["data"]["debug_session_id"] == sid
    assert resp["data"]["breakpoints_removed"] == 1
    assert sid not in ds._debug_sessions
    assert "bpremove all" in fake_bridge._eval_history


@pytest.mark.asyncio
async def test_debug_close_twice_returns_invalid_session(mock_agent_c,
                                                         fake_bridge):
    sid = _make_halted_session()
    await ds.debug_close(fake_bridge, sid)
    resp2 = await ds.debug_close(fake_bridge, sid)
    assert resp2["status"] == "error"
    assert resp2["error"]["code"] == "CONTEXT_INVALID"
    assert resp2["error"]["details"]["reason_code"] == "INVALID_DEBUG_SESSION"


@pytest.mark.asyncio
async def test_debug_close_bpremove_failure_reports_incomplete(mock_agent_c,
                                                               fake_bridge):
    sid = _make_halted_session()
    fake_bridge.set_error("bpremove", "bridge broke", code="XSDM_EVAL_ERROR")
    resp = await ds.debug_close(fake_bridge, sid)
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "JTAG_ERROR"  # unified via ps_error()
    assert resp["error"]["details"]["reason_code"] == "DEBUG_CLOSE_INCOMPLETE"
    assert resp["error"]["details"]["failed_steps"] == ["bpremove_all"]
    # The Python-side session is still released so resources are freed.
    assert sid not in ds._debug_sessions


# ══════════════════════════════════════════════════════════════════════
# -- host_live (real xsdb + hw_server + board) --
# ══════════════════════════════════════════════════════════════════════

def _host_live_skip_reason() -> str | None:
    """Return a skip reason when the real debug chain is unavailable.

    Gates on three things: xsdb on PATH, Agent A's real XsdbBridge, and
    Agent C's real jtag_target/target_control. Skeleton modules carry
    _IS_SKELETON = True; the real Agent A/C implementations remove it.
    """
    if shutil.which("xsdb") is None:
        return "xsdb not on PATH"
    try:
        from mcps.zynq_mcp.adapters.xsct import xsdb_bridge as xsdb_mod
    except ImportError:
        return "adapters.xsct.xsdb_bridge not importable (Agent A incomplete)"
    if getattr(xsdb_mod, "_IS_SKELETON", False):
        return "XsdbBridge is still the Agent A interface skeleton"
    from mcps.zynq_mcp.domains.ps import jtag_target, target_control
    if getattr(jtag_target, "_IS_SKELETON", False):
        return "jtag_target is still the Agent C interface skeleton"
    if getattr(target_control, "_IS_SKELETON", False):
        return "target_control is still the Agent C interface skeleton"
    return None


@pytest.mark.host_live
@pytest.mark.asyncio
async def test_debug_start_halt_real():
    """Real board: halt -> download ELF -> debug_start returns a session id.

    The test tears the session down via debug_close so the board is left
    halted (never left running / breakpoints never left set).
    """
    reason = _host_live_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
    bridge = XsdbBridge()
    try:
        await bridge.start("localhost:3121")
    except XsdbBridgeError as e:
        await bridge.stop()
        pytest.skip(f"cannot start/connect xsdb bridge: {e}")
    sid = None
    try:
        resp = await ds.debug_start(bridge, "app.elf", target_id=1)
        assert resp["status"] == "success", resp
        sid = resp["data"]["debug_session_id"]
        assert sid.startswith("debug-")
    finally:
        if sid is not None:
            await ds.debug_close(bridge, sid)
        await bridge.stop()


@pytest.mark.host_live
@pytest.mark.asyncio
async def test_breakpoint_add_real():
    """Real board: start a session, set a breakpoint, verify, clean up."""
    reason = _host_live_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
    bridge = XsdbBridge()
    try:
        await bridge.start("localhost:3121")
    except XsdbBridgeError as e:
        await bridge.stop()
        pytest.skip(f"cannot start/connect xsdb bridge: {e}")
    sid = None
    try:
        resp = await ds.debug_start(bridge, "app.elf", target_id=1)
        assert resp["status"] == "success", resp
        sid = resp["data"]["debug_session_id"]
        bresp = await ds.breakpoint_add(bridge, sid, "main")
        assert bresp["status"] == "success", bresp
        assert isinstance(bresp["data"]["breakpoint_id"], int)
    finally:
        if sid is not None:
            await ds.debug_close(bridge, sid)
        await bridge.stop()
