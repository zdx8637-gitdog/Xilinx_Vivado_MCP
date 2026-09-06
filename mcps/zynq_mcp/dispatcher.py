"""
dispatcher.py — Atomic dispatch. Query=shared read. Command=ledger_transaction.
Unknown tools return immediately. workspace_id validated on every read.

P0-2: Atomic close_session with CLOSING lane.
  1. ledger_transaction writes CLOSING (re-verifies session_id, active_op, instance).
  2. CLOSING blocks ALL other set/command calls → CHANNEL_BUSY.
  3. Cleanup: Worker shutdown → PID verify → Project lease → JTAG lease.
  4. Final ledger_transaction deletes context (re-verifies CLOSING state).
  Any step failure → RECOVERY_REQUIRED, Context retained, no false success.

P0-3: Real lease release via project_lock.list_leases_for_owner() +
       release_lease_safe(). Thread-safe, no private dict access.
"""
import asyncio, json, logging, time, uuid
from pathlib import Path

from mcps.common.tool_response import success, error
from mcps.common.project_lock import (
    list_leases_for_owner, release_lease_safe,
)
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_CLOSING,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_OUTCOME_UNKNOWN, OP_INTERRUPTED,
    OP_NON_TERMINAL, OP_TERMINAL,
    WORKER_STATE_ABSENT, WORKER_STATE_ORPHANED, WORKER_STATE_DEAD,
    ChannelBusyError, LedgerWriteError, LedgerWorkspaceMismatchError,
)
from mcps.zynq_mcp.control.execution_gate import preflight_mutator, _parse_iso
from mcps.zynq_mcp.control.process_guard import is_pid_alive
from mcps.zynq_mcp.control.operation_service import (
    InFlightDuplicateError, TerminalDuplicateError, request_signature,
    operation_public_view, channel_busy_details,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.recovery import diagnose_execution, recovery_mutator
from mcps.zynq_mcp.control.resource_registry import resource_public_view
from mcps.zynq_mcp.control.session import (
    create_session_mutator, close_session_mutator, handle_get_session_info,
)
from mcps.zynq_mcp.control.workflow import (
    workflow_rollback_mutator, workflow_resume_mutator,
)
from mcps.zynq_mcp.control.capabilities import build_capabilities, ALL_TOOLS, _PS_ALLOWED_ARGS
from mcps.zynq_mcp.domains.ps import (
    jtag_target, target_control, memory_access, target_recovery, ps_bsp,
    debug_session, uart_capture, uart_diagnostics, hw_server_start,
)
# B06 second batch: BSP/Build tool names run on the XSCT shell (XsctBridge)
# and keep project_path as a real argument. Single source in domain_runner.
from mcps.zynq_mcp.control.domain_runner import (
    _PS_PROJECT_PATH_TOOLS, _PS_UART_CAPTURE_TOOLS,
    _PS_UART_DIRECT_TOOLS,
    _JTAG_OBSERVATION_STEP, ResourceRequirement,
)
# B07: PL bridge tools (wrap old Vivado MCP tools via VivadoAdapter).
# PL_TOOL_MAP is the single source: tool name -> (bridge fn, call timeout).
from mcps.zynq_mcp.domains.pl.pl_bridge_tools import PL_TOOL_MAP
# B01 §5 Phase 4: cross-domain manifest consistency verification (query tool).
from mcps.zynq_mcp.domains.verification.consistency_check import verify_consistency
# B01 §5 Phase 6: Observation & Pass/Fail adjudication (query tool).
from mcps.zynq_mcp.domains.verification.observation import evaluate_observation
# B05-R2: Platform atomic APIs. PLATFORM_ATOM_MAP is the single source:
# tool name -> atom function. Command atoms route through the CommandRunner
# with the VivadoAdapter injected (_pl_adapter marker); query atoms are read
# directly by the dispatcher query handlers.
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    PLATFORM_ATOM_MAP,
    PLATFORM_ATOM_COMMAND_TOOL_NAMES,
    PLATFORM_ATOM_QUERY_TOOL_NAMES,
    PLATFORM_ATOM_CONTEXT_ARGS,
    PLATFORM_ATOM_TIMEOUT,
)
from mcps.zynq_mcp.domains.platform.platform_domain import PlatformError

logger = logging.getLogger("zynq_mcp.dispatcher")

# B07: names routed through the CommandRunner with the Vivado adapter injected
# as the first positional argument by domain_runner._execute.
_PL_BRIDGE_TOOL_NAMES = frozenset(PL_TOOL_MAP.keys())
_PL_BRIDGE_TIMEOUT = {n: t for n, (_f, t) in PL_TOOL_MAP.items()}

_QUERY_TOOLS = frozenset({
    "get_capabilities", "get_session_info", "get_operation_status",
    "wait_operation", "get_execution_state", "diagnose_execution",
    "verify_consistency", "evaluate_observation",
} | PLATFORM_ATOM_QUERY_TOOL_NAMES)  # B05-R2: platform_get_status, platform_list_ips
_COMMAND_TOOLS = frozenset({"create_session", "close_session", "recover_execution",
                            "workflow_rollback", "workflow_resume_from"})
# R3.1-C + B05 + B06 first batch (22 PS tools) + B06 second batch (11 BSP)
# + B01 §5 Phase 5 UART capture lifecycle (3 tools)
# + B01 §5 Phase 7 UART diagnostics (1 tool)
_PS_TOOL_NAMES = frozenset({
    "ps_connect_hw_server", "ps_disconnect_hw_server", "ps_list_targets",
    "ps_select_target", "ps_get_target_status", "ps_get_device_info",
    "ps_reset_target", "ps_ensure_arm_accessible", "ps_initialize_ps",
    "ps_load_hardware",
    "ps_run_target", "ps_halt_target",
    "ps_step_target", "ps_wait_for_state", "ps_reg_read", "ps_reg_write",
    "ps_mem_read", "ps_mem_write", "ps_recover_target", "ps_reconnect_target",
    "ps_clear_debug_session", "ps_diagnose_dap", "ps_read_uart",
    "ps_list_serial_ports",
    "ps_import_hardware", "ps_create_platform", "ps_create_bsp",
    "ps_update_hardware", "ps_get_bsp_status", "ps_create_app",
    "ps_add_sources", "ps_set_compiler_options", "ps_compile",
    "ps_get_build_status", "ps_read_elf_info",
    "ps_start_uart_capture", "ps_wait_uart_capture", "ps_stop_uart_capture",
    "ps_diagnose_uart_clock",
    # B12-N3 — local hw_server auto-start (process-free, no EDA worker)
    "ps_start_hw_server",
    # B06 third batch — download + debug (registered in capabilities, now routable)
    "ps_download_elf", "ps_write_uart",
    "ps_debug_start", "ps_debug_close",
    "ps_breakpoint_add", "ps_breakpoint_remove",
    "ps_read_register", "ps_write_register", "ps_stack_trace",
})
_DOMAIN_TOOLS = (frozenset({"pl_generate_system_top"})
                 | _PS_TOOL_NAMES | _PL_BRIDGE_TOOL_NAMES
                 | PLATFORM_ATOM_COMMAND_TOOL_NAMES)  # B05-R2: 12 platform command atoms
_ALL_KNOWN = _QUERY_TOOLS | _COMMAND_TOOLS | _DOMAIN_TOOLS

# R3.1-C: success-stage mapping from single source (domain_runner._PL_SUCCESS_STAGE)
from mcps.zynq_mcp.control.domain_runner import _PL_SUCCESS_STAGE as _DOMAIN_NEXT_STAGE


class ZynqDispatcher:
    def __init__(self, ledger, op_registry, guard, ledger_path, worker,
                 cmd_runner=None, process_controller=None):
        self._ledger = ledger; self._op_registry = op_registry
        self._guard = guard; self._ledger_path = ledger_path; self._worker = worker
        self._cmd_runner = cmd_runner
        self._process_controller = process_controller

    def schemas(self): return list(ALL_TOOLS)

    async def dispatch(self, tool_name: str, arguments: dict, is_primary: bool) -> list:
        if not isinstance(arguments, dict):
            return _text(error("arguments must be a JSON object", code="INVALID_ARGUMENT").to_dict())
        if tool_name not in _ALL_KNOWN:
            return _text(error(f"Unknown tool: {tool_name}", code="INVALID_ARGUMENT",
                               details={"reason_code": "UNKNOWN_TOOL"}).to_dict())

        wsid = self._guard.workspace_id if hasattr(self._guard, 'workspace_id') else None

        # ---- QUERY ----
        if tool_name in _QUERY_TOOLS:
            try:
                ledger, sha = ledger_read_shared(self._guard, self._ledger_path, wsid)
            except LedgerWorkspaceMismatchError as e:
                return _text(error(str(e), code="INTERNAL_ERROR",
                                   details={"reason_code": "WORKSPACE_MISMATCH"}).to_dict())
            except Exception as e:
                return _text(error(f"Ledger read failed: {e}", code="INTERNAL_ERROR").to_dict())
            self._ledger = ledger
            try:
                if tool_name == "wait_operation":
                    return _text(await _wait_operation(arguments, self))
                if tool_name == "verify_consistency":
                    return _text(await _verify_consistency_query(arguments))
                if tool_name == "evaluate_observation":
                    return _text(await _evaluate_observation_query(arguments))
                if tool_name in PLATFORM_ATOM_QUERY_TOOL_NAMES:
                    # B05-R2: platform query atoms read the open Vivado design
                    # directly (no CommandRunner, no stage advance).
                    return _text(await _platform_query(arguments, tool_name, self))
                h = _SYNC_QUERIES.get(tool_name)
                if h is None:
                    return _text(error(f"Unknown query: {tool_name}", code="INVALID_ARGUMENT").to_dict())
                return _text(h(arguments, self))
            except Exception as exc:
                logger.exception("Query error: %s", tool_name)
                return _text(error(str(exc), code="INTERNAL_ERROR").to_dict())

        # ---- COMMAND ----
        # P0-2: Every command must read fresh ledger and reject CLOSING.
        # Cannot rely on disp._ledger cache.
        try:
            fresh_ledger, _ = ledger_read_shared(self._guard, self._ledger_path, wsid)
        except Exception:
            fresh_ledger = self._ledger
        if fresh_ledger.execution_lane == EXECUTION_LANE_CLOSING:
            return _text(_channel_busy(ChannelBusyError("CHANNEL_CLOSING"), fresh_ledger))

        try:
            if tool_name == "create_session":
                return _text(_create_session(arguments, self))
            if tool_name == "close_session":
                return _text(await _close_session_atomic(arguments, self))
            if tool_name == "recover_execution":
                return _text(await _recover_execution(self))
            if tool_name == "workflow_rollback":
                return _text(_workflow_rollback(arguments, self))
            if tool_name == "workflow_resume_from":
                return _text(_workflow_resume_from(arguments, self))
            # B06: PS domain tools route to _dispatch_ps (local, bridge-based)
            if tool_name.startswith("ps_"):
                return _text(await _dispatch_ps(arguments, tool_name, self))
            # R3.1-C: domain tool dispatch via CommandRunner
            if tool_name in _DOMAIN_TOOLS:
                return _text(await _domain_command_runner(arguments, tool_name, self))
            return _text(_domain_command(arguments, tool_name, self))
        except Exception as exc:
            logger.exception("Command error: %s", tool_name)
            return _text(error(str(exc), code="INTERNAL_ERROR").to_dict())


def _existing_project_artifacts_hint(project_path: str) -> dict:
    """B12 fix round #3 (item #3): detect already-produced project artifacts.

    Returns an advisory dict describing prior products under ``project_path``
    (a platform manifest, XSA, or bitstream). Purely informative — it does NOT
    change the workflow stage or any gate, so a re-create_session on a project
    that still holds products gets an explicit prompt instead of silently
    restarting at PLATFORM_DESIGN. Empty dict when nothing (or nothing
    recognizable) is found (fail-closed: no false "resume").
    """
    try:
        root = Path(project_path)
    except (TypeError, ValueError):
        return {}
    if not root.is_dir():
        return {}
    hints = {}
    plat_dir = root / "manifests" / "platform"
    if plat_dir.is_dir() and any(plat_dir.glob("sha256_*.json")):
        hints["platform_manifest"] = True
    if (root / "platform.xsa").is_file():
        hints["platform_xsa"] = True
    if (root / "bitstream").is_dir() and any(
            (root / "bitstream").glob("*.bit")):
        hints["bitstream"] = True
    return hints


def _create_session(args, disp):
    bid = args.get("board_id"); pp = args.get("project_path")
    if not isinstance(bid, str) or not isinstance(pp, str) or not bid.strip() or not pp.strip():
        return error("board_id and project_path must be non-empty strings", code="INVALID_ARGUMENT").to_dict()
    op_id = f"op-{uuid.uuid4().hex}"
    sig = request_signature("", "IDLE", "create_session", args, "")
    commit = create_session_mutator(args, disp._guard.instance_id, op_id, sig)
    try: ledger = commit(disp._guard, disp._ledger_path)
    except ChannelBusyError as e: return _channel_busy(e, disp._ledger)
    except Exception as e: return error(str(e), code="INTERNAL_ERROR").to_dict()
    ctx = ledger.context
    disp._ledger = ledger
    resume_hint = _existing_project_artifacts_hint(ctx["project_path"])
    result = {"session_id": ctx["session_id"], "board_id": ctx["board_id"],
        "project_path": ctx["project_path"], "board_package_revision": ctx["board_package_revision"],
        "board_profile_sha256": ctx.get("board_profile_sha256", ""),  # E005
        "current_stage": ctx["current_stage"], "ledger_sequence": ledger.ledger_sequence}
    if resume_hint:
        result["resume_hint"] = resume_hint
    return success(result, context_ref=ctx["session_id"]).to_dict()


def _workflow_rollback(args, disp):
    sid = args.get("session_id"); target = args.get("target_stage")
    if not isinstance(sid, str) or not isinstance(target, str) or not sid.strip() or not target.strip():
        return error("session_id and target_stage must be non-empty strings",
                     code="INVALID_ARGUMENT").to_dict()
    try:
        commit = workflow_rollback_mutator(args)
        ledger = commit(disp._guard, disp._ledger_path)
    except ChannelBusyError as e:
        return error(f"Cannot rollback: {e.args[0]}", code="LOCK_BUSY",
                     details={"reason_code": e.args[0]}).to_dict()
    except Exception as e:
        return error(str(e), code="INTERNAL_ERROR").to_dict()
    disp._ledger = ledger
    ctx = ledger.context
    return success({"session_id": ctx["session_id"],
                    "current_stage": ctx["current_stage"],
                    "platform_revision": ctx.get("platform_revision"),
                    "pl_revision": ctx.get("pl_revision"),
                    "ps_revision": ctx.get("ps_revision"),
                    "workflow_history": ctx.get("workflow_history"),
                    "ledger_sequence": ledger.ledger_sequence},
                   context_ref=ctx["session_id"]).to_dict()


def _workflow_resume_from(args, disp):
    sid = args.get("session_id"); target = args.get("target_stage")
    if not isinstance(sid, str) or not isinstance(target, str) or not sid.strip() or not target.strip():
        return error("session_id and target_stage must be non-empty strings",
                     code="INVALID_ARGUMENT").to_dict()
    try:
        commit = workflow_resume_mutator(args)
        ledger = commit(disp._guard, disp._ledger_path)
    except ChannelBusyError as e:
        return error(f"Cannot resume: {e.args[0]}", code="LOCK_BUSY",
                     details={"reason_code": e.args[0]}).to_dict()
    except Exception as e:
        return error(str(e), code="INTERNAL_ERROR").to_dict()
    disp._ledger = ledger
    ctx = ledger.context
    return success({"session_id": ctx["session_id"],
                    "current_stage": ctx["current_stage"],
                    "workflow_history": ctx.get("workflow_history"),
                    "ledger_sequence": ledger.ledger_sequence},
                   context_ref=ctx["session_id"]).to_dict()


async def _close_session_atomic(args, disp):
    """P0-2: Atomic close_session protocol.

    Step 0: Validate args
    Step 1: ledger_transaction writes CLOSING (re-verifies session_id,
            no active operation, instance is primary, execution_lane not already CLOSING).
    Step 2: Stop UART resources, direct EDA backend, then legacy Worker.
    Step 3: Release Project leases → release JTAG leases (via thread-safe public API).
    Step 4: Final ledger_transaction deletes context (re-verifies lane is still CLOSING).
    Any failure → RECOVERY_REQUIRED, Context retained.
    """
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return error("session_id must be a non-empty string", code="INVALID_ARGUMENT").to_dict()

    completed = []
    incomplete = []

    # ============ Step 1: Atomic write CLOSING ============
    def _enter_closing(current):
        """Re-verify in transaction: session_id, active op, instance, lane."""
        cur_sid = current.context.get("session_id", "")
        if not cur_sid:
            raise ChannelBusyError("NO_ACTIVE_SESSION")
        if cur_sid != sid:
            raise ChannelBusyError("SESSION_ID_MISMATCH")
        ao = current.active_operation
        if ao and ao.get("status") in OP_NON_TERMINAL:
            raise ChannelBusyError("ACTIVE_OPERATION_PRESENT")
        # Also reject if lane=BUSY without active_operation (Set is running)
        if current.execution_lane == EXECUTION_LANE_BUSY and not (ao and ao.get("status") in OP_NON_TERMINAL):
            raise ChannelBusyError("ACTIVE_SET_PRESENT")
        if current.execution_lane == EXECUTION_LANE_CLOSING:
            raise ChannelBusyError("ALREADY_CLOSING")
        current.execution_lane = EXECUTION_LANE_CLOSING
        return current

    try:
        ledger = ledger_transaction(disp._guard, disp._ledger_path, _enter_closing)
        disp._ledger = ledger
        completed.append("lane_entered_closing")
    except ChannelBusyError as e:
        return error(f"Cannot close: {e.args[0]}", code="LOCK_BUSY",
                     details={"reason_code": e.args[0]}).to_dict()
    except Exception as e:
        return error(f"CLOSING transition failed: {e}", code="INTERNAL_ERROR").to_dict()

    # ============ Step 2a: Stop persistent UART resources ============
    if disp._cmd_runner is not None:
        try:
            uart_ok = await disp._cmd_runner.shutdown_uart_resources()
            if not uart_ok:
                incomplete.append("uart_resource_shutdown_failed")
                await _close_failed(disp, sid, "UART_CLEANUP_FAILED",
                                    completed, incomplete)
                return error("UART cleanup incomplete — session not closed",
                             code="INTERNAL_ERROR",
                             details={"reason_code": "UART_CLEANUP_FAILED",
                                      "session_id": sid,
                                      "completed": completed,
                                      "incomplete": incomplete}).to_dict()
            completed.append("uart_resources_stopped")
        except Exception as e:
            incomplete.append(f"uart_resource_shutdown_exception:{e}")
            await _close_failed(disp, sid, "UART_CLEANUP_FAILED",
                                completed, incomplete)
            return error("UART cleanup incomplete — session not closed",
                         code="INTERNAL_ERROR",
                         details={"reason_code": "UART_CLEANUP_FAILED",
                                  "session_id": sid,
                                  "completed": completed,
                                  "incomplete": incomplete}).to_dict()

    # ============ Step 2b: Shutdown controller-owned EDA backend ============
    controller = getattr(disp, "_process_controller", None)
    if controller is not None and controller.has_backend:
        try:
            direct = await controller.shutdown_backend(force=True)
            if not direct.success or not direct.pid_cleaned or not direct.supervisor_cleaned:
                incomplete.append(f"backend_shutdown_failed:{direct.reason_code}")
                await _close_failed(disp, sid, "BACKEND_SHUTDOWN_FAILED",
                                    completed, incomplete)
                return error("Backend cleanup incomplete — session not closed",
                             code="INTERNAL_ERROR",
                             details={"reason_code": "BACKEND_SHUTDOWN_FAILED",
                                      "session_id": sid,
                                      "completed": completed,
                                      "incomplete": incomplete}).to_dict()
            completed.append("direct_backend_shutdown")
        except Exception as e:
            incomplete.append(f"backend_shutdown_exception:{e}")
            await _close_failed(disp, sid, "BACKEND_SHUTDOWN_FAILED",
                                completed, incomplete)
            return error("Backend cleanup incomplete — session not closed",
                         code="INTERNAL_ERROR",
                         details={"reason_code": "BACKEND_SHUTDOWN_FAILED",
                                  "session_id": sid,
                                  "completed": completed,
                                  "incomplete": incomplete}).to_dict()
    else:
        completed.append("direct_backend_shutdown_nop")

    # ============ Step 2c: Shutdown legacy Worker ============
    worker_ok = False
    pid_before = None
    has_worker = disp._worker._adapter is not None

    if has_worker:
        try:
            pid_before = disp._worker._adapter.child_pid
            sw_result = await disp._worker.shutdown()
            if sw_result.get("success") and sw_result.get("pid_cleaned"):
                worker_ok = True
                completed.append("worker_shutdown")
                if pid_before and pid_before > 0:
                    from mcps.zynq_mcp.control.process_guard import is_pid_alive
                    if is_pid_alive(pid_before):
                        incomplete.append(f"pid_still_alive:{pid_before}")
                        worker_ok = False
            else:
                incomplete.append(f"worker_shutdown_failed:{sw_result.get('error','unknown')}")
        except Exception as e:
            incomplete.append(f"worker_shutdown_exception:{e}")

        if not worker_ok:
            await _close_failed(disp, sid, "WORKER_SHUTDOWN_FAILED", completed, incomplete)
            return error("Worker shutdown incomplete — session not closed",
                         code="INTERNAL_ERROR",
                         details={"reason_code": "WORKER_SHUTDOWN_FAILED",
                                  "session_id": sid,
                                  "completed": completed,
                                  "incomplete": incomplete}).to_dict()
    else:
        worker_ok = True
        completed.append("worker_shutdown_nop")

    # ============ Step 3: Release leases (Project before JTAG) ============
    lease_ok = True
    try:
        session_leases = list_leases_for_owner(sid)
        if session_leases:
            for lease in session_leases:
                ok, msg = release_lease_safe(lease)
                if not ok:
                    incomplete.append(f"lease_release_failed:{lease.lease_id}:{msg}")
                    lease_ok = False
                else:
                    completed.append(f"lease_released:{lease.lease_id}")
        else:
            completed.append("leases_released_nop")
    except Exception as e:
        incomplete.append(f"leases_exception:{e}")
        lease_ok = False

    if not lease_ok:
        # Lease release failure → RECOVERY_REQUIRED, Context retained
        await _close_failed(disp, sid, "LEASE_RELEASE_FAILED", completed, incomplete)
        return error("Lease release failed — session not closed",
                     code="INTERNAL_ERROR",
                     details={"reason_code": "LEASE_RELEASE_FAILED",
                              "session_id": sid,
                              "completed": completed,
                              "incomplete": incomplete}).to_dict()

    # ============ Step 4: Final — delete context ============
    def _exit_closing(current):
        """Re-verify CLOSING state, then delete context."""
        if current.execution_lane != EXECUTION_LANE_CLOSING:
            raise ChannelBusyError("LANE_STATE_DRIFT")
        cur_sid = current.context.get("session_id", "")
        if cur_sid != sid:
            raise ChannelBusyError("SESSION_ID_DRIFT")
        current.execution_lane = EXECUTION_LANE_IDLE
        current.context = {}
        return current

    try:
        ledger = ledger_transaction(disp._guard, disp._ledger_path, _exit_closing)
        disp._ledger = ledger
        completed.append("context_deleted")
    except ChannelBusyError as e:
        incomplete.append(f"context_delete_blocked:{e.args[0]}")
        await _close_failed(disp, sid, e.args[0], completed, incomplete)
        return error(f"Context delete blocked: {e.args[0]}",
                     code="LOCK_BUSY",
                     details={"reason_code": e.args[0],
                              "session_id": sid,
                              "completed": completed,
                              "incomplete": incomplete}).to_dict()
    except Exception as e:
        incomplete.append(f"context_delete_failed:{e}")
        await _close_failed(disp, sid, "CONTEXT_DELETE_FAILED", completed, incomplete)
        return error(f"Context delete failed: {e}",
                     code="INTERNAL_ERROR",
                     details={"reason_code": "CONTEXT_DELETE_FAILED",
                              "session_id": sid,
                              "completed": completed,
                              "incomplete": incomplete}).to_dict()

    return success({"closed": sid, "completed": completed,
                    "incomplete": incomplete if incomplete else []}).to_dict()


async def _close_failed(disp, sid, reason, completed, incomplete):
    """Write RECOVERY_REQUIRED to ledger. Context retained."""
    def _fail(l):
        l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
        l.recent_errors.append({"at": _now_iso(), "reason": reason,
                                "session_id": sid,
                                "completed": list(completed),
                                "incomplete": list(incomplete)})
        return l
    try:
        ledger = ledger_transaction(disp._guard, disp._ledger_path, _fail)
        disp._ledger = ledger
    except Exception as e:
        logger.error("Failed to write RECOVERY_REQUIRED on close failure: %s", e)


def _now_iso():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime()) + \
           f"{int(time.time()*1e6)%1000000:06d}Z"


def _alive_stale_revive_eligibility(ledger) -> bool:
    """B11 阶段③.1 (D4): is this the ALIVE+STALE idle deadlock?

    True when the worker process is alive, the lane is IDLE, there is no
    non-terminal active operation, and the heartbeat is stale/missing (an idle
    deadlock the old model could only escape via close_session). The recovery
    service layer may then revive the controller's heartbeat loop instead of
    refusing with RECOVERY_BLOCKED_WORKER_ALIVE.
    """
    w = ledger.worker or {}
    pid = w.get("pid")
    ao = ledger.active_operation
    if ledger.execution_lane != EXECUTION_LANE_IDLE:
        return False
    if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0):
        return False
    if w.get("state") in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD):
        return False
    if not is_pid_alive(pid):
        return False
    if ao and ao.get("status") in OP_NON_TERMINAL:
        # A live worker with a non-terminal active operation is still refused
        # (RECOVERY_BLOCKED_WORKER_ALIVE preserved): never touch a worker that
        # is mid-operation.
        return False
    hb = w.get("last_heartbeat_at")
    if not hb:
        # No heartbeat evidence at all — the loop is dead/never ran; reviving
        # it is exactly the takeover this path exists for.
        return True
    try:
        ts = _parse_iso(hb)
        return ts <= 0 or (time.time() - ts) > 120.0
    except Exception:
        return True  # unparseable heartbeat counts as stale → revivable


async def _revive_heartbeat(disp, ledger, op_id) -> dict:
    """Revive a live-but-stale worker's heartbeat without closing the session.

    Fail-closed: the revive is only possible when THIS controller actually
    holds the worker (same PID). If the process is alive but not held by the
    controller, no process operation is attempted — the caller gets the
    original RECOVERY_BLOCKED_WORKER_ALIVE error (the only safe way out is
    close_session). The recovery_log append stays atomic; the process
    operation (restart_heartbeat) happens here in the service layer, never
    inside a ledger mutator.
    """
    worker = getattr(disp, "_worker", None)
    pid = (ledger.worker or {}).get("pid")
    held = (worker is not None and getattr(worker, "has_worker", False)
            and getattr(getattr(worker, "_adapter", None), "child_pid", None) == pid)
    if not held:
        return error(
            f"Recovery blocked: worker process {pid} is alive but not held by "
            "this controller — heartbeat cannot be revived; use close_session "
            "to release the backend",
            code="INTERNAL_ERROR",
            details={"reason_code": "RECOVERY_BLOCKED_WORKER_ALIVE"}).to_dict()
    try:
        result = await worker.restart_heartbeat()
    except Exception as e:
        logger.exception("Heartbeat revive failed")
        return error(f"Heartbeat revive failed: {e}", code="INTERNAL_ERROR",
                     details={"reason_code": "HEARTBEAT_REVIVE_FAILED"}).to_dict()
    if not result.get("success"):
        return error(f"Heartbeat revive failed: {result.get('error')}",
                     code="INTERNAL_ERROR",
                     details={"reason_code": result.get("reason_code")
                              or "HEARTBEAT_REVIVE_FAILED"}).to_dict()

    def _log(l):
        if not isinstance(l.recovery_log, list):
            l.recovery_log = []
        l.recovery_log.append({
            "action": "heartbeat_revive", "result": "SUCCEEDED",
            "timestamp": _now_iso(),
            "worker_generation": (l.worker or {}).get("worker_generation", 0),
            "recovery_op_id": op_id,
        })
        return l

    try:
        ledger = ledger_transaction(disp._guard, disp._ledger_path, _log)
    except Exception as e:
        return error(f"Heartbeat revived but recovery log write failed: {e}",
                     code="INTERNAL_ERROR",
                     details={"reason_code": "LEDGER_WRITE_FAILED"}).to_dict()
    disp._ledger = ledger
    return success({
        "execution_lane": ledger.execution_lane,
        "worker_state": ledger.worker.get("state", WORKER_STATE_ABSENT),
        "worker_generation": ledger.worker.get("worker_generation", 0),
        "heartbeat_revived": True,
    }).to_dict()


async def _recover_execution(disp):
    op_id = f"op-{uuid.uuid4().hex}"
    # B11 阶段③.1 (D4): ALIVE+STALE takeover — worker process alive, lane
    # IDLE, no active operation, heartbeat stale (idle deadlock). Revive the
    # controller's heartbeat loop instead of refusing or forcing close_session.
    # The recovery_mutator itself stays pure atomic; the process operation
    # (restart_heartbeat) happens in this service layer.
    try:
        cur, _ = ledger_read_shared(
            disp._guard, disp._ledger_path,
            getattr(disp._guard, "workspace_id", None))
    except Exception:
        cur = None
    if cur is not None and _alive_stale_revive_eligibility(cur):
        return await _revive_heartbeat(disp, cur, op_id)
    try:
        ledger = ledger_transaction(disp._guard, disp._ledger_path, recovery_mutator(op_id))
    except ChannelBusyError as e:
        return error(f"Recovery blocked: {e.args[0]}", code="INTERNAL_ERROR",
                     details={"reason_code": e.args[0]}).to_dict()
    except Exception as e:
        return error(str(e), code="INTERNAL_ERROR").to_dict()
    disp._ledger = ledger
    return success({"execution_lane": ledger.execution_lane,
        "worker_state": ledger.worker.get("state", WORKER_STATE_ABSENT),
        "worker_generation": ledger.worker.get("worker_generation", 0)}).to_dict()


def _domain_command(args, tool_name, disp):
    sid = str(args.get("session_id", "")); bid = str(args.get("board_id", ""))
    pp = str(args.get("project_path", ""))
    stg = disp._ledger.context.get("current_stage", "IDLE")
    rev = disp._ledger.context.get("board_package_revision", "")
    sig = request_signature(sid, stg, tool_name, args, rev)
    op_id = f"op-{uuid.uuid4().hex}"
    mut = preflight_mutator(tool_name, args, sid, bid, pp, op_id, sig)
    try: ledger = ledger_transaction(disp._guard, disp._ledger_path, mut)
    except InFlightDuplicateError as e:
        return {"status":"success","data":{"operation_id":e.args[0],"deduplicated":True,
            "status":"RUNNING","recommended_action":"WAIT","poll_after_s":10}}
    except TerminalDuplicateError as e:
        return {"status":"error","error":{"code":"LOCK_BUSY",
            "message":f"Request already completed as {e.args[0]}. Use explicit retry.",
            "details":{"reason_code":"CONFIRM_RETRY_REQUIRED","previous_operation_id":e.args[0]}}}
    except ChannelBusyError as e: return _channel_busy(e, disp._ledger)
    except Exception as e: return error(str(e), code="INTERNAL_ERROR").to_dict()
    disp._ledger = ledger
    return success({"operation_id": op_id, "status": "accepted"}).to_dict()


# R3.1-C: contextual local handler for pl_generate_system_top
async def _pl_generate_local_fn(arguments, snapshot):
    """Production local executor. Async wrapper around frozen generate_system_top.
    Uses E006 snapshot for all context fields. Maps component exceptions per Contract D.
    Returns ToolResponse-style dict — CommandRunner handles transition.
    """
    from mcps.zynq_mcp.domains.pl.system_top import (
        generate_system_top,
        ManifestBindingError, WrapperParseError, PathSafetyError, AtomicWriteError,
    )
    import re as _re
    _REV_RE = _re.compile(r'^sha256:[0-9a-f]{64}$')
    wrapper_path = arguments.get("wrapper_path")  # raw — frozen _validate_contained rejects non-str
    project_path = str(snapshot.get("project_path", ""))
    board_profile_sha256 = str(snapshot.get("board_profile_sha256", ""))

    # Contract C: None/""/absent → missing; non-string → invalid; non-matching string → invalid
    raw_rev = snapshot.get("platform_revision")

    try:
        if raw_rev is None or (isinstance(raw_rev, str) and not raw_rev):
            raise ManifestBindingError("PLATFORM_MANIFEST_NOT_FOUND",
                "platform_revision missing or empty in execution snapshot")
        if not isinstance(raw_rev, str) or not _REV_RE.match(raw_rev):
            raise ManifestBindingError("INVALID_PLATFORM_REVISION",
                f"platform_revision={raw_rev!r}")
        platform_revision = raw_rev
        result = generate_system_top(
            wrapper_path=wrapper_path,
            project_path=project_path,
            platform_revision=platform_revision,
            board_profile_sha256=board_profile_sha256,
        )
    except (ManifestBindingError, WrapperParseError, PathSafetyError) as e:
        return {"status": "error", "error": {
            "code": "TOOL_ERROR", "message": str(e),
            "details": {"reason_code": e.reason_code}}}
    except AtomicWriteError as e:
        return {"status": "error", "error": {
            "code": "TOOL_ERROR", "message": str(e),
            "details": {"reason_code": "ATOMIC_WRITE_FAILED"}}}

    # Compact result — no full Verilog text or ports dict in Ledger
    return {"status": "success", "data": {
        "output_path": result["output_path"],
        "system_top_sha256": result["system_top_sha256"],
        "wrapper_module": result["wrapper_module"],
        "instance_name": result["instance_name"],
        "port_count": result["port_count"],
    }}

# Mark as contextual so CommandRunner passes snapshot
_pl_generate_local_fn._contextual = True


def _make_pl_bridge_local_fn(tool_name):
    """B07: build the local executor for a PL bridge tool.

    Returns an async function marked `_pl_bridge` so domain_runner._execute
    injects the VivadoTclBridge as the first positional argument (the direct
    `vivado -mode tcl` bridge; no old-MCP stdio middle layer). The bridge
    function itself performs all argument validation and envelope conversion
    (fail-closed). Simulation tools are still injected the old VivadoAdapter
    by _execute via _PL_OLD_ADAPTER_TOOLS.
    """
    bridge_fn = PL_TOOL_MAP[tool_name][0]

    async def _local(bridge, **arguments):
        return await bridge_fn(bridge, **arguments)

    _local._pl_bridge = True
    return _local


def _make_platform_atom_local_fn(tool_name):
    """B05-R2: build the local executor for a Platform atom tool.

    Marked `_pl_adapter` so domain_runner._execute injects the VivadoAdapter
    as the first positional argument (the same injection path as PL bridge
    tools). PlatformError raised by an atom is mapped to the standard
    error envelope (code TOOL_ERROR + stable reason_code) before it reaches
    the CommandRunner — the same envelope the platform domain has always
    used.
    """
    atom_fn = PLATFORM_ATOM_MAP[tool_name]

    async def _local(adapter, **arguments):
        try:
            return await atom_fn(adapter, **arguments)
        except PlatformError as e:
            return {"status": "error", "error": {
                "code": "TOOL_ERROR", "message": str(e),
                "details": {"reason_code": e.reason_code}}}

    _local._pl_adapter = True
    return _local


async def _platform_query(arguments, tool_name, disp):
    """B05-R2: query handler for platform_get_status / platform_list_ips.

    Pure read of the open Vivado design through the atom function. The
    VivadoAdapter is started lazily via the worker controller; an absent or
    unstartable worker fails closed with ADAPTER_NOT_READY. Requires an active
    session and rejects the CLOSING lane (starting a worker mid-close would
    race the shutdown). Never advances the workflow stage.
    """
    ctx = disp._ledger.context or {}
    if not ctx.get("session_id"):
        return error("No active session", code="INVALID_ARGUMENT",
                     details={"reason_code": "NO_ACTIVE_SESSION"}).to_dict()
    if disp._ledger.execution_lane != EXECUTION_LANE_IDLE:
        reason = ("CHANNEL_CLOSING" if
                  disp._ledger.execution_lane == EXECUTION_LANE_CLOSING
                  else "CHANNEL_BUSY")
        return _channel_busy(ChannelBusyError(reason), disp._ledger)
    adapter = None
    controller = getattr(disp, "_process_controller", None)
    if controller is not None and controller.has_backend:
        try:
            from mcps.zynq_mcp.control.execution_ledger import BACKEND_VIVADO
            from mcps.zynq_mcp.control.vivado_execution_observer import (
                VivadoExecutionFacade,
            )
            if controller.backend == BACKEND_VIVADO:
                await controller.ensure_backend(BACKEND_VIVADO)
                adapter = VivadoExecutionFacade(
                    controller, None, disp._guard, disp._ledger_path,
                    observe_process=False)
        except Exception:
            adapter = None
    elif disp._worker is not None:
        # Historical non-server tests only.  Production never lazily starts a
        # backend from a query.
        try:
            adapter = await disp._worker.ensure_worker()
        except Exception:
            adapter = None
    if adapter is None:
        return error("Vivado worker not available", code="TOOL_ERROR",
                     details={"reason_code": "ADAPTER_NOT_READY"}).to_dict()
    atom_fn = PLATFORM_ATOM_MAP[tool_name]
    try:
        return await atom_fn(adapter, **arguments)
    except PlatformError as e:
        return {"status": "error", "error": {
            "code": "TOOL_ERROR", "message": str(e),
            "details": {"reason_code": e.reason_code}}}


async def _domain_command_runner(args, tool_name, disp):
    """R3.1-C: dispatch domain tool through CommandRunner with full lifecycle."""
    if disp._cmd_runner is None:
        return error("Domain command runner not configured", code="INTERNAL_ERROR",
                     details={"reason_code": "NO_DOMAIN_RUNNER"}).to_dict()
    # Read session from fresh ledger (already read above for CLOSING check)
    fresh_ledger, _ = ledger_read_shared(
        disp._guard, disp._ledger_path,
        disp._guard.workspace_id if hasattr(disp._guard, 'workspace_id') else None)
    ctx = fresh_ledger.context or {}
    sid = ctx.get("session_id", "")
    bid = ctx.get("board_id", "")
    pp = ctx.get("project_path", "")
    next_stage = _DOMAIN_NEXT_STAGE.get(tool_name)

    # Select local executor function
    if tool_name == "pl_generate_system_top":
        local_fn = _pl_generate_local_fn
        cmd_args = args
        cmd_timeout = 30.0
    elif tool_name in _PL_BRIDGE_TOOL_NAMES:
        # B07: PL bridge tools. session_id/board_id/project_path are
        # transport keys from the ledger context — strip them before the
        # bridge function runs (mirrors _dispatch_ps). The VivadoTclBridge is
        # injected by domain_runner._execute via the _pl_bridge marker.
        cmd_args = dict(args)
        cmd_args.pop("session_id", None)
        cmd_args.pop("board_id", None)
        cmd_args.pop("project_path", None)
        local_fn = _make_pl_bridge_local_fn(tool_name)
        # Outer wait must exceed the adapter call timeout for the longest
        # tool in this class (synth/place/route = 660s).
        cmd_timeout = _PL_BRIDGE_TIMEOUT[tool_name] + 60.0
        if tool_name == "pl_generate_bitstream":
            # O3 uses an observable impl_1 write_bitstream run; its operation
            # deadline must cover the 30 minute vendor run bound.
            cmd_timeout = 1860.0
    elif tool_name in PLATFORM_ATOM_COMMAND_TOOL_NAMES:
        # B05-R2: Platform command atoms. session_id/board_id/project_path are
        # session transport keys — strip them, then re-inject the context keys
        # each atom actually needs (PLATFORM_ATOM_CONTEXT_ARGS is the single
        # source). The VivadoAdapter is injected by domain_runner._execute via
        # the _pl_adapter marker. Atoms do not advance the stage except
        # platform_export_manifest, whose next_stage resolves to PL_GENERATE
        # from _DOMAIN_NEXT_STAGE (B11 phase 2 decision (a)); all others stay
        # at None — not in _DOMAIN_NEXT_STAGE.
        cmd_args = dict(args)
        cmd_args.pop("session_id", None)
        cmd_args.pop("board_id", None)
        cmd_args.pop("project_path", None)
        for ck in PLATFORM_ATOM_CONTEXT_ARGS.get(tool_name, ()):
            if ck == "project_path" and pp:
                cmd_args["project_path"] = pp
            elif ck == "board_id" and bid:
                cmd_args["board_id"] = bid
            elif ck == "board_profile_sha256" and ctx.get("board_profile_sha256"):
                cmd_args["board_profile_sha256"] = ctx["board_profile_sha256"]
        local_fn = _make_platform_atom_local_fn(tool_name)
        cmd_timeout = PLATFORM_ATOM_TIMEOUT[tool_name]
    else:
        return error(f"Unknown domain tool: {tool_name}", code="INVALID_ARGUMENT",
                     details={"reason_code": "UNKNOWN_TOOL"}).to_dict()

    resource_req = ResourceRequirement(type="NONE")
    if tool_name == "pl_program_fpga":
        resource_req = ResourceRequirement(type="JTAG_REQUIRE_OWNED")
    r = await disp._cmd_runner.run_command(
        tool_name=tool_name, arguments=cmd_args,
        session_id=sid, board_id=bid, project_path=pp,
        executor="local", local_fn=local_fn,
        timeout_s=cmd_timeout,
        next_stage=next_stage,
        resource_req=resource_req,
    )
    return r


# ── B06: PS domain dispatch ────────────────────────────────────────────────
_PS_UART_DEFAULT_BAUD = 115200
_PS_UART_DEFAULT_DURATION_MS = 5000


def _uart_error(reason_code: str, message: str, *, code: str = "UART_ERROR") -> dict:
    """Fail-closed UART error envelope with a stable top-level ErrorCode."""
    return error(message=message, code=code,
                 details={"reason_code": reason_code}).to_dict()


async def _ps_read_uart_wrapper(bridge, *, port=None, baudrate=_PS_UART_DEFAULT_BAUD,
                                duration_ms=_PS_UART_DEFAULT_DURATION_MS):
    """Local executor for ps_read_uart.

    `bridge` is passed by the CommandRunner for the uniform ps_* calling
    convention; UART observation does not use the XSDB shell. SerialAdapter
    is imported lazily so a missing pyserial never crashes the server import.
    """
    from mcps.zynq_mcp.adapters.uart import SerialAdapter, SerialAdapterError
    if not isinstance(port, str) or not port.strip():
        return _uart_error("INVALID_ARGUMENT", "port must be a non-empty string",
                           code="INVALID_ARGUMENT")
    if isinstance(baudrate, bool) or not isinstance(baudrate, int) or baudrate <= 0:
        return _uart_error("INVALID_ARGUMENT", "baudrate must be a positive integer",
                           code="INVALID_ARGUMENT")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms <= 0:
        return _uart_error("INVALID_ARGUMENT", "duration_ms must be a positive integer",
                           code="INVALID_ARGUMENT")
    adapter = SerialAdapter()
    try:
        adapter.open(port, baudrate)
    except SerialAdapterError as e:
        return _uart_error("SERIAL_OPEN_FAILED", f"open {port}: {e}")
    try:
        data = await asyncio.to_thread(adapter.read, duration_ms)
        return success(data={
            "port": port, "baudrate": baudrate,
            "bytes_read": len(data),
            "data_hex": data.hex(),
            "text": data.decode("utf-8", errors="replace"),
        }).to_dict()
    except SerialAdapterError as e:
        return _uart_error("SERIAL_READ_FAILED", f"read {port}: {e}")
    finally:
        # Best-effort close; the primary read result is already produced.
        try:
            adapter.close()
        except Exception as e:
            logger.warning("ps_read_uart best-effort close failed: %s", e)


async def _ps_list_serial_ports_wrapper(bridge):
    """Local executor for ps_list_serial_ports."""
    from mcps.zynq_mcp.adapters.uart import SerialAdapter
    try:
        ports = SerialAdapter.list_ports()
    except Exception as e:
        return _uart_error("SERIAL_LIST_FAILED", f"list serial ports: {e}")
    return success(data={"ports": ports, "count": len(ports)}).to_dict()


async def _ps_write_uart_wrapper(bridge, *, port=None, baudrate=_PS_UART_DEFAULT_BAUD,
                                   data=None, encoding="utf-8"):
    """Local executor for ps_write_uart.

    encoding: "utf-8" (default, text) or "hex" (binary downlink frames --
    the data string is hex-decoded after stripping whitespace, e.g.
    "a55a 01 85 000f4240a5d132"; payload bytes need not be valid text).
    The hex channel closes the binary-downlink gap for wire protocols.
    """
    from mcps.zynq_mcp.adapters.uart import SerialAdapter, SerialAdapterError
    if not isinstance(port, str) or not port.strip():
        return _uart_error("INVALID_ARGUMENT", "port must be a non-empty string",
                           code="INVALID_ARGUMENT")
    if not isinstance(data, str) and not isinstance(data, bytes):
        return _uart_error("INVALID_ARGUMENT", "data must be str or bytes",
                           code="INVALID_ARGUMENT")
    if encoding not in ("utf-8", "hex"):
        return _uart_error("INVALID_ARGUMENT", "encoding must be 'utf-8' or 'hex'",
                           code="INVALID_ARGUMENT")
    payload = data
    if encoding == "hex":
        if isinstance(data, bytes):
            return _uart_error("INVALID_ARGUMENT",
                               "hex encoding requires a str data argument",
                               code="INVALID_ARGUMENT")
        compact = "".join(data.split())
        try:
            payload = bytes.fromhex(compact)
        except ValueError:
            return _uart_error(
                "INVALID_ARGUMENT",
                "hex data must be an even-length hex string (whitespace tolerated)",
                code="INVALID_ARGUMENT")
        if not payload:
            return _uart_error("INVALID_ARGUMENT", "hex data must not be empty",
                               code="INVALID_ARGUMENT")
    adapter = SerialAdapter()
    try:
        adapter.open(port, baudrate)
    except SerialAdapterError as e:
        return _uart_error("SERIAL_OPEN_FAILED", f"open {port}: {e}")
    try:
        n = adapter.write(payload)
        return success(data={"port": port, "bytes_written": n}).to_dict()
    except SerialAdapterError as e:
        return _uart_error("SERIAL_WRITE_FAILED", f"write {port}: {e}")
    finally:
        try:
            adapter.close()
        except Exception as e:
            logger.warning("ps_write_uart best-effort close failed: %s", e)


# 22 tool names -> (module, function_name) or a local wrapper callable.
_PS_TOOL_MAP = {
    "ps_connect_hw_server": (jtag_target, "connect_hw_server"),
    "ps_disconnect_hw_server": (jtag_target, "disconnect_hw_server"),
    "ps_list_targets": (jtag_target, "list_targets"),
    "ps_select_target": (jtag_target, "select_target"),
    "ps_get_target_status": (jtag_target, "get_target_status"),
    "ps_get_device_info": (jtag_target, "get_device_info"),
    "ps_reset_target": (target_control, "reset_target"),
    "ps_ensure_arm_accessible": (target_control, "ensure_arm_accessible"),
    "ps_initialize_ps": (target_control, "initialize_ps"),
    "ps_load_hardware": (target_control, "load_hardware"),
    "ps_run_target": (target_control, "run_target"),
    "ps_halt_target": (target_control, "halt_target"),
    "ps_step_target": (target_control, "step_target"),
    "ps_wait_for_state": (target_control, "wait_for_state"),
    "ps_reg_read": (memory_access, "reg_read"),
    "ps_reg_write": (memory_access, "reg_write"),
    "ps_mem_read": (memory_access, "mem_read"),
    "ps_mem_write": (memory_access, "mem_write"),
    "ps_recover_target": (target_recovery, "recover_target"),
    "ps_reconnect_target": (target_recovery, "reconnect_target"),
    "ps_clear_debug_session": (target_recovery, "clear_debug_session"),
    "ps_diagnose_dap": (target_recovery, "diagnose_dap"),
    "ps_read_uart": _ps_read_uart_wrapper,
    "ps_list_serial_ports": _ps_list_serial_ports_wrapper,
    # B01 §5 Phase 5 — UART capture lifecycle (start → wait → stop).
    # Capture state persists in the uart_capture module; the XsdbBridge is
    # passed by the CommandRunner but unused (UART is an independent port).
    "ps_start_uart_capture": (uart_capture, "start_uart_capture"),
    "ps_wait_uart_capture": (uart_capture, "wait_uart_capture"),
    "ps_stop_uart_capture": (uart_capture, "stop_uart_capture"),
    # B01 §5 Phase 7 — UART diagnosis cascade. Reads SLCR clock-control and
    # UART baud registers, computes the actual baud rate. Requires the target
    # to be halted (the caller halts before calling).
    "ps_diagnose_uart_clock": (uart_diagnostics, "diagnose_uart_clock"),
    # B12-N3 — local hw_server auto-start (process-free; bridge is always None)
    "ps_start_hw_server": (hw_server_start, "start_hw_server"),
    # B06 second batch — BSP/Build.  XSCT-backed tools keep project_path in
    # the forwarded arguments (see _dispatch_ps); ps_read_elf_info is a
    # process-free pure-Python parser and intentionally does not.
    "ps_import_hardware": (ps_bsp, "import_hardware"),
    "ps_create_platform": (ps_bsp, "create_platform"),
    "ps_create_bsp": (ps_bsp, "create_bsp"),
    "ps_update_hardware": (ps_bsp, "update_hardware"),
    "ps_get_bsp_status": (ps_bsp, "get_bsp_status"),
    "ps_create_app": (ps_bsp, "create_app"),
    "ps_add_sources": (ps_bsp, "add_sources"),
    "ps_set_compiler_options": (ps_bsp, "set_compiler_options"),
    "ps_compile": (ps_bsp, "compile_app"),
    "ps_get_build_status": (ps_bsp, "get_build_status"),
    "ps_read_elf_info": (ps_bsp, "read_elf_info"),
    # Third batch — download + debug (B06 library phase, registered post B05 freeze)
    "ps_download_elf": (target_control, "download_elf"),
    "ps_write_uart": _ps_write_uart_wrapper,
    "ps_debug_start": (debug_session, "debug_start"),
    "ps_debug_close": (debug_session, "debug_close"),
    "ps_breakpoint_add": (debug_session, "breakpoint_add"),
    "ps_breakpoint_remove": (debug_session, "breakpoint_remove"),
    "ps_read_register": (debug_session, "read_register"),
    "ps_write_register": (debug_session, "write_register"),
    "ps_stack_trace": (debug_session, "stack_trace"),
}


async def _dispatch_ps(args, tool_name, disp):
    """B06: dispatch a ps_* tool through CommandRunner (local executor).

    session_id is extracted from arguments (required) and stripped before the
    domain function is invoked. board_id/project_path are read from the fresh
    ledger context. PS tools never advance the workflow stage (next_stage=None).
    """
    if disp._cmd_runner is None:
        return error("Domain command runner not configured", code="INTERNAL_ERROR",
                     details={"reason_code": "NO_DOMAIN_RUNNER"}).to_dict()
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return error("session_id must be a non-empty string", code="INVALID_ARGUMENT",
                     details={"reason_code": "SESSION_ID_REQUIRED"}).to_dict()
    entry = _PS_TOOL_MAP.get(tool_name)
    if entry is None:
        return error(f"Unknown PS tool: {tool_name}", code="INVALID_ARGUMENT",
                     details={"reason_code": "UNKNOWN_TOOL"}).to_dict()
    if isinstance(entry, tuple):
        local_fn = getattr(entry[0], entry[1])
    else:
        local_fn = entry

    fresh_ledger, _ = ledger_read_shared(
        disp._guard, disp._ledger_path,
        disp._guard.workspace_id if hasattr(disp._guard, 'workspace_id') else None)
    ctx = fresh_ledger.context or {}
    bid = ctx.get("board_id", "")
    pp = ctx.get("project_path", "")

    # Strip transport/control keys before forwarding to the domain function.
    # BSP/Build tools KEEP project_path: for the 4 setup tools
    # (import_hardware/create_platform/create_bsp/create_app) it is a real
    # argument (the XSCT workspace). For every other ps_* tool project_path is
    # session transport and must NOT be forwarded — forwarding it reaches a
    # domain signature that does not accept it → TypeError → OUTCOME_UNKNOWN
    # → P6 gate. B12 D-B: reject it deterministically with a stable
    # INVALID_ARGUMENT / UNSUPPORTED_ARGUMENT instead.
    ps_args = dict(args)
    ps_args.pop("session_id", None)
    ps_args.pop("board_id", None)
    # B12 D-B (extension): enforce the per-tool forwarded-argument contract so
    # an unsupported key (e.g. platform_name on ps_get_bsp_status) is rejected
    # deterministically BEFORE admission — never a TypeError → OUTCOME_UNKNOWN
    # → P6 gate. This generalises the project_path guard to every ps_* tool.
    allowed = _PS_ALLOWED_ARGS.get(tool_name)
    if allowed is not None:
        unsupported = [k for k in ps_args if k not in allowed]
        if unsupported:
            return error(
                f"unsupported argument(s) for {tool_name}: "
                f"{', '.join(sorted(unsupported))}; supported: "
                f"{', '.join(sorted(allowed)) or '(none)'}",
                code="INVALID_ARGUMENT",
                details={"reason_code": "UNSUPPORTED_ARGUMENT",
                         "unsupported": sorted(unsupported),
                         "supported": sorted(allowed)}).to_dict()
    else:
        # Fallback (unregistered/missed tool): keep the narrow project_path
        # guard only for the 4 genuine workspace tools; drop it otherwise so it
        # never reaches a domain signature as a TypeError.
        if tool_name in _PS_PROJECT_PATH_TOOLS:
            pass  # project_path is a genuine domain argument
        else:
            ps_args.pop("project_path", None)

    if tool_name == "ps_connect_hw_server":
        resource_req = ResourceRequirement(
            type="JTAG_ACQUIRE",
            lease_key=str(ps_args.get("url") or "localhost:3121"))
    elif tool_name == "ps_reset_target" and ps_args.get("scope", "processor") \
            not in ("processor", "system"):
        # Frozen B06 contract admits this deterministic argument error as an
        # Operation before the domain validator returns INVALID_SCOPE.
        resource_req = ResourceRequirement(type="NONE")
    elif tool_name in _JTAG_OBSERVATION_STEP:
        resource_req = ResourceRequirement(type="JTAG_REQUIRE_OWNED")
    elif tool_name == "ps_start_uart_capture":
        resource_req = ResourceRequirement(type="UART_ACQUIRE")
    elif tool_name in ("ps_wait_uart_capture", "ps_stop_uart_capture"):
        resource_req = ResourceRequirement(
            type="UART_REQUIRE_OWNED", lease_key=ps_args.get("capture_id"))
    elif tool_name in ("ps_read_uart", "ps_write_uart"):
        resource_req = ResourceRequirement(type="UART_ACQUIRE")
    else:
        resource_req = ResourceRequirement(type="NONE")

    return await disp._cmd_runner.run_command(
        tool_name=tool_name, arguments=ps_args,
        session_id=sid, board_id=bid, project_path=pp,
        executor="local", local_fn=local_fn,
        resource_req=resource_req,
        timeout_s=None, next_stage=None,
    )


def _get_capabilities(args, disp):
    role = "primary" if disp._guard.is_primary else "secondary"
    adapter_status = disp._worker.adapter_status if hasattr(disp, '_worker') else "absent"
    return success(build_capabilities(instance_role=role, adapter_status=adapter_status)).to_dict()

def _get_execution_state(args, disp):
    w = disp._ledger.worker or {}; ao = disp._ledger.active_operation or {}; po = disp._ledger.previous_operation or {}
    return success({"instance_role": "primary" if disp._guard.is_primary else "secondary",
        "instance_id": disp._guard.instance_id, "execution_lane": disp._ledger.execution_lane,
        "current_stage": disp._ledger.context.get("current_stage", "IDLE"),
        "ledger_sequence": disp._ledger.ledger_sequence,
        "active_operation": ao.get("operation_id") if ao else None,
        "active_operation_status": ao.get("status") if ao else None,
        "previous_operation": po.get("operation_id") if po else None,
        "previous_operation_status": po.get("status") if po else None,
        "worker_pid": disp._ledger.worker.get("pid"),
        "worker_state": disp._ledger.worker.get("state", "ABSENT"),
        "worker_generation": disp._ledger.worker.get("worker_generation", 0),
        "resources": resource_public_view(disp._ledger.worker)}).to_dict()

def _get_session_info(args, disp):
    sid = args.get("session_id")
    if not isinstance(sid, str) or not sid.strip():
        return error("session_id must be a non-empty string", code="INVALID_ARGUMENT").to_dict()
    wsid = disp._guard.workspace_id if hasattr(disp._guard, 'workspace_id') else None
    return handle_get_session_info(args, disp._guard, disp._ledger_path, wsid)

def _get_operation_status(args, disp):
    op_id = args.get("operation_id")
    if not isinstance(op_id, str) or not op_id.strip():
        return error("operation_id must be a non-empty string", code="INVALID_ARGUMENT").to_dict()
    ao = disp._ledger.active_operation; po = disp._ledger.previous_operation
    for src in (ao, po):
        if src and src.get("operation_id") == op_id:
            return success(operation_public_view(disp._ledger, src)).to_dict()
    # O1 C01: memory is cache only. An operation absent from Ledger is absent
    # from the public contract even when a stale cache entry remains.
    return error(f"Operation not found: {op_id}", code="OPERATION_NOT_FOUND",
                 details={"reason_code": "OPERATION_NOT_FOUND",
                          "operation_id": op_id}).to_dict()

def _classify_operation(ledger, op_id):
    for src in (ledger.active_operation, ledger.previous_operation):
        if src and src.get("operation_id") == op_id:
            if src.get("status") in OP_TERMINAL:
                return ("TERMINAL", dict(src))
            return ("NON_TERMINAL", dict(src))
    return ("NOT_FOUND", None)


async def _wait_operation(args, disp):
    op_id = args.get("operation_id")
    if not isinstance(op_id, str) or not op_id.strip():
        return error("operation_id required", code="INVALID_ARGUMENT").to_dict()
    wsid = disp._guard.workspace_id if hasattr(disp._guard, 'workspace_id') else None
    # Cap raised from 300s to 900s so the wait bound covers long vendor
    # operations (BD synthesis, bitstream write, long builds) that legitimately
    # run several minutes; the wait bound must exceed the operation duration.
    timeout_s = min(900.0, max(5.0, float(args.get("timeout_s", 30))))

    try:
        ledger, _ = ledger_read_shared(disp._guard, disp._ledger_path, wsid)
    except Exception as e:
        return error(f"Ledger read failed: {e}", code="INTERNAL_ERROR",
                     details={"reason_code": "LEDGER_READ_FAILED", "operation_id": op_id}).to_dict()

    kind, data = _classify_operation(ledger, op_id)
    if kind == "TERMINAL": return success(operation_public_view(ledger, data)).to_dict()
    if kind == "NOT_FOUND":
        return error(f"Operation not found: {op_id}", code="OPERATION_NOT_FOUND",
                     details={"reason_code": "OPERATION_NOT_FOUND", "operation_id": op_id}).to_dict()

    deadline = asyncio.get_running_loop().time() + timeout_s
    started_at = asyncio.get_running_loop().time()
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.5)
        try:
            ledger, _ = ledger_read_shared(disp._guard, disp._ledger_path, wsid)
        except Exception as e:
            return error(f"Ledger read failed: {e}", code="INTERNAL_ERROR",
                         details={"reason_code": "LEDGER_READ_FAILED", "operation_id": op_id}).to_dict()
        kind, data = _classify_operation(ledger, op_id)
        if kind == "TERMINAL": return success(operation_public_view(ledger, data)).to_dict()
        if kind == "NOT_FOUND":
            return error(f"Operation {op_id} disappeared from ledger",
                         code="INTERNAL_ERROR",
                         details={"reason_code": "OPERATION_STATE_LOST", "operation_id": op_id}).to_dict()

    try:
        ledger, _ = ledger_read_shared(disp._guard, disp._ledger_path, wsid)
    except Exception as e:
        return error(f"Ledger read failed: {e}", code="INTERNAL_ERROR",
                     details={"reason_code": "LEDGER_READ_FAILED", "operation_id": op_id}).to_dict()

    kind, data = _classify_operation(ledger, op_id)
    if kind == "TERMINAL": return success(operation_public_view(ledger, data)).to_dict()
    if kind == "NOT_FOUND":
        return error(f"Operation {op_id} disappeared before timeout",
                     code="INTERNAL_ERROR",
                     details={"reason_code": "OPERATION_STATE_LOST", "operation_id": op_id}).to_dict()

    view = operation_public_view(ledger, data)
    view["wait_timed_out"] = True
    # wait timeout is not an Operation timeout. The persisted Operation status
    # remains the frozen enum value (normally RUNNING), never "still_running".
    return success(view).to_dict()

def _diagnose_execution(args, disp): return diagnose_execution(disp._ledger)


async def _verify_consistency_query(args):
    """B01 §5 Phase 4 query handler: cross-domain manifest consistency check.

    Pure read of manifest files on disk — no side effects, always idempotent.
    Returns the ToolResponse-style dict produced by verify_consistency.
    """
    return await verify_consistency(
        platform_manifest_path=args.get("platform_manifest_path"),
        pl_build_manifest_path=args.get("pl_build_manifest_path"),
        ps_build_manifest_path=args.get("ps_build_manifest_path"),
        board_profile_sha256=args.get("board_profile_sha256"),
        resolve_root=args.get("resolve_root"),
    )


async def _evaluate_observation_query(args):
    """B01 §5 Phase 6 query handler: PASS/FAIL adjudication from UART text.

    Pure text analysis of the capture output already produced by
    ps_stop_uart_capture / ps_wait_uart_capture — no hardware, no side
    effects, always idempotent. Returns the ToolResponse-style dict produced
    by evaluate_observation.

    pass_marker / fail_marker are forwarded verbatim: the schema marks them
    required (B11 phase 2 — no GPIO_E2E_* defaults any more), and a missing
    or invalid marker is rejected by evaluate_observation as INVALID_ARGUMENT
    (fail-closed, never a default verdict).
    """
    return await evaluate_observation(
        uart_text=args.get("uart_text"),
        pass_marker=args.get("pass_marker"),
        fail_marker=args.get("fail_marker"),
    )


_SYNC_QUERIES = {"get_capabilities": _get_capabilities, "get_session_info": _get_session_info,
    "get_operation_status": _get_operation_status, "get_execution_state": _get_execution_state,
    "diagnose_execution": _diagnose_execution}

def _channel_busy(e, ledger=None):
    reason = str(e.args[0]) if e.args else "CHANNEL_BUSY"
    return {"status":"error","error":{"code":"LOCK_BUSY","message":f"Channel blocked: {reason}",
        "details":channel_busy_details(ledger, reason)}}

def _text(d):
    from mcp.types import TextContent; return [TextContent(type="text", text=json.dumps(d))]
