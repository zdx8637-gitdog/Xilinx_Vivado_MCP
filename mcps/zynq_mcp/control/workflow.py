"""
workflow.py — B13-M1: legal iteration mutators (workflow_rollback /
workflow_resume_from).

The stage machine is forward-by-atoms; real development iterates
(build → test → find defect → fix → rebuild). These mutators make the loop
a first-class, auditable operation instead of ledger surgery or a full
close+create re-walk:

- workflow_rollback: move BACK to a legal target stage (per
  ROLLBACK_TARGETS), invalidating downstream artifact revisions so
  verify_consistency stays truthful.
- workflow_resume_from: validated FORWARD jump using existing artifacts
  (platform manifest / XSA / wrapper present), replacing the close+create
  full re-walk when only the PL/PS side changed.

Both are fail-closed: lane must be IDLE, no non-terminal active operation,
session_id must match. Both reset dedup_registry (P1-B semantics) so that
re-running the same build commands after a stage move is not falsely
rejected by the P10 dedup gate.
"""
import json
import logging
from pathlib import Path
from typing import Callable

from mcps.zynq_mcp.control.context import (
    SERIAL_STAGES, STAGE_PL_GENERATE, STAGE_PL_BUILD,
    STAGE_PL_BITSTREAM, STAGE_PS_BUILD, is_valid_rollback,
)
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, EXECUTION_LANE_IDLE, OP_NON_TERMINAL, ChannelBusyError,
)

logger = logging.getLogger("zynq_mcp.workflow")

# Artifacts that must exist on disk for a resume target to be plausible.
# resume_from never fabricates products — it only fast-forwards the stage
# pointer when the evidence is already on disk.
_RESUME_ARTIFACTS = {
    "PL_GENERATE": ("manifest",),
    "PL_BUILD": ("manifest", "xsa", "wrapper"),
    "PL_IMPLEMENT": ("manifest", "xsa", "wrapper"),
    "PL_TIMING": ("manifest", "xsa", "wrapper"),
    "PL_BITSTREAM": ("manifest", "xsa", "wrapper"),
    "PS_BUILD": ("manifest", "xsa"),
}


def _stage_index(stage: str) -> int:
    try:
        return SERIAL_STAGES.index(stage)
    except ValueError:
        raise ChannelBusyError("STAGE_UNKNOWN")


def _check_artifacts(project_path: str, target: str) -> None:
    """Fail-closed artifact presence check for a resume target."""
    kinds = _RESUME_ARTIFACTS.get(target)
    if not kinds:
        return
    pp = Path(project_path)
    missing = []
    if "manifest" in kinds:
        mdir = pp / "manifests" / "platform"
        if not mdir.is_dir() or not any(mdir.glob("*.json")):
            missing.append("platform_manifest")
    if "xsa" in kinds:
        if not (pp / "platform.xsa").is_file():
            missing.append("platform.xsa")
    if "wrapper" in kinds:
        hdl = pp / "hdl"
        if not hdl.is_dir() or not any(hdl.glob("*_wrapper.v")):
            missing.append("hdl_wrapper")
    if missing:
        raise ChannelBusyError("RESUME_ARTIFACTS_MISSING:" + ",".join(missing))


def workflow_rollback_mutator(arguments) -> Callable:
    session_id = str(arguments.get("session_id", "")).strip()
    target = str(arguments.get("target_stage", "")).strip()
    reason = str(arguments.get("reason", "")).strip()

    def _mutator(current):
        if current.execution_lane != EXECUTION_LANE_IDLE:
            raise ChannelBusyError("CHANNEL_BUSY")
        ctx = current.context or {}
        if not session_id or ctx.get("session_id") != session_id:
            raise ChannelBusyError("SESSION_ID_MISMATCH")
        cur_stage = ctx.get("current_stage", "")
        if not is_valid_rollback(cur_stage, target):
            raise ChannelBusyError("ROLLBACK_TARGET_INVALID")
        ao = current.active_operation
        if ao and ao.get("status") in OP_NON_TERMINAL:
            raise ChannelBusyError("ACTIVE_OPERATION_PRESENT")
        t_idx = _stage_index(target)
        ctx["current_stage"] = target
        # Invalidate downstream revisions so verify_consistency stays truthful.
        if t_idx <= _stage_index(STAGE_PL_GENERATE):
            ctx["platform_revision"] = None
        if t_idx <= _stage_index(STAGE_PL_BUILD):
            ctx["pl_revision"] = None
        if t_idx <= _stage_index(STAGE_PS_BUILD):
            ctx["ps_revision"] = None
        # Iteration observability (B13-M1): record the loop in the ledger.
        history = ctx.get("workflow_history") or []
        if isinstance(history, list):
            history.append({"from": cur_stage, "to": target, "reason": reason})
            ctx["workflow_history"] = history
        # P1-B: dedup_registry must not survive a stage move — re-running the
        # same build commands after a rollback must not be falsely rejected.
        if not isinstance(current.dedup_registry, dict):
            current.dedup_registry = {}
        current.dedup_registry.clear()
        return current

    def _commit(guard, ledger_path):
        return ledger_transaction(guard, ledger_path, _mutator)
    return _commit


def workflow_resume_mutator(arguments) -> Callable:
    session_id = str(arguments.get("session_id", "")).strip()
    target = str(arguments.get("target_stage", "")).strip()
    reason = str(arguments.get("reason", "")).strip()

    def _mutator(current):
        if current.execution_lane != EXECUTION_LANE_IDLE:
            raise ChannelBusyError("CHANNEL_BUSY")
        ctx = current.context or {}
        if not session_id or ctx.get("session_id") != session_id:
            raise ChannelBusyError("SESSION_ID_MISMATCH")
        cur_stage = ctx.get("current_stage", "")
        cur_idx = _stage_index(cur_stage)
        t_idx = _stage_index(target)
        if t_idx <= cur_idx:
            # resume_from is a forward fast-forward; backward moves go through
            # workflow_rollback (legal-target checked there).
            raise ChannelBusyError("RESUME_TARGET_NOT_FORWARD")
        ao = current.active_operation
        if ao and ao.get("status") in OP_NON_TERMINAL:
            raise ChannelBusyError("ACTIVE_OPERATION_PRESENT")
        _check_artifacts(ctx.get("project_path", ""), target)
        ctx["current_stage"] = target
        history = ctx.get("workflow_history") or []
        if isinstance(history, list):
            history.append({"from": cur_stage, "to": target, "reason": reason,
                            "via": "resume"})
            ctx["workflow_history"] = history
        if not isinstance(current.dedup_registry, dict):
            current.dedup_registry = {}
        current.dedup_registry.clear()
        return current

    def _commit(guard, ledger_path):
        return ledger_transaction(guard, ledger_path, _mutator)
    return _commit
