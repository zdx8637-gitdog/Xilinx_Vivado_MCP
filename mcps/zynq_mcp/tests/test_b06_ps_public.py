"""B06 PS Domain public MCP SDK contract tests.

Covers the B06 first-batch integration (22 ps_* tools: JTAG + UART +
Recovery, see docs/development/ps_domain/B06_integration_plan.md) through
the REAL MCP SDK — a `python -m mcps.zynq_mcp.server` subprocess reached via
stdio_client + ClientSession (same pattern as test_b05_platform_public.py).

Three groups:

  - TestPsToolDiscovery (no marker) — ps_* tools exist in list_tools, exact
    inputSchema, capabilities counts. These exercise the contract/discovery
    path only (no XSDB / hw_server needed).
  - TestPsErrorPaths (no marker)    — error envelope FORMAT is verified
    without XSDB. Tests 8/9 pass in both the pre- and post-integration
    states (the server always returns a well-formed error envelope). The
    strict INVALID_SCOPE check (test 10) requires the integration to be
    present so the domain function is reachable.
  - TestPsRealHardware (host_live)  — real XSDB bridge calls against a live
    hw_server / JTAG chain. PASS on a real success, SKIP with a precise
    reason when the hardware prerequisite is absent.

Pre-integration note: until the B06 integration agent registers the 22
ps_* tools in control/capabilities.py + dispatcher.py, list_tools exposes no
ps_* tools. Tests that REQUIRE the tools to exist therefore SKIP with an
explicit reason instead of failing; count assertions use >= N (not ==) per
the B06 task brief, so the exact 22 are verified automatically once the
integration lands.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import find_xsdb

pytestmark = pytest.mark.asyncio(loop_scope="function")

BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"

# B06 first batch — exactly 22 ps_* tools (B06_integration_plan.md §集成范围)
EXPECTED_PS_TOOLS = {
    # JTAG connection / target management
    "ps_connect_hw_server", "ps_disconnect_hw_server", "ps_list_targets",
    "ps_select_target", "ps_get_target_status", "ps_get_device_info",
    # target control
    "ps_reset_target", "ps_initialize_ps", "ps_run_target", "ps_halt_target",
    "ps_step_target", "ps_wait_for_state",
    # memory / registers
    "ps_reg_read", "ps_reg_write", "ps_mem_read", "ps_mem_write",
    # recovery
    "ps_recover_target", "ps_reconnect_target", "ps_clear_debug_session",
    "ps_diagnose_dap",
    # UART
    "ps_read_uart", "ps_list_serial_ports",
}
assert len(EXPECTED_PS_TOOLS) == 22, "B06 first batch must define exactly 22 tools"

# Domain outcomes that are legitimate "hardware prerequisite missing" results
# for the host_live tests (a real bridge executed but the JTAG/hw_server is
# not reachable). Anything else on the failure path fails the test.
_HW_SKIP_REASONS = {
    "HW_SERVER_UNREACHABLE", "CONNECT_FAILED", "NOT_CONNECTED",
    "BRIDGE_NOT_READY", "JTAG_EMPTY_CHAIN", "DEVICE_INFO_FAILED",
    "TARGET_UNRESPONSIVE", "NO_TARGET_SELECTED",
}


def _server_params(runtime_root):
    """StdioServerParameters for the zynq MCP server subprocess."""
    env = os.environ.copy()
    env["PYTHONPATH"] = _PROJECT_ROOT
    env["ZYNQ_RUNTIME_ROOT"] = str(runtime_root)
    return StdioServerParameters(
        command=sys.executable, args=["-m", "mcps.zynq_mcp.server"], env=env)


async def _sdk_call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return json.loads(r.content[0].text)


async def _list_tools(session):
    res = await session.list_tools()
    return list(res.tools)


def _tool_names(tools):
    return [t.name for t in tools]


def _ps_tool_names(tools):
    return [t.name for t in tools if t.name.startswith("ps_")]


def _skip_unless_integrated(tools):
    """Skip the strict contract check when the B06 PS integration is absent."""
    if "ps_list_targets" not in _tool_names(tools):
        pytest.skip(
            "B06 PS integration not yet present — no ps_* tools registered "
            "(integration agent pending). Strict contract is verified after "
            "the 22 ps_* tools land in control/capabilities.py + dispatcher.py.")


async def _tool_by_name(session, name):
    tools = await _list_tools(session)
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found in list_tools")


async def _create_session(session, project_path):
    d = await _sdk_call(session, "create_session",
                        {"board_id": BOARD, "project_path": str(project_path)})
    assert d["status"] == "success", f"create_session failed: {d}"
    return d["data"]["session_id"]


async def _ps_call(session, tool, session_id, extra=None):
    """Call a ps_* tool with session_id merged into the arguments.

    B06 dispatcher contract: ps_* tools take session_id as a transport
    argument (stripped before the domain function is invoked).
    """
    args = {"session_id": session_id}
    if extra:
        args.update(extra)
    return await _sdk_call(session, tool, args)


async def _await_op(session, op_id, timeout_s=60):
    """Wait for a domain command to reach a terminal state.

    Returns {"status", "reason", "payload", "data"}:
      - SUCCEEDED -> payload = local_fn result.data (dict)
      - FAILED/TIMED_OUT/OUTCOME_UNKNOWN/... -> reason = operation reason_code
    """
    wait = await _sdk_call(session, "wait_operation",
                           {"operation_id": op_id, "timeout_s": timeout_s})
    assert wait["status"] == "success", f"wait_operation failed: {wait}"
    data = wait["data"]
    status = data.get("status", "")
    reason = data.get("reason_code", "") or ""
    payload = {}
    if status == "SUCCEEDED":
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            payload = result["data"]
    return {"status": status, "reason": reason, "payload": payload, "data": data}


def _failed_envelope(data):
    """Parse the operation record's `error` field into the domain envelope.

    The failed terminal transition stores ``str(<envelope dict>)`` (a Python
    literal), so ast.literal_eval recovers the original envelope.
    """
    err = data.get("error")
    if isinstance(err, dict):
        return err
    if isinstance(err, str):
        try:
            parsed = ast.literal_eval(err)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, SyntaxError):
            return {}
    return {}


def _xsdb_available() -> bool:
    return find_xsdb() is not None


def _require_xsdb():
    if not _xsdb_available():
        pytest.skip("xsdb executable not found (find_xsdb() returned None)")


def _require_hw_server(port: int = 3121, host: str = "127.0.0.1"):
    """Skip when no hw_server is listening on the default JTAG port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
    except OSError:
        pytest.skip(
            f"no hw_server listening on tcp:{host}:{port} — cannot verify a "
            "real JTAG connection")
    finally:
        s.close()


# ═══════════════════════════════════════════════════════════════════════
#  Discovery + schema (no marker — contract path only, no XSDB)
# ═══════════════════════════════════════════════════════════════════════

class TestPsToolDiscovery:

    async def test_ps_tools_in_list_tools(self, tmp_runtime_root):
        """list_tools exposes at least 20 ps_* tools once integrated."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                assert len(_ps_tool_names(tools)) >= 20

    async def test_list_tools_returns_22_ps_tools(self, tmp_runtime_root):
        """The full B06 first batch (all 22 expected names, count >= 22)."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                ps_tools = set(_ps_tool_names(tools))
                missing = EXPECTED_PS_TOOLS - ps_tools
                assert not missing, f"PS tools missing from list_tools: {sorted(missing)}"
                assert len(ps_tools) >= 22

    async def test_ps_connect_hw_server_schema(self, tmp_runtime_root):
        """ps_connect_hw_server schema: url param present and optional."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                tool = await _tool_by_name(s, "ps_connect_hw_server")
                schema = tool.inputSchema
                assert schema["type"] == "object"
                assert "url" in schema.get("properties", {}), "url param missing"
                assert "url" not in schema.get("required", []), \
                    "url is optional (B06 plan: url?)"

    async def test_ps_select_target_schema(self, tmp_runtime_root):
        """ps_select_target schema: target_id is required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                tool = await _tool_by_name(s, "ps_select_target")
                schema = tool.inputSchema
                assert "target_id" in schema.get("properties", {})
                assert "target_id" in schema.get("required", []), \
                    "target_id must be required"

    async def test_ps_read_uart_schema(self, tmp_runtime_root):
        """ps_read_uart schema: port (required) + baudrate/duration_ms (optional)."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                tool = await _tool_by_name(s, "ps_read_uart")
                schema = tool.inputSchema
                props = schema.get("properties", {})
                for k in ("port", "baudrate", "duration_ms"):
                    assert k in props, f"ps_read_uart missing property {k}"
                req = schema.get("required", [])
                assert "port" in req, "port must be required"
                assert "baudrate" not in req, "baudrate is optional"
                assert "duration_ms" not in req, "duration_ms is optional"

    async def test_capabilities_ps_implemented(self, tmp_runtime_root):
        """get_capabilities reports the PS domain implemented once integrated."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                caps = (await _sdk_call(s, "get_capabilities", {}))["data"]
                ps = caps["domains"]["ps"]
                assert ps["implemented"] >= 22, f"ps.implemented={ps['implemented']}"

    async def test_list_tools_count(self, tmp_runtime_root):
        """Capabilities tool-count arithmetic: 9 control + 2 PL/Platform + 22 PS."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                caps = (await _sdk_call(s, "get_capabilities", {}))["data"]
                assert caps["control_apis"] >= 9, f"control_apis={caps['control_apis']}"
                assert caps["total_tools"] >= 33, f"total_tools={caps['total_tools']}"


# ═══════════════════════════════════════════════════════════════════════
#  Error paths (no marker — verified without XSDB)
# ═══════════════════════════════════════════════════════════════════════

class TestPsErrorPaths:

    async def test_ps_list_targets_no_session(self, tmp_runtime_root):
        """ps_list_targets with no session must return a well-formed error
        envelope. Passes both pre-integration (UNKNOWN_TOOL) and post-
        integration (LOCK_BUSY / NO_ACTIVE_SESSION): the contract under test
        is the error envelope FORMAT."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                data = await _sdk_call(s, "ps_list_targets", {})
                assert data["status"] == "error"
                err = data.get("error", {})
                assert isinstance(err.get("code"), str) and err["code"]
                assert isinstance(err.get("message"), str) and err["message"]
                assert err.get("details", {}).get("reason_code"), \
                    "error envelope must carry details.reason_code"

    async def test_ps_select_target_missing_id(self, tmp_runtime_root):
        """Missing required target_id must be rejected.

        Post-integration the MCP SDK validates the required property before
        dispatch and returns an "Input validation error" result; pre-integration
        the server returns a well-formed error envelope for the unregistered
        tool. Either way the call must not succeed."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("ps_select_target", {})
                text = res.content[0].text if res.content else ""
                if text.startswith("Input validation error"):
                    # MCP SDK rejected the missing required target_id before
                    # dispatch (post-integration, tool is listed).
                    assert "target_id" in text, \
                        f"expected target_id validation, got: {text}"
                else:
                    # Server error envelope (e.g. pre-integration UNKNOWN_TOOL).
                    data = json.loads(text)
                    assert data["status"] == "error", \
                        f"missing required target_id must error, got: {data}"
                    assert data.get("error", {}).get("details", {}).get("reason_code"), \
                        "error envelope must carry details.reason_code"

    async def test_ps_reset_target_invalid_scope(self, tmp_runtime_root, tmp_path):
        """scope=invalid must yield INVALID_SCOPE (top-level INVALID_ARGUMENT).

        Requires the integration to be present so the domain function is
        reachable (via a real session + CommandRunner admission). If the XSDB
        bridge/environment prerequisite cannot be met the test skips with a
        precise reason (same category as a host_live hardware skip)."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(s, "ps_reset_target", sid, {"scope": "invalid"})
                assert adm["status"] == "success", f"admission failed: {adm}"
                assert adm["data"]["status"] == "accepted"
                out = await _await_op(s, adm["data"]["operation_id"])
                if out["status"] == "FAILED" and out["reason"] == "INVALID_SCOPE":
                    # fail-closed: the reason must map to a stable top-level code
                    env = _failed_envelope(out["data"])
                    assert env.get("error", {}).get("code") == "INVALID_ARGUMENT", \
                        f"expected INVALID_ARGUMENT, got: {env}"
                elif out["status"] in ("FAILED", "OUTCOME_UNKNOWN", "TIMED_OUT"):
                    pytest.skip(
                        "cannot reach domain scope validation: "
                        f"status={out['status']} reason={out['reason']!r} "
                        "(XSDB bridge / environment prerequisite not met)")
                else:
                    pytest.fail(f"ps_reset_target(scope=invalid) unexpected outcome: {out}")


# ═══════════════════════════════════════════════════════════════════════
#  Real hardware (host_live — XSDB + hw_server / JTAG chain)
# ═══════════════════════════════════════════════════════════════════════

class TestPsRealHardware:

    @pytest.mark.host_live
    async def test_ps_connect_hw_server_real(self, tmp_runtime_root, tmp_path):
        """Real connect to a live hw_server via XSDB."""
        # Hardware gates run BEFORE stdio_client so the skip reason is clean.
        _require_xsdb()
        _require_hw_server()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(s, "ps_connect_hw_server", sid)
                assert adm["status"] == "success", f"admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"])
                if out["status"] == "SUCCEEDED":
                    assert out["payload"].get("status") == "connected", out
                    assert out["payload"].get("url"), out
                    assert isinstance(out["payload"].get("already_connected"), bool), out
                elif out["status"] == "FAILED" and out["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"hw_server/hardware not reachable: {out['reason']}")
                else:
                    pytest.fail(f"ps_connect_hw_server unexpected outcome: {out}")

    @pytest.mark.host_live
    async def test_ps_disconnect_hw_server_real(self, tmp_runtime_root, tmp_path):
        """Real disconnect after a real connect (idempotent)."""
        _require_xsdb()
        _require_hw_server()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)

                conn = await _ps_call(s, "ps_connect_hw_server", sid)
                out1 = await _await_op(s, conn["data"]["operation_id"])
                if out1["status"] == "FAILED" and out1["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"hw_server/hardware not reachable: {out1['reason']}")
                assert out1["status"] == "SUCCEEDED", f"connect failed: {out1}"
                assert out1["payload"].get("status") == "connected"

                disc = await _ps_call(s, "ps_disconnect_hw_server", sid)
                out2 = await _await_op(s, disc["data"]["operation_id"])
                if out2["status"] == "SUCCEEDED":
                    assert out2["payload"].get("status") == "disconnected", out2
                    assert isinstance(out2["payload"].get("already_disconnected"), bool), out2
                elif out2["status"] == "FAILED" and out2["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"disconnect could not reach hw_server: {out2['reason']}")
                else:
                    pytest.fail(f"ps_disconnect_hw_server unexpected outcome: {out2}")

    @pytest.mark.host_live
    async def test_ps_list_targets_real(self, tmp_runtime_root, tmp_path):
        """Real JTAG chain enumeration (needs a connected board)."""
        _require_xsdb()
        _require_hw_server()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(s, "ps_list_targets", sid)
                assert adm["status"] == "success", f"admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"])
                if out["status"] == "SUCCEEDED":
                    targets = out["payload"].get("targets")
                    assert isinstance(targets, list), out
                    assert out["payload"].get("count") == len(targets), out
                    assert len(targets) >= 1, "real JTAG chain must have >= 1 target"
                elif out["status"] == "FAILED" and out["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"no reachable JTAG chain: {out['reason']}")
                else:
                    pytest.fail(f"ps_list_targets unexpected outcome: {out}")

    @pytest.mark.host_live
    async def test_ps_get_device_info_real(self, tmp_runtime_root, tmp_path):
        """Real ARM DAP device-properties query (needs a connected board)."""
        _require_xsdb()
        _require_hw_server()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(s, "ps_get_device_info", sid)
                assert adm["status"] == "success", f"admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"])
                if out["status"] == "SUCCEEDED":
                    assert isinstance(out["payload"], dict) and len(out["payload"]) >= 1, out
                elif out["status"] == "FAILED" and out["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"no reachable DAP: {out['reason']}")
                else:
                    pytest.fail(f"ps_get_device_info unexpected outcome: {out}")

    @pytest.mark.host_live
    async def test_ps_read_uart_list_ports_real(self, tmp_runtime_root, tmp_path):
        """ps_list_serial_ports enumerates real serial ports (pyserial).

        No hw_server / board required. Contract: data = {"ports": [..], "count": N}
        mirroring ps_list_targets' targets/count shape."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(s, "ps_list_serial_ports", sid)
                assert adm["status"] == "success", f"admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"])
                if out["status"] == "SUCCEEDED":
                    ports = out["payload"].get("ports")
                    assert isinstance(ports, list), out
                    assert out["payload"].get("count") == len(ports), out
                    for p in ports:
                        assert isinstance(p, dict) and "port" in p, \
                            f"port entry must carry a 'port' key: {p}"
                elif out["status"] == "FAILED" and out["reason"] in _HW_SKIP_REASONS:
                    pytest.skip(f"serial port enumeration failed: {out['reason']}")
                else:
                    pytest.fail(f"ps_list_serial_ports unexpected outcome: {out}")
