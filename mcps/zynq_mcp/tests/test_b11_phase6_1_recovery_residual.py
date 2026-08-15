"""B11 阶段⑥.1 — crash-recover residual: recovery must clear owner/instance fields.

Agent2 终验 BLOCKED 事件链（证据 `D:\\_b11_p4_external\\agent2_20260815\\evidence\\`）：
Vivado worker 崩溃 → close_session 后端清理失败（BACKEND_SHUTDOWN_FAILED）→
``recover_execution`` 只把 state 置 ABSENT、**未清 backend/owner/instance 残留字段** →
Ledger 残留 backend="VIVADO" → tool_process_controller 门禁（_ensure_backend /
_commit_started）对所有 command 永久抛 UNOWNED_WORKER_PRESENT，recover/重启/新会话
均无法解除（recover 在 IDLE lane 是 no-op，不触碰残留字段）。

本文件复现完整失败链并锁定修复：

- 组件级：构造崩溃+恢复后的残留记录 → recover_execution → 断言 backend/identity/
  supervisor/instance_id 全部清除 → _ensure_backend / 新 command 不再抛
  UNOWNED_WORKER_PRESENT。
- 真实进程级：启动真实后端子进程 → 异常终止（真崩溃，不主动释放）→ 清理失败
  （身份无法核验，fail-closed）→ recover_execution → close_session → 新会话 →
  新 command 准入 → 新后端真实启动。
- 门禁保持：活 worker（READY + 活 PID）→ recover 仍不触碰、_ensure_backend 仍
  UNOWNED_WORKER_PRESENT；活 PID 的 RECOVERY_REQUIRED → 仍
  RECOVERY_BLOCKED_WORKER_ALIVE。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_NONE, BACKEND_VIVADO,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_ACCEPTED,
    WORKER_STATE_ABSENT, WORKER_STATE_DEAD, WORKER_STATE_POISONED,
    WORKER_STATE_READY,
    ChannelBusyError, ledger_read_shared, ledger_transaction, _now_iso,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.process_guard import (
    WorkerIdentity, get_process_identity, is_pid_alive, kill_process_tree_exact,
)
from mcps.zynq_mcp.control.recovery import recovery_mutator
from mcps.zynq_mcp.control.tool_process_controller import (
    ToolProcessController, ToolProcessControllerError,
)


class _SyntheticBridge:
    """Deterministic fake process: alive-map backed, no real OS process."""

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


class _ChildBridge:
    """Real Python child process exercising the production PID/identity paths."""

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


@pytest_asyncio.fixture
async def rtg():
    rt = Path(tempfile.mkdtemp())
    guard = InstanceGuard(rt, "ws-phase61")
    guard.determine_role()
    path = rt / "execution_ledger.json"

    def _init(ledger):
        ledger.instance_id = guard.instance_id
        ledger.workspace_id = guard.workspace_id
        ledger.primary_instance_id = guard.instance_id
        ledger.execution_lane = EXECUTION_LANE_IDLE
        ledger.context = {
            "session_id": "sid-o2", "board_id": "ALINX_AX7020_v1.0",
            "project_path": str(rt / "project"), "current_stage": "PL_BUILD",
            "board_package_revision": "sha256:" + "1" * 64,
        }
        return ledger

    ledger_transaction(guard, path, _init)
    controllers = []
    yield rt, guard, path, controllers

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
    shutil.rmtree(rt, ignore_errors=True)
    if cleanup_errors:
        raise AssertionError(
            "phase6.1 fixture cleanup failed: " + "; ".join(cleanup_errors))


def _controller(rtg, **kwargs):
    _, guard, path, controllers = rtg
    if "bridge_factories" in kwargs and "identity_resolver" not in kwargs:
        # Component bridges are real Python children, not fake Xilinx
        # binaries — the identity resolver must classify them by OS identity.
        kwargs["identity_resolver"] = lambda pid, backend: (
            get_process_identity(pid), None)
    controller = ToolProcessController(guard, path, **kwargs)
    controllers.append(controller)
    return controller


def _crash_residue(ledger, *, backend="VIVADO", state=WORKER_STATE_DEAD,
                   pid=None, generation=5, instance="instance-dead"):
    """Write the residual record a crash + failed cleanup leaves behind."""
    ledger.execution_lane = EXECUTION_LANE_RECOVERY_REQUIRED
    w = ledger.worker
    w.update({
        "backend": backend, "state": state, "pid": pid,
        "process_start_time": 123.0,
        "executable_path": "C:/fake/vivado.exe",
        "executable_args": ["-mode", "batch"],
        "worker_generation": generation,
        "instance_id": instance,
        "supervisor_pid": 999,
        "supervisor_process_start_time": 100.0,
        "supervisor_executable_path": "C:/fake/cmd.exe",
    })
    return ledger


class TestPhase61Component:
    """组件级：recover_execution 必须清掉全部 owner/instance 残留字段。"""

    def test_recover_from_recovery_required_residue_clears_owner_fields(self, rtg):
        rt, g, lp, _ = rtg
        ledger_transaction(g, lp, _crash_residue)
        l = ledger_transaction(g, lp, recovery_mutator("op-recover"))

        assert l.execution_lane == EXECUTION_LANE_IDLE
        assert l.worker["backend"] == BACKEND_NONE
        assert l.worker["state"] == WORKER_STATE_ABSENT
        assert l.worker["pid"] is None
        assert l.worker["process_start_time"] is None
        assert l.worker["executable_path"] is None
        assert l.worker["executable_args"] is None
        assert l.worker["instance_id"] is None
        assert l.worker["supervisor_pid"] is None
        assert l.worker["supervisor_process_start_time"] is None
        assert l.worker["supervisor_executable_path"] is None
        assert l.worker["last_heartbeat_at"] is None
        assert l.worker["worker_generation"] == 6
        assert l.recovery_log[-1]["result"] == "SUCCEEDED"

    def test_recover_heals_idle_lane_residue_left_by_pre_fix_recover(self, rtg):
        """Agent2 的死锁态：recover 后 lane=IDLE、state=ABSENT 但 backend 残留。
        修复前在此状态再调 recover 是 no-op（ALREADY_IDLE），门禁永久拒绝；
        修复后 recover 在无活进程时应清除残留并重新开门。"""
        rt, g, lp, _ = rtg

        def _init(ledger):
            ledger.instance_id = g.instance_id
            ledger.workspace_id = g.workspace_id
            ledger.primary_instance_id = g.instance_id
            ledger.execution_lane = EXECUTION_LANE_IDLE
            w = ledger.worker
            w.update({
                "backend": "VIVADO", "state": WORKER_STATE_ABSENT, "pid": None,
                "worker_generation": 2, "instance_id": "instance-old",
                "process_start_time": 50.0,
                "executable_path": "C:/fake/vivado.exe",
            })
            return ledger

        ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator("op-recover"))

        assert l.execution_lane == EXECUTION_LANE_IDLE
        assert l.worker["backend"] == BACKEND_NONE
        assert l.worker["state"] == WORKER_STATE_ABSENT
        assert l.worker["pid"] is None
        assert l.worker["instance_id"] is None
        assert l.recovery_log[-1]["result"] == "RESIDUE_CLEARED"

        # 门禁重新打开：同一 Ledger 上新控制器可真实启动后端
        async def _run():
            alive = {701: False}
            identities = {701: WorkerIdentity(701, 1.0, "C:/fake/vivado.exe")}
            controller = ToolProcessController(
                g, lp,
                bridge_factories={BACKEND_VIVADO: lambda: _SyntheticBridge(
                    701, alive)},
                identity_resolver=lambda pid, backend: (identities[pid], None),
                identity_reader=lambda pid: identities.get(pid),
                pid_alive=lambda pid: alive.get(pid, False))
            snap = await controller.ensure_backend(BACKEND_VIVADO)
            assert snap.pid == 701
            assert alive[701] is True
            alive[701] = False
            await controller.shutdown_backend(force=True)
        asyncio.run(_run())

    def test_recover_from_dead_record_then_ensure_backend_admits(self, rtg):
        """组件级：{state:ABSENT, backend:"VIVADO", pid:None}（崩溃+recover 后
        残留）→ recover_execution → backend 已清 → _ensure_backend 不再抛
        UNOWNED_WORKER_PRESENT。"""
        rt, g, lp, _ = rtg

        def _init(ledger):
            ledger.instance_id = g.instance_id
            ledger.workspace_id = g.workspace_id
            ledger.primary_instance_id = g.instance_id
            ledger.execution_lane = EXECUTION_LANE_IDLE
            w = ledger.worker
            w.update({
                "backend": "VIVADO", "state": WORKER_STATE_ABSENT, "pid": None,
                "worker_generation": 3, "instance_id": "instance-old",
                "process_start_time": 50.0,
                "executable_path": "C:/fake/vivado.exe",
                "supervisor_pid": 999,
            })
            return ledger

        ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator("op-recover"))
        assert l.worker["backend"] == BACKEND_NONE

        async def _run():
            alive = {702: False}
            identities = {702: WorkerIdentity(702, 1.0, "C:/fake/vivado.exe")}
            controller = ToolProcessController(
                g, lp,
                bridge_factories={BACKEND_VIVADO: lambda: _SyntheticBridge(
                    702, alive)},
                identity_resolver=lambda pid, backend: (identities[pid], None),
                identity_reader=lambda pid: identities.get(pid),
                pid_alive=lambda pid: alive.get(pid, False))
            snap = await controller.ensure_backend(BACKEND_VIVADO)
            assert snap.pid == 702
            alive[702] = False
            await controller.shutdown_backend(force=True)
        asyncio.run(_run())

    def test_recover_idle_with_live_worker_is_noop(self, rtg):
        """活 worker（READY + 活 PID）在 IDLE lane 是正常稳态：recover 必须
        完全不触碰（历史 no-op 语义保持）。"""
        rt, g, lp, _ = rtg
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            ident = get_process_identity(proc.pid)
            assert ident is not None

            def _init(ledger):
                ledger.instance_id = g.instance_id
                ledger.workspace_id = g.workspace_id
                ledger.primary_instance_id = g.instance_id
                ledger.execution_lane = EXECUTION_LANE_IDLE
                ledger.worker.update({
                    "backend": BACKEND_VIVADO, "state": WORKER_STATE_READY,
                    "pid": ident.pid,
                    "process_start_time": ident.process_start_time,
                    "executable_path": ident.executable_path,
                    "worker_generation": 1, "instance_id": g.instance_id,
                    "last_heartbeat_at": _now_iso(),
                })
                return ledger

            ledger_transaction(g, lp, _init)
            l = ledger_transaction(g, lp, recovery_mutator("op-recover"))
            assert l.execution_lane == EXECUTION_LANE_IDLE
            assert l.worker["backend"] == BACKEND_VIVADO
            assert l.worker["state"] == WORKER_STATE_READY
            assert l.worker["pid"] == ident.pid
            assert l.worker["instance_id"] == g.instance_id
            assert l.recovery_log[-1]["result"] == "ALREADY_IDLE"
        finally:
            proc.kill()
            proc.wait(timeout=5)


class TestPhase61GatePreserved:
    """门禁不削弱：活 worker / 活 PID 依旧拒绝，只有确认真实死亡的残留被恢复清除。"""

    def test_gate_still_refuses_live_worker_owned_by_other_instance(self, rtg):
        """READY + 活 PID（他实例 owner）→ recover no-op；_ensure_backend 仍
        UNOWNED_WORKER_PRESENT（活 worker 门禁语义不变）。"""
        rt, g, lp, _ = rtg

        def _init(ledger):
            ledger.instance_id = g.instance_id
            ledger.workspace_id = g.workspace_id
            ledger.primary_instance_id = g.instance_id
            ledger.execution_lane = EXECUTION_LANE_IDLE
            ledger.worker.update({
                "backend": BACKEND_NONE, "state": WORKER_STATE_READY,
                "pid": os.getpid(), "process_start_time": 1.0,
                "executable_path": sys.executable,
                "worker_generation": 1, "instance_id": "foreign-instance",
                "last_heartbeat_at": _now_iso(),
            })
            return ledger

        ledger_transaction(g, lp, _init)
        l = ledger_transaction(g, lp, recovery_mutator("op-recover"))
        # 活 PID → recover 不触碰（IDLE 分支 no-op），不能以恢复名义清掉活 worker
        assert l.worker["state"] == WORKER_STATE_READY
        assert l.worker["pid"] == os.getpid()
        assert l.recovery_log[-1]["result"] == "ALREADY_IDLE"

        async def _run():
            controller = ToolProcessController(g, lp, bridge_factories={})
            with pytest.raises(ToolProcessControllerError) as exc:
                await controller.ensure_backend(BACKEND_VIVADO)
            assert exc.value.reason_code == "UNOWNED_WORKER_PRESENT"
        asyncio.run(_run())

    def test_recovery_still_blocks_alive_pid(self, rtg):
        """RECOVERY_REQUIRED + 活 PID → recover 仍 RECOVERY_BLOCKED_WORKER_ALIVE。"""
        rt, g, lp, _ = rtg
        ledger_transaction(g, lp, lambda l: _crash_residue(
            l, pid=os.getpid(), state=WORKER_STATE_POISONED))
        with pytest.raises(ChannelBusyError) as ei:
            ledger_transaction(g, lp, recovery_mutator("op-recover"))
        assert str(ei.value) == "RECOVERY_BLOCKED_WORKER_ALIVE"
        # 门禁侧同样拒绝（lane RECOVERY_REQUIRED → BACKEND_RECOVERY_REQUIRED）
        async def _run():
            controller = ToolProcessController(g, lp, bridge_factories={})
            with pytest.raises(ToolProcessControllerError) as exc:
                await controller.ensure_backend(BACKEND_VIVADO)
            assert exc.value.reason_code == "BACKEND_RECOVERY_REQUIRED"
        asyncio.run(_run())


class TestPhase61RealProcessChain:
    """真实进程级：完整复现 Agent2 失败链并验证修复后的全链路可继续。"""

    @pytest.mark.asyncio
    async def test_crash_cleanup_failure_recover_then_new_session_command(self, rtg):
        from mcps.zynq_mcp.control.session import (
            close_session_mutator, create_session_mutator,
        )
        from mcps.zynq_mcp.control.operation_service import request_signature
        from mcps.zynq_mcp.control.execution_gate import preflight_mutator

        rt, guard, path, _ = rtg
        # 1) 启动真实后端（真实子进程）
        calls = [0]
        controller = _controller(rtg, bridge_factories={
            BACKEND_VIVADO: lambda: _ChildBridge(calls),
        })
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        pid = snap.pid
        assert is_pid_alive(pid)
        assert snap.worker_generation == 1

        # 2) 异常终止后端进程（真崩溃，不主动释放）
        assert kill_process_tree_exact(pid)
        for _ in range(50):
            if not is_pid_alive(pid):
                break
            await asyncio.sleep(0.05)
        assert not is_pid_alive(pid)

        # 3) close_session 的后端清理失败：崩溃时刻身份无法核验（PID 仍报
        #    存活/僵尸竞态而 identity 不可读）→ fail-closed
        #    BACKEND_CLEANUP_FAILED / BACKEND_IDENTITY_LOST_DURING_CLEANUP，
        #    残留记录保留 backend 字段（_persist_failure 不清理）
        original_pid_alive = controller._pid_alive
        controller._pid_alive = lambda p: p == pid
        try:
            stopped = await controller.shutdown_backend(force=True)
        finally:
            controller._pid_alive = original_pid_alive
        assert stopped.success is False
        assert stopped.reason_code in (
            "BACKEND_CLEANUP_FAILED", "BACKEND_IDENTITY_LOST_DURING_CLEANUP")
        ledger, _ = ledger_read_shared(guard, path, guard.workspace_id)
        assert ledger.execution_lane == EXECUTION_LANE_RECOVERY_REQUIRED
        assert ledger.worker["backend"] == BACKEND_VIVADO      # 残留字段
        assert ledger.worker["state"] == WORKER_STATE_POISONED

        # 4) recover_execution 是唯一公开出路：必须清掉残留并使门禁重开
        ledger = ledger_transaction(guard, path,
                                    recovery_mutator("op-recover"))
        assert ledger.execution_lane == EXECUTION_LANE_IDLE
        assert ledger.worker["backend"] == BACKEND_NONE
        assert ledger.worker["state"] == WORKER_STATE_ABSENT
        assert ledger.worker["pid"] is None
        assert ledger.worker["instance_id"] is None
        assert ledger.worker["supervisor_pid"] is None

        # 5) 全链路继续：close_session → 新会话 → 新 command 准入 → 新后端
        commit = close_session_mutator({"session_id": "sid-o2"})
        ledger = commit(guard, path)
        assert ledger.context == {}

        new_proj = rt / "project_new"
        new_proj.mkdir(exist_ok=True)
        sig = request_signature(
            "", "IDLE", "create_session",
            {"board_id": "ALINX_AX7020_v1.0", "project_path": str(new_proj)}, "")
        commit = create_session_mutator(
            {"board_id": "ALINX_AX7020_v1.0", "project_path": str(new_proj)},
            guard.instance_id, "op-new-session", sig)
        ledger = commit(guard, path)
        sid = ledger.context["session_id"]
        assert sid
        stage = ledger.context.get("current_stage", "PLATFORM_DESIGN")

        op_id = "op-new-command"
        sig2 = request_signature(
            sid, stage, "platform_create_design", {},
            ledger.context.get("board_package_revision", ""))
        ledger = ledger_transaction(guard, path, preflight_mutator(
            "platform_create_design", {}, sid, "ALINX_AX7020_v1.0",
            str(new_proj), op_id, sig2))
        assert ledger.active_operation["status"] == OP_ACCEPTED

        # 新 command 走 Agent2 当年被拒的同一门禁，必须真实启动成功
        calls2 = [0]
        controller2 = _controller(rtg, bridge_factories={
            BACKEND_VIVADO: lambda: _ChildBridge(calls2),
        })
        snap2 = await controller2.ensure_backend(BACKEND_VIVADO,
                                                 operation_id=op_id)
        assert calls2[0] == 1
        assert is_pid_alive(snap2.pid)
        assert snap2.pid != pid
        assert snap2.worker_generation == 3  # 1(旧) → recover+1 → 再+1

    @pytest.mark.asyncio
    async def test_recover_residue_then_gate_accepts_without_cleanup_failure(self, rtg):
        """崩溃残留（backend=VIVADO, state=DEAD, pid=None）在 IDLE lane 下由
        recover 直接清除，之后新控制器可真实启动 —— 覆盖进程已死但从未执行
        shutdown 的路径（服务器重启场景）。"""
        rt, guard, path, _ = rtg

        def _init(ledger):
            ledger.instance_id = guard.instance_id
            ledger.workspace_id = guard.workspace_id
            ledger.primary_instance_id = guard.instance_id
            ledger.execution_lane = EXECUTION_LANE_IDLE
            ledger.worker.update({
                "backend": BACKEND_VIVADO, "state": WORKER_STATE_DEAD,
                "pid": None, "process_start_time": None,
                "executable_path": None, "executable_args": None,
                "worker_generation": 4, "instance_id": "instance-dead",
                "supervisor_pid": None,
                "supervisor_process_start_time": None,
                "supervisor_executable_path": None,
            })
            return ledger

        ledger_transaction(guard, path, _init)
        ledger = ledger_transaction(guard, path,
                                    recovery_mutator("op-recover"))
        assert ledger.worker["backend"] == BACKEND_NONE

        calls = [0]
        controller = _controller(rtg, bridge_factories={
            BACKEND_VIVADO: lambda: _ChildBridge(calls),
        })
        snap = await controller.ensure_backend(BACKEND_VIVADO)
        assert calls[0] == 1
        assert is_pid_alive(snap.pid)
