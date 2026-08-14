"""Controller-owned XSDB facade with real PROCESS and JTAG RESOURCE truth."""
from __future__ import annotations

from mcps.zynq_mcp.control.execution_ledger import BACKEND_XSDB


class XsdbExecutionFacade:
    """XsdbBridge-compatible facade used by formal PS/JTAG command paths."""

    def __init__(self, controller, operation_id, session_id, guard, ledger_path,
                 jtag_registry):
        self._controller = controller
        self._operation_id = operation_id
        self._session_id = session_id
        self._guard = guard
        self._ledger_path = ledger_path
        self._registry = jtag_registry
        self._step = "XSDB_COMMAND"

    @property
    def _bridge(self):
        return self._controller.bridge

    @property
    def ready(self) -> bool:
        return bool(self._controller.backend == BACKEND_XSDB and
                    self._bridge is not None and getattr(self._bridge, "ready", False))

    @property
    def pid(self):
        return getattr(self._bridge, "pid", None) if self._bridge is not None else None

    @property
    def hw_connected(self) -> bool:
        return bool(self._bridge is not None and
                    getattr(self._bridge, "hw_connected", False))

    def set_hw_connected(self, value: bool) -> None:
        if self._bridge is None:
            raise RuntimeError("BACKEND_NOT_ACTIVE")
        self._bridge.set_hw_connected(value)

    def set_current_step(self, step: str) -> None:
        if isinstance(step, str) and step.strip():
            self._step = step.strip()

    async def eval(self, tcl: str, timeout_s: float | None = None,
                   tolerate_stderr: bool = False) -> dict:
        observed = await self._controller.observe_backend(
            operation_id=self._operation_id, current_step=self._step)
        if observed.get("status") != "success":
            return observed
        return await self._bridge.eval(
            tcl, timeout_s=timeout_s, tolerate_stderr=tolerate_stderr)

    def record_tool_result(self, tool_name: str, arguments: dict,
                           result: dict) -> dict:
        return self._registry.record_result(
            self._operation_id, self._session_id, tool_name, arguments,
            result, self._step)
