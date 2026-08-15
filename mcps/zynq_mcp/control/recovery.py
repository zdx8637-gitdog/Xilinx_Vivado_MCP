"""recovery.py — Atomic recovery. No P1-P6 blocking."""
import time
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, _now_iso,
    BACKEND_NONE,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT, OP_NON_TERMINAL,
    WORKER_STATE_ABSENT, WORKER_STATE_DEAD, ChannelBusyError,
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


def _owner_residue_present(w) -> bool:
    """True when the worker record still carries owner/instance identity.

    The controller gate (tool_process_controller._ensure_backend /
    _commit_started) treats ``backend`` outside (None, "", BACKEND_NONE) or a
    ``state`` outside (ABSENT, DEAD) as "a worker not owned by this controller"
    and refuses every command with UNOWNED_WORKER_PRESENT.  A crash whose
    shutdown failed leaves exactly those fields behind (``_persist_failure``
    keeps backend/identity/supervisor/instance_id), so recovery must detect
    and erase all of them — not only state/pid.
    """
    return (w.get("backend") not in (None, "", BACKEND_NONE)
            or w.get("state") not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD)
            or w.get("pid") is not None
            or w.get("process_start_time") is not None
            or w.get("executable_path") is not None
            or w.get("instance_id") is not None
            or w.get("supervisor_pid") is not None)


def _clear_owner_residue(w) -> None:
    """Reset owner/instance fields to the ledger's "never had a worker" shape.

    Mirrors the fields written by ``_worker_record``/``_commit_absent`` so the
    recovered record is indistinguishable from a fresh one: backend NONE,
    state ABSENT, no PID, no process identity, no supervisor, no instance
    ownership.  Resource evidence (jtag_lease / uart_capture / serial_owner)
    is intentionally untouched here — the caller decides its fate.
    """
    w["backend"] = BACKEND_NONE
    w["state"] = WORKER_STATE_ABSENT
    w["pid"] = None
    w["process_start_time"] = None
    w["executable_path"] = None
    w["executable_args"] = None
    w["instance_id"] = None
    w["supervisor_pid"] = None
    w["supervisor_process_start_time"] = None
    w["supervisor_executable_path"] = None
    w["last_heartbeat_at"] = None


def recovery_mutator(op_id):
    def _mutator(ledger):
        w = ledger.worker or {}; pid = w.get("pid")
        # IDLE → no-op, unless a crash-recover left owner/instance residue on
        # an already-IDLE lane.  A pre-fix recover set state=ABSENT but kept
        # backend/identity, after which the controller gate refused every
        # command with UNOWNED_WORKER_PRESENT and — because the lane was IDLE —
        # even a repeat recover_execution was a no-op.  Heal that residue when
        # no worker process is alive; a live idle worker is a normal steady
        # state and is never touched.
        if ledger.execution_lane == EXECUTION_LANE_IDLE:
            if pid and pid > 0 and is_pid_alive(pid):
                ledger.recovery_log.append({"action": "recover",
                    "result": "ALREADY_IDLE", "timestamp": _now_iso(),
                    "recovery_op_id": op_id})
                return ledger
            if _owner_residue_present(w):
                _clear_owner_residue(w)
                ledger.recovery_log.append({"action": "recover",
                    "result": "RESIDUE_CLEARED", "timestamp": _now_iso(),
                    "worker_generation": w.get("worker_generation", 0),
                    "recovery_op_id": op_id})
            else:
                ledger.recovery_log.append({"action": "recover",
                    "result": "ALREADY_IDLE", "timestamp": _now_iso(),
                    "recovery_op_id": op_id})
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
        _clear_owner_residue(w)
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
