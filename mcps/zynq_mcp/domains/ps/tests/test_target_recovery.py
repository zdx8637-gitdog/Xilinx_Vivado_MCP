"""test_target_recovery.py — Agent C, target_recovery module (4 APIs).

Unit tests use FakeXsdbBridge (shared conftest).
"""
import pytest

from mcps.zynq_mcp.domains.ps import target_recovery as tr

pytestmark = pytest.mark.asyncio

_RUNNING_PROP = "1   ARM Cortex-A9 #0  (DAP)\n    State: Running"


# ══════════════════════════════════════════════════════════════════════
# -- recover_target --
# ══════════════════════════════════════════════════════════════════════

async def test_recover_auto_success(connected_bridge):
    resp = await tr.recover_target(connected_bridge, "auto")
    assert resp["status"] == "success"
    assert resp["data"]["recovered"] is True
    assert resp["data"]["state"] == "halted"
    assert resp["data"]["failed_at_step"] is None
    assert resp["data"]["completed_steps"] == [
        "halt", "processor_reset", "core_reset", "system_reset", "ps7_init"]
    assert len(resp["data"]["completed_steps"]) == 5
    history = connected_bridge._eval_history
    assert "stop" in history
    assert "rst -processor" in history
    assert "rst -cores" in history
    assert "rst -system" in history
    assert "ps7_init" in history


async def test_recover_stops_at_step_3(connected_bridge):
    connected_bridge.set_error("rst -cores", "core reset refused")
    resp = await tr.recover_target(connected_bridge, "auto")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECOVERY_PARTIAL"
    assert resp["error"]["details"]["failed_at_step"] == 3
    assert resp["error"]["details"]["completed_steps"] == [
        "halt", "processor_reset"]
    # cascade must not continue past the failed step
    assert "rst -system" not in connected_bridge._eval_history
    assert "ps7_init" not in connected_bridge._eval_history


async def test_recover_stops_at_step_5(connected_bridge):
    connected_bridge.set_error("ps7_init", "init refused")
    resp = await tr.recover_target(connected_bridge, "auto")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECOVERY_PARTIAL"
    assert resp["error"]["details"]["failed_at_step"] == 5
    assert len(resp["error"]["details"]["completed_steps"]) == 4


async def test_recover_first_step_fails_cascade(connected_bridge):
    connected_bridge.set_error("stop", "halt refused")
    resp = await tr.recover_target(connected_bridge, "auto")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECOVERY_CASCADE_FAILED"
    assert resp["error"]["details"]["failed_at_step"] == 1
    assert resp["error"]["details"]["completed_steps"] == []
    assert "rst -processor" not in connected_bridge._eval_history


async def test_recover_verify_fails_partial(connected_bridge):
    # First state query (halt confirm) -> halted; final verify -> running.
    calls = {"n": 0}

    def state_fn(tcl):
        calls["n"] += 1
        return ("1   ARM Cortex-A9 #0  (DAP)\n    State: Halted"
                if calls["n"] == 1 else _RUNNING_PROP)

    connected_bridge.set_response_fn("targets -target-properties", state_fn)
    resp = await tr.recover_target(connected_bridge, "auto")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECOVERY_PARTIAL"
    assert resp["error"]["details"]["failed_at_step"] is None
    assert len(resp["error"]["details"]["completed_steps"]) == 5
    assert resp["error"]["details"]["state"] == "running"


async def test_recover_invalid_strategy(connected_bridge):
    resp = await tr.recover_target(connected_bridge, "custom")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_STRATEGY"


# ══════════════════════════════════════════════════════════════════════
# -- reconnect_target --
# ══════════════════════════════════════════════════════════════════════

async def test_reconnect_success(connected_bridge):
    resp = await tr.reconnect_target(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["reconnected"] is True
    assert resp["data"]["target_id"] == 1
    history = connected_bridge._eval_history
    assert "disconnect" in history
    assert "connect -url tcp:localhost:3121" in history
    assert "targets 1" in history


async def test_reconnect_no_arm_dap(connected_bridge):
    connected_bridge.set_response(
        "targets", "  1  xc7z020  (FPGA)\n  2  xc7z020_1  (FPGA)")
    resp = await tr.reconnect_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECONNECT_FAILED"
    assert resp["error"]["details"]["sub_reason"] == "NO_ARM_DAP"
    assert resp["error"]["details"]["step"] == "list_targets"
    # No target selection (new positional syntax) may have been issued.
    assert not any(h.startswith("targets ")
                   for h in connected_bridge._eval_history)


async def test_reconnect_connect_fails(connected_bridge):
    connected_bridge.set_error("connect -url", "refused")
    resp = await tr.reconnect_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RECONNECT_FAILED"
    assert resp["error"]["details"]["step"] == "connect"


# ══════════════════════════════════════════════════════════════════════
# -- clear_debug_session --
# ══════════════════════════════════════════════════════════════════════

async def test_clear_debug_success(connected_bridge):
    resp = await tr.clear_debug_session(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["cleared"] is True
    assert all(s["ok"] for s in resp["data"]["steps"])
    assert "bpd -all" in connected_bridge._eval_history


async def test_clear_debug_halt_failure_still_success(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties", _RUNNING_PROP)
    resp = await tr.clear_debug_session(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["cleared"] is False
    by_step = {s["step"]: s for s in resp["data"]["steps"]}
    assert by_step["halt"]["ok"] is False
    assert by_step["halt"]["error"] == "HALT_FAILED"
    # cleanup continues past the failed halt
    assert "bpd -all" in connected_bridge._eval_history


async def test_clear_debug_bridge_not_ready(fake_bridge):
    resp = await tr.clear_debug_session(fake_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["cleared"] is False
    assert resp["data"]["reason"] == "BRIDGE_NOT_READY"
    assert fake_bridge._eval_history == []


# ══════════════════════════════════════════════════════════════════════
# -- diagnose_dap --
# ══════════════════════════════════════════════════════════════════════

async def test_diagnose_dap_healthy(connected_bridge):
    resp = await tr.diagnose_dap(connected_bridge)
    assert resp["status"] == "success"
    d = resp["data"]["diagnosis"]
    assert d["connected"] is True
    assert d["target_selected"] is True
    assert d["target_state"] == "halted"
    assert isinstance(d["likely_issues"], list)
    assert d["suggested_action"] == "Target is healthy; no recovery needed"


async def test_diagnose_dap_not_connected(fake_bridge):
    resp = await tr.diagnose_dap(fake_bridge)
    assert resp["status"] == "success"
    d = resp["data"]["diagnosis"]
    assert d["connected"] is False
    assert d["target_selected"] is False
    assert d["target_state"] is None
    assert any("Cable disconnected" in i for i in d["likely_issues"])
    assert "recover_target('auto')" in d["suggested_action"]


async def test_diagnose_dap_bridge_crash_is_graceful(connected_bridge):
    connected_bridge.fail_eval = True
    resp = await tr.diagnose_dap(connected_bridge)
    assert resp["status"] == "success"
    d = resp["data"]["diagnosis"]
    assert d["connected"] is True
    assert isinstance(d["likely_issues"], list)
    assert d["suggested_action"]
