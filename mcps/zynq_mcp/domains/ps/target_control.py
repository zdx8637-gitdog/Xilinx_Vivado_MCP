"""target_control.py — JTAG download and execution control (8 APIs).

B06 Library Phase, Agent C. Stateless functions taking an XsdbBridge;
each returns a ToolResponse envelope dict (mcps/common/tool_response.py).

Tcl command strings come from adapters/xsct.templates (Agent A's shared
contract). Agent D's debug_session.py imports halt_target() and
download_elf() from this module.
"""
from __future__ import annotations

import asyncio
import os
import time

from mcps.common.tool_response import success
from mcps.zynq_mcp.adapters.xsct import templates
from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridge
from mcps.zynq_mcp.domains.ps import (
    extract_bridge_error,
    ps_error,
    reason_of,
    require_connected,
    require_target_selected,
    safe_eval,
)
from mcps.zynq_mcp.domains.ps.jtag_target import (
    get_target_status,
    list_targets,
    select_target,
)

__all__ = [
    "reset_target",
    "ensure_arm_accessible",
    "initialize_ps",
    "download_elf",
    "run_target",
    "halt_target",
    "step_target",
    "wait_for_state",
]

_VALID_SCOPES = ("processor", "system")
_VALID_STATES = ("halted", "running")
_POLL_INTERVAL_S = 0.5

# ELF magic: \\x7f 'E' 'L' 'F'
_ELF_MAGIC = b"\x7fELF"


async def reset_target(bridge: XsdbBridge, scope: str = "processor") -> dict:
    """Reset the target (xsdb rst).

    scope: 'processor' or 'system'.

    Errors: INVALID_SCOPE, NOT_CONNECTED, NO_TARGET_SELECTED, RESET_FAILED.
    """
    if scope not in _VALID_SCOPES:
        return ps_error("INVALID_SCOPE",
                        f"scope must be one of {_VALID_SCOPES}, got {scope!r}",
                        details={"scope": scope})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.rst(scope))
    err = extract_bridge_error(result)
    if err:
        return ps_error("RESET_FAILED",
                        f"{scope} reset failed: {err[2]}",
                        details={"scope": scope})
    return success(data={"scope": scope, "reset_done": True,
                         "target_id": tid}).to_dict()


async def ensure_arm_accessible(bridge: XsdbBridge) -> dict:
    """Ensure ARM cores are visible on the JTAG chain.

    After board power-cycle, the ARM DAP may be in a "power-up not
    acknowledged" state (DAP status 0x30000021), causing ARM Cortex-A9
    cores to not enumerate. This function:

    1. Lists targets. If ARM cores are already present, returns success.
    2. If ARM cores are missing, selects DAP (target 1) and executes
       `rst -system` to bring ARM cores out of reset.
    3. Re-lists targets and verifies ARM cores are now present.

    Returns:
      data.recovery_needed: bool — whether a system reset was required
      data.targets: current JTAG chain after recovery
      data.count: number of targets

    Errors: NOT_CONNECTED, NO_ARM_DAP, ARM_ACCESS_FAILED, and the
    underlying JTAG_LIST_FAILED / JTAG_EMPTY_CHAIN.
    """
    pre = require_connected(bridge)
    if pre:
        return pre
    listing = await list_targets(bridge)
    if listing["status"] != "success":
        return listing
    targets = listing["data"]["targets"]
    if _has_arm_cores(targets):
        return success(data={"recovery_needed": False,
                             "targets": targets,
                             "count": len(targets)}).to_dict()
    dap = _find_dap(targets)
    if dap is None:
        return ps_error("NO_ARM_DAP",
                        "ARM cores are missing and no ARM DAP target is "
                        "present to recover",
                        details={"targets": targets})
    s = await select_target(bridge, dap["id"])
    if s["status"] != "success":
        return ps_error("ARM_ACCESS_FAILED",
                        f"failed to select ARM DAP target {dap['id']}: "
                        f"{reason_of(s)}",
                        details={"step": "select_dap",
                                 "target_id": dap["id"]})
    r = await reset_target(bridge, scope="system")
    if r["status"] != "success":
        return ps_error("ARM_ACCESS_FAILED",
                        f"system reset failed while recovering ARM access: "
                        f"{reason_of(r)}",
                        details={"step": "system_reset"})
    after = await list_targets(bridge)
    if after["status"] != "success":
        return after
    after_targets = after["data"]["targets"]
    if not _has_arm_cores(after_targets):
        return ps_error("ARM_ACCESS_FAILED",
                        "system reset completed but ARM cores still do not "
                        "enumerate on the JTAG chain",
                        details={"step": "verify",
                                 "targets": after_targets})
    return success(data={"recovery_needed": True,
                         "targets": after_targets,
                         "count": len(after_targets)}).to_dict()


async def initialize_ps(bridge: XsdbBridge, tcl_path: str = "") -> dict:
    """Run the PS7 initialization sequence.

    Standard Zynq-7020 JTAG flow (verified by 7 legacy download scripts):
      1. source <tcl_path>   ← load ps7_init/ps7_post_config functions into XSDB
      2. ps7_init             ← init clocks, PLLs, MIO, DDR controller
      3. ps7_post_config      ← post-initialization

    After this sequence, DDR is accessible and ARM cores respond to JTAG.

    ``tcl_path`` must be the absolute path to ``ps7_init.tcl``, which is
    embedded in every XSA and extracted by XSCT to
    ``{workspace}/{platform}/hw/ps7_init.tcl``.

    Errors: NOT_CONNECTED, NO_TARGET_SELECTED, PS7_INIT_FAILED.
    """
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err

    if tcl_path:
        tcl_path = tcl_path.replace("\\", "/")
        src_result = await safe_eval(bridge, templates.source_tcl(tcl_path))
        src_err = extract_bridge_error(src_result)
        if src_err:
            return ps_error("PS7_INIT_FAILED",
                            f"failed to source ps7_init.tcl: {src_err[2]}",
                            details={"tcl_path": tcl_path})

    result = await safe_eval(bridge, templates.ps7_init())
    err = extract_bridge_error(result)
    if err:
        return ps_error("PS7_INIT_FAILED",
                        f"ps7_init failed: {err[2]}")
    result = await safe_eval(bridge, templates.ps7_post_config())
    err = extract_bridge_error(result)
    if err:
        return ps_error("PS7_INIT_FAILED",
                        f"ps7_post_config failed: {err[2]}")
    return success(data={"status": "initialized", "command": "ps7_init+ps7_post_config",
                         "target_id": tid, "tcl_path": tcl_path if tcl_path else None}).to_dict()


async def load_hardware(bridge: XsdbBridge, xsa_path: str) -> dict:
    """Register PL hardware design (AXI memory map) with the PS via XSDB ``loadhw``.

    Standard Zynq-7020 flow (verified by legacy download scripts):
      ps7_init → ps7_post_config → loadhw <xsa> → dow <elf> → con

    ``loadhw`` tells the ARM core which PL peripherals exist in the
    address space and how to route AXI transactions. Without it,
    Xil_Out32/In32 to PL addresses (e.g. GPIO at 0x41200000) would
    access unmapped memory and crash the CPU.

    Must be called AFTER ps_initialize_ps and BEFORE ps_download_elf.
    ``xsa_path`` must be the absolute path to the Platform XSA file.
    """
    if not isinstance(xsa_path, str) or not xsa_path.strip():
        return ps_error("INVALID_XSA_PATH",
                        f"xsa_path must be a non-empty string, got {xsa_path!r}")
    xsa_path = xsa_path.replace("\\", "/")
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge, templates.load_hardware(xsa_path))
    err2 = extract_bridge_error(result)
    if err2:
        return ps_error("LOAD_HW_FAILED",
                        f"loadhw failed: {err2[2]}",
                        details={"xsa_path": xsa_path})
    return success(data={"status": "hardware_loaded", "xsa_path": xsa_path,
                         "target_id": tid}).to_dict()


async def download_elf(bridge: XsdbBridge, elf_path: str) -> dict:
    """Download an ELF to DDR over JTAG (xsdb dow).

    Validates the path (no '..' traversal), existence, and ELF magic
    before sending anything to the bridge.

    Errors: INVALID_ELF_PATH, PATH_ESCAPE, ELF_NOT_FOUND, ELF_INVALID,
    NOT_CONNECTED, NO_TARGET_SELECTED, DOWNLOAD_FAILED.
    """
    if not isinstance(elf_path, str) or not elf_path.strip():
        return ps_error("INVALID_ELF_PATH",
                        f"elf_path must be a non-empty string, got {elf_path!r}")
    parts = elf_path.replace("\\", "/").split("/")
    if ".." in parts:
        return ps_error("PATH_ESCAPE",
                        f"elf_path must not contain '..' traversal: {elf_path!r}",
                        details={"elf_path": elf_path})
    if not os.path.isfile(elf_path):
        return ps_error("ELF_NOT_FOUND",
                        f"ELF file does not exist: {elf_path}",
                        details={"elf_path": elf_path})
    try:
        with open(elf_path, "rb") as f:
            magic = f.read(4)
    except OSError as e:
        return ps_error("ELF_INVALID",
                        f"ELF file is not readable: {e}",
                        details={"elf_path": elf_path})
    if magic != _ELF_MAGIC:
        return ps_error("ELF_INVALID",
                        f"file is not a valid ELF: {elf_path}",
                        details={"elf_path": elf_path})
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    tcl_path = elf_path.replace("\\", "/")
    result = await safe_eval(bridge,templates.dow(tcl_path))
    err = extract_bridge_error(result)
    if err:
        return ps_error("DOWNLOAD_FAILED",
                        f"ELF download failed: {err[2]}",
                        details={"elf_path": elf_path})
    return success(data={"elf_path": elf_path, "downloaded": True,
                         "target_id": tid}).to_dict()


async def run_target(bridge: XsdbBridge, core: int | None = None) -> dict:
    """Start processor execution (xsdb con).

    `core` is advisory: xsdb resumes the currently selected target. The
    postcondition (state == running) is confirmed via get_target_status.

    Errors: INVALID_CORE, NOT_CONNECTED, NO_TARGET_SELECTED, RUN_FAILED.
    """
    core, cerr = _validate_core(core)
    if cerr:
        return cerr
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.con())
    err = extract_bridge_error(result)
    if err:
        return ps_error("RUN_FAILED", f"run (con) failed: {err[2]}",
                        details={"core": core})
    status = await get_target_status(bridge)
    if status["status"] != "success" or status["data"].get("state") != "running":
        state = status["data"].get("state") if status["status"] == "success" \
            else None
        return ps_error("RUN_FAILED",
                        f"target did not confirm running state after con "
                        f"(state={state})",
                        details={"core": core, "state": state})
    return success(data={"state": "running", "core": core,
                         "target_id": tid}).to_dict()


async def halt_target(bridge: XsdbBridge, core: int | None = None) -> dict:
    """Halt the processor (xsdb stop). Idempotent.

    already_halted=True when xsdb reports the target was already stopped.

    Errors: INVALID_CORE, NOT_CONNECTED, NO_TARGET_SELECTED, HALT_FAILED.
    """
    core, cerr = _validate_core(core)
    if cerr:
        return cerr
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge,templates.stop())
    err = extract_bridge_error(result)
    out = (result.get("data") or "").lower()
    already = ("already stopped" in out) or ("already halted" in out)
    # XSDB with -interactive sends "Already stopped" to stderr, not stdout.
    # Accept it as idempotent success.
    if err and ("already stopped" in str(err[2]).lower() or
                "already halted" in str(err[2]).lower()):
        err = None
        already = True
    if err:
        return ps_error("HALT_FAILED", f"halt (stop) failed: {err[2]}",
                        details={"core": core})
    # Verify via target status query
    status = await get_target_status(bridge)
    if status["status"] == "success" and status["data"].get("state") == "halted":
        return success(data={"state": "halted", "already_halted": already,
                             "core": core, "target_id": tid}).to_dict()
    # Status query fallback: if the stop command itself confirmed halted
    if "stopped" in out or "suspended" in out:
        return success(data={"state": "halted", "already_halted": already,
                             "core": core, "target_id": tid}).to_dict()
    return ps_error("HALT_FAILED",
                    "target did not confirm halted state after stop",
                    details={"core": core})


async def step_target(bridge: XsdbBridge, core: int | None = None) -> dict:
    """Single-step execution (xsdb stp).

    The target must already be halted.

    Errors: INVALID_CORE, NOT_CONNECTED, NO_TARGET_SELECTED,
    TARGET_NOT_HALTED, STEP_FAILED.
    """
    core, cerr = _validate_core(core)
    if cerr:
        return cerr
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    status = await get_target_status(bridge)
    if status["status"] == "error":
        return status
    if status["data"].get("state") != "halted":
        return ps_error("TARGET_NOT_HALTED",
                        "target must be halted before single-step",
                        details={"state": status["data"].get("state"),
                                 "core": core})
    result = await safe_eval(bridge,templates.stp())
    err = extract_bridge_error(result)
    if err:
        return ps_error("STEP_FAILED", f"single-step (stp) failed: {err[2]}",
                        details={"core": core})
    return success(data={"state": "halted", "stepped": True,
                         "core": core, "target_id": tid}).to_dict()


async def wait_for_state(
    bridge: XsdbBridge,
    state: str,
    timeout_s: float = 30.0,
) -> dict:
    """Wait until the target reaches `state` ('halted' | 'running').

    Polls get_target_status every 0.5s. On timeout returns an error
    (never raises).

    Errors: INVALID_STATE, INVALID_TIMEOUT, and the underlying
    NOT_CONNECTED / NO_TARGET_SELECTED / TARGET_UNRESPONSIVE are
    propagated immediately (a broken precondition won't be polled).
    """
    if state not in _VALID_STATES:
        return ps_error("INVALID_STATE",
                        f"state must be one of {_VALID_STATES}, got {state!r}",
                        details={"state": state})
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
        return ps_error("INVALID_TIMEOUT",
                        f"timeout_s must be a number, got {timeout_s!r}")
    if timeout_s <= 0:
        return ps_error("INVALID_TIMEOUT",
                        f"timeout_s must be positive, got {timeout_s}")
    start = time.monotonic()
    deadline = start + float(timeout_s)
    last_state = None
    while True:
        status = await get_target_status(bridge)
        if status["status"] == "success":
            last_state = status["data"].get("state")
            if last_state == state:
                return success(data={
                    "state": state,
                    "achieved": True,
                    "elapsed_s": round(time.monotonic() - start, 3),
                }).to_dict()
        else:
            reason = reason_of(status)
            if reason in ("NOT_CONNECTED", "NO_TARGET_SELECTED",
                          "BRIDGE_NOT_READY", "TARGET_UNRESPONSIVE"):
                return status
        if time.monotonic() >= deadline:
            return ps_error(
                "TIMEOUT",
                f"target did not reach state {state!r} within {timeout_s}s",
                details={"state": state, "timeout_s": timeout_s,
                         "last_state": last_state})
        await asyncio.sleep(_POLL_INTERVAL_S)


def _has_arm_cores(targets: list[dict]) -> bool:
    """True when the JTAG chain lists ARM Cortex-A9 core targets.

    XSDB names the cores "ARM Cortex-A9 #N" (type "ARM"). The ARM DAP is a
    separate target (type "DAP" / "ARM DAP") and does NOT count as a core —
    after a board power-cycle the DAP can enumerate while the cores do not.
    """
    for t in targets:
        ty = (t.get("type") or "").lower()
        name = (t.get("name") or "").lower()
        if "cortex" in ty or "cortex" in name:
            return True
        # Type "(ARM)" marks a core; exclude the DAP which also carries the
        # "arm" word in its type ("ARM DAP").
        if "arm" in ty and "dap" not in ty:
            return True
    return False


def _find_dap(targets: list[dict]) -> dict | None:
    """Find the ARM DAP target in a list_targets listing."""
    for t in targets:
        ty = (t.get("type") or "").lower()
        name = (t.get("name") or "").lower()
        if "dap" in ty or "dap" in name:
            return t
    return None


def _validate_core(core):
    """Return (core, None) for a valid core, or (None, error envelope)."""
    if core is None:
        return None, None
    if isinstance(core, bool) or not isinstance(core, int):
        return None, ps_error("INVALID_CORE",
                              "core must be a non-negative integer or None, "
                              f"got {core!r}")
    if core < 0:
        return None, ps_error("INVALID_CORE",
                              f"core must be >= 0, got {core}")
    return core, None
