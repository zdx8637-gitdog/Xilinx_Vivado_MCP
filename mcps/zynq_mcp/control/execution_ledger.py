"""
execution_ledger.py — Atomic mutable Execution Ledger with fail-closed validation.

Core invariant: state mutation = ledger_transaction(guard, path, mutator).
The mutator runs inside the exclusive lock. No TOCTOU between read and write.
"""
import copy, json, os, time, hashlib, datetime, math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

# ---- constants ----
EXECUTION_LANE_IDLE = "IDLE"
EXECUTION_LANE_BUSY = "BUSY"
EXECUTION_LANE_CLOSING = "CLOSING"
EXECUTION_LANE_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
VALID_LANES = frozenset([EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    EXECUTION_LANE_CLOSING, EXECUTION_LANE_RECOVERY_REQUIRED])
WORKER_STATE_ABSENT = "ABSENT"; WORKER_STATE_STARTING = "STARTING"; WORKER_STATE_READY = "READY"
WORKER_STATE_BUSY = "BUSY"; WORKER_STATE_UNRESPONSIVE = "UNRESPONSIVE"
WORKER_STATE_ORPHANED = "ORPHANED"; WORKER_STATE_POISONED = "POISONED"
WORKER_STATE_STOPPING = "STOPPING"; WORKER_STATE_DEAD = "DEAD"
VALID_WORKER_STATES = frozenset([WORKER_STATE_ABSENT, WORKER_STATE_STARTING, WORKER_STATE_READY,
    WORKER_STATE_BUSY, WORKER_STATE_UNRESPONSIVE, WORKER_STATE_ORPHANED,
    WORKER_STATE_POISONED, WORKER_STATE_STOPPING, WORKER_STATE_DEAD])
OP_ACCEPTED = "ACCEPTED"; OP_RUNNING = "RUNNING"; OP_SUCCEEDED = "SUCCEEDED"
OP_FAILED = "FAILED"; OP_CANCELLED = "CANCELLED"; OP_TIMED_OUT = "TIMED_OUT"
OP_INTERRUPTED = "INTERRUPTED"; OP_OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
VALID_OP_STATES = frozenset([OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED,
    OP_CANCELLED, OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN])
OP_TERMINAL = frozenset([OP_SUCCEEDED, OP_FAILED, OP_CANCELLED, OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN])
OP_NON_TERMINAL = frozenset([OP_ACCEPTED, OP_RUNNING])
CURRENT_SCHEMA = "2.0"
LEGACY_SCHEMA_V1 = "1.0"

STATUS_SOURCE_VENDOR_RUN = "VENDOR_RUN"
STATUS_SOURCE_PROCESS = "PROCESS"
STATUS_SOURCE_RESOURCE = "RESOURCE"
STATUS_SOURCE_LOCAL = "LOCAL"
STATUS_SOURCE_RECOVERY = "RECOVERY"
VALID_STATUS_SOURCES = frozenset({
    STATUS_SOURCE_VENDOR_RUN, STATUS_SOURCE_PROCESS, STATUS_SOURCE_RESOURCE,
    STATUS_SOURCE_LOCAL, STATUS_SOURCE_RECOVERY,
})

BACKEND_VIVADO = "VIVADO"; BACKEND_XSCT = "XSCT"; BACKEND_XSDB = "XSDB"
BACKEND_UART = "UART"; BACKEND_PYTHON = "PYTHON"; BACKEND_NONE = "NONE"
VALID_BACKENDS = frozenset({
    BACKEND_VIVADO, BACKEND_XSCT, BACKEND_XSDB, BACKEND_UART,
    BACKEND_PYTHON, BACKEND_NONE,
})

OBS_NOT_STARTED = "NOT_STARTED"; OBS_STARTING = "STARTING"; OBS_RUNNING = "RUNNING"
OBS_COMPLETE = "COMPLETE"; OBS_FAILED = "FAILED"; OBS_UNKNOWN = "UNKNOWN"
OBS_NOT_APPLICABLE = "NOT_APPLICABLE"
VALID_OBSERVED_STATES = frozenset({
    OBS_NOT_STARTED, OBS_STARTING, OBS_RUNNING, OBS_COMPLETE, OBS_FAILED,
    OBS_UNKNOWN, OBS_NOT_APPLICABLE,
})

HEALTH_NOT_STARTED = "NOT_STARTED"; HEALTH_STARTING = "STARTING"; HEALTH_ALIVE = "ALIVE"
HEALTH_UNRESPONSIVE = "UNRESPONSIVE"; HEALTH_DEAD = "DEAD"
HEALTH_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"; HEALTH_NOT_APPLICABLE = "NOT_APPLICABLE"
VALID_WORKER_HEALTH = frozenset({
    HEALTH_NOT_STARTED, HEALTH_STARTING, HEALTH_ALIVE, HEALTH_UNRESPONSIVE,
    HEALTH_DEAD, HEALTH_IDENTITY_MISMATCH, HEALTH_NOT_APPLICABLE,
})

ARTIFACT_NOT_APPLICABLE = "NOT_APPLICABLE"; ARTIFACT_PENDING = "PENDING"
ARTIFACT_VERIFYING = "VERIFYING"; ARTIFACT_PUBLISHING_MANIFEST = "PUBLISHING_MANIFEST"
ARTIFACT_PUBLISHED = "PUBLISHED"; ARTIFACT_FAILED = "FAILED"
VALID_ARTIFACT_STATES = frozenset({
    ARTIFACT_NOT_APPLICABLE, ARTIFACT_PENDING, ARTIFACT_VERIFYING,
    ARTIFACT_PUBLISHING_MANIFEST, ARTIFACT_PUBLISHED, ARTIFACT_FAILED,
})

ACTION_WAIT = "WAIT"; ACTION_NEXT_STEP = "NEXT_STEP"; ACTION_DIAGNOSE = "DIAGNOSE"
ACTION_RECOVER = "RECOVER"; ACTION_CONFIRM_RETRY = "CONFIRM_RETRY"
ACTION_CLOSE_SESSION = "CLOSE_SESSION"; ACTION_NONE = "NONE"
VALID_RECOMMENDED_ACTIONS = frozenset({
    ACTION_WAIT, ACTION_NEXT_STEP, ACTION_DIAGNOSE, ACTION_RECOVER,
    ACTION_CONFIRM_RETRY, ACTION_CLOSE_SESSION, ACTION_NONE,
})

OBSERVATION_REQUIRED_FIELDS = frozenset({
    "status_source", "backend", "observed_state", "vendor_status",
    "current_step", "progress_pct", "worker_health", "pid",
    "process_start_time", "executable_path", "worker_generation",
    "instance_id", "controller_heartbeat_at", "observed_at",
    "last_output_at", "detail",
})

# ---- errors ----
class LedgerError(Exception): pass
class LedgerCorruptError(LedgerError): pass
class LedgerSchemaError(LedgerError): pass
class LedgerWorkspaceMismatchError(LedgerError): pass
class LedgerInvalidError(LedgerError): pass
class LedgerInconsistentError(LedgerError): pass
class LedgerWriteError(LedgerError): pass
class ChannelBusyError(LedgerError): pass
class DuplicateRequestError(LedgerError): pass
class ObservationValidationError(LedgerInvalidError): pass

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + f"{int(time.time()*1e6)%1000000:06d}Z"

def ledger_sha256(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _parse_iso_datetime(value: str) -> datetime.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _iso_after(base_iso: str, seconds: float) -> str:
    try:
        base = _parse_iso_datetime(base_iso)
    except (TypeError, ValueError):
        base = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    target = base + datetime.timedelta(seconds=float(seconds))
    return target.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def operation_deadline_at(tool_name: str, accepted_at: str, timeout_s: Optional[float] = None) -> str:
    """Return a deterministic, bounded operation deadline timestamp."""
    if timeout_s is None:
        from mcps.zynq_mcp.control.timeout_config import load_timeout_config, deadline_for_tool
        config = load_timeout_config()
        seconds = deadline_for_tool(tool_name or "", config)
    else:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise LedgerInvalidError("timeout_s must be a finite positive number")
        seconds = float(timeout_s)
        if not math.isfinite(seconds) or seconds <= 0:
            raise LedgerInvalidError("timeout_s must be a finite positive number")
    return _iso_after(accepted_at, seconds)


def default_observation(*, observed_at: Optional[str] = None, migrated: bool = False) -> dict:
    """Truthful initial observation: local admission happened; no backend has started."""
    detail = {"migrated_from_schema": LEGACY_SCHEMA_V1} if migrated else {}
    return {
        "status_source": STATUS_SOURCE_RECOVERY if migrated else STATUS_SOURCE_LOCAL,
        "backend": BACKEND_NONE,
        "observed_state": OBS_UNKNOWN if migrated else OBS_NOT_STARTED,
        "vendor_status": None,
        "current_step": "LEGACY_RECONCILE" if migrated else "ADMISSION",
        "progress_pct": None,
        "worker_health": HEALTH_UNRESPONSIVE if migrated else HEALTH_NOT_STARTED,
        "pid": None,
        "process_start_time": None,
        "executable_path": None,
        "worker_generation": 0,
        "instance_id": None,
        "controller_heartbeat_at": None,
        "observed_at": observed_at,
        "last_output_at": None,
        "detail": detail,
    }


def recommended_action_for_status(status: str) -> str:
    if status in OP_NON_TERMINAL:
        return ACTION_WAIT
    if status == OP_SUCCEEDED:
        return ACTION_NEXT_STEP
    if status == OP_FAILED:
        return ACTION_DIAGNOSE
    if status == OP_CANCELLED:
        return ACTION_CONFIRM_RETRY
    if status in (OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN):
        return ACTION_RECOVER
    return ACTION_NONE


def operation_contract_fields(tool_name: str, accepted_at: str,
                              timeout_s: Optional[float] = None) -> dict:
    """Create the O1 fields shared by every newly-admitted command."""
    return {
        "deadline_at": operation_deadline_at(tool_name, accepted_at, timeout_s),
        "artifact_state": ARTIFACT_NOT_APPLICABLE,
        "recommended_action": ACTION_WAIT,
        # Admission is controller truth, not a real tool/process/resource
        # observation.  observed_at remains null until op_observe() records
        # evidence from an observer.
        "observation": default_observation(),
    }


def _upgrade_operation_record(record: Optional[dict], *, worker: Optional[dict] = None,
                              migrated: bool = False, base_timestamp: str = "") -> Optional[dict]:
    if record is None:
        return None
    if not isinstance(record, dict):
        raise LedgerInvalidError("operation record must be an object or null")
    upgraded = copy.deepcopy(record)
    status = upgraded.get("status", "")
    accepted_at = upgraded.get("accepted_at") or upgraded.get("created_at") or base_timestamp
    if not accepted_at:
        accepted_at = "1970-01-01T00:00:00.000000Z"
    if "deadline_at" not in upgraded or not upgraded.get("deadline_at"):
        upgraded["deadline_at"] = operation_deadline_at(
            str(upgraded.get("tool_name", "")), accepted_at)
    if "artifact_state" not in upgraded:
        upgraded["artifact_state"] = ARTIFACT_NOT_APPLICABLE
    if "recommended_action" not in upgraded:
        upgraded["recommended_action"] = recommended_action_for_status(status)
    if "observation" not in upgraded:
        # Legacy timestamps and controller heartbeats are not evidence that a
        # tool was observed.  Preserve them only in their explicit controller
        # field and leave observed_at null for reconciliation.
        observation = default_observation(migrated=migrated)
        old_hb = upgraded.get("heartbeat_at")
        if isinstance(old_hb, str) and old_hb:
            observation["controller_heartbeat_at"] = old_hb
        upgraded["observation"] = observation
    return upgraded


def _migrate_v1_dict(data: dict) -> dict:
    """Pure in-memory 1.0→2.0 migration. Persistence happens only in a transaction."""
    migrated = copy.deepcopy(data)
    migrated["schema_version"] = CURRENT_SCHEMA
    base = migrated.get("updated_at", "")
    worker = migrated.get("worker") if isinstance(migrated.get("worker"), dict) else {}
    migrated["active_operation"] = _upgrade_operation_record(
        migrated.get("active_operation"), worker=worker, migrated=True,
        base_timestamp=base)
    migrated["previous_operation"] = _upgrade_operation_record(
        migrated.get("previous_operation"), worker=worker, migrated=True,
        base_timestamp=base)
    return migrated


def _validate_optional_timestamp(value: Any, field_name: str) -> None:
    if value is None:
        return
    try:
        _parse_iso_datetime(value)
    except (TypeError, ValueError) as exc:
        raise ObservationValidationError(f"{field_name} must be an ISO-8601 timestamp or null") from exc


def validate_observation(observation: Any) -> None:
    if not isinstance(observation, dict):
        raise ObservationValidationError("observation must be an object")
    missing = OBSERVATION_REQUIRED_FIELDS - observation.keys()
    if missing:
        raise ObservationValidationError(
            f"observation missing required fields: {', '.join(sorted(missing))}")
    if observation.get("status_source") not in VALID_STATUS_SOURCES:
        raise ObservationValidationError("invalid observation.status_source")
    if observation.get("backend") not in VALID_BACKENDS:
        raise ObservationValidationError("invalid observation.backend")
    if observation.get("observed_state") not in VALID_OBSERVED_STATES:
        raise ObservationValidationError("invalid observation.observed_state")
    if observation.get("worker_health") not in VALID_WORKER_HEALTH:
        raise ObservationValidationError("invalid observation.worker_health")
    current_step = observation.get("current_step")
    if not isinstance(current_step, str) or not current_step.strip():
        raise ObservationValidationError("observation.current_step must be non-empty")
    vendor = observation.get("vendor_status")
    if vendor is not None and not isinstance(vendor, str):
        raise ObservationValidationError("observation.vendor_status must be string or null")
    progress = observation.get("progress_pct")
    if progress is not None:
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            raise ObservationValidationError("observation.progress_pct must be numeric or null")
        if not math.isfinite(float(progress)) or not 0 <= float(progress) <= 100:
            raise ObservationValidationError("observation.progress_pct must be between 0 and 100")
    pid = observation.get("pid")
    if pid is not None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ObservationValidationError("observation.pid must be a positive integer or null")
        start = observation.get("process_start_time")
        generation = observation.get("worker_generation")
        if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(float(start)) or start <= 0:
            raise ObservationValidationError("pid requires positive process_start_time")
        if not isinstance(observation.get("executable_path"), str) or not observation["executable_path"].strip():
            raise ObservationValidationError("pid requires executable_path")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ObservationValidationError("pid requires non-negative worker_generation")
        if not isinstance(observation.get("instance_id"), str) or not observation["instance_id"].strip():
            raise ObservationValidationError("pid requires instance_id")
    elif observation.get("process_start_time") is not None or observation.get("executable_path") is not None:
        raise ObservationValidationError("process identity cannot be present without pid")
    for name in ("controller_heartbeat_at", "observed_at", "last_output_at"):
        _validate_optional_timestamp(observation.get(name), f"observation.{name}")
    if not isinstance(observation.get("detail"), dict):
        raise ObservationValidationError("observation.detail must be an object")


def _validate_operation_record(record: Optional[dict], field_name: str) -> None:
    if record is None:
        return
    if not isinstance(record, dict):
        raise LedgerInvalidError(f"{field_name} must be an object or null")
    status = record.get("status", "")
    if status and status not in VALID_OP_STATES:
        raise LedgerInvalidError(f"Invalid {field_name}.status: {status!r}")
    deadline_at = record.get("deadline_at")
    if not isinstance(deadline_at, str) or not deadline_at.strip():
        raise LedgerInvalidError(f"{field_name}.deadline_at must be non-empty")
    _validate_optional_timestamp(deadline_at, f"{field_name}.deadline_at")
    if record.get("artifact_state") not in VALID_ARTIFACT_STATES:
        raise LedgerInvalidError(f"Invalid {field_name}.artifact_state")
    if record.get("recommended_action") not in VALID_RECOMMENDED_ACTIONS:
        raise LedgerInvalidError(f"Invalid {field_name}.recommended_action")
    validate_observation(record.get("observation"))

@dataclass
class ExecutionLedger:
    schema_version: str = CURRENT_SCHEMA; ledger_sequence: int = 0
    instance_id: str = ""; workspace_id: str = ""
    updated_at: str = field(default_factory=_now_iso)
    execution_lane: str = EXECUTION_LANE_IDLE
    context: dict[str, Any] = field(default_factory=dict)
    active_operation: Optional[dict] = None
    previous_operation: Optional[dict] = None
    worker: dict[str, Any] = field(default_factory=lambda: {
        "pid": None, "process_start_time": None, "executable_path": None, "executable_args": None,
        "worker_generation": 0, "instance_id": None, "last_heartbeat_at": None, "state": WORKER_STATE_ABSENT,
        "project_lease_held": False, "jtag_lease_held": False,
        "jtag_lease": None, "serial_owner": None, "uart_capture": None,
    })
    dedup_registry: dict[str, str] = field(default_factory=dict)
    recent_errors: list[dict] = field(default_factory=list)
    recovery_log: list[dict] = field(default_factory=list)
    takeover_count: int = 0
    primary_instance_id: Optional[str] = None
    owner_lock_held_since: Optional[str] = None

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "ledger_sequence": self.ledger_sequence,
            "instance_id": self.instance_id, "workspace_id": self.workspace_id,
            "updated_at": self.updated_at, "execution_lane": self.execution_lane,
            "context": dict(self.context),
            "active_operation": dict(self.active_operation) if self.active_operation else None,
            "previous_operation": dict(self.previous_operation) if self.previous_operation else None,
            "worker": dict(self.worker), "dedup_registry": dict(self.dedup_registry),
            "recent_errors": list(self.recent_errors), "recovery_log": list(self.recovery_log),
            "takeover_count": self.takeover_count,
            "primary_instance_id": self.primary_instance_id,
            "owner_lock_held_since": self.owner_lock_held_since}

    @classmethod
    def from_validated_dict(cls, d: dict, expected_workspace_id: str = "") -> "ExecutionLedger":
        sv = d.get("schema_version", "")
        if sv == LEGACY_SCHEMA_V1:
            d = _migrate_v1_dict(d)
            sv = CURRENT_SCHEMA
        elif sv != CURRENT_SCHEMA:
            raise LedgerSchemaError(f"Expected schema {CURRENT_SCHEMA} or migratable {LEGACY_SCHEMA_V1}, got {sv!r}")
        ws = d.get("workspace_id", "")
        if expected_workspace_id and ws and ws != expected_workspace_id:
            raise LedgerWorkspaceMismatchError(f"Workspace mismatch: expected {expected_workspace_id}, got {ws}")
        seq = d.get("ledger_sequence")
        if not isinstance(seq, int) or seq < 0:
            raise LedgerInvalidError("ledger_sequence must be non-negative integer")
        lane = d.get("execution_lane", "")
        if lane not in VALID_LANES: raise LedgerInvalidError(f"Invalid execution_lane: {lane!r}")
        w = d.get("worker")
        if isinstance(w, dict):
            ws_state = w.get("state", "")
            if ws_state and ws_state not in VALID_WORKER_STATES:
                raise LedgerInvalidError(f"Invalid worker.state: {ws_state!r}")
        ao = d.get("active_operation")
        po = d.get("previous_operation")
        _validate_operation_record(ao, "active_operation")
        _validate_operation_record(po, "previous_operation")
        if isinstance(ao, dict) and ao.get("status") not in (None, "",) + tuple(OP_TERMINAL):
            if lane == EXECUTION_LANE_IDLE:
                raise LedgerInconsistentError("active_operation present but execution_lane is IDLE")
        tc = d.get("takeover_count", 0)
        if not isinstance(tc, int): raise LedgerInvalidError("takeover_count must be integer")
        return cls(schema_version=sv, ledger_sequence=seq, instance_id=d.get("instance_id", ""),
            workspace_id=ws, updated_at=d.get("updated_at", _now_iso()), execution_lane=lane,
            context=d.get("context", {}), active_operation=ao, previous_operation=po,
            worker=w if isinstance(w, dict) else {"state": WORKER_STATE_ABSENT},
            dedup_registry=d.get("dedup_registry", {}),
            recent_errors=d.get("recent_errors", []), recovery_log=d.get("recovery_log", []),
            takeover_count=tc, primary_instance_id=d.get("primary_instance_id"),
            owner_lock_held_since=d.get("owner_lock_held_since"))


# ---- atomic transaction ----
def ledger_transaction(guard, ledger_path, mutator):
    """Atomic RMW: inproc lock → OS exclusive → read+validate → mutate → increment → write → unlock.
    Bounded wait on inproc lock; returns LOCK_BUSY on timeout."""
    # PHASE 0: in-process serialization (bounded, prevents thread races)
    if hasattr(guard, 'acquire_inproc') and callable(guard.acquire_inproc):
        if not guard.acquire_inproc():
            raise ChannelBusyError("LEDGER_LOCK_TIMEOUT")
    try:
        wsid = guard.workspace_id if hasattr(guard, 'workspace_id') else None
        fd = guard.acquire_ledger_exclusive()
        try:
            current = _read_and_validate(ledger_path, allow_missing=not ledger_path.exists(),
                                          expected_workspace_id=wsid)
            updated = mutator(current)
            updated.ledger_sequence = current.ledger_sequence + 1
            updated.updated_at = _now_iso()
            _validate_result(updated)
            _atomic_write(ledger_path, updated.to_dict())
            return updated
        except (LedgerCorruptError, LedgerSchemaError, LedgerWorkspaceMismatchError,
                LedgerInvalidError, LedgerInconsistentError): raise
        except (ChannelBusyError, DuplicateRequestError): raise
        except LedgerWriteError: raise
        except Exception as e: raise LedgerWriteError(f"Transaction failed: {e}") from e
        finally: guard.release_ledger_lock(fd)
    finally:
        if hasattr(guard, 'release_inproc') and callable(guard.release_inproc):
            guard.release_inproc()


def ledger_read_shared(guard, ledger_path, expected_workspace_id=None):
    """Read ledger under shared lock. Validates workspace_id if provided. Zero writes."""
    if not ledger_path.exists():
        raise LedgerCorruptError("Ledger file does not exist")
    fd = guard.acquire_ledger_shared()
    try:
        ledger = _read_and_validate(ledger_path,
            expected_workspace_id=expected_workspace_id or guard.workspace_id if hasattr(guard, 'workspace_id') else None)
        raw = ledger_path.read_text(encoding="utf-8")
        sha = ledger_sha256(json.loads(raw))
        return ledger, sha
    finally: guard.release_ledger_lock(fd)


def _read_and_validate(ledger_path, allow_missing=False, expected_workspace_id=None):
    if not ledger_path.exists():
        if allow_missing: return ExecutionLedger(ledger_sequence=0)
        raise LedgerCorruptError("Ledger file does not exist")
    try: raw = ledger_path.read_text(encoding="utf-8")
    except Exception as e: raise LedgerCorruptError(f"Cannot read ledger file: {e}") from e
    if not raw.strip(): raise LedgerCorruptError("Ledger file is empty")
    try: data = json.loads(raw)
    except json.JSONDecodeError as e: raise LedgerCorruptError(f"Ledger JSON corrupt: {e}") from e
    if not isinstance(data, dict): raise LedgerInvalidError("Ledger root must be a JSON object")
    return ExecutionLedger.from_validated_dict(data, expected_workspace_id=expected_workspace_id or "")


def _validate_result(ledger):
    if ledger.schema_version != CURRENT_SCHEMA: raise LedgerSchemaError(f"schema {ledger.schema_version}")
    if ledger.execution_lane not in VALID_LANES: raise LedgerInvalidError(f"lane {ledger.execution_lane}")
    # Mutators written before O1 may create a minimal operation dict. Normalize
    # it at the single persistence boundary; a malformed explicit observation
    # remains fail-closed and is never silently replaced.
    ledger.active_operation = _upgrade_operation_record(
        ledger.active_operation, worker=ledger.worker, base_timestamp=ledger.updated_at)
    ledger.previous_operation = _upgrade_operation_record(
        ledger.previous_operation, worker=ledger.worker, base_timestamp=ledger.updated_at)
    _validate_operation_record(ledger.active_operation, "active_operation")
    _validate_operation_record(ledger.previous_operation, "previous_operation")
    ao = ledger.active_operation
    if isinstance(ao, dict) and ao.get("status") in OP_NON_TERMINAL:
        if ledger.execution_lane == EXECUTION_LANE_IDLE:
            raise LedgerInconsistentError("active_operation running but lane is IDLE")


def _atomic_write(path, data):
    tmp_path = Path(str(path) + ".tmp")
    raw = json.dumps(data, indent=2, ensure_ascii=False)
    with open(tmp_path, "wb") as f:
        f.write(raw.encode("utf-8")); f.flush(); os.fsync(f.fileno())
    os.replace(str(tmp_path), str(path))
