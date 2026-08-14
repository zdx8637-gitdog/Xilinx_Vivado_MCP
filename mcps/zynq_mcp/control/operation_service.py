"""
operation_service.py — Ledger-persisted Operation lifecycle.

Every state transition is a ledger_transaction. OperationRegistry is cache only.

BUGFIX r1-final: terminal states must NOT reassign active_operation after
setting it to None. Only non-terminal (ACCEPTED, RUNNING) keep active_operation.
"""
import copy, uuid, hashlib, json, time
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED, OP_CANCELLED,
    OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_NON_TERMINAL, OP_TERMINAL,
    ChannelBusyError, DuplicateRequestError, ObservationValidationError,
    validate_observation, recommended_action_for_status,
    ACTION_WAIT, ACTION_RECOVER,
    ARTIFACT_NOT_APPLICABLE, VALID_ARTIFACT_STATES,
    VALID_RECOMMENDED_ACTIONS,
    STATUS_SOURCE_LOCAL, BACKEND_NONE,
    OBS_STARTING, OBS_COMPLETE, OBS_FAILED, OBS_UNKNOWN,
    HEALTH_NOT_STARTED, HEALTH_NOT_APPLICABLE,
)


class InFlightDuplicateError(DuplicateRequestError):
    """P10: existing operation is still running."""


class TerminalDuplicateError(DuplicateRequestError):
    """P10: previous attempt ended in terminal state."""


def request_signature(session_id, stage, tool_name, args, artifact_revision):
    canonical = json.dumps({"sid": session_id, "stage": stage, "tool": tool_name,
        "args": dict(sorted(args.items())), "rev": artifact_revision or ""},
        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def op_admit(guard, ledger_path, tool_name, args, session_id, board_id, project_path, op_id, sig) -> dict:
    """Atomic admission: preflight + P10 dedup + ACCEPTED in one transaction."""
    from mcps.zynq_mcp.control.execution_gate import preflight_mutator
    mut = preflight_mutator(tool_name, args, session_id, board_id, project_path, op_id, sig)
    try:
        ledger = ledger_transaction(guard, ledger_path, mut)
    except InFlightDuplicateError as e:
        return {"status": "success", "data": {
            "operation_id": e.args[0], "deduplicated": True, "status": "RUNNING",
            "recommended_action": ACTION_WAIT, "poll_after_s": 10}}
    except TerminalDuplicateError as e:
        return {"status": "error", "error": {
            "code": "LOCK_BUSY", "message": f"Previous attempt {e.args[0]} completed. Retry with explicit intent.",
            "details": {"reason_code": "CONFIRM_RETRY_REQUIRED", "previous_operation_id": e.args[0]}}}
    except ChannelBusyError as e:
        try:
            from mcps.zynq_mcp.control.execution_ledger import ledger_read_shared
            busy_ledger = ledger_read_shared(guard, ledger_path)[0]
        except Exception:
            busy_ledger = None
        return {"status": "error", "error": {
            "code": "LOCK_BUSY", "message": f"Channel blocked: {e.args[0]}",
            "details": channel_busy_details(busy_ledger, str(e.args[0]))}}
    except Exception as e:
        return {"status": "error", "error": {
            "code": "INTERNAL_ERROR", "message": f"Admission failed: {e}"}}
    return {"status": "success", "data": {"operation_id": op_id, "status": "accepted",
        "ledger_sequence": ledger.ledger_sequence}, "ledger": ledger}


def op_transition(guard, ledger_path, op_id, new_status, next_stage=None, context_updates=None, **fields):
    """ACCEPTED→RUNNING or RUNNING→terminal. Atomic. Terminal: active→None, previous=operation.
    Non-terminal: active=operation.

    E004: On SUCCEEDED with next_stage, atomically advances workflow stage
    within the same ledger_transaction. Validates via is_valid_forward.

    B05: On SUCCEEDED with context_updates dict, atomically writes those fields
    into current.context (e.g. platform_revision). Any failure → nothing published.
    """
    from mcps.zynq_mcp.control.context import is_valid_forward
    import copy as _copy

    def _mutator(current):
        ao = current.active_operation
        if not ao or ao.get("operation_id") != op_id:
            raise ChannelBusyError("OPERATION_NOT_FOUND")
        cur = ao.get("status", "")
        legal = {"ACCEPTED": {"RUNNING", "FAILED", "CANCELLED", "INTERRUPTED", "OUTCOME_UNKNOWN"},
                 "RUNNING": {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT",
                             "INTERRUPTED", "OUTCOME_UNKNOWN"}}
        if new_status not in legal.get(cur, set()):
            raise ChannelBusyError(f"ILLEGAL_TRANSITION: {cur}->{new_status}")
        transition_at = _now_iso()
        ao["status"] = new_status
        ao["updated_at"] = transition_at
        if new_status == "RUNNING" and cur != "RUNNING":
            ao["started_at"] = transition_at  # never move started_at on a RUNNING→RUNNING heartbeat refresh
        for k, v in fields.items():
            ao[k] = v

        # O1: the legacy top-level heartbeat_at remains as a compatibility
        # alias, but it is controller liveness only. It must never refresh the
        # real observed_at timestamp.
        observation = copy.deepcopy(ao.get("observation") or {})
        if fields.get("heartbeat_at") is not None:
            observation["controller_heartbeat_at"] = fields["heartbeat_at"]

        # Existing executors have not yet been migrated to a vendor observer
        # (O3/O4). Record only what this local state transition proves. Once a
        # real observer has supplied VENDOR_RUN/PROCESS/RESOURCE evidence, do
        # not overwrite it with controller inference.
        if (observation.get("status_source") == STATUS_SOURCE_LOCAL and
                observation.get("backend") == BACKEND_NONE):
            if new_status == OP_RUNNING:
                observation["observed_state"] = OBS_STARTING
                observation["current_step"] = "EXECUTION"
                # A local Operation transition does not prove that a backend
                # process exists.  O2+ observers own worker_health/observed_at.
                observation["worker_health"] = HEALTH_NOT_STARTED
            elif new_status in OP_TERMINAL:
                if new_status == OP_SUCCEEDED:
                    observation["observed_state"] = OBS_COMPLETE
                elif new_status in (OP_FAILED, OP_CANCELLED):
                    observation["observed_state"] = OBS_FAILED
                else:
                    observation["observed_state"] = OBS_UNKNOWN
                observation["current_step"] = "TERMINAL"
                observation["worker_health"] = HEALTH_NOT_APPLICABLE
        validate_observation(observation)
        ao["observation"] = observation
        ao["recommended_action"] = recommended_action_for_status(new_status)

        # E004: atomic stage advancement on SUCCEEDED
        if new_status == OP_SUCCEEDED and next_stage is not None:
            current_stage = current.context.get("current_stage", "")
            if not is_valid_forward(current_stage, next_stage):
                raise ChannelBusyError("ILLEGAL_STAGE_TRANSITION")
            # completion_evidence: None→dict, dict→copy+merge, other→fail
            ev = ao.get("completion_evidence")
            if ev is None:
                new_ev = {}
            elif isinstance(ev, dict):
                new_ev = dict(ev)
            else:
                raise ChannelBusyError("COMPLETION_EVIDENCE_CORRUPT")
            new_ev["stage_advanced_from"] = current_stage
            new_ev["stage_advanced_to"] = next_stage
            ao["completion_evidence"] = new_ev
            current.context["current_stage"] = next_stage
            # B05: atomic context updates (platform_revision etc.)
            if context_updates and isinstance(context_updates, dict):
                for k, v in context_updates.items():
                    current.context[k] = v

        if new_status in OP_TERMINAL:
            ao["finished_at"] = transition_at
            current.previous_operation = dict(ao)
            current.active_operation = None
            if new_status == OP_SUCCEEDED:
                current.execution_lane = EXECUTION_LANE_IDLE
            elif new_status in (OP_FAILED, OP_CANCELLED):
                current.execution_lane = EXECUTION_LANE_IDLE
            elif new_status in (OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN):
                current.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            # terminal: do NOT reassign active_operation; it remains None
        else:
            # non-terminal (ACCEPTED, RUNNING): keep active_operation
            current.active_operation = ao

        return current
    try:
        ledger = ledger_transaction(guard, ledger_path, _mutator)
        return {"status": "success", "data": {"operation_id": op_id, "status": new_status,
            "ledger_sequence": ledger.ledger_sequence}, "ledger": ledger}
    except ChannelBusyError as e:
        return {"status": "error", "error": {
            "code": "LOCK_BUSY", "message": str(e.args[0]),
            "details": {"reason_code": e.args[0]}}}
    except Exception as e:
        return {"status": "error", "error": {
            "code": "INTERNAL_ERROR", "message": str(e)}}


def op_observe(guard, ledger_path, op_id: str, observation_updates: dict,
               *, artifact_state=None, recommended_action=None):
    """Atomically update real observation data without changing Operation state.

    A controller heartbeat may be updated alone. Any tool/process/resource
    observation update must carry a fresh ``observed_at`` so controller
    liveness can never masquerade as real tool evidence.
    """
    if not isinstance(op_id, str) or not op_id.strip():
        raise ObservationValidationError("operation_id must be non-empty")
    if not isinstance(observation_updates, dict) or not observation_updates:
        raise ObservationValidationError("observation_updates must be a non-empty object")
    allowed = {
        "status_source", "backend", "observed_state", "vendor_status",
        "current_step", "progress_pct", "worker_health", "pid",
        "process_start_time", "executable_path", "worker_generation",
        "instance_id", "controller_heartbeat_at", "observed_at",
        "last_output_at", "detail",
    }
    unknown = set(observation_updates) - allowed
    if unknown:
        raise ObservationValidationError(
            f"unknown observation fields: {', '.join(sorted(unknown))}")
    real_updates = set(observation_updates) - {"controller_heartbeat_at"}
    if real_updates and not observation_updates.get("observed_at"):
        raise ObservationValidationError("real observation update requires observed_at")
    if artifact_state is not None and artifact_state not in VALID_ARTIFACT_STATES:
        raise ObservationValidationError("invalid artifact_state")
    if recommended_action is not None and recommended_action not in VALID_RECOMMENDED_ACTIONS:
        raise ObservationValidationError("invalid recommended_action")

    def _mutator(current):
        ao = current.active_operation
        if not ao or ao.get("operation_id") != op_id or ao.get("status") not in OP_NON_TERMINAL:
            raise ChannelBusyError("OPERATION_NOT_ACTIVE")
        before_status = ao.get("status")
        before_lane = current.execution_lane
        merged = copy.deepcopy(ao.get("observation") or {})
        for key, value in observation_updates.items():
            merged[key] = copy.deepcopy(value)
        validate_observation(merged)
        ao["observation"] = merged
        if artifact_state is not None:
            ao["artifact_state"] = artifact_state
        if recommended_action is not None:
            ao["recommended_action"] = recommended_action
        ao["updated_at"] = _now_iso()
        if ao.get("status") != before_status or current.execution_lane != before_lane:
            raise ChannelBusyError("OBSERVATION_MOVED_OPERATION_STATE")
        current.active_operation = ao
        return current

    ledger = ledger_transaction(guard, ledger_path, _mutator)
    return {"status": "success", "data": {
        "operation_id": op_id,
        "status": ledger.active_operation.get("status"),
        "ledger_sequence": ledger.ledger_sequence,
        "observation": copy.deepcopy(ledger.active_operation.get("observation")),
        "artifact_state": ledger.active_operation.get("artifact_state"),
        "recommended_action": ledger.active_operation.get("recommended_action"),
    }, "ledger": ledger}


def _parse_record_time(value):
    if not isinstance(value, str) or not value:
        return None
    import datetime
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


def operation_public_view(ledger, record: dict, *, now_s=None) -> dict:
    """Build the frozen public status view from Ledger truth only."""
    if not isinstance(record, dict):
        raise ValueError("operation record must be an object")
    now_s = time.time() if now_s is None else float(now_s)
    observation = copy.deepcopy(record.get("observation") or {})
    result = copy.deepcopy(record)
    result["current_status"] = record.get("status")  # compatibility alias
    result["execution_lane"] = ledger.execution_lane
    result["workflow_stage"] = record.get(
        "workflow_stage", (ledger.context or {}).get("current_stage", ""))
    result["current_step"] = observation.get("current_step")
    result["status_source"] = observation.get("status_source")
    result["backend"] = observation.get("backend")
    result["observed_state"] = observation.get("observed_state")
    result["vendor_status"] = observation.get("vendor_status")
    result["worker_health"] = observation.get("worker_health")
    result["worker_pid"] = observation.get("pid")
    result["observed_at"] = observation.get("observed_at")
    result["controller_heartbeat_at"] = observation.get("controller_heartbeat_at")
    result["last_output_at"] = observation.get("last_output_at")
    result["artifact_state"] = record.get("artifact_state", ARTIFACT_NOT_APPLICABLE)
    result["recommended_action"] = record.get(
        "recommended_action", recommended_action_for_status(record.get("status", "")))
    result["deadline_at"] = record.get("deadline_at")
    deadline_s = _parse_record_time(record.get("deadline_at"))
    result["deadline_remaining_s"] = (
        round(max(0.0, deadline_s - now_s), 1) if deadline_s is not None else None)
    result["progress_pct"] = observation.get("progress_pct")
    result["observation"] = observation
    accepted_s = _parse_record_time(record.get("accepted_at"))
    if accepted_s is not None:
        result["elapsed_s"] = round(max(0.0, now_s - accepted_s), 1)
    else:
        result["elapsed_s"] = 0.0
    controller_hb = observation.get("controller_heartbeat_at") or record.get("heartbeat_at")
    controller_hb_s = _parse_record_time(controller_hb)
    if controller_hb_s is not None:
        # Compatibility alias. This is explicitly controller heartbeat age;
        # observed_at remains independent and is never derived from it.
        result["heartbeat_age_s"] = round(max(0.0, now_s - controller_hb_s), 1)
    result["poll_after_s"] = 10 if result["recommended_action"] == ACTION_WAIT else 0
    return result


def channel_busy_details(ledger, reason_code="CHANNEL_BUSY") -> dict:
    """Return the frozen busy payload when an active Operation is present."""
    details = {"reason_code": reason_code}
    ao = ledger.active_operation if ledger is not None else None
    if isinstance(ao, dict) and ao.get("status") in OP_NON_TERMINAL:
        view = operation_public_view(ledger, ao)
        details.update({
            "active_operation_id": view.get("operation_id"),
            "tool_name": view.get("tool_name"),
            "status": view.get("status"),
            "current_step": view.get("current_step"),
            "status_source": view.get("status_source"),
            "backend": view.get("backend"),
            "observed_state": view.get("observed_state"),
            "vendor_status": view.get("vendor_status"),
            "progress_pct": view.get("progress_pct"),
            "worker_health": view.get("worker_health"),
            "worker_pid": view.get("worker_pid"),
            "observed_at": view.get("observed_at"),
            "controller_heartbeat_at": view.get("controller_heartbeat_at"),
            "elapsed_s": view.get("elapsed_s"),
            "deadline_at": view.get("deadline_at"),
            "deadline_remaining_s": view.get("deadline_remaining_s"),
            "artifact_state": view.get("artifact_state"),
            "recommended_action": ACTION_WAIT,
            "poll_after_s": view.get("poll_after_s", 10),
        })
    else:
        details.update({"recommended_action": ACTION_RECOVER if reason_code != "CHANNEL_BUSY" else ACTION_WAIT,
                        "poll_after_s": 5})
    return details


def op_admit_create_session(guard, ledger_path, args, instance_id, op_id, sig):
    """Admit a create_session command."""
    from mcps.zynq_mcp.control.session import create_session_mutator
    commit = create_session_mutator(args, instance_id, op_id, sig)
    try:
        ledger = commit(guard, ledger_path)
    except ChannelBusyError as e:
        return {"status": "error", "error": {
            "code": "LOCK_BUSY", "message": str(e.args[0]),
            "details": {"reason_code": e.args[0]}}}
    except Exception as e:
        return {"status": "error", "error": {
            "code": "INTERNAL_ERROR", "message": str(e)}}
    ctx = ledger.context
    return {"status": "success", "data": {"session_id": ctx["session_id"],
        "board_id": ctx["board_id"], "project_path": ctx["project_path"],
        "board_package_revision": ctx["board_package_revision"],
        "board_profile_sha256": ctx.get("board_profile_sha256", ""),  # E005
        "current_stage": ctx["current_stage"],
        "ledger_sequence": ledger.ledger_sequence}, "ledger": ledger}
