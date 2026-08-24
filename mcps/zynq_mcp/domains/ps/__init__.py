"""domains/ps — ARM / PS target operations.

B06 Library Phase (Agent C). The API modules — jtag_target,
target_control, memory_access, target_recovery — are pure stateless
function collections. Each function takes an XsdbBridge as dependency
injection and returns the ToolResponse envelope as a dict built with the
mcps/common/tool_response.py success()/error() constructors (never a
hand-written dict). Agent D's debug_session.py consumes these same
envelopes (it calls `resp.get("status")`), so every public function here
returns the `.to_dict()` form.

This package __init__ hosts the shared parsing / precondition / error
helpers used by the API modules so they stay thin and consistent.

Error model (fail-closed):
  - The top-level ToolResponse `code` is always one of the canonical
    ErrorCode values (mcps/common/error_codes.py) — tool_response.error()
    rejects unknown codes.
  - The PS-specific reasons from the Agent C spec (e.g. TARGET_NOT_FOUND,
    INVALID_SCOPE) are carried in `details.reason_code` via ps_error().
"""
from __future__ import annotations

import logging
import re

from mcps.common.error_codes import ErrorCode
from mcps.common.tool_response import error
from mcps.zynq_mcp.adapters.xsct.xsct_bridge import XsctBridgeError
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError

logger = logging.getLogger("zynq_mcp.ps")

__all__ = [
    "ps_error",
    "reason_of",
    "safe_eval",
    "extract_bridge_error",
    "is_generic_bridge_reason",
    "require_connected",
    "parse_targets",
    "selected_target_id",
    "require_target_selected",
    "parse_hex_token",
    "parse_state",
    "parse_target_properties",
]


# ── reason_code -> top-level ErrorCode mapping ────────────────────────────────
_REASON_TO_CODE = {
    # connectivity / env
    "HW_SERVER_UNREACHABLE": ErrorCode.ENV_ERROR,
    "BRIDGE_NOT_READY": ErrorCode.ENV_ERROR,
    "CONNECT_FAILED": ErrorCode.ENV_ERROR,
    "INVALID_URL": ErrorCode.INVALID_ARGUMENT,
    # hw_server local auto-start (B12-N3)
    "HW_SERVER_NOT_FOUND": ErrorCode.ENV_ERROR,
    "HW_SERVER_START_FAILED": ErrorCode.TOOL_ERROR,
    "HW_SERVER_START_TIMEOUT": ErrorCode.TOOL_ERROR,
    "NOT_CONNECTED": ErrorCode.JTAG_ERROR,
    "DISCONNECT_FAILED": ErrorCode.JTAG_ERROR,
    # enumeration / selection
    "JTAG_LIST_FAILED": ErrorCode.JTAG_ERROR,
    "JTAG_EMPTY_CHAIN": ErrorCode.JTAG_ERROR,
    "TARGET_NOT_FOUND": ErrorCode.JTAG_ERROR,
    "INVALID_TARGET_ID": ErrorCode.INVALID_ARGUMENT,
    "NO_TARGET_SELECTED": ErrorCode.JTAG_ERROR,
    "NO_ARM_DAP": ErrorCode.JTAG_ERROR,
    "TARGET_UNRESPONSIVE": ErrorCode.JTAG_ERROR,
    "DEVICE_INFO_FAILED": ErrorCode.JTAG_ERROR,
    # execution control
    "INVALID_SCOPE": ErrorCode.INVALID_ARGUMENT,
    "INVALID_STATE": ErrorCode.INVALID_ARGUMENT,
    "INVALID_TIMEOUT": ErrorCode.INVALID_ARGUMENT,
    "INVALID_CORE": ErrorCode.INVALID_ARGUMENT,
    "INVALID_STRATEGY": ErrorCode.INVALID_ARGUMENT,
    "RESET_FAILED": ErrorCode.JTAG_ERROR,
    "PS7_INIT_FAILED": ErrorCode.JTAG_ERROR,
    "DOWNLOAD_FAILED": ErrorCode.JTAG_ERROR,
    "RUN_FAILED": ErrorCode.JTAG_ERROR,
    "HALT_FAILED": ErrorCode.JTAG_ERROR,
    "STEP_FAILED": ErrorCode.JTAG_ERROR,
    "TARGET_NOT_HALTED": ErrorCode.JTAG_ERROR,
    "TIMEOUT": ErrorCode.JTAG_ERROR,
    # memory / registers
    "INVALID_REGISTER": ErrorCode.INVALID_ARGUMENT,
    "INVALID_VALUE": ErrorCode.INVALID_ARGUMENT,
    "INVALID_ADDRESS": ErrorCode.INVALID_ARGUMENT,
    "INVALID_LENGTH": ErrorCode.INVALID_ARGUMENT,
    "INVALID_DATA": ErrorCode.INVALID_ARGUMENT,
    "REG_READ_FAILED": ErrorCode.JTAG_ERROR,
    "REG_WRITE_FAILED": ErrorCode.JTAG_ERROR,
    "MEM_READ_FAILED": ErrorCode.JTAG_ERROR,
    "MEM_WRITE_FAILED": ErrorCode.JTAG_ERROR,
    # ELF / path
    "INVALID_ELF_PATH": ErrorCode.INVALID_ARGUMENT,
    "ELF_NOT_FOUND": ErrorCode.INVALID_ARGUMENT,
    "ELF_INVALID": ErrorCode.INVALID_ARGUMENT,
    "PATH_ESCAPE": ErrorCode.INVALID_ARGUMENT,
    # BSP / build (Agent C, ps_bsp.py — second batch)
    "INVALID_XSA_PATH": ErrorCode.INVALID_ARGUMENT,
    "XSA_NOT_FOUND": ErrorCode.INVALID_ARGUMENT,
    "INVALID_PROJECT_PATH": ErrorCode.INVALID_ARGUMENT,
    "INVALID_NAME": ErrorCode.INVALID_ARGUMENT,
    "INVALID_PLATFORM_NAME": ErrorCode.INVALID_ARGUMENT,
    "PLATFORM_NOT_FOUND": ErrorCode.INVALID_ARGUMENT,
    "INVALID_APP_NAME": ErrorCode.INVALID_ARGUMENT,
    "APP_NOT_FOUND": ErrorCode.INVALID_ARGUMENT,
    "INVALID_FILES": ErrorCode.INVALID_ARGUMENT,
    "FILE_NOT_FOUND": ErrorCode.INVALID_ARGUMENT,
    "INVALID_OPTIONS": ErrorCode.INVALID_ARGUMENT,
    "INVALID_OPTION": ErrorCode.INVALID_ARGUMENT,
    "WORKSPACE_UNKNOWN": ErrorCode.ENV_ERROR,
    "IMPORT_HW_FAILED": ErrorCode.PS_BUILD_ERROR,
    "PLATFORM_CREATE_FAILED": ErrorCode.PS_BUILD_ERROR,
    "BSP_CREATE_FAILED": ErrorCode.PS_BUILD_ERROR,
    "UPDATE_HW_FAILED": ErrorCode.PS_BUILD_ERROR,
    "APP_CREATE_FAILED": ErrorCode.PS_BUILD_ERROR,
    "APP_CONFIG_FAILED": ErrorCode.PS_BUILD_ERROR,
    "BUILD_FAILED": ErrorCode.PS_BUILD_ERROR,
    "BSP_STATUS_FAILED": ErrorCode.PS_BUILD_ERROR,
    "BUILD_STATUS_FAILED": ErrorCode.PS_BUILD_ERROR,
    "FLAG_UNSUPPORTED_IN_XSCT": ErrorCode.INVALID_ARGUMENT,
    "IMPORTSOURCES_FAILED": ErrorCode.PS_BUILD_ERROR,
    # recovery
    "RECOVERY_CASCADE_FAILED": ErrorCode.JTAG_ERROR,
    "RECOVERY_PARTIAL": ErrorCode.JTAG_ERROR,
    "RECONNECT_FAILED": ErrorCode.JTAG_ERROR,
    "ARM_ACCESS_FAILED": ErrorCode.JTAG_ERROR,
    # debug session (Agent D, debug_session.py)
    "INVALID_DEBUG_SESSION": ErrorCode.CONTEXT_INVALID,
    "INVALID_LOCATION": ErrorCode.INVALID_ARGUMENT,
    "INVALID_BP_ID": ErrorCode.INVALID_ARGUMENT,
    "INVALID_REGISTER_VALUE": ErrorCode.INVALID_ARGUMENT,
    "BREAKPOINT_ADD_FAILED": ErrorCode.JTAG_ERROR,
    "BREAKPOINT_NOT_FOUND": ErrorCode.JTAG_ERROR,
    "BREAKPOINT_REMOVE_FAILED": ErrorCode.JTAG_ERROR,
    "BACKTRACE_FAILED": ErrorCode.JTAG_ERROR,
    "DEBUG_CLOSE_INCOMPLETE": ErrorCode.JTAG_ERROR,
    # internal
    "SUBCALL_NO_RESULT": ErrorCode.INTERNAL_ERROR,
    # bridge-level generic (surfaced by adapters/xsct)
    "XSDM_EVAL_ERROR": ErrorCode.JTAG_ERROR,
    "XSDM_TCL_ERROR": ErrorCode.JTAG_ERROR,
    "XSDM_PROCESS_DEAD": ErrorCode.JTAG_ERROR,
    "XSDM_STDERR_OUTPUT": ErrorCode.JTAG_ERROR,
    "XSDM_WRITE_FAILED": ErrorCode.JTAG_ERROR,
    "BRIDGE_EVAL_NONE": ErrorCode.JTAG_ERROR,
    "XSDM_BRIDGE_UNAVAILABLE": ErrorCode.JTAG_ERROR,
}

_DEFAULT_REASON_CODE = ErrorCode.JTAG_ERROR


def ps_error(reason_code: str, message: str,
             details: dict | None = None) -> dict:
    """Build a ToolResponse error envelope (dict) with a stable ErrorCode.

    `reason_code` is preserved in `details.reason_code`; the top-level
    `code` always maps to a canonical ErrorCode value (fail-closed).
    """
    code = _REASON_TO_CODE.get(reason_code, _DEFAULT_REASON_CODE)
    extra = dict(details or {})
    extra["reason_code"] = reason_code
    return error(message=message, code=code.value, details=extra).to_dict()


def reason_of(resp) -> str:
    """Extract `details.reason_code` from a response envelope (dict or
    ToolResponse), or ''."""
    if isinstance(resp, dict):
        err = resp.get("error") or {}
        details = err.get("details") or {}
        return details.get("reason_code", "") or ""
    err = getattr(resp, "error", None)
    if err is not None:
        details = err.details or {}
        return details.get("reason_code", "") or ""
    return ""


# ── bridge eval result handling ───────────────────────────────────────────────
_BRIDGE_ERROR_MARKER_RE = re.compile(r"^__ERROR__:([^:]+):(.*)$", re.DOTALL)

# Bridge error reasons that indicate a generic failure, not a domain cause.
_GENERIC_BRIDGE_REASONS = {
    "XSDM_EVAL_ERROR", "XSDM_TCL_ERROR", "XSDM_PROCESS_DEAD",
    "XSDM_STDERR_OUTPUT", "XSDM_WRITE_FAILED", "BRIDGE_EVAL_NONE",
    "XSDM_BRIDGE_UNAVAILABLE",
}


def is_generic_bridge_reason(reason_code: str) -> bool:
    """True when reason_code is a bridge-level generic, not a domain cause."""
    return reason_code in _GENERIC_BRIDGE_REASONS


async def safe_eval(bridge, tcl: str, timeout_s: float = 30.0,
                    tolerate_stderr: bool = False) -> dict:
    """bridge.eval() that converts bridge errors into an error envelope.

    Handles both XsdbBridgeError (JTAG domain) and XsctBridgeError (BSP/
    Build domain, second batch). A dead or unresponsive bridge must
    surface as a structured error result (fail-closed), never as an
    unhandled crash inside a domain function.

    ``tolerate_stderr`` is forwarded to the bridge for XSCT BSP/Build
    commands whose compilers write routine noise to stderr. The kwarg is
    only forwarded when True so legacy fake bridges (used in the domain
    unit tests) that lack the parameter keep working.
    """
    try:
        if tolerate_stderr:
            return await bridge.eval(tcl, timeout_s=timeout_s,
                                     tolerate_stderr=True)
        return await bridge.eval(tcl, timeout_s=timeout_s)
    except (XsdbBridgeError, XsctBridgeError) as exc:
        logger.warning("BridgeError during %r: %s", tcl, exc)
        return {
            "status": "error",
            "error": {
                "code": "XSDM_EVAL_ERROR",
                "message": str(exc) or "bridge unavailable",
                "details": {"reason_code": "XSDM_BRIDGE_UNAVAILABLE"},
            },
        }


def extract_bridge_error(bridge_result, default_code=ErrorCode.JTAG_ERROR):
    """Extract an error from a bridge eval() result.

    Returns (top_code, reason_code, message) when the eval failed, else None.

    Two error channels are recognized:
      1. {"status": "error", ...} — the adapters/xsct failure envelope
         (code XSDM_EVAL_ERROR, details.reason_code XSDM_*).
      2. success data carrying the '__ERROR__:<reason>:<message>' marker
         (test-double convention for canned command-level errors).
    """
    if bridge_result is None:
        return (default_code, "BRIDGE_EVAL_NONE", "bridge returned no result")
    if bridge_result.get("status") == "error":
        err = bridge_result.get("error") or {}
        top_code = err.get("code") or default_code
        message = err.get("message") or "bridge eval error"
        details = err.get("details") or {}
        reason = details.get("reason_code") or "XSDM_EVAL_ERROR"
        return (top_code, reason, message)
    data = bridge_result.get("data")
    if isinstance(data, str):
        m = _BRIDGE_ERROR_MARKER_RE.match(data.strip())
        if m:
            return (default_code, m.group(1), m.group(2))
    return None


# ── connection precondition ───────────────────────────────────────────────────
def require_connected(bridge):
    """Return an error envelope when the bridge is not connected, else None."""
    if not getattr(bridge, "hw_connected", False):
        return ps_error("NOT_CONNECTED", "hw_server is not connected")
    return None


# ── xsdb `targets` output parsing ─────────────────────────────────────────────
_TARGET_LINE_RE = re.compile(
    r"^\s*(\*)?\s*(\d+)\s*(\*)?\s+(.+?)\s*(?:\(([^)]*)\))?\s*$")


def parse_targets(text: str) -> list[dict]:
    """Parse `xsdb targets` output into target dicts.

    Each entry: {"id": int, "name": str, "type": str, "selected": bool}.
    xsdb marks the current target with '*' before or after the id.
    """
    targets = []
    for line in (text or "").splitlines():
        m = _TARGET_LINE_RE.match(line)
        if not m:
            continue
        targets.append({
            "id": int(m.group(2)),
            "name": m.group(4).strip(),
            "type": (m.group(5) or "").strip(),
            "selected": bool(m.group(1) or m.group(3)),
        })
    return targets


async def selected_target_id(bridge):
    """Return (target_id, error_or_None) for the currently selected target.

    A target counts as selected when the `targets` listing marks it with
    '*'. Returns (None, None) when no target is selected.
    """
    from mcps.zynq_mcp.adapters.xsct import templates
    result = await safe_eval(bridge, templates.targets())
    err = extract_bridge_error(result)
    if err:
        return None, ps_error(err[1], err[2])
    for t in parse_targets(result.get("data", "")):
        if t["selected"]:
            return t["id"], None
    return None, None


async def require_target_selected(bridge):
    """Return (target_id, None) if a target is selected, else (None, error)."""
    tid, err = await selected_target_id(bridge)
    if err:
        return None, err
    if tid is None:
        return None, ps_error(
            "NO_TARGET_SELECTED", "no target is selected on the JTAG chain")
    return tid, None


# ── small output parsers ──────────────────────────────────────────────────────
_HEX_TOKEN_RE = re.compile(r"0[xX][0-9a-fA-F]+")
_HEX_DIGITS = "0123456789abcdefABCDEF"


def parse_hex_token(text: str):
    """Return the first hex value (0x-prefixed) found in text, else None.

    Real XSDB prints register values as bare hex (``pc: ffffff28``); the
    bare value is normalized to the 0x-prefixed form. The value is looked
    up only after a ``:`` so hex-digit characters in a register name
    (e.g. ``c`` in ``pc``) are never mistaken for the value.
    """
    t = text or ""
    m = _HEX_TOKEN_RE.search(t)
    if m:
        return m.group(0)
    after = t.partition(":")[2].strip()
    first = after.split(None, 1)[0] if after else ""
    if first and all(c in _HEX_DIGITS for c in first):
        return "0x" + first
    return None


def parse_state(text: str) -> str:
    """Map xsdb state text to 'running'|'halted'|'reset'|'unknown'."""
    t = (text or "").strip().lower()
    if any(k in t for k in ("halted", "stopped", "suspended",
                            "already stopped")):
        return "halted"
    if "running" in t:
        return "running"
    if "reset" in t:
        return "reset"
    return "unknown"


def parse_target_properties(text: str) -> tuple:
    """Extract (state, pc) from `targets -target-properties` output.

    Handles two formats:
      1. human-readable ``key: value`` lines ('State: Halted',
         'PC: 0x00100000', 'STATE = Running', ...);
      2. the raw Tcl list that real Vitis 2023.1 XSDB emits
         ('{target_ctx ... state_reason {Hardware Breakpoint} suspended 1
         is_current 1 ...} {target_ctx ...} ...') — the current target's
         state is derived with priority: explicit ``state`` field, then the
         ``suspended`` flag (the authoritative halt signal; ``state_reason``
         only says *why* the target stopped), then a bare ``state_reason``
         state word (e.g. ``Running``).

    Returns (None, None) for fields that are absent.
    """
    state = None
    pc = None
    for line in (text or "").splitlines():
        m = re.search(r"\bstate\b\s*[:=]\s*([A-Za-z0-9_]+)", line,
                      re.IGNORECASE)
        if m:
            state = m.group(1)
        m = re.search(r"\bpc\b\s*[:=]\s*(0[xX][0-9a-fA-F]+)", line,
                      re.IGNORECASE)
        if m:
            pc = m.group(1)
    if state is None and "{" in (text or ""):
        # Raw XSDB 2023.1 Tcl-list form: read the current target's state.
        state = _tcl_list_current_state(text)
    return state, pc


def _tcl_list_current_state(text: str) -> str | None:
    """Extract the current (``is_current 1``) target's state from a
    Tcl-list blob produced by ``targets -target-properties``.

    Priority (real Vitis 2023.1 XSDB):
      1. an explicit ``state`` field, when the output provides one;
      2. the ``suspended`` flag — ``suspended 1`` means halted. This is the
         authoritative signal: ``state_reason`` only says *why* the target
         stopped (e.g. ``{Hardware Breakpoint}``), not that it is running;
      3. a bare ``state_reason`` whose value is itself a state word
         (``Running`` / ``Suspended``), for outputs without ``suspended``;
      4. braced multi-word ``state_reason`` values (``{Hardware
         Breakpoint}``, ``{Software Breakpoint}``, ...) are *reasons*, not
         states — ignored.
    """
    for group in _split_tcl_groups(text or ""):
        if not re.search(r"\bis_current\s+1\b", group):
            continue
        explicit = _tcl_group_value(group, "state")
        if explicit:
            return explicit
        m = re.search(r"\bsuspended\s+(\d+)", group)
        if m:
            return "Suspended" if m.group(1) == "1" else "Running"
        reason = _tcl_group_value(group, "state_reason")
        if reason and reason.lower() in (
                "running", "halted", "stopped", "suspended", "resumed"):
            return reason
    return None


def _tcl_group_value(group: str, key: str) -> str | None:
    """Return the value of ``key`` inside a Tcl-list group, handling both
    bare tokens and braced multi-word values (e.g. ``state_reason {Hardware
    Breakpoint}`` -> ``"Hardware Breakpoint"``). None when absent."""
    m = re.search(r"\b" + re.escape(key) + r"\b\s+(\{([^{}]*)\}|\S+)", group)
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith("{"):
        return raw[1:-1].strip() if raw.endswith("}") else raw[1:].strip()
    return raw


def _split_tcl_groups(text: str) -> list[str]:
    """Split a Tcl list into its top-level ``{...}`` groups.

    Nested braces (e.g. ``name {ARM Cortex-A9 MPCore #0}``) stay inside
    their enclosing group. Text outside braces is ignored.
    """
    groups = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
            if depth > 1:
                buf.append(ch)
        elif ch == "}":
            depth -= 1
            if depth > 0:
                buf.append(ch)
            elif depth == 0 and buf:
                groups.append("".join(buf).strip())
                buf = []
        else:
            if depth > 0:
                buf.append(ch)
    return groups
