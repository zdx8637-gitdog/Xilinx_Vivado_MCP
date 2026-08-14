"""test_r1_gate.py — P1-P10 preflight tests."""
import os, shutil, tempfile, uuid
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_RUNNING, OP_OUTCOME_UNKNOWN, WORKER_STATE_ABSENT,
    ChannelBusyError,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_service import (
    request_signature, InFlightDuplicateError, TerminalDuplicateError,
)

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

def _ctx(l):
    l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}
    return l


class TestPreflightP1_P6:
    def test_p1_active_op_blocks(self, pled):
        l,g,lp=pled
        def _set(l): l.active_operation={"operation_id":"op-1","status":OP_RUNNING}; l.execution_lane=EXECUTION_LANE_BUSY; return _ctx(l)
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_synthesize",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","sig1")
        with pytest.raises(ChannelBusyError, match="CHANNEL_BUSY"):
            ledger_transaction(g,lp,mut)

    def test_p6_previous_unresolved_blocks(self, pled):
        l,g,lp=pled
        def _set(l): l.previous_operation={"operation_id":"op-old","status":OP_OUTCOME_UNKNOWN}; return _ctx(l)
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_synthesize",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","sig2")
        with pytest.raises(ChannelBusyError, match="PREVIOUS"):
            ledger_transaction(g,lp,mut)

    def test_p3_identity_fail_closed(self, pled):
        l,g,lp=pled
        def _set(l): l.worker["pid"]=99999; l.worker["state"]="READY"; return _ctx(l)
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_synthesize",{},"s","b1","p1",f"op-{uuid.uuid4().hex}","sig3")
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g,lp,mut)

    def test_p5_missing_heartbeat_blocks(self, pled):
        l,g,lp=pled
        def _set(l): l.worker["pid"]=os.getpid(); l.worker["state"]="READY"; l.worker["last_heartbeat_at"]=None; return _ctx(l)
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_synthesize",{},"s","b1","p1",f"op-{uuid.uuid4().hex}","sig4")
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g,lp,mut)


class TestPreflightP7:
    def test_p7_stage_skip_rejected(self, pled):
        l,g,lp=pled
        mut=preflight_mutator("pl_generate_bitstream",{},"s","b1","p1",f"op-{uuid.uuid4().hex}","s")
        with pytest.raises(ChannelBusyError, match="STAGE_PREREQUISITE"):
            ledger_transaction(g,lp,mut)

    def test_p7_place_needs_synthesis_evidence(self, pled):
        l,g,lp=pled
        def _set(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_IMPLEMENT","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; return l
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_place_and_route",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","s")
        with pytest.raises(ChannelBusyError, match="STAGE_PREREQUISITE"):
            ledger_transaction(g,lp,mut)

    def test_p7_place_passes_with_synthesis_succeeded(self, pled):
        l,g,lp=pled
        def _set(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_IMPLEMENT","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; l.previous_operation={"tool_name":"pl_synthesize","status":"SUCCEEDED"}; return l
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_place_and_route",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","s")
        l=ledger_transaction(g,lp,mut)
        assert l.active_operation is not None

    def test_p7_bitstream_needs_timing_met(self, pled):
        l,g,lp=pled
        def _set(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_TIMING","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; l.previous_operation={"tool_name":"pl_analyze_timing","status":"SUCCEEDED","completion_evidence":{"timing_met":False}}; return l
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_generate_bitstream",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","s")
        with pytest.raises(ChannelBusyError, match="STAGE_PREREQUISITE"):
            ledger_transaction(g,lp,mut)

    def test_p7_bitstream_passes_with_timing_met_true(self, pled):
        l,g,lp=pled
        def _set(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_TIMING","board_package_revision":REAL_REV,"expected_board_revision":REAL_REV}; l.previous_operation={"tool_name":"pl_analyze_timing","status":"SUCCEEDED","completion_evidence":{"timing_met":True}}; return l
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_generate_bitstream",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","s")
        l=ledger_transaction(g,lp,mut)
        assert l.active_operation is not None


class TestPreflightP8_P10:
    def test_p8_revision_mismatch_blocks(self, pled):
        l,g,lp=pled
        def _set(l): l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD","board_package_revision":"","expected_board_revision":""}; return l
        l=ledger_transaction(g,lp,_set)
        mut=preflight_mutator("pl_synthesize",{},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}","s")
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g,lp,mut)

    def test_p10_inflight_dedup(self, pled):
        l,g,lp=pled
        l=_ctx(l); l.execution_lane=EXECUTION_LANE_IDLE; l=ledger_transaction(g,lp,lambda x: l)
        sig=request_signature("s","PL_BUILD","pl_synthesize",{"top":"a"},REAL_REV)
        oid=f"op-{uuid.uuid4().hex}"
        mut=preflight_mutator("pl_synthesize",{"top":"a"},"s","ALINX_AX7020_v1.0","p",oid,sig)
        l=ledger_transaction(g,lp,mut)
        # Same sig with RUNNING op → InFlightDuplicateError
        with pytest.raises(InFlightDuplicateError):
            mut2=preflight_mutator("pl_synthesize",{"top":"a"},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}",sig)
            ledger_transaction(g,lp,mut2)

    def test_p10_terminal_confirm_retry(self, pled):
        l,g,lp=pled
        l=_ctx(l); l=ledger_transaction(g,lp,lambda x: l)
        sig=request_signature("s","PL_BUILD","pl_synthesize",{"top":"b"},REAL_REV)
        oid=f"op-{uuid.uuid4().hex}"
        mut=preflight_mutator("pl_synthesize",{"top":"b"},"s","ALINX_AX7020_v1.0","p",oid,sig)
        l=ledger_transaction(g,lp,mut)
        # Move to terminal
        def _term(l): l.active_operation["status"]="SUCCEEDED"; l.previous_operation=dict(l.active_operation); l.active_operation=None; l.execution_lane=EXECUTION_LANE_IDLE; return l
        l=ledger_transaction(g,lp,_term)
        # Same sig with TERMINAL → TerminalDuplicateError
        with pytest.raises(TerminalDuplicateError):
            mut2=preflight_mutator("pl_synthesize",{"top":"b"},"s","ALINX_AX7020_v1.0","p",f"op-{uuid.uuid4().hex}",sig)
            ledger_transaction(g,lp,mut2)
