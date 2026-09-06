"""
server.py — Unified Zynq MCP Server (R1 final).

Exit contract (P0-1):
  ALL exit paths flow through a single finalizer:
    1. Worker shutdown
    2. If failure: persist RECOVERY_REQUIRED
    3. If persist failed: DO NOT release owner lock (OS release is implicit
       on process exit, but the app must not create a takeover window).
    4. If persist succeeded: release owner lock.
  Returns structured diagnostics.
"""
import asyncio, json, logging, sys, traceback
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from mcps.zynq_mcp.control.workspace import resolve_workspace_root, resolve_runtime_root, compute_workspace_id
from mcps.zynq_mcp.control.instance_guard import InstanceGuard, InstanceGuardFatalError
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared, _now_iso,
    BACKEND_NONE,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    WORKER_STATE_ABSENT, WORKER_STATE_ORPHANED, WORKER_STATE_DEAD,
    OP_OUTCOME_UNKNOWN, OP_INTERRUPTED, OP_RUNNING, OP_SUCCEEDED,
    LedgerCorruptError, LedgerSchemaError, LedgerWorkspaceMismatchError,
    LedgerInvalidError, LedgerInconsistentError, ChannelBusyError,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.single_worker import SingleWorkerController, _succeeded_auto_recover
from mcps.zynq_mcp.control.domain_runner import CommandRunner, DomainExecutionMutex
from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
from mcps.zynq_mcp.dispatcher import ZynqDispatcher
from mcps.zynq_mcp.control.process_guard import (
    is_pid_alive, get_process_identity, process_identity_matches,
)
from mcps.zynq_mcp.control.tool_process_controller import ToolProcessController

logging.basicConfig(level=logging.WARNING,
    format="%(asctime)s [zynq_mcp] %(levelname)s: %(message)s", stream=sys.stderr)
logger = logging.getLogger("zynq_mcp")


def start_reconcile(guard, ledger_path, workspace_id):
    if not ledger_path.exists():
        def _fresh(l):
            l.instance_id = guard.instance_id; l.workspace_id = workspace_id
            l.execution_lane = EXECUTION_LANE_IDLE
            l.primary_instance_id = guard.instance_id; l.owner_lock_held_since = _now_iso()
            return l
        try:
            return ledger_transaction(guard, ledger_path, _fresh)
        except Exception as e:
            logger.critical("Primary: cannot create ledger: %s", e); raise
    ledger, _ = ledger_read_shared(guard, ledger_path)
    stale_uart = (ledger.worker or {}).get("uart_capture")
    stale_jtag = (ledger.worker or {}).get("jtag_lease")
    if (isinstance(stale_uart, dict) and stale_uart.get("status") not in
            (None, "STOPPED", "INTERRUPTED", "OUTCOME_UNKNOWN")) or \
            (isinstance(stale_jtag, dict) and stale_jtag.get("connected")):
        def _invalidate_resources(current):
            worker = current.worker or {}
            now = _now_iso()
            uart = worker.get("uart_capture")
            if isinstance(uart, dict) and uart.get("status") not in (
                    None, "STOPPED", "DISCONNECTED", "INTERRUPTED",
                    "OUTCOME_UNKNOWN"):
                uart = dict(uart)
                uart.update({"status": "INTERRUPTED", "finished_at": now,
                             "reason_code": "MCP_RESTART"})
                worker["uart_capture"] = uart
                worker["serial_owner"] = None
            jtag = worker.get("jtag_lease")
            if isinstance(jtag, dict) and jtag.get("connected"):
                jtag = dict(jtag)
                jtag.update({"status": "ORPHANED", "connected": False,
                             "last_observed_at": now,
                             "reason_code": "MCP_RESTART"})
                worker["jtag_lease"] = jtag
                worker["jtag_lease_held"] = False
            current.worker = worker
            return current
        ledger = ledger_transaction(
            guard, ledger_path, _invalidate_resources)
    w = ledger.worker or {}; ao = ledger.active_operation; wp = w.get("pid")
    sp = w.get("supervisor_pid")
    wp_valid = isinstance(wp, int) and not isinstance(wp, bool) and wp > 0
    sp_valid = isinstance(sp, int) and not isinstance(sp, bool) and sp > 0
    wp_alive = bool(wp_valid and is_pid_alive(wp))
    sp_alive = bool(sp_valid and is_pid_alive(sp))
    wp_owned = (wp_alive and process_identity_matches(
        get_process_identity(wp), w))
    supervisor_expected = {
        "pid": sp,
        "process_start_time": w.get("supervisor_process_start_time"),
        "executable_path": w.get("supervisor_executable_path"),
    }
    sp_owned = (sp_alive and process_identity_matches(
        get_process_identity(sp), supervisor_expected))
    if wp_alive or sp_alive:
        reconcile_reason = ("BACKEND_ORPHANED" if (not wp_alive or wp_owned) and
                            (not sp_alive or sp_owned)
                            else "BACKEND_IDENTITY_MISMATCH")
        def _orphan(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso(); l.takeover_count += 1
            wk = l.worker or {}; wk["state"] = WORKER_STATE_ORPHANED; l.worker = wk
            if l.active_operation:
                l.active_operation["status"] = OP_OUTCOME_UNKNOWN
                l.active_operation["reason_code"] = reconcile_reason
                l.active_operation["finished_at"] = _now_iso()
                l.previous_operation = dict(l.active_operation)
                l.active_operation = None
            l.recent_errors.append({"at": _now_iso(),
                                    "reason_code": reconcile_reason,
                                    "backend": wk.get("backend", "NONE")})
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
        return ledger_transaction(guard, ledger_path, _orphan)
    # A terminal success permits automatic IDLE recovery only after proving
    # that no recorded backend process remains alive. Process truth wins over
    # an Operation record.
    if ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED and \
            (ledger.previous_operation or {}).get("status") == OP_SUCCEEDED:
        logger.info("Startup reconcile: previous op succeeded and no backend "
                    "is alive; recovering RECOVERY_REQUIRED -> IDLE")
        def _recover(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso()
            return _succeeded_auto_recover(l)
        return ledger_transaction(guard, ledger_path, _recover)
    elif wp is not None or sp is not None:
        def _dead(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso(); l.takeover_count += 1
            if l.active_operation:
                l.active_operation["status"] = OP_OUTCOME_UNKNOWN
                l.previous_operation = dict(l.active_operation); l.active_operation = None
            l.worker["state"] = WORKER_STATE_DEAD
            l.recent_errors.append({"at": _now_iso(),
                                    "reason_code": "BACKEND_PROCESS_DEAD",
                                    "backend": l.worker.get("backend", "NONE")})
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
        return ledger_transaction(guard, ledger_path, _dead)
    elif ao and not wp:
        def _orphan_ao(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso(); l.takeover_count += 1
            if l.active_operation:
                l.active_operation["status"] = OP_OUTCOME_UNKNOWN
                l.active_operation["reason_code"] = "BACKEND_PROCESS_MISSING"
                l.active_operation["finished_at"] = _now_iso()
                l.previous_operation = dict(l.active_operation); l.active_operation = None
            l.worker["state"] = WORKER_STATE_DEAD
            l.recent_errors.append({"at": _now_iso(),
                                    "reason_code": "BACKEND_PROCESS_MISSING",
                                    "backend": l.worker.get("backend", BACKEND_NONE)})
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
        return ledger_transaction(guard, ledger_path, _orphan_ao)
    elif w.get("state") not in (WORKER_STATE_ABSENT, WORKER_STATE_DEAD) or \
            w.get("backend", BACKEND_NONE) not in (None, "", BACKEND_NONE):
        def _malformed_worker(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso(); l.takeover_count += 1
            l.worker["state"] = WORKER_STATE_ORPHANED
            l.recent_errors.append({"at": _now_iso(),
                                    "reason_code": "BACKEND_IDENTITY_MISSING",
                                    "backend": l.worker.get("backend", BACKEND_NONE)})
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            return l
        return ledger_transaction(guard, ledger_path, _malformed_worker)
    else:
        def _safe(l):
            l.primary_instance_id = guard.instance_id; l.instance_id = guard.instance_id
            l.owner_lock_held_since = _now_iso(); return l
        return ledger_transaction(guard, ledger_path, _safe)


def second_instance_report(guard, ledger_path):
    """F-06: report an already-running instance on STDERR only. stdout is
    reserved for JSONRPC frames -- printing a bare dict there crashed MCP
    clients (model_validate_json). The caller exits non-zero so startup
    scripts can detect the conflict. """
    try:
        ledger, _ = ledger_read_shared(guard, ledger_path, guard.workspace_id)
    except Exception:
        print(json.dumps({"status":"error","error":{"code":"INSTANCE_ALREADY_RUNNING",
            "message":"Another zynq_mcp instance is running","details":{
            "reason_code":"INSTANCE_ALREADY_RUNNING","primary_instance_id":"unknown",
            "recommended_action":"Connect to the running instance"}}}),
            file=sys.stderr, flush=True)
        return
    ao = ledger.active_operation or {}
    print(json.dumps({"status":"error","error":{"code":"INSTANCE_ALREADY_RUNNING",
        "message":"Another zynq_mcp instance is running","details":{
        "reason_code":"INSTANCE_ALREADY_RUNNING",
        "primary_instance_id":ledger.primary_instance_id,
        "execution_lane":ledger.execution_lane,
        "active_operation_id":ao.get("operation_id"),
        "active_operation_status":ao.get("status"),
        "current_stage":ledger.context.get("current_stage","IDLE"),
        "recommended_action":"Connect to the running instance"}}}),
        file=sys.stderr, flush=True)


async def _main():
    instance_guard = None; worker = None; ledger_path = None; fatal_error = None
    cmd_runner = None; process_controller = None
    try:
        workspace_root = resolve_workspace_root()
        runtime_root = resolve_runtime_root()
        workspace_id = compute_workspace_id(workspace_root)
        instance_guard = InstanceGuard(runtime_root, workspace_id)
        ledger_path = runtime_root / "execution_ledger.json"
        try:
            instance_guard.determine_role()
        except InstanceGuardFatalError as e:
            fatal_error = e; return 1
        if instance_guard.is_secondary:
            second_instance_report(instance_guard, ledger_path); return 1
        ledger = start_reconcile(instance_guard, ledger_path, workspace_id)
        op_registry = OperationRegistry(); op_registry.restore_from_ledger(ledger)
        lifecycle_lock = asyncio.Lock()
        worker = SingleWorkerController(
            ledger, instance_guard, ledger_path,
            lifecycle_lock=lifecycle_lock)
        # O2: server-scoped owner for the one direct EDA Tcl backend. Domain
        # cutover happens in O3-O5; ownership, identity, switching and cleanup
        # are established here first.
        process_controller = ToolProcessController(
            instance_guard, ledger_path, lifecycle_lock=lifecycle_lock)
        # R3.1-C: process-scoped DomainExecutionMutex + CommandRunner
        domain_mutex = DomainExecutionMutex()
        # O5: the formal server never constructs a standalone XsdbBridge.
        # ToolProcessController is the sole XSDB process owner; these
        # registries persist connection/capture truth around that process.
        from mcps.zynq_mcp.control.resource_registry import (
            JtagResourceRegistry, UartResourceRegistry,
        )
        jtag_registry = JtagResourceRegistry(instance_guard, ledger_path)
        uart_registry = UartResourceRegistry(instance_guard, ledger_path)
        xsdb_bridge = None  # historical component-test injection only
        # O4: production XSCT is created only by ToolProcessController.
        # Keeping this injection slot as None preserves component-test
        # compatibility without creating a second process owner.
        xsct_bridge = None
        # B08: optional VivadoTclBridge for PL bridge tools (direct
        # `vivado -mode tcl`, no old-MCP stdio middle layer). A failed import
        # must NOT crash the server — PL tools will then fail-closed instead.
        vivado_bridge = None
        cmd_runner = CommandRunner(instance_guard, ledger_path, op_registry,
                                   domain_mutex, worker=worker,
                                   xsdb_bridge=xsdb_bridge,
                                   xsct_bridge=xsct_bridge,
                                   vivado_bridge=vivado_bridge,
                                   process_controller=process_controller,
                                   jtag_registry=jtag_registry,
                                   uart_registry=uart_registry)
        dispatcher = ZynqDispatcher(ledger, op_registry, instance_guard, ledger_path,
                                    worker, cmd_runner=cmd_runner,
                                    process_controller=process_controller)
        server = Server("zynq")
        @server.list_tools()
        async def list_tools(): return dispatcher.schemas()
        @server.call_tool()
        async def call_tool(name, arguments):
            if not isinstance(arguments, dict):
                return [TextContent(type="text", text=json.dumps({
                    "status":"error","error":{"code":"INVALID_ARGUMENT",
                    "message":"arguments must be a JSON object"}}))]
            result = await dispatcher.dispatch(name, arguments, True)
            # 修复轮 #12: 响应附注（已知症状 → 独立 annotations 字段）
            return dispatcher.annotate(result)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    except InstanceGuardFatalError as e:
        fatal_error = e
    except (LedgerCorruptError, LedgerSchemaError, LedgerWorkspaceMismatchError,
            LedgerInvalidError, LedgerInconsistentError) as e:
        fatal_error = e
    except Exception as e:
        fatal_error = e
    finally:
        # B06: stop the bridges on every exit path (idempotent; safe when a
        # bridge was never started).
        if cmd_runner is not None:
            try:
                await cmd_runner.shutdown_uart_resources()
            except Exception as e:
                logger.error("UART resource shutdown during exit: %s", e)
            try:
                await cmd_runner.shutdown_xsdb_bridge()
            except Exception as e:
                logger.error("xsdb bridge shutdown during exit: %s", e)
            try:
                await cmd_runner.shutdown_xsct_bridge()
            except Exception as e:
                logger.error("xsct bridge shutdown during exit: %s", e)
            try:
                await cmd_runner.shutdown_vivado_bridge()
            except Exception as e:
                logger.error("vivado bridge shutdown during exit: %s", e)
        await _server_finalizer(instance_guard, worker, ledger_path, fatal_error,
                                process_controller=process_controller)


async def _server_finalizer(guard, worker, ledger_path, fatal_error,
                            process_controller=None) -> dict:
    """Unified server exit. Returns structured diagnostics.

    Rules:
    - If Worker shutdown fails AND persist succeeds: release owner lock.
    - If Worker shutdown fails AND persist ALSO fails: DO NOT release
      owner lock — the OS will release it when the process exits, but
      the application must not create a deliberate takeover window
      without durable evidence.
    - Normal exit: persist N/A, release owner lock.
    """
    diag = {
        "fatal_error": str(fatal_error) if fatal_error else None,
        "owner_lock_released": False,
        "persist_failed": False,
        "shutdown_incomplete": False,
    }

    need_persist = False
    persist_ok = None

    # --- Phase 0: O2 direct EDA backend shutdown ---
    if process_controller is not None:
        try:
            backend_result = await process_controller.shutdown_backend(force=True)
            diag["backend_shutdown"] = backend_result.to_dict()
            if not backend_result.success:
                diag["shutdown_incomplete"] = True
                need_persist = True
        except Exception as e:
            diag["backend_shutdown_exception"] = str(e)
            diag["shutdown_incomplete"] = True
            need_persist = True

    # --- Phase 1: legacy Worker shutdown ---
    if worker is not None:
        try:
            sw_result = await worker.shutdown()
            diag["worker_shutdown"] = {
                "success": sw_result.get("success"),
                "pid_cleaned": sw_result.get("pid_cleaned"),
                "hb_stopped": sw_result.get("hb_stopped"),
                "error": sw_result.get("error"),
            }
            if not sw_result.get("success"):
                diag["shutdown_incomplete"] = True
                need_persist = True
        except Exception as e:
            diag["worker_shutdown_exception"] = str(e)
            diag["shutdown_incomplete"] = True
            need_persist = True

    if need_persist:
        persist_ok = _persist_shutdown_failure(
            guard, ledger_path,
            diag.get("worker_shutdown", {}).get("pid_cleaned", False) if isinstance(diag.get("worker_shutdown"), dict) else False,
            str(diag.get("worker_shutdown", {}).get("error", "unknown")) if isinstance(diag.get("worker_shutdown"), dict) else "exception")
        diag["recovery_persisted"] = persist_ok
        if not persist_ok:
            diag["persist_failed"] = True
            logger.critical("CRITICAL: persist FAILED — not releasing owner lock")

    # --- Phase 2: Release owner lock ---
    # Only release if persist succeeded (or not needed).
    if not diag["persist_failed"] and guard is not None and guard.is_primary:
        try:
            guard.release_owner_lock()
            diag["owner_lock_released"] = True
        except Exception as e:
            diag["owner_lock_release_error"] = str(e)

    if diag["persist_failed"]:
        logger.error("FATAL exit: %s", json.dumps(diag, default=str))
    elif diag["shutdown_incomplete"]:
        logger.error("Exit with shutdown errors: %s", json.dumps(diag, default=str))
    else:
        logger.info("Exit: %s", json.dumps(diag, default=str))
    return diag


def _persist_shutdown_failure(guard, ledger_path, pid_cleaned, error_msg) -> bool:
    if guard is None or ledger_path is None or not ledger_path.exists():
        return False
    try:
        def _fail(l):
            l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
            l.recent_errors.append({
                "at": _now_iso(), "reason": "SHUTDOWN_FAILED",
                "pid_cleaned": pid_cleaned,
                "error": str(error_msg)[:500],
            })
            return l
        ledger_transaction(guard, ledger_path, _fail)
        return True
    except Exception as e:
        logger.error("Cannot persist RECOVERY_REQUIRED: %s", e)
        return False


def main():
    rc = asyncio.run(_main())
    # F-06: honor non-zero exit codes (secondary instance, fatal guard
    # errors) so launchers can detect the conflict; primary path exits 0.
    sys.exit(rc if isinstance(rc, int) and rc else 0)
if __name__ == "__main__": main()
