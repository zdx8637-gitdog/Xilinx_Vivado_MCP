"""test_target_control.py — Agent C, target_control module (8 APIs).

Unit tests use FakeXsdbBridge (shared conftest). host_live tests require
real xsdb + hw_server + a powered board and skip otherwise.
"""
import pytest

from mcps.zynq_mcp.domains.ps import target_control as tc

pytestmark = pytest.mark.asyncio


def _elf_path(tmp_path, name="app.elf", magic=b"\x7fELF"):
    p = tmp_path / name
    p.write_bytes(magic + b"\x02\x01\x01\x00" * 4)
    return str(p)


# ══════════════════════════════════════════════════════════════════════
# -- reset_target --
# ══════════════════════════════════════════════════════════════════════

async def test_reset_processor_success(connected_bridge):
    resp = await tc.reset_target(connected_bridge, scope="processor")
    assert resp["status"] == "success"
    assert resp["data"]["scope"] == "processor"
    assert resp["data"]["reset_done"] is True
    assert connected_bridge._eval_history[-1] == "rst -processor"


async def test_reset_system_success(connected_bridge):
    resp = await tc.reset_target(connected_bridge, scope="system")
    assert resp["status"] == "success"
    assert resp["data"]["scope"] == "system"
    assert connected_bridge._eval_history[-1] == "rst -system"


async def test_reset_invalid_scope(connected_bridge):
    resp = await tc.reset_target(connected_bridge, scope="core")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_SCOPE"
    assert connected_bridge._eval_history == []


async def test_reset_no_target_selected(connected_bridge):
    connected_bridge.set_response(
        "targets", "  1  ARM Cortex-A9 #0  (DAP)")
    resp = await tc.reset_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NO_TARGET_SELECTED"


async def test_reset_eval_failed(connected_bridge):
    connected_bridge.set_error("rst -processor", "reset refused")
    resp = await tc.reset_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RESET_FAILED"


async def test_reset_not_connected(fake_bridge):
    resp = await tc.reset_target(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


# ══════════════════════════════════════════════════════════════════════
# -- initialize_ps --
# ══════════════════════════════════════════════════════════════════════

async def test_initialize_ps_success(connected_bridge):
    resp = await tc.initialize_ps(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["status"] == "initialized"
    assert connected_bridge._eval_history[-1] == "ps7_post_config"
    # Without tcl_path, the source step is skipped but ps7_init+ps7_post_config still run


async def test_initialize_ps_eval_failed(connected_bridge):
    connected_bridge.set_error("ps7_init", "init failed")
    resp = await tc.initialize_ps(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "PS7_INIT_FAILED"


async def test_initialize_ps_no_target_selected(connected_bridge):
    connected_bridge.set_response(
        "targets", "  1  ARM Cortex-A9 #0  (DAP)")
    resp = await tc.initialize_ps(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NO_TARGET_SELECTED"


# ══════════════════════════════════════════════════════════════════════
# -- download_elf --
# ══════════════════════════════════════════════════════════════════════

async def test_download_elf_success(tmp_path, connected_bridge):
    path = _elf_path(tmp_path)
    resp = await tc.download_elf(connected_bridge, path)
    assert resp["status"] == "success"
    assert resp["data"]["downloaded"] is True
    tcl_path = path.replace("\\", "/")
    assert connected_bridge._eval_history[-1] == f"dow {tcl_path}"


async def test_download_elf_not_found(tmp_path, connected_bridge):
    path = str(tmp_path / "missing.elf")
    resp = await tc.download_elf(connected_bridge, path)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "ELF_NOT_FOUND"


async def test_download_elf_path_escape(connected_bridge):
    resp = await tc.download_elf(connected_bridge, "../escape.elf")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "PATH_ESCAPE"


async def test_download_elf_invalid_magic(tmp_path, connected_bridge):
    path = _elf_path(tmp_path, magic=b"NOTELF")
    resp = await tc.download_elf(connected_bridge, path)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "ELF_INVALID"


async def test_download_elf_invalid_path(connected_bridge):
    resp = await tc.download_elf(connected_bridge, 123)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_ELF_PATH"


async def test_download_elf_not_connected(tmp_path, fake_bridge):
    path = _elf_path(tmp_path)
    resp = await tc.download_elf(fake_bridge, path)  # not connected
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


async def test_download_elf_eval_failed(tmp_path, connected_bridge):
    path = _elf_path(tmp_path)
    connected_bridge.set_error("dow", "download refused")
    resp = await tc.download_elf(connected_bridge, path)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "DOWNLOAD_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- run_target --
# ══════════════════════════════════════════════════════════════════════

async def test_run_success(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties",
        "1   ARM Cortex-A9 #0  (DAP)\n    State: Running")
    resp = await tc.run_target(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "running"
    assert "con" in connected_bridge._eval_history


async def test_run_eval_failed(connected_bridge):
    connected_bridge.set_error("con", "run refused")
    resp = await tc.run_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RUN_FAILED"


async def test_run_does_not_confirm_running(connected_bridge):
    # con succeeds but the target stays halted -> RUN_FAILED (fail-closed).
    resp = await tc.run_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "RUN_FAILED"


async def test_run_invalid_core(connected_bridge):
    resp = await tc.run_target(connected_bridge, core="x")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_CORE"


# ══════════════════════════════════════════════════════════════════════
# -- halt_target --
# ══════════════════════════════════════════════════════════════════════

async def test_halt_success(connected_bridge):
    resp = await tc.halt_target(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"
    assert resp["data"]["already_halted"] is False


async def test_halt_already_halted(connected_bridge):
    connected_bridge.set_response("stop", "Already stopped")
    resp = await tc.halt_target(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["already_halted"] is True


async def test_halt_eval_failed(connected_bridge):
    connected_bridge.set_error("stop", "halt refused")
    resp = await tc.halt_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "HALT_FAILED"


async def test_halt_confirm_failed(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties",
        "1   ARM Cortex-A9 #0  (DAP)\n    State: Running")
    resp = await tc.halt_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "HALT_FAILED"


async def test_halt_bridge_crash_is_fail_closed(connected_bridge):
    # A dead bridge (XsdbBridgeError) must surface as an error envelope,
    # never as an unhandled crash. The crash is caught during the target
    # selection probe, before the stop command is issued.
    connected_bridge.fail_eval = True
    resp = await tc.halt_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "XSDM_BRIDGE_UNAVAILABLE"


# ══════════════════════════════════════════════════════════════════════
# -- step_target --
# ══════════════════════════════════════════════════════════════════════

async def test_step_success(connected_bridge):
    resp = await tc.step_target(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["stepped"] is True
    assert connected_bridge._eval_history[-1] == "stp"


async def test_step_not_halted(connected_bridge):
    connected_bridge.set_response(
        "targets -target-properties",
        "1   ARM Cortex-A9 #0  (DAP)\n    State: Running")
    resp = await tc.step_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "TARGET_NOT_HALTED"
    assert "stp" not in connected_bridge._eval_history


async def test_step_eval_failed(connected_bridge):
    connected_bridge.set_error("stp", "step refused")
    resp = await tc.step_target(connected_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "STEP_FAILED"


# ══════════════════════════════════════════════════════════════════════
# -- wait_for_state --
# ══════════════════════════════════════════════════════════════════════

async def test_wait_for_state_reached(connected_bridge):
    resp = await tc.wait_for_state(connected_bridge, "halted", timeout_s=1.0)
    assert resp["status"] == "success"
    assert resp["data"]["state"] == "halted"
    assert resp["data"]["achieved"] is True


async def test_wait_for_state_timeout(connected_bridge):
    resp = await tc.wait_for_state(connected_bridge, "running", timeout_s=0.2)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "TIMEOUT"
    assert resp["error"]["details"]["last_state"] == "halted"


async def test_wait_for_state_invalid_state(connected_bridge):
    resp = await tc.wait_for_state(connected_bridge, "bogus")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_STATE"


async def test_wait_for_state_invalid_timeout(connected_bridge):
    resp = await tc.wait_for_state(connected_bridge, "halted", timeout_s=0)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "INVALID_TIMEOUT"


async def test_wait_for_state_not_connected(fake_bridge):
    resp = await tc.wait_for_state(fake_bridge, "halted")
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"


# ══════════════════════════════════════════════════════════════════════
# -- ensure_arm_accessible --
# ══════════════════════════════════════════════════════════════════════

# Healthy chain: APU + both Cortex-A9 cores enumerate (type "ARM").
_GOOD_CHAIN = (
    "  1  APU  (APU)\n"
    "  2  ARM Cortex-A9 #0  (ARM)\n"
    "  3  ARM Cortex-A9 #1  (ARM)\n"
    "  4  xc7z020  (FPGA)"
)
# Power-cycled DAP: only DAP + FPGA enumerate, no ARM cores. The DAP is the
# current (selected) target so reset_target's target-selection probe passes.
_BAD_CHAIN = (
    "* 1  DAP  (DAP)\n"
    "  2  xc7z020  (FPGA)"
)


async def test_ensure_arm_accessible_cores_present(connected_bridge):
    """Healthy chain: ARM cores already enumerate -> no recovery."""
    connected_bridge.set_response("targets", _GOOD_CHAIN)
    resp = await tc.ensure_arm_accessible(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["recovery_needed"] is False
    assert resp["data"]["count"] == 4
    assert [t["name"] for t in resp["data"]["targets"]] == [
        "APU", "ARM Cortex-A9 #0", "ARM Cortex-A9 #1", "xc7z020"]
    # No recovery was needed: no DAP selection, no system reset.
    assert "rst -system" not in connected_bridge._eval_history


async def test_ensure_arm_accessible_recovery(connected_bridge):
    """Power-cycled DAP: cores missing -> select DAP + rst -system
    -> cores enumerate (recovery_needed=True)."""
    def targets_fn(tcl):
        tcl = tcl.strip()
        if tcl == "targets":
            # Once the system reset has run, the chain is healthy again.
            if "rst -system" in connected_bridge._eval_history:
                return _GOOD_CHAIN
            return _BAD_CHAIN
        return tcl
    connected_bridge.set_response_fn("targets", targets_fn)
    resp = await tc.ensure_arm_accessible(connected_bridge)
    assert resp["status"] == "success"
    assert resp["data"]["recovery_needed"] is True
    assert resp["data"]["count"] == 4
    names = [t["name"] for t in resp["data"]["targets"]]
    assert "ARM Cortex-A9 #0" in names
    assert "ARM Cortex-A9 #1" in names
    # The verified recovery sequence ran: select DAP -> system reset ->
    # re-list targets.
    assert "targets 1" in connected_bridge._eval_history
    assert "rst -system" in connected_bridge._eval_history
    assert connected_bridge._eval_history[-1] == "targets"


async def test_ensure_arm_accessible_not_connected(fake_bridge):
    resp = await tc.ensure_arm_accessible(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NOT_CONNECTED"
    assert fake_bridge._eval_history == []


async def test_ensure_arm_accessible_no_dap(fake_bridge):
    """Fail-closed: cores missing and no DAP to recover -> NO_ARM_DAP."""
    fake_bridge.set_started(True)
    fake_bridge.set_connected(True)
    fake_bridge.set_response("targets", "  1  xc7z020  (FPGA)")
    resp = await tc.ensure_arm_accessible(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "NO_ARM_DAP"
    assert "rst -system" not in fake_bridge._eval_history


async def test_ensure_arm_accessible_still_missing_after_reset(fake_bridge):
    """Fail-closed: rst -system runs but cores still absent -> error."""
    fake_bridge.set_started(True)
    fake_bridge.set_connected(True)
    fake_bridge.set_response("targets", _BAD_CHAIN)
    resp = await tc.ensure_arm_accessible(fake_bridge)
    assert resp["status"] == "error"
    assert resp["error"]["details"]["reason_code"] == "ARM_ACCESS_FAILED"
    assert resp["error"]["details"]["step"] == "verify"
    assert "rst -system" in fake_bridge._eval_history


# ══════════════════════════════════════════════════════════════════════
# -- host_live (real xsdb + hw_server + board) --
# ══════════════════════════════════════════════════════════════════════

class TestControlHostLive:
    """Real chain: halt then run."""

    pytestmark = pytest.mark.host_live

    async def test_halt_run_real(self, live_bridge):
        c = await tc_connect(live_bridge)
        assert c["status"] == "success", c
        h = await tc.halt_target(live_bridge)
        assert h["status"] == "success", h
        r = await tc.run_target(live_bridge)
        assert r["status"] == "success", r


async def tc_connect(bridge):
    from mcps.zynq_mcp.domains.ps.jtag_target import connect_hw_server
    return await connect_hw_server(bridge)
