"""recovery.py — Atomic recovery. No P1-P6 blocking."""
import time
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT, OP_NON_TERMINAL,
    WORKER_STATE_ABSENT, ChannelBusyError,
)
from mcps.zynq_mcp.control.process_guard import is_pid_alive
from mcps.zynq_mcp.control.resource_registry import resource_public_view


def diagnose_execution(ledger):
    w = ledger.worker or {}; ao = ledger.active_operation or {}; po = ledger.previous_operation or {}
    pid = w.get("pid"); pa = is_pid_alive(pid) if pid and pid > 0 else False
    return {"status": "success", "data": {
        "execution_lane": ledger.execution_lane,
        "worker_process_health": "ALIVE" if pa else ("NOT_RUNNING" if pid else "NONE"),
        "worker_heartbeat_health": "CURRENT" if _hb_current(w) else ("STALE" if w.get("last_heartbeat_at") else "NEVER"),
        "worker_state": w.get("state", WORKER_STATE_ABSENT), "worker_pid": pid,
        "active_operation_id": ao.get("operation_id"),
        "active_operation_status": ao.get("status"),
        "previous_operation_id": po.get("operation_id"),
        "previous_operation_status": po.get("status"),
        "operation_progress_state": "UNKNOWN", "outcome_confidence": "NONE",
        "resources": resource_public_view(w),
        "recommended_action": _recommend(ledger, pa),
    }}


def recovery_mutator(op_id):
    def _mutator(ledger):
        w = ledger.worker or {}; pid = w.get("pid")
        # IDLE → no-op
        if ledger.execution_lane == EXECUTION_LANE_IDLE:
            ledger.recovery_log.append({"action": "recover", "result": "ALREADY_IDLE",
                "timestamp": _now_iso(), "recovery_op_id": op_id})
            return ledger
        # P1: worker alive
        if pid and pid > 0 and is_pid_alive(pid):
            raise ChannelBusyError("RECOVERY_BLOCKED_WORKER_ALIVE")
        # P2-P4: resources
        if w.get("project_lease_held"): raise ChannelBusyError("RECOVERY_BLOCKED_PROJECT_LOCK")
        if w.get("jtag_lease_held"): raise ChannelBusyError("RECOVERY_BLOCKED_JTAG_LOCK")
        if w.get("serial_owner"): raise ChannelBusyError("RECOVERY_BLOCKED_SERIAL")
        # P5: resolve
        ao = ledger.active_operation
        if ao and ao.get("status") in OP_NON_TERMINAL:
            raise ChannelBusyError("RECOVERY_BLOCKED_OPERATION_NON_TERMINAL")
        if ledger.active_operation:
            resolved = dict(ledger.active_operation)
            resolved["resolved_by_recovery"] = True
            resolved["resolved_at"] = _now_iso()
            resolved["recovery_generation"] = w.get("worker_generation", 0) + 1
            ledger.previous_operation = resolved; ledger.active_operation = None
        elif ledger.previous_operation and ledger.previous_operation.get("status") in (
            OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT):
            ledger.previous_operation["resolved_by_recovery"] = True
            ledger.previous_operation["resolved_at"] = _now_iso()
            ledger.previous_operation["recovery_generation"] = w.get("worker_generation", 0) + 1
        # P6-P7: commit
        gen = w.get("worker_generation", 0) + 1
        ledger.execution_lane = EXECUTION_LANE_IDLE
        w["state"] = WORKER_STATE_ABSENT; w["pid"] = None; w["last_heartbeat_at"] = None
        w["worker_generation"] = gen
        w["project_lease_held"] = False; w["jtag_lease_held"] = False; w["serial_owner"] = None
        jtag = w.get("jtag_lease")
        if isinstance(jtag, dict):
            jtag = dict(jtag)
            jtag.update({"status": "ORPHANED", "connected": False,
                         "recovered_at": _now_iso()})
            w["jtag_lease"] = jtag
        uart = w.get("uart_capture")
        if isinstance(uart, dict) and uart.get("status") in (
                "RUNNING", "MATCHED", "PARTIAL", "TIMEOUT"):
            uart = dict(uart)
            uart.update({"status": "INTERRUPTED", "finished_at": _now_iso(),
                         "reason_code": "RECOVERY"})
            w["uart_capture"] = uart
        ledger.worker = w
        ledger.recovery_log.append({"action": "recover", "result": "SUCCEEDED",
            "timestamp": _now_iso(), "worker_generation": gen, "recovery_op_id": op_id})
        return ledger
    return _mutator


def _hb_current(w, timeout=120.0):
    ts = w.get("last_heartbeat_at")
    if not ts: return False
    try: return (time.time() - _parse_iso(ts)) < timeout
    except Exception: return False

def _recommend(ledger, pid_alive):
    if ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED:
        if pid_alive: return "Worker PID alive. Stop manually, then recover_execution."
        return "No worker alive. Run recover_execution."
    if ledger.execution_lane == "BUSY": return "Operation in progress. Poll or wait_operation."
    return "No active operation. Submit a new command."

def _parse_iso(iso):
    import datetime
    try:
        dt = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception: return 0.0
