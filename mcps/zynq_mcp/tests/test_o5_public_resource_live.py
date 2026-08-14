"""O5 public MCP resource-observation gates.

These tests never instantiate a bridge, write a Ledger, or open a serial port
directly.  Every side effect and every observation crosses the MCP SDK public
surface of the unified ``zynq_mcp`` server.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from mcps.zynq_mcp.tests.test_b06_ps_public import (
    _await_op, _create_session, _ps_call, _sdk_call, _server_params,
)


pytestmark = pytest.mark.asyncio(loop_scope="function")

GPIO_PROJECT = Path("D:/fpgaproject/workspaces/gpio_b09_r3_20260812")
BITSTREAM = GPIO_PROJECT / "bitstream" / "gpio_b09.bit"
XSA = GPIO_PROJECT / "platform.xsa"
PS7_INIT = GPIO_PROJECT / "ps7_init.tcl"
ELF = GPIO_PROJECT / "gpio_app" / "Debug" / "gpio_app.elf"


async def _command(session, name, session_id, arguments=None, timeout_s=120):
    args = {"session_id": session_id, **(arguments or {})}
    admission = await _sdk_call(session, name, args)
    assert admission["status"] == "success", (name, admission)
    assert admission["data"]["status"] in ("accepted", "deduplicated")
    terminal = await _await_op(
        session, admission["data"]["operation_id"], timeout_s=timeout_s)
    assert terminal["status"] == "SUCCEEDED", (name, terminal)
    return terminal


def _arm_core_zero(targets):
    matches = [item for item in targets
               if "ARM Cortex-A9" in str(item.get("name", ""))
               and "#0" in str(item.get("name", ""))]
    assert len(matches) == 1, targets
    return matches[0]


@pytest.mark.host_live
async def test_o5_public_jtag_resource_truth(tmp_runtime_root, tmp_path):
    """Real XSDB PID, JTAG lease/target, and disconnect are publicly visible."""
    params = _server_params(tmp_runtime_root)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            sid = await _create_session(session, tmp_path)

            connected = await _command(
                session, "ps_connect_hw_server", sid,
                {"url": "localhost:3121"})
            obs = connected["data"]["observation"]
            assert obs["status_source"] == "RESOURCE"
            assert obs["backend"] == "XSDB"
            assert obs["current_step"] == "JTAG_CONNECT"
            assert isinstance(obs["pid"], int) and obs["pid"] > 0

            state = await _sdk_call(session, "get_execution_state")
            jtag = state["data"]["resources"]["jtag"]
            assert jtag["held"] is True
            assert jtag["connected"] is True
            assert jtag["status"] == "CONNECTED"
            assert jtag["owner_session_id"] == sid
            assert jtag["worker_pid"] == obs["pid"]
            assert jtag["lease"]["worker_generation"] == jtag["worker_generation"]
            assert jtag["lease"]["instance_id"] == jtag["worker_instance_id"]

            listed = await _command(session, "ps_list_targets", sid)
            arm = _arm_core_zero(listed["payload"]["targets"])
            selected = await _command(
                session, "ps_select_target", sid,
                {"target_id": arm["id"]})
            assert selected["data"]["current_step"] == "JTAG_SELECT_TARGET"

            state = await _sdk_call(session, "get_execution_state")
            lease = state["data"]["resources"]["jtag"]["lease"]
            assert lease["target_id"] == arm["id"]
            assert "ARM Cortex-A9" in lease["target_name"]
            assert "#0" in lease["target_name"]

            disconnected = await _command(
                session, "ps_disconnect_hw_server", sid)
            assert disconnected["data"]["current_step"] == "JTAG_DISCONNECT"
            state = await _sdk_call(session, "get_execution_state")
            jtag = state["data"]["resources"]["jtag"]
            assert jtag["held"] is False
            assert jtag["connected"] is False
            assert jtag["status"] == "DISCONNECTED"
            assert state["data"]["worker_pid"] is None


@pytest.mark.device_live
async def test_o5_public_gpio_uart_marker_resource_truth(tmp_runtime_root):
    """Public MCP-only deployment yields GPIO_E2E_PASS and real UART truth."""
    for artifact in (BITSTREAM, XSA, PS7_INIT, ELF):
        assert artifact.is_file(), artifact

    params = _server_params(tmp_runtime_root)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            sid = await _create_session(session, GPIO_PROJECT)
            capture_id = None
            connected = False
            try:
                await _command(session, "ps_connect_hw_server", sid,
                               {"url": "localhost:3121"})
                connected = True

                ports = await _command(session, "ps_list_serial_ports", sid)
                com4 = [item for item in ports["payload"]["ports"]
                        if item.get("port") == "COM4"]
                assert len(com4) == 1, ports

                started = await _command(
                    session, "ps_start_uart_capture", sid,
                    {"port": "COM4", "baudrate": 115200})
                capture_id = started["payload"]["capture_id"]
                state = await _sdk_call(session, "get_execution_state")
                uart = state["data"]["resources"]["uart"]
                assert uart["active"] is True
                assert uart["capture"]["status"] == "RUNNING"
                assert uart["capture"]["capture_id"] == capture_id
                assert uart["serial_owner"]["session_id"] == sid

                targets = await _command(session, "ps_list_targets", sid)
                arm = _arm_core_zero(targets["payload"]["targets"])
                await _command(session, "ps_select_target", sid,
                               {"target_id": arm["id"]})
                await _command(session, "ps_halt_target", sid)
                await _command(session, "ps_reset_target", sid,
                               {"scope": "system"})
                await _command(session, "ps_initialize_ps", sid,
                               {"tcl_path": str(PS7_INIT)})
                await _command(session, "pl_program_fpga", sid,
                               {"bitstream_path": str(BITSTREAM)})
                await _command(session, "ps_load_hardware", sid,
                               {"xsa_path": str(XSA)})
                await _command(session, "ps_download_elf", sid,
                               {"elf_path": str(ELF)})
                await _command(session, "ps_run_target", sid)

                waited = await _command(
                    session, "ps_wait_uart_capture", sid,
                    {"capture_id": capture_id,
                     "markers": ["GPIO_E2E_PASS"], "timeout_s": 30.0},
                    timeout_s=45)
                assert waited["payload"]["status"] == "matched", waited
                assert waited["payload"]["matched"] == ["GPIO_E2E_PASS"]
                assert waited["payload"]["bytes_received"] > 0
                assert waited["payload"]["last_rx_at"]
                assert "GPIO_E2E_FAIL" not in waited["payload"]["partial_text"]
                observation = waited["data"]["observation"]
                assert observation["status_source"] == "RESOURCE"
                assert observation["backend"] == "UART"
                assert observation["current_step"] == "UART_MARKER_MATCH"
                assert observation["detail"]["markers_found"] == ["GPIO_E2E_PASS"]

                stopped = await _command(
                    session, "ps_stop_uart_capture", sid,
                    {"capture_id": capture_id})
                capture_id = None
                assert "GPIO_E2E_PASS" in stopped["payload"]["text"]
                assert "GPIO_E2E_FAIL" not in stopped["payload"]["text"]
                state = await _sdk_call(session, "get_execution_state")
                uart = state["data"]["resources"]["uart"]
                assert uart["active"] is False
                assert uart["capture"]["status"] == "STOPPED"
                assert uart["capture"]["bytes_received"] > 0
                assert uart["capture"]["last_rx_at"]
                assert uart["capture"]["markers_found"] == ["GPIO_E2E_PASS"]
            finally:
                if capture_id is not None:
                    stopped = await _command(
                        session, "ps_stop_uart_capture", sid,
                        {"capture_id": capture_id})
                    assert stopped["status"] == "SUCCEEDED"
                if connected:
                    disconnected = await _command(
                        session, "ps_disconnect_hw_server", sid)
                    assert disconnected["status"] == "SUCCEEDED"
