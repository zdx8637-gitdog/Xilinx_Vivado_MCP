"""
errors.py -- WorkerExecutor: production worker lifecycle + command submission.
Background tasks tracked in WorkerEntry, cancelled on close_session.
Uses B02 command_accepted() for submit_command responses.
"""
import asyncio
from mcps.pl_mcp.vivado_bridge import BridgeOwner, BridgeError
from mcps.pl_mcp.worker_registry import (
    get_registry, MaxWorkersError, WorkerBusyError, WorkerNotFoundError, ReservationError,
)

def _worker_busy(sid: str) -> dict:
    from mcps.common.tool_response import error
    return error(message=f"Worker busy: {sid}", code="LOCK_BUSY", recoverable=True,
                 details={"reason_code":"WORKER_BUSY"}).to_dict()
def _max_workers_exceeded(n: int) -> dict:
    from mcps.common.tool_response import error
    return error(message=f"Max workers ({n}) exceeded", code="LOCK_BUSY", recoverable=True,
                 details={"reason_code":"MAX_WORKERS_EXCEEDED"}).to_dict()
def _server_not_found(path: str) -> dict:
    from mcps.common.tool_response import error
    return error(message=f"Server not found: {path}", code="ENV_ERROR",
                 details={"reason_code":"VIVADO_MCP_SERVER_NOT_FOUND"}).to_dict()
def _session_recovery_required() -> dict:
    from mcps.common.tool_response import error
    return error(message="Worker crashed; create new session.", code="TOOL_ERROR",
                 details={"reason_code":"SESSION_RECOVERY_REQUIRED"}).to_dict()
def _device_reselect_required() -> dict:
    from mcps.common.tool_response import error
    return error(message="Worker crashed; re-select device.", code="JTAG_ERROR",
                 details={"reason_code":"DEVICE_RESELECT_REQUIRED"}).to_dict()
def _vivado_timeout() -> dict:
    from mcps.common.tool_response import error
    return error(message="Vivado timed out", code="TOOL_ERROR",
                 details={"reason_code":"VIVADO_TIMEOUT"}).to_dict()
def _vivado_process_dead() -> dict:
    from mcps.common.tool_response import error
    return error(message="Worker process died", code="ENV_ERROR",
                 details={"reason_code":"VIVADO_PROCESS_DEAD"}).to_dict()
def _worker_cleanup_failed(reason: str) -> dict:
    from mcps.common.tool_response import error
    return error(message=f"Worker cleanup failed: {reason}", code="TOOL_ERROR",
                 recoverable=True, details={"reason_code":"WORKER_CLEANUP_FAILED"}).to_dict()
def _operation_outcome_unknown() -> dict:
    from mcps.common.tool_response import error
    return error(message="Command outcome unknown after worker failure", code="INTERNAL_ERROR",
                 details={"reason_code":"OPERATION_OUTCOME_UNKNOWN"}).to_dict()

worker_busy=_worker_busy; max_workers_exceeded=_max_workers_exceeded
server_not_found=_server_not_found; session_recovery_required=_session_recovery_required
device_reselect_required=_device_reselect_required; vivado_timeout=_vivado_timeout
vivado_process_dead=_vivado_process_dead; worker_cleanup_failed=_worker_cleanup_failed
operation_outcome_unknown=_operation_outcome_unknown

def _default_owner_factory(): return BridgeOwner()

class WorkerExecutor:
    def __init__(self, registry=None, owner_factory=None):
        self._reg = registry or get_registry()
        self._owner_factory = owner_factory or _default_owner_factory

    async def start_or_get_worker(self, session_id: str) -> dict:
        existing = self._reg.get_worker(session_id)
        if existing is not None:
            from mcps.common.tool_response import success
            return success({"session_id":session_id,"worker":"existing"}).to_dict()
        try: self._reg.reserve_slot(session_id)
        except MaxWorkersError: return _max_workers_exceeded(self._reg.max_workers)
        except ReservationError: return _worker_busy(session_id)
        owner = None
        try:
            try: owner = self._owner_factory()
            except FileNotFoundError as e:
                self._reg.release_reservation(session_id); return _server_not_found(str(e))
            except Exception:
                self._reg.release_reservation(session_id); return _vivado_process_dead()
            try: await owner.start()
            except FileNotFoundError as e:
                self._reg.release_reservation(session_id); return _server_not_found(str(e))
            except Exception:
                self._reg.release_reservation(session_id); return _vivado_process_dead()
            pid = getattr(owner, 'child_pid', None)
            try: self._reg.commit_reservation(session_id, owner, pid=pid)
            except ReservationError:
                await owner.shutdown(); self._reg.release_reservation(session_id)
                return _session_recovery_required()
            from mcps.common.tool_response import success
            return success({"session_id":session_id,"worker":"started","pid":pid}).to_dict()
        except Exception:
            if owner is not None:
                try: await owner.shutdown()
                except Exception: pass
            self._reg.release_reservation(session_id); return _vivado_process_dead()

    async def rebuild_worker(self, session_id: str, events: list | None = None) -> dict:
        old_any = self._reg._get_any(session_id)
        if old_any is not None and old_any.owner is not None:
            try:
                result = await old_any.owner.shutdown()
                if events is not None: events.append("old_shutdown")
                if not result.cleaned: return _worker_cleanup_failed(result.error or "unknown")
            except Exception as e: return _worker_cleanup_failed(str(e))
        with self._reg._lock:
            if old_any is not None and old_any.operations:
                self._reg._move_to_tombstone(dict(old_any.operations))
            if old_any is not None: self._reg._workers.pop(session_id, None)
        try: self._reg.reserve_slot(session_id)
        except MaxWorkersError: return _max_workers_exceeded(self._reg.max_workers)
        except ReservationError: return _worker_busy(session_id)
        owner = None
        try:
            owner = self._owner_factory()
            if events is not None: events.append("new_created")
        except FileNotFoundError as e:
            self._reg.release_reservation(session_id); return _server_not_found(str(e))
        except Exception:
            self._reg.release_reservation(session_id); return _vivado_process_dead()
        try:
            await owner.start()
            if events is not None: events.append("new_started")
        except FileNotFoundError as e:
            self._reg.release_reservation(session_id); return _server_not_found(str(e))
        except Exception:
            self._reg.release_reservation(session_id); return _vivado_process_dead()
        pid = getattr(owner, 'child_pid', None)
        try: self._reg.commit_reservation(session_id, owner, pid=pid)
        except ReservationError:
            await owner.shutdown(); self._reg.release_reservation(session_id)
            return _session_recovery_required()
        if events is not None: events.append("new_registered")
        from mcps.common.tool_response import success
        return success({"session_id":session_id,"worker":"rebuilt","pid":pid}).to_dict()

    async def submit_command(self, session_id: str, cmd_fn) -> dict:
        """Create operation → return accepted immediately. Background task tracked."""
        w = self._reg.get_worker(session_id)
        if w is None or w.poisoned: return _session_recovery_required()
        try: self._reg.acquire_in_flight(session_id)
        except WorkerBusyError: return _worker_busy(session_id)
        op = self._reg.create_operation(session_id)
        op_id = op.operation_id

        async def _bg():
            try:
                op.transition("running")
                result = await cmd_fn(w)
                if not op.is_terminal():
                    op.transition("succeeded")
                    if hasattr(result, 'to_dict'): op.result = result.to_dict()
                    elif isinstance(result, dict): op.result = result
                    else: op.result = str(result)
            except BridgeError as e:
                if not op.is_terminal():
                    op.transition("failed")
                    op.error_code="INTERNAL_ERROR"; op.reason_code="OPERATION_OUTCOME_UNKNOWN"
                    op.error_message=str(e)
                self._reg.mark_poisoned(session_id, command_reason=True)
            except Exception as e:
                if not op.is_terminal():
                    op.transition("failed")
                    op.error_code="INTERNAL_ERROR"; op.reason_code="OPERATION_OUTCOME_UNKNOWN"
                    op.error_message=str(e)
                self._reg.mark_poisoned(session_id, command_reason=True)
            finally:
                self._reg.release_in_flight(session_id)
                self._reg.unregister_task(session_id, op_id)

        task = asyncio.ensure_future(_bg())
        self._reg.register_task(session_id, op_id, task)
        from mcps.common.tool_response import command_accepted
        return command_accepted(op_id).to_dict()

    async def execute(self, session_id: str, api_category: str, domain_fn) -> dict:
        async def _attempt():
            w = self._reg.get_worker(session_id)
            if w is None or w.poisoned:
                if api_category in ("query-stateful","set","command"): return _session_recovery_required()
                if api_category == "query-hw": return _device_reselect_required()
                return _vivado_process_dead()
            try: self._reg.acquire_in_flight(session_id)
            except WorkerBusyError: return _worker_busy(session_id)
            try:
                r = await domain_fn(w)
                if hasattr(r, 'to_dict'): return r.to_dict()
                return r
            except BridgeError as e:
                msg = str(e).lower()
                self._reg.mark_poisoned(session_id, command_reason=(api_category=="command"))
                if "timed out" in msg: return _vivado_timeout()
                if api_category == "command": return _operation_outcome_unknown()
                return _vivado_process_dead()
            finally: self._reg.release_in_flight(session_id)

        result = await _attempt()
        if (api_category == "query-stateless" and result.get("status") == "error" and
            result.get("error",{}).get("details",{}).get("reason_code")=="VIVADO_PROCESS_DEAD"):
            rebuild_r = await self.rebuild_worker(session_id)
            if rebuild_r.get("status") != "success": return result
            result = await _attempt()
        return result
