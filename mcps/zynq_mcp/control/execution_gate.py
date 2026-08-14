"""
execution_gate.py — Atomic preflight mutator. P10 dedup before P1.
P4 generation check embedded in P1 (before CHANNEL_BUSY). P7 with evidence.
P9 honest: Ledger resource record check only (real lock probe deferred to adapters).
"""
import time
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_NON_TERMINAL, OP_TERMINAL, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT,
    WORKER_STATE_ABSENT, WORKER_STATE_ORPHANED, WORKER_STATE_DEAD,
    ChannelBusyError, _now_iso, operation_contract_fields,
)
from mcps.zynq_mcp.control.operation_service import InFlightDuplicateError, TerminalDuplicateError
from mcps.zynq_mcp.control.process_guard import is_pid_alive, get_process_identity


def preflight_mutator(tool_name, arguments, session_id, board_id, project_path, op_id, signature):
    return lambda ledger: _gate(ledger, tool_name, arguments, session_id, board_id, project_path, op_id, signature)


def _gate(ledger, tool_name, arguments, session_id, board_id, project_path, op_id, sig):
    ctx = ledger.context or {}; wo = ledger.worker or {}
    cur_stage = ctx.get("current_stage", "IDLE"); rev = ctx.get("board_package_revision", "")
    ao = ledger.active_operation; po = ledger.previous_operation

    # ---- P10: dedup FIRST ----
    dr = ledger.dedup_registry or {}
    existing = dr.get(sig)
    if existing:
        if ao and ao.get("operation_id") == existing:
            if ao.get("status") in OP_NON_TERMINAL:
                raise InFlightDuplicateError(existing)
            raise TerminalDuplicateError(existing)
        if po and po.get("operation_id") == existing:
            raise TerminalDuplicateError(existing)

    # ---- P4: worker_generation BEFORE P1 ----
    if ao and ao.get("status") in OP_NON_TERMINAL:
        op_gen = ao.get("worker_generation", -1)
        w_gen = wo.get("worker_generation", -2)
        if op_gen != -1 and w_gen != -2 and op_gen != w_gen:
            raise ChannelBusyError("WORKER_GENERATION_STALE")

    # ---- P1: active operation ----
    if ao and ao.get("status") in OP_NON_TERMINAL:
        raise ChannelBusyError("CHANNEL_BUSY")

    # ---- P6: previous unresolved (skip if resolved_by_recovery) ----
    if po and po.get("status") in (OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT):
        if not po.get("resolved_by_recovery"):
            raise ChannelBusyError("PREVIOUS_OPERATION_UNRESOLVED")

    # ---- P2: worker PID alive ----
    pid = wo.get("pid")
    if pid and pid > 0 and wo.get("state") not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD):
        if not is_pid_alive(pid): raise ChannelBusyError("WORKER_PID_DEAD")

    # ---- P3: full process identity ----
    if pid and pid > 0 and is_pid_alive(pid):
        ident = get_process_identity(pid)
        if ident is None: raise ChannelBusyError("WORKER_IDENTITY_UNVERIFIABLE")
        expected_start = wo.get("process_start_time")
        expected_exe = wo.get("executable_path")
        if expected_start is not None and abs(ident.process_start_time - expected_start) > 5.0:
            raise ChannelBusyError("WORKER_IDENTITY_MISMATCH")
        if expected_exe is not None and ident.executable_path != expected_exe:
            raise ChannelBusyError("WORKER_IDENTITY_MISMATCH")

    # ---- P5: heartbeat (asks for the process) ----
    # B11 阶段③.1 (D4): P5 no longer double-counts the process. P2 above has
    # just proven the PID is alive and P3 has proven the full 5-field identity
    # matches — the worker process demonstrably exists. A STALE heartbeat
    # timestamp on an otherwise verified-alive worker is an idle condition, not
    # a rejection: the SingleWorkerController refreshes the timestamp on every
    # heartbeat tick, and when no operation is active the heartbeat may be
    # legitimately old. So staleness alone never blocks admission (it is
    # recorded on the worker record as ``last_heartbeat_stale_s`` for
    # observability). Missing or unparseable heartbeat timestamps still fail
    # closed (WORKER_HEARTBEAT_MISSING / WORKER_HEARTBEAT_UNREADABLE): they
    # carry no evidence the worker was ever heartbeat-maintained.
    #
    # Runtime-operation fail-closedness is UNAFFECTED: heartbeat freshness
    # during an active operation is the responsibility of the execution
    # observer / process controller (stale during an operation → 
    # RECOVERY_REQUIRED), not of this admission gate.
    if pid and pid > 0 and wo.get("state") not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD):
        hb = wo.get("last_heartbeat_at")
        if not hb: raise ChannelBusyError("WORKER_HEARTBEAT_MISSING")
        try:
            hb_ts = _parse_iso(hb)
            if hb_ts <= 0: raise ValueError("bad heartbeat timestamp")
        except Exception: raise ChannelBusyError("WORKER_HEARTBEAT_UNREADABLE")
        age = time.time() - hb_ts
        if age > 120.0:
            # Diagnostic only — process liveness was already proven by P2/P3.
            ledger.worker["last_heartbeat_stale_s"] = round(age, 1)

    # ---- P7: workflow stage (with evidence for synthesis/implement/timing) ----
    if _check_stage(tool_name, cur_stage, po):
        raise ChannelBusyError("STAGE_PREREQUISITE_UNMET")

    # ---- P8: Board/Artifact revision ----
    if tool_name.startswith("pl_") or tool_name.startswith("platform_") or tool_name.startswith("ps_"):
        if not rev: raise ChannelBusyError("REVISION_MISMATCH")
        expected = ctx.get("expected_board_revision", "")
        if not expected: raise ChannelBusyError("BOARD_REVISION_UNKNOWN")
        bid = ctx.get("board_id", "")
        if bid:
            from mcps.zynq_mcp.control.session import verify_board_revision
            verify_board_revision(bid, expected)

    # ---- P9: Ledger resource record check only ----
    if wo.get("project_lease_held"): raise ChannelBusyError("PROJECT_LOCK_BUSY")
    if wo.get("jtag_lease_held"): raise ChannelBusyError("JTAG_LOCK_BUSY")
    if wo.get("serial_owner"): raise ChannelBusyError("SERIAL_PORT_BUSY")
    if wo.get("state") == WORKER_STATE_ORPHANED: raise ChannelBusyError("RESOURCE_ORPHANED")

    # ---- ADMIT ----
    ledger.execution_lane = EXECUTION_LANE_BUSY
    accepted_at = _now_iso()
    ledger.active_operation = {
        "operation_id": op_id, "tool_name": tool_name, "status": OP_ACCEPTED,
        "api_category": "command", "session_id": session_id,
        "board_id": board_id, "project_path": project_path,
        "workflow_stage": cur_stage, "request_signature": sig,
        "worker_generation": wo.get("worker_generation", 0),
        "input_artifact_revision": rev,
        "accepted_at": accepted_at, "started_at": None,
        "heartbeat_at": None, "finished_at": None,
        "output_artifact_revision": None, "completion_evidence": None,
        "error": None, "progress_pct": None,
        **operation_contract_fields(tool_name, accepted_at),
    }
    if not isinstance(ledger.dedup_registry, dict): ledger.dedup_registry = {}
    ledger.dedup_registry[sig] = op_id
    return ledger


def _check_stage(tool_name, current, prev_op):
    t = tool_name.lower()
    if "platform_export_manifest" in t:
        # B11 phase 2 decision (a): platform_export_manifest is the terminal
        # atom of the platform sequence and the ONLY stage-advancing platform
        # atom (PLATFORM_DESIGN → PL_GENERATE). It must only be admitted from
        # PLATFORM_DESIGN, otherwise a later-stage call would push the frozen
        # stage machine forward illegally.
        if current != "PLATFORM_DESIGN": return True
    elif "platform_generate_wrapper" in t:
        # Preserved gate (previously an incidental substring match of the
        # removed shortcut name): the BD wrapper is only regenerated while the
        # platform design is still open — before platform_export_manifest
        # locks the manifest and advances to PL_GENERATE.
        if current != "PLATFORM_DESIGN": return True
    elif "platform_assign_addresses" in t or "platform_make_external" in t \
            or "platform_synthesize" in t:
        # B11 阶段③.1 (D1/D2/D3): the three added platform atoms (address
        # assignment, port externalization, synthesis) are BD-design steps —
        # admitted only in PLATFORM_DESIGN, before platform_export_manifest
        # advances the frozen stage machine. None of them advances the stage.
        if current != "PLATFORM_DESIGN": return True
    elif "pl_generate_system_top" in t:
        if current != "PL_GENERATE": return True  # E003: PLATFORM_DESIGN→PL_BUILD skip rejected
    elif "pl_create_project" in t:
        if current not in ("PL_GENERATE", "PL_BUILD"): return True
    elif "pl_synthesize" in t:
        # B07: pl_synthesize advances PL_BUILD → PL_IMPLEMENT. Re-synthesis is
        # only legal after ROLLBACK_FIX back to PL_BUILD (B04_single_channel_audit
        # §4.3); admitting it from a later stage would leave a stuck RUNNING op
        # when the (strict-serial) stage advance fails.
        if current != "PL_BUILD": return True
    elif "pl_place" in t and "pl_place_and_route" not in t:
        # B07 bridge: pl_place is the placement half of implementation; it runs
        # inside PL_BUILD/PL_IMPLEMENT/PL_TIMING and does NOT advance the stage.
        if current not in ("PL_BUILD", "PL_IMPLEMENT", "PL_TIMING"): return True
    elif "pl_route" in t:
        # B07 bridge: pl_route completes place_and_route and advances
        # PL_IMPLEMENT → PL_TIMING, so it is only legal from PL_IMPLEMENT.
        if current != "PL_IMPLEMENT": return True
    elif "pl_place_and_route" in t:
        if current not in ("PL_IMPLEMENT", "PL_TIMING", "PL_BUILD"): return True
        # P7 evidence: must have synthesis SUCCEEDED
        if not prev_op or not prev_op.get("tool_name", "").startswith("pl_synthesize") or prev_op.get("status") != "SUCCEEDED":
            return True
    elif "pl_analyze_timing" in t:
        # B07: pl_analyze_timing advances PL_TIMING → PL_BITSTREAM, so it is
        # only legal from PL_TIMING (admitting it from PL_BITSTREAM would leave
        # a stuck RUNNING op when the strict-serial advance fails).
        if current != "PL_TIMING": return True
        # P7 evidence: must have the implementation step SUCCEEDED — the last
        # PL bridge step is pl_route (or the standalone pl_place / the combined
        # pl_place_and_route for a place-only run).
        pt = prev_op.get("tool_name", "") if prev_op else ""
        if not prev_op or not (pt.startswith("pl_place") or pt.startswith("pl_route")) or prev_op.get("status") != "SUCCEEDED":
            return True
    elif "pl_generate_bitstream" in t:
        if current not in ("PL_TIMING", "PL_BITSTREAM"): return True
        # P7 evidence: must have timing_met = true
        if not prev_op or not prev_op.get("tool_name", "").startswith("pl_analyze_timing"):
            return True
        ev = prev_op.get("completion_evidence") or {}
        if not ev.get("timing_met"): return True
    return False


def _parse_iso(iso):
    import datetime
    try:
        dt = datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception: return 0.0
