"""test_r1_recovery.py — Recovery tests."""
import os, shutil, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_TIMED_OUT, OP_OUTCOME_UNKNOWN, OP_RUNNING,
    WORKER_STATE_READY, WORKER_STATE_ABSENT,
    operation_contract_fields, _iso_after, _now_iso,
    ChannelBusyError,
)
from mcps.zynq_mcp.control.recovery import recovery_mutator, diagnose_execution, is_zombie_accepted
from mcps.zynq_mcp.control.process_guard import get_process_identity

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

    def test_d1_zombie_accepted_resolves_with_stale_live_worker(self, rtg):
        """D1: a zombie ACCEPTED op (never started + expired deadline) with a
        live-but-stale worker must be resolved by recover_execution, not
        deadlock with RECOVERY_BLOCKED_WORKER_ALIVE."""
        rt, g = rtg; lp = rt / "l.json"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            assert proc.poll() is None  # the worker is a REAL live process
            ident = get_process_identity(proc.pid)
            assert ident is not None
            oid = f"op-zombie-{uuid.uuid4().hex[:8]}"
            old = "2020-01-01T00:00:00.000000Z"
            def _init(l):
                l.instance_id = g.instance_id; l.workspace_id = "ws-test"
                l.primary_instance_id = g.instance_id
                l.execution_lane = EXECUTION_LANE_BUSY
                l.context = {"session_id": "s", "board_id": "ALINX_AX7020_v1.0",
                             "project_path": "D:/proj", "current_stage": "PL_BUILD",
                             "board_package_revision": REAL_REV,
                             "expected_board_revision": REAL_REV}
                l.worker.update({
                    "pid": proc.pid, "state": WORKER_STATE_READY,
                    "process_start_time": ident.process_start_time,
                    "executable_path": ident.executable_path,
                    "worker_generation": 1, "instance_id": g.instance_id,
                    "last_heartbeat_at": old,  # stale heartbeat
                })
                ao = {
                    "operation_id": oid, "tool_name": "ps_create_platform",
                    "status": OP_ACCEPTED, "api_category": "command",
                    "session_id": "s", "board_id": "ALINX_AX7020_v1.0",
                    "project_path": "D:/proj", "workflow_stage": "PL_BUILD",
                    "request_signature": "sig", "worker_generation": 1,
                    "input_artifact_revision": REAL_REV, "accepted_at": old,
                    "started_at": None, "heartbeat_at": None, "finished_at": None,
                    "output_artifact_revision": None, "completion_evidence": None,
                    "error": None, "progress_pct": None,
                    **operation_contract_fields("ps_create_platform", old),
                }
                # force the deadline into the past (provably expired zombie).
                ao["deadline_at"] = _iso_after(old, -600.0)
                l.active_operation = ao
                return l
            ledger_transaction(g, lp, _init)
            l0, _ = ledger_read_shared(g, lp)
            assert is_zombie_accepted(l0.active_operation)
            assert l0.active_operation["started_at"] is None
            assert l0.execution_lane == EXECUTION_LANE_BUSY

            l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
            assert l.execution_lane == EXECUTION_LANE_IDLE
            assert l.active_operation is None
            assert l.previous_operation["operation_id"] == oid
            assert l.previous_operation["status"] == OP_TIMED_OUT
            assert l.previous_operation["resolved_by_recovery"] is True
            assert l.worker["pid"] == proc.pid  # fail-closed: worker untouched

            # the resolved op no longer blocks admission (P6).
            from mcps.zynq_mcp.control.execution_gate import preflight_mutator
            from mcps.zynq_mcp.control.operation_service import request_signature
            sig = request_signature("s", "PL_BUILD", "pl_synthesize", {}, REAL_REV)
            mut = preflight_mutator("pl_synthesize", {}, "s", "ALINX_AX7020_v1.0",
                                    "D:/proj", f"op-{uuid.uuid4().hex}", sig)
            l2 = ledger_transaction(g, lp, mut)
            assert l2.active_operation is not None
            assert l2.active_operation["status"] == OP_ACCEPTED
        finally:
            proc.kill(); proc.wait(timeout=5)

    def test_d1_zombie_accepted_still_refuses_running(self, rtg):
        """D1 fail-closed: a genuinely RUNNING op is never a zombie and must
        still refuse recovery (never auto-resolve an open-ended op)."""
        rt, g = rtg; lp = rt / "l.json"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.primary_instance_id = g.instance_id
            l.execution_lane = EXECUTION_LANE_BUSY
            l.active_operation = {
                "operation_id": "op-running", "tool_name": "ps_create_platform",
                "status": OP_RUNNING, "api_category": "command",
                "session_id": "s", "board_id": "ALINX_AX7020_v1.0",
                "project_path": "D:/proj", "workflow_stage": "PL_BUILD",
                "request_signature": "sig", "worker_generation": 1,
                "input_artifact_revision": REAL_REV, "accepted_at": "2020-01-01T00:00:00.000000Z",
                "started_at": "2020-01-01T00:00:01.000000Z", "heartbeat_at": None,
                "finished_at": None, "output_artifact_revision": None,
                "completion_evidence": None, "error": None, "progress_pct": None,
                **operation_contract_fields("ps_create_platform", "2020-01-01T00:00:00.000000Z"),
            }
            return l
        ledger_transaction(g, lp, _init)
        assert is_zombie_accepted(ledger_read_shared(g, lp)[0].active_operation) is False
        with pytest.raises(ChannelBusyError):
            ledger_transaction(g, lp, recovery_mutator("op-x"))

    def test_de_unresolved_previous_resolves_with_live_worker(self, rtg):
        """D-E: a previous op in OUTCOME_UNKNOWN (unresolved) with a live
        worker must be resolved by recover_execution so a SINGLE unresolved op
        does not force a whole-runtime rotation."""
        rt, g = rtg; lp = rt / "l.json"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            assert proc.poll() is None  # the worker is a REAL live process
            ident = get_process_identity(proc.pid)
            assert ident is not None
            def _init(l):
                l.instance_id = g.instance_id; l.workspace_id = "ws-test"
                l.primary_instance_id = g.instance_id
                l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
                l.context = {"session_id": "s", "board_id": "ALINX_AX7020_v1.0",
                             "project_path": "D:/proj", "current_stage": "PL_BUILD",
                             "board_package_revision": REAL_REV,
                             "expected_board_revision": REAL_REV}
                l.worker.update({
                    "pid": proc.pid, "state": WORKER_STATE_READY,
                    "process_start_time": ident.process_start_time,
                    "executable_path": ident.executable_path,
                    "worker_generation": 1, "instance_id": g.instance_id,
                    "last_heartbeat_at": _now_iso(),
                })
                l.previous_operation = {
                    "operation_id": "op-old", "tool_name": "ps_create_platform",
                    "status": OP_OUTCOME_UNKNOWN, "api_category": "command",
                    "session_id": "s", "board_id": "ALINX_AX7020_v1.0",
                    "project_path": "D:/proj", "workflow_stage": "PL_BUILD",
                    "request_signature": "sig", "worker_generation": 1,
                    "input_artifact_revision": REAL_REV,
                    "accepted_at": "2020-01-01T00:00:00.000000Z",
                    "started_at": "2020-01-01T00:00:01.000000Z",
                    "finished_at": "2020-01-01T00:00:02.000000Z",
                    "output_artifact_revision": None, "completion_evidence": None,
                    "error": "boom", "progress_pct": None,
                    **operation_contract_fields("ps_create_platform", "2020-01-01T00:00:00.000000Z"),
                }
                return l
            ledger_transaction(g, lp, _init)
            l = ledger_transaction(g, lp, recovery_mutator(f"op-{uuid.uuid4().hex}"))
            assert l.execution_lane == EXECUTION_LANE_IDLE
            assert l.previous_operation["status"] == OP_OUTCOME_UNKNOWN
            assert l.previous_operation["resolved_by_recovery"] is True
            assert l.worker["pid"] == proc.pid  # fail-closed: worker untouched
            # D1-residual fix: when the live worker is KEPT, its generation MUST
            # stay the controller's own in-memory generation. Bumping it would
            # desync the next ensure_backend re-entry (which compares
            # worker_generation == self._generation) and fail with
            # BACKEND_IDENTITY_MISMATCH — the exact whitebox v2 residual.
            assert l.worker["worker_generation"] == 1

            # the next command is ADMITTED (P6 no longer blocks).
            from mcps.zynq_mcp.control.execution_gate import preflight_mutator
            from mcps.zynq_mcp.control.operation_service import request_signature
            sig = request_signature("s", "PL_BUILD", "pl_synthesize", {}, REAL_REV)
            mut = preflight_mutator("pl_synthesize", {}, "s", "ALINX_AX7020_v1.0",
                                    "D:/proj", f"op-{uuid.uuid4().hex}", sig)
            l2 = ledger_transaction(g, lp, mut)
            assert l2.active_operation is not None
            assert l2.active_operation["status"] == OP_ACCEPTED
            # A kept-live worker with unchanged generation also re-verifies
            # cleanly through the process-controller identity gate (the D1
            # residual regression).
            assert l2.worker["worker_generation"] == 1
        finally:
            proc.kill(); proc.wait(timeout=5)
