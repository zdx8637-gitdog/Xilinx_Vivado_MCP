"""test_r1_wait_operation.py — wait_operation fail-closed: 6 scenarios at dispatcher level."""
import asyncio, os, shutil, tempfile, threading, time, uuid
from pathlib import Path
import pytest

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, _now_iso,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator
from mcps.zynq_mcp.control.operation_service import op_transition, request_signature
from mcps.zynq_mcp.control.workspace import compute_workspace_id, resolve_workspace_root

REAL = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


class _MockDisp:
    def __init__(self, guard, ledger_path, wsid):
        self._guard = guard; self._ledger_path = ledger_path; self.workspace_id = wsid


@pytest.fixture
def prepped(tmp_path):
    """Create ledger with RUNNING operation. Returns (guard, ledger_path, oid)."""
    rt = tmp_path / ".zynq_w"; rt.mkdir(parents=True); lp = rt / "ledger.json"
    wsid = compute_workspace_id(resolve_workspace_root())
    g = InstanceGuard(rt, wsid); g.determine_role()
    def _i(l):
        l.instance_id=g.instance_id; l.workspace_id=wsid; l.execution_lane=EXECUTION_LANE_IDLE
        l.primary_instance_id=g.instance_id
        l.context={"board_id":"ALINX_AX7020_v1.0","current_stage":"PL_BUILD",
                   "board_package_revision":REAL,"expected_board_revision":REAL}
        return l
    l=ledger_transaction(g,lp,_i)
    sig=request_signature("w","PL_BUILD","pl_synthesize",{},REAL)
    oid=f"op-w-{uuid.uuid4().hex[:8]}"
    mut=preflight_mutator("pl_synthesize",{},"w","ALINX_AX7020_v1.0","p",oid,sig)
    l=ledger_transaction(g,lp,mut); op_transition(g,lp,oid,OP_RUNNING)
    return g, lp, oid, wsid


class TestWaitOperation:
    # --- A: nonexistent → OPERATION_NOT_FOUND ---
    def test_a_nonexistent_immediate(self, prepped):
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)
        d = asyncio.run(_wait_operation({"operation_id":"does-not-exist","timeout_s":30}, disp))
        assert d["status"]=="error", f"Expected error, got: {d}"
        assert d["error"]["code"]=="OPERATION_NOT_FOUND"

    # --- B: terminal → immediate return ---
    def test_b_terminal_immediate(self, prepped):
        g, lp, oid, wsid = prepped
        op_transition(g,lp,oid,OP_SUCCEEDED,result={"ok":True})
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)
        d = asyncio.run(_wait_operation({"operation_id":oid,"timeout_s":30}, disp))
        assert d["status"]=="success"
        assert d["data"]["operation_id"]==oid
        assert d["data"]["status"]==OP_SUCCEEDED

    # --- C: wait timeout preserves the real non-terminal Operation status ---
    def test_c_nonterminal_timeout(self, prepped):
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)
        d = asyncio.run(_wait_operation({"operation_id":oid,"timeout_s":5}, disp))
        assert d["status"]=="success"
        data = d["data"]
        assert data["status"]==OP_RUNNING
        assert data["wait_timed_out"] is True
        assert data["operation_id"]==oid
        assert data["current_status"]==OP_RUNNING
        assert "workflow_stage" in data
        assert data["elapsed_s"]>=0
        assert "poll_after_s" in data

    # --- D: transition during wait → terminal ---
    def test_d_transition_during_wait(self, prepped):
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)

        result = [None]
        async def waiter():
            result[0] = await _wait_operation({"operation_id":oid,"timeout_s":15}, disp)
        async def transitioner():
            await asyncio.sleep(1.0)
            op_transition(g,lp,oid,OP_SUCCEEDED,result={"ok":True})

        async def _run():
            await asyncio.gather(waiter(), transitioner())
        asyncio.run(_run())

        d = result[0]
        assert d["status"]=="success", f"Expected success, got: {d}"
        assert d["data"]["operation_id"]==oid
        assert d["data"]["status"]==OP_SUCCEEDED

    # --- E: Ledger failure → LEDGER_READ_FAILED ---
    def test_e_ledger_read_failure(self, prepped):
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)

        result = [None]
        async def waiter():
            result[0] = await _wait_operation({"operation_id":oid,"timeout_s":15}, disp)
        async def corruptor():
            await asyncio.sleep(0.5)
            lp.write_text("{corrupt",encoding="utf-8")

        async def _run():
            await asyncio.gather(waiter(), corruptor())
        asyncio.run(_run())

        d = result[0]
        assert d["status"]=="error", f"Expected error, got: {d}"
        assert d["error"]["code"]=="INTERNAL_ERROR"
        assert d["error"]["details"]["reason_code"]=="LEDGER_READ_FAILED"

    # --- F: Operation state lost ---
    def test_f_operation_state_lost(self, prepped):
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)

        result = [None]
        async def waiter():
            result[0] = await _wait_operation({"operation_id":oid,"timeout_s":15}, disp)
        async def clearer():
            await asyncio.sleep(0.5)
            def _clear(l):
                l.active_operation=None; l.previous_operation=None; return l
            ledger_transaction(g,lp,_clear)

        async def _run():
            await asyncio.gather(waiter(), clearer())
        asyncio.run(_run())

        d = result[0]
        assert d["status"]=="error", f"Expected error, got: {d}"
        assert d["error"]["details"]["reason_code"]=="OPERATION_STATE_LOST"

    # --- Timeout boundary: op disappears near timeout final read ---
    def test_g_not_found_at_timer_final(self, prepped):
        """Op disappears during wait → poll catches NOT_FOUND before timeout."""
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)

        result = [None]
        async def waiter():
            result[0] = await _wait_operation({"operation_id":oid,"timeout_s":2.0}, disp)
        async def clearer():
            await asyncio.sleep(0.5)
            def _clear(l): l.active_operation=None; l.previous_operation=None; return l
            ledger_transaction(g,lp,_clear)

        async def _run():
            await asyncio.gather(waiter(), clearer())
        asyncio.run(_run())

        d = result[0]
        assert d["status"]=="error", f"Expected error, got: {d}"
        assert d["error"]["details"]["reason_code"]=="OPERATION_STATE_LOST"

    # --- Sanity: wait timeout never fabricates SUCCEEDED or unknown ---
    def test_h_wait_timeout_preserves_real_status(self, prepped):
        """Timed wait must report status/current_status=RUNNING exactly."""
        g, lp, oid, wsid = prepped
        from mcps.zynq_mcp.dispatcher import _wait_operation
        disp = _MockDisp(g, lp, wsid)
        d = asyncio.run(_wait_operation({"operation_id":oid,"timeout_s":5}, disp))
        assert d["status"]=="success"
        assert d["data"]["status"]==OP_RUNNING
        assert d["data"]["wait_timed_out"] is True
        assert d["data"]["current_status"]==OP_RUNNING
        assert d["data"]["current_status"]!="unknown"
        assert d["data"]["current_status"]!="SUCCEEDED"
