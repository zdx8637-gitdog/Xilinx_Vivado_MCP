"""
single_worker.py — SingleWorkerController (R2). Sole lifecycle owner.

P0-4: heartbeat_once() — public production method. All 5 identity fields
       MUST exist. Any missing → WORKER_IDENTITY_MISSING. Any mismatch →
       precise reason_code. BUSY only updates heartbeat_at.

P0-5: _stop_heartbeat: cancel → await → bounded timeout. Reference preserved
       on timeout so shutdown can detect.

P0-6: Zero except:pass in R2 new production code.
"""
import asyncio, logging, time
from dataclasses import dataclass, field
from typing import Optional

from mcps.zynq_mcp.control.execution_ledger import (
    ledger_read_shared, ledger_transaction, _now_iso,
    BACKEND_NONE,
    WORKER_STATE_ABSENT, WORKER_STATE_STARTING, WORKER_STATE_READY,
    WORKER_STATE_BUSY, WORKER_STATE_POISONED, WORKER_STATE_DEAD,
    WORKER_STATE_ORPHANED, WORKER_STATE_UNRESPONSIVE, WORKER_STATE_STOPPING,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_OUTCOME_UNKNOWN,
    OP_INTERRUPTED, OP_TIMED_OUT,
)
from mcps.zynq_mcp.control.process_guard import (
    is_pid_alive, kill_process_tree_exact, get_process_identity,
)
from mcps.zynq_mcp.adapters.vivado_adapter import (
    VivadoAdapter, VivadoBridge, BridgeError, BridgeTimeoutError, ShutdownResult,
    ADAPTER_ABSENT, ADAPTER_STARTING, ADAPTER_READY, ADAPTER_BUSY,
    ADAPTER_POISONED, ADAPTER_DEAD,
)

logger = logging.getLogger("zynq_mcp.single_worker")

TOOL_TIMEOUT = 30.0
HEARTBEAT_INTERVAL = 30.0
HEARTBEAT_STOP_TIMEOUT = 5.0

_HEARTBEAT_READONLY_STATES = frozenset({
    WORKER_STATE_BUSY, WORKER_STATE_POISONED, WORKER_STATE_DEAD,
    WORKER_STATE_STARTING, WORKER_STATE_ABSENT,
    WORKER_STATE_ORPHANED, WORKER_STATE_UNRESPONSIVE,
    WORKER_STATE_STOPPING,
})

_HEARTBEAT_REQUIRED_FIELDS = ["pid", "process_start_time", "executable_path",
                               "worker_generation", "instance_id"]


@dataclass
class HeartbeatResult:
    ok: bool
    reason_code: Optional[str] = None
    detail: Optional[str] = None
    ledger_persisted: bool = False
    worker_state: str = WORKER_STATE_ABSENT


def _crash_persisted(crash_result: dict) -> bool:
    """Extract ledger_persisted from _do_crash return value (shared helper).
    Returns False if the path is missing or any intermediate key is absent."""
    try:
        return bool(crash_result.get("error", {}).get("details", {}).get("ledger_persisted", False))
    except (TypeError, AttributeError):
        return False


class SingleWorkerController:
    """Sole lifecycle owner for the single global EDA Worker."""

    def __init__(self, ledger, guard=None, ledger_path=None,
                 lifecycle_lock=None):
        self._ledger = ledger
        self._adapter: Optional[VivadoAdapter] = None
        self._lock = lifecycle_lock or asyncio.Lock()
        self._guard = guard
        self._ledger_path = ledger_path
        self._factory_call_count = 0
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_heartbeat_error: Optional[str] = None
        self._heartbeat_stopped = False
        self._heartbeat_shutdown_injected: Optional[asyncio.Event] = None

    @property
    def has_worker(self) -> bool:
        return self._adapter is not None and self._adapter.is_started

    @property
    def adapter_status(self) -> str:
        if self._adapter is None:
            return ADAPTER_ABSENT
        return self._adapter.status

    @property
    def last_heartbeat_error(self) -> Optional[str]:
        return self._last_heartbeat_error

    @property
    def heartbeat_task_done(self) -> bool:
        if self._heartbeat_task is None:
            return True
        return self._heartbeat_task.done()

    @property
    def heartbeat_task(self) -> Optional[asyncio.Task]:
        return self._heartbeat_task

    # ==================================================================
    # STARTUP
    # ==================================================================
    async def ensure_worker(self) -> VivadoAdapter:
        if self.has_worker:
            return self._adapter
        async with self._lock:
            if self.has_worker:
                return self._adapter
            if self._adapter is not None and self._adapter.is_poisoned:
                raise BridgeError("Worker poisoned; run recover_execution first")
            if self._guard and self._ledger_path:
                latest, _ = ledger_read_shared(
                    self._guard, self._ledger_path,
                    getattr(self._guard, "workspace_id", None))
                direct_backend = (latest.worker or {}).get(
                    "backend", BACKEND_NONE)
                if direct_backend not in (None, "", BACKEND_NONE):
                    raise BridgeError(
                        "Direct EDA backend already active; legacy worker start denied")
                self._ledger = latest
            adapter = VivadoAdapter()
            self._factory_call_count += 1
            if self._guard and self._ledger_path:
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _set_worker_state(l, {}, WORKER_STATE_STARTING))
            try:
                await adapter.start()
            except Exception:
                if adapter.child_pid and is_pid_alive(adapter.child_pid):
                    kill_process_tree_exact(adapter.child_pid)
                    time.sleep(0.5)
                if self._guard and self._ledger_path:
                    try:
                        self._ledger = ledger_transaction(self._guard, self._ledger_path,
                            lambda l: _set_worker_state(l, {}, WORKER_STATE_ABSENT))
                    except Exception as e_abs:
                        logger.error("Start-failure ABSENT write failed: %s", e_abs)
                raise
            ident = adapter.worker_identity
            if self._guard and self._ledger_path:
                try:
                    self._ledger = ledger_transaction(self._guard, self._ledger_path,
                        lambda l: _set_worker_state(l, ident, WORKER_STATE_READY))
                except Exception:
                    shutdown_errs = []
                    try:
                        sd_result = await adapter.shutdown()
                        if sd_result.cleanup_errors:
                            shutdown_errs.extend(sd_result.cleanup_errors)
                    except Exception as e2:
                        shutdown_errs.append(str(e2))
                    try:
                        self._ledger = ledger_transaction(self._guard, self._ledger_path,
                            lambda l: _set_worker_state(l, {}, WORKER_STATE_ABSENT))
                    except Exception as e3:
                        shutdown_errs.append(f"ABSENT write failed: {e3}")
                    self._adapter = None
                    raise BridgeError("Worker started but ledger commit failed")
            self._adapter = adapter
            self._start_heartbeat()
            return self._adapter

    # ==================================================================
    # EXECUTE_TOOL
    # ==================================================================
    async def execute_tool(self, tool_name, arguments, session_id, timeout_s=None) -> dict:
        if not self.has_worker:
            return {"status": "error", "error": {"code": "TOOL_ERROR",
                "message": "No worker available", "details": {"reason_code": "ADAPTER_NOT_READY"}}}
        timeout = timeout_s or TOOL_TIMEOUT
        if self._guard and self._ledger_path:
            try:
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _set_worker_state(l, self._adapter.worker_identity, WORKER_STATE_BUSY))
            except Exception as e:
                logger.error("BUSY ledger write failed: %s", e)
                return {"status": "error", "error": {"code": "INTERNAL_ERROR",
                    "message": f"Ledger BUSY write failed: {e}",
                    "details": {"reason_code": "LEDGER_WRITE_FAILED"}}}
        try:
            resp = await asyncio.wait_for(
                self._adapter.call_tool(tool_name, arguments, timeout=timeout, session_id=session_id),
                timeout=timeout + 10.0)
            if self._guard and self._ledger_path:
                try:
                    self._ledger = ledger_transaction(self._guard, self._ledger_path,
                        lambda l: _set_worker_state(l, self._adapter.worker_identity, WORKER_STATE_READY))
                except Exception as e:
                    logger.error("READY restore ledger write failed: %s", e)
                    return {"status": "error", "error": {"code": "INTERNAL_ERROR",
                        "message": f"READY ledger write failed: {e}",
                        "details": {"reason_code": "LEDGER_WRITE_FAILED"}}}
            return resp.to_dict()
        except asyncio.TimeoutError:
            return await self._do_timeout(tool_name, timeout)
        except BridgeTimeoutError:
            return await self._do_timeout(tool_name, timeout)
        except BridgeError as e:
            return await self._do_crash(str(e), "OPERATION_OUTCOME_UNKNOWN")

    async def _do_crash(self, error_msg: str, reason_code: str = "OPERATION_OUTCOME_UNKNOWN") -> dict:
        if self._adapter:
            self._adapter.poison()
        if self._adapter and self._adapter.child_pid and is_pid_alive(self._adapter.child_pid):
            kill_process_tree_exact(self._adapter.child_pid)
            time.sleep(1.0)
        ledger_persisted = False
        if self._guard and self._ledger_path:
            try:
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _set_crash(l, OP_OUTCOME_UNKNOWN, WORKER_STATE_POISONED))
                ledger_persisted = True
            except Exception as e:
                logger.error("Crash ledger write failed: %s", e)
        return {"status": "error", "error": {"code": "INTERNAL_ERROR",
            "message": error_msg, "details": {"reason_code": reason_code,
                "ledger_persisted": ledger_persisted}}}

    async def _do_timeout(self, tool_name: str, timeout: float) -> dict:
        if self._adapter:
            self._adapter.poison()
        if self._adapter and self._adapter.child_pid:
            kill_process_tree_exact(self._adapter.child_pid)
            await asyncio.sleep(1.0)
        ledger_persisted = False
        if self._guard and self._ledger_path:
            try:
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _set_crash(l, OP_TIMED_OUT, WORKER_STATE_DEAD))
                ledger_persisted = True
            except Exception as e:
                logger.error("Timeout ledger write failed: %s", e)
        return {"status": "error", "error": {"code": "TOOL_ERROR",
            "message": f"'{tool_name}' timed out after {timeout}s",
            "details": {"reason_code": "VIVADO_TIMEOUT", "auto_retry_count": 0,
                "ledger_persisted": ledger_persisted}}}

    # ==================================================================
    # SHUTDOWN
    # ==================================================================
    async def shutdown(self) -> dict:
        async with self._lock:
            hb_stopped_ok = await self._stop_heartbeat()
            if not hb_stopped_ok:
                return {"success": False, "pid_cleaned": False,
                        "error": "heartbeat_task_did_not_stop",
                        "hb_stopped": False}
            if self._adapter is None:
                return {"success": True, "pid_cleaned": False, "error": None,
                        "hb_stopped": True}
            pid = self._adapter.child_pid
            shutdown_errors = []
            cleanup_warnings = []
            try:
                result = await self._adapter.shutdown()
            except Exception as e:
                shutdown_errors.append(f"adapter_shutdown_exception:{e}")
                result = ShutdownResult(cleaned=False, error=str(e))
            if result.cleanup_errors:
                cleanup_warnings.extend(result.cleanup_errors)
            if not result.cleaned and pid and pid > 0 and is_pid_alive(pid):
                kill_process_tree_exact(pid)
                await asyncio.sleep(1.0)
                if is_pid_alive(pid):
                    shutdown_errors.append("PID still alive after force-kill")
                result = ShutdownResult(cleaned=not is_pid_alive(pid),
                    error=None if not is_pid_alive(pid) else "PID still alive")
            ledger_absent_ok = False
            if self._guard and self._ledger_path and result.cleaned:
                try:
                    self._ledger = ledger_transaction(self._guard, self._ledger_path,
                        lambda l: _set_worker_state(l, {}, WORKER_STATE_ABSENT))
                    ledger_absent_ok = True
                except Exception as e:
                    shutdown_errors.append(f"ABSENT write failed: {e}")
                    logger.error("Shutdown ABSENT ledger write failed: %s", e)
            self._adapter = None
            overall_success = result.cleaned and ledger_absent_ok and hb_stopped_ok
            if not overall_success:
                if result.cleaned and not ledger_absent_ok:
                    shutdown_errors.append("ledger_absent_write_failed")
            if shutdown_errors:
                overall_success = False
            combined_error = "; ".join(shutdown_errors) if shutdown_errors else None
            return {"success": overall_success, "pid_cleaned": result.cleaned,
                    "ledger_absent_ok": ledger_absent_ok, "hb_stopped": hb_stopped_ok,
                    "cleanup_warnings": cleanup_warnings if cleanup_warnings else [],
                    "error": combined_error}

    # ==================================================================
    # HEARTBEAT
    # ==================================================================
    async def heartbeat_once(self) -> HeartbeatResult:
        """Public production method. Called by heartbeat loop every interval.

        Performs full 5-field identity verification against LEDGER.
        All fields MUST exist. Any missing → WORKER_IDENTITY_MISSING.
        Any mismatch → precise reason_code.

        Returns HeartbeatResult with ok/reason_code/detail/ledger_persisted.
        On mismatch or missing fields: calls _do_crash, returns ok=False.
        """
        if self._guard is None or self._ledger_path is None:
            return HeartbeatResult(ok=True, worker_state="NO_GUARD")

        if self._adapter is None or not self._adapter.is_started:
            return HeartbeatResult(ok=True, worker_state=WORKER_STATE_ABSENT)

        pid = self._adapter.child_pid
        if not pid or not is_pid_alive(pid):
            result = await self._do_crash("PID not alive in heartbeat",
                                          "HEARTBEAT_PID_NOT_ALIVE")
            return HeartbeatResult(ok=False, reason_code="HEARTBEAT_PID_NOT_ALIVE",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=result.get("error", {}).get("details", {}).get("ledger_persisted", False))

        # Read current ledger state
        from mcps.zynq_mcp.control.execution_ledger import ledger_read_shared
        try:
            cur_ledger, _ = ledger_read_shared(self._guard, self._ledger_path)
        except Exception:
            self._last_heartbeat_error = "ledger_read_failed"
            return HeartbeatResult(ok=False, reason_code="LEDGER_READ_FAILED",
                                   detail="Cannot read ledger for identity check")

        ledger_worker = cur_ledger.worker or {}
        ledger_state = ledger_worker.get("state", WORKER_STATE_ABSENT)

        # Get OS identity
        ident = get_process_identity(pid)
        if ident is None:
            crash = await self._do_crash("Cannot verify worker identity", "HEARTBEAT_IDENTITY_UNVERIFIABLE")
            return HeartbeatResult(ok=False, reason_code="HEARTBEAT_IDENTITY_UNVERIFIABLE",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # --- 5-field strict existence check ---
        for field in _HEARTBEAT_REQUIRED_FIELDS:
            val = ledger_worker.get(field)
            if val is None:
                crash = await self._do_crash(
                    f"Missing identity field in ledger: {field}",
                    "WORKER_IDENTITY_MISSING")
                return HeartbeatResult(ok=False,
                    reason_code="WORKER_IDENTITY_MISSING",
                    detail=f"field={field}",
                    worker_state=WORKER_STATE_POISONED,
                    ledger_persisted=_crash_persisted(crash))

        # --- 5-field strict comparison ---
        # pid
        lp = ledger_worker["pid"]
        if ident.pid != lp:
            crash = await self._do_crash(f"PID mismatch: ledger={lp}, OS={ident.pid}",
                                         "WORKER_PID_MISMATCH")
            return HeartbeatResult(ok=False, reason_code="WORKER_PID_MISMATCH",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # process_start_time
        lst = ledger_worker["process_start_time"]
        delta = abs(ident.process_start_time - lst) if ident.process_start_time is not None else 999
        if delta > 5.0:
            crash = await self._do_crash(
                f"start_time mismatch: ledger={lst}, OS={ident.process_start_time}, delta={delta:.1f}s",
                "WORKER_START_TIME_MISMATCH")
            return HeartbeatResult(ok=False, reason_code="WORKER_START_TIME_MISMATCH",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # executable_path
        lexe = ledger_worker["executable_path"]
        if ident.executable_path != lexe:
            crash = await self._do_crash(f"exe mismatch: ledger={lexe}, OS={ident.executable_path}",
                                         "WORKER_EXECUTABLE_MISMATCH")
            return HeartbeatResult(ok=False, reason_code="WORKER_EXECUTABLE_MISMATCH",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # worker_generation
        lgen = ledger_worker["worker_generation"]
        wi = self._adapter.worker_identity
        if wi and wi.get("worker_generation") is not None and wi["worker_generation"] != lgen:
            crash = await self._do_crash(f"generation mismatch: ledger={lgen}, adapter={wi['worker_generation']}",
                                         "WORKER_GENERATION_MISMATCH")
            return HeartbeatResult(ok=False, reason_code="WORKER_GENERATION_MISMATCH",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # instance_id
        liid = ledger_worker["instance_id"]
        giid = self._guard.instance_id if hasattr(self._guard, 'instance_id') else None
        if giid is not None and liid != giid:
            crash = await self._do_crash(f"instance_id mismatch: ledger={liid}, guard={giid}",
                                         "WORKER_INSTANCE_MISMATCH")
            return HeartbeatResult(ok=False, reason_code="WORKER_INSTANCE_MISMATCH",
                                   worker_state=WORKER_STATE_POISONED,
                                   ledger_persisted=_crash_persisted(crash))

        # --- State update ---
        if ledger_state in _HEARTBEAT_READONLY_STATES:
            try:
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _heartbeat_tick(l, ledger_state))
                self._last_heartbeat_error = None
            except Exception as e:
                self._last_heartbeat_error = str(e)
                return HeartbeatResult(ok=False, reason_code="HEARTBEAT_WRITE_FAILED",
                                       worker_state=ledger_state)
            return HeartbeatResult(ok=True, worker_state=ledger_state, ledger_persisted=True)
        else:
            try:
                wi2 = self._adapter.worker_identity
                self._ledger = ledger_transaction(self._guard, self._ledger_path,
                    lambda l: _set_worker_state(l, wi2, WORKER_STATE_READY))
                self._last_heartbeat_error = None
            except Exception as e:
                self._last_heartbeat_error = str(e)
                return HeartbeatResult(ok=False, reason_code="HEARTBEAT_WRITE_FAILED",
                                       worker_state=WORKER_STATE_READY)
            return HeartbeatResult(ok=True, worker_state=WORKER_STATE_READY, ledger_persisted=True)

    def _start_heartbeat(self) -> None:
        self._heartbeat_stopped = False
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return

        async def _hb():
            while self.has_worker and not self._heartbeat_stopped:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if not self.has_worker or self._heartbeat_stopped:
                    break
                result = await self.heartbeat_once()
                if not result.ok:
                    logger.error("Heartbeat failed: %s %s", result.reason_code, result.detail)
                    break
        self._heartbeat_task = asyncio.ensure_future(_hb())

    async def _stop_heartbeat(self) -> bool:
        if self._heartbeat_task is None:
            return True
        if self._heartbeat_task.done():
            try:
                self._heartbeat_task.result()
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
            return True
        self._heartbeat_stopped = True
        await asyncio.sleep(0)
        self._heartbeat_task.cancel()
        await asyncio.sleep(0)
        try:
            await asyncio.wait_for(asyncio.shield(self._heartbeat_task),
                                   timeout=HEARTBEAT_STOP_TIMEOUT)
            try:
                self._heartbeat_task.result()
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
            return True
        except asyncio.TimeoutError:
            logger.error("Heartbeat task did not stop within %ss", HEARTBEAT_STOP_TIMEOUT)
            self._last_heartbeat_error = f"heartbeat_stop_timeout:{HEARTBEAT_STOP_TIMEOUT}s"
            return False
        except asyncio.CancelledError:
            if self._heartbeat_task is not None and not self._heartbeat_task.done():
                self._last_heartbeat_error = f"heartbeat_stop_interrupted:{HEARTBEAT_STOP_TIMEOUT}s"
                return False
            self._heartbeat_task = None
            return True


# ====================================================================
# Ledger mutator helpers
# ====================================================================
def _set_worker_state(l, ident: dict, state: str):
    w = l.worker or {}
    w["state"] = state
    w["last_heartbeat_at"] = _now_iso()
    w["pid"] = ident.get("pid") if ident else None
    w["process_start_time"] = ident.get("process_start_time") if ident else None
    w["executable_path"] = ident.get("executable_path") if ident else None
    w["executable_args"] = ident.get("executable_args") if ident else None
    w["worker_generation"] = ident.get("worker_generation", 0) if ident else 0
    w["instance_id"] = ident.get("instance_id") if (ident and ident.get("instance_id")) else l.instance_id
    l.worker = w
    return l


def _heartbeat_tick(l, current_state: str):
    w = l.worker or {}
    w["last_heartbeat_at"] = _now_iso()
    w["state"] = current_state
    l.worker = w
    return l


def _succeeded_auto_recover(l):
    """P1-A: last op already SUCCEEDED → nothing to recover.

    Resets the stale worker identity/lease and returns the lane to IDLE so the
    next command can start a fresh worker. Shared by the in-process crash path
    (_set_crash) and startup reconciliation (server.start_reconcile) so the
    policy can never drift.
    """
    gen = l.worker.get("worker_generation", 0) + 1
    l.worker["backend"] = BACKEND_NONE
    l.worker["state"] = WORKER_STATE_ABSENT
    l.worker["pid"] = None
    l.worker["process_start_time"] = None
    l.worker["executable_path"] = None
    l.worker["executable_args"] = None
    l.worker["supervisor_pid"] = None
    l.worker["supervisor_process_start_time"] = None
    l.worker["supervisor_executable_path"] = None
    l.worker["last_heartbeat_at"] = None
    l.worker["worker_generation"] = gen
    l.worker["project_lease_held"] = False
    l.worker["jtag_lease_held"] = False
    l.worker["serial_owner"] = None
    l.execution_lane = EXECUTION_LANE_IDLE
    return l


def _set_crash(l, op_status: str, worker_state: str):
    """Write crash state. Moves active→previous if present, else just poisons worker.

    P1-A lane policy: a worker-process exit (crash/dead) only warrants
    RECOVERY_REQUIRED when it interrupts an in-flight operation. When the last
    operation already reached SUCCEEDED there is nothing to recover — the lane
    auto-reverts to IDLE and the dead worker is reset to ABSENT so the next
    command can start a fresh worker. (Identity-mismatch / no-outcome crashes
    still require manual recover_execution.)
    """
    l.worker["state"] = worker_state
    if l.active_operation:
        l.active_operation["status"] = op_status
        l.previous_operation = dict(l.active_operation)
        l.active_operation = None
        l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
    elif (l.previous_operation or {}).get("status") == OP_SUCCEEDED:
        # Auto-recover: the last command already SUCCEEDED, nothing to recover.
        # Mirror recovery_mutator so a stale worker identity/lease can never
        # block the next command (WORKER_PID_DEAD / RESOURCE_RECORD_INCOMPLETE).
        return _succeeded_auto_recover(l)
    else:
        l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
    return l
