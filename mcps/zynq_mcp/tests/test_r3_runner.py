"""
test_r3_runner.py — R3.0 Domain Runner tests. @pytest.mark.asyncio.
Formal R301-R312 match B04_R3_test_plan.md. Supplemental R3X01+.
Run: -W error::RuntimeWarning. 0 warnings required.
"""
import asyncio, json, os, shutil, tempfile, time, uuid
from pathlib import Path
import pytest

from mcps.common.tool_response import success
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_CLOSING,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT, WORKER_STATE_READY, WORKER_STATE_BUSY,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED,
    OP_TIMED_OUT, OP_OUTCOME_UNKNOWN, OP_INTERRUPTED,
    OP_NON_TERMINAL, OP_TERMINAL,
    ChannelBusyError,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, SetRunner, QueryRunner, DomainExecutionMutex,
    ResourceRequirement, DomainValidationError, DomainOutcomeUnknownError,
    _shared_preflight_check, PL_API_CONTRACTS, derive_project_dir,
)

SH = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
BOARD = "ALINX_AX7020_v1.0"


class FakeSingleWorker:
    def __init__(self, *, response=None, should_raise=None, call_count=None,
                 entered=None, barrier=None, delay_s=None):
        self._response = response or {"status": "success", "data": {}}
        self._should_raise = should_raise
        self._call_count = call_count
        self._entered = entered
        self._barrier = barrier
        self._delay_s = delay_s
        self.has_worker = True
    @property
    def adapter_status(self): return "ready"
    async def execute_tool(self, tool_name, arguments, session_id, timeout_s=None):
        if self._call_count is not None: self._call_count.append(1)
        if self._entered: self._entered.set()
        if self._barrier: await self._barrier.wait()
        if self._delay_s: await asyncio.sleep(self._delay_s)
        if self._should_raise: raise self._should_raise
        return self._response


class _LongFakeAdapter:
    """VivadoAdapter stand-in: records the bridge call and holds the run open
    for `delay_s` (a fake 5-30 min synthesis) before returning success."""

    def __init__(self, delay_s):
        self._delay_s = delay_s
        self.calls = []

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        self.calls.append((name, dict(arguments), timeout))
        await asyncio.sleep(self._delay_s)
        return success(data={"ok": True})


class _FakeWorkerHB:
    """SingleWorkerController stand-in returning the configured VivadoAdapter."""

    def __init__(self, adapter):
        self._adapter = adapter

    async def ensure_worker(self):
        return self._adapter


class _LongFakeVivadoBridge:
    """VivadoTclBridge stand-in: records the eval() call and holds the run open
    for `delay_s` (a fake 5-30 min synthesis) before returning success."""

    def __init__(self, delay_s):
        self._delay_s = delay_s
        self.calls = []

    @property
    def ready(self):
        return True

    async def eval(self, tcl, timeout_s=None):
        self.calls.append((tcl, timeout_s))
        await asyncio.sleep(self._delay_s)
        return {"status": "success", "data": ""}


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-r3"); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _new_sid(): return f"session-{uuid.uuid4().hex[:8]}"


def _setup_board(rtg):
    rt, g = rtg; lp = rt / "l.json"; sid = _new_sid()
    def _i(l):
        l.instance_id = g.instance_id; l.workspace_id = "ws-r3"
        l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
        l.context["session_id"] = sid; l.context["board_id"] = BOARD
        l.context["board_package_revision"] = SH; l.context["expected_board_revision"] = SH
        return l
    return ledger_transaction(g, lp, _i), g, lp, sid


class TestR3Runner:

    # ======== R301-R312 FORMAL ========

    @pytest.mark.asyncio
    async def test_R301_local_command_lifecycle(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        called = []
        async def _fn(a): called.append(1); return {"status": "success", "data": {"ok": True}}
        r = await runner.run_command("pl_local", {}, sid, BOARD, "p1", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "success"; oid = r["data"]["operation_id"]
        await asyncio.sleep(0.3); assert len(called) == 1
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["operation_id"] == oid
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    @pytest.mark.asyncio
    async def test_R302_dedup_in_flight(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        entered = asyncio.Event()
        async def _slow(a): entered.set(); await asyncio.sleep(0.5); return {"status": "success", "data": {}}
        r1 = await runner.run_command("pl_dedup", {"x": 1}, sid, BOARD, "p2", executor="local", local_fn=_slow, timeout_s=5)
        assert r1["status"] == "success"
        await entered.wait(); await asyncio.sleep(0.1)
        r2 = await runner.run_command("pl_dedup", {"x": 1}, sid, BOARD, "p2", executor="local", local_fn=_slow, timeout_s=5)
        assert r2["status"] == "success"
        assert r2["data"].get("deduplicated") is True
        assert r2["data"]["operation_id"] == r1["data"]["operation_id"]

    @pytest.mark.asyncio
    async def test_R303_channel_busy_active_op(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        entered = asyncio.Event()
        async def _slow(a): entered.set(); await asyncio.sleep(0.5); return {"status": "success", "data": {}}
        r1 = await runner.run_command("pl_A", {"x": 1}, sid, BOARD, "p3", executor="local", local_fn=_slow, timeout_s=5)
        assert r1["status"] == "success"
        await entered.wait(); await asyncio.sleep(0.1)
        r2 = await runner.run_command("pl_B", {"x": 2}, sid, BOARD, "p3", executor="local", local_fn=lambda a: {"status": "success"}, timeout_s=5)
        assert r2["status"] == "error"
        assert r2["error"]["details"]["reason_code"] == "CHANNEL_BUSY"

    @pytest.mark.asyncio
    async def test_R304_close_session_rejects_active_op(self, rtg):
        """R304. Real bg task -> close rejects -> task NOT cancelled, context retained, op still RUNNING."""
        rt, g = rtg; _, g2, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g2, lp, oreg, mutex, worker=None)
        started = asyncio.Event(); unblock = asyncio.Event()
        cancel_called = []
        async def _long(args):
            started.set()
            try: await unblock.wait()  # block until test says go
            except asyncio.CancelledError: cancel_called.append(1); raise
            return {"status": "success", "data": {}}
        r = await runner.run_command("pl_long", {}, sid, BOARD, "p4", executor="local", local_fn=_long, timeout_s=30)
        assert r["status"] == "success"
        await started.wait()
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        from mcps.zynq_mcp.control.single_worker import SingleWorkerController
        cur, _ = ledger_read_shared(g2, lp)
        sw = SingleWorkerController(cur, g2, lp)
        d = ZynqDispatcher(cur, OperationRegistry(), g2, lp, sw)
        rl = await d.dispatch("close_session", {"session_id": sid}, True)
        data = json.loads(rl[0].text)
        assert data["status"] == "error"
        assert "ACTIVE_OPERATION_PRESENT" in str(data)
        # Task NOT cancelled by close_session
        assert len(cancel_called) == 0, "task must NOT be cancelled by close_session"
        # Context still exists in ledger
        cur2, _ = ledger_read_shared(g2, lp)
        assert cur2.context.get("session_id") == sid
        # Operation still RUNNING
        assert cur2.active_operation["status"] == OP_RUNNING
        # Worker not shut down (SingleWorkerController still alive)
        assert sw.has_worker is False or sw._adapter is None  # no real adapter
        # Let task finish
        unblock.set(); await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_R305_local_timeout(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _forever(a): await asyncio.sleep(60); return {}
        await runner.run_command("test_to", {}, sid, BOARD, "p5", executor="local", local_fn=_forever, timeout_s=0.5)
        await asyncio.sleep(1.0)
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_TIMED_OUT
        assert l2.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED

    @pytest.mark.asyncio
    async def test_R306_local_crash_outcome_unknown(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _crash(a): raise RuntimeError("sim crash")
        await runner.run_command("pl_crash", {}, sid, BOARD, "p6", executor="local", local_fn=_crash, timeout_s=5)
        await asyncio.sleep(0.3)
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_OUTCOME_UNKNOWN
        assert l2.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED

    @pytest.mark.asyncio
    async def test_R307_task_registered_visible(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        entered = asyncio.Event()
        async def _slow(a): entered.set(); await asyncio.sleep(0.5); return {"status": "success", "data": {}}
        r = await runner.run_command("pl_track", {}, sid, BOARD, "p7", executor="local", local_fn=_slow, timeout_s=5)
        oid = r["data"]["operation_id"]
        await entered.wait()
        assert oreg.has_task(oid) is True; assert oreg.task_count() == 1
        await asyncio.sleep(0.5)
        assert oreg.task_count() == 0

    @pytest.mark.asyncio
    async def test_R308_wait_operation_real_polling(self, rtg):
        _, g, lp, sid = _setup_board(rtg)
        oid = f"op-wait-{uuid.uuid4().hex[:8]}"
        def _run(lx):
            lx.active_operation = {"operation_id": oid, "status": OP_RUNNING, "tool_name": "pl_test",
                "session_id": sid, "workflow_stage": "IDLE", "request_signature": "sig-wait",
                "accepted_at": _now_iso(), "started_at": _now_iso()}
            lx.execution_lane = EXECUTION_LANE_BUSY; return lx
        l2 = ledger_transaction(g, lp, _run)
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        from mcps.zynq_mcp.control.single_worker import SingleWorkerController
        sw = SingleWorkerController(l2, g, lp)
        d = ZynqDispatcher(l2, OperationRegistry(), g, lp, sw)
        wait_started = asyncio.Event(); results = []
        async def _waiter():
            wait_started.set()
            rl = await d.dispatch("wait_operation", {"operation_id": oid, "timeout_s": 5}, True)
            results.append(json.loads(rl[0].text))
        t = asyncio.ensure_future(_waiter())
        await wait_started.wait(); await asyncio.sleep(0.15)
        from mcps.zynq_mcp.control.operation_service import op_transition
        op_transition(g, lp, oid, OP_SUCCEEDED, result={"ok": True})
        await t
        assert len(results) == 1
        assert results[0]["status"] == "success"
        assert results[0]["data"]["operation_id"] == oid
        assert results[0]["data"]["status"] == OP_SUCCEEDED

    @pytest.mark.asyncio
    async def test_R309_set_success_idle(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        called = []; fake = FakeSingleWorker(call_count=called)
        runner = SetRunner(g, lp, mutex, worker=fake)
        r = await runner.run_set("pl_set_top", {"module": "top"}, sid, BOARD, "p9", timeout_s=5)
        assert r["status"] == "success"; assert len(called) == 1
        l2, _ = ledger_read_shared(g, lp)
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    @pytest.mark.asyncio
    async def test_R310_query_no_sequence_bump(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        fake = FakeSingleWorker(response={"status": "success", "data": {"done": True}})
        runner = QueryRunner(g, lp, mutex, worker=fake)
        seq = l.ledger_sequence
        r = await runner.run_query("pl_get_device_status", {}, session_id=sid, timeout_s=5)
        assert r["status"] == "success"
        l2, _ = ledger_read_shared(g, lp)
        assert l2.ledger_sequence == seq

    def test_R311_b01_api_contract_check(self):
        """R311. PL_API_CONTRACTS metadata matches B01 frozen: 12 entries, 9/2/1."""
        assert len(PL_API_CONTRACTS) == 12
        cmd = [c for c in PL_API_CONTRACTS if c["category"] == "command"]
        set_ = [c for c in PL_API_CONTRACTS if c["category"] == "set"]
        qry = [c for c in PL_API_CONTRACTS if c["category"] == "query"]
        assert len(cmd) == 9; assert len(set_) == 2; assert len(qry) == 1
        # Verify arg names match B01
        b01_args = {"pl_generate_system_top": ["wrapper_path"],
            "pl_create_project": ["name","part","sources","constraints"],
            "pl_set_top": ["module"], "pl_synthesize": [], "pl_place_and_route": [],
            "pl_analyze_timing": [], "pl_generate_bitstream": ["path"],
            "pl_connect_hw_server": [], "pl_open_hw_target": [],
            "pl_select_device": ["id"], "pl_program": ["bitstream"],
            "pl_get_device_status": []}
        for c in PL_API_CONTRACTS:
            assert c["arg_names"] == b01_args[c["name"]], \
                f"{c['name']}: expected {b01_args[c['name']]}, got {c['arg_names']}"

    def test_R312_project_dir_derivation(self):
        """R312. derive_project_dir() rejects escapes, produces canonical join."""
        result = derive_project_dir("D:/test_proj", "my_design")
        assert result == os.path.normpath("D:/test_proj/vivado/my_design"), f"got {result}"
        with pytest.raises(ValueError): derive_project_dir("D:/t", "..")
        with pytest.raises(ValueError): derive_project_dir("D:/t", "")
        with pytest.raises(ValueError): derive_project_dir("D:/t", ".")
        with pytest.raises(ValueError): derive_project_dir("D:/t", "a/b")

    # ================================================================
    # P2-P8 REGRESSION (each via CommandRunner production entry)
    # ================================================================

    @pytest.mark.asyncio
    async def test_R3X01_p2_dead_pid(self, rtg):
        """P2: worker pid dead -> WORKER_PID_DEAD via CommandRunner, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        # Write worker with dead pid (very unlikely to exist)
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY; lx.worker["pid"] = 99999
            lx.worker["process_start_time"] = 1000000.0
            lx.worker["executable_path"] = "/fake/python"
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = lx.primary_instance_id
            lx.worker["last_heartbeat_at"] = _now_iso()
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p2t", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_PID_DEAD"
        assert len(called) == 0, "Executor must NOT be called on preflight rejection"

    @pytest.mark.asyncio
    async def test_R3X02_p3_identity_mismatch(self, rtg):
        """P3: exe path mismatch -> WORKER_IDENTITY_MISMATCH via CommandRunner, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        # Need a real pid for identity check
        pid = os.getpid()  # use our own process
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY; lx.worker["pid"] = pid
            lx.worker["process_start_time"] = 1000000.0
            lx.worker["executable_path"] = "/nonexistent/python"  # wrong
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = lx.primary_instance_id
            lx.worker["last_heartbeat_at"] = _now_iso()
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p3t", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_IDENTITY_MISMATCH"
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_R3X03_p4_generation_stale(self, rtg):
        """P4: stale gen -> WORKER_GENERATION_STALE via CommandRunner, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        # Need active_operation with ACCEPTED/RUNNING status + lane=BUSY
        def _w(lx):
            lx.active_operation = {"operation_id": "op-gs", "status": OP_ACCEPTED,
                "worker_generation": 10, "tool_name": "pl_other", "session_id": sid}
            lx.execution_lane = EXECUTION_LANE_BUSY
            lx.worker["worker_generation"] = 5
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p4t", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_GENERATION_STALE"
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_R3X04_p5_stale_heartbeat(self, rtg):
        """P5: stale heartbeat (>120s) -> WORKER_UNRESPONSIVE via CommandRunner, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        import sys
        pid = os.getpid()
        from mcps.zynq_mcp.control.process_guard import get_process_identity
        ident = get_process_identity(pid)
        assert ident is not None
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY; lx.worker["pid"] = pid
            lx.worker["process_start_time"] = ident.process_start_time  # real start time
            lx.worker["executable_path"] = ident.executable_path
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = lx.primary_instance_id
            lx.worker["last_heartbeat_at"] = "2020-01-01T00:00:00.000000Z"
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p5t", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_UNRESPONSIVE"
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_R3X05_p6_unresolved_previous(self, rtg):
        """P6: unresolved OUTCOME_UNKNOWN previous -> PREVIOUS_OPERATION_UNRESOLVED."""
        l, g, lp, sid = _setup_board(rtg)
        def _w(lx):
            lx.previous_operation = {"operation_id": "op-prv", "status": OP_OUTCOME_UNKNOWN,
                "tool_name": "pl_crash", "resolved_by_recovery": False}
            return lx
        ledger_transaction(g, lp, _w)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_test", {}, sid, BOARD, "p6t", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "PREVIOUS_OPERATION_UNRESOLVED"

    @pytest.mark.asyncio
    async def test_R3X06_p7_invalid_stage(self, rtg):
        """P7: pl_synthesize at OBSERVATION -> STAGE_PREREQUISITE_UNMET."""
        l, g, lp, sid = _setup_board(rtg)
        def _w(lx):
            lx.context["current_stage"] = "OBSERVATION"; return lx
        ledger_transaction(g, lp, _w)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_synthesize", {}, sid, BOARD, "p7t", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "STAGE_PREREQUISITE_UNMET"

    @pytest.mark.asyncio
    async def test_R3X07_p8_wrong_board_revision(self, rtg):
        """P8: empty board_package_revision -> REVISION_MISMATCH via CommandRunner."""
        l, g, lp, sid = _setup_board(rtg)
        # Set stage to PL_BUILD (valid for pl_synthesize) so P7 passes, then trips P8
        def _w(lx):
            lx.context["board_package_revision"] = ""
            lx.context["current_stage"] = "PL_BUILD"
            return lx
        ledger_transaction(g, lp, _w)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_synthesize", {}, sid, BOARD, "p8t", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "REVISION_MISMATCH", \
            f"Expected REVISION_MISMATCH, got {r['error']['details']}"

    # ---- Instance ownership ----
    @pytest.mark.asyncio
    async def test_R3X08_same_instance_allowed(self, rtg):
        """Worker.instance_id == primary_instance_id -> admission succeeds."""
        l, g, lp, sid = _setup_board(rtg)
        pid = os.getpid()
        from mcps.zynq_mcp.control.process_guard import get_process_identity
        ident = get_process_identity(pid)
        assert ident is not None
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY
            lx.worker["pid"] = pid
            lx.worker["process_start_time"] = ident.process_start_time
            lx.worker["executable_path"] = ident.executable_path
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = lx.primary_instance_id  # match
            lx.worker["last_heartbeat_at"] = _now_iso()
            return lx
        ledger_transaction(g, lp, _w)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_test", {}, sid, BOARD, "pi1", executor="local", local_fn=_fn, timeout_s=5)
        assert r["status"] == "success"

    @pytest.mark.asyncio
    async def test_R3X09_foreign_instance_rejected(self, rtg):
        """Worker.instance_id != primary_instance_id -> WORKER_INSTANCE_MISMATCH, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        pid = os.getpid()
        from mcps.zynq_mcp.control.process_guard import get_process_identity
        ident = get_process_identity(pid)
        assert ident is not None
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY
            lx.worker["pid"] = pid
            lx.worker["process_start_time"] = ident.process_start_time
            lx.worker["executable_path"] = ident.executable_path
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = "foreign-instance-id"  # mismatch
            lx.worker["last_heartbeat_at"] = _now_iso()
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_test", {}, sid, BOARD, "pi2", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_INSTANCE_MISMATCH"
        assert len(called) == 0, "Executor must NOT be called"

    @pytest.mark.asyncio
    async def test_R3X22_primary_owner_missing_rejected(self, rtg):
        """Active worker + primary_instance_id=None -> WORKER_INSTANCE_MISMATCH, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        pid = os.getpid()
        from mcps.zynq_mcp.control.process_guard import get_process_identity
        ident = get_process_identity(pid)
        assert ident is not None
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY
            lx.worker["pid"] = pid
            lx.worker["process_start_time"] = ident.process_start_time
            lx.worker["executable_path"] = ident.executable_path
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = "some-iid"
            lx.worker["last_heartbeat_at"] = _now_iso()
            lx.primary_instance_id = None  # owner missing
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_test", {}, sid, BOARD, "pi3", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_INSTANCE_MISMATCH"
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_R3X23_primary_owner_empty_rejected(self, rtg):
        """Active worker + primary_instance_id="" -> WORKER_INSTANCE_MISMATCH, executor=0."""
        l, g, lp, sid = _setup_board(rtg)
        pid = os.getpid()
        from mcps.zynq_mcp.control.process_guard import get_process_identity
        ident = get_process_identity(pid)
        assert ident is not None
        def _w(lx):
            lx.worker["state"] = WORKER_STATE_READY
            lx.worker["pid"] = pid
            lx.worker["process_start_time"] = ident.process_start_time
            lx.worker["executable_path"] = ident.executable_path
            lx.worker["worker_generation"] = 1
            lx.worker["instance_id"] = "some-iid"
            lx.worker["last_heartbeat_at"] = _now_iso()
            lx.primary_instance_id = ""  # owner empty
            return lx
        ledger_transaction(g, lp, _w)
        called = []; fake = FakeSingleWorker(call_count=called)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_test", {}, sid, BOARD, "pi4", executor="worker", timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "WORKER_INSTANCE_MISMATCH"
        assert len(called) == 0

    # ---- Fault injection: task creation failures ----
    @pytest.mark.asyncio
    async def test_R3X10_ensure_future_fails(self, rtg, monkeypatch):
        """Fault injection: ensure_future raises -> coro.close(), FAILED, 0 warnings."""
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        orig_ensure = asyncio.ensure_future
        def _bad(*a, **kw): raise RuntimeError("simulated")
        monkeypatch.setattr(asyncio, "ensure_future", _bad)
        r = await runner.run_command("pl_test", {}, sid, BOARD, "fi1", executor="local",
            local_fn=lambda a: {"status": "success"}, timeout_s=5)
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "TASK_CREATION_FAILED"
        assert oreg.task_count() == 0
        l2, _ = ledger_read_shared(g, lp)
        assert l2.active_operation is None
        assert l2.previous_operation is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    @pytest.mark.asyncio
    async def test_R3X11_register_task_fails(self, rtg):
        """Fault injection: register_task fails -> FAILED, task cleaned up, no leak."""
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        # Patch register_task to fail
        _orig_register = oreg.register_task
        def _bad_register(*a, **kw): raise RuntimeError("simulated register_task failure")
        oreg.register_task = _bad_register
        try:
            r = await runner.run_command("pl_test", {}, sid, BOARD, "fi2", executor="local",
                local_fn=lambda a: {"status": "success"}, timeout_s=5)
        finally:
            oreg.register_task = _orig_register
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "TASK_REGISTER_FAILED"
        # task was cancelled and awaited (no CancelledError leaked)
        assert oreg.task_count() == 0
        l2, _ = ledger_read_shared(g, lp)
        assert l2.active_operation is None
        assert l2.previous_operation is not None
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    # ---- Remaining supplemental ----
    @pytest.mark.asyncio
    async def test_R3X12_worker_success(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        called = []; fake = FakeSingleWorker(call_count=called)
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        r = await runner.run_command("pl_test", {}, sid, BOARD, "sw", executor="worker", timeout_s=5)
        assert r["status"] == "success"; oid = r["data"]["operation_id"]
        await asyncio.sleep(0.3); assert len(called) == 1
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["operation_id"] == oid

    @pytest.mark.asyncio
    async def test_R3X13_worker_deterministic_error_failed(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        called = []; fake = FakeSingleWorker(call_count=called, response={
            "status": "error", "error": {"code": "TOOL_ERROR", "details": {"reason_code": "INVALID_ARGUMENT"}}})
        runner = CommandRunner(g, lp, oreg, mutex, worker=fake)
        await runner.run_command("pl_err", {}, sid, BOARD, "fe", executor="worker", timeout_s=5)
        await asyncio.sleep(0.3); assert len(called) == 1
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_FAILED
        assert l2.execution_lane == EXECUTION_LANE_IDLE

    @pytest.mark.asyncio
    async def test_R3X14_set_worker_error_recovery_required(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        called = []; fake = FakeSingleWorker(call_count=called, response={
            "status": "error", "error": {"code": "TOOL_ERROR", "message": "fail", "details": {"reason_code": "TOOL_CRASH"}}})
        runner = SetRunner(g, lp, mutex, worker=fake)
        r = await runner.run_set("pl_set_top", {"module":"x"}, sid, BOARD, "sr", timeout_s=5)
        assert r["status"] == "error"
        l2, _ = ledger_read_shared(g, lp)
        assert l2.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED

    # ---- Concurrency ----
    @pytest.mark.asyncio
    async def test_R3X15_two_commands_concurrent(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        results = []; barrier = asyncio.Barrier(2)
        async def _racer(name, args):
            await barrier.wait()
            async def _d(a): await asyncio.sleep(0.2); return {"status": "success", "data": {}}
            r = await runner.run_command(name, args, sid, BOARD, "cc", executor="local", local_fn=_d, timeout_s=5)
            results.append((name, r))
        await asyncio.gather(_racer("pl_A", {"v": 1}), _racer("pl_B", {"v": 2}))
        assert len(results) == 2
        ok = [(n, r) for n, r in results if r["status"] == "success"]
        busy = [(n, r) for n, r in results if r["status"] == "error"]
        assert len(ok) == 1; assert len(busy) == 1
        assert busy[0][1]["error"]["details"]["reason_code"] == "CHANNEL_BUSY"

    @pytest.mark.asyncio
    async def test_R3X16_two_sets_concurrent(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        results = []; barrier = asyncio.Barrier(2); total_calls = []
        async def _racer(name):
            await barrier.wait()
            fake = FakeSingleWorker(call_count=total_calls, delay_s=0.15, response={"status": "success", "data": {"by": name}})
            runner = SetRunner(g, lp, mutex, worker=fake)
            r = await runner.run_set(f"pl_set_{name[-1]}", {}, sid, BOARD, "sc", timeout_s=5)
            results.append((name, r))
        await asyncio.gather(_racer("set_A"), _racer("set_B"))
        assert len(results) == 2
        ok = [(n, r) for n, r in results if r["status"] == "success"]
        busy = [(n, r) for n, r in results if r["status"] == "error"]
        assert len(ok) == 1; assert len(busy) == 1
        assert busy[0][1]["error"]["details"]["reason_code"] == "CHANNEL_BUSY"
        assert len(total_calls) == 1

    @pytest.mark.asyncio
    async def test_R3X17_two_queries_concurrent(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        results = []; barrier = asyncio.Barrier(2); total_calls = []
        async def _racer(name):
            await barrier.wait()
            fake = FakeSingleWorker(call_count=total_calls, delay_s=0.15, response={"status": "success", "data": {"q": name}})
            runner = QueryRunner(g, lp, mutex, worker=fake)
            r = await runner.run_query("pl_get_device_status", {}, session_id=sid, timeout_s=5)
            results.append((name, r))
        await asyncio.gather(_racer("qA"), _racer("qB"))
        assert len(results) == 2
        ok = [(n, r) for n, r in results if r["status"] == "success"]
        busy = [(n, r) for n, r in results if r["status"] == "error"]
        assert len(ok) == 1; assert len(busy) == 1
        assert len(total_calls) == 1

    @pytest.mark.asyncio
    async def test_R3X18_set_vs_query_concurrent(self, rtg):
        l, g, lp, sid = _setup_board(rtg); mutex = DomainExecutionMutex()
        results = []; barrier = asyncio.Barrier(2); total_calls = []
        async def _set():
            await barrier.wait()
            fake = FakeSingleWorker(call_count=total_calls, delay_s=0.15, response={"status": "success", "data": {"role": "set"}})
            runner = SetRunner(g, lp, mutex, worker=fake)
            r = await runner.run_set("pl_set_top", {}, sid, BOARD, "sq", timeout_s=5)
            results.append(("set", r))
        async def _query():
            await barrier.wait()
            fake = FakeSingleWorker(call_count=total_calls, delay_s=0.05)
            runner = QueryRunner(g, lp, mutex, worker=fake)
            r = await runner.run_query("pl_get_device_status", {}, session_id=sid, timeout_s=5)
            results.append(("query", r))
        await asyncio.gather(_set(), _query())
        assert len(results) == 2
        ok = [(n, r) for n, r in results if r["status"] == "success"]
        busy = [(n, r) for n, r in results if r["status"] == "error"]
        assert len(ok) == 1; assert len(busy) == 1
        assert busy[0][1]["error"]["details"]["reason_code"] == "CHANNEL_BUSY"
        assert len(total_calls) == 1

    # ---- P9 ----
    @pytest.mark.asyncio
    async def test_R3X19_p9_acquire_no_lease(self, rtg):
        l, g, lp, sid = _setup_board(rtg); oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p9a", executor="local", local_fn=_fn, timeout_s=5,
            resource_req=ResourceRequirement(type="JTAG_ACQUIRE"))
        assert r["status"] == "success"

    @pytest.mark.asyncio
    async def test_R3X20_p9_acquire_duplicate(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        def _s(lx): lx.worker["jtag_lease"] = {"lease_id":"l","owner_session_id":"o"}; return lx
        ledger_transaction(g, lp, _s)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD, "p9b", executor="local", local_fn=_fn, timeout_s=5,
            resource_req=ResourceRequirement(type="JTAG_ACQUIRE"))
        assert r["status"] == "error"
        assert r["error"]["details"]["reason_code"] == "JTAG_ALREADY_HELD"

    @pytest.mark.asyncio
    async def test_R3X21_p9_require_same_owner(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        def _s(lx): lx.worker["jtag_lease"] = {"lease_id":"l","owner_session_id":sid,"heartbeat_at":_now_iso(),"ttl_s":300}; return lx
        ledger_transaction(g, lp, _s)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)
        async def _fn(a): return {"status": "success", "data": {}}
        r = await runner.run_command("pl_program", {"bitstream":"x.bit"}, sid, BOARD, "p9c", executor="local", local_fn=_fn, timeout_s=5,
            resource_req=ResourceRequirement(type="JTAG_REQUIRE_OWNED"))
        assert r["status"] == "success"

    def test_Xlist_tools_is_ten(self):
        """E007: R3.1-C list_tools=10 (9 control + 1 PL domain). B05 adds platform_generate → 11. B06 first batch adds 24 PS → 35. B06 2nd batch adds 11 BSP → 46. B07 PL bridge adds 26 → 72. B06 third batch (9 download+debug) → 81. B01 UART capture adds 3 → 84. B01 UART diagnostics adds 1 → 85. B01 Phase 4 verify_consistency adds 1 → 86. B01 Phase 6 observation adds 1 → 87. B05-R2 platform atoms add 14 → 101. B11 phase 2 removes platform_generate → 100 (9 control + 91 domain)."""
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        assert len(ALL_TOOLS) == 100

    @pytest.mark.asyncio
    async def test_long_run_does_not_fabricate_heartbeat(self, rtg):
        """O3: a Python timer must not impersonate a real tool observation."""
        l, g, lp, sid = _setup_board(rtg)
        # pl_synthesize is only admitted from PL_BUILD
        def _stg(lx): lx.context["current_stage"] = "PL_BUILD"; return lx
        ledger_transaction(g, lp, _stg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex,
                               vivado_bridge=_LongFakeVivadoBridge(delay_s=0.6))
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        local_fn = _make_pl_bridge_local_fn("pl_synthesize")
        r = await runner.run_command("pl_synthesize", {}, sid, BOARD, "/p",
            executor="local", local_fn=local_fn, timeout_s=5.0, next_stage=None)
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]

        # The compatibility bridge has no O3 observer, so heartbeat_at must
        # stay null rather than becoming false evidence of a healthy Vivado.
        heartbeat_values = []
        deadline = time.time() + 5.0
        while time.time() < deadline:
            cur, _ = ledger_read_shared(g, lp)
            ao = cur.active_operation
            if ao is None or ao.get("operation_id") != oid:
                break
            heartbeat_values.append(ao.get("heartbeat_at"))
            await asyncio.sleep(0.02)
        assert heartbeat_values
        assert all(value is None for value in heartbeat_values)
        # the async bridge call carried the long-run PL_TOOL_MAP eval timeout
        assert runner._vivado_bridge.calls[-1][1] >= 3600.0
        # Operation completion does not retroactively invent one either.
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["heartbeat_at"] is None


class TestGetOperationStatusHeartbeat:
    """B07: get_operation_status surfaces computed heartbeat_age_s / elapsed_s
    (and omits them when the underlying timestamp is absent)."""

    @staticmethod
    def _call(g, lp, oid):
        from mcps.zynq_mcp.dispatcher import _get_operation_status
        class _Disp:
            def __init__(self):
                self._op_registry = OperationRegistry()
        d = _Disp()
        d._ledger, _ = ledger_read_shared(g, lp)
        return _get_operation_status({"operation_id": oid}, d)

    def test_running_op_computes_heartbeat_age_and_elapsed(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oid = f"op-hb-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        def _run(lx):
            lx.active_operation = {
                "operation_id": oid, "tool_name": "pl_synthesize",
                "status": OP_RUNNING, "session_id": sid, "workflow_stage": "PL_BUILD",
                "accepted_at": now, "started_at": now, "heartbeat_at": now,
            }
            lx.execution_lane = EXECUTION_LANE_BUSY
            return lx
        ledger_transaction(g, lp, _run)
        out = self._call(g, lp, oid)
        assert out["status"] == "success"
        data = out["data"]
        assert data["operation_id"] == oid and data["status"] == OP_RUNNING
        assert data["heartbeat_at"] == now
        assert isinstance(data["heartbeat_age_s"], (int, float)) and data["heartbeat_age_s"] >= 0
        assert isinstance(data["elapsed_s"], (int, float)) and data["elapsed_s"] >= 0
        # O1: percentage remains optional/null, but every command now has a
        # bounded persisted deadline independent of wait_operation timeout.
        assert data.get("progress_pct") is None
        assert isinstance(data.get("deadline_at"), str) and data["deadline_at"]

    def test_terminal_op_previous_computes_fields(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oid = f"op-term-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        def _run(lx):
            lx.previous_operation = {
                "operation_id": oid, "tool_name": "pl_synthesize",
                "status": OP_SUCCEEDED, "session_id": sid, "workflow_stage": "PL_BUILD",
                "accepted_at": now, "started_at": now, "heartbeat_at": now,
            }
            return lx
        ledger_transaction(g, lp, _run)
        out = self._call(g, lp, oid)
        assert out["status"] == "success"
        assert out["data"]["status"] == OP_SUCCEEDED
        assert out["data"]["heartbeat_age_s"] >= 0
        assert out["data"]["elapsed_s"] >= 0

    def test_no_heartbeat_omits_age(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oid = f"op-nohb-{uuid.uuid4().hex[:8]}"
        def _run(lx):
            lx.active_operation = {
                "operation_id": oid, "tool_name": "pl_synthesize",
                "status": OP_RUNNING, "session_id": sid, "workflow_stage": "PL_BUILD",
                "accepted_at": _now_iso(), "started_at": _now_iso(), "heartbeat_at": None,
            }
            lx.execution_lane = EXECUTION_LANE_BUSY
            return lx
        ledger_transaction(g, lp, _run)
        out = self._call(g, lp, oid)
        assert out["status"] == "success"
        assert "heartbeat_age_s" not in out["data"]
        assert "elapsed_s" in out["data"]


class _ToggleXsctBridge:
    """XsctBridge stand-in with a togglable ready flag.

    Mirrors the XsctBridge interface used by _ensure_xsct_bridge (start,
    stop, ready, eval, workspace) without launching a real xsct process. The
    test calls ``die()`` to simulate the XSCT subprocess dying after a
    successful start (the C3 failure mode).
    """

    def __init__(self, ready=False):
        self._ready = ready
        self.workspace = ""
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def ready(self):
        return self._ready

    async def start(self, workspace=""):
        self.start_calls += 1
        self._ready = True
        self.workspace = workspace or ""

    async def stop(self):
        self.stop_calls += 1
        self._ready = False

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        return {"status": "success", "data": ""}

    def die(self):
        self._ready = False


class TestBridgeLifecycleC3:
    """C3: a bridge subprocess that dies after a successful start must be
    re-started by _ensure_*_bridge instead of permanently failing every later
    XSCT-dependent tool.  Process-free helpers such as ps_read_elf_info are
    covered separately and must never start or restart an EDA backend."""

    @pytest.mark.asyncio
    async def test_xsct_bridge_restarts_after_death(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        bridge = _ToggleXsctBridge()
        runner = CommandRunner(g, lp, oreg, mutex, xsct_bridge=bridge)

        b1 = await runner._ensure_xsct_bridge("D:/ws")
        assert b1 is bridge and bridge.ready
        assert bridge.start_calls == 1
        assert bridge.workspace == "D:/ws"

        # the XSCT subprocess dies after a successful compile
        bridge.die()
        b2 = await runner._ensure_xsct_bridge("D:/ws")
        assert b2 is bridge and bridge.ready
        assert bridge.start_calls == 2, "dead bridge must be restarted"
        assert bridge.stop_calls >= 1, "dead shell must be reaped first"

    @pytest.mark.asyncio
    async def test_xsct_bridge_restart_failure_is_fail_closed(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()

        class _Broken(_ToggleXsctBridge):
            async def start(self, workspace=""):
                self.start_calls += 1
                raise RuntimeError("xsct executable not found")

        bridge = _Broken()
        runner = CommandRunner(g, lp, oreg, mutex, xsct_bridge=bridge)
        b = await runner._ensure_xsct_bridge("D:/ws")
        assert b is None, "a failed (re)start must fail closed, never a crash"

    @pytest.mark.asyncio
    async def test_xsct_bridge_absent_returns_none(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, xsct_bridge=None)
        b = await runner._ensure_xsct_bridge("D:/ws")
        assert b is None

    @pytest.mark.asyncio
    async def test_xsdb_bridge_restarts_after_death(self, rtg):
        l, g, lp, sid = _setup_board(rtg)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        bridge = _ToggleXsctBridge()
        runner = CommandRunner(g, lp, oreg, mutex, xsdb_bridge=bridge)
        b1 = await runner._ensure_xsdb_bridge()
        assert b1 is bridge and bridge.ready
        bridge.die()
        b2 = await runner._ensure_xsdb_bridge()
        assert b2 is bridge and bridge.ready
        assert bridge.start_calls == 2
        assert bridge.stop_calls >= 1

    @pytest.mark.asyncio
    async def test_query_tool_succeeds_after_compile_bridge_death(
            self, rtg, tmp_path, monkeypatch):
        """C3 end-to-end: ps_compile succeeds, then the XSCT subprocess dies;
        ps_get_build_status must still succeed because the dead bridge is
        re-started instead of permanently bricking the session."""
        from mcps.zynq_mcp.domains.ps import ps_bsp
        l, g, lp, sid = _setup_board(rtg)

        def _ctx(lx):
            lx.context["project_path"] = str(tmp_path)
            return lx
        ledger_transaction(g, lp, _ctx)

        # an app with a Debug ELF so compile_app succeeds (app build -> ELF)
        app_dir = tmp_path / "app"
        (app_dir / "src").mkdir(parents=True)
        (app_dir / "Debug").mkdir()
        elf_header = bytearray(52)
        elf_header[:4] = b"\x7fELF"
        elf_header[4] = 1
        elf_header[5] = 1
        elf_header[16:18] = (2).to_bytes(2, "little")
        elf_header[18:20] = (40).to_bytes(2, "little")
        (app_dir / "Debug" / "app.elf").write_bytes(bytes(elf_header))

        # This historical test isolates bridge restart semantics.  Supply a
        # deterministic manifest publisher so the newer O4 terminal-integrity
        # gate does not turn it into a duplicate Manifest integration test.
        manifest = tmp_path / "manifests" / "ps" / "fixture.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({
            "manifest_revision": "sha256:" + "9" * 64}),
            encoding="utf-8")
        monkeypatch.setattr(
            "mcps.zynq_mcp.control.domain_runner._publish_build_manifest",
            lambda *args, **kwargs: str(manifest))

        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        bridge = _ToggleXsctBridge()
        runner = CommandRunner(g, lp, oreg, mutex, xsct_bridge=bridge)

        async def _wait_terminal(oid, timeout_s=5.0):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                cur, _ = ledger_read_shared(g, lp)
                if cur.active_operation is None or \
                        cur.active_operation.get("operation_id") != oid:
                    return cur
                await asyncio.sleep(0.01)
            raise AssertionError(f"op {oid} did not reach terminal")

        # first command: starts the XsctBridge
        r1 = await runner.run_command(
            "ps_compile", {"app_name": "app"}, sid, BOARD, str(tmp_path),
            executor="local", local_fn=ps_bsp.compile_app, timeout_s=5)
        assert r1["status"] == "success"
        l1 = await _wait_terminal(r1["data"]["operation_id"])
        assert l1.previous_operation["status"] == OP_SUCCEEDED, l1
        assert bridge.start_calls == 1

        # the XSCT subprocess dies after the compile succeeds
        bridge.die()

        # query tool on the dead bridge must re-start it and succeed
        r2 = await runner.run_command(
            "ps_get_build_status", {}, sid, BOARD, str(tmp_path),
            executor="local", local_fn=ps_bsp.get_build_status, timeout_s=5)
        assert r2["status"] == "success", r2
        l2 = await _wait_terminal(r2["data"]["operation_id"])
        assert l2.previous_operation["status"] == OP_SUCCEEDED, l2
        assert bridge.start_calls == 2, "query tool must restart the bridge"
        assert bridge.stop_calls >= 1
