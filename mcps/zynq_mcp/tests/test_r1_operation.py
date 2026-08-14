"""test_r1_operation.py — Operation lifecycle and persistence."""
import shutil, tempfile, uuid
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED, OP_CANCELLED,
    OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_service import op_transition, request_signature
from mcps.zynq_mcp.control.operation_registry import OperationRegistry

REAL_REV = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g; g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

@pytest.fixture
def pled(rtg):
    rt, g = rtg; lp = rt / "ledger.json"
    def _i(l): l.instance_id=g.instance_id; l.workspace_id="ws-test"; l.execution_lane=EXECUTION_LANE_IDLE; l.primary_instance_id=g.instance_id; return l
    return ledger_transaction(g, lp, _i), g, lp

def _admit(l0, g, lp):
    def _ctx(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; return l
    l0 = ledger_transaction(g, lp, _ctx)
    sig = request_signature("s","PL_BUILD","pl_synthesize",{},REAL_REV)
    oid = f"op-{uuid.uuid4().hex}"
    mut = preflight_mutator("pl_synthesize",{},"s","ALINX_AX7020_v1.0","p",oid,sig)
    return ledger_transaction(g, lp, mut), g, lp, oid


class TestOpLifecycle:
    def test_accepted_to_running(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is not None
        assert l.active_operation["operation_id"]==oid
        assert l.active_operation["status"]==OP_RUNNING

    def test_succeeded_active_none_previous_set(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_SUCCEEDED)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.previous_operation is not None
        assert l.previous_operation["operation_id"]==oid
        assert l.previous_operation["status"]==OP_SUCCEEDED
        assert l.execution_lane==EXECUTION_LANE_IDLE

    def test_failed_active_none(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_FAILED)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.previous_operation["status"]==OP_FAILED
        assert l.execution_lane==EXECUTION_LANE_IDLE

    def test_cancelled_active_none(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_CANCELLED)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.previous_operation["status"]==OP_CANCELLED
        assert l.execution_lane==EXECUTION_LANE_IDLE

    def test_timed_out_recovery(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_TIMED_OUT)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.previous_operation["status"]==OP_TIMED_OUT
        assert l.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED

    def test_interrupted_recovery(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_INTERRUPTED)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED

    def test_outcome_unknown_recovery(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_OUTCOME_UNKNOWN)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        assert l.active_operation is None
        assert l.execution_lane==EXECUTION_LANE_RECOVERY_REQUIRED

    def test_restart_preserves_succeeded(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_SUCCEEDED)
        oreg=OperationRegistry(); oreg.restore_from_ledger(ledger_read_shared(g,lp,g.workspace_id)[0])
        assert oreg.get(oid) is not None and oreg.get(oid).status==OP_SUCCEEDED

    def test_no_op_in_both_active_and_previous(self, pled):
        l,g,lp,oid=_admit(*pled)
        op_transition(g,lp,oid,OP_RUNNING); op_transition(g,lp,oid,OP_SUCCEEDED)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        ao_id=l.active_operation.get("operation_id") if l.active_operation else None
        po_id=l.previous_operation.get("operation_id") if l.previous_operation else None
        assert not (ao_id==oid and po_id==oid)

    def test_running_heartbeat_refresh_preserves_started_at(self, pled):
        """B07: RUNNING→RUNNING is a legal heartbeat refresh (used by the
        long-run heartbeat task): heartbeat_at is updated, started_at is NOT
        moved, status stays RUNNING, and a later terminal transition still
        succeeds."""
        l, g, lp, oid = _admit(*pled)
        op_transition(g, lp, oid, OP_RUNNING)
        l1, _ = ledger_read_shared(g, lp, g.workspace_id)
        started1 = l1.active_operation["started_at"]
        tr = op_transition(g, lp, oid, OP_RUNNING,
                           heartbeat_at="2026-08-09T00:00:00.000000Z")
        assert tr["status"] == "success"
        l2, _ = ledger_read_shared(g, lp, g.workspace_id)
        ao = l2.active_operation
        assert ao["status"] == OP_RUNNING
        assert ao["heartbeat_at"] == "2026-08-09T00:00:00.000000Z"
        assert ao["started_at"] == started1  # started_at never moved by heartbeat
        op_transition(g, lp, oid, OP_SUCCEEDED)  # terminal transition still legal
        l3, _ = ledger_read_shared(g, lp, g.workspace_id)
        assert l3.previous_operation["status"] == OP_SUCCEEDED

    def test_dedup_persisted_in_ledger(self, pled):
        l,g,lp,oid=_admit(*pled)
        l,_=ledger_read_shared(g,lp,g.workspace_id)
        dr=l.dedup_registry or {}
        assert len(dr) >= 1
        oreg=OperationRegistry(); oreg.restore_from_ledger(l)
        sig=request_signature("s","PL_BUILD","pl_synthesize",{},REAL_REV)
        found=oreg.find_duplicate(sig)
        assert found is not None and found.operation_id==oid
