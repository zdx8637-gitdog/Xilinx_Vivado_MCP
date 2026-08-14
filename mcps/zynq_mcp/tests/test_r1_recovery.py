"""test_r1_recovery.py — Recovery tests."""
import os, shutil, tempfile, uuid
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_OUTCOME_UNKNOWN, ChannelBusyError,
)
from mcps.zynq_mcp.control.recovery import recovery_mutator, diagnose_execution

REAL_REV = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g; g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)


class TestRecovery:
    def test_diagnose_returns_structure(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
        l = ledger_transaction(g, lp, _init)
        r = diagnose_execution(l)
        assert r["status"] == "success"
        assert "worker_process_health" in r["data"]

    def test_from_dead_worker(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_RECOVERY_REQUIRED; return l
        l = ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
        assert l.execution_lane == EXECUTION_LANE_IDLE

    def test_from_outcome_unknown(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_RECOVERY_REQUIRED; l.previous_operation={"operation_id":"op-old","status":OP_OUTCOME_UNKNOWN}; return l
        l = ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
        assert l.execution_lane == EXECUTION_LANE_IDLE
        assert l.previous_operation.get("resolved_by_recovery") is True

    def test_worker_alive_blocks(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_RECOVERY_REQUIRED; l.worker["pid"]=os.getpid(); return l
        l = ledger_transaction(g, lp, _init)
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g, lp, recovery_mutator("op-x"))

    def test_resource_held_blocks(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_RECOVERY_REQUIRED; l.worker["project_lease_held"]=True; return l
        l = ledger_transaction(g, lp, _init)
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g, lp, recovery_mutator("op-x"))

    def test_recover_then_new_command_admitted(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_RECOVERY_REQUIRED; return l
        l = ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
        assert l.execution_lane == EXECUTION_LANE_IDLE
        # Next admission
        from mcps.zynq_mcp.control.execution_gate import preflight_mutator
        from mcps.zynq_mcp.control.operation_service import request_signature
        def _ctx(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; return l
        l = ledger_transaction(g, lp, _ctx)
        sig = request_signature("s","PL_BUILD","pl_synthesize",{},REAL_REV)
        mut = preflight_mutator("pl_synthesize",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}",sig)
        l = ledger_transaction(g, lp, mut)
        assert l.active_operation is not None
        assert l.active_operation["status"] == OP_ACCEPTED

    def test_idle_recovery_is_noop(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
        l = ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
        assert l.execution_lane == EXECUTION_LANE_IDLE
