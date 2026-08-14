"""test_jtag_target.py — Agent C, jtag_target module (6 APIs).

Unit tests use FakeXsdbBridge (shared conftest) to verify Tcl generation,
output parsing, and error mapping. host_live tests require real xsdb +
hw_server + a powered board and skip otherwise.
"""
import pytest

from mcps.zynq_mcp.domains.ps import jtag_target as jt

pytestmark = pytest.mark.asyncio


# ══════════════════════════════════════════════════════════════════════
# -- connect_hw_server --
# ══════════════════════════════════════════════════════════════════════

async def test_connect_hw_server_establishes_connection(fake_bridge):
    await fake_bridge.start("")  # launched but not connected
    resp = await jt.connect_hw_server(fake_bridge, "localhost:3121")
    assert resp["status"] == "success"
    assert resp["data"]["status"] == "connected"
    assert resp["data"]["already_connected"] is False
    assert fake_bridge.hw_connected is True
    assert fake_bridge._eval_history == ["connect -url tcp:localhost:3121"]


async def test_connect_hw_server_idempotent(connected_bridge):
    resp = await jt.connect_hw_server(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["already_connected"] is True


async def test_connect_hw_server_bridge_not_ready(fake_bridge):
    resp = await jt.connect_hw_server(fake_bridge)  # never started
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "BRIDGE_NOT_READY"
    assert fake_bridge._eval_history == []


async def test_connect_hw_server_invalid_url(fake_bridge):
    resp = await jt.connect_hw_server(fake_bridge, url="   ")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_URL"


async def test_connect_hw_server_unreachable_envelope(fake_bridge):
    await fake_bridge.start("")
    fake_bridge.set_error("connect -url", "connection refused")
    resp = await jt.connect_hw_server(fake_bridge, "localhost:3121")
    assert resp["status"] == "error"
    assert resp["error"]["code"] == "ENV_ERROR"
    assert resp["error"]["details"]["reason_code"] == "HW_SERVER_UNREACHABLE"


async def test_connect_hw_server_unreachable_marker(fake_bridge):
    await fake_bridge.start("")
    fake_bridge.set_response("connect -url",
                             "__ERROR__:HW_SERVER_UNREACHABLE:refused")
    resp = await jt.connect_hw_server(fake_bridge, "localhost:3121")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "HW_SERVER_UNREACHABLE"


# ══════════════════════════════════════════════════════════════════════
# -- disconnect_hw_server --
# ══════════════════════════════════════════════════════════════════════

async def test_disconnect_hw_server_idempotent(fake_bridge):
    await fake_bridge.start("")
    resp = await jt.disconnect_hw_server(fake_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["already_disconnected"] is True
    assert fake_bridge._eval_history == []


async def test_disconnect_hw_server_success(connected_bridge):
    resp = await jt.disconnect_hw_server(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["already_disconnected"] is False
    assert connected_bridge.hw_connected is False
    assert connected_bridge._eval_history == ["disconnect"]


async def test_disconnect_hw_server_eval_failed(fake_bridge):
    await fake_bridge.start("localhost:3121")
    fake_bridge.set_error("disconnect", "busy")
    resp = await jt.disconnect_hw_server(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "DISCONNECT_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- list_targets --
# ══════════════════════════════════════════════════════════════════════

async def test_list_targets_parses(connected_bridge):
    resp = await jt.list_targets(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["count"] == 2
    t0 = resp["data"]["targets"][0]
    assert t0["id"] == 1
    assert t0["name"] == "ARM Cortex-A9 #0"
    assert t0["type"] == "DAP"
    assert t0["selected"] is True
    assert resp["data"]["targets"][1]["type"] == "FPGA"


async def test_list_targets_not_connected(fake_bridge):
    resp = await jt.list_targets(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"
    assert fake_bridge._eval_history == []


async def test_list_targets_empty_chain(fake_bridge):
    fake_bridge.set_connected(True)
    fake_bridge.set_response("targets", "")
    resp = await jt.list_targets(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "JTAG_EMPTY_CHAIN"


async def test_list_targets_eval_error(fake_bridge):
    fake_bridge.set_connected(True)
    fake_bridge.set_error("targets", "list failed")
    resp = await jt.list_targets(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "JTAG_LIST_FAILED"


async def test_list_targets_bridge_crash_is_fail_closed(fake_bridge):
    fake_bridge.set_connected(True)
    fake_bridge.fail_eval = True
    resp = await jt.list_targets(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "JTAG_LIST_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- select_target --
# ══════════════════════════════════════════════════════════════════════

async def test_select_target_success(connected_bridge):
    resp = await jt.select_target(connected_bridge, 1)
    assert resp["status"] == "success"
    assert resp["data"]["selected"]["id"] == 1
    assert connected_bridge._eval_history[-1] == "targets 1"


async def test_select_target_not_found(connected_bridge):
    resp = await jt.select_target(connected_bridge, 99)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "TARGET_NOT_FOUND"
    assert resp["error"]["details"]["available"] == [1, 2]


async def test_select_target_invalid_id_type(fake_bridge):
    resp = await jt.select_target(fake_bridge, "abc")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_TARGET_ID"


async def test_select_target_invalid_id_zero(fake_bridge):
    resp = await jt.select_target(fake_bridge, 0)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_TARGET_ID"


async def test_select_target_not_connected(fake_bridge):
    resp = await jt.select_target(fake_bridge, 1)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


# ══════════════════════════════════════════════════════════════════════
# -- get_target_status --
# ══════════════════════════════════════════════════════════════════════

async def test_get_target_status_halted(connected_bridge):
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"
    assert resp["data"]["pc"] == "0x00100000"
    assert resp["data"]["target_id"] == 1


async def test_get_target_status_running(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties",
        "1   ARM Cortex-A9 #0  (DAP)\n    State: Running")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "running"
    assert "pc" not in resp["data"]


async def test_get_target_status_tcl_list_running(connected_bridge):
    # Real Vitis 2023.1 XSDB emits target-properties as a Tcl list; the
    # state is the current target's (is_current 1) state_reason.
    connected_bridge.set_response(
        "targets -target-properties",
        "{target_ctx t0 target_id 1 name {ARM Cortex-A9 MPCore #0}"
        " state_reason Running is_current 1 suspended 0}"
        " {target_ctx t1 target_id 2 name xc7z020 state_reason {}"
        " is_current 0}")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "running"


async def test_get_target_status_tcl_list_halted(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties",
        "{target_ctx t0 target_id 1 name {ARM Cortex-A9 MPCore #0}"
        " state_reason Suspended is_current 1 suspended 1}"
        " {target_ctx t1 target_id 2 name xc7z020 state_reason {}"
        " is_current 0}")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"


async def test_get_target_status_tcl_list_halted_breakpoint_reason(
        connected_bridge):
    # Real Vitis 2023.1 XSDB reports a halted-at-breakpoint target as
    # `state_reason {Hardware Breakpoint}` — the *reason* it stopped, not
    # its state. The `suspended 1` flag is the authoritative halt signal;
    # the braced reason must not be misread as an unknown state (P1 bug:
    # ps_halt_target returned HALT_FAILED while the CPU was already halted).
    connected_bridge.set_response(
        "targets -target-properties",
        "{target_ctx t0 target_id 1 name {ARM Cortex-A9 MPCore #0}"
        " state_reason {Hardware Breakpoint} is_current 1 suspended 1}"
        " {target_ctx t1 target_id 2 name xc7z020 state_reason {}"
        " is_current 0}")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"


async def test_get_target_status_tcl_list_explicit_state_field(
        connected_bridge):
    # When target-properties carries an explicit `state` field it takes
    # precedence over the suspended flag / state_reason reason.
    connected_bridge.set_response(
        "targets -target-properties",
        "{target_ctx t0 target_id 1 name {ARM Cortex-A9 MPCore #0}"
        " state Halted is_current 1 suspended 0}"
        " {target_ctx t1 target_id 2 name xc7z020 state {} is_current 0}")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"


async def test_get_target_status_no_selection(connected_bridge):
    connected_bridge.set_response(
        "targets",
        "  1  ARM Cortex-A9 #0  (DAP)\n  2  xc7z020  (FPGA)")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NO_TARGET_SELECTED"


async def test_get_target_status_not_connected(fake_bridge):
    resp = await jt.get_target_status(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


async def test_get_target_status_unresponsive(connected_bridge):
    connected_bridge.set_error("targets -target-properties", "state query failed")
    resp = await jt.get_target_status(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "TARGET_UNRESPONSIVE"


# ══════════════════════════════════════════════════════════════════════
# -- get_device_info --
# ══════════════════════════════════════════════════════════════════════

async def test_get_device_info_parses(connected_bridge):
    # XSDB 2023.1 `targets -target-properties` emits space-separated k/v.
    connected_bridge.set_response(
        "targets -target-properties", "idcode 4ba00477 irmask 0x000fffff")
    resp = await jt.get_device_info(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["idcode"] == "4ba00477"
    assert resp["data"]["irmask"] == "0x000fffff"


async def test_get_device_info_not_connected(fake_bridge):
    resp = await jt.get_device_info(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


async def test_get_device_info_eval_failed(connected_bridge):
    connected_bridge.set_error("targets -target-properties", "read refused")
    resp = await jt.get_device_info(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "DEVICE_INFO_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- host_live (real xsdb + hw_server + board) --
# ══════════════════════════════════════════════════════════════════════

class TestJtagHostLive:
    """Real chain: connect + disconnect + list targets."""

    pytestmark = pytest.mark.host_live

    async def test_connect_disconnect_real(self, live_bridge):
        c = await jt.connect_hw_server(live_bridge)
        assert c["status"] == "success", c
        d = await jt.disconnect_hw_server(live_bridge)
        assert d["status"] == "success", d
        assert live_bridge.hw_connected is False

    async def test_list_targets_real(self, live_bridge):
        c = await jt.connect_hw_server(live_bridge)
        assert c["status"] == "success", c
        resp = await jt.list_targets(live_bridge)
        # A valid domain outcome for a real chain is either a populated
        # listing or a clean empty-chain error; anything else is a failure.
        assert resp["status"] in ("success", "error"), resp
        if resp["status"] == "error":
            assert resp["error"]["details"]["reason_code"] == "JTAG_EMPTY_CHAIN"
        else:
            assert resp["data"]["count"] >= 1
