"""target_recovery.py — target recovery and diagnostics (4 APIs).

B06 Library Phase, Agent C. Stateless functions taking an XsdbBridge;
each returns a ToolResponse envelope dict (mcps/common/tool_response.py).

recover_target("auto") cascade (architecture §"目标恢复", task note):
    halt -> processor_reset -> core_reset -> system_reset -> ps7_init
    -> verify halted
Each step is best-effort but the cascade STOPS at the first failing step
(no silent skips): failed_at_step reports where it stopped.
"""
from __future__ import annotations

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
    connect_hw_server,
    disconnect_hw_server,
    get_target_status,
    list_targets,
    select_target,
)
from mcps.zynq_mcp.domains.ps.target_control import (
    halt_target,
    initialize_ps,
    reset_target,
)

__all__ = [
    "recover_target",
    "reconnect_target",
    "clear_debug_session",
    "diagnose_dap",
]

# xsdb command to clear all breakpoints (not covered by templates).
_BPD_ALL = "bpd -all"

# recovery cascade, in order. Each entry: (step_name, async callable).
_CASCADE_STEPS = [
    ("halt", halt_target),
    ("processor_reset", lambda b: reset_target(b, scope="processor")),
    ("core_reset", lambda b: _core_reset(b)),
    ("system_reset", lambda b: reset_target(b, scope="system")),
    ("ps7_init", initialize_ps),
]


async def recover_target(
    bridge: XsdbBridge,
    strategy: str = "auto",
) -> dict:
    """Automatically recover the target connection.

    strategy='auto' runs the halt -> processor_reset -> core_reset ->
    system_reset -> ps7_init cascade and verifies the target halts.

    The cascade stops at the first failing step. completed_steps lists the
    steps that succeeded; failed_at_step is the 1-based index of the step
    that stopped the cascade (None when the cascade finished).

    Errors: INVALID_STRATEGY, RECOVERY_CASCADE_FAILED (first step failed),
    RECOVERY_PARTIAL (partial completion or final verify failed).
    """
    if strategy != "auto":
        return ps_error("INVALID_STRATEGY",
                        f"strategy must be 'auto', got {strategy!r} "
                        "(custom cascades are not yet supported)")
    completed_steps: list[str] = []
    for idx, (name, step_fn) in enumerate(_CASCADE_STEPS, start=1):
        resp = await step_fn(bridge)
        if resp["status"] != "success":
            base = {"failed_at_step": idx,
                    "completed_steps": list(completed_steps),
                    "step": name,
                    "step_error": reason_of(resp)}
            if idx == 1:
                return ps_error(
                    "RECOVERY_CASCADE_FAILED",
                    f"recovery cascade failed at step {idx} ({name})",
                    details=base)
            return ps_error(
                "RECOVERY_PARTIAL",
                f"recovery cascade stopped at step {idx} ({name})",
                details=base)
        completed_steps.append(name)

    status = await get_target_status(bridge)
    if status["status"] != "success" or status["data"].get("state") != "halted":
        state = status["data"].get("state") if status["status"] == "success" \
            else None
        return ps_error(
            "RECOVERY_PARTIAL",
            "recovery cascade completed but the target did not verify as "
            "halted",
            details={"failed_at_step": None,
                     "completed_steps": completed_steps,
                     "state": state})
    return success(data={"recovered": True,
                         "strategy": strategy,
                         "state": "halted",
                         "completed_steps": completed_steps,
                         "failed_at_step": None}).to_dict()


async def reconnect_target(bridge: XsdbBridge) -> dict:
    """Reconnect to the already-open JTAG target.

    Sequence: disconnect -> connect -> list targets -> select the ARM DAP.

    Errors: RECONNECT_FAILED (with details.step naming the failing stage;
    reason NO_ARM_DAP when no ARM DAP target is present).
    """
    d = await disconnect_hw_server(bridge)
    if d["status"] != "success":
        return _reconnect_error("disconnect", reason_of(d))
    c = await connect_hw_server(bridge)
    if c["status"] != "success":
        return _reconnect_error("connect", reason_of(c))
    listing = await list_targets(bridge)
    if listing["status"] != "success":
        return _reconnect_error("list_targets", reason_of(listing))
    arm = _find_arm_dap(listing["data"]["targets"])
    if arm is None:
        return ps_error(
            "RECONNECT_FAILED",
            "no ARM DAP target found on the JTAG chain",
            details={"step": "list_targets", "sub_reason": "NO_ARM_DAP",
                     "targets": listing["data"]["targets"]})
    s = await select_target(bridge, arm["id"])
    if s["status"] != "success":
        return _reconnect_error("select_target", reason_of(s))
    return success(data={"reconnected": True,
                         "target_id": arm["id"],
                         "target_name": arm["name"],
                         "target_type": arm["type"]}).to_dict()


async def clear_debug_session(bridge: XsdbBridge) -> dict:
    """Clear residual debugger state (best-effort).

    Sequence: halt (best-effort) -> clear breakpoints (bpd -all) ->
    disconnect -> reconnect. This operation assumes no particular current
    state and always returns a success envelope carrying a per-step
    report; `cleared` is False when any step failed.

    Contract note: this is the one intentionally best-effort API in the
    domain — its purpose is cleanup, so a dead/unstarted bridge is
    reported (cleared=False) rather than raising.
    """
    if not getattr(bridge, "ready", False):
        return success(data={"cleared": False, "steps": [],
                             "reason": "BRIDGE_NOT_READY"}).to_dict()
    steps = []

    h = await halt_target(bridge)
    steps.append({"step": "halt", "ok": h["status"] == "success",
                  "error": None if h["status"] == "success" else reason_of(h)})

    r = await safe_eval(bridge, _BPD_ALL)
    br_err = extract_bridge_error(r)
    steps.append({"step": "clear_breakpoints", "ok": br_err is None,
                  "error": None if br_err is None else br_err[2]})

    d = await disconnect_hw_server(bridge)
    steps.append({"step": "disconnect", "ok": d["status"] == "success",
                  "error": None if d["status"] == "success" else reason_of(d)})

    rc = await reconnect_target(bridge)
    steps.append({"step": "reconnect", "ok": rc["status"] == "success",
                  "error": None if rc["status"] == "success" else reason_of(rc)})

    cleared = all(s["ok"] for s in steps)
    return success(data={
        "cleared": cleared,
        "steps": steps,
        "note": "best-effort cleanup; partial failures are reported per-step",
    }).to_dict()


async def diagnose_dap(bridge: XsdbBridge) -> dict:
    """Diagnose the DAP state and report likely causes.

    Always returns a success envelope (diagnosis is read-only).
    data.diagnosis: {"connected", "target_selected", "target_state",
    "dap_locked", "likely_issues", "suggested_action"}.
    """
    diagnosis = {"connected": False, "target_selected": False,
                 "target_state": None, "dap_locked": None,
                 "likely_issues": [], "suggested_action": None}
    diagnosis["connected"] = bool(getattr(bridge, "hw_connected", False))

    if diagnosis["connected"]:
        tid, _sel_err = await require_target_selected(bridge)
        if tid is not None:
            diagnosis["target_selected"] = True
            st = await get_target_status(bridge)
            diagnosis["target_state"] = st["data"].get("state") \
                if st["status"] == "success" else "unknown"
        # Best-effort DAP lock probe (read-only; failures are ignored).
        r = await safe_eval(bridge, templates.device_info())
        br_err = extract_bridge_error(r)
        if br_err is None:
            text = (r.get("data") or "").lower()
            if "lock" in text:
                diagnosis["dap_locked"] = "unlocked" not in text

    diagnosis["likely_issues"] = _likely_issues(diagnosis)
    diagnosis["suggested_action"] = _suggested_action(diagnosis)
    return success(data={"diagnosis": diagnosis}).to_dict()


# ── internal helpers ──────────────────────────────────────────────────────────

async def _core_reset(bridge: XsdbBridge) -> dict:
    """Reset the ARM cores (xsdb rst -cores). Recovery-only step."""
    pre = require_connected(bridge)
    if pre:
        return pre
    tid, err = await require_target_selected(bridge)
    if err:
        return err
    result = await safe_eval(bridge, templates.rst("cores"))
    br_err = extract_bridge_error(result)
    if br_err:
        return ps_error("RESET_FAILED",
                        f"core reset (rst -cores) failed: {br_err[2]}")
    return success(data={"scope": "cores", "reset_done": True,
                         "target_id": tid}).to_dict()


def _find_arm_dap(targets: list[dict]) -> dict | None:
    """Find the ARM DAP target in a list_targets listing."""
    for t in targets:
        ty = (t.get("type") or "").lower()
        name = (t.get("name") or "").lower()
        if "dap" in ty or "cortex" in ty or "arm" in name:
            return t
    return None


def _reconnect_error(step: str, step_reason: str) -> dict:
    return ps_error(
        "RECONNECT_FAILED",
        f"reconnect failed at step {step}",
        details={"reason_code": "RECONNECT_FAILED", "step": step,
                 "step_error": step_reason or "unknown"})


def _likely_issues(d: dict) -> list[str]:
    issues = []
    if not d["connected"]:
        issues.append("Cable disconnected or hw_server not reachable")
    elif not d["target_selected"]:
        issues.append("No ARM target selected on the JTAG chain")
    elif d["target_state"] == "reset":
        issues.append("Target held in reset")
    elif d["target_state"] == "unknown":
        issues.append("Target state cannot be determined")
    if d["dap_locked"] is True:
        issues.append("DAP locked - a power cycle may be required")
    if d["dap_locked"] is False and not issues:
        issues.append("Target is running normally")
    return issues


def _suggested_action(d: dict) -> str:
    if not d["connected"]:
        return "Connect hw_server, then run recover_target('auto')"
    if not d["target_selected"]:
        return "Select the ARM DAP target, then run recover_target('auto')"
    if d["target_state"] in ("running", "reset", "unknown"):
        return "Run recover_target('auto')"
    return "Target is healthy; no recovery needed"
