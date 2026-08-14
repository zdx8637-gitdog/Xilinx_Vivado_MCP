"""O3 production observation tests: real PID ownership + Vivado run truth."""
from __future__ import annotations

import asyncio
import sys

import pytest

from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_VIVADO,
    EXECUTION_LANE_BUSY,
    OP_RUNNING,
    STATUS_SOURCE_VENDOR_RUN,
    OBS_STARTING,
    OBS_RUNNING,
    OBS_COMPLETE,
    OBS_FAILED,
    OBS_UNKNOWN,
    HEALTH_ALIVE,
    HEALTH_UNRESPONSIVE,
    WORKER_STATE_ABSENT,
    operation_contract_fields,
    ledger_transaction,
    ledger_read_shared,
    _now_iso,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.process_guard import (
    get_process_identity,
    is_pid_alive,
)
from mcps.zynq_mcp.control.tool_process_controller import ToolProcessController
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner,
    DomainExecutionMutex,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.vivado_execution_observer import (
    VivadoExecutionFacade,
    normalize_vivado_run_status,
    parse_vivado_progress,
    parse_vivado_run_query,
)


class _ScriptedVivadoBridge:
    def __init__(self, statuses):
        self._statuses = list(statuses)
        self._last = self._statuses[-1] if self._statuses else ("Running", None)
        self._proc = None
        self.calls = []

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    @property
    def ready(self):
        return self._proc is not None and self._proc.returncode is None

    async def start(self):
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def eval(self, tcl, timeout_s=None):
        self.calls.append((tcl, timeout_s))
        if "__O3_STATUS=" in tcl:
            if self._statuses:
                self._last = self._statuses.pop(0)
            status, progress = self._last
            progress_text = "" if progress is None else str(progress)
            return {"status": "success", "data": (
                f"__O3_STATUS={status}\n__O3_PROGRESS={progress_text}")}
        if "BIT_DONE" in tcl:
            return {"status": "success", "data": "BIT_DONE"}
        return {"status": "success", "data": "OK"}

    async def stop(self):
        if self._proc is None:
            return
        proc = self._proc
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


def _runtime(tmp_path, statuses, operation_id="op-o3"):
    guard = InstanceGuard(tmp_path / "runtime", "ws-o3")
    guard.determine_role()
    ledger_path = tmp_path / "runtime" / "execution_ledger.json"

    def _init(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.execution_lane = EXECUTION_LANE_BUSY
        ledger.context = {
            "session_id": "sid-o3",
            "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(tmp_path / "project"),
            "current_stage": "PL_BUILD",
            "board_package_revision": "sha256:" + "1" * 64,
        }
        accepted_at = _now_iso()
        ledger.active_operation = {
            "operation_id": operation_id,
            "tool_name": "pl_synthesize",
            "status": OP_RUNNING,
            "api_category": "command",
            "session_id": "sid-o3",
            "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(tmp_path / "project"),
            "workflow_stage": "PL_BUILD",
            "request_signature": "sig-o3",
            "worker_generation": 0,
            "input_artifact_revision": "sha256:" + "1" * 64,
            "accepted_at": accepted_at,
            "started_at": accepted_at,
            "heartbeat_at": None,
            "finished_at": None,
            "output_artifact_revision": None,
            "completion_evidence": None,
            "error": None,
            "progress_pct": None,
            **operation_contract_fields(
                "pl_synthesize", accepted_at, timeout_s=30),
        }
        return ledger

    ledger_transaction(guard, ledger_path, _init)
    bridge = _ScriptedVivadoBridge(statuses)
    controller = ToolProcessController(
        guard,
        ledger_path,
        bridge_factories={BACKEND_VIVADO: lambda: bridge},
        identity_resolver=lambda pid, backend: (get_process_identity(pid), None),
    )
    return guard, ledger_path, controller, bridge


@pytest.mark.parametrize("raw,expected", [
    ("Not started", OBS_STARTING),
    ("Queued", OBS_STARTING),
    ("Running", OBS_RUNNING),
    ("synth_design Complete!", OBS_COMPLETE),
    ("ERROR", OBS_FAILED),
    ("", OBS_UNKNOWN),
])
def test_o3_status_normalization(raw, expected):
    assert normalize_vivado_run_status(raw) == expected


def test_o3_place_partial_run_terminal_matches_real_vivado_status():
    raw = "Not started phys_opt_design"
    assert normalize_vivado_run_status(
        raw, current_step="PLACE") == OBS_COMPLETE
    assert normalize_vivado_run_status(
        raw, current_step="SYNTHESIS") == OBS_STARTING


def test_o3_place_without_phys_opt_accepts_next_route_boundary():
    assert normalize_vivado_run_status(
        "Not started route_design", current_step="PLACE") == OBS_COMPLETE


def test_o3_bitstream_rejects_stale_route_complete_status():
    assert normalize_vivado_run_status(
        "route_design Complete!", current_step="BITSTREAM_WRITE") == OBS_RUNNING
    assert normalize_vivado_run_status(
        "write_bitstream Complete!", current_step="BITSTREAM_WRITE") == (
            OBS_COMPLETE)


@pytest.mark.parametrize("raw,expected", [
    ("42%", 42), ("3.5", 3.5), ("", None), ("unknown", None),
    ("101", None), ("-1", None),
])
def test_o3_progress_is_real_or_null(raw, expected):
    assert parse_vivado_progress(raw) == expected


def test_o3_query_marker_parser_uses_last_real_value():
    output = (
        'puts "__O3_STATUS=$__o3_status"\n'
        "__O3_STATUS=Running\n"
        "__O3_PROGRESS=57%")
    assert parse_vivado_run_query(output) == ("Running", 57)


def test_o3_server_does_not_construct_standalone_vivado_bridge():
    from pathlib import Path
    server_source = (Path(__file__).parents[1] / "server.py").read_text(
        encoding="utf-8")
    assert "VivadoTclBridge()" not in server_source
    assert "process_controller=process_controller" in server_source


@pytest.mark.asyncio
async def test_o3_run_timeline_is_persisted_from_vendor(tmp_path):
    guard, path, controller, bridge = _runtime(tmp_path, [
        ("Not started", None),
        ("Running", 50),
        ("synth_design Complete!", 100),
    ])
    try:
        snap = await controller.ensure_backend(
            BACKEND_VIVADO, operation_id="op-o3")
        assert is_pid_alive(snap.pid)
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="synth_1",
            launch_tcl="launch_runs synth_1 -jobs 4",
            current_step="SYNTHESIS",
            timeout_s=2.0,
            open_run=True,
        )
        assert result["status"] == "success"
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        obs = ledger.active_operation["observation"]
        assert obs["status_source"] == STATUS_SOURCE_VENDOR_RUN
        assert obs["observed_state"] == OBS_COMPLETE
        assert obs["vendor_status"] == "synth_design Complete!"
        assert obs["progress_pct"] == 100
        assert obs["worker_health"] == HEALTH_ALIVE
        assert obs["pid"] == snap.pid
        assert obs["current_step"] == "SYNTHESIS"
        assert all("wait_on_run" not in call[0] for call in bridge.calls)
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_missing_progress_remains_null_but_status_is_decidable(tmp_path):
    guard, path, controller, _ = _runtime(
        tmp_path, [("Running", None), ("Complete!", None)])
    try:
        await controller.ensure_backend(BACKEND_VIVADO, operation_id="op-o3")
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="synth_1", launch_tcl="launch_runs synth_1",
            current_step="SYNTHESIS", timeout_s=2.0)
        assert result["status"] == "success"
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.active_operation["observation"]["progress_pct"] is None
        assert ledger.active_operation["observation"]["observed_state"] == OBS_COMPLETE
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_place_partial_run_status_is_terminal(tmp_path):
    guard, path, controller, _ = _runtime(
        tmp_path, [("Not started phys_opt_design", 60)])
    try:
        await controller.ensure_backend(BACKEND_VIVADO, operation_id="op-o3")
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="impl_1",
            launch_tcl="launch_runs impl_1 -to_step place_design",
            current_step="PLACE", timeout_s=2.0)
        assert result["status"] == "success"
        assert result["data"]["vendor_status"] == (
            "Not started phys_opt_design")
        assert result["data"]["progress_pct"] == 60
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.active_operation["observation"]["observed_state"] == (
            OBS_COMPLETE)
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_bitstream_waits_past_stale_route_terminal(tmp_path):
    guard, path, controller, bridge = _runtime(tmp_path, [
        ("route_design Complete!", 100),
        ("write_bitstream Complete!", 100),
    ])
    try:
        await controller.ensure_backend(BACKEND_VIVADO, operation_id="op-o3")
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="impl_1",
            launch_tcl="launch_runs impl_1 -to_step write_bitstream",
            current_step="BITSTREAM_WRITE", timeout_s=2.0)
        assert result["status"] == "success"
        assert result["data"]["vendor_status"] == (
            "write_bitstream Complete!")
        status_queries = [tcl for tcl, _ in bridge.calls
                          if "__O3_STATUS=" in tcl]
        assert len(status_queries) == 2
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_vendor_failed_status_is_not_reported_complete(tmp_path):
    guard, path, controller, _ = _runtime(
        tmp_path, [("route_design ERROR", 73)])
    try:
        await controller.ensure_backend(BACKEND_VIVADO, operation_id="op-o3")
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="impl_1", launch_tcl="launch_runs impl_1",
            current_step="ROUTE", timeout_s=2.0)
        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == "VIVADO_RUN_FAILED"
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.active_operation["observation"]["observed_state"] == OBS_FAILED
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_timeout_marks_unresponsive_and_cleans_real_pid(tmp_path):
    guard, path, controller, _ = _runtime(tmp_path, [("Running", None)])
    try:
        snap = await controller.ensure_backend(
            BACKEND_VIVADO, operation_id="op-o3")
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=0.01)
        result = await facade.run_vivado_run(
            run_name="synth_1", launch_tcl="launch_runs synth_1",
            current_step="SYNTHESIS", timeout_s=0.03)
        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == "VIVADO_TIMEOUT"
        assert result["error"]["details"]["pid_cleaned"] is True
        assert not is_pid_alive(snap.pid)
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.worker["state"] == WORKER_STATE_ABSENT
        obs = ledger.active_operation["observation"]
        assert obs["worker_health"] == HEALTH_UNRESPONSIVE
        assert obs["observed_state"] == OBS_UNKNOWN
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_command_runner_uses_controller_and_vendor_terminal(tmp_path):
    """Production admission -> controller -> observer -> terminal chain."""
    from mcps.common.board_profile import board_profile_load
    from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn

    profile = board_profile_load("ALINX_AX7020_v1.0")
    guard = InstanceGuard(tmp_path / "runner-runtime", "ws-o3-runner")
    guard.determine_role()
    path = tmp_path / "runner-runtime" / "execution_ledger.json"
    project = tmp_path / "project"
    project.mkdir()

    def _init(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.context = {
            "session_id": "sid-o3-runner",
            "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(project),
            "current_stage": "PL_BUILD",
            "board_package_revision": profile["package_revision"],
            "expected_board_revision": profile["package_revision"],
            "board_profile_sha256": profile["sha256"],
        }
        return ledger

    ledger_transaction(guard, path, _init)
    bridge = _ScriptedVivadoBridge([("synth_design Complete!", 100)])
    controller = ToolProcessController(
        guard, path,
        bridge_factories={BACKEND_VIVADO: lambda: bridge},
        identity_resolver=lambda pid, backend: (get_process_identity(pid), None),
    )
    registry = OperationRegistry()
    runner = CommandRunner(
        guard, path, registry, DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "pl_synthesize", {}, "sid-o3-runner",
            "ALINX_AX7020_v1.0", str(project),
            executor="local",
            local_fn=_make_pl_bridge_local_fn("pl_synthesize"),
            timeout_s=5.0, next_stage="PL_IMPLEMENT")
        assert accepted["status"] == "success", accepted
        oid = accepted["data"]["operation_id"]
        for _ in range(200):
            ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
            if ledger.previous_operation and \
                    ledger.previous_operation.get("operation_id") == oid:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("controlled Vivado operation did not terminate")
        assert ledger.previous_operation["status"] == "SUCCEEDED"
        assert ledger.previous_operation["observation"]["status_source"] == \
            STATUS_SOURCE_VENDOR_RUN
        assert ledger.previous_operation["observation"]["vendor_status"] == \
            "synth_design Complete!"
        assert ledger.context["current_stage"] == "PL_IMPLEMENT"
        assert controller.backend == BACKEND_VIVADO
        assert is_pid_alive(controller.bridge.pid)
        assert all("wait_on_run" not in call[0] for call in bridge.calls)
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.host_live
@pytest.mark.asyncio
async def test_o3_host_live_real_vivado_status_timeline(tmp_path):
    """Real Vivado launch_runs is polled through STATUS/PROGRESS markers."""
    guard, path, _, _ = _runtime(tmp_path, [("unused", None)])
    source = tmp_path / "top.v"
    source.write_text(
        "module top(input wire clk, output wire led);\n"
        "assign led = clk;\nendmodule\n",
        encoding="utf-8")
    project_dir = tmp_path / "vivado_project"
    controller = ToolProcessController(guard, path)
    try:
        snap = await controller.ensure_backend(
            BACKEND_VIVADO, operation_id="op-o3")
        assert is_pid_alive(snap.pid)
        facade = VivadoExecutionFacade(
            controller, "op-o3", guard, path, poll_interval_s=2.0)
        facade.set_current_step("PROJECT_OPEN")
        setup = await facade.eval(
            f"create_project o3_live {{{project_dir.as_posix()}}} "
            "-part xc7z020clg400-2 -force\n"
            f"add_files {{{source.as_posix()}}}\n"
            "set_property top top [current_fileset]",
            timeout_s=120.0)
        assert setup["status"] == "success", setup
        result = await facade.run_vivado_run(
            run_name="synth_1",
            launch_tcl="launch_runs synth_1 -jobs 2",
            current_step="SYNTHESIS",
            timeout_s=600.0,
            open_run=False)
        assert result["status"] == "success", result
        assert "Complete!" in result["data"]["vendor_status"]
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        obs = ledger.active_operation["observation"]
        assert obs["status_source"] == STATUS_SOURCE_VENDOR_RUN
        assert obs["observed_state"] == OBS_COMPLETE
        assert obs["vendor_status"] == result["data"]["vendor_status"]
        assert obs["pid"] == snap.pid
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()


@pytest.mark.asyncio
async def test_o3_bitstream_terminal_closes_vivado_and_publishes_manifest(
        tmp_path):
    """C08/C04: PL terminal evidence and backend cleanup precede PS_BUILD."""
    from mcps.zynq_mcp.tests.test_build_manifest import (
        _pl_project, _prep_ledger, _wait_terminal, BOARD,
    )
    from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn

    root, snapshot, files = _pl_project(tmp_path)
    runtime = tmp_path / "bit-runtime"
    guard = InstanceGuard(runtime, "ws-build-manifest")
    guard.determine_role()
    path = _prep_ledger(
        runtime, guard, root, stage="PL_BITSTREAM",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"],
        prev={"operation_id": "op-prev", "tool_name": "pl_analyze_timing",
              "status": "SUCCEEDED",
              "completion_evidence": {"timing_met": True}})
    bridge = _ScriptedVivadoBridge([("write_bitstream Complete!", 100)])
    controller = ToolProcessController(
        guard, path,
        bridge_factories={BACKEND_VIVADO: lambda: bridge},
        identity_resolver=lambda pid, backend: (get_process_identity(pid), None),
    )
    runner = CommandRunner(
        guard, path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "pl_generate_bitstream", {"path": files["bit"]},
            snapshot["session_id"], BOARD, root,
            executor="local",
            local_fn=_make_pl_bridge_local_fn("pl_generate_bitstream"),
            timeout_s=5.0, next_stage="PS_BUILD")
        assert accepted["status"] == "success", accepted
        oid = accepted["data"]["operation_id"]
        await _wait_terminal(guard, path, oid)
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        previous = ledger.previous_operation
        assert previous["status"] == "SUCCEEDED"
        assert previous["artifact_state"] == "PUBLISHED"
        assert previous["observation"]["current_step"] == \
            "PL_MANIFEST_PUBLISH"
        assert previous["completion_evidence"]["manifest_revision"]
        assert ledger.context["current_stage"] == "PS_BUILD"
        assert ledger.worker["state"] == WORKER_STATE_ABSENT
        assert controller.has_backend is False
        assert bridge.pid is not None and not is_pid_alive(bridge.pid)
    finally:
        await controller.shutdown_backend(force=True)
        guard.release_owner_lock()
