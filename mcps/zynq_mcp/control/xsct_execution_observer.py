"""Controlled XSCT execution with truthful process/command observations.

XSCT exposes synchronous Tcl commands rather than Vivado-style run objects.
Consequently this observer never invents a vendor run status or progress.  It
records the actual owned XSCT process identity and the command step that is
currently in flight, then records the command's real return outcome.
"""
from __future__ import annotations

import asyncio

from mcps.zynq_mcp.control.execution_ledger import (
    STATUS_SOURCE_PROCESS, BACKEND_XSCT,
    OBS_RUNNING, OBS_COMPLETE, OBS_FAILED,
    HEALTH_ALIVE, ACTION_WAIT, _now_iso,
)
from mcps.zynq_mcp.control.operation_service import op_observe


class XsctExecutionFacade:
    """XsctBridge-compatible facade owned by ToolProcessController."""

    def __init__(self, controller, operation_id, guard, ledger_path,
                 *, pulse_interval_s: float = 5.0):
        self._controller = controller
        self._operation_id = operation_id
        self._guard = guard
        self._ledger_path = ledger_path
        self._step = "XSCT_COMMAND"
        self._pulse_interval_s = max(0.05, float(pulse_interval_s))

    @property
    def _bridge(self):
        return self._controller.bridge

    @property
    def ready(self) -> bool:
        return bool(self._bridge is not None and
                    getattr(self._bridge, "ready", False))

    @property
    def workspace(self):
        return getattr(self._bridge, "workspace", None)

    @workspace.setter
    def workspace(self, value) -> None:
        bridge = self._bridge
        if bridge is None:
            raise RuntimeError("BACKEND_NOT_ACTIVE")
        bridge.workspace = value

    @property
    def pid(self):
        bridge = self._bridge
        return getattr(bridge, "pid", None) if bridge is not None else None

    def set_current_step(self, step: str) -> None:
        if isinstance(step, str) and step.strip():
            self._step = step.strip()

    async def observe_step(self, step: str, observed_state: str = OBS_RUNNING,
                           *, vendor_status: str | None = None,
                           detail: dict | None = None) -> None:
        self.set_current_step(step)
        if observed_state == OBS_RUNNING:
            result = await self._controller.observe_backend(
                operation_id=self._operation_id, current_step=self._step)
            if result.get("status") != "success":
                reason = (result.get("error", {}).get("details", {})
                          .get("reason_code", "BACKEND_OBSERVE_FAILED"))
                raise RuntimeError(reason)
            return
        now = _now_iso()
        op_observe(
            self._guard, self._ledger_path, self._operation_id,
            {
                "status_source": STATUS_SOURCE_PROCESS,
                "backend": BACKEND_XSCT,
                "observed_state": observed_state,
                "vendor_status": vendor_status,
                "current_step": self._step,
                "progress_pct": None,
                "worker_health": HEALTH_ALIVE,
                "observed_at": now,
                "last_output_at": now,
                "detail": dict(detail or {}),
            },
            recommended_action=ACTION_WAIT,
        )

    async def _pulse(self, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), self._pulse_interval_s)
                return
            except asyncio.TimeoutError:
                result = await self._controller.observe_backend(
                    operation_id=self._operation_id,
                    current_step=self._step)
                if result.get("status") != "success":
                    return

    async def eval(self, tcl: str, timeout_s: float | None = None,
                   tolerate_stderr: bool = False) -> dict:
        await self.observe_step(self._step, OBS_RUNNING)
        stop = asyncio.Event()
        pulse = asyncio.create_task(self._pulse(stop))
        try:
            result = await self._bridge.eval(
                tcl, timeout_s=timeout_s,
                tolerate_stderr=tolerate_stderr)
        finally:
            stop.set()
            await pulse

        success = isinstance(result, dict) and result.get("status") == "success"
        if self._operation_is_active():
            await self.observe_step(
                self._step, OBS_COMPLETE if success else OBS_FAILED,
                vendor_status=("XSCT_COMMAND_COMPLETE" if success
                               else "XSCT_COMMAND_FAILED"),
            )
        return result

    def _operation_is_active(self) -> bool:
        try:
            from mcps.zynq_mcp.control.execution_ledger import ledger_read_shared
            ledger, _ = ledger_read_shared(self._guard, self._ledger_path)
            active = ledger.active_operation
            return (isinstance(active, dict) and
                    active.get("operation_id") == self._operation_id)
        except Exception:
            return False
