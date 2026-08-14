"""O5: controller-owned XSDB and Ledger-backed JTAG/UART resources."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex, ResourceRequirement,
)
from mcps.zynq_mcp.control.execution_ledger import (
    BACKEND_XSDB, OP_SUCCEEDED, STATUS_SOURCE_RESOURCE,
    ledger_read_shared, ledger_transaction,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.process_guard import get_process_identity, is_pid_alive
from mcps.zynq_mcp.control.resource_registry import (
    JtagResourceRegistry, UartResourceRegistry,
)
from mcps.zynq_mcp.control.tool_process_controller import ToolProcessController
from mcps.zynq_mcp.domains.ps import uart_capture
from mcps.zynq_mcp.server import start_reconcile
from mcps.zynq_mcp.tests.test_build_manifest import _prep_ledger


BOARD = "ALINX_AX7020_v1.0"
PROFILE_SHA = "sha256:" + "a" * 64
PLATFORM_REV = "sha256:" + "b" * 64


@pytest.fixture
def rtg():
    runtime = Path(tempfile.mkdtemp())
    guard = InstanceGuard(runtime, "ws-build-manifest")
    guard.determine_role()
    yield runtime, guard
    guard.release_owner_lock()
    shutil.rmtree(runtime, ignore_errors=True)


class _ScriptedXsdb:
    def __init__(self):
        self._proc = None
        self._connected = False
        self.calls = []

    @property
    def pid(self):
        return self._proc.pid if self._proc is not None else None

    @property
    def ready(self):
        return self._proc is not None and self._proc.returncode is None

    @property
    def hw_connected(self):
        return self._connected

    def set_hw_connected(self, value):
        self._connected = bool(value)

    async def start(self, url=""):
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(120)",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        self.calls.append(tcl)
        if str(tcl).lstrip().startswith("connect"):
            self._connected = True
        if str(tcl).lstrip().startswith("disconnect"):
            self._connected = False
        return {"status": "success", "data": "OK"}

    async def stop(self):
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), 3.0)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()


class _FakeSerial:
    def __init__(self, chunks=None, fail=False):
        self.chunks = list(chunks or [])
        self.fail = fail
        self.is_open = False
        self.port = ""
        self.baudrate = 0
        self.read_calls = 0

    def open(self, port, baudrate):
        self.is_open = True
        self.port = port
        self.baudrate = baudrate

    def read(self, duration_ms):
        from mcps.zynq_mcp.adapters.uart import SerialAdapterError
        self.read_calls += 1
        if self.fail:
            raise SerialAdapterError("device removed")
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self):
        self.is_open = False


def _runtime(rtg, tmp_path, *, serial_factory=None):
    rt, guard = rtg
    project = tmp_path / "project"
    project.mkdir()
    ledger_path = _prep_ledger(
        rt, guard, str(project), stage="PS_BUILD",
        platform_revision=PLATFORM_REV,
        board_profile_sha256=PROFILE_SHA, session_id="sid-o5")
    bridge = _ScriptedXsdb()
    controller = ToolProcessController(
        guard, ledger_path,
        bridge_factories={BACKEND_XSDB: lambda: bridge},
        identity_resolver=lambda pid, backend: (get_process_identity(pid), None))
    jtag = JtagResourceRegistry(guard, ledger_path)
    uart = UartResourceRegistry(
        guard, ledger_path, serial_factory=serial_factory)
    runner = CommandRunner(
        guard, ledger_path, OperationRegistry(), DomainExecutionMutex(),
        process_controller=controller, jtag_registry=jtag,
        uart_registry=uart)
    return guard, ledger_path, bridge, controller, uart, runner


async def _terminal(guard, ledger_path, op_id, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        ledger, _ = ledger_read_shared(guard, ledger_path)
        if ledger.active_operation is None:
            assert ledger.previous_operation["operation_id"] == op_id
            return ledger
        await asyncio.sleep(0.01)
    raise AssertionError("operation did not terminate")


async def _connect_fn(bridge, url="localhost:3121"):
    result = await bridge.eval(f"connect -url tcp:{url}")
    if result["status"] == "success":
        bridge.set_hw_connected(True)
        return {"status": "success", "data": {
            "status": "connected", "already_connected": False, "url": url}}
    return result


async def _disconnect_fn(bridge):
    result = await bridge.eval("disconnect")
    if result["status"] == "success":
        bridge.set_hw_connected(False)
        return {"status": "success", "data": {
            "status": "disconnected", "already_disconnected": False}}
    return result


def test_o5_server_has_no_standalone_xsdb_constructor():
    source = (Path(__file__).parents[1] / "server.py").read_text(encoding="utf-8")
    assert "XsdbBridge()" not in source
    assert "JtagResourceRegistry" in source
    assert "UartResourceRegistry" in source


@pytest.mark.asyncio
async def test_o5_jtag_lease_real_pid_target_and_disconnect(rtg, tmp_path):
    guard, lp, bridge, controller, _uart, runner = _runtime(rtg, tmp_path)
    try:
        admitted = await runner.run_command(
            "ps_connect_hw_server", {"url": "localhost:3121"},
            "sid-o5", BOARD, str(tmp_path), executor="local",
            local_fn=_connect_fn, timeout_s=5,
            resource_req=ResourceRequirement(
                type="JTAG_ACQUIRE", lease_key="localhost:3121"))
        final = await _terminal(guard, lp, admitted["data"]["operation_id"])
        lease = final.worker["jtag_lease"]
        assert final.previous_operation["status"] == OP_SUCCEEDED
        assert final.previous_operation["observation"]["status_source"] == \
            STATUS_SOURCE_RESOURCE
        assert final.previous_operation["observation"]["current_step"] == \
            "JTAG_CONNECT"
        assert final.worker["backend"] == BACKEND_XSDB
        assert is_pid_alive(final.worker["pid"])
        assert lease["status"] == "CONNECTED"
        assert lease["owner_session_id"] == "sid-o5"
        assert lease["worker_generation"] == final.worker["worker_generation"]
        assert lease["instance_id"] == guard.instance_id

        async def _select(_bridge, target_id):
            return {"status": "success", "data": {"selected": {
                "id": target_id, "name": "ARM Cortex-A9 #0", "type": "ARM"}}}

        selected = await runner.run_command(
            "ps_select_target", {"target_id": 3}, "sid-o5", BOARD,
            str(tmp_path), executor="local", local_fn=_select, timeout_s=5,
            resource_req=ResourceRequirement(type="JTAG_REQUIRE_OWNED"))
        final = await _terminal(guard, lp, selected["data"]["operation_id"])
        assert final.worker["jtag_lease"]["target_id"] == 3
        assert final.worker["jtag_lease"]["target_name"] == "ARM Cortex-A9 #0"

        pid = final.worker["pid"]
        disconnected = await runner.run_command(
            "ps_disconnect_hw_server", {}, "sid-o5", BOARD, str(tmp_path),
            executor="local", local_fn=_disconnect_fn, timeout_s=5,
            resource_req=ResourceRequirement(type="JTAG_REQUIRE_OWNED"))
        final = await _terminal(
            guard, lp, disconnected["data"]["operation_id"])
        assert final.worker["jtag_lease_held"] is False
        assert final.worker["jtag_lease"]["status"] == "DISCONNECTED"
        assert final.worker["pid"] is None
        assert not is_pid_alive(pid)
    finally:
        await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o5_foreign_and_stale_jtag_lease_fail_before_executor(rtg, tmp_path):
    guard, lp, _bridge, controller, _uart, runner = _runtime(rtg, tmp_path)
    calls = 0

    async def _never(_bridge):
        nonlocal calls
        calls += 1
        return {"status": "success", "data": {}}

    def _foreign(ledger):
        ledger.worker["jtag_lease_held"] = True
        ledger.worker["jtag_lease"] = {
            "lease_id": "j-foreign", "owner_session_id": "other",
            "status": "CONNECTED", "connected": True,
            "heartbeat_at": __import__(
                "mcps.zynq_mcp.control.execution_ledger", fromlist=["_now_iso"])._now_iso(),
            "ttl_s": 600, "worker_generation": 0,
            "instance_id": guard.instance_id}
        return ledger
    ledger_transaction(guard, lp, _foreign)
    rejected = await runner.run_command(
        "ps_list_targets", {}, "sid-o5", BOARD, str(tmp_path),
        executor="local", local_fn=_never,
        resource_req=ResourceRequirement(type="JTAG_REQUIRE_OWNED"))
    assert rejected["error"]["details"]["reason_code"] == "JTAG_OWNER_MISMATCH"
    assert calls == 0

    def _stale(ledger):
        ledger.worker["jtag_lease"]["owner_session_id"] = "sid-o5"
        ledger.worker["jtag_lease"]["worker_generation"] = 99
        return ledger
    ledger_transaction(guard, lp, _stale)
    rejected = await runner.run_command(
        "ps_list_targets", {"probe": 2}, "sid-o5", BOARD, str(tmp_path),
        executor="local", local_fn=_never,
        resource_req=ResourceRequirement(type="JTAG_REQUIRE_OWNED"))
    assert rejected["error"]["details"]["reason_code"] == "JTAG_LEASE_STALE"
    assert calls == 0
    await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o5_uart_capture_ledger_marker_and_stop(rtg, tmp_path):
    serial = _FakeSerial([b"boot ", b"LED_E2E_PASS\n"])
    guard, lp, _bridge, controller, _uart, runner = _runtime(
        rtg, tmp_path, serial_factory=lambda: serial)
    start = await runner.run_command(
        "ps_start_uart_capture", {"port": "COM4", "baudrate": 115200},
        "sid-o5", BOARD, str(tmp_path), executor="local",
        local_fn=uart_capture.start_uart_capture,
        resource_req=ResourceRequirement(type="UART_ACQUIRE"), timeout_s=5)
    first = await _terminal(guard, lp, start["data"]["operation_id"])
    capture_id = first.previous_operation["result"]["data"]["capture_id"]
    assert first.worker["serial_owner"] == {
        "session_id": "sid-o5", "capture_id": capture_id, "port": "COM4"}
    assert first.worker["uart_capture"]["status"] == "RUNNING"

    wait = await runner.run_command(
        "ps_wait_uart_capture", {"capture_id": capture_id,
                                 "markers": ["LED_E2E_PASS"], "timeout_s": 2},
        "sid-o5", BOARD, str(tmp_path), executor="local",
        local_fn=uart_capture.wait_uart_capture,
        resource_req=ResourceRequirement(
            type="UART_REQUIRE_OWNED", lease_key=capture_id), timeout_s=3)
    matched = await _terminal(guard, lp, wait["data"]["operation_id"])
    record = matched.worker["uart_capture"]
    observation = matched.previous_operation["observation"]
    assert record["status"] == "MATCHED"
    assert record["bytes_received"] >= len(b"LED_E2E_PASS")
    assert record["last_rx_at"]
    assert record["markers_found"] == ["LED_E2E_PASS"]
    assert observation["status_source"] == STATUS_SOURCE_RESOURCE
    assert observation["backend"] == "UART"
    assert observation["current_step"] == "UART_MARKER_MATCH"
    assert observation["progress_pct"] is None

    stop = await runner.run_command(
        "ps_stop_uart_capture", {"capture_id": capture_id}, "sid-o5", BOARD,
        str(tmp_path), executor="local", local_fn=uart_capture.stop_uart_capture,
        resource_req=ResourceRequirement(
            type="UART_REQUIRE_OWNED", lease_key=capture_id), timeout_s=5)
    stopped = await _terminal(guard, lp, stop["data"]["operation_id"])
    assert stopped.worker["serial_owner"] is None
    assert stopped.worker["uart_capture"]["status"] == "STOPPED"
    assert serial.is_open is False
    await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o5_second_uart_capture_same_port_rejected_before_open(rtg, tmp_path):
    created = []
    def _factory():
        serial = _FakeSerial()
        created.append(serial)
        return serial
    guard, lp, _bridge, controller, _uart, runner = _runtime(
        rtg, tmp_path, serial_factory=_factory)
    start = await runner.run_command(
        "ps_start_uart_capture", {"port": "COM4"}, "sid-o5", BOARD,
        str(tmp_path), executor="local", local_fn=uart_capture.start_uart_capture,
        resource_req=ResourceRequirement(type="UART_ACQUIRE"), timeout_s=5)
    first = await _terminal(guard, lp, start["data"]["operation_id"])
    capture_id = first.worker["serial_owner"]["capture_id"]
    second = await runner.run_command(
        "ps_start_uart_capture", {"port": "COM4", "baudrate": 57600},
        "sid-o5", BOARD, str(tmp_path), executor="local",
        local_fn=uart_capture.start_uart_capture,
        resource_req=ResourceRequirement(type="UART_ACQUIRE"), timeout_s=5)
    assert second["error"]["details"]["reason_code"] == "UART_ALREADY_HELD"
    assert len(created) == 1
    stop = await runner.run_command(
        "ps_stop_uart_capture", {"capture_id": capture_id}, "sid-o5", BOARD,
        str(tmp_path), executor="local", local_fn=uart_capture.stop_uart_capture,
        resource_req=ResourceRequirement(
            type="UART_REQUIRE_OWNED", lease_key=capture_id), timeout_s=5)
    await _terminal(guard, lp, stop["data"]["operation_id"])
    await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o5_uart_disconnect_is_persistent_machine_terminal(rtg, tmp_path):
    serial = _FakeSerial(fail=True)
    guard, lp, _bridge, controller, _uart, runner = _runtime(
        rtg, tmp_path, serial_factory=lambda: serial)
    start = await runner.run_command(
        "ps_start_uart_capture", {"port": "COM4"}, "sid-o5", BOARD,
        str(tmp_path), executor="local", local_fn=uart_capture.start_uart_capture,
        resource_req=ResourceRequirement(type="UART_ACQUIRE"), timeout_s=5)
    first = await _terminal(guard, lp, start["data"]["operation_id"])
    capture_id = first.previous_operation["result"]["data"]["capture_id"]
    deadline = asyncio.get_running_loop().time() + 2
    while asyncio.get_running_loop().time() < deadline:
        current, _ = ledger_read_shared(guard, lp)
        if (current.worker.get("uart_capture") or {}).get("status") == "DISCONNECTED":
            break
        await asyncio.sleep(0.01)
    current, _ = ledger_read_shared(guard, lp)
    assert current.worker["uart_capture"]["status"] == "DISCONNECTED"
    assert current.worker["uart_capture"]["reason_code"] == "UART_DISCONNECTED"
    assert current.worker["serial_owner"] is None
    wait = await runner.run_command(
        "ps_wait_uart_capture", {"capture_id": capture_id, "markers": ["PASS"]},
        "sid-o5", BOARD, str(tmp_path), executor="local",
        local_fn=uart_capture.wait_uart_capture,
        resource_req=ResourceRequirement(
            type="UART_REQUIRE_OWNED", lease_key=capture_id), timeout_s=2)
    assert wait["error"]["details"]["reason_code"] in {
        "UART_CAPTURE_MISSING", "UART_DISCONNECTED"}
    await controller.shutdown_backend(force=True)


@pytest.mark.asyncio
async def test_o5_one_shot_uart_read_records_resource_observation(rtg, tmp_path):
    guard, lp, _bridge, controller, _uart, runner = _runtime(rtg, tmp_path)

    async def _read(_bridge, port, baudrate, duration_ms):
        assert _bridge is None
        return {"status": "success", "data": {
            "port": port, "text": "READY\n", "bytes_read": 6}}

    admitted = await runner.run_command(
        "ps_read_uart", {"port": "COM4", "baudrate": 115200,
                         "duration_ms": 100},
        "sid-o5", BOARD, str(tmp_path), executor="local", local_fn=_read,
        resource_req=ResourceRequirement(type="UART_ACQUIRE"), timeout_s=2)
    final = await _terminal(guard, lp, admitted["data"]["operation_id"])
    observation = final.previous_operation["observation"]
    assert final.previous_operation["status"] == OP_SUCCEEDED
    assert observation["status_source"] == STATUS_SOURCE_RESOURCE
    assert observation["backend"] == "UART"
    assert observation["current_step"] == "UART_READ"
    assert observation["detail"]["bytes_read"] == 6
    assert observation["detail"]["port"] == "COM4"
    await controller.shutdown_backend(force=True)


def test_o5_restart_invalidates_jtag_and_uart_records(rtg, tmp_path):
    rt, guard = rtg
    project = tmp_path / "project"
    project.mkdir()
    lp = _prep_ledger(
        rt, guard, str(project), stage="PS_BUILD",
        platform_revision=PLATFORM_REV,
        board_profile_sha256=PROFILE_SHA, session_id="sid-o5")
    def _resources(ledger):
        ledger.worker["jtag_lease_held"] = True
        ledger.worker["jtag_lease"] = {
            "lease_id": "j1", "owner_session_id": "sid-o5",
            "connected": True, "status": "CONNECTED"}
        ledger.worker["serial_owner"] = {
            "session_id": "sid-o5", "capture_id": "uart-old", "port": "COM4"}
        ledger.worker["uart_capture"] = {
            "capture_id": "uart-old", "session_id": "sid-o5",
            "port": "COM4", "baudrate": 115200, "status": "RUNNING",
            "started_at": "2026-08-12T00:00:00.000000Z",
            "last_rx_at": None, "bytes_received": 0, "markers_found": [],
            "deadline_at": None, "finished_at": None}
        return ledger
    ledger_transaction(guard, lp, _resources)
    recovered = start_reconcile(guard, lp, "ws-build-manifest")
    assert recovered.worker["jtag_lease_held"] is False
    assert recovered.worker["jtag_lease"]["status"] == "ORPHANED"
    assert recovered.worker["jtag_lease"]["connected"] is False
    assert recovered.worker["serial_owner"] is None
    assert recovered.worker["uart_capture"]["status"] == "INTERRUPTED"
    assert recovered.worker["uart_capture"]["reason_code"] == "MCP_RESTART"
