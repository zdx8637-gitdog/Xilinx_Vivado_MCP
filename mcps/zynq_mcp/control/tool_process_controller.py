"""Unified ownership for the one real EDA backend process (O2).

This controller owns direct Vivado/XSCT/XSDB Tcl bridges.  It deliberately
does not execute domain Tcl: O3-O5 migrate domain commands onto this owner.
O2 establishes the invariants those migrations depend on:

* at most one actual EDA backend;
* actual tool PID is distinct from an optional batch/shell supervisor PID;
* five-field identity and generation are persisted in the Execution Ledger;
* backend switches stop and verify the old PID before starting the new one;
* crash/identity ambiguity enters RECOVERY_REQUIRED and never auto-restarts;
* PROCESS observations can be written from a real PID/identity check.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import logging
from dataclasses import dataclass
from typing import Callable, Optional

from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_NONE, BACKEND_VIVADO, BACKEND_XSCT, BACKEND_XSDB,
    STATUS_SOURCE_PROCESS, STATUS_SOURCE_RECOVERY,
    OBS_RUNNING, OBS_UNKNOWN,
    HEALTH_ALIVE, HEALTH_DEAD, HEALTH_IDENTITY_MISMATCH,
    ACTION_WAIT, ACTION_RECOVER,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_NON_TERMINAL, OP_OUTCOME_UNKNOWN, OP_INTERRUPTED,
    WORKER_STATE_ABSENT, WORKER_STATE_READY, WORKER_STATE_BUSY,
    WORKER_STATE_DEAD, WORKER_STATE_POISONED,
    ChannelBusyError,
    ledger_read_shared, ledger_transaction, validate_observation, _now_iso,
)
from mcps.zynq_mcp.control.process_guard import (
    WorkerIdentity, get_process_identity, is_pid_alive,
    kill_process_tree_exact, process_identity_matches,
    resolve_backend_process_identity,
)

logger = logging.getLogger("zynq_mcp.tool_process_controller")

VALID_EDA_BACKENDS = frozenset({BACKEND_VIVADO, BACKEND_XSCT, BACKEND_XSDB})
BACKEND_START_TIMEOUT_S = 320.0
BACKEND_STOP_TIMEOUT_S = 30.0
PID_GONE_TIMEOUT_S = 10.0


class ToolProcessControllerError(Exception):
    def __init__(self, reason_code: str, message: str = ""):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


@dataclass(frozen=True)
class BackendSnapshot:
    backend: str
    state: str
    pid: Optional[int]
    supervisor_pid: Optional[int]
    process_start_time: Optional[float]
    executable_path: Optional[str]
    worker_generation: int
    instance_id: Optional[str]
    observed_at: Optional[str] = None
    reason_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "backend": self.backend, "state": self.state, "pid": self.pid,
            "supervisor_pid": self.supervisor_pid,
            "process_start_time": self.process_start_time,
            "executable_path": self.executable_path,
            "worker_generation": self.worker_generation,
            "instance_id": self.instance_id,
            "observed_at": self.observed_at, "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class BackendShutdownResult:
    success: bool
    backend: str
    pid_cleaned: bool
    supervisor_cleaned: bool
    reason_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success, "backend": self.backend,
            "pid_cleaned": self.pid_cleaned,
            "supervisor_cleaned": self.supervisor_cleaned,
            "reason_code": self.reason_code,
        }


def _default_bridge_factories() -> dict[str, Callable[[], object]]:
    def _vivado():
        from mcps.zynq_mcp.adapters.vivado.vivado_bridge import VivadoTclBridge
        return VivadoTclBridge()

    def _xsct():
        from mcps.zynq_mcp.adapters.xsct.xsct_bridge import XsctBridge
        return XsctBridge()

    def _xsdb():
        from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
        return XsdbBridge()

    return {BACKEND_VIVADO: _vivado, BACKEND_XSCT: _xsct,
            BACKEND_XSDB: _xsdb}


class ToolProcessController:
    """Server-scoped owner for exactly one real EDA Tcl backend."""

    def __init__(self, guard, ledger_path, *, bridge_factories=None,
                 identity_resolver=resolve_backend_process_identity,
                 identity_reader=get_process_identity,
                 pid_alive=is_pid_alive, kill_tree=kill_process_tree_exact,
                 event_sink=None, lifecycle_lock=None):
        self._guard = guard
        self._ledger_path = ledger_path
        self._factories = dict(bridge_factories or _default_bridge_factories())
        self._identity_resolver = identity_resolver
        self._identity_reader = identity_reader
        self._pid_alive = pid_alive
        self._kill_tree = kill_tree
        self._event_sink = event_sink
        # During O3-O5 migration the legacy SingleWorkerController and this
        # direct-tool owner coexist in the server.  They must share this one
        # lifecycle lock so the transition cannot briefly create two EDA
        # process trees.
        self._lock = lifecycle_lock or asyncio.Lock()
        self._backend = BACKEND_NONE
        self._bridge = None
        self._identity: Optional[WorkerIdentity] = None
        self._supervisor_identity: Optional[WorkerIdentity] = None
        self._generation = 0
        self._poisoned = False

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def bridge(self):
        """Internal migration hook for O3-O5; never exposed as a public tool."""
        return self._bridge

    @property
    def has_backend(self) -> bool:
        return (self._backend in VALID_EDA_BACKENDS and self._bridge is not None
                and bool(getattr(self._bridge, "ready", False)))

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    def _event(self, name: str, **details) -> None:
        if self._event_sink is not None:
            self._event_sink(name, dict(details))

    def _read_ledger(self):
        return ledger_read_shared(
            self._guard, self._ledger_path,
            getattr(self._guard, "workspace_id", None))[0]

    @staticmethod
    def _require_authority(ledger, operation_id: Optional[str] = None,
                           synchronous_owner: bool = False) -> None:
        """Authorize lifecycle work without violating atomic admission.

        IDLE is the maintenance/switch path.  After a command has atomically
        admitted, its exact Operation id may start or reuse a backend while
        Lane is BUSY.  A synchronous Set runner may do the same only when BUSY
        has no active command.  No other BUSY caller can create a process.
        """
        ao = ledger.active_operation
        if ledger.execution_lane == EXECUTION_LANE_IDLE:
            if isinstance(ao, dict) and ao.get("status") in OP_NON_TERMINAL:
                raise ToolProcessControllerError(
                    "ACTIVE_OPERATION_PRESENT",
                    "active Operation is inconsistent with IDLE lane")
            return
        if ledger.execution_lane == EXECUTION_LANE_BUSY:
            if isinstance(ao, dict) and ao.get("status") in OP_NON_TERMINAL:
                if isinstance(operation_id, str) and operation_id and \
                        ao.get("operation_id") == operation_id:
                    return
                raise ToolProcessControllerError(
                    "BACKEND_OPERATION_MISMATCH",
                    "BUSY backend lifecycle requires the exact active operation")
            if synchronous_owner and ao is None:
                return
        raise ToolProcessControllerError(
            "BACKEND_SWITCH_REQUIRES_IDLE",
            f"execution lane is {ledger.execution_lane}")

    async def _resolve_identity(self, supervisor_pid: int, backend: str):
        if inspect.iscoroutinefunction(self._identity_resolver):
            return await self._identity_resolver(supervisor_pid, backend)
        # The production resolver walks the OS process tree and may poll for a
        # Windows .bat child; keep that bounded work off the event loop.
        result = await asyncio.to_thread(
            self._identity_resolver, supervisor_pid, backend)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _start_bridge(self, bridge, backend: str, *, workspace: str,
                            hw_server_url: str) -> None:
        if backend == BACKEND_XSCT:
            call = bridge.start(workspace or "")
        elif backend == BACKEND_XSDB:
            # Connection is a domain/resource action in O5.  O2 starts only
            # the shell unless an explicit internal caller supplies a URL.
            call = bridge.start(hw_server_url or "")
        else:
            call = bridge.start()
        await asyncio.wait_for(call, timeout=BACKEND_START_TIMEOUT_S)

    async def ensure_backend(self, backend: str, *, workspace: str = "",
                             hw_server_url: str = "",
                             operation_id: Optional[str] = None,
                             synchronous_owner: bool = False) -> BackendSnapshot:
        """Start/return one backend. Switching is allowed only while IDLE."""
        if backend not in VALID_EDA_BACKENDS:
            raise ToolProcessControllerError("INVALID_BACKEND", str(backend))
        async with self._lock:
            ledger = self._read_ledger()
            if self._poisoned or ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED:
                raise ToolProcessControllerError("BACKEND_RECOVERY_REQUIRED")
            self._require_authority(ledger, operation_id, synchronous_owner)

            if self.has_backend and self._backend == backend:
                return self._verify_current_identity(ledger)

            if self._bridge is not None:
                if ledger.execution_lane != EXECUTION_LANE_IDLE:
                    raise ToolProcessControllerError(
                        "BACKEND_SWITCH_REQUIRES_IDLE",
                        "changing EDA backend is forbidden while BUSY")
                shutdown = await self._shutdown_locked(persist_absent=True,
                                                       interrupted=False)
                if not shutdown.success:
                    raise ToolProcessControllerError(
                        shutdown.reason_code or "BACKEND_CLEANUP_FAILED")
                ledger = self._read_ledger()
                self._require_authority(
                    ledger, operation_id, synchronous_owner)

            worker = ledger.worker or {}
            recorded_backend = worker.get("backend", BACKEND_NONE)
            recorded_state = worker.get("state", WORKER_STATE_ABSENT)
            if recorded_backend not in (None, "", BACKEND_NONE) or \
                    recorded_state not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD):
                raise ToolProcessControllerError(
                    "UNOWNED_WORKER_PRESENT",
                    "Ledger contains a worker not owned by this controller")

            factory = self._factories.get(backend)
            if factory is None:
                raise ToolProcessControllerError("BACKEND_FACTORY_MISSING", backend)
            bridge = factory()
            generation = int(worker.get("worker_generation", 0) or 0) + 1
            self._event("backend_starting", backend=backend, generation=generation)
            try:
                await self._start_bridge(bridge, backend, workspace=workspace,
                                         hw_server_url=hw_server_url)
                supervisor_pid = getattr(bridge, "pid", None)
                if isinstance(supervisor_pid, bool) or not isinstance(supervisor_pid, int) \
                        or supervisor_pid <= 0:
                    raise ToolProcessControllerError("BACKEND_PID_MISSING")
                actual, supervisor = await self._resolve_identity(supervisor_pid, backend)
                if actual is None:
                    raise ToolProcessControllerError(
                        "BACKEND_IDENTITY_UNVERIFIABLE")
                if not bool(getattr(bridge, "ready", False)):
                    raise ToolProcessControllerError("BACKEND_NOT_READY")
                instance_id = getattr(self._guard, "instance_id", None)
                identity = WorkerIdentity(
                    pid=actual.pid,
                    process_start_time=actual.process_start_time,
                    executable_path=actual.executable_path,
                    executable_args=actual.executable_args,
                    worker_generation=generation,
                    instance_id=instance_id,
                )
                record = self._worker_record(backend, identity, supervisor,
                                             WORKER_STATE_READY)
                committed = ledger_transaction(
                    self._guard, self._ledger_path,
                    lambda current: self._commit_started(
                        current, record, operation_id, synchronous_owner))
            except asyncio.CancelledError:
                await self._cleanup_uncommitted_bridge(bridge)
                raise
            except Exception as exc:
                await self._cleanup_uncommitted_bridge(bridge)
                if isinstance(exc, ToolProcessControllerError):
                    raise
                raise ToolProcessControllerError(
                    "BACKEND_START_FAILED", str(exc)) from exc

            self._bridge = bridge
            self._backend = backend
            self._identity = identity
            self._supervisor_identity = supervisor
            self._generation = generation
            self._poisoned = False
            self._event("backend_ready", backend=backend, pid=identity.pid,
                        supervisor_pid=supervisor.pid if supervisor else None,
                        generation=generation,
                        ledger_sequence=committed.ledger_sequence)
            return self._snapshot(WORKER_STATE_READY, observed_at=_now_iso())

    def _commit_started(self, current, record: dict,
                        operation_id: Optional[str],
                        synchronous_owner: bool):
        self._require_authority(current, operation_id, synchronous_owner)
        worker = current.worker or {}
        state = worker.get("state", WORKER_STATE_ABSENT)
        backend = worker.get("backend", BACKEND_NONE)
        if state not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD) or \
                backend not in (None, "", BACKEND_NONE):
            raise ChannelBusyError("UNOWNED_WORKER_PRESENT")
        # UART is an independent resource and may remain active while the one
        # EDA backend changes.  Never erase its owner/capture record when a
        # Vivado/XSCT/XSDB process starts.
        record = copy.deepcopy(record)
        record["serial_owner"] = copy.deepcopy(worker.get("serial_owner"))
        record["uart_capture"] = copy.deepcopy(worker.get("uart_capture"))
        current.worker = record
        if isinstance(current.active_operation, dict) and operation_id is not None:
            current.active_operation["worker_generation"] = record[
                "worker_generation"]
        return current

    def _worker_record(self, backend: str, identity: WorkerIdentity,
                       supervisor: Optional[WorkerIdentity], state: str) -> dict:
        now = _now_iso()
        return {
            "backend": backend, "state": state,
            "pid": identity.pid,
            "process_start_time": identity.process_start_time,
            "executable_path": identity.executable_path,
            "executable_args": identity.executable_args,
            "worker_generation": identity.worker_generation,
            "instance_id": identity.instance_id,
            "supervisor_pid": supervisor.pid if supervisor else None,
            "supervisor_process_start_time": (
                supervisor.process_start_time if supervisor else None),
            "supervisor_executable_path": (
                supervisor.executable_path if supervisor else None),
            "last_heartbeat_at": now,
            "project_lease_held": False,
            "jtag_lease_held": False,
            "jtag_lease": None,
            "serial_owner": None, "uart_capture": None,
        }

    def _verify_current_identity(self, ledger) -> BackendSnapshot:
        worker = ledger.worker or {}
        actual = self._identity_reader(self._identity.pid) if self._identity else None
        identity_ok = (
            process_identity_matches(actual, self._identity)
            and process_identity_matches(actual, worker)
            and worker.get("worker_generation") == self._generation
            and worker.get("instance_id") == getattr(self._guard, "instance_id", None)
        )
        if not identity_ok:
            self._persist_failure(
                "BACKEND_IDENTITY_MISMATCH", HEALTH_IDENTITY_MISMATCH, None)
            raise ToolProcessControllerError("BACKEND_IDENTITY_MISMATCH")
        if worker.get("backend") != self._backend:
            self._persist_failure(
                "BACKEND_RECORD_MISMATCH", HEALTH_IDENTITY_MISMATCH, None)
            raise ToolProcessControllerError("BACKEND_RECORD_MISMATCH")
        if self._supervisor_identity is not None:
            supervisor_actual = self._identity_reader(self._supervisor_identity.pid)
            supervisor_record = {
                "pid": worker.get("supervisor_pid"),
                "process_start_time": worker.get("supervisor_process_start_time"),
                "executable_path": worker.get("supervisor_executable_path"),
            }
            if not process_identity_matches(
                    supervisor_actual, self._supervisor_identity) or not \
                    process_identity_matches(supervisor_actual, supervisor_record):
                self._persist_failure(
                    "SUPERVISOR_IDENTITY_MISMATCH",
                    HEALTH_IDENTITY_MISMATCH, None)
                raise ToolProcessControllerError("SUPERVISOR_IDENTITY_MISMATCH")
        return self._snapshot(worker.get("state", WORKER_STATE_READY),
                              observed_at=worker.get("last_heartbeat_at"))

    async def observe_backend(self, *, operation_id: Optional[str] = None,
                              current_step: str = "BACKEND_READY") -> dict:
        """Verify the real process and atomically publish PROCESS evidence."""
        async with self._lock:
            if self._bridge is None or self._identity is None or \
                    self._backend not in VALID_EDA_BACKENDS:
                return self._error("BACKEND_NOT_ACTIVE")
            if not bool(getattr(self._bridge, "ready", False)):
                return self._persist_failure(
                    "BACKEND_PROCESS_DEAD", HEALTH_DEAD, operation_id)

            ledger = self._read_ledger()
            worker = ledger.worker or {}
            actual = self._identity_reader(self._identity.pid)
            if actual is None:
                return self._persist_failure(
                    "BACKEND_PROCESS_DEAD", HEALTH_DEAD, operation_id)
            if not process_identity_matches(actual, worker):
                return self._persist_failure(
                    "BACKEND_IDENTITY_MISMATCH", HEALTH_IDENTITY_MISMATCH,
                    operation_id)
            if worker.get("backend") != self._backend or \
                    worker.get("worker_generation") != self._generation or \
                    worker.get("instance_id") != getattr(self._guard, "instance_id", None):
                return self._persist_failure(
                    "BACKEND_RECORD_MISMATCH", HEALTH_IDENTITY_MISMATCH,
                    operation_id)
            if self._supervisor_identity is not None:
                sup_actual = self._identity_reader(self._supervisor_identity.pid)
                supervisor_record = {
                    "pid": worker.get("supervisor_pid"),
                    "process_start_time": worker.get(
                        "supervisor_process_start_time"),
                    "executable_path": worker.get("supervisor_executable_path"),
                }
                if not process_identity_matches(
                        sup_actual, self._supervisor_identity) or not \
                        process_identity_matches(sup_actual, supervisor_record):
                    return self._persist_failure(
                        "SUPERVISOR_IDENTITY_MISMATCH", HEALTH_IDENTITY_MISMATCH,
                        operation_id)

            observed_at = _now_iso()
            observation = {
                "status_source": STATUS_SOURCE_PROCESS,
                "backend": self._backend,
                "observed_state": OBS_RUNNING,
                "vendor_status": None,
                "current_step": current_step,
                "progress_pct": None,
                "worker_health": HEALTH_ALIVE,
                "pid": self._identity.pid,
                "process_start_time": self._identity.process_start_time,
                "executable_path": self._identity.executable_path,
                "worker_generation": self._generation,
                "instance_id": self._identity.instance_id,
                "controller_heartbeat_at": observed_at,
                "observed_at": observed_at,
                "last_output_at": None,
                "detail": {
                    "supervisor_pid": (self._supervisor_identity.pid
                                       if self._supervisor_identity else None),
                },
            }
            validate_observation(observation)

            def _observe(current):
                current_worker = current.worker or {}
                if current_worker.get("pid") != self._identity.pid or \
                        current_worker.get("worker_generation") != self._generation:
                    raise ChannelBusyError("BACKEND_RECORD_MISMATCH")
                current_worker["last_heartbeat_at"] = observed_at
                if current_worker.get("state") != WORKER_STATE_BUSY:
                    current_worker["state"] = WORKER_STATE_READY
                current.worker = current_worker
                if operation_id is not None:
                    ao = current.active_operation
                    if not isinstance(ao, dict) or \
                            ao.get("operation_id") != operation_id or \
                            ao.get("status") not in OP_NON_TERMINAL:
                        raise ChannelBusyError("OPERATION_NOT_ACTIVE")
                    ao["observation"] = copy.deepcopy(observation)
                    ao["recommended_action"] = ACTION_WAIT
                    ao["updated_at"] = observed_at
                    current.active_operation = ao
                return current

            try:
                committed = ledger_transaction(
                    self._guard, self._ledger_path, _observe)
            except Exception as exc:
                return self._error("LEDGER_WRITE_FAILED", str(exc))
            self._event("backend_observed", backend=self._backend,
                        pid=self._identity.pid, operation_id=operation_id)
            return {"status": "success", "data": {
                **self._snapshot(committed.worker.get("state", WORKER_STATE_READY),
                                 observed_at=observed_at).to_dict(),
                "observation": observation,
                "ledger_sequence": committed.ledger_sequence,
            }}

    def _persist_failure(self, reason_code: str, health: str,
                         operation_id: Optional[str]) -> dict:
        observed_at = _now_iso()

        def _fail(current):
            worker = current.worker or {}
            worker["state"] = (WORKER_STATE_DEAD
                               if health == HEALTH_DEAD else WORKER_STATE_POISONED)
            worker["last_heartbeat_at"] = observed_at
            current.worker = worker
            ao = current.active_operation
            if isinstance(ao, dict) and ao.get("status") in OP_NON_TERMINAL and \
                    (operation_id is None or ao.get("operation_id") == operation_id):
                obs = copy.deepcopy(ao.get("observation") or {})
                obs.update({
                    "status_source": STATUS_SOURCE_RECOVERY,
                    "backend": self._backend,
                    "observed_state": OBS_UNKNOWN,
                    "vendor_status": None,
                    "current_step": "BACKEND_IDENTITY_CHECK",
                    "progress_pct": None,
                    "worker_health": health,
                    "pid": worker.get("pid"),
                    "process_start_time": worker.get("process_start_time"),
                    "executable_path": worker.get("executable_path"),
                    "worker_generation": worker.get("worker_generation", 0),
                    "instance_id": worker.get("instance_id"),
                    "controller_heartbeat_at": observed_at,
                    "observed_at": observed_at,
                    "last_output_at": obs.get("last_output_at"),
                    "detail": {"reason_code": reason_code,
                               "supervisor_pid": worker.get("supervisor_pid")},
                })
                validate_observation(obs)
                ao["observation"] = obs
                ao["status"] = OP_OUTCOME_UNKNOWN
                ao["reason_code"] = reason_code
                ao["recommended_action"] = ACTION_RECOVER
                ao["finished_at"] = observed_at
                ao["updated_at"] = observed_at
                current.previous_operation = dict(ao)
                current.active_operation = None
            current.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            current.recent_errors.append({
                "at": observed_at, "reason_code": reason_code,
                "backend": self._backend,
            })
            return current

        persisted = False
        try:
            ledger_transaction(self._guard, self._ledger_path, _fail)
            persisted = True
        except Exception as exc:
            logger.error("backend failure persistence failed: %s", exc)
        self._poisoned = True
        self._event("backend_failed", backend=self._backend,
                    reason_code=reason_code, persisted=persisted)
        return self._error(reason_code, ledger_persisted=persisted)

    async def shutdown_backend(self, *, force: bool = False,
                               operation_id: Optional[str] = None,
                               synchronous_owner: bool = False) -> BackendShutdownResult:
        """Stop the owned backend; force is reserved for MCP finalization."""
        async with self._lock:
            if not force:
                self._require_authority(
                    self._read_ledger(), operation_id, synchronous_owner)
            return await self._shutdown_locked(persist_absent=True,
                                               interrupted=force)

    async def _shutdown_locked(self, *, persist_absent: bool,
                               interrupted: bool) -> BackendShutdownResult:
        backend = self._backend
        if self._bridge is None:
            return BackendShutdownResult(True, BACKEND_NONE, True, True)
        self._event("backend_stopping", backend=backend,
                    pid=self._identity.pid if self._identity else None)
        bridge = self._bridge
        stop_error = None
        try:
            await asyncio.wait_for(bridge.stop(), timeout=BACKEND_STOP_TIMEOUT_S)
        except asyncio.CancelledError:
            # The caller owns cancellation.  Do not turn it into an apparent
            # successful shutdown or a generic backend failure.
            raise
        except Exception as exc:
            stop_error = exc

        actual_clean = await self._ensure_owned_pid_gone(self._identity)
        supervisor_clean = await self._ensure_owned_pid_gone(
            self._supervisor_identity)
        success = actual_clean and supervisor_clean
        reason = None
        if not success:
            reason = ("BACKEND_IDENTITY_LOST_DURING_CLEANUP"
                      if self._owned_identity_changed()
                      else "BACKEND_CLEANUP_FAILED")
            self._poisoned = True
            self._persist_failure(reason, HEALTH_IDENTITY_MISMATCH, None)
        elif persist_absent:
            try:
                ledger_transaction(
                    self._guard, self._ledger_path,
                    lambda current: self._commit_absent(current, interrupted))
            except Exception as exc:
                reason = "BACKEND_ABSENT_WRITE_FAILED"
                success = False
                self._poisoned = True
                logger.error("backend ABSENT write failed: %s", exc)

        if success:
            self._event("backend_stopped", backend=backend)
            self._bridge = None
            self._backend = BACKEND_NONE
            self._identity = None
            self._supervisor_identity = None
            self._poisoned = False
        elif stop_error is not None:
            logger.error("backend stop failed: %s", stop_error)
        return BackendShutdownResult(success, backend, actual_clean,
                                     supervisor_clean, reason)

    def _commit_absent(self, current, interrupted: bool):
        now = _now_iso()
        old_worker = current.worker or {}
        generation = int(old_worker.get("worker_generation", 0) or 0)
        uart_owner = copy.deepcopy(old_worker.get("serial_owner"))
        uart_capture = copy.deepcopy(old_worker.get("uart_capture"))
        old_jtag = copy.deepcopy(old_worker.get("jtag_lease"))
        if isinstance(old_jtag, dict) and old_jtag:
            old_jtag.update({
                "status": "INTERRUPTED" if interrupted else "DISCONNECTED",
                "connected": False, "last_observed_at": now,
                "heartbeat_at": now,
            })
        current.worker = {
            "backend": BACKEND_NONE, "state": WORKER_STATE_ABSENT,
            "pid": None, "process_start_time": None,
            "executable_path": None, "executable_args": None,
            "worker_generation": generation,
            "instance_id": getattr(self._guard, "instance_id", None),
            "supervisor_pid": None,
            "supervisor_process_start_time": None,
            "supervisor_executable_path": None,
            "last_heartbeat_at": now,
            "project_lease_held": False, "jtag_lease_held": False,
            "jtag_lease": old_jtag or None,
            "serial_owner": uart_owner, "uart_capture": uart_capture,
        }
        if interrupted and isinstance(current.active_operation, dict) and \
                current.active_operation.get("status") in OP_NON_TERMINAL:
            ao = current.active_operation
            ao["status"] = OP_INTERRUPTED
            ao["reason_code"] = "MCP_EXIT"
            ao["recommended_action"] = ACTION_RECOVER
            ao["finished_at"] = now
            ao["updated_at"] = now
            current.previous_operation = dict(ao)
            current.active_operation = None
            current.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
        return current

    async def _cleanup_uncommitted_bridge(self, bridge) -> None:
        pid = getattr(bridge, "pid", None)
        try:
            await asyncio.wait_for(bridge.stop(), timeout=BACKEND_STOP_TIMEOUT_S)
        except Exception as exc:
            logger.error("uncommitted backend stop failed: %s", exc)
        if isinstance(pid, int) and pid > 0 and self._pid_alive(pid):
            self._kill_tree(pid)
            await self._wait_pid_gone(pid)

    async def _ensure_owned_pid_gone(self, expected: Optional[WorkerIdentity]) -> bool:
        if expected is None:
            return True
        if not self._pid_alive(expected.pid):
            return True
        actual = self._identity_reader(expected.pid)
        if not process_identity_matches(actual, expected):
            # PID reuse or unverifiable identity: never kill an unowned PID.
            return False
        self._kill_tree(expected.pid)
        return await self._wait_pid_gone(expected.pid)

    async def _wait_pid_gone(self, pid: int) -> bool:
        deadline = asyncio.get_running_loop().time() + PID_GONE_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            if not self._pid_alive(pid):
                return True
            await asyncio.sleep(0.05)
        return not self._pid_alive(pid)

    def _owned_identity_changed(self) -> bool:
        for expected in (self._identity, self._supervisor_identity):
            if expected is None or not self._pid_alive(expected.pid):
                continue
            if not process_identity_matches(self._identity_reader(expected.pid), expected):
                return True
        return False

    def _snapshot(self, state: str, *, observed_at=None,
                  reason_code=None) -> BackendSnapshot:
        return BackendSnapshot(
            backend=self._backend,
            state=state,
            pid=self._identity.pid if self._identity else None,
            supervisor_pid=(self._supervisor_identity.pid
                            if self._supervisor_identity else None),
            process_start_time=(self._identity.process_start_time
                                if self._identity else None),
            executable_path=(self._identity.executable_path
                             if self._identity else None),
            worker_generation=self._generation,
            instance_id=(self._identity.instance_id if self._identity else None),
            observed_at=observed_at,
            reason_code=reason_code,
        )

    @staticmethod
    def _error(reason_code: str, message: str = "", **details) -> dict:
        return {"status": "error", "error": {
            "code": "ENV_ERROR" if reason_code.startswith("BACKEND_") else "INTERNAL_ERROR",
            "message": message or reason_code,
            "details": {"reason_code": reason_code, **details},
        }}
