"""debug_session.py — ARM JTAG debug session management.

A debug session wraps an XsdbBridge connection in a debug context:
- ELF is loaded (symbols available)
- Breakpoints can be set/removed
- Registers can be read/written
- Stack trace can be captured

The debug_session_id is an opaque token returned by debug_start().
It must be passed to all other debug_* functions.

Implementation note: xsdb does not have a native "debug session" concept.
We emulate it by tracking state in a module-level dict (_debug_sessions):
- debug_start: select target (optional), halt target, download ELF,
  register the session as halted.
- read_register / write_register / stack_trace require the session to be
  in the halted state (precondition check against _debug_sessions).
- debug_close: clear breakpoints, re-halt if needed, drop the session
  (the JTAG connection stays up — other operations reuse it).

Integration phase: _debug_sessions will be migrated into the CommandRunner
worker context / Ledger for persistence. Library phase keeps it module-local
(thread-safety is guaranteed by the CommandRunner mutex above this layer).

Module reuse (Agent C, per B06_agent_D_arm_debug.md §4.3 — not reimplemented):
    from mcps.zynq_mcp.domains.ps.jtag_target import select_target
    from mcps.zynq_mcp.domains.ps.target_control import halt_target, download_elf

Error model: the top-level error.code is always a stable ErrorCode from
mcps/common/error_codes.py, derived centrally by Agent C's ps_error()
(domains/ps/__init__.py). The fine-grained reason from
B06_agent_D_arm_debug.md (INVALID_DEBUG_SESSION, BREAKPOINT_ADD_FAILED, ...)
is carried in error.details.reason_code; the top-level code follows Agent C's
canonical _REASON_TO_CODE mapping.

Evidence level (B06 library phase):
- All 7 public functions IMPLEMENTED_AND_TESTED at the unit level with a
  FakeXsdbBridge + mocked Agent C dependencies. Dependency status at time of
  development: Agent A's XsdbBridge was complete; Agent C's
  jtag_target/target_control were not yet shipped, so the tests replace
  them at the debug_session module namespace.
- host_live coverage DEFERRED until Agent C (jtag_target/target_control)
  ships its real implementations.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from mcps.common.tool_response import success
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.zynq_mcp.domains.ps import ps_error, safe_eval
from mcps.zynq_mcp.domains.ps.jtag_target import select_target
from mcps.zynq_mcp.domains.ps.memory_access import _extract_reg_value
from mcps.zynq_mcp.domains.ps.target_control import halt_target, download_elf

logger = logging.getLogger("zynq_mcp.ps.debug_session")

__all__ = [
    "debug_start",
    "breakpoint_add",
    "breakpoint_remove",
    "read_register",
    "write_register",
    "stack_trace",
    "debug_close",
]

# ── fine-grained reason codes (carried in error.details.reason_code) ──────────
REASON_INVALID_DEBUG_SESSION = "INVALID_DEBUG_SESSION"
REASON_INVALID_LOCATION = "INVALID_LOCATION"
REASON_INVALID_REGISTER = "INVALID_REGISTER"
REASON_INVALID_REGISTER_VALUE = "INVALID_REGISTER_VALUE"
REASON_INVALID_ELF_PATH = "INVALID_ELF_PATH"
REASON_INVALID_TARGET_ID = "INVALID_TARGET_ID"
REASON_INVALID_BP_ID = "INVALID_BP_ID"
REASON_TARGET_NOT_HALTED = "TARGET_NOT_HALTED"
REASON_BREAKPOINT_ADD_FAILED = "BREAKPOINT_ADD_FAILED"
REASON_BREAKPOINT_NOT_FOUND = "BREAKPOINT_NOT_FOUND"
REASON_BREAKPOINT_REMOVE_FAILED = "BREAKPOINT_REMOVE_FAILED"
REASON_REG_READ_FAILED = "REG_READ_FAILED"
REASON_REG_WRITE_FAILED = "REG_WRITE_FAILED"
REASON_BACKTRACE_FAILED = "BACKTRACE_FAILED"
REASON_DEBUG_CLOSE_INCOMPLETE = "DEBUG_CLOSE_INCOMPLETE"
REASON_SUBCALL_NO_RESULT = "SUBCALL_NO_RESULT"

# ARM Cortex-A9 (ARMv7) registers: r0-r15 plus the standard aliases.
_VALID_REGISTERS = {f"r{i}" for i in range(16)} | {"sp", "lr", "pc", "cpsr"}
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BP_ID_RE = re.compile(r"\b(\d+)\b")

# ── debug session tracking (module-local; see module docstring) ────────────────
_debug_sessions: dict[str, dict] = {}


def _create_session(elf_path: str) -> str:
    sid = f"debug-{uuid.uuid4().hex[:8]}"
    _debug_sessions[sid] = {
        "session_id": sid,
        "elf_path": elf_path,
        "breakpoints": set(),
        "created_at": None,
        # A freshly created session is not known to be halted. debug_start
        # flips this to True only after halt_target() reports success.
        "halted": False,
    }
    return sid


def _get_session(session_id: str) -> dict:
    if session_id not in _debug_sessions:
        raise ValueError("INVALID_DEBUG_SESSION")
    return _debug_sessions[session_id]


def _remove_session(session_id: str) -> None:
    _debug_sessions.pop(session_id, None)


# ── internal helpers ───────────────────────────────────────────────────────────

def _invalid_session_response() -> dict:
    return ps_error(REASON_INVALID_DEBUG_SESSION,
                    "Invalid or unknown debug_session_id")


def _subcall_error(result) -> dict | None:
    """Return an error dict when a dependency sub-call failed, else None.

    Agent C domain functions return ToolResponse dicts (via .to_dict()) on
    success but may return a ToolResponse *object* (ps_error) on error, so
    this normalizes both forms. Fail-closed: a missing result is an error.
    """
    if result is None:
        return ps_error(REASON_SUBCALL_NO_RESULT,
                        "dependency returned no result")
    if isinstance(result, dict):
        if result.get("status") == "error":
            return result
        return None
    status = getattr(result, "status", None)
    if status == "error":
        return result.to_dict()
    return None


def _resolve_session(debug_session_id: str) -> tuple[dict | None, dict | None]:
    """Return (session, None) on success, or (None, error_response)."""
    if not isinstance(debug_session_id, str) or not debug_session_id:
        return None, _invalid_session_response()
    try:
        return _get_session(debug_session_id), None
    except ValueError:
        return None, _invalid_session_response()


def _require_halted(session: dict) -> dict | None:
    """Return an error response when the target is not known to be halted."""
    if not session.get("halted", False):
        return ps_error(
            REASON_TARGET_NOT_HALTED,
            "Target must be halted before this debug operation "
            f"(session {session['session_id']})",
            {"debug_session_id": session["session_id"]},
        )
    return None


def _validate_register(register: str) -> str | None:
    if not isinstance(register, str):
        return None
    norm = register.lower()
    return norm if norm in _VALID_REGISTERS else None


def _bridge_failure_response(result: dict, reason_code: str,
                             fallback_msg: str) -> dict:
    """Build a ToolResponse error from a failed bridge.eval() result."""
    err = result.get("error") or {}
    details = {}
    bridge_msg = err.get("message")
    if bridge_msg:
        details["bridge_message"] = bridge_msg
    bridge_code = err.get("code")
    if bridge_code:
        details["bridge_code"] = bridge_code
    return ps_error(reason_code, fallback_msg, details)


def _parse_reg_value(data: str, register: str) -> str | None:
    """Extract the hex value for `register` from an rrd output blob.

    Delegates to memory_access._extract_reg_value, which accepts both the
    0x-prefixed form and the bare hex that real XSDB prints
    (``pc: ffffff28``), returning the value 0x-prefixed.
    """
    return _extract_reg_value(data, register)


def _normalize_reg_value(value: int | str) -> str | None:
    """Normalize a register value to a hex string, or None if invalid."""
    if isinstance(value, bool):  # bool is a subclass of int — reject
        return None
    if isinstance(value, int):
        return f"0x{value & 0xFFFFFFFF:08X}"
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return None
        if v[:2].lower() == "0x":
            try:
                int(v, 16)
            except ValueError:
                return None
            return v
        if v.isdigit():
            return v
    return None


def _parse_backtrace(data: str) -> list[dict]:
    frames = []
    for line in data.splitlines():
        frame = _parse_stack_frame(line)
        if frame is not None:
            frames.append(frame)
    return frames


def _parse_stack_frame(line: str) -> dict | None:
    m = re.match(r"^\s*#(\d+)\s+(.*)$", line)
    if not m:
        return None
    level = int(m.group(1))
    rest = m.group(2).strip()
    if not rest:
        return None

    pc = None
    mpc = re.search(r"0x[0-9a-fA-F]+", rest)
    if mpc:
        pc = mpc.group(0)

    file = None
    before = rest
    if " at " in rest:
        before, _, file = rest.partition(" at ")
        before = before.strip()
        file = file.strip() or None

    func = None
    if " in " in before:
        _, _, after = before.partition(" in ")
        func = _func_token(after)
    elif "(" in before:
        func = _func_token(before)

    return {"level": level, "pc": pc, "function": func, "file": file}


def _func_token(text: str) -> str | None:
    """Extract the function-name token (text before '('), if sensible."""
    text = text.strip()
    name = text.split("(", 1)[0].strip()
    if not name:
        return None
    if name.lower().startswith("0x"):  # an address, not a function name
        return None
    return name


# ── public debug-session API ───────────────────────────────────────────────────

async def debug_start(
    bridge: XsdbBridge,
    elf_path: str,
    target_id: int | None = None,
) -> dict:
    """开始调试会话。

    1. 如果 target_id 指定，select_target(target_id)
    2. halt_target (确保目标暂停)
    3. download_elf(elf_path)  (加载符号)
    4. 生成 debug_session_id (UUID)
    5. 返回 session_id

    内部复用 Agent C 的函数（jtag_target.select_target,
    target_control.halt_target, target_control.download_elf），
    不重新实现相同的 Tcl 逻辑。

    返回 data.debug_session_id: str

    错误:
    - INVALID_ARGUMENT/INVALID_ELF_PATH, INVALID_TARGET_ID
    - 以及所有子调用的错误（ELF_NOT_FOUND, DOWNLOAD_FAILED 等）原样传播
    """
    if not isinstance(elf_path, str) or not elf_path.strip():
        return ps_error(REASON_INVALID_ELF_PATH,
                        "elf_path must be a non-empty string")
    if target_id is not None and not isinstance(target_id, int):
        return ps_error(REASON_INVALID_TARGET_ID,
                        "target_id must be an integer")

    if target_id is not None:
        sel = await select_target(bridge, target_id)
        sel_err = _subcall_error(sel)
        if sel_err is not None:
            return sel_err

    halt_res = await halt_target(bridge)
    halt_err = _subcall_error(halt_res)
    if halt_err is not None:
        return halt_err

    dl_res = await download_elf(bridge, elf_path)
    dl_err = _subcall_error(dl_res)
    if dl_err is not None:
        return dl_err

    sid = _create_session(elf_path)
    session = _debug_sessions[sid]
    session["halted"] = True
    session["created_at"] = datetime.now(timezone.utc).isoformat()

    return success(data={
        "debug_session_id": sid,
        "elf_path": elf_path,
        "target_halted": True,
    }).to_dict()


async def breakpoint_add(
    bridge: XsdbBridge,
    debug_session_id: str,
    location: str,  # 地址 "0x00100000" 或符号 "main"
) -> dict:
    """设置断点。

    返回 data.breakpoint_id: int  (xsdb bpadd 返回的 bp id)

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION: session_id 无效
    - JTAG_ERROR/BREAKPOINT_ADD_FAILED: bpadd 失败
    - INVALID_ARGUMENT/INVALID_LOCATION: 地址格式无效
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    if not isinstance(location, str) or not location:
        return ps_error(REASON_INVALID_LOCATION,
                        "location must be a non-empty string")

    if _ADDRESS_RE.match(location):
        tcl = f"bpadd -addr {location}"
    elif _SYMBOL_RE.match(location):
        tcl = f"bpadd -sym {location}"
    else:
        return ps_error(
            REASON_INVALID_LOCATION,
            f"Invalid breakpoint location: {location!r} "
            "(expected 0x-address or symbol)",
            {"location": location},
        )

    result = await safe_eval(bridge, tcl)
    if result.get("status") == "error":
        return _bridge_failure_response(
            result, REASON_BREAKPOINT_ADD_FAILED,
            f"Failed to set breakpoint at {location!r}")

    m = _BP_ID_RE.search(str(result.get("data") or ""))
    if m is None:
        # Fail-closed: we cannot attribute a breakpoint id, so we must not
        # claim success.
        logger.warning("bpadd output contained no breakpoint id: %r",
                       result.get("data"))
        return ps_error(
            REASON_BREAKPOINT_ADD_FAILED,
            f"Could not determine breakpoint id from xsdb output "
            f"for {location!r}",
            {"location": location, "output": result.get("data")},
        )

    bp_id = int(m.group(1))
    session["breakpoints"].add(bp_id)
    return success(data={
        "breakpoint_id": bp_id,
        "location": location,
        "debug_session_id": debug_session_id,
    }).to_dict()


async def breakpoint_remove(
    bridge: XsdbBridge,
    debug_session_id: str,
    bp_id: int,
) -> dict:
    """移除断点。

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION
    - JTAG_ERROR/BREAKPOINT_NOT_FOUND: bp_id 不存在
    - INVALID_ARGUMENT/INVALID_BP_ID
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    if not isinstance(bp_id, int):
        return ps_error(REASON_INVALID_BP_ID, "bp_id must be an integer")

    if bp_id not in session["breakpoints"]:
        return ps_error(
            REASON_BREAKPOINT_NOT_FOUND,
            f"Breakpoint {bp_id} does not exist in session "
            f"{debug_session_id}",
            {"breakpoint_id": bp_id, "debug_session_id": debug_session_id},
        )

    result = await safe_eval(bridge, f"bpremove {bp_id}")
    if result.get("status") == "error":
        return _bridge_failure_response(
            result, REASON_BREAKPOINT_REMOVE_FAILED,
            f"Failed to remove breakpoint {bp_id}")

    session["breakpoints"].discard(bp_id)
    return success(data={
        "breakpoint_id": bp_id,
        "removed": True,
        "debug_session_id": debug_session_id,
    }).to_dict()


async def read_register(
    bridge: XsdbBridge,
    debug_session_id: str,
    register: str,
) -> dict:
    """读取 CPU 寄存器（带调试上下文验证）。

    返回 data: {register: str, value: str, debug_session_id: str}

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION
    - INVALID_ARGUMENT/INVALID_REGISTER
    - JTAG_ERROR/TARGET_NOT_HALTED: 读寄存器前目标必须 halted
    - JTAG_ERROR/REG_READ_FAILED: xsdb rrd 失败或输出无法解析
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    halted_err = _require_halted(session)
    if halted_err is not None:
        return halted_err

    norm_reg = _validate_register(register)
    if norm_reg is None:
        return ps_error(
            REASON_INVALID_REGISTER, f"Invalid register: {register!r}",
            {"register": register},
        )

    result = await safe_eval(bridge, f"rrd {norm_reg}")
    if result.get("status") == "error":
        return _bridge_failure_response(
            result, REASON_REG_READ_FAILED,
            f"Failed to read register {norm_reg}")

    value = _parse_reg_value(str(result.get("data") or ""), norm_reg)
    if value is None:
        return ps_error(
            REASON_REG_READ_FAILED,
            f"Could not parse value for register {norm_reg} "
            "from xsdb output",
            {"register": norm_reg, "output": result.get("data")},
        )

    return success(data={
        "register": norm_reg,
        "value": value,
        "debug_session_id": debug_session_id,
    }).to_dict()


async def write_register(
    bridge: XsdbBridge,
    debug_session_id: str,
    register: str,
    value: int | str,
) -> dict:
    """写 CPU 寄存器。

    与 read_register 相同的前提条件（session 有效 + 目标已 halted）。

    返回 data: {register: str, value: str, debug_session_id: str}

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION
    - INVALID_ARGUMENT/INVALID_REGISTER, INVALID_REGISTER_VALUE
    - JTAG_ERROR/TARGET_NOT_HALTED
    - JTAG_ERROR/REG_WRITE_FAILED: xsdb rwr 失败
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    halted_err = _require_halted(session)
    if halted_err is not None:
        return halted_err

    norm_reg = _validate_register(register)
    if norm_reg is None:
        return ps_error(
            REASON_INVALID_REGISTER, f"Invalid register: {register!r}",
            {"register": register},
        )

    value_hex = _normalize_reg_value(value)
    if value_hex is None:
        return ps_error(
            REASON_INVALID_REGISTER_VALUE,
            f"Invalid register value: {value!r}",
            {"register": norm_reg},
        )

    result = await safe_eval(bridge, f"rwr {norm_reg} {value_hex}")
    if result.get("status") == "error":
        return _bridge_failure_response(
            result, REASON_REG_WRITE_FAILED,
            f"Failed to write register {norm_reg}")

    return success(data={
        "register": norm_reg,
        "value": value_hex,
        "debug_session_id": debug_session_id,
    }).to_dict()


async def stack_trace(
    bridge: XsdbBridge,
    debug_session_id: str,
) -> dict:
    """获取调用栈。

    内部: xsdb bt → 解析输出。

    返回 data.frames: [
        {"level": 0, "pc": "0x...", "function": "main", "file": "main.c:42"},
        {"level": 1, "pc": "0x...", "function": "_start", "file": None},
    ]

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION
    - JTAG_ERROR/TARGET_NOT_HALTED
    - JTAG_ERROR/BACKTRACE_FAILED
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    halted_err = _require_halted(session)
    if halted_err is not None:
        return halted_err

    result = await safe_eval(bridge, "bt")
    if result.get("status") == "error":
        return _bridge_failure_response(
            result, REASON_BACKTRACE_FAILED,
            "Failed to capture backtrace")

    frames = _parse_backtrace(str(result.get("data") or ""))
    if not frames:
        # Fail-closed: zero parseable frames is not proof of an empty stack.
        return ps_error(
            REASON_BACKTRACE_FAILED,
            "Backtrace returned no parseable frames",
            {"output": result.get("data")},
        )

    return success(data={
        "frames": frames,
        "debug_session_id": debug_session_id,
    }).to_dict()


async def debug_close(
    bridge: XsdbBridge,
    debug_session_id: str,
) -> dict:
    """关闭调试会话。

    1. 清除所有断点 (bpremove all)
    2. halt_target (如果 running —— halt_target 幂等，直接调用即可)
    3. 不 disconnect（JTAG 连接保持，供其他操作使用）

    错误:
    - CONTEXT_INVALID/INVALID_DEBUG_SESSION
    - JTAG_ERROR/DEBUG_CLOSE_INCOMPLETE: 清理步骤失败（session 仍被释放）
    """
    session, err = _resolve_session(debug_session_id)
    if err is not None:
        return err

    failures: list[str] = []

    # 1. Clear all breakpoints. Best-effort: a dead bridge must not prevent
    #    the Python-side session from being released.
    bp_result = await safe_eval(bridge, "bpremove all")
    if bp_result.get("status") == "error":
        failures.append("bpremove_all")

    # 2. Re-halt the target. halt_target is idempotent (already_halted=True
    #    when already halted), which implements the "halt if running" rule.
    halt_result = await halt_target(bridge)
    if _subcall_error(halt_result) is not None:
        failures.append("halt_target")

    # 3. Drop the Python-side session; the JTAG connection stays up.
    tracked_breakpoints = len(session["breakpoints"])
    _remove_session(debug_session_id)

    if failures:
        return ps_error(
            REASON_DEBUG_CLOSE_INCOMPLETE,
            f"debug_close incomplete: {', '.join(failures)}",
            {"failed_steps": failures, "debug_session_id": debug_session_id},
        )

    return success(data={
        "debug_session_id": debug_session_id,
        "breakpoints_removed": tracked_breakpoints,
        "halted": True,
    }).to_dict()
