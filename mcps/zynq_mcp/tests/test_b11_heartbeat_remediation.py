"""
test_b11_heartbeat_remediation.py — B11 phase ③.1 D4 heartbeat liveness model.

Real-process regression tests (FAKE_MCP stdio subprocess style, mirrors
test_r2_adapter.py) for the "ask for the process" heartbeat model:

  (a) a stale heartbeat on a verified-alive worker no longer blocks the next
      command (no WORKER_UNRESPONSIVE) — both the execution_gate P5 and the
      CommandRunner shared preflight admit, and the staleness is recorded as
      a diagnostic on the worker record;
  (b) a dead process still fails closed with WORKER_PID_DEAD;
  (c) an identity mismatch still fails closed with WORKER_IDENTITY_MISMATCH;
  (d) transient ledger failures (LEDGER_READ_FAILED / HEARTBEAT_WRITE_FAILED)
      do NOT kill the heartbeat loop — it survives and refreshes;
  (e) recover_execution on ALIVE+STALE (idle deadlock) revives the heartbeat
      without close_session; a live worker with a non-terminal active
      operation is still refused (RECOVERY_BLOCKED_WORKER_ALIVE).

Runtime-operation fail-closedness (observer / process controller → stale →
RECOVERY_REQUIRED) is NOT touched by this round and is covered by the O3
observer tests.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT, WORKER_STATE_READY, WORKER_STATE_POISONED,
    WORKER_STATE_DEAD, WORKER_STATE_BUSY, OP_ACCEPTED, OP_SUCCEEDED,
    OP_RUNNING, OP_NON_TERMINAL, OP_TERMINAL, ChannelBusyError,
    LedgerWriteError,
)
from mcps.zynq_mcp.control.single_worker import (
    SingleWorkerController, _set_worker_state, HEARTBEAT_INTERVAL,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.operation_service import request_signature
from mcps.zynq_mcp.control.domain_runner import CommandRunner, DomainExecutionMutex
from mcps.zynq_mcp.control.process_guard import is_pid_alive, kill_process_tree_exact
from mcps.zynq_mcp.adapters.vivado_adapter import (
    VivadoAdapter, VivadoBridge, ADAPTER_READY,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")

BOARD = "ALINX_AX7020_v1.0"
_SHA = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
FAKE_MCP = str(Path(__file__).resolve().parent / "helpers" / "fake_mcp.py")


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-b11hb"); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _fresh_ledger(rtg):
    rt, g = rtg
    lp = rt / "l.json"

    def _i(l):
        l.instance_id = g.instance_id; l.workspace_id = "ws-b11hb"
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        l.context["session_id"] = f"session-{uuid.uuid4().hex[:8]}"
        l.context["board_id"] = BOARD
        l.context["board_package_revision"] = _SHA
        l.context["expected_board_revision"] = _SHA
        l.context["current_stage"] = "PL_BUILD"
        return l
    return ledger_transaction(g, lp, _i), g, lp


async def _start_real_worker(rtg):
    """Start a REAL FAKE_MCP subprocess as the worker and wire a
    SingleWorkerController that holds it (ledger identity = OS truth)."""
    l, g, lp = _fresh_ledger(rtg)
    a = VivadoAdapter()
    a._server_path = FAKE_MCP
    a._bridge = VivadoBridge(command=sys.executable, args=[FAKE_MCP],
                             cwd=str(rtg[0]))
    await a._bridge.start()
    a._child_pid = a._bridge.child_pid
    a._started = True; a.status = ADAPTER_READY; a._generation += 1
    pid = a.child_pid
    assert is_pid_alive(pid)
    sw = SingleWorkerController(l, g, lp)
    sw._adapter = a
    ident = a.worker_identity
    ledger_transaction(g, lp, lambda lx: _set_worker_state(
        lx, ident, WORKER_STATE_READY))
    return sw, a, pid, g, lp, ident


def _set_stale_hb(g, lp, pid, ident, ts="2020-01-01T00:00:00.000000Z"):
    def _w(lx):
        lx.worker["state"] = WORKER_STATE_READY; lx.worker["pid"] = pid
        lx.worker["process_start_time"] = ident.get("process_start_time")
        lx.worker["executable_path"] = ident.get("executable_path")
        lx.worker["worker_generation"] = ident.get("worker_generation", 0)
        lx.worker["instance_id"] = ident.get("instance_id") or lx.instance_id
        lx.worker["last_heartbeat_at"] = ts
        return lx
    ledger_transaction(g, lp, _w)


def _op(g, lp, tool="pl_connect_hw_server"):
    op_id = f"op-{uuid.uuid4().hex}"
    sig = request_signature("", "PL_BUILD", tool, {}, _SHA)
    return op_id, sig


# ═══════════════════════════════════════════════════════════════════════
#  (a) stale heartbeat + verified-alive worker → ADMITTED (P5 asks for the
#      process; both the execution_gate and the CommandRunner shared preflight)
# ═══════════════════════════════════════════════════════════════════════

async def test_a1_gate_admits_stale_heartbeat_on_alive_process(rtg):
    """execution_gate P5: stale hb + alive real process → admitted (no
    WORKER_UNRESPONSIVE); the staleness is recorded as a diagnostic."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        _set_stale_hb(g, lp, pid, ident)
        op_id, sig = _op(g, lp)
        mut = preflight_mutator("pl_connect_hw_server", {}, "s", BOARD,
                                "/p", op_id, sig)
        l2 = ledger_transaction(g, lp, mut)  # must NOT raise
        assert l2.active_operation is not None
        assert l2.active_operation["operation_id"] == op_id
        # diagnostic written for observability
        assert l2.worker["last_heartbeat_stale_s"] > 120.0
    finally:
        await sw.shutdown()


async def test_a2_runner_admits_stale_heartbeat_on_alive_process(rtg):
    """CommandRunner shared preflight: stale hb + alive real process → the
    command is admitted and the local executor actually runs."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        _set_stale_hb(g, lp, pid, ident)
        called = []
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None)

        async def _fn(args):
            called.append(1)
            return {"status": "success", "data": {}}

        sid = ledger_read_shared(g, lp)[0].context["session_id"]
        r = await runner.run_command("pl_connect_hw_server", {}, sid, BOARD,
                                     "/p", executor="local", local_fn=_fn,
                                     timeout_s=5)
        assert r["status"] == "success", r
        oid = r["data"]["operation_id"]
        deadline = time.time() + 5.0
        while time.time() < deadline:
            l2, _ = ledger_read_shared(g, lp)
            po = l2.previous_operation or {}
            if po.get("operation_id") == oid and po.get("status") in OP_TERMINAL:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("operation did not reach a terminal state")
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert len(called) == 1
    finally:
        await sw.shutdown()


# ═══════════════════════════════════════════════════════════════════════
#  (b) dead process → still WORKER_PID_DEAD
# ═══════════════════════════════════════════════════════════════════════

async def test_b1_dead_process_still_rejected(rtg):
    """Killing the real worker process → the next command still fails closed
    with WORKER_PID_DEAD (the process ask is still authoritative)."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        kill_process_tree_exact(pid)
        deadline = time.time() + 5.0
        while time.time() < deadline and is_pid_alive(pid):
            await asyncio.sleep(0.05)
        assert not is_pid_alive(pid), "worker process must be dead"
        # worker ledger still points at the (now dead) pid
        op_id, sig = _op(g, lp)
        mut = preflight_mutator("pl_connect_hw_server", {}, "s", BOARD,
                                "/p", op_id, sig)
        with pytest.raises(ChannelBusyError) as ei:
            ledger_transaction(g, lp, mut)
        assert "WORKER_PID_DEAD" in str(ei.value)
    finally:
        await sw.shutdown()


# ═══════════════════════════════════════════════════════════════════════
#  (c) identity mismatch → still rejected
# ═══════════════════════════════════════════════════════════════════════

async def test_c1_identity_mismatch_still_rejected(rtg):
    """A start-time drift against the real process → WORKER_IDENTITY_MISMATCH
    (P3 unchanged by the remediation)."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        ident_bad = dict(ident)
        ident_bad["process_start_time"] = ident["process_start_time"] - 100.0
        _set_stale_hb(g, lp, pid, ident_bad)
        op_id, sig = _op(g, lp)
        mut = preflight_mutator("pl_connect_hw_server", {}, "s", BOARD,
                                "/p", op_id, sig)
        with pytest.raises(ChannelBusyError) as ei:
            ledger_transaction(g, lp, mut)
        assert "WORKER_IDENTITY_MISMATCH" in str(ei.value)
    finally:
        await sw.shutdown()


# ═══════════════════════════════════════════════════════════════════════
#  (d) transient ledger failures → heartbeat loop survives and refreshes
# ═══════════════════════════════════════════════════════════════════════

async def test_d1_heartbeat_once_transient_write_failure_no_crash(rtg, monkeypatch):
    """HEARTBEAT_WRITE_FAILED (ledger_transaction raising) → ok=False with the
    reason code, NO crash state, and the error is recorded for the next tick."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        import mcps.zynq_mcp.control.single_worker as sw_mod
        real = sw_mod.ledger_transaction
        calls = {"n": 0}

        def _failing(l, p, m, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise LedgerWriteError("injected transient write failure")
            return real(l, p, m, **kw)

        monkeypatch.setattr(sw_mod, "ledger_transaction", _failing)
        result = await sw.heartbeat_once()
        assert not result.ok
        assert result.reason_code == "HEARTBEAT_WRITE_FAILED"
        assert sw.last_heartbeat_error is not None
        l2, _ = ledger_read_shared(g, lp)
        # NO crash: worker state untouched, lane stays IDLE
        assert l2.worker["state"] == WORKER_STATE_READY
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        # retry succeeds → timestamp refreshed, error cleared
        result2 = await sw.heartbeat_once()
        assert result2.ok, result2
        l3, _ = ledger_read_shared(g, lp)
        assert l3.worker["last_heartbeat_at"] is not None
        assert sw.last_heartbeat_error is None
    finally:
        await sw.shutdown()


async def test_d2_heartbeat_loop_survives_transient_failures(rtg, monkeypatch):
    """The _hb loop does not break on transient failures: with two injected
    LEDGER_READ_FAILED ticks followed by real ticks, the loop stays alive and
    the ledger heartbeat timestamp ends up refreshed (no crash)."""
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        import mcps.zynq_mcp.control.single_worker as sw_mod
        real_once = sw.heartbeat_once
        state = {"transient_left": 2, "calls": 0}

        async def _flaky():
            state["calls"] += 1
            if state["transient_left"] > 0:
                state["transient_left"] -= 1
                return HeartbeatResultStub(False, "LEDGER_READ_FAILED",
                                           "injected read failure")
            return await real_once()

        # a tiny interval makes the loop tick fast and deterministically
        monkeypatch.setattr(sw_mod, "HEARTBEAT_INTERVAL", 0.02)
        monkeypatch.setattr(sw, "heartbeat_once", _flaky)
        sw._start_heartbeat()
        await asyncio.sleep(0.25)
        # the loop is STILL ALIVE (a pre-fix loop would have broken on the
        # first transient failure)
        assert not sw.heartbeat_task_done
        assert state["calls"] >= 3
        l2, _ = ledger_read_shared(g, lp)
        # the real tick eventually refreshed the ledger timestamp
        assert l2.worker["last_heartbeat_at"] is not None
        assert l2.worker["state"] == WORKER_STATE_READY
        assert l2.execution_lane == EXECUTION_LANE_IDLE
    finally:
        await sw.shutdown()


class HeartbeatResultStub:
    """Minimal ok/reason_code/detail stand-in for the transient tick result."""

    def __init__(self, ok, reason_code=None, detail=None):
        self.ok = ok
        self.reason_code = reason_code
        self.detail = detail
        self.ledger_persisted = False
        self.worker_state = WORKER_STATE_ABSENT


# ═══════════════════════════════════════════════════════════════════════
#  (e) ALIVE+STALE recover_execution revives without close_session
# ═══════════════════════════════════════════════════════════════════════

async def test_e1_recover_revives_alive_stale_without_close_session(rtg):
    """recover_execution on ALIVE+STALE (lane IDLE, stale hb, live process)
    revives the heartbeat: heartbeat_revived=True, timestamp refreshed,
    recovery_log heartbeat_revive appended, process still alive — no
    close_session needed."""
    from mcps.zynq_mcp.dispatcher import ZynqDispatcher
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        _set_stale_hb(g, lp, pid, ident)
        l0, _ = ledger_read_shared(g, lp)
        assert l0.worker["pid"] == pid
        d = ZynqDispatcher(l0, OperationRegistry(), g, lp, sw)
        msgs = await d.dispatch("recover_execution", {}, True)
        data = json.loads(msgs[0].text)
        assert data["status"] == "success", data
        assert data["data"]["heartbeat_revived"] is True
        l2, _ = ledger_read_shared(g, lp)
        # timestamp refreshed to NOW (not 2020)
        from mcps.zynq_mcp.control.execution_gate import _parse_iso
        assert time.time() - _parse_iso(l2.worker["last_heartbeat_at"]) < 30.0
        actions = [r.get("action") for r in l2.recovery_log]
        assert "heartbeat_revive" in actions
        # the process was NOT killed and the lane is still IDLE
        assert is_pid_alive(pid)
        assert l2.execution_lane == EXECUTION_LANE_IDLE
        assert l2.worker["state"] == WORKER_STATE_READY
    finally:
        await sw.shutdown()


async def test_e2_recover_still_refused_with_active_operation(rtg):
    """A live worker with a non-terminal active operation is still refused:
    RECOVERY_BLOCKED_WORKER_ALIVE (the revive path never touches a worker that
    is mid-operation)."""
    from mcps.zynq_mcp.dispatcher import ZynqDispatcher
    sw, a, pid, g, lp, ident = await _start_real_worker(rtg)
    try:
        def _busy(lx):
            lx.worker["state"] = WORKER_STATE_BUSY
            lx.execution_lane = EXECUTION_LANE_BUSY
            lx.active_operation = {
                "operation_id": "op-active", "status": OP_RUNNING,
                "tool_name": "pl_connect_hw_server", "session_id": "s",
                "worker_generation": 1}
            return lx
        ledger_transaction(g, lp, _busy)
        l1, _ = ledger_read_shared(g, lp)
        d = ZynqDispatcher(l1, OperationRegistry(), g, lp, sw)
        msgs = await d.dispatch("recover_execution", {}, True)
        data = json.loads(msgs[0].text)
        assert data["status"] == "error"
        assert data["error"]["details"]["reason_code"] == "RECOVERY_BLOCKED_WORKER_ALIVE"
        # worker untouched
        l2, _ = ledger_read_shared(g, lp)
        assert l2.worker["state"] == WORKER_STATE_BUSY
        assert is_pid_alive(pid)
    finally:
        await sw.shutdown()
