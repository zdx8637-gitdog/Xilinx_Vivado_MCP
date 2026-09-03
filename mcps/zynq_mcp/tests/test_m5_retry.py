"""test_m5_retry.py — B13-M5: FAILED-terminal retry semantics (P10 dedup).

The P2 real-board incident: ps_compile FAILED (duplicate define in the
workspace) → the legitimate same-args retry was rejected with
CONFIRM_RETRY_REQUIRED because the previous attempt's terminal state was
treated as a completed duplicate. The fix (dedup_lookup shared helper):
only a SUCCEEDED previous attempt blocks re-admission; FAILED-class
terminals (FAILED/CANCELLED/TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN) are
retryable because they produced no artifacts.
"""
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, EXECUTION_LANE_IDLE, ChannelBusyError,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator, dedup_lookup
from mcps.zynq_mcp.control.operation_service import (
    request_signature, InFlightDuplicateError, TerminalDuplicateError,
)

# Real locked fixture board-package revision (mcps/common/tests/fixtures)
REAL_REV = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-m5")
    g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


@pytest.fixture
def pled(rtg):
    rt, g = rtg
    lp = rt / "ledger.json"

    def _i(l):
        l.instance_id = g.instance_id
        l.workspace_id = "ws-m5"
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        return l

    l = ledger_transaction(g, lp, _i)

    def _ctx(l):
        l.context = {"board_id": "ALINX_AX7020_v1.0",
                     "current_stage": "PL_BUILD",
                     "board_package_revision": REAL_REV,
                     "expected_board_revision": REAL_REV}
        return l

    l = ledger_transaction(g, lp, _ctx)
    return l, g, lp


def _seed_terminal(g, lp, sig, status):
    """Seed a dedup entry + a terminal previous_operation with `status`."""
    def _m(l):
        l.dedup_registry[sig] = "op-old"
        l.previous_operation = {"operation_id": "op-old",
                                "tool_name": "pl_synthesize",
                                "status": status}
        return l
    return ledger_transaction(g, lp, _m)


class TestDedupRetry:
    @pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
    def test_failed_terminal_retryable(self, pled, status):
        l, g, lp = pled
        sig = request_signature("s", "PL_BUILD", "pl_synthesize",
                                {"top": "x"}, REAL_REV)
        l = _seed_terminal(g, lp, sig, status)
        # dedup_lookup must not raise for FAILED-class terminals
        assert dedup_lookup(l, sig) is None
        # Full admission path: preflight admits and overwrites the entry
        oid = f"op-{uuid.uuid4().hex}"
        mut = preflight_mutator("pl_synthesize", {"top": "x"}, "s",
                                "ALINX_AX7020_v1.0", "p", oid, sig)
        l2 = ledger_transaction(g, lp, mut)
        assert l2.active_operation["operation_id"] == oid
        assert l2.dedup_registry[sig] == oid  # entry overwritten by new op

    @pytest.mark.parametrize("status", [
        "INTERRUPTED", "TIMED_OUT", "OUTCOME_UNKNOWN"])
    def test_unresolved_terminal_dedup_free_but_p6_blocks(self, pled, status):
        # B13-M5: the DEDUP layer no longer blocks these; the separate P6 gate
        # (D-E design) still requires recover_execution first — layered,
        # fail-closed semantics preserved.
        l, g, lp = pled
        sig = request_signature("s", "PL_BUILD", "pl_synthesize",
                                {"top": "u"}, REAL_REV)
        l = _seed_terminal(g, lp, sig, status)
        assert dedup_lookup(l, sig) is None
        mut = preflight_mutator("pl_synthesize", {"top": "u"}, "s",
                                "ALINX_AX7020_v1.0", "p",
                                f"op-{uuid.uuid4().hex}", sig)
        with pytest.raises(ChannelBusyError) as ei:
            ledger_transaction(g, lp, mut)
        assert "PREVIOUS_OPERATION_UNRESOLVED" in str(ei.value)

    def test_succeeded_terminal_still_blocked(self, pled):
        l, g, lp = pled
        sig = request_signature("s", "PL_BUILD", "pl_synthesize",
                                {"top": "y"}, REAL_REV)
        l = _seed_terminal(g, lp, sig, "SUCCEEDED")
        with pytest.raises(TerminalDuplicateError):
            dedup_lookup(l, sig)

    def test_inflight_still_blocked(self, pled):
        l, g, lp = pled
        sig = request_signature("s", "PL_BUILD", "pl_synthesize",
                                {"top": "z"}, REAL_REV)
        def _m(l):
            l.dedup_registry[sig] = "op-run"
            l.active_operation = {"operation_id": "op-run",
                                  "tool_name": "pl_synthesize",
                                  "status": "RUNNING"}
            l.execution_lane = "BUSY"
            return l
        l = ledger_transaction(g, lp, _m)
        with pytest.raises(InFlightDuplicateError):
            dedup_lookup(l, sig)

    def test_stale_entry_retryable(self, pled):
        l, g, lp = pled
        sig = request_signature("s", "PL_BUILD", "pl_synthesize",
                                {"top": "w"}, REAL_REV)
        def _m(l):
            l.dedup_registry[sig] = "op-gone"  # matches neither ao nor po
            return l
        l = ledger_transaction(g, lp, _m)
        assert dedup_lookup(l, sig) is None
