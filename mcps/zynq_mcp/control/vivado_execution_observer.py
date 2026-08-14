"""Vivado execution facade backed by the O2 ToolProcessController.

The facade is deliberately internal.  It gives the existing Platform and PL
domain implementations their historical ``call_tool``/``eval`` shapes while
making the real Vivado process and Vivado run object the source of Ledger
observations.

Long runs never use ``wait_on_run``.  A launch command returns control to the
MCP, then short Tcl queries poll STATUS/PROGRESS until a vendor terminal state
is observed or the operation deadline expires.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Optional

from mcps.zynq_mcp.control.execution_ledger import (
    _now_iso,
    STATUS_SOURCE_VENDOR_RUN,
    BACKEND_VIVADO,
    OBS_STARTING,
    OBS_RUNNING,
    OBS_COMPLETE,
    OBS_FAILED,
    OBS_UNKNOWN,
    HEALTH_ALIVE,
    HEALTH_UNRESPONSIVE,
    ARTIFACT_PENDING,
    ACTION_WAIT,
)
from mcps.zynq_mcp.control.operation_service import op_observe
from mcps.zynq_mcp.control.tool_process_controller import (
    ToolProcessControllerError,
)


_STATUS_RE = re.compile(r"__O3_STATUS=(.*)")
_PROGRESS_RE = re.compile(r"__O3_PROGRESS=(.*)")


def normalize_vivado_run_status(raw_status: str, *, current_step: str = "") -> str:
    """Map a real Vivado run STATUS string to the frozen observation model."""
    text = str(raw_status or "").strip().lower()
    step = str(current_step or "").strip().upper()
    if not text:
        return OBS_UNKNOWN
    if any(token in text for token in (
            "error", "failed", "cancelled", "canceled", "aborted")):
        return OBS_FAILED
    if "complete!" in text or text in {"complete", "completed"}:
        # Vivado retains the previous run-step STATUS while a newly requested
        # later step is starting.  In particular, immediately after
        # ``launch_runs impl_1 -to_step write_bitstream`` it can still report
        # ``route_design Complete!``.  Do not finalize the bitstream operation
        # until the requested vendor step itself is complete.
        expected = {
            "SYNTHESIS": "synth_design",
            "ROUTE": "route_design",
            "BITSTREAM_WRITE": "write_bitstream",
        }.get(step)
        generic_complete = text in {"complete!", "complete", "completed"}
        if expected and expected not in text and not generic_complete:
            return OBS_RUNNING
        return OBS_COMPLETE
    # A run launched with ``-to_step place_design`` stops immediately before
    # the next implementation step.  Vivado 2023.1 reports that successful
    # partial-run terminal as "Not started phys_opt_design" (or, for a
    # strategy without phys_opt, "Not started route_design") rather than a
    # generic Complete string.  It is terminal only for the PLACE request;
    # the same raw status must remain STARTING for every other requested step.
    if step == "PLACE" and any(
            text.startswith(f"not started {next_step}")
            for next_step in ("phys_opt_design", "route_design")):
        return OBS_COMPLETE
    if "not started" in text or "queued" in text or "launching" in text:
        return OBS_STARTING
    if any(token in text for token in (
            "running", "synth_design", "place_design", "route_design",
            "write_bitstream")):
        return OBS_RUNNING
    return OBS_UNKNOWN


def parse_vivado_progress(raw_progress: object) -> Optional[float]:
    """Return a real 0..100 Vivado progress value, otherwise ``None``."""
    text = str(raw_progress or "").strip().rstrip("%").strip()
    if not text:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0 or value > 100:
        return None
    return int(value) if value.is_integer() else value


def parse_vivado_run_query(output: object) -> tuple[str, Optional[float]]:
    """Parse the marker output emitted by :meth:`run_vivado_run`."""
    statuses = []
    progresses = []
    for line in str(output or "").splitlines():
        sm = _STATUS_RE.search(line.strip())
        if sm and "$__" not in sm.group(1):
            statuses.append(sm.group(1).strip())
        pm = _PROGRESS_RE.search(line.strip())
        if pm and "$__" not in pm.group(1):
            progresses.append(pm.group(1).strip())
    status = statuses[-1] if statuses else ""
    progress = parse_vivado_progress(progresses[-1] if progresses else None)
    return status, progress


class VivadoExecutionFacade:
    """Compatibility facade plus real Vivado run observer.

    ``controller`` owns the subprocess and its five-field identity.  This
    class never starts or kills a process directly.
    """

    def __init__(self, controller, operation_id: Optional[str], guard,
                 ledger_path, *, poll_interval_s: float = 2.0,
                 observe_process: bool = True):
        self._controller = controller
        self._operation_id = operation_id
        self._guard = guard
        self._ledger_path = ledger_path
        self._poll_interval_s = max(0.01, float(poll_interval_s))
        self._current_step = "VIVADO_COMMAND"
        self._observe_process_enabled = bool(observe_process)

    @property
    def ready(self) -> bool:
        return bool(self._controller.has_backend and
                    self._controller.backend == BACKEND_VIVADO and
                    self._controller.bridge is not None and
                    getattr(self._controller.bridge, "ready", False))

    def set_current_step(self, step: str) -> None:
        if isinstance(step, str) and step.strip():
            self._current_step = step.strip()

    async def _observe_process(self, step: Optional[str] = None) -> dict:
        if not self._observe_process_enabled:
            return {"status": "success", "data": {}}
        return await self._controller.observe_backend(
            operation_id=self._operation_id,
            current_step=step or self._current_step,
        )

    async def _eval_raw(self, tcl: str, timeout_s: Optional[float] = None) -> dict:
        bridge = self._controller.bridge
        if bridge is None or not self.ready:
            return self._error("BACKEND_NOT_ACTIVE", "Vivado backend is not active")
        try:
            result = await bridge.eval(tcl, timeout_s=timeout_s)
        except Exception as exc:
            # Re-observation converts a dead/replaced process into the
            # controller's persisted OUTCOME_UNKNOWN result when possible.
            observed = await self._observe_process(self._current_step)
            persisted = bool(observed.get("error", {}).get(
                "details", {}).get("ledger_persisted", False))
            return self._error(
                "OPERATION_OUTCOME_UNKNOWN", str(exc),
                ledger_persisted=persisted,
            )
        reason = result.get("error", {}).get("details", {}).get("reason_code") \
            if isinstance(result, dict) else None
        if reason in {"XSDM_PROCESS_DEAD", "XSDM_WRITE_FAILED"}:
            observed = await self._observe_process(self._current_step)
            persisted = bool(observed.get("error", {}).get(
                "details", {}).get("ledger_persisted", False))
            return self._error(
                "OPERATION_OUTCOME_UNKNOWN",
                result.get("error", {}).get("message", reason),
                ledger_persisted=persisted,
            )
        return result

    async def eval(self, tcl: str, timeout_s: Optional[float] = None) -> dict:
        observed = await self._observe_process(self._current_step)
        if observed.get("status") != "success":
            return observed
        result = await self._eval_raw(tcl, timeout_s=timeout_s)
        if result.get("status") == "success":
            after = await self._observe_process(self._current_step)
            if after.get("status") != "success":
                return after
        return result

    async def call_tool(self, name: str, arguments: dict,
                        timeout: Optional[float] = None):
        if name != "run_tcl":
            return self._error("UNSUPPORTED_INTERNAL_TOOL", name)
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if not isinstance(command, str) or not command.strip():
            return self._error("INVALID_ARGUMENT", "command must be non-empty")
        requested = arguments.get("timeout") if isinstance(arguments, dict) else None
        timeout_s = timeout if timeout is not None else requested
        result = await self.eval(command, timeout_s=timeout_s)
        if result.get("status") != "success":
            return result
        return {"status": "success", "data": {"output": result.get("data", "")}}

    async def run_vivado_run(self, *, run_name: str, launch_tcl: str,
                             current_step: str, timeout_s: float,
                             open_run: bool = False) -> dict:
        """Launch and poll a Vivado run without a long blocking Tcl eval."""
        if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) \
                or not math.isfinite(float(timeout_s)) or timeout_s <= 0:
            return self._error("INVALID_ARGUMENT", "timeout_s must be positive")
        self.set_current_step(current_step)
        before = await self._observe_process(current_step)
        if before.get("status") != "success":
            return before
        launched = await self._eval_raw(launch_tcl, timeout_s=min(120.0, timeout_s))
        if launched.get("status") != "success":
            return self._tcl_error(launched, "VIVADO_RUN_LAUNCH_FAILED")

        deadline = time.monotonic() + float(timeout_s)
        query = (
            f"set __o3_run [get_runs {{{run_name}}}]\n"
            "set __o3_status [get_property STATUS $__o3_run]\n"
            "set __o3_progress [get_property PROGRESS $__o3_run]\n"
            'puts "__O3_STATUS=$__o3_status"\n'
            'puts "__O3_PROGRESS=$__o3_progress"'
        )
        while True:
            process = await self._observe_process(current_step)
            if process.get("status") != "success":
                return process
            queried = await self._eval_raw(query, timeout_s=30.0)
            if queried.get("status") != "success":
                return self._tcl_error(queried, "VIVADO_STATUS_QUERY_FAILED")
            raw_status, progress = parse_vivado_run_query(queried.get("data", ""))
            state = normalize_vivado_run_status(
                raw_status, current_step=current_step)
            try:
                self._publish_vendor_observation(
                    process, run_name, current_step, raw_status, state, progress)
            except Exception as exc:
                return self._error("LEDGER_WRITE_FAILED", str(exc))

            if state == OBS_COMPLETE:
                if open_run:
                    opened = await self._eval_raw(
                        f"open_run [get_runs {{{run_name}}}]", timeout_s=120.0)
                    if opened.get("status") != "success":
                        return self._tcl_error(opened, "VIVADO_OPEN_RUN_FAILED")
                return {"status": "success", "data": {
                    "run_name": run_name,
                    "vendor_status": raw_status,
                    "progress_pct": progress,
                    "output": queried.get("data", ""),
                }}
            if state == OBS_FAILED:
                return self._error(
                    "VIVADO_RUN_FAILED",
                    f"{run_name} failed with STATUS={raw_status}",
                    vendor_status=raw_status,
                )
            if state == OBS_UNKNOWN:
                return self._error(
                    "VIVADO_STATUS_UNPARSEABLE",
                    f"{run_name} returned unknown STATUS={raw_status!r}",
                    vendor_status=raw_status,
                )
            if time.monotonic() >= deadline:
                self._publish_unresponsive(
                    process, run_name, current_step, raw_status, progress)
                cleanup = await self._controller.shutdown_backend(
                    operation_id=self._operation_id)
                if cleanup.success:
                    return self._error(
                        "VIVADO_TIMEOUT", f"{run_name} exceeded deadline",
                        ledger_persisted=False, pid_cleaned=True)
                return self._error(
                    "OPERATION_OUTCOME_UNKNOWN",
                    f"{run_name} timed out and cleanup was not proven",
                    ledger_persisted=True, pid_cleaned=False)
            await asyncio.sleep(self._poll_interval_s)

    def _publish_vendor_observation(self, process_result: dict, run_name: str,
                                    step: str, raw_status: str, state: str,
                                    progress: Optional[float]) -> None:
        process_obs = process_result.get("data", {}).get("observation", {})
        observed_at = _now_iso()
        update = dict(process_obs)
        update.update({
            "status_source": STATUS_SOURCE_VENDOR_RUN,
            "backend": BACKEND_VIVADO,
            "observed_state": state,
            "vendor_status": raw_status,
            "current_step": step,
            "progress_pct": progress,
            "worker_health": HEALTH_ALIVE,
            "controller_heartbeat_at": observed_at,
            "observed_at": observed_at,
            "last_output_at": observed_at,
            "detail": {"run_name": run_name},
        })
        op_observe(
            self._guard, self._ledger_path,
            self._operation_id, update,
            artifact_state=(ARTIFACT_PENDING
                            if step == "BITSTREAM_WRITE" else None),
            recommended_action=ACTION_WAIT,
        )

    def _publish_unresponsive(self, process_result: dict, run_name: str,
                              step: str, raw_status: str,
                              progress: Optional[float]) -> None:
        process_obs = process_result.get("data", {}).get("observation", {})
        observed_at = _now_iso()
        update = dict(process_obs)
        update.update({
            "status_source": STATUS_SOURCE_VENDOR_RUN,
            "backend": BACKEND_VIVADO,
            "observed_state": OBS_UNKNOWN,
            "vendor_status": raw_status,
            "current_step": step,
            "progress_pct": progress,
            "worker_health": HEALTH_UNRESPONSIVE,
            "controller_heartbeat_at": observed_at,
            "observed_at": observed_at,
            "last_output_at": observed_at,
            "detail": {"run_name": run_name, "reason_code": "VIVADO_TIMEOUT"},
        })
        op_observe(
            self._guard, self._ledger_path,
            self._operation_id, update,
            artifact_state=(ARTIFACT_PENDING
                            if step == "BITSTREAM_WRITE" else None),
            recommended_action=ACTION_WAIT,
        )

    @staticmethod
    def _tcl_error(result: dict, fallback: str) -> dict:
        if isinstance(result, dict) and result.get("status") == "error":
            details = result.get("error", {}).get("details", {})
            if details.get("reason_code") == "OPERATION_OUTCOME_UNKNOWN":
                return result
            message = result.get("error", {}).get("message", fallback)
            return VivadoExecutionFacade._error(fallback, str(message))
        return VivadoExecutionFacade._error(fallback, str(result))

    @staticmethod
    def _error(reason_code: str, message: str = "", **details) -> dict:
        return {"status": "error", "error": {
            "code": "TOOL_ERROR",
            "message": message or reason_code,
            "details": {"reason_code": reason_code, **details},
        }}
