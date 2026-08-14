"""
domain_runner.py — Unified Domain Execution Lifecycle (R3.0).
"""
import asyncio, logging, math, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable

from mcps.common.tool_response import success, error
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, ledger_read_shared, _now_iso,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    EXECUTION_LANE_CLOSING, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED, OP_CANCELLED,
    OP_TIMED_OUT, OP_INTERRUPTED, OP_OUTCOME_UNKNOWN,
    OP_NON_TERMINAL, OP_TERMINAL,
    WORKER_STATE_ABSENT, WORKER_STATE_READY,
    ChannelBusyError, LedgerWriteError, operation_contract_fields,
)
from mcps.zynq_mcp.control.operation_service import (
    request_signature, op_transition, op_observe,
    InFlightDuplicateError, TerminalDuplicateError,
    channel_busy_details,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry, Operation

logger = logging.getLogger("zynq_mcp.domain_runner")


class DomainValidationError(Exception): pass
class DomainOutcomeUnknownError(Exception): pass


# B06 second batch: BSP/Build tools run on the XSCT shell (xsct on PATH),
# not the XSDB shell. These names select the XsctBridge in _execute.
_PS_XSCT_TOOL_NAMES = frozenset({
    "ps_import_hardware", "ps_create_platform", "ps_create_bsp",
    "ps_update_hardware", "ps_get_bsp_status", "ps_create_app",
    "ps_add_sources", "ps_set_compiler_options", "ps_compile",
    "ps_get_build_status",
})

# PS helpers that are deliberately process-free.  They keep the uniform
# ``local_fn(bridge, **arguments)`` calling convention, but ``bridge`` is
# always None and no EDA backend may be started.  In particular,
# ps_read_elf_info parses the ELF header in pure Python; starting XSCT here
# would recreate an XSCT worker after ps_compile has intentionally shut it
# down, then make the following XSDB/JTAG operation an illegal backend switch.
_PS_LOCAL_DIRECT_TOOLS = frozenset({"ps_read_elf_info"})

# Historical classification retained only for compatibility component tests.
# The formal server path launches through VivadoExecutionFacade and polls real
# Vivado run STATUS; it never refreshes operation state with a synthetic timer.
_LONG_RUN_TOOLS = frozenset({"pl_synthesize", "pl_place", "pl_route"})

# B07 fix: PL tools that run on the XsdbBridge (NOT the VivadoTclBridge).
# pl_program_fpga programs the FPGA via XSDB `fpga -f` — the canonical
# Zynq-7020 flow — because the Vivado hw_manager path (pl_program_device)
# cannot find the xc7z020 device on the ARM-first JTAG chain. These names
# select the XsdbBridge in _execute, BEFORE the _pl_bridge branch.
_PL_XSDB_TOOLS = frozenset({"pl_program_fpga"})

_PS_UART_CAPTURE_TOOLS = frozenset({
    "ps_start_uart_capture", "ps_wait_uart_capture", "ps_stop_uart_capture",
})
_PS_UART_DIRECT_TOOLS = frozenset({
    "ps_read_uart", "ps_write_uart", "ps_list_serial_ports",
})

_JTAG_OBSERVATION_STEP = {
    "ps_connect_hw_server": "JTAG_CONNECT",
    "ps_disconnect_hw_server": "JTAG_DISCONNECT",
    "ps_list_targets": "JTAG_LIST_TARGETS",
    "ps_select_target": "JTAG_SELECT_TARGET",
    "ps_get_target_status": "JTAG_TARGET_STATUS",
    "ps_get_device_info": "JTAG_DEVICE_INFO",
    "ps_reset_target": "JTAG_RESET",
    "ps_ensure_arm_accessible": "JTAG_ARM_ACCESS",
    "ps_initialize_ps": "JTAG_PS_INIT",
    "ps_load_hardware": "JTAG_LOAD_HARDWARE",
    "ps_download_elf": "JTAG_DOWNLOAD_ELF",
    "ps_run_target": "JTAG_RUN",
    "ps_halt_target": "JTAG_HALT",
    "ps_step_target": "JTAG_STEP",
    "ps_wait_for_state": "JTAG_WAIT_STATE",
    "ps_reg_read": "JTAG_REGISTER_READ",
    "ps_reg_write": "JTAG_REGISTER_WRITE",
    "ps_mem_read": "JTAG_MEMORY_READ",
    "ps_mem_write": "JTAG_MEMORY_WRITE",
    "ps_recover_target": "JTAG_RECOVER",
    "ps_reconnect_target": "JTAG_RECONNECT",
    "ps_clear_debug_session": "JTAG_CLEAR_DEBUG",
    "ps_diagnose_dap": "JTAG_DIAGNOSE_DAP",
    "ps_diagnose_uart_clock": "JTAG_UART_DIAGNOSE",
    "ps_debug_start": "JTAG_DEBUG_START",
    "ps_debug_close": "JTAG_DEBUG_CLOSE",
    "ps_breakpoint_add": "JTAG_BREAKPOINT_ADD",
    "ps_breakpoint_remove": "JTAG_BREAKPOINT_REMOVE",
    "ps_read_register": "JTAG_REGISTER_READ",
    "ps_write_register": "JTAG_REGISTER_WRITE",
    "ps_stack_trace": "JTAG_STACK_TRACE",
    "pl_program_fpga": "JTAG_PROGRAM_FPGA",
}

# B08: PL simulation tools (xvlog/xelab/xsim) run OUTSIDE vivado.exe, so they
# still need the old Vivado MCP adapter (VivadoAdapter via SingleWorkerController).
# # DEFERRED: migrate to a standalone XSim adapter. In _execute they are
# injected the old VivadoAdapter, NOT the VivadoTclBridge.
_PL_OLD_ADAPTER_TOOLS = frozenset({
    "pl_compile_sim", "pl_elaborate_sim", "pl_run_simulation",
    "pl_parse_sim_log",
})

_PL_OBSERVATION_STEP = {
    "pl_create_project": "PROJECT_OPEN",
    "pl_open_project": "PROJECT_OPEN",
    "pl_open_checkpoint": "PROJECT_OPEN",
    "pl_set_top": "PROJECT_OPEN",
    "pl_generate_target": "PROJECT_OPEN",
    "pl_synthesize": "SYNTHESIS",
    "pl_place": "PLACE",
    "pl_route": "ROUTE",
    "pl_analyze_timing": "TIMING_ANALYSIS",
    "pl_generate_bitstream": "BITSTREAM_WRITE",
}


class ResourceRequirementType(Enum):
    NONE = "NONE"
    JTAG_ACQUIRE = "JTAG_ACQUIRE"
    JTAG_REQUIRE_OWNED = "JTAG_REQUIRE_OWNED"
    UART_ACQUIRE = "UART_ACQUIRE"
    UART_REQUIRE_OWNED = "UART_REQUIRE_OWNED"
_VALID_RESOURCE_TYPES = frozenset({e.value for e in ResourceRequirementType})


@dataclass
class ResourceRequirement:
    type: str = "NONE"
    lease_key: Optional[str] = None
    def __post_init__(self):
        if self.type not in _VALID_RESOURCE_TYPES:
            raise ValueError(f"Unknown ResourceRequirement type: {self.type}")


def _parse_iso_timestamp(iso: str) -> float:
    import datetime
    try:
        return datetime.datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        return 0.0


def _lease_expired(record: dict) -> bool:
    if not isinstance(record, dict): return True
    hb = record.get("heartbeat_at"); ttl = record.get("ttl_s")
    if hb is None or ttl is None: return True
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)): return True
    if ttl <= 0 or not (ttl < float("inf")): return True
    try:
        ts = _parse_iso_timestamp(hb)
        return ts <= 0 or (time.time() - ts) > ttl
    except Exception: return True


def _check_jtag_access(ledger_worker: dict, requirement: ResourceRequirement,
                       session_id: str) -> tuple[bool, Optional[str]]:
    if requirement.type == "NONE": return (True, None)
    if requirement.type not in _VALID_RESOURCE_TYPES: return (False, "INVALID_RESOURCE_TYPE")
    if requirement.type == "JTAG_ACQUIRE":
        jtag = ledger_worker.get("jtag_lease") or {}
        if jtag and jtag.get("lease_id"):
            # A disconnected/orphaned historical record is audit evidence,
            # not a live lease.  Once held=false a new controlled XSDB
            # connection may acquire the resource.
            if not ledger_worker.get("jtag_lease_held") and \
                    jtag.get("status") in ("DISCONNECTED", "ORPHANED",
                                             "INTERRUPTED"):
                return (True, None)
            if jtag.get("owner_session_id") != session_id:
                # Frozen R3 admission contract classifies a second acquire as
                # already-held regardless of owner.  Ownership is disclosed
                # only by REQUIRE_OWNED operations, not by acquisition probes.
                return (False, "JTAG_ALREADY_HELD")
            if requirement.lease_key and jtag.get("lock_key") != requirement.lease_key:
                return (False, "JTAG_KEY_MISMATCH")
            if jtag.get("status") != "CONNECTED" or _lease_expired(jtag):
                return (False, "JTAG_LEASE_EXPIRED")
            return (True, None)
        if ledger_worker.get("jtag_lease_held") and not jtag.get("lease_id"):
            return (False, "RESOURCE_RECORD_INCOMPLETE")
        return (True, None)
    if requirement.type == "JTAG_REQUIRE_OWNED":
        jtag = ledger_worker.get("jtag_lease") or {}
        if not jtag or not jtag.get("lease_id"):
            if ledger_worker.get("jtag_lease_held"): return (False, "RESOURCE_RECORD_INCOMPLETE")
            return (False, "JTAG_LEASE_MISSING")
        if jtag.get("owner_session_id") != session_id: return (False, "JTAG_OWNER_MISMATCH")
        if requirement.lease_key and jtag.get("lock_key") != requirement.lease_key:
            return (False, "JTAG_KEY_MISMATCH")
        # O5 records are strict and generation/instance bound.  Historical
        # R3 component fixtures contain only lease_id/owner/heartbeat/ttl;
        # keep that frozen test contract while every production-created O5
        # record takes the strict branch below.
        if "status" in jtag or "worker_generation" in jtag or "instance_id" in jtag:
            if jtag.get("status") != "CONNECTED" or not jtag.get("connected"):
                return (False, "JTAG_LEASE_EXPIRED")
            if jtag.get("instance_id") != ledger_worker.get("instance_id") or \
                    jtag.get("worker_generation") != ledger_worker.get("worker_generation"):
                return (False, "JTAG_LEASE_STALE")
        if _lease_expired(jtag): return (False, "JTAG_LEASE_EXPIRED")
        return (True, None)
    if requirement.type == "UART_ACQUIRE":
        owner = ledger_worker.get("serial_owner")
        if owner:
            if not isinstance(owner, dict):
                return (False, "RESOURCE_RECORD_INCOMPLETE")
            if owner.get("session_id") != session_id:
                return (False, "UART_OWNER_MISMATCH")
            return (False, "UART_ALREADY_HELD")
        return (True, None)
    if requirement.type == "UART_REQUIRE_OWNED":
        owner = ledger_worker.get("serial_owner")
        capture = ledger_worker.get("uart_capture") or {}
        if not isinstance(owner, dict) or not owner.get("capture_id"):
            return (False, "UART_CAPTURE_MISSING")
        if owner.get("session_id") != session_id:
            return (False, "UART_OWNER_MISMATCH")
        if requirement.lease_key and owner.get("capture_id") != requirement.lease_key:
            return (False, "UART_CAPTURE_MISMATCH")
        if capture.get("capture_id") != owner.get("capture_id"):
            return (False, "RESOURCE_RECORD_INCOMPLETE")
        if capture.get("status") in ("DISCONNECTED", "INTERRUPTED", "OUTCOME_UNKNOWN", "STOPPED"):
            return (False, "UART_DISCONNECTED")
        return (True, None)
    return (False, "UNKNOWN_RESOURCE_TYPE")


# ---- Fail-fast Domain Execution Mutex (synchronous, no await) ----
@dataclass
class MutexBusyInfo:
    active_category: Optional[str] = None
    active_tool_name: Optional[str] = None
    started_at: Optional[float] = None
    elapsed_s: float = 0.0
    recommended_action: str = "Poll get_execution_state or wait_operation"
    poll_after_s: int = 5


class DomainExecutionMutex:
    def __init__(self):
        self._locked = False
        self._category: Optional[str] = None
        self._tool_name: Optional[str] = None
        self._started_at: Optional[float] = None

    def try_acquire(self, category: str, tool_name: str):
        """Synchronous fail-fast. No await. No async context needed."""
        if self._locked:
            return None
        self._locked = True
        self._category = category
        self._tool_name = tool_name
        self._started_at = time.time()
        return True

    def release(self, category: str):
        self._locked = False
        self._category = None
        self._tool_name = None
        self._started_at = None

    def busy_info(self) -> MutexBusyInfo:
        if not self._locked: return MutexBusyInfo()
        elapsed = time.time() - self._started_at if self._started_at else 0.0
        return MutexBusyInfo(
            active_category=self._category, active_tool_name=self._tool_name,
            started_at=self._started_at, elapsed_s=round(elapsed, 1),
            poll_after_s=max(1, min(30, int(elapsed * 2))))

    @property
    def locked(self) -> bool: return self._locked


# ---- Shared Preflight (P1-P9) with value validation ----
_ACTIVE_STATES = frozenset({WORKER_STATE_READY, "BUSY", "STARTING", "POISONED",
                              "UNRESPONSIVE", "STOPPING"})


def _validate_worker_identity_fields(wo: dict, *, ledger=None):
    """Fail-closed: each field must exist AND have valid value.
    Additionally: worker.instance_id must match primary_instance_id if provided."""
    pid = wo.get("pid")
    if pid is None or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    pst = wo.get("process_start_time")
    if pst is None or isinstance(pst, bool) or not isinstance(pst, (int, float)):
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    if math.isnan(pst) or math.isinf(pst) or pst <= 0:
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    exe = wo.get("executable_path")
    if not isinstance(exe, str) or not exe.strip():
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    gen = wo.get("worker_generation")
    if gen is None or isinstance(gen, bool) or not isinstance(gen, int) or gen < 0:
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    iid = wo.get("instance_id")
    if not isinstance(iid, str) or not iid.strip():
        raise ChannelBusyError("WORKER_IDENTITY_INVALID")
    # instance ownership: primary must exist, worker must match
    if ledger is not None:
        owner = getattr(ledger, 'primary_instance_id', None) if hasattr(ledger, 'primary_instance_id') else None
        if not owner or not isinstance(owner, str) or not owner.strip():
            raise ChannelBusyError("WORKER_INSTANCE_MISMATCH")
        if iid != owner:
            raise ChannelBusyError("WORKER_INSTANCE_MISMATCH")


def _validate_heartbeat_field(wo: dict):
    """Fail-closed: heartbeat must be a valid timestamp."""
    hb = wo.get("last_heartbeat_at")
    if not hb:
        raise ChannelBusyError("WORKER_HEARTBEAT_MISSING")
    if not isinstance(hb, str) or not hb.strip():
        raise ChannelBusyError("WORKER_HEARTBEAT_INVALID")


def _shared_preflight_check(ledger, tool_name, session_id, resource_req=None):
    from mcps.zynq_mcp.control.execution_gate import _check_stage, _parse_iso
    from mcps.zynq_mcp.control.process_guard import is_pid_alive, get_process_identity

    ctx = ledger.context or {}; wo = ledger.worker or {}
    ao = ledger.active_operation; po = ledger.previous_operation
    cur_stage = ctx.get("current_stage", "IDLE")

    cur_sid = ctx.get("session_id", "")
    if not cur_sid: raise ChannelBusyError("NO_ACTIVE_SESSION")
    if cur_sid != session_id: raise ChannelBusyError("SESSION_ID_MISMATCH")

    if ao and ao.get("status") in OP_NON_TERMINAL:
        op_gen = ao.get("worker_generation", -1)
        w_gen = wo.get("worker_generation", -2)
        if op_gen != -1 and w_gen != -2 and op_gen != w_gen:
            raise ChannelBusyError("WORKER_GENERATION_STALE")
    if ao and ao.get("status") in OP_NON_TERMINAL:
        raise ChannelBusyError("CHANNEL_BUSY")

    if ledger.execution_lane != EXECUTION_LANE_IDLE:
        if ledger.execution_lane == EXECUTION_LANE_CLOSING: raise ChannelBusyError("CHANNEL_CLOSING")
        if ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED: raise ChannelBusyError("CHANNEL_RECOVERY_REQUIRED")
        raise ChannelBusyError(f"CHANNEL_{ledger.execution_lane}")

    wstate = wo.get("state", WORKER_STATE_ABSENT)
    if wstate in _ACTIVE_STATES:
        _validate_worker_identity_fields(wo, ledger=ledger)
        _validate_heartbeat_field(wo)

    if po and po.get("status") in (OP_INTERRUPTED, OP_OUTCOME_UNKNOWN, OP_TIMED_OUT):
        if not po.get("resolved_by_recovery"):
            raise ChannelBusyError("PREVIOUS_OPERATION_UNRESOLVED")

    pid = wo.get("pid")
    if isinstance(pid, int) and pid > 0 and wstate not in (WORKER_STATE_ABSENT, "DEAD", "ORPHANED"):
        if not is_pid_alive(pid): raise ChannelBusyError("WORKER_PID_DEAD")
    if isinstance(pid, int) and pid > 0 and is_pid_alive(pid):
        ident = get_process_identity(pid)
        if ident is None: raise ChannelBusyError("WORKER_IDENTITY_UNVERIFIABLE")
        est = wo.get("process_start_time"); eex = wo.get("executable_path")
        if est is not None and abs(ident.process_start_time - est) > 5.0:
            raise ChannelBusyError("WORKER_IDENTITY_MISMATCH")
        if eex is not None and ident.executable_path != eex:
            raise ChannelBusyError("WORKER_IDENTITY_MISMATCH")

    if isinstance(pid, int) and pid > 0 and wstate not in (WORKER_STATE_ABSENT, "DEAD", "ORPHANED"):
        hb = wo.get("last_heartbeat_at")
        if not hb: raise ChannelBusyError("WORKER_HEARTBEAT_MISSING")
        hb_ts = _parse_iso(hb)
        if hb_ts <= 0: raise ChannelBusyError("WORKER_HEARTBEAT_UNREADABLE")
        if time.time() - hb_ts > 120.0: raise ChannelBusyError("WORKER_UNRESPONSIVE")

    if _check_stage(tool_name, cur_stage, po):
        raise ChannelBusyError("STAGE_PREREQUISITE_UNMET")

    if tool_name.startswith("pl_") or tool_name.startswith("platform_") or tool_name.startswith("ps_"):
        rev = ctx.get("board_package_revision", "")
        if not rev: raise ChannelBusyError("REVISION_MISMATCH")
        expected = ctx.get("expected_board_revision", "")
        if not expected: raise ChannelBusyError("BOARD_REVISION_UNKNOWN")
        bid = ctx.get("board_id", "")
        if bid:
            from mcps.zynq_mcp.control.session import verify_board_revision
            verify_board_revision(bid, expected)

    if resource_req is not None:
        allowed, rejection = _check_jtag_access(wo, resource_req, session_id)
        if not allowed: raise ChannelBusyError(rejection)
        if wo.get("state") == "ORPHANED": raise ChannelBusyError("RESOURCE_ORPHANED")


def _mutex_busy_response(info: MutexBusyInfo, ledger=None) -> dict:
    details = channel_busy_details(ledger, "CHANNEL_BUSY")
    details.update({
        "active_category": info.active_category,
        "active_tool_name": info.active_tool_name,
        "mutex_started_at": str(info.started_at) if info.started_at else None,
        "mutex_elapsed_s": info.elapsed_s,
    })
    return {"status": "error", "error": {
        "code": "LOCK_BUSY", "message": "Execution mutex busy",
        "details": details}}


def _read_ledger_for_busy(guard, ledger_path):
    try:
        return ledger_read_shared(guard, ledger_path)[0]
    except Exception:
        return None


# ---- Command Runner ----
# E006: domain input revision mapping — revision field used for request_signature + input_artifact_revision
_DOMAIN_INPUT_REVISION_FIELD: dict[str, str] = {
    "pl_generate_system_top": "platform_revision",
    "platform_generate": "board_package_revision",
}

# E006: execution context snapshot fields for domain local commands
_EXECUTION_SNAPSHOT_FIELDS = ("session_id", "board_id", "project_path",
    "current_stage", "platform_revision", "board_profile_sha256",
    "board_package_revision")

# E004: IMMUTABLE success-stage mapping — single source of truth for all modules
# B07: PL bridge tools advance the frozen B01 §5 serial chain
# (docs/development/mcp/B04_single_channel_audit.md §4.3 — the ONLY forward path):
#   PL_BUILD      --pl_synthesize--------> PL_IMPLEMENT
#   PL_IMPLEMENT  --pl_route (completes place_and_route)--> PL_TIMING
#   PL_TIMING     --pl_analyze_timing (timing_met=true)--> PL_BITSTREAM
#   PL_BITSTREAM  --pl_generate_bitstream--> PS_BUILD
# pl_place does NOT advance (it is the placement half of implementation and
# runs inside PL_IMPLEMENT; next_stage stays None).
from types import MappingProxyType as _MappingProxyType
_PL_SUCCESS_STAGE = _MappingProxyType({
    "pl_generate_system_top": "PL_BUILD",
    "platform_generate": "PL_GENERATE",
    "pl_synthesize": "PL_IMPLEMENT",
    "pl_route": "PL_TIMING",
    "pl_analyze_timing": "PL_BITSTREAM",
    "pl_generate_bitstream": "PS_BUILD",
})


class CommandRunner:
    def __init__(self, guard, ledger_path, op_registry: OperationRegistry,
                 mutex: DomainExecutionMutex, worker=None, xsdb_bridge=None,
                 xsct_bridge=None, vivado_bridge=None,
                 process_controller=None, jtag_registry=None,
                 uart_registry=None):
        self._guard = guard; self._ledger_path = ledger_path
        self._op_registry = op_registry; self._mutex = mutex; self._worker = worker
        # B06: optional XsdbBridge for PS domain JTAG tools. Lazy-started on
        # the first ps_* local command; stopped by shutdown_xsdb_bridge().
        # No one-shot "_started" flag: a subprocess can die after starting
        # (crash / eval-timeout kill), and the ensure method must then restart
        # it instead of permanently failing every later tool.
        self._xsdb_bridge = xsdb_bridge
        # B06 second batch: optional XsctBridge for PS BSP/Build tools.
        # Lazy-started with the session's project_path as the xsct workspace.
        self._xsct_bridge = xsct_bridge
        # B08: optional VivadoTclBridge for PL bridge tools (direct
        # `vivado -mode tcl`, no old-MCP stdio middle layer). Lazy-started on
        # the first PL bridge command; stopped by shutdown_vivado_bridge().
        self._vivado_bridge = vivado_bridge
        # O2/O3: the sole owner of direct VIVADO/XSCT/XSDB subprocesses.
        # When configured, production Platform/PL execution never starts the
        # legacy worker or the standalone bridge above.
        self._process_controller = process_controller
        if jtag_registry is None and process_controller is not None:
            from mcps.zynq_mcp.control.resource_registry import JtagResourceRegistry
            jtag_registry = JtagResourceRegistry(guard, ledger_path)
        self._jtag_registry = jtag_registry
        self._uart_registry = uart_registry

    async def _ensure_controlled_vivado(self, op_id):
        if self._process_controller is None:
            return None
        from mcps.zynq_mcp.control.execution_ledger import BACKEND_VIVADO
        from mcps.zynq_mcp.control.vivado_execution_observer import (
            VivadoExecutionFacade,
        )
        await self._process_controller.ensure_backend(
            BACKEND_VIVADO, operation_id=op_id)
        return VivadoExecutionFacade(
            self._process_controller, op_id, self._guard, self._ledger_path)

    async def _ensure_controlled_xsct(self, op_id, workspace: str):
        if self._process_controller is None:
            return None
        from mcps.zynq_mcp.control.execution_ledger import BACKEND_XSCT
        from mcps.zynq_mcp.control.xsct_execution_observer import (
            XsctExecutionFacade,
        )
        await self._process_controller.ensure_backend(
            BACKEND_XSCT, workspace=workspace, operation_id=op_id)
        return XsctExecutionFacade(
            self._process_controller, op_id, self._guard, self._ledger_path)

    async def _ensure_controlled_xsdb(self, op_id, session_id: str):
        if self._process_controller is None:
            return None
        from mcps.zynq_mcp.control.execution_ledger import BACKEND_XSDB
        from mcps.zynq_mcp.control.xsdb_execution_observer import (
            XsdbExecutionFacade,
        )
        await self._process_controller.ensure_backend(
            BACKEND_XSDB, operation_id=op_id)
        return XsdbExecutionFacade(
            self._process_controller, op_id, session_id, self._guard,
            self._ledger_path, self._jtag_registry)

    # ---- B06: XsdbBridge lifecycle (PS domain tools) ----

    async def _ensure_xsdb_bridge(self):
        """Lazy-init the XsdbBridge. Returns the ready bridge, or None.

        Fail-closed: when no bridge is configured, or the shell cannot be
        started, returns None (the caller produces an explicit error). If the
        shell was started earlier but has since died, the dead shell is reaped
        and a fresh one started — a one-shot guard would otherwise brick every
        later JTAG/PS tool after any subprocess death.
        """
        bridge = self._xsdb_bridge
        if bridge is None:
            return None
        if bridge.ready:
            return bridge
        # Reap a shell that died since it was last started (or clean up a
        # partially-failed start). stop() is a safe no-op on a never-started
        # bridge.
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("xsdb bridge stop before (re)start failed: %s", e)
        try:
            # Launch the xsdb shell WITHOUT auto-connecting to a hw_server;
            # the ps_connect_hw_server tool performs the explicit connect.
            await bridge.start("")
        except Exception as e:
            logger.error("xsdb bridge start failed: %s", e)
            return None
        return bridge if bridge.ready else None

    async def shutdown_xsdb_bridge(self):
        """Stop the XsdbBridge subprocess (idempotent). Safe when never started."""
        bridge = self._xsdb_bridge
        if bridge is None:
            return
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("xsdb bridge shutdown failed: %s", e)

    async def shutdown_uart_resources(self):
        if self._uart_registry is None:
            return True
        return await self._uart_registry.shutdown_all()

    # ---- B06 second batch: XsctBridge lifecycle (PS BSP/Build tools) ----

    async def _ensure_xsct_bridge(self, workspace: str = ""):
        """Lazy-init the XsctBridge. Returns the ready bridge, or None.

        BSP/Build tools use the XSCT shell (``xsct`` on PATH), not the XSDB
        shell. The bridge is started with the session's project_path as the
        xsct workspace (``setws``) on first use. Fail-closed: when no bridge
        is configured, or the shell cannot be started, returns None and the
        caller produces an explicit error.

        C3: if the shell was started earlier but has since died (a crash, an
        eval-timeout kill, or a build subprocess interaction can terminate the
        xsct process), the dead shell is reaped and re-started. A one-shot
        guard would make ``ps_get_build_status``/``ps_read_elf_info`` and every
        other BSP/Build tool fail permanently with a dead XsctBridge after a
        successful compile.
        """
        bridge = self._xsct_bridge
        if bridge is None:
            return None
        if bridge.ready:
            return bridge
        # Reap a shell that died since it was last started (or clean up a
        # partially-failed start). stop() is a safe no-op on a never-started
        # bridge.
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("xsct bridge stop before (re)start failed: %s", e)
        try:
            await bridge.start(workspace or "")
        except Exception as e:
            logger.error("xsct bridge start failed: %s", e)
            return None
        return bridge if bridge.ready else None

    async def shutdown_xsct_bridge(self):
        """Stop the XsctBridge subprocess (idempotent). Safe when never started."""
        bridge = self._xsct_bridge
        if bridge is None:
            return
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("xsct bridge shutdown failed: %s", e)

    # ---- B08: VivadoTclBridge lifecycle (PL bridge tools) ----

    async def _ensure_vivado_bridge(self):
        """Lazy-init the VivadoTclBridge. Returns the ready bridge, or None.

        Fail-closed: when no bridge is configured, or vivado cannot be
        started, returns None (the caller produces an explicit error). If the
        shell was started earlier but has since died, the dead shell is reaped
        and a fresh one started — a one-shot guard would otherwise brick every
        later PL bridge tool after any subprocess death. Unlike the old
        SingleWorkerController-managed adapter, the bridge manages its own
        subprocess (``vivado -mode tcl``), so no ledger worker state is
        involved.
        """
        bridge = self._vivado_bridge
        if bridge is None:
            return None
        if bridge.ready:
            return bridge
        # Reap a shell that died since it was last started (or clean up a
        # partially-failed start). stop() is a safe no-op on a never-started
        # bridge.
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("vivado bridge stop before (re)start failed: %s", e)
        try:
            await bridge.start()
        except Exception as e:
            logger.error("vivado bridge start failed: %s", e)
            return None
        return bridge if bridge.ready else None

    async def shutdown_vivado_bridge(self):
        """Stop the VivadoTclBridge subprocess (idempotent). Safe when never started."""
        bridge = self._vivado_bridge
        if bridge is None:
            return
        try:
            await bridge.stop()
        except Exception as e:
            logger.error("vivado bridge shutdown failed: %s", e)

    # ---- B07: VivadoAdapter lifecycle (platform atoms / sim tools) ----
    # Kept for the old-MCP paths that still need it: platform command atoms
    # (B05-R2) and the 4 PL simulation tools (B08, DEFERRED XSim adapter).

    async def _ensure_vivado_adapter(self):
        """Ensure the Vivado worker adapter is started and ready.

        Returns the ready VivadoAdapter, or None on failure (fail-closed; the
        caller produces an explicit error). Delegates to the
        SingleWorkerController so the ledger worker state remains the single
        source of truth — the adapter is never started behind the controller's
        back.
        """
        if self._worker is None:
            return None
        try:
            return await self._worker.ensure_worker()
        except Exception as e:
            logger.error("vivado adapter ensure failed: %s", e)
            return None

    async def run_command(
        self, tool_name: str, arguments: dict,
        session_id: str, board_id: str, project_path: str,
        executor: str, local_fn=None,
        resource_req=ResourceRequirement(type="NONE"),
        timeout_s: Optional[float] = None,
        next_stage: Optional[str] = None,
    ) -> dict:
        token = self._mutex.try_acquire("command", tool_name)
        if token is None:
            return _mutex_busy_response(self._mutex.busy_info(),
                                        _read_ledger_for_busy(self._guard, self._ledger_path))

        try:
            op_id = f"op-{_new_uuid()}"; sig_holder = {}
            snapshot_holder = {}

            def _admit(ledger):
                ctx = ledger.context or {}
                stg = ctx.get("current_stage", "IDLE")
                # E006: domain input revision — per-tool revision field
                domain_rev_field = _DOMAIN_INPUT_REVISION_FIELD.get(tool_name, "board_package_revision")
                rev = ctx.get(domain_rev_field, "")
                sig = request_signature(session_id, stg, tool_name, arguments, rev)
                sig_holder["sig"] = sig
                # E006: atomic execution context snapshot from same ledger read — IMMUTABLE
                snap = {}
                for k in _EXECUTION_SNAPSHOT_FIELDS:
                    snap[k] = ctx.get(k, "")
                snapshot_holder["snap"] = _MappingProxyType(snap)
                dr = ledger.dedup_registry or {}
                existing = dr.get(sig)
                if existing:
                    ao = ledger.active_operation
                    if ao and ao.get("operation_id") == existing:
                        if ao.get("status") in OP_NON_TERMINAL: raise InFlightDuplicateError(existing)
                        raise TerminalDuplicateError(existing)
                    po = ledger.previous_operation
                    if po and po.get("operation_id") == existing: raise TerminalDuplicateError(existing)
                _shared_preflight_check(ledger, tool_name, session_id, resource_req)
                ledger.execution_lane = EXECUTION_LANE_BUSY
                accepted_at = _now_iso()
                ledger.active_operation = {
                    "operation_id": op_id, "tool_name": tool_name,
                    "status": OP_ACCEPTED, "api_category": "command",
                    "session_id": snap.get("session_id", session_id),
                    "board_id": snap.get("board_id", board_id),
                    "project_path": snap.get("project_path", project_path),
                    "workflow_stage": stg, "request_signature": sig,
                    "worker_generation": (ledger.worker or {}).get("worker_generation", 0),
                    "input_artifact_revision": rev,
                    "accepted_at": accepted_at, "started_at": None,
                    "heartbeat_at": None, "finished_at": None,
                    "output_artifact_revision": None, "completion_evidence": None,
                    "error": None, "progress_pct": None,
                    **operation_contract_fields(tool_name, accepted_at, timeout_s),
                }
                if not isinstance(ledger.dedup_registry, dict): ledger.dedup_registry = {}
                ledger.dedup_registry[sig] = op_id
                return ledger

            try:
                ledger = ledger_transaction(self._guard, self._ledger_path, _admit)
            except InFlightDuplicateError as e:
                return {"status": "success", "data": {
                    "operation_id": e.args[0], "deduplicated": True, "status": "RUNNING",
                    "recommended_action": "WAIT",
                    "poll_after_s": 10}}
            except TerminalDuplicateError as e:
                return {"status": "error", "error": {
                    "code": "LOCK_BUSY", "message": f"Previous attempt {e.args[0]} completed.",
                    "details": {"reason_code": "CONFIRM_RETRY_REQUIRED",
                                "previous_operation_id": e.args[0]}}}
            except ChannelBusyError as e:
                busy_ledger = _read_ledger_for_busy(self._guard, self._ledger_path)
                return {"status": "error", "error": {
                    "code": "LOCK_BUSY", "message": f"Preflight: {e.args[0]}",
                    "details": channel_busy_details(busy_ledger, str(e.args[0]))}}
            except Exception as e:
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": f"Admission: {e}"}}

            self._op_registry.admit_cache(op_id, tool_name, OP_ACCEPTED,
                sig_holder.get("sig", ""))

            task = None; coro = None
            try:
                snapshot = snapshot_holder.get("snap", {})
                coro = self._execute(executor, tool_name, arguments, session_id,
                                     op_id, local_fn, timeout_s, snapshot, next_stage)
                task = asyncio.ensure_future(coro)
            except Exception as e:
                if coro is not None:
                    coro.close()
                self._fail_admitted(op_id, f"ensure_future: {e}", "TASK_CREATION_FAILED")
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": f"Task creation: {e}",
                    "details": {"reason_code": "TASK_CREATION_FAILED",
                                "operation_id": op_id}}}

            try:
                self._op_registry.register_task(op_id, task)
            except Exception as e:
                task.cancel()
                try: await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception): pass
                self._fail_admitted(op_id, f"register_task: {e}", "TASK_REGISTER_FAILED")
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": f"Task registration: {e}",
                    "details": {"reason_code": "TASK_REGISTER_FAILED",
                                "operation_id": op_id}}}
        finally:
            self._mutex.release("command")

        return {"status": "success", "data": {
            "operation_id": op_id, "status": "accepted",
            "ledger_sequence": ledger.ledger_sequence}}

    def _fail_admitted(self, op_id, message, reason_code):
        tr = op_transition(self._guard, self._ledger_path, op_id, OP_FAILED,
                           error=message, reason_code=reason_code)
        if tr.get("status") != "success":
            logger.error("FAILED transition for %s: %s", op_id, tr)
        else:
            self._op_registry.transition(op_id, OP_FAILED,
                error_code="INTERNAL_ERROR", error_message=message,
                reason_code=reason_code)
        self._op_registry.remove_cache(op_id)

    def _check_trans(self, tr, op_id, default_rc):
        if tr.get("status") != "success":
            logger.error("Transition failed for %s: %s", op_id, tr)
            return False
        return True

    async def _execute(self, executor, tool_name, arguments, session_id, op_id,
                       local_fn, timeout_s, snapshot=None, next_stage=None):
        try:
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_RUNNING)
            if not self._check_trans(tr, op_id, "OP_RUNNING"):
                self._op_registry.transition(op_id, OP_FAILED,
                    error_code="INTERNAL_ERROR",
                    error_message=f"RUNNING transition: {tr}",
                    reason_code="LEDGER_WRITE_FAILED")
                self._op_registry.unregister_task(op_id)
                return
            self._op_registry.transition(op_id, OP_RUNNING)

            if executor == "local" and local_fn is not None:
                if tool_name.startswith("ps_"):
                    # B06: PS domain tools receive a bridge as the first
                    # positional argument. JTAG/UART tools use the XsdbBridge;
                    # BSP/Build tools use the XsctBridge (started once with the
                    # session's project_path as the xsct workspace). A missing
                    # or unstartable bridge fails the operation (fail-closed,
                    # never a crash).
                    if tool_name in _PS_UART_CAPTURE_TOOLS:
                        if self._uart_registry is None:
                            bridge = None
                        else:
                            bridge = self._uart_registry.facade(session_id, op_id)
                        missing_msg = ("UART resource registry is not configured")
                    elif tool_name in (_PS_UART_DIRECT_TOOLS |
                                       _PS_LOCAL_DIRECT_TOOLS):
                        bridge = None
                        missing_msg = ""
                    elif tool_name in _PS_XSCT_TOOL_NAMES:
                        ws = ""
                        if snapshot is not None:
                            ws = str(snapshot.get("project_path", "") or "")
                        # Tcl treats backslashes as escapes: normalize Windows
                        # paths to forward slashes before `setws` in xsct.
                        ws = ws.replace("\\", "/")
                        if self._process_controller is not None:
                            try:
                                bridge = await self._ensure_controlled_xsct(
                                    op_id, ws)
                            except Exception as exc:
                                self._terminal_failed(op_id, {
                                    "status": "error", "error": {
                                        "code": "TOOL_ERROR",
                                        "message": str(exc),
                                        "details": {"reason_code": getattr(
                                            exc, "reason_code",
                                            "WORKER_START_FAILED")}}},
                                    getattr(exc, "reason_code",
                                            "WORKER_START_FAILED"))
                                return
                        else:
                            bridge = await self._ensure_xsct_bridge(ws)
                        missing_msg = ("XsctBridge is not ready; BSP/Build tools "
                                       "require an XSCT shell (xsct on PATH)")
                    else:
                        if self._process_controller is not None:
                            try:
                                bridge = await self._ensure_controlled_xsdb(
                                    op_id, session_id)
                            except Exception as exc:
                                self._terminal_failed(op_id, {
                                    "status": "error", "error": {
                                        "code": "JTAG_ERROR",
                                        "message": str(exc),
                                        "details": {"reason_code": getattr(
                                            exc, "reason_code",
                                            "WORKER_START_FAILED")}}},
                                    getattr(exc, "reason_code",
                                            "WORKER_START_FAILED"))
                                return
                        else:
                            bridge = await self._ensure_xsdb_bridge()
                        missing_msg = ("XsdbBridge is not ready; PS tools require "
                                       "an XSDB shell (xsdb on PATH)")
                    if bridge is None and tool_name not in (
                            _PS_UART_DIRECT_TOOLS | _PS_LOCAL_DIRECT_TOOLS):
                        self._terminal_failed(op_id, {"status": "error", "error": {
                            "code": "TOOL_ERROR",
                            "message": missing_msg,
                            "details": {"reason_code": "BRIDGE_NOT_READY"}}},
                            "BRIDGE_NOT_READY")
                        self._op_registry.unregister_task(op_id)
                        return
                    if bridge is not None and hasattr(bridge, "set_current_step") \
                            and tool_name in _JTAG_OBSERVATION_STEP:
                        bridge.set_current_step(_JTAG_OBSERVATION_STEP[tool_name])
                    result = await asyncio.wait_for(
                        local_fn(bridge, **arguments), timeout_s or 300)
                    if tool_name in _PS_UART_DIRECT_TOOLS and \
                            self._uart_registry is not None:
                        self._uart_registry.record_ephemeral_result(
                            session_id, op_id, tool_name, arguments, result)
                    if bridge is not None and hasattr(bridge, "record_tool_result") \
                            and tool_name in _JTAG_OBSERVATION_STEP:
                        bridge.record_tool_result(tool_name, arguments, result)
                elif tool_name in _PL_XSDB_TOOLS:
                    # B07 fix: PL tools that run on the XSDB shell (currently
                    # pl_program_fpga) receive the XsdbBridge as the first
                    # positional argument, NOT the VivadoAdapter. The bridge is
                    # lazy-started like the PS tools; a missing or unstartable
                    # bridge fails the operation (fail-closed, never a crash).
                    if self._process_controller is not None:
                        try:
                            bridge = await self._ensure_controlled_xsdb(
                                op_id, session_id)
                        except Exception as exc:
                            self._terminal_failed(op_id, {
                                "status": "error", "error": {
                                    "code": "JTAG_ERROR", "message": str(exc),
                                    "details": {"reason_code": getattr(
                                        exc, "reason_code",
                                        "WORKER_START_FAILED")}}},
                                getattr(exc, "reason_code",
                                        "WORKER_START_FAILED"))
                            return
                    else:
                        bridge = await self._ensure_xsdb_bridge()
                    if bridge is None:
                        self._terminal_failed(op_id, {"status": "error", "error": {
                            "code": "TOOL_ERROR",
                            "message": "XsdbBridge is not ready; PL fpga tools "
                                       "require an XSDB shell (xsdb on PATH)",
                            "details": {"reason_code": "BRIDGE_NOT_READY"}}},
                            "BRIDGE_NOT_READY")
                        self._op_registry.unregister_task(op_id)
                        return
                    if hasattr(bridge, "set_current_step"):
                        bridge.set_current_step(
                            _JTAG_OBSERVATION_STEP.get(tool_name, tool_name.upper()))
                    result = await asyncio.wait_for(
                        local_fn(bridge, **arguments), timeout_s or 300)
                    if hasattr(bridge, "record_tool_result"):
                        bridge.record_tool_result(tool_name, arguments, result)
                elif getattr(local_fn, '_pl_bridge', False):
                    # B08: PL bridge tools receive the VivadoTclBridge as the
                    # first positional argument — a direct `vivado -mode tcl`
                    # subprocess (no old-MCP stdio middle layer). The bridge is
                    # lazy-started; a missing or unstartable bridge fails the
                    # operation (fail-closed, never a crash). Simulation tools
                    # (_PL_OLD_ADAPTER_TOOLS) still get the old VivadoAdapter.
                    if self._process_controller is not None and \
                            tool_name not in _PL_OLD_ADAPTER_TOOLS:
                        try:
                            bridge = await self._ensure_controlled_vivado(op_id)
                        except Exception as exc:
                            self._terminal_failed(op_id, {
                                "status": "error", "error": {
                                    "code": "TOOL_ERROR",
                                    "message": str(exc),
                                    "details": {
                                        "reason_code": getattr(
                                            exc, "reason_code",
                                            "WORKER_START_FAILED")}}},
                                getattr(exc, "reason_code",
                                        "WORKER_START_FAILED"))
                            return
                        bridge.set_current_step(
                            _PL_OBSERVATION_STEP.get(
                                tool_name, tool_name.upper()))
                        result = await local_fn(bridge, **arguments)
                    elif tool_name in _PL_OLD_ADAPTER_TOOLS:
                        adapter = await self._ensure_vivado_adapter()
                        if adapter is None:
                            self._terminal_failed(op_id, {"status": "error", "error": {
                                "code": "TOOL_ERROR",
                                "message": "Vivado worker not available; "
                                           "simulation tools require the "
                                           "Vivado adapter",
                                "details": {"reason_code": "ADAPTER_NOT_READY"}}},
                                "ADAPTER_NOT_READY")
                            self._op_registry.unregister_task(op_id)
                            return
                        result = await asyncio.wait_for(
                            local_fn(adapter, **arguments), timeout_s or 720)
                    else:
                        bridge = await self._ensure_vivado_bridge()
                        if bridge is None:
                            self._terminal_failed(op_id, {"status": "error", "error": {
                                "code": "TOOL_ERROR",
                                "message": "VivadoTclBridge is not ready; PL "
                                           "tools require the Vivado Tcl bridge",
                                "details": {"reason_code": "BRIDGE_NOT_READY"}}},
                                "BRIDGE_NOT_READY")
                            self._op_registry.unregister_task(op_id)
                            return
                        if tool_name in _LONG_RUN_TOOLS:
                            # Long-run bridge tool (synth/place/route): NO
                            # wait_for — the async launch_runs/wait_on_run
                            # bridge call bounds the wait itself (PL_TOOL_MAP
                            # timeout, 3660s). A background task refreshes
                            # heartbeat_at every 30s so get_operation_status
                            # never reports a stale op during a legitimately
                            # 5-30 min run.
                            result = await local_fn(bridge, **arguments)
                        else:
                            result = await asyncio.wait_for(
                                local_fn(bridge, **arguments), timeout_s or 720)
                elif getattr(local_fn, '_pl_adapter', False):
                    # B05-R2: platform command atoms still receive the old
                    # VivadoAdapter (they call adapter.call_tool("run_tcl", ...),
                    # which the VivadoTclBridge does not provide). The adapter
                    # is lazy-started via the SingleWorkerController; a missing
                    # or unstartable adapter fails the operation (fail-closed,
                    # never a crash).
                    if self._process_controller is not None:
                        try:
                            adapter = await self._ensure_controlled_vivado(op_id)
                        except Exception as exc:
                            self._terminal_failed(op_id, {
                                "status": "error", "error": {
                                    "code": "TOOL_ERROR", "message": str(exc),
                                    "details": {"reason_code": getattr(
                                        exc, "reason_code",
                                        "WORKER_START_FAILED")}}},
                                getattr(exc, "reason_code",
                                        "WORKER_START_FAILED"))
                            return
                    else:
                        adapter = await self._ensure_vivado_adapter()
                    if adapter is None:
                        self._terminal_failed(op_id, {"status": "error", "error": {
                            "code": "TOOL_ERROR",
                            "message": "Vivado worker not available; PL tools "
                                       "require the Vivado adapter",
                            "details": {"reason_code": "ADAPTER_NOT_READY"}}},
                            "ADAPTER_NOT_READY")
                        self._op_registry.unregister_task(op_id)
                        return
                    result = await asyncio.wait_for(
                        local_fn(adapter, **arguments), timeout_s or 720)
                elif getattr(local_fn, '_controlled_vivado_contextual', False):
                    try:
                        adapter = await self._ensure_controlled_vivado(op_id)
                    except Exception as exc:
                        self._terminal_failed(op_id, {
                            "status": "error", "error": {
                                "code": "TOOL_ERROR", "message": str(exc),
                                "details": {"reason_code": getattr(
                                    exc, "reason_code",
                                    "WORKER_START_FAILED")}}},
                            getattr(exc, "reason_code", "WORKER_START_FAILED"))
                        return
                    result = await asyncio.wait_for(
                        local_fn(adapter, arguments, snapshot), timeout_s or 720)
                elif getattr(local_fn, '_contextual', False):
                    # E006: contextual local executors receive (arguments, snapshot)
                    result = await asyncio.wait_for(
                        local_fn(arguments, snapshot), timeout_s or 300)
                else:
                    # Plain local executors receive (arguments)
                    result = await asyncio.wait_for(
                        local_fn(arguments), timeout_s or 300)
            elif executor == "worker" and self._worker is not None:
                result = await self._worker.execute_tool(
                    tool_name, arguments, session_id, timeout_s=timeout_s)
            else:
                self._fail_admitted(op_id, f"No executor: {executor}", "NO_EXECUTOR")
                self._op_registry.unregister_task(op_id)
                return

            if not isinstance(result, dict):
                self._terminal_success(op_id, {"data": str(result)}, next_stage)
                return
            if result.get("status") == "success":
                ctx_updates = result.pop("_context_updates", None) if isinstance(result, dict) else None
                # Extract output_artifact_revision from result data
                oar = None
                completion_evidence = None
                terminal_observation = None
                artifact_state = None
                if isinstance(result, dict):
                    rd = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
                    oar = rd.get("platform_revision")
                    # P7 evidence: a strict-bool `timing_met` in the tool's
                    # success data (produced by pl_analyze_timing) is surfaced
                    # as completion evidence so the next gate
                    # (pl_generate_bitstream) can verify timing closed.
                    tm = rd.get("timing_met")
                    if isinstance(tm, bool):
                        completion_evidence = {"timing_met": tm}

                if tool_name == "platform_generate":
                    manifest_path = rd.get("manifest_path")
                    evidence = _verified_manifest_evidence(
                        manifest_path,
                        expected_sha=rd.get("manifest_sha256"),
                        revision_keys=("platform_revision", "manifest_revision"),
                    )
                    if evidence is None:
                        self._terminal_failed(op_id, {
                            "status": "error", "error": {
                                "code": "ARTIFACT_STALE",
                                "message": "Platform manifest verification failed",
                                "details": {
                                    "reason_code": "MANIFEST_PUBLISH_FAILED"}}},
                            "MANIFEST_PUBLISH_FAILED", artifact_failed=True,
                            artifact_step="PLATFORM_MANIFEST_PUBLISH")
                        return
                    completion_evidence = {
                        **(completion_evidence or {}), **evidence}
                    terminal_observation = _local_artifact_observation(
                        "PLATFORM_MANIFEST_PUBLISH", "COMPLETE")
                    artifact_state = "PUBLISHED"
                    oar = evidence["manifest_revision"]

                if tool_name == "pl_generate_bitstream":
                    op_observe(
                        self._guard, self._ledger_path, op_id,
                        _local_artifact_observation(
                            "BITSTREAM_VERIFY", "RUNNING"),
                        artifact_state="VERIFYING")
                    op_observe(
                        self._guard, self._ledger_path, op_id,
                        _local_artifact_observation(
                            "PL_MANIFEST_PUBLISH", "RUNNING"),
                        artifact_state="PUBLISHING_MANIFEST")
                    manifest_path = _publish_build_manifest(
                        tool_name, snapshot, result, arguments)
                    evidence = _verified_manifest_evidence(manifest_path)
                    if evidence is None:
                        self._terminal_failed(op_id, {
                            "status": "error", "error": {
                                "code": "ARTIFACT_STALE",
                                "message": "PL build manifest publication failed",
                                "details": {
                                    "reason_code": "MANIFEST_PUBLISH_FAILED"}}},
                            "MANIFEST_PUBLISH_FAILED", artifact_failed=True,
                            artifact_step="PL_MANIFEST_PUBLISH")
                        return
                    result.setdefault("data", {}).update({
                        "pl_manifest_path": evidence["manifest_path"],
                        "pl_manifest_sha256": evidence["manifest_sha256"],
                        "pl_manifest_revision": evidence["manifest_revision"],
                    })
                    completion_evidence = {
                        **(completion_evidence or {}), **evidence}
                    terminal_observation = _local_artifact_observation(
                        "PL_MANIFEST_PUBLISH", "COMPLETE")
                    artifact_state = "PUBLISHED"
                    oar = evidence["manifest_revision"]

                    # VIVADO must be gone before the stage advances to
                    # PS_BUILD, otherwise O4 cannot lawfully acquire XSCT.
                    if self._process_controller is not None:
                        cleaned = await self._process_controller.shutdown_backend(
                            operation_id=op_id)
                        if not cleaned.success:
                            actual = _get_ledger_op_status(
                                self._guard, self._ledger_path, op_id)
                            self._op_registry.transition(
                                op_id, actual or OP_OUTCOME_UNKNOWN,
                                error_code="INTERNAL_ERROR",
                                error_message="Vivado cleanup not proven",
                                reason_code=(cleaned.reason_code or
                                             "BACKEND_CLEANUP_FAILED"))
                            return

                if tool_name == "ps_compile":
                    op_observe(
                        self._guard, self._ledger_path, op_id,
                        _local_artifact_observation(
                            "ELF_VERIFY", "RUNNING"),
                        artifact_state="VERIFYING")
                    elf_evidence = _verify_ps_elf(result, snapshot)
                    if elf_evidence is None:
                        self._terminal_failed(op_id, {
                            "status": "error", "error": {
                                "code": "ARTIFACT_STALE",
                                "message": "PS ELF verification failed",
                                "details": {
                                    "reason_code": "ELF_VERIFY_FAILED"}}},
                            "ELF_VERIFY_FAILED", artifact_failed=True,
                            artifact_step="ELF_VERIFY")
                        return
                    op_observe(
                        self._guard, self._ledger_path, op_id,
                        _local_artifact_observation(
                            "MANIFEST_PUBLISH", "RUNNING"),
                        artifact_state="PUBLISHING_MANIFEST")
                    manifest_path = _publish_build_manifest(
                        tool_name, snapshot, result, arguments)
                    evidence = _verified_manifest_evidence(manifest_path)
                    if evidence is None:
                        self._terminal_failed(op_id, {
                            "status": "error", "error": {
                                "code": "ARTIFACT_STALE",
                                "message": "PS build manifest publication failed",
                                "details": {
                                    "reason_code": "MANIFEST_PUBLISH_FAILED"}}},
                            "MANIFEST_PUBLISH_FAILED", artifact_failed=True,
                            artifact_step="MANIFEST_PUBLISH")
                        return
                    result.setdefault("data", {}).update({
                        "ps_manifest_path": evidence["manifest_path"],
                        "ps_manifest_sha256": evidence["manifest_sha256"],
                        "ps_manifest_revision": evidence["manifest_revision"],
                    })
                    completion_evidence = {
                        **(completion_evidence or {}),
                        **elf_evidence, **evidence}
                    terminal_observation = _local_artifact_observation(
                        "MANIFEST_PUBLISH", "COMPLETE")
                    artifact_state = "PUBLISHED"
                    oar = evidence["manifest_revision"]

                    # XSCT must be gone before the next hardware/debug phase
                    # can acquire XSDB.  A cleanup result is part of success.
                    if self._process_controller is not None:
                        cleaned = await self._process_controller.shutdown_backend(
                            operation_id=op_id)
                        if not cleaned.success:
                            actual = _get_ledger_op_status(
                                self._guard, self._ledger_path, op_id)
                            self._op_registry.transition(
                                op_id, actual or OP_OUTCOME_UNKNOWN,
                                error_code="INTERNAL_ERROR",
                                error_message="XSCT cleanup not proven",
                                reason_code=(cleaned.reason_code or
                                             "BACKEND_CLEANUP_FAILED"))
                            return

                if tool_name == "ps_disconnect_hw_server" and \
                        self._process_controller is not None:
                    # A disconnected XSDB shell has no reusable JTAG state.
                    # Prove its PID is gone before publishing command success,
                    # so the next operation can lawfully acquire another EDA
                    # backend through the single channel.
                    cleaned = await self._process_controller.shutdown_backend(
                        operation_id=op_id)
                    if not cleaned.success:
                        actual = _get_ledger_op_status(
                            self._guard, self._ledger_path, op_id)
                        self._op_registry.transition(
                            op_id, actual or OP_OUTCOME_UNKNOWN,
                            error_code="INTERNAL_ERROR",
                            error_message="XSDB cleanup not proven",
                            reason_code=(cleaned.reason_code or
                                         "BACKEND_CLEANUP_FAILED"))
                        return

                self._terminal_success(op_id, result, next_stage,
                                       context_updates=ctx_updates,
                                       output_artifact_revision=oar,
                                       completion_evidence=completion_evidence,
                                       artifact_state=artifact_state,
                                       observation=terminal_observation)
                return

            details = result.get("error", {}).get("details", {})
            rc = details.get("reason_code", "OPERATION_FAILED")
            lp = details.get("ledger_persisted")

            if rc == "VIVADO_TIMEOUT":
                self._handle_worker_terminal(op_id, OP_TIMED_OUT, result, lp, "VIVADO_TIMEOUT"); return
            if rc == "OPERATION_OUTCOME_UNKNOWN":
                self._handle_worker_terminal(op_id, OP_OUTCOME_UNKNOWN, result, lp, "OPERATION_OUTCOME_UNKNOWN"); return
            if tool_name == "ps_compile":
                self._terminal_failed(
                    op_id, result, rc, artifact_failed=True,
                    artifact_step=("MAKE_FALLBACK" if rc == "BUILD_FAILED"
                                   else "APP_BUILD"))
            else:
                self._terminal_failed(op_id, result, rc)
        except asyncio.TimeoutError:
            if self._process_controller is not None and \
                    self._process_controller.has_backend:
                cleaned = await self._process_controller.shutdown_backend(
                    operation_id=op_id)
                if not cleaned.success:
                    actual = _get_ledger_op_status(
                        self._guard, self._ledger_path, op_id)
                    self._op_registry.transition(
                        op_id, actual or OP_OUTCOME_UNKNOWN,
                        error_code="INTERNAL_ERROR",
                        error_message="Deadline cleanup not proven",
                        reason_code=(cleaned.reason_code or
                                     "BACKEND_CLEANUP_FAILED"))
                    return
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_TIMED_OUT)
            if self._check_trans(tr, op_id, "TIMED_OUT"):
                self._op_registry.transition(op_id, OP_TIMED_OUT,
                    error_code="TOOL_ERROR", error_message="Timed out", reason_code="OP_TIMED_OUT")
            else:
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR", error_message=f"Terminal write failed: {tr}",
                    reason_code="LEDGER_WRITE_FAILED")
        except asyncio.CancelledError:
            if self._process_controller is not None and \
                    self._process_controller.has_backend:
                cleaned = await self._process_controller.shutdown_backend(
                    operation_id=op_id)
                if not cleaned.success:
                    actual = _get_ledger_op_status(
                        self._guard, self._ledger_path, op_id)
                    self._op_registry.transition(
                        op_id, actual or OP_OUTCOME_UNKNOWN,
                        error_code="INTERNAL_ERROR",
                        error_message="Interrupt cleanup not proven",
                        reason_code=(cleaned.reason_code or
                                     "BACKEND_CLEANUP_FAILED"))
                    return
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_INTERRUPTED)
            if self._check_trans(tr, op_id, "INTERRUPTED"):
                self._op_registry.transition(op_id, OP_INTERRUPTED,
                    error_code="INTERNAL_ERROR", error_message="Cancelled", reason_code="OP_INTERRUPTED")
        except DomainValidationError as e:
            self._terminal_failed(op_id, {"status": "error", "error": {
                "code": "INVALID_ARGUMENT", "message": str(e),
                "details": {"reason_code": "INVALID_ARGUMENT"}}}, "INVALID_ARGUMENT")
        except DomainOutcomeUnknownError as e:
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_OUTCOME_UNKNOWN,
                              error=str(e), reason_code="OP_OUTCOME_UNKNOWN")
            if self._check_trans(tr, op_id, "OUTCOME_UNKNOWN"):
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR", error_message=str(e), reason_code="OP_OUTCOME_UNKNOWN")
            else:
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR", error_message=f"Terminal write failed: {tr}",
                    reason_code="LEDGER_WRITE_FAILED")
        except Exception as e:
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_OUTCOME_UNKNOWN,
                              error=str(e), reason_code="OP_OUTCOME_UNKNOWN")
            if self._check_trans(tr, op_id, "OUTCOME_UNKNOWN"):
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR", error_message=str(e), reason_code="OP_OUTCOME_UNKNOWN")
            else:
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR", error_message=f"Terminal write failed: {tr}",
                    reason_code="LEDGER_WRITE_FAILED")
        finally:
            self._op_registry.unregister_task(op_id)

    def _terminal_success(self, op_id, result, next_stage=None, context_updates=None,
                          output_artifact_revision=None, completion_evidence=None,
                          artifact_state=None, observation=None):
        kwargs = {}
        if output_artifact_revision is not None:
            kwargs["output_artifact_revision"] = output_artifact_revision
        # op_transition writes every explicit field into the operation record
        # (even without a stage advance); on a stage advance it merges the
        # provided evidence with stage_advanced_from/to.
        if completion_evidence is not None:
            kwargs["completion_evidence"] = completion_evidence
        if artifact_state is not None:
            kwargs["artifact_state"] = artifact_state
        if observation is not None:
            kwargs["observation"] = observation
        tr = op_transition(self._guard, self._ledger_path, op_id, OP_SUCCEEDED,
                          next_stage=next_stage, context_updates=context_updates,
                          result=result, **kwargs)
        if self._check_trans(tr, op_id, "SUCCEEDED"):
            self._op_registry.transition(op_id, OP_SUCCEEDED)
        else:
            self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                error_code="INTERNAL_ERROR", error_message=f"Terminal write failed: {tr}",
                reason_code="LEDGER_WRITE_FAILED")

    def _terminal_failed(self, op_id, result, rc, *, artifact_failed=False,
                         artifact_step="ARTIFACT_VERIFY"):
        kwargs = {}
        if artifact_failed:
            kwargs["artifact_state"] = "FAILED"
            kwargs["observation"] = _local_artifact_observation(
                artifact_step, "FAILED")
        tr = op_transition(self._guard, self._ledger_path, op_id, OP_FAILED,
            error=str(result),
            reason_code=rc, **kwargs)
        if self._check_trans(tr, op_id, "FAILED"):
            self._op_registry.transition(op_id, OP_FAILED,
                error_code=result.get("error", {}).get("code", "INTERNAL_ERROR"),
                error_message=str(result), reason_code=rc)
        else:
            self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                error_code="INTERNAL_ERROR", error_message=f"Terminal write failed: {tr}",
                reason_code="LEDGER_WRITE_FAILED")

    def _handle_worker_terminal(self, op_id, expected_status, result, lp, rc):
        if lp is True:
            actual = _get_ledger_op_status(self._guard, self._ledger_path, op_id)
            if actual is None:
                tr = op_transition(self._guard, self._ledger_path, op_id, OP_OUTCOME_UNKNOWN,
                    error="ledger_persisted=true but no terminal in ledger",
                    reason_code="LEDGER_STATE_INCONSISTENT")
                if self._check_trans(tr, op_id, "INCONSISTENT"):
                    self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                        error_code="INTERNAL_ERROR",
                        error_message="Worker claimed persisted but ledger missing",
                        reason_code="LEDGER_STATE_INCONSISTENT")
                else:
                    self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                        error_code="INTERNAL_ERROR",
                        error_message=f"Terminal write failed: {tr}",
                        reason_code="LEDGER_WRITE_FAILED")
            else:
                self._op_registry.transition(op_id, actual,
                    error_code="TOOL_ERROR" if rc == "VIVADO_TIMEOUT" else "INTERNAL_ERROR",
                    error_message=str(result), reason_code=rc)
        elif lp is False or lp is None:
            tr = op_transition(self._guard, self._ledger_path, op_id, expected_status,
                error=str(result), reason_code=rc)
            if self._check_trans(tr, op_id, expected_status):
                self._op_registry.transition(op_id, expected_status,
                    error_code="TOOL_ERROR" if rc == "VIVADO_TIMEOUT" else "INTERNAL_ERROR",
                    error_message=str(result), reason_code=rc)
            else:
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR",
                    error_message=f"Terminal write failed: {tr}",
                    reason_code="LEDGER_WRITE_FAILED")
        else:
            logger.error("Non-bool ledger_persisted=%s for %s", lp, op_id)
            tr = op_transition(self._guard, self._ledger_path, op_id, OP_OUTCOME_UNKNOWN,
                error=f"Non-bool ledger_persisted: {lp}",
                reason_code="LEDGER_STATE_INCONSISTENT")
            if self._check_trans(tr, op_id, "INCONSISTENT"):
                self._op_registry.transition(op_id, OP_OUTCOME_UNKNOWN,
                    error_code="INTERNAL_ERROR",
                    error_message=f"Non-bool ledger_persisted: {lp}",
                    reason_code="LEDGER_STATE_INCONSISTENT")


def _get_ledger_op_status(guard, ledger_path, op_id):
    try:
        l, _ = ledger_read_shared(guard, ledger_path)
        for src in (l.active_operation, l.previous_operation):
            if src and src.get("operation_id") == op_id and src.get("status") in OP_TERMINAL:
                return src.get("status")
    except Exception as exc:
        logger.error("Ledger terminal evidence read failed for %s: %s", op_id, exc)
    return None


def _local_artifact_observation(step: str, observed_state: str) -> dict:
    """Build a complete LOCAL observation for an artifact terminal phase."""
    from mcps.zynq_mcp.control.execution_ledger import (
        STATUS_SOURCE_LOCAL, BACKEND_PYTHON, HEALTH_NOT_APPLICABLE,
    )
    now = _now_iso()
    return {
        "status_source": STATUS_SOURCE_LOCAL,
        "backend": BACKEND_PYTHON,
        "observed_state": observed_state,
        "vendor_status": None,
        "current_step": step,
        "progress_pct": None,
        "worker_health": HEALTH_NOT_APPLICABLE,
        "pid": None,
        "process_start_time": None,
        "executable_path": None,
        "worker_generation": 0,
        "instance_id": None,
        "controller_heartbeat_at": None,
        "observed_at": now,
        "last_output_at": now,
        "detail": {},
    }


def _verified_manifest_evidence(path, *, expected_sha=None,
                                revision_keys=("manifest_revision",)):
    """Read back a published manifest and return evidence or ``None``."""
    import json as _json
    import os as _os
    from mcps.common.revision import is_sha256, sha256_file
    if not isinstance(path, str) or not path.strip() or not _os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            manifest = _json.load(stream)
        actual_sha = sha256_file(path)
    except (OSError, ValueError, _json.JSONDecodeError):
        return None
    if expected_sha is not None and actual_sha != expected_sha:
        return None
    revision = None
    for key in revision_keys:
        candidate = manifest.get(key) if isinstance(manifest, dict) else None
        if isinstance(candidate, str) and is_sha256(candidate):
            revision = candidate
            break
    if revision is None:
        return None
    return {
        "manifest_path": path,
        "manifest_sha256": actual_sha,
        "manifest_revision": revision,
    }


def _verify_ps_elf(result, snapshot):
    """Return grounded Zynq-7000 ELF evidence, or ``None`` fail-closed."""
    import os as _os
    from mcps.common.revision import sha256_file

    data = result.get("data") if isinstance(result, dict) else None
    raw = data.get("elf") if isinstance(data, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    project_path = ""
    try:
        project_path = str(snapshot.get("project_path", "") or "")
    except Exception:
        return None
    if not project_path:
        return None
    candidate = (raw if _os.path.isabs(raw)
                 else _os.path.join(project_path, raw))
    project_real = _os.path.realpath(project_path)
    elf_real = _os.path.realpath(candidate)
    try:
        if _os.path.commonpath([project_real, elf_real]) != project_real:
            return None
    except ValueError:
        return None
    if not _os.path.isfile(elf_real):
        return None
    try:
        with open(elf_real, "rb") as stream:
            header = stream.read(52)
    except OSError:
        return None
    if len(header) < 52 or header[:4] != b"\x7fELF":
        return None
    # Zynq-7000 PS is 32-bit little-endian ARM (EM_ARM == 40).
    if header[4] != 1 or header[5] != 1:
        return None
    machine = int.from_bytes(header[18:20], "little")
    if machine != 40:
        return None
    return {
        "elf_path": elf_real,
        "elf_sha256": sha256_file(elf_real),
        "elf_class": "ELFCLASS32",
        "elf_machine": machine,
        "elf_architecture": "ARM",
    }


# ---- Set Runner ----
class SetRunner:
    def __init__(self, guard, ledger_path,
                 mutex: DomainExecutionMutex, worker=None):
        self._guard = guard; self._ledger_path = ledger_path
        self._mutex = mutex; self._worker = worker

    async def run_set(self, tool_name: str, arguments: dict,
                      session_id: str, board_id: str, project_path: str,
                      resource_req=ResourceRequirement(type="NONE"),
                      timeout_s: Optional[float] = None) -> dict:
        token = self._mutex.try_acquire("set", tool_name)
        if token is None:
            return _mutex_busy_response(self._mutex.busy_info(),
                                        _read_ledger_for_busy(self._guard, self._ledger_path))
        try:
            def _p(l):
                _shared_preflight_check(l, tool_name, session_id, resource_req)
                l.execution_lane = EXECUTION_LANE_BUSY; return l
            try: ledger_transaction(self._guard, self._ledger_path, _p)
            except ChannelBusyError as e:
                busy_ledger = _read_ledger_for_busy(self._guard, self._ledger_path)
                return {"status": "error", "error": {
                    "code": "LOCK_BUSY", "message": f"Set preflight: {e.args[0]}",
                    "details": channel_busy_details(busy_ledger, str(e.args[0]))}}
            if self._worker is None:
                def _idle(l): l.execution_lane = EXECUTION_LANE_IDLE; return l
                ledger_transaction(self._guard, self._ledger_path, _idle)
                return {"status": "error", "error": {
                    "code": "TOOL_ERROR", "message": "No worker", "details": {"reason_code": "ADAPTER_NOT_READY"}}}
            try:
                result = await asyncio.wait_for(
                    self._worker.execute_tool(tool_name, arguments, session_id, timeout_s=timeout_s),
                    timeout_s or 60)
            except asyncio.TimeoutError:
                def _to(l): l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
                ledger_transaction(self._guard, self._ledger_path, _to)
                return {"status": "error", "error": {
                    "code": "TOOL_ERROR", "message": f"Set '{tool_name}' timed out",
                    "details": {"reason_code": "OP_TIMED_OUT"}}}
            except asyncio.CancelledError:
                def _cr(l): l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
                try: ledger_transaction(self._guard, self._ledger_path, _cr)
                except Exception as le: logger.error("Set cancel: %s", le)
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": "Set cancelled",
                    "details": {"reason_code": "OP_INTERRUPTED"}}}
            except Exception as e:
                def _cr(l): l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
                try: ledger_transaction(self._guard, self._ledger_path, _cr)
                except Exception as le: logger.error("Set crash: %s", le)
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": str(e),
                    "details": {"reason_code": "OP_OUTCOME_UNKNOWN"}}}
            if isinstance(result, dict) and result.get("status") == "error":
                def _rec(l): l.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED; return l
                ledger_transaction(self._guard, self._ledger_path, _rec)
                return result
            def _ok(l): l.execution_lane = EXECUTION_LANE_IDLE; return l
            ledger_transaction(self._guard, self._ledger_path, _ok)
            return {"status": "success",
                    "data": result if isinstance(result, dict) else {"result": str(result)}}
        finally:
            self._mutex.release("set")


# ---- Query Runner ----
class QueryRunner:
    def __init__(self, guard, ledger_path,
                 mutex: DomainExecutionMutex, worker=None):
        self._guard = guard; self._ledger_path = ledger_path
        self._mutex = mutex; self._worker = worker

    async def run_query(self, tool_name: str, arguments: dict,
                        session_id: str = "",
                        resource_req=ResourceRequirement(type="NONE"),
                        timeout_s: Optional[float] = None) -> dict:
        token = self._mutex.try_acquire("query", tool_name)
        if token is None:
            return _mutex_busy_response(self._mutex.busy_info(),
                                        _read_ledger_for_busy(self._guard, self._ledger_path))
        try:
            try: cur_ledger, _ = ledger_read_shared(self._guard, self._ledger_path)
            except Exception as e:
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": f"Ledger read: {e}",
                    "details": {"reason_code": "LEDGER_READ_FAILED"}}}
            try: _shared_preflight_check(cur_ledger, tool_name, session_id, resource_req)
            except ChannelBusyError as e:
                return {"status": "error", "error": {
                    "code": "LOCK_BUSY", "message": f"Query preflight: {e.args[0]}",
                    "details": channel_busy_details(cur_ledger, str(e.args[0]))}}
            if self._worker is None or not self._worker.has_worker:
                return {"status": "error", "error": {
                    "code": "TOOL_ERROR", "message": "No worker",
                    "details": {"reason_code": "ADAPTER_NOT_READY"}}}
            try:
                result = await asyncio.wait_for(
                    self._worker.execute_tool(tool_name, arguments, session_id, timeout_s=timeout_s),
                    timeout_s or 30)
            except asyncio.TimeoutError:
                return {"status": "error", "error": {
                    "code": "TOOL_ERROR", "message": "Query timed out",
                    "details": {"reason_code": "OP_TIMED_OUT"}}}
            except Exception as e:
                return {"status": "error", "error": {
                    "code": "INTERNAL_ERROR", "message": str(e),
                    "details": {"reason_code": "QUERY_FAILED"}}}
            if isinstance(result, dict): return result
            return {"status": "success", "data": {"result": str(result)}}
        finally:
            self._mutex.release("query")


def _new_uuid():
    import uuid
    return uuid.uuid4().hex


# ---- PL API Contracts (B01 frozen, single source of truth for R3.1-R3.5) ----
PL_API_CONTRACTS = [
    {"name": "pl_generate_system_top", "category": "command",
     "arg_names": ["wrapper_path"]},
    {"name": "pl_create_project", "category": "command",
     "arg_names": ["name", "part", "sources", "constraints"]},
    {"name": "pl_set_top", "category": "set",
     "arg_names": ["module"]},
    {"name": "pl_synthesize", "category": "command",
     "arg_names": []},
    {"name": "pl_place_and_route", "category": "command",
     "arg_names": []},
    {"name": "pl_analyze_timing", "category": "command",
     "arg_names": []},
    {"name": "pl_generate_bitstream", "category": "command",
     "arg_names": ["path"]},
    {"name": "pl_connect_hw_server", "category": "command",
     "arg_names": []},
    {"name": "pl_open_hw_target", "category": "command",
     "arg_names": []},
    {"name": "pl_select_device", "category": "set",
     "arg_names": ["id"]},
    {"name": "pl_program", "category": "command",
     "arg_names": ["bitstream"]},
    {"name": "pl_get_device_status", "category": "query",
     "arg_names": []},
]

# Verify invariants at import time
_PL_CMD = sum(1 for c in PL_API_CONTRACTS if c["category"] == "command")
_PL_SET = sum(1 for c in PL_API_CONTRACTS if c["category"] == "set")
_PL_QRY = sum(1 for c in PL_API_CONTRACTS if c["category"] == "query")
assert _PL_CMD == 9 and _PL_SET == 2 and _PL_QRY == 1, \
    f"B01 frozen counts violated: cmd={_PL_CMD} set={_PL_SET} query={_PL_QRY}"
assert len(PL_API_CONTRACTS) == 12

# B01 frozen name order
_B01_ORDER = ["pl_generate_system_top", "pl_create_project", "pl_set_top",
    "pl_synthesize", "pl_place_and_route", "pl_analyze_timing",
    "pl_generate_bitstream", "pl_connect_hw_server", "pl_open_hw_target",
    "pl_select_device", "pl_program", "pl_get_device_status"]
assert [c["name"] for c in PL_API_CONTRACTS] == _B01_ORDER, \
    "B01 order mismatch"


# O3/O4 terminal gate: publish the corresponding Build Manifest before the
# Operation may enter SUCCEEDED.  Returning ``None`` is a deterministic
# MANIFEST_PUBLISH_FAILED result; callers must not treat it as best-effort.
def _publish_build_manifest(tool_name: str, snapshot, result, arguments):
    try:
        if tool_name == "pl_generate_bitstream":
            from mcps.zynq_mcp.domains.verification.build_manifest import (
                publish_pl_build_manifest as _publish_fn)
        elif tool_name == "ps_compile":
            from mcps.zynq_mcp.domains.verification.build_manifest import (
                publish_ps_build_manifest as _publish_fn)
        else:
            return None
    except Exception as e:
        logger.error("build manifest module import failed: %s", e)
        return None
    try:
        snap = {}
        if snapshot is not None:
            try:
                snap = dict(snapshot)  # snapshot is a MappingProxyType
            except Exception:
                snap = {}
        path = _publish_fn(
            snap,
            result if isinstance(result, dict) else {},
            str(snap.get("project_path", "") or ""),
            tool_args=arguments if isinstance(arguments, dict) else None,
        )
        if path:
            logger.info("published %s build manifest: %s", tool_name, path)
        return path
    except Exception as e:
        logger.error("build manifest publish failed for %s: %s", tool_name, e)
        return None


def derive_project_dir(project_path: str, name: str) -> str:
    """Production function. Returns '{project_path}/vivado/{name}'.
    Rejects: empty name, name containing '/' or '\\', name == '.' or '..', absolute name."""
    import os as _os
    if not isinstance(name, str) or not name or not name.strip():
        raise ValueError("name must be non-empty string")
    n = name.strip()
    if n in (".", ".."):
        raise ValueError(f"name must not be . or ..")
    if "/" in n or "\\" in n:
        raise ValueError("name must not be a path")
    if _os.path.isabs(n):
        raise ValueError("name must not be an absolute path")
    return _os.path.join(_os.path.normpath(project_path), "vivado", n)
