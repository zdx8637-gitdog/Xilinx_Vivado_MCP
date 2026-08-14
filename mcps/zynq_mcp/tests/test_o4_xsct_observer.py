"""O4: controlled XSCT, real process observations, ELF/Manifest terminal gate."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
)
from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_XSCT, STATUS_SOURCE_PROCESS, OBS_RUNNING,
    OP_SUCCEEDED, OP_FAILED, OP_TIMED_OUT,
    ledger_read_shared,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.process_guard import (
    get_process_identity, is_pid_alive,
)
from mcps.zynq_mcp.control.tool_process_controller import ToolProcessController
from mcps.zynq_mcp.domains.ps import ps_bsp
from mcps.zynq_mcp.tests.test_build_manifest import (
    _ps_project, _prep_ledger,
)


@pytest.fixture
def rtg():
    runtime = Path(tempfile.mkdtemp())
    guard = InstanceGuard(runtime, "ws-build-manifest")
    guard.determine_role()
    yield runtime, guard
    guard.release_owner_lock()
    shutil.rmtree(runtime, ignore_errors=True)


class _ScriptedXsctBridge:
    def __init__(self, *, app_dir: Path, block_step: str | None = None,
                 create_elf_on_make: bool = False):
        self.workspace = ""
        self._proc = None
        self.app_dir = app_dir
        self.block_step = block_step
        self.create_elf_on_make = create_elf_on_make
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = []

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    @property
    def ready(self):
        return self._proc is not None and self._proc.returncode is None

    async def start(self, workspace=""):
        self.workspace = str(Path(workspace))
        # Keep a genuine owned OS process alive; Tcl behavior is scripted.
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        self.calls.append(tcl)
        step = "MAKE_FALLBACK" if "exec" in tcl else "APP_BUILD"
        if self.block_step == step:
            self.entered.set()
            await self.release.wait()
        if step == "MAKE_FALLBACK" and self.create_elf_on_make:
            _write_arm_elf(self.app_dir / "Debug" / "app.elf")
        return {"status": "success", "data": "OK"}

    async def stop(self):
        if self._proc is None:
            return
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()


def _write_arm_elf(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(52)
    header[:4] = b"\x7fELF"
    header[4] = 1
    header[5] = 1
    header[16:18] = (2).to_bytes(2, "little")
    header[18:20] = (40).to_bytes(2, "little")
    path.write_bytes(bytes(header))


def _controller(guard, ledger_path, bridge):
    return ToolProcessController(
        guard, ledger_path,
        bridge_factories={BACKEND_XSCT: lambda: bridge},
        identity_resolver=lambda pid, backend: (get_process_identity(pid), None),
    )


async def _wait_terminal(guard, ledger_path, operation_id, timeout_s=5.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        ledger, _ = ledger_read_shared(guard, ledger_path)
        if ledger.active_operation is None:
            return ledger
        await asyncio.sleep(0.01)
    raise AssertionError(f"operation {operation_id} did not terminate")


def test_o4_server_does_not_construct_standalone_xsct_bridge():
    source = (Path(__file__).parents[1] / "server.py").read_text(
        encoding="utf-8")
    assert "XsctBridge()" not in source
    assert "process_controller=process_controller" in source


@pytest.mark.asyncio
async def test_o4_app_build_process_observation_and_terminal_integrity(rtg, tmp_path):
    root, snapshot, _ = _ps_project(tmp_path)
    rt, guard = rtg
    ledger_path = _prep_ledger(
        rt, guard, root, stage="PS_BUILD",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"])
    bridge = _ScriptedXsctBridge(
        app_dir=Path(root) / "app", block_step="APP_BUILD")
    controller = _controller(guard, ledger_path, bridge)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "ps_compile", {"app_name": "app"}, snapshot["session_id"],
            "ALINX_AX7020_v1.0", root, executor="local",
            local_fn=ps_bsp.compile_app, timeout_s=5)
        await asyncio.wait_for(bridge.entered.wait(), 3.0)
        active, _ = ledger_read_shared(guard, ledger_path)
        observation = active.active_operation["observation"]
        assert observation["status_source"] == STATUS_SOURCE_PROCESS
        assert observation["backend"] == BACKEND_XSCT
        assert observation["observed_state"] == OBS_RUNNING
        assert observation["current_step"] == "APP_BUILD"
        assert is_pid_alive(observation["pid"])
        bridge.release.set()
        final = await _wait_terminal(
            guard, ledger_path, accepted["data"]["operation_id"])
        previous = final.previous_operation
        assert previous["status"] == OP_SUCCEEDED
        assert previous["artifact_state"] == "PUBLISHED"
        assert previous["observation"]["current_step"] == "MANIFEST_PUBLISH"
        assert previous["completion_evidence"]["elf_machine"] == 40
        assert Path(previous["completion_evidence"]["manifest_path"]).is_file()
        assert controller.has_backend is False
        assert not is_pid_alive(bridge.pid)
    finally:
        bridge.release.set()
        await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o4_internal_make_fallback_is_observed(rtg, tmp_path, monkeypatch):
    root, snapshot, files = _ps_project(tmp_path)
    Path(files["elf"]).unlink()
    rt, guard = rtg
    ledger_path = _prep_ledger(
        rt, guard, root, stage="PS_BUILD",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"])
    monkeypatch.setattr(ps_bsp, "_find_make", lambda: "D:/tools/make.exe")
    bridge = _ScriptedXsctBridge(
        app_dir=Path(root) / "app", block_step="MAKE_FALLBACK",
        create_elf_on_make=True)
    controller = _controller(guard, ledger_path, bridge)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "ps_compile", {"app_name": "app"}, snapshot["session_id"],
            "ALINX_AX7020_v1.0", root, executor="local",
            local_fn=ps_bsp.compile_app, timeout_s=5)
        await asyncio.wait_for(bridge.entered.wait(), 3.0)
        active, _ = ledger_read_shared(guard, ledger_path)
        assert active.active_operation["observation"]["current_step"] == \
            "MAKE_FALLBACK"
        bridge.release.set()
        final = await _wait_terminal(
            guard, ledger_path, accepted["data"]["operation_id"])
        assert final.previous_operation["status"] == OP_SUCCEEDED
        assert final.previous_operation["result"]["data"]["build_method"] == \
            "MAKE_FALLBACK"
    finally:
        bridge.release.set()
        await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o4_invalid_elf_blocks_success(rtg, tmp_path):
    root, snapshot, files = _ps_project(tmp_path)
    Path(files["elf"]).write_bytes(b"not an ELF")
    rt, guard = rtg
    ledger_path = _prep_ledger(
        rt, guard, root, stage="PS_BUILD",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"])
    bridge = _ScriptedXsctBridge(app_dir=Path(root) / "app")
    controller = _controller(guard, ledger_path, bridge)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "ps_compile", {"app_name": "app"}, snapshot["session_id"],
            "ALINX_AX7020_v1.0", root, executor="local",
            local_fn=ps_bsp.compile_app, timeout_s=5)
        final = await _wait_terminal(
            guard, ledger_path, accepted["data"]["operation_id"])
        assert final.previous_operation["status"] == OP_FAILED
        assert final.previous_operation["reason_code"] == "ELF_VERIFY_FAILED"
        assert final.previous_operation["artifact_state"] == "FAILED"
    finally:
        await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o4_missing_platform_manifest_blocks_ps_success(rtg, tmp_path):
    root, snapshot, _ = _ps_project(tmp_path)
    shutil.rmtree(Path(root) / "manifests" / "platform")
    rt, guard = rtg
    ledger_path = _prep_ledger(
        rt, guard, root, stage="PS_BUILD",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"])
    bridge = _ScriptedXsctBridge(app_dir=Path(root) / "app")
    controller = _controller(guard, ledger_path, bridge)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    try:
        accepted = await runner.run_command(
            "ps_compile", {"app_name": "app"}, snapshot["session_id"],
            "ALINX_AX7020_v1.0", root, executor="local",
            local_fn=ps_bsp.compile_app, timeout_s=5)
        final = await _wait_terminal(
            guard, ledger_path, accepted["data"]["operation_id"])
        assert final.previous_operation["status"] == OP_FAILED
        assert final.previous_operation["reason_code"] == \
            "MANIFEST_PUBLISH_FAILED"
        assert final.previous_operation["artifact_state"] == "FAILED"
        assert final.context["current_stage"] == "PS_BUILD"
    finally:
        await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o4_timeout_cleans_owned_xsct_and_records_timed_out(rtg, tmp_path):
    root, snapshot, _ = _ps_project(tmp_path)
    rt, guard = rtg
    ledger_path = _prep_ledger(
        rt, guard, root, stage="PS_BUILD",
        platform_revision=snapshot["platform_revision"],
        board_profile_sha256=snapshot["board_profile_sha256"],
        session_id=snapshot["session_id"])
    bridge = _ScriptedXsctBridge(
        app_dir=Path(root) / "app", block_step="APP_BUILD")
    controller = _controller(guard, ledger_path, bridge)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller)
    accepted = await runner.run_command(
        "ps_compile", {"app_name": "app"}, snapshot["session_id"],
        "ALINX_AX7020_v1.0", root, executor="local",
        local_fn=ps_bsp.compile_app, timeout_s=0.05)
    final = await _wait_terminal(
        guard, ledger_path, accepted["data"]["operation_id"])
    assert final.previous_operation["status"] == OP_TIMED_OUT
    assert controller.has_backend is False
    assert not is_pid_alive(bridge.pid)
