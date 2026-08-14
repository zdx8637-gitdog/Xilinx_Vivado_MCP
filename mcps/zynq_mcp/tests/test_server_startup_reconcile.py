"""
test_server_startup_reconcile.py — P1-A residual: startup reconciliation.

Root cause (R3): the lane auto-recovery to IDLE on SUCCEEDED worked only within
a single server lifetime. When each Phase ran in a separate script process
(each spawning a fresh MCP server), the old server died before _set_crash could
run, and the new server's start_reconcile() read execution_lane=RECOVERY_REQUIRED
with previous_operation.status=SUCCEEDED and kept the lane stuck in recovery.

Fix: server.start_reconcile() now applies the same P1-A policy as _set_crash —
RECOVERY_REQUIRED + SUCCEEDED previous op -> auto-recover to IDLE + worker ABSENT.

Evidence levels:
- Unit: start_reconcile() is called directly on prepared ledgers (production entry).
- Real MCP: a real server subprocess starts over a ledger left by a "dead" primary
  and is observed via MCP SDK get_execution_state.
"""
import asyncio, os, shutil, subprocess, sys, tempfile
from pathlib import Path
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT, WORKER_STATE_DEAD, WORKER_STATE_ORPHANED,
    OP_SUCCEEDED, OP_FAILED, OP_OUTCOME_UNKNOWN,
)
from mcps.zynq_mcp.server import start_reconcile


async def _call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return __import__("json").loads(r.content[0].text)


def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait(timeout=10)
    return p.pid


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp()); g = InstanceGuard(rt, "ws-test"); g.determine_role()
    yield rt, g
    g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)


class TestStartupReconcileUnit:
    def test_fresh_ledger_stays_idle(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_IDLE
        assert out.worker["state"] == WORKER_STATE_ABSENT

    def test_succeeded_previous_dead_worker_auto_recovers(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        dead = _dead_pid()
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-done", "status": OP_SUCCEEDED,
                "tool_name": "pl_synthesize"}
            l.worker["state"] = WORKER_STATE_DEAD
            l.worker["backend"] = "VIVADO"
            l.worker["pid"] = dead
            l.worker["supervisor_pid"] = dead
            l.worker["supervisor_process_start_time"] = 1.0
            l.worker["supervisor_executable_path"] = "C:/dead/cmd.exe"
            l.worker["worker_generation"] = 3
            l.worker["project_lease_held"] = True
            l.worker["jtag_lease_held"] = True
            l.worker["serial_owner"] = "serial0"
            return l
        ledger_transaction(g, lp, _init)
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_IDLE
        assert out.worker["state"] == WORKER_STATE_ABSENT
        assert out.worker["backend"] == "NONE"
        assert out.worker["pid"] is None
        assert out.worker["supervisor_pid"] is None
        assert out.worker["worker_generation"] == 4
        assert out.worker["project_lease_held"] is False
        assert out.worker["jtag_lease_held"] is False
        assert out.worker["serial_owner"] is None
        # History preserved; no phantom active operation.
        assert out.previous_operation["status"] == OP_SUCCEEDED
        assert out.active_operation is None

    def test_succeeded_previous_alive_pid_is_orphaned(self, rtg):
        # Process truth wins over a terminal Operation record. A live old
        # backend cannot be erased from the Ledger and silently accepted.
        rt, g = rtg; lp = rt / "l.json"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-done", "status": OP_SUCCEEDED}
            l.worker["state"] = WORKER_STATE_ORPHANED
            l.worker["pid"] = os.getpid()
            return l
        ledger_transaction(g, lp, _init)
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        assert out.worker["state"] == WORKER_STATE_ORPHANED
        assert out.worker["pid"] == os.getpid()
        assert out.recent_errors[-1]["reason_code"] == \
            "BACKEND_IDENTITY_MISMATCH"

    def test_backend_record_without_pid_fails_closed(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.primary_instance_id = "dead-primary"
            l.worker["backend"] = "VIVADO"
            l.worker["state"] = "READY"
            l.worker["pid"] = None
            return l
        ledger_transaction(g, lp, _init)
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        assert out.worker["state"] == WORKER_STATE_ORPHANED
        assert out.recent_errors[-1]["reason_code"] == \
            "BACKEND_IDENTITY_MISSING"

    def test_outcome_unknown_previous_keeps_recovery(self, rtg):
        rt, g = rtg; lp = rt / "l.json"
        dead = _dead_pid()
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-unknown", "status": OP_OUTCOME_UNKNOWN}
            l.worker["state"] = WORKER_STATE_DEAD
            l.worker["pid"] = dead
            return l
        ledger_transaction(g, lp, _init)
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        assert out.worker["state"] == WORKER_STATE_DEAD
        assert out.previous_operation["status"] == OP_OUTCOME_UNKNOWN

    def test_failed_previous_keeps_recovery(self, rtg):
        # Only SUCCEEDED auto-recovers; a FAILED op still warrants inspection.
        rt, g = rtg; lp = rt / "l.json"
        dead = _dead_pid()
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-test"
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-failed", "status": OP_FAILED}
            l.worker["state"] = WORKER_STATE_DEAD
            l.worker["pid"] = dead
            return l
        ledger_transaction(g, lp, _init)
        out = start_reconcile(g, lp, "ws-test")
        assert out.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED


class TestStartupReconcileAcrossRestart:
    def test_succeeded_previous_recovers_across_server_restart(self, tmp_path):
        """Real MCP: a ledger left RECOVERY_REQUIRED + SUCCEEDED by a dead
        primary must auto-recover to IDLE when a fresh server process starts."""
        rt = tmp_path / ".zynq_reconcile"; rt.mkdir(parents=True)
        wsid = compute_workspace_id(resolve_workspace_root())
        # Phase 0: simulate the residual ledger a dead primary leaves behind.
        g = InstanceGuard(rt, wsid); g.determine_role()
        lp = rt / "execution_ledger.json"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = wsid
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-syn", "status": OP_SUCCEEDED,
                "tool_name": "pl_synthesize"}
            l.worker["state"] = WORKER_STATE_DEAD
            l.worker["pid"] = _dead_pid()
            l.worker["worker_generation"] = 1
            return l
        ledger_transaction(g, lp, _init)
        g.release_owner_lock()

        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "mcps.zynq_mcp.server"], env=os.environ)
        try:
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        d = await _call(s, "get_execution_state")
                        assert d["status"] == "success", f"Failed: {d}"
                        assert d["data"]["instance_role"] == "primary"
                        assert d["data"]["execution_lane"] == "IDLE", \
                            f"Startup reconcile did not auto-recover: {d['data']['execution_lane']}"
                        assert d["data"]["worker_state"] == "ABSENT"
                        assert d["data"]["worker_pid"] is None
                        assert d["data"]["previous_operation_status"] == "SUCCEEDED"
                        assert d["data"]["active_operation"] is None
            asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
            shutil.rmtree(str(rt), ignore_errors=True)

    def test_outcome_unknown_stays_recovery_across_restart(self, tmp_path):
        """Real MCP: a non-SUCCEEDED previous op must keep RECOVERY_REQUIRED."""
        rt = tmp_path / ".zynq_reconcile_unknown"; rt.mkdir(parents=True)
        wsid = compute_workspace_id(resolve_workspace_root())
        g = InstanceGuard(rt, wsid); g.determine_role()
        lp = rt / "execution_ledger.json"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = wsid
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.primary_instance_id = "dead-primary"
            l.previous_operation = {"operation_id": "op-unknown",
                "status": OP_OUTCOME_UNKNOWN, "tool_name": "pl_synthesize"}
            l.worker["state"] = WORKER_STATE_DEAD
            l.worker["pid"] = _dead_pid()
            return l
        ledger_transaction(g, lp, _init)
        g.release_owner_lock()

        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        params = StdioServerParameters(command=sys.executable,
            args=["-m", "mcps.zynq_mcp.server"], env=os.environ)
        try:
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        d = await _call(s, "get_execution_state")
                        assert d["status"] == "success", f"Failed: {d}"
                        assert d["data"]["execution_lane"] == "RECOVERY_REQUIRED", \
                            f"Non-SUCCEEDED op must keep recovery: {d['data']['execution_lane']}"
                        assert d["data"]["previous_operation_status"] == "OUTCOME_UNKNOWN"
            asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
            shutil.rmtree(str(rt), ignore_errors=True)
