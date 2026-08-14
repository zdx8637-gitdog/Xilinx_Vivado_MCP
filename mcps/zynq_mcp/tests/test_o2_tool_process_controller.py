"""O2 — unified EDA backend ownership and truthful PROCESS observation."""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_NONE, BACKEND_VIVADO, BACKEND_XSCT, BACKEND_XSDB,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY,
    EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_RUNNING, OP_OUTCOME_UNKNOWN,
    WORKER_STATE_ABSENT, WORKER_STATE_READY, WORKER_STATE_ORPHANED,
    STATUS_SOURCE_PROCESS, OBS_RUNNING, HEALTH_ALIVE,
    operation_contract_fields, ledger_transaction, ledger_read_shared, _now_iso,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.process_guard import (
    WorkerIdentity, get_process_identity, is_pid_alive, kill_process_tree_exact,
    backend_process_matches, descendant_pids, is_descendant_pid,
    resolve_backend_process_identity,
)
from mcps.zynq_mcp.control.tool_process_controller import (
    ToolProcessController, ToolProcessControllerError,
    BackendShutdownResult,
)
from mcps.zynq_mcp.control.single_worker import SingleWorkerController
from mcps.zynq_mcp.adapters.vivado_adapter import BridgeError


class _ProcessBridge:
    """Real Python child exercising production PID/identity/cleanup paths."""

    def __init__(self, counter=None):
        self._proc = None
        self._counter = counter

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    @property
    def ready(self):
        return self._proc is not None and self._proc.returncode is None

    async def start(self, *_args):
        if self._counter is not None:
            self._counter[0] += 1
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

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


class _SyntheticBridge:
    def __init__(self, pid, alive, counter=None, stop_clears=True):
        self._pid = pid
        self._alive = alive
        self._counter = counter
        self._stop_clears = stop_clears

    @property
    def pid(self):
        return self._pid

    @property
    def ready(self):
        return bool(self._alive.get(self._pid, False))

    async def start(self, *_args):
        if self._counter is not None:
            self._counter[0] += 1
        self._alive[self._pid] = True

    async def stop(self):
        if self._stop_clears:
            self._alive[self._pid] = False


@pytest_asyncio.fixture
async def o2_runtime():
    root = Path(tempfile.mkdtemp())
    guard = InstanceGuard(root, "ws-o2")
    guard.determine_role()
    path = root / "execution_ledger.json"

    def _init(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.execution_lane = EXECUTION_LANE_IDLE
        ledger.context = {
            "session_id": "sid-o2", "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(root / "project"), "current_stage": "PL_BUILD",
            "board_package_revision": "sha256:" + "1" * 64,
        }
        return ledger

    ledger_transaction(guard, path, _init)
    controllers = []
    yield root, guard, path, controllers

    cleanup_errors = []
    for controller in reversed(controllers):
        try:
            await controller.shutdown_backend(force=True)
        except Exception as controller_error:
            bridge = controller.bridge
            if bridge is not None:
                try:
                    await bridge.stop()
                except Exception as bridge_error:
                    cleanup_errors.append(
                        f"controller={controller_error!r}; bridge={bridge_error!r}")
            else:
                cleanup_errors.append(f"controller={controller_error!r}")
    guard.release_owner_lock()
    shutil.rmtree(root, ignore_errors=True)
    if cleanup_errors:
        raise AssertionError("O2 fixture cleanup failed: " + "; ".join(cleanup_errors))


def _controller(o2_runtime, **kwargs):
    _, guard, path, controllers = o2_runtime
    if "bridge_factories" in kwargs and "identity_resolver" not in kwargs:
        # Component bridges are real processes but intentionally Python, not
        # fake Xilinx binaries. Backend executable classification is exercised
        # separately and by O219 against the real installed tools.
        kwargs["identity_resolver"] = lambda pid, backend: (
            get_process_identity(pid), None)
    controller = ToolProcessController(guard, path, **kwargs)
    controllers.append(controller)
    return controller


def _record_running_operation(guard, path, operation_id="op-o2"):
    def _mutate(ledger):
        accepted_at = _now_iso()
        worker = ledger.worker or {}
        ledger.execution_lane = EXECUTION_LANE_BUSY
        ledger.active_operation = {
            "operation_id": operation_id, "tool_name": "pl_synthesize",
            "status": OP_RUNNING, "api_category": "command",
            "session_id": "sid-o2", "board_id": "ALINX_AX7020_v1.0",
            "project_path": ledger.context.get("project_path", ""),
            "workflow_stage": "PL_BUILD", "request_signature": "o2-sig",
            "worker_generation": worker.get("worker_generation", 0),
            "input_artifact_revision": ledger.context.get(
                "board_package_revision", ""),
            "accepted_at": accepted_at, "started_at": accepted_at,
            "heartbeat_at": None, "finished_at": None,
            "output_artifact_revision": None, "completion_evidence": None,
            "error": None, "progress_pct": None,
            **operation_contract_fields("pl_synthesize", accepted_at),
        }
        return ledger
    return ledger_transaction(guard, path, _mutate)


class TestO2Ownership:
    @pytest.mark.asyncio
    async def test_o201_two_concurrent_ensure_calls_create_one_real_pid(self, o2_runtime):
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        a, b = await asyncio.gather(
            controller.ensure_backend(BACKEND_VIVADO),
            controller.ensure_backend(BACKEND_VIVADO),
        )
        assert calls[0] == 1
        assert a.pid == b.pid and a.pid > 0
        assert is_pid_alive(a.pid)
        assert a.worker_generation == b.worker_generation == 1

    @pytest.mark.asyncio
    async def test_o202_ledger_records_actual_pid_and_five_field_identity(self, o2_runtime):
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
        })
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        worker = ledger.worker
        assert worker["backend"] == BACKEND_VIVADO
        assert worker["pid"] == snap.pid
        assert worker["supervisor_pid"] is None
        assert worker["process_start_time"] > 0
        assert worker["executable_path"]
        assert worker["worker_generation"] == 1
        assert worker["instance_id"] == guard.instance_id
        assert worker["state"] == WORKER_STATE_READY

    @pytest.mark.asyncio
    async def test_o203_process_observation_updates_active_operation_from_real_identity(self, o2_runtime):
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
        })
        await controller.ensure_backend(BACKEND_VIVADO)
        _, guard, path, _ = o2_runtime
        _record_running_operation(guard, path)

        result = await controller.observe_backend(
            operation_id="op-o2", current_step="TCL_PROCESS_ALIVE")

        assert result["status"] == "success"
        observation = result["data"]["observation"]
        assert observation["status_source"] == STATUS_SOURCE_PROCESS
        assert observation["backend"] == BACKEND_VIVADO
        assert observation["observed_state"] == OBS_RUNNING
        assert observation["worker_health"] == HEALTH_ALIVE
        assert observation["pid"] == result["data"]["pid"]
        assert observation["observed_at"]
        assert observation["progress_pct"] is None
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.active_operation["observation"] == observation
        assert ledger.active_operation["status"] == OP_RUNNING
        assert ledger.execution_lane == EXECUTION_LANE_BUSY

    @pytest.mark.asyncio
    async def test_o204_vivado_to_xsct_switch_stops_old_before_new_start(self, o2_runtime):
        events = []
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
            BACKEND_XSCT: _ProcessBridge,
        }, event_sink=lambda name, detail: events.append((name, detail)))
        first = await controller.ensure_backend(BACKEND_VIVADO)
        second = await controller.ensure_backend(BACKEND_XSCT)

        assert not is_pid_alive(first.pid)
        assert is_pid_alive(second.pid)
        assert first.pid != second.pid
        assert second.worker_generation == 2
        names = [name for name, _ in events]
        stop_at = names.index("backend_stopping")
        stopped_at = names.index("backend_stopped")
        second_start = names.index("backend_starting", stopped_at + 1)
        second_ready = names.index("backend_ready", second_start + 1)
        assert stop_at < stopped_at < second_start < second_ready

    @pytest.mark.asyncio
    async def test_o205_busy_lane_rejects_before_factory_or_process_start(self, o2_runtime):
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        _, guard, path, _ = o2_runtime
        ledger_transaction(guard, path, lambda ledger: _set_lane(ledger, EXECUTION_LANE_BUSY))

        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_SWITCH_REQUIRES_IDLE"
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_o206_cleanup_failure_blocks_new_backend(self, o2_runtime, monkeypatch):
        import mcps.zynq_mcp.control.tool_process_controller as module
        monkeypatch.setattr(module, "PID_GONE_TIMEOUT_S", 0.05)
        alive = {101: False, 202: False}
        calls = {BACKEND_VIVADO: [0], BACKEND_XSCT: [0]}
        identities = {
            101: WorkerIdentity(101, 1001.0, "C:/fake/vivado.exe"),
            202: WorkerIdentity(202, 1002.0, "C:/fake/xsct.exe"),
        }
        controller = _controller(
            o2_runtime,
            bridge_factories={
                BACKEND_VIVADO: lambda: _SyntheticBridge(
                    101, alive, calls[BACKEND_VIVADO], stop_clears=False),
                BACKEND_XSCT: lambda: _SyntheticBridge(
                    202, alive, calls[BACKEND_XSCT]),
            },
            identity_resolver=lambda pid, backend: (identities[pid], None),
            identity_reader=lambda pid: identities.get(pid),
            pid_alive=lambda pid: alive.get(pid, False),
            kill_tree=lambda pid: False,
        )
        await controller.ensure_backend(BACKEND_VIVADO)
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_XSCT)
        assert exc.value.reason_code == "BACKEND_CLEANUP_FAILED"
        assert calls[BACKEND_XSCT][0] == 0
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        alive[101] = False

    @pytest.mark.asyncio
    async def test_o207_external_crash_does_not_auto_restart(self, o2_runtime):
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        assert kill_process_tree_exact(snap.pid)
        for _ in range(50):
            if not is_pid_alive(snap.pid):
                break
            await asyncio.sleep(0.05)
        result = await controller.observe_backend()
        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == "BACKEND_PROCESS_DEAD"
        with pytest.raises(ToolProcessControllerError):
            await controller.ensure_backend(BACKEND_VIVADO)
        assert calls[0] == 1
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED

    @pytest.mark.asyncio
    async def test_o208_pid_reuse_identity_mismatch_is_fail_closed_without_kill(self, o2_runtime):
        bridge = _ProcessBridge()
        kills = []
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: bridge,
        }, kill_tree=lambda pid: kills.append(pid) or False)
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        real = get_process_identity(snap.pid)
        assert real is not None
        controller._identity_reader = lambda pid: WorkerIdentity(
            pid, real.process_start_time + 100.0, real.executable_path)

        result = await controller.observe_backend()
        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == "BACKEND_IDENTITY_MISMATCH"
        assert kills == []
        assert is_pid_alive(snap.pid)
        # Test cleanup uses the bridge handle; the controller must not claim
        # ownership of the mismatched identity.
        await bridge.stop()

    @pytest.mark.asyncio
    async def test_o209_actual_pid_and_supervisor_pid_are_distinct(self, o2_runtime):
        alive = {301: False, 302: True}
        supervisor = WorkerIdentity(301, 2001.0, "C:/Windows/System32/cmd.exe")
        actual = WorkerIdentity(302, 2002.0, "C:/Xilinx/tclsh85t.exe")
        identities = {301: supervisor, 302: actual}
        controller = _controller(
            o2_runtime,
            bridge_factories={BACKEND_XSCT: lambda: _SyntheticBridge(301, alive)},
            identity_resolver=lambda pid, backend: (actual, supervisor),
            identity_reader=lambda pid: identities.get(pid),
            pid_alive=lambda pid: alive.get(pid, False),
            kill_tree=lambda pid: alive.__setitem__(pid, False) or True,
        )
        snap = await controller.ensure_backend(BACKEND_XSCT)
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert snap.pid == 302
        assert snap.supervisor_pid == 301
        assert ledger.worker["pid"] == 302
        assert ledger.worker["supervisor_pid"] == 301
        alive[301] = False
        alive[302] = False

    @pytest.mark.asyncio
    async def test_o210_unknown_backend_rejected_before_factory(self, o2_runtime):
        controller = _controller(o2_runtime, bridge_factories={})
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend("VITIS")
        assert exc.value.reason_code == "INVALID_BACKEND"

    @pytest.mark.asyncio
    async def test_o213_shutdown_kills_only_exact_owned_pid_tree(self, o2_runtime):
        alive = {401: False, 402: True}
        supervisor = WorkerIdentity(401, 3001.0, "C:/Windows/System32/cmd.exe")
        actual = WorkerIdentity(402, 3002.0, "C:/Xilinx/tclsh85t.exe")
        identities = {401: supervisor, 402: actual}
        killed = []

        def _kill(pid):
            killed.append(pid)
            alive[pid] = False
            return True

        controller = _controller(
            o2_runtime,
            bridge_factories={
                BACKEND_XSDB: lambda: _SyntheticBridge(
                    401, alive, stop_clears=False),
            },
            identity_resolver=lambda pid, backend: (actual, supervisor),
            identity_reader=lambda pid: identities.get(pid),
            pid_alive=lambda pid: alive.get(pid, False),
            kill_tree=_kill,
        )
        await controller.ensure_backend(BACKEND_XSDB)
        result = await controller.shutdown_backend()
        assert result.success is True
        assert killed == [402, 401]
        assert all(isinstance(pid, int) for pid in killed)

    @pytest.mark.asyncio
    async def test_o214_ledger_commit_failure_cleans_started_process(self, o2_runtime, monkeypatch):
        import mcps.zynq_mcp.control.tool_process_controller as module
        bridge = _ProcessBridge()
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: bridge,
        })
        original = module.ledger_transaction

        def _fail_commit(*args, **kwargs):
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(module, "ledger_transaction", _fail_commit)
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_START_FAILED"
        assert bridge.pid is not None
        assert not is_pid_alive(bridge.pid)
        monkeypatch.setattr(module, "ledger_transaction", original)

    @pytest.mark.asyncio
    async def test_o215_unverifiable_identity_cleans_started_process(self, o2_runtime):
        bridge = _ProcessBridge()
        controller = _controller(
            o2_runtime,
            bridge_factories={BACKEND_VIVADO: lambda: bridge},
            identity_resolver=lambda pid, backend: (None, None),
        )
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_IDENTITY_UNVERIFIABLE"
        assert bridge.pid is not None
        assert not is_pid_alive(bridge.pid)

    @pytest.mark.asyncio
    async def test_o216_observe_absent_backend_is_read_only_error(self, o2_runtime):
        controller = _controller(o2_runtime, bridge_factories={})
        _, guard, path, _ = o2_runtime
        before, _ = ledger_read_shared(guard, path, guard.workspace_id)
        result = await controller.observe_backend()
        after, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == "BACKEND_NOT_ACTIVE"
        assert after.ledger_sequence == before.ledger_sequence

    @pytest.mark.asyncio
    async def test_o217_same_backend_identity_mismatch_never_restarts(self, o2_runtime):
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        actual = get_process_identity(snap.pid)
        assert actual is not None
        controller._identity_reader = lambda pid: WorkerIdentity(
            pid, actual.process_start_time + 100.0, actual.executable_path)
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_IDENTITY_MISMATCH"
        assert calls[0] == 1
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        assert ledger.worker["state"] == "POISONED"
        await controller.bridge.stop()

    @pytest.mark.asyncio
    async def test_o220_generation_tamper_enters_recovery_without_restart(self, o2_runtime):
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        await controller.ensure_backend(BACKEND_VIVADO)
        _, guard, path, _ = o2_runtime

        def _tamper(ledger):
            ledger.worker["worker_generation"] += 1
            return ledger

        ledger_transaction(guard, path, _tamper)
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_IDENTITY_MISMATCH"
        assert calls[0] == 1
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        await controller.bridge.stop()

    @pytest.mark.asyncio
    async def test_o221_direct_backend_blocks_legacy_worker_start(self, o2_runtime):
        lifecycle_lock = asyncio.Lock()
        controller = _controller(
            o2_runtime,
            bridge_factories={BACKEND_VIVADO: _ProcessBridge},
            lifecycle_lock=lifecycle_lock,
        )
        await controller.ensure_backend(BACKEND_VIVADO)
        _, guard, path, _ = o2_runtime
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        legacy = SingleWorkerController(
            ledger, guard, path, lifecycle_lock=lifecycle_lock)
        with pytest.raises(BridgeError, match="Direct EDA backend already active"):
            await legacy.ensure_worker()
        assert controller.has_backend is True

    @pytest.mark.asyncio
    async def test_o222_legacy_worker_record_blocks_direct_backend_start(self, o2_runtime):
        _, guard, path, _ = o2_runtime

        def _legacy_ready(ledger):
            ledger.worker.update({
                "backend": BACKEND_NONE,
                "state": WORKER_STATE_READY,
                "pid": 500,
                "process_start_time": 1.0,
                "executable_path": "C:/legacy/python.exe",
            })
            return ledger

        ledger_transaction(guard, path, _legacy_ready)
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "UNOWNED_WORKER_PRESENT"
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_o223_exact_admitted_operation_can_start_and_stop_backend(self, o2_runtime):
        _, guard, path, _ = o2_runtime
        _record_running_operation(guard, path, "op-authorized")
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
        })
        snapshot = await controller.ensure_backend(
            BACKEND_VIVADO, operation_id="op-authorized")
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert snapshot.pid > 0
        assert ledger.active_operation["operation_id"] == "op-authorized"
        assert ledger.active_operation["worker_generation"] == 1
        stopped = await controller.shutdown_backend(
            operation_id="op-authorized")
        assert stopped.success is True
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_BUSY
        assert ledger.active_operation["status"] == OP_RUNNING

    @pytest.mark.asyncio
    async def test_o224_wrong_operation_id_cannot_start_backend(self, o2_runtime):
        _, guard, path, _ = o2_runtime
        _record_running_operation(guard, path, "op-real")
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(
                BACKEND_VIVADO, operation_id="op-wrong")
        assert exc.value.reason_code == "BACKEND_OPERATION_MISMATCH"
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_o225_busy_without_owner_cannot_start_backend(self, o2_runtime):
        _, guard, path, _ = o2_runtime
        ledger_transaction(
            guard, path, lambda ledger: _set_lane(ledger, EXECUTION_LANE_BUSY))
        calls = [0]
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: lambda: _ProcessBridge(calls),
        })
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(BACKEND_VIVADO)
        assert exc.value.reason_code == "BACKEND_SWITCH_REQUIRES_IDLE"
        assert calls[0] == 0

    @pytest.mark.asyncio
    async def test_o226_synchronous_set_owner_can_start_and_stop(self, o2_runtime):
        _, guard, path, _ = o2_runtime
        ledger_transaction(
            guard, path, lambda ledger: _set_lane(ledger, EXECUTION_LANE_BUSY))
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
        })
        snapshot = await controller.ensure_backend(
            BACKEND_VIVADO, synchronous_owner=True)
        assert is_pid_alive(snapshot.pid)
        stopped = await controller.shutdown_backend(synchronous_owner=True)
        assert stopped.success is True

    @pytest.mark.asyncio
    async def test_o227_busy_operation_cannot_switch_backend(self, o2_runtime):
        controller = _controller(o2_runtime, bridge_factories={
            BACKEND_VIVADO: _ProcessBridge,
            BACKEND_XSCT: _ProcessBridge,
        })
        first = await controller.ensure_backend(BACKEND_VIVADO)
        _, guard, path, _ = o2_runtime
        _record_running_operation(guard, path, "op-switch")
        with pytest.raises(ToolProcessControllerError) as exc:
            await controller.ensure_backend(
                BACKEND_XSCT, operation_id="op-switch")
        assert exc.value.reason_code == "BACKEND_SWITCH_REQUIRES_IDLE"
        assert is_pid_alive(first.pid)


class TestO2ServerLifecycle:
    def test_o211_startup_reconcile_marks_live_owned_backend_orphaned(self, o2_runtime):
        from mcps.zynq_mcp.server import start_reconcile
        proc = __import__("subprocess").Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            ident = get_process_identity(proc.pid)
            assert ident is not None
            _, guard, path, _ = o2_runtime

            def _record(ledger):
                ledger.worker.update({
                    "backend": BACKEND_VIVADO, "state": WORKER_STATE_READY,
                    "pid": ident.pid,
                    "process_start_time": ident.process_start_time,
                    "executable_path": ident.executable_path,
                    "worker_generation": 4,
                    "instance_id": guard.instance_id,
                    "supervisor_pid": None,
                })
                return ledger
            ledger_transaction(guard, path, _record)

            reconciled = start_reconcile(guard, path, guard.workspace_id)
            assert reconciled.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
            assert reconciled.worker["state"] == WORKER_STATE_ORPHANED
            assert reconciled.recent_errors[-1]["reason_code"] == "BACKEND_ORPHANED"
        finally:
            proc.kill()
            proc.wait(timeout=5)

    def test_o228_reconcile_moves_live_active_operation_to_unknown_history(self, o2_runtime):
        from mcps.zynq_mcp.server import start_reconcile
        proc = __import__("subprocess").Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"])
        try:
            ident = get_process_identity(proc.pid)
            assert ident is not None
            _, guard, path, _ = o2_runtime

            def _record(ledger):
                ledger.worker.update({
                    "backend": BACKEND_VIVADO, "state": WORKER_STATE_READY,
                    "pid": ident.pid,
                    "process_start_time": ident.process_start_time,
                    "executable_path": ident.executable_path,
                    "worker_generation": 2,
                    "instance_id": guard.instance_id,
                    "supervisor_pid": None,
                })
                ledger.execution_lane = EXECUTION_LANE_BUSY
                ledger.active_operation = {
                    "operation_id": "op-orphan", "tool_name": "pl_synthesize",
                    "status": OP_RUNNING,
                }
                return ledger

            ledger_transaction(guard, path, _record)
            reconciled = start_reconcile(guard, path, guard.workspace_id)
            assert reconciled.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
            assert reconciled.active_operation is None
            assert reconciled.previous_operation["operation_id"] == "op-orphan"
            assert reconciled.previous_operation["status"] == OP_OUTCOME_UNKNOWN
            assert reconciled.previous_operation["reason_code"] == "BACKEND_ORPHANED"
        finally:
            proc.kill()
            proc.wait(timeout=5)

    @pytest.mark.asyncio
    async def test_o212_server_finalizer_stops_process_controller(self, o2_runtime):
        from mcps.zynq_mcp.server import _server_finalizer

        class _FinalizerController:
            def __init__(self):
                self.calls = []

            async def shutdown_backend(self, *, force=False):
                self.calls.append(force)
                return BackendShutdownResult(True, BACKEND_VIVADO, True, True)

        fake = _FinalizerController()
        _, guard, path, _ = o2_runtime
        diag = await _server_finalizer(
            guard, None, path, None, process_controller=fake)
        assert fake.calls == [True]
        assert diag["backend_shutdown"]["success"] is True
        assert diag["owner_lock_released"] is True

    def test_o218_temporal_process_tree_tracks_current_child(self):
        import subprocess
        if os.name == "nt":
            cmd = ["cmd.exe", "/d", "/c", sys.executable, "-c",
                   "import time; time.sleep(120)"]
        else:
            import shlex
            cmd = ["sh", "-c", f"{shlex.quote(sys.executable)} -c "
                   "'import time; time.sleep(120)'"]
        proc = subprocess.Popen(cmd)
        try:
            supervisor = get_process_identity(proc.pid)
            descendants = [get_process_identity(pid)
                           for pid in descendant_pids(proc.pid)]
            assert supervisor is not None and supervisor.pid == proc.pid
            python_children = [identity for identity in descendants
                               if identity is not None and
                               os.path.basename(identity.executable_path).lower().startswith("python")]
            assert len(python_children) == 1
            assert is_descendant_pid(python_children[0].pid, proc.pid)
        finally:
            kill_process_tree_exact(proc.pid)
            proc.wait(timeout=10)

    def test_o229_backend_classifier_and_ambiguous_matches_fail_closed(self, monkeypatch):
        import mcps.zynq_mcp.control.process_guard as module
        supervisor = WorkerIdentity(700, 100.0, "C:/Windows/System32/cmd.exe")
        first = WorkerIdentity(701, 101.0, "C:/Xilinx/rdi_xsct.exe")
        second = WorkerIdentity(702, 102.0, "C:/Xilinx/xsct.exe")
        identities = {700: supervisor, 701: first, 702: second}
        monkeypatch.setattr(module, "get_process_identity",
                            lambda pid: identities.get(pid))
        monkeypatch.setattr(module, "descendant_pids", lambda pid: [701, 702])
        actual, wrapper = resolve_backend_process_identity(
            700, BACKEND_XSCT, timeout_s=0.0)
        assert actual is None
        assert wrapper == supervisor
        assert backend_process_matches(first, BACKEND_XSCT) is True
        assert backend_process_matches(first, BACKEND_XSDB) is False


@pytest.mark.host_live
@pytest.mark.asyncio
async def test_o219_real_vivado_xsct_xsdb_sequential_backends(o2_runtime):
    """Real tools: one controller, one backend PID at a time, no JTAG connect."""
    def _assert_live_owned(snapshot):
        assert is_pid_alive(snapshot.pid)
        assert isinstance(snapshot.executable_path, str)
        assert snapshot.executable_path
        assert backend_process_matches(
            get_process_identity(snapshot.pid), snapshot.backend)
        if snapshot.supervisor_pid is not None:
            assert snapshot.pid != snapshot.supervisor_pid
            assert is_pid_alive(snapshot.supervisor_pid)
            assert is_descendant_pid(snapshot.pid, snapshot.supervisor_pid)

    controller = _controller(o2_runtime)
    vivado = await controller.ensure_backend(BACKEND_VIVADO)
    _assert_live_owned(vivado)
    assert (await controller.observe_backend())["status"] == "success"

    xsct = await controller.ensure_backend(BACKEND_XSCT)
    assert not is_pid_alive(vivado.pid)
    if vivado.supervisor_pid is not None:
        assert not is_pid_alive(vivado.supervisor_pid)
    _assert_live_owned(xsct)
    assert xsct.worker_generation == vivado.worker_generation + 1
    assert (await controller.observe_backend())["status"] == "success"

    xsdb = await controller.ensure_backend(BACKEND_XSDB)
    assert not is_pid_alive(xsct.pid)
    if xsct.supervisor_pid is not None:
        assert not is_pid_alive(xsct.supervisor_pid)
    _assert_live_owned(xsdb)
    assert xsdb.worker_generation == xsct.worker_generation + 1
    assert (await controller.observe_backend())["status"] == "success"

    stopped = await controller.shutdown_backend()
    assert stopped.success is True
    assert not is_pid_alive(xsdb.pid)
    if xsdb.supervisor_pid is not None:
        assert not is_pid_alive(xsdb.supervisor_pid)


def _set_lane(ledger, lane):
    ledger.execution_lane = lane
    return ledger
