"""
test_platform_atoms.py — B05-R2 + B11 ③.1 Platform atomic APIs (unit, no EDA).

Exercises the 17 production atom functions in
mcps/zynq_mcp/domains/platform/platform_atoms.py against a fake adapter that
records run_tcl calls. Verifies, per API:
  - the adapter is called with the correct Tcl command;
  - the returned envelope format (success data / error contract);
  - fail-closed error paths (adapter failure, invalid args, validation).

Also verifies registration/routing consistency (every atom is in
capabilities.ALL_TOOLS and dispatcher._ALL_KNOWN) and that the CommandRunner
injects the VivadoAdapter through the _pl_adapter marker without advancing
the workflow stage.

B11 phase ③.1 additions covered here: platform_assign_addresses /
platform_make_external / platform_synthesize (D1/D2/D3), D5 segment
resolution, D6 Tcl-error classification (TCL_ERROR vs ADAPTER_NOT_READY),
D7 validate -force cache invalidation, D0 EMIO GPIO config keys, D8 puts
capture contract, D9 full-path clock_tree.
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from mcps.common.tool_response import success
from mcps.common.revision import sha256_file as _sha256_file
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, OP_SUCCEEDED, OP_FAILED,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
)
from mcps.zynq_mcp.domains.platform.platform_domain import (
    PlatformError, TclError,
)
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    PLATFORM_ATOM_MAP, PLATFORM_ATOM_TOOL_NAMES,
    PLATFORM_ATOM_COMMAND_TOOL_NAMES, PLATFORM_ATOM_QUERY_TOOL_NAMES,
    platform_create_design, platform_get_status,
    platform_add_ps7, platform_configure_ps7,
    platform_add_ip, platform_list_ips,
    platform_connect_interface, platform_connect_clock, platform_connect_reset,
    platform_set_address, platform_assign_addresses, platform_make_external,
    platform_validate, platform_generate_wrapper, platform_synthesize,
    platform_export_hardware, platform_export_manifest,
)

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])  # D:/fpgaproject
BOARD = "ALINX_AX7020_v1.0"
_SHA = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"


class _FakeAdapter:
    """Records (name, args, timeout) per call_tool; returns scripted outputs.

    ``_outputs`` is a list of {"output": str} dicts returned in order; the
    last entry repeats. Responses satisfy the _run_tcl contract: a dict with
    status == "success" and output under data.output.
    """

    def __init__(self, outputs=None):
        self.calls = []
        self._outputs = outputs if outputs is not None else [{"output": ""}]
        self._i = 0

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        self.calls.append((name, dict(arguments), timeout))
        out = self._outputs[min(self._i, len(self._outputs) - 1)]
        self._i += 1
        return {"status": "success", "data": out}


class _RaisingAdapter:
    """call_tool raises — exercises the _run_tcl AdapterError path."""

    def __init__(self, exc):
        self._exc = exc

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        raise self._exc


class _ErrorAdapter:
    """call_tool returns a status=error response — exercises the _run_tcl
    error-classification path (D6: TclError vs AdapterError)."""

    def __init__(self, message, reason_code=None):
        self._message = message
        self._reason_code = reason_code

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        details = {"reason_code": self._reason_code} if self._reason_code else {}
        return {"status": "error", "error": {
            "code": "XSDM_EVAL_ERROR", "message": self._message,
            "details": details}}


def _last_tcl(adapter) -> str:
    return adapter.calls[-1][1]["command"]


# ═══════════════════════════════════════════════════════════════════
#  1. Design lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestCreateDesign:
    @pytest.mark.asyncio
    async def test_sends_create_project_and_returns_dir(self):
        adapter = _FakeAdapter()
        out = await platform_create_design(
            adapter, name="platform", part="xc7z020clg400-2", project_path="D:/proj")
        assert out["status"] == "success"
        assert out["data"] == {"name": "platform", "part": "xc7z020clg400-2",
                               "project_dir": "D:/proj/vivado/platform"}
        assert adapter.calls[-1][0] == "run_tcl"
        tcl = _last_tcl(adapter)
        assert "create_project" in tcl and "platform_bd" in tcl
        assert "{D:/proj/vivado/platform}" in tcl
        assert "-part xc7z020clg400-2" in tcl and "-force" in tcl

    @pytest.mark.asyncio
    async def test_rejects_empty_name(self):
        with pytest.raises(PlatformError) as ei:
            await platform_create_design(_FakeAdapter(), name="", part="x",
                                         project_path="D:/proj")
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_adapter_failure_maps_to_platform_error(self):
        adapter = _RaisingAdapter(RuntimeError("boom"))
        with pytest.raises(PlatformError) as ei:
            await platform_create_design(adapter, name="p", part="x",
                                         project_path="D:/proj")
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


class TestGetStatus:
    @pytest.mark.asyncio
    async def test_returns_project_name_and_ip_count(self):
        adapter = _FakeAdapter([{"output": "platform_bd"}, {"output": "3"}])
        out = await platform_get_status(adapter)
        assert out["status"] == "success"
        assert out["data"]["project_name"] == "platform_bd"
        assert out["data"]["ip_count"] == 3
        assert out["data"]["has_project"] is True
        # D8: result-returning commands are printed with puts — the Tcl bridge
        # captures stdout only, never a bare command return value.
        assert adapter.calls[0][1]["command"] == "puts [get_property NAME [current_project]]"
        assert adapter.calls[1][1]["command"] == "puts [llength [get_bd_cells *]]"

    @pytest.mark.asyncio
    async def test_empty_project_reports_no_project(self):
        adapter = _FakeAdapter([{"output": ""}, {"output": "0"}])
        out = await platform_get_status(adapter)
        assert out["data"]["project_name"] == ""
        assert out["data"]["has_project"] is False
        assert out["data"]["ip_count"] == 0


# ═══════════════════════════════════════════════════════════════════
#  2. PS7 configuration
# ═══════════════════════════════════════════════════════════════════

class TestAddPs7:
    @pytest.mark.asyncio
    async def test_sources_real_preset_and_creates_cell(self):
        adapter = _FakeAdapter([{"output": ""}] * 5)
        out = await platform_add_ps7(adapter, board_id=BOARD)
        assert out["status"] == "success"
        assert out["data"]["instance"] == "processing_system7_0"
        assert len(adapter.calls) == 5
        cmds = [c[1]["command"] for c in adapter.calls]
        # 1 ensure_bd, 2 create_ps7, 3 automation, 4 source preset, 5 set_ps_config
        assert "create_bd_design" in cmds[0]
        assert "create_bd_cell" in cmds[1] and "processing_system7:5.5" in cmds[1]
        assert "apply_bd_automation" in cmds[2] and "FIXED_IO, DDR" in cmds[2]
        assert "set_ps_config processing_system7_0" in cmds[4]
        # the preset is sourced from the real board package (no copy of logic)
        preset = Path(PROJECT_ROOT) / "boards" / BOARD / "ps7_preset.tcl"
        with open(str(preset), encoding="utf-8") as f:
            preset_content = f.read().replace("\r\n", "\n")
        assert cmds[3] == preset_content

    @pytest.mark.asyncio
    async def test_missing_board_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_add_ps7(_FakeAdapter(), board_id="NOPE_ABSENT")
        assert ei.value.reason_code == "BOARD_PACKAGE_NOT_FOUND"


class TestConfigurePs7:
    @pytest.mark.asyncio
    async def test_maps_config_to_pcw_properties(self):
        adapter = _FakeAdapter()
        out = await platform_configure_ps7(adapter, config={
            "m_axi_gp0": True, "fclk0_mhz": 100, "ddr": "MT41K256M16RE-125"})
        assert out["status"] == "success"
        assert sorted(out["data"]["updated"]) == ["ddr", "fclk0_mhz", "m_axi_gp0"]
        tcl = _last_tcl(adapter)
        assert "set_property -dict [list" in tcl
        assert "CONFIG.PCW_USE_M_AXI_GP0 {1}" in tcl
        assert "CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ {100}" in tcl
        assert "CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16RE-125}" in tcl
        assert "get_bd_cells processing_system7_0" in tcl

    @pytest.mark.asyncio
    async def test_handles_uart1_nested_dict(self):
        adapter = _FakeAdapter()
        out = await platform_configure_ps7(adapter, config={
            "uart1": {"enable": True, "io": "MIO 48..49"}})
        assert out["data"]["updated"] == ["uart1_enable", "uart1_io"]
        tcl = _last_tcl(adapter)
        assert "CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1}" in tcl
        assert "CONFIG.PCW_UART1_GRP_FULL_IO {1}" in tcl

    @pytest.mark.asyncio
    async def test_unknown_key_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_configure_ps7(_FakeAdapter(), config={"bogus": 1})
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_emio_gpio_nested_dict_d0(self):
        """D0: config.gpio {emio_enable, width, io} maps to the EMIO GPIO PCW
        properties (PCW_EN_EMIO_GPIO / PCW_GPIO_EMIO_GPIO_WIDTH /
        PCW_GPIO_EMIO_GPIO_IO)."""
        adapter = _FakeAdapter()
        out = await platform_configure_ps7(adapter, config={
            "gpio": {"emio_enable": True, "width": 64, "io": "MIO 0..63"}})
        assert out["data"]["updated"] == ["gpio_emio_enable", "gpio_width",
                                          "gpio_io"]
        tcl = _last_tcl(adapter)
        assert "CONFIG.PCW_EN_EMIO_GPIO {1}" in tcl
        assert "CONFIG.PCW_GPIO_EMIO_GPIO_WIDTH {64}" in tcl
        assert "CONFIG.PCW_GPIO_EMIO_GPIO_IO {1}" in tcl


# ═══════════════════════════════════════════════════════════════════
#  3. IP management
# ═══════════════════════════════════════════════════════════════════

class TestAddIp:
    @pytest.mark.asyncio
    async def test_creates_cell_when_absent(self):
        # outputs: exists check(0) -> write("") -> readback C_GPIO_WIDTH(4)
        #         -> readback C_ALL_OUTPUTS(1). The fresh-add path now verifies
        # the requested config really applied (D-A), so the readbacks must
        # echo the requested values.
        adapter = _FakeAdapter([{"output": "0"}, {"output": ""},
                                {"output": "4"}, {"output": "1"}])
        out = await platform_add_ip(adapter,
            vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led",
            properties={"C_GPIO_WIDTH": 4, "C_ALL_OUTPUTS": 1})
        assert out["status"] == "success"
        assert out["data"]["already_exists"] is False
        # D8: the existence check prints its result (stdout-capture contract).
        assert adapter.calls[0][1]["command"] == "puts [llength [get_bd_cells -quiet axi_gpio_led]]"
        # the create+set_property write is the second Tcl command.
        write_tcl = adapter.calls[1][1]["command"]
        assert "create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_led" in write_tcl
        assert "set_property -dict" in write_tcl and "C_GPIO_WIDTH {4}" in write_tcl
        # D-A: each readback is printed with `puts` (stdout-capture contract).
        for c in adapter.calls:
            if "get_property CONFIG." in c[1]["command"]:
                assert c[1]["command"].startswith("puts [get_property CONFIG.")

    @pytest.mark.asyncio
    async def test_existing_same_config_is_unchanged(self):
        adapter = _FakeAdapter([{"output": "1"}, {"output": "4"}])
        out = await platform_add_ip(adapter,
            vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led",
            properties={"C_GPIO_WIDTH": 4})
        assert out["status"] == "success"
        assert out["data"]["already_exists"] is True
        assert out["data"]["status"] == "unchanged"
        # no create_bd_cell was ever sent
        assert all("create_bd_cell" not in c[1]["command"] for c in adapter.calls)

    @pytest.mark.asyncio
    async def test_existing_mismatch_fails_closed(self):
        adapter = _FakeAdapter([{"output": "1"}, {"output": "8"}])
        with pytest.raises(PlatformError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led",
                properties={"C_GPIO_WIDTH": 4})
        assert ei.value.reason_code == "IP_CONFIG_MISMATCH"

    @pytest.mark.asyncio
    async def test_dual_channel_config_actually_written(self):
        """D-A: a dual-channel AXI GPIO add must write AND verify channel-2
        params (C_IS_DUAL / C_GPIO2_WIDTH / C_ALL_INPUTS_2). The property is
        either really applied (readback matches -> success) or an explicit
        IP_CONFIG_MISMATCH is raised — never a silent success."""
        adapter = _FakeAdapter([
            {"output": "0"},  # exists check
            {"output": ""},   # create + set_property
            {"output": "1"},  # readback C_IS_DUAL
            {"output": "10"}, # readback C_GPIO_WIDTH
            {"output": "10"}, # readback C_GPIO2_WIDTH
            {"output": "0"},  # readback C_ALL_INPUTS
            {"output": "1"},  # readback C_ALL_INPUTS_2
        ])
        out = await platform_add_ip(adapter,
            vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_0",
            properties={"C_IS_DUAL": 1, "C_GPIO_WIDTH": 10,
                        "C_GPIO2_WIDTH": 10, "C_ALL_INPUTS": 0,
                        "C_ALL_INPUTS_2": 1})
        assert out["status"] == "success"
        assert out["data"]["already_exists"] is False
        write_tcl = adapter.calls[1][1]["command"]
        for prop, val in (("C_IS_DUAL", "1"), ("C_GPIO2_WIDTH", "10"),
                          ("C_ALL_INPUTS_2", "1")):
            assert f"CONFIG.{prop} {{{val}}}" in write_tcl
        # every readback is printed with puts and carries the applied value.
        readback_cmds = [c[1]["command"] for c in adapter.calls
                         if "get_property CONFIG." in c[1]["command"]]
        assert all(cmd.startswith("puts [get_property CONFIG.") for cmd in readback_cmds)
        assert len(readback_cmds) == 5

    @pytest.mark.asyncio
    async def test_fresh_add_silent_drop_raises_not_success(self):
        """D-A: if a requested property does NOT stick on a fresh add (the
        readback returns a stale/empty value), the atom must raise a real
        error, never return success."""
        adapter = _FakeAdapter([
            {"output": "0"},  # exists check
            {"output": ""},   # create + set_property
            {"output": ""},   # readback C_GPIO2_WIDTH -> empty (silent drop)
        ])
        with pytest.raises(PlatformError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_0",
                properties={"C_GPIO2_WIDTH": 10})
        assert ei.value.reason_code == "IP_PROPERTY_NOT_RECOGNIZED"
        assert "C_GPIO2_WIDTH" in str(ei.value)

    @pytest.mark.asyncio
    async def test_fresh_add_unknown_property_raises_not_recognized(self):
        """B12 fix round #2 (item #5): a property name Vivado silently ignores
        (wrong name, e.g. `C_DATA_WIDTH` on axi_bram_ctrl whose real parameter
        is `C_S_AXI_DATA_WIDTH`) must raise a distinct IP_PROPERTY_NOT_RECOGNIZED
        — telling the caller the NAME is wrong, not a value mismatch."""
        adapter = _FakeAdapter([
            {"output": "0"},  # exists check
            {"output": ""},   # create + set_property
            {"output": ""},   # readback C_DATA_WIDTH -> '' (not a real param)
        ])
        with pytest.raises(PlatformError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_bram_ctrl:4.1",
                instance_name="axi_bram_ctrl_0",
                properties={"C_DATA_WIDTH": 32})
        assert ei.value.reason_code == "IP_PROPERTY_NOT_RECOGNIZED"
        assert "C_DATA_WIDTH" in str(ei.value)

    @pytest.mark.asyncio
    async def test_fresh_add_recognized_value_mismatch_raises_ip_config(self):
        """A property the IP DOES recognize but whose value was not applied
        (readback returns a different value) raises IP_CONFIG_MISMATCH — the
        generic mismatch, distinct from an unknown property name."""
        adapter = _FakeAdapter([
            {"output": "0"},  # exists check
            {"output": ""},   # create + set_property
            {"output": "64"}, # readback C_S_AXI_DATA_WIDTH -> different value
        ])
        with pytest.raises(PlatformError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_bram_ctrl:4.1",
                instance_name="axi_bram_ctrl_0",
                properties={"C_S_AXI_DATA_WIDTH": 32})
        assert ei.value.reason_code == "IP_CONFIG_MISMATCH"
        assert "C_S_AXI_DATA_WIDTH" in str(ei.value)


class TestListIps:
    @pytest.mark.asyncio
    async def test_returns_cells_without_filter(self):
        adapter = _FakeAdapter([{"output": "processing_system7_0 smartconnect_0 axi_gpio_led"}])
        out = await platform_list_ips(adapter)
        assert out["status"] == "success"
        assert out["data"]["ips"] == ["processing_system7_0", "smartconnect_0", "axi_gpio_led"]
        assert out["data"]["count"] == 3
        # D8: printed with puts (stdout-capture contract).
        assert _last_tcl(adapter) == "puts [get_bd_cells *]"

    @pytest.mark.asyncio
    async def test_returns_cells_with_filter(self):
        adapter = _FakeAdapter([{"output": "axi_gpio_led"}])
        out = await platform_list_ips(adapter, filter="VLNV =~ *axi_gpio*")
        assert out["data"]["ips"] == ["axi_gpio_led"]
        assert _last_tcl(adapter) == "puts [get_bd_cells -filter {VLNV =~ *axi_gpio*}]"


# ═══════════════════════════════════════════════════════════════════
#  4. Interface & clock connection
# ═══════════════════════════════════════════════════════════════════

class TestConnectInterface:
    @pytest.mark.asyncio
    async def test_connects_two_axi_interfaces(self):
        adapter = _FakeAdapter()
        out = await platform_connect_interface(adapter,
            source="processing_system7_0/M_AXI_GP0",
            destination="smartconnect_0/S00_AXI")
        assert out["status"] == "success"
        assert out["data"] == {"source": "processing_system7_0/M_AXI_GP0",
                               "destination": "smartconnect_0/S00_AXI"}
        tcl = _last_tcl(adapter)
        assert "connect_bd_intf_net" in tcl
        assert "processing_system7_0/M_AXI_GP0" in tcl
        assert "smartconnect_0/S00_AXI" in tcl


class TestConnectClock:
    @pytest.mark.asyncio
    async def test_connects_source_to_each_target(self):
        adapter = _FakeAdapter()
        targets = ["smartconnect_0/aclk", "axi_gpio_led/s_axi_aclk"]
        out = await platform_connect_clock(adapter,
            source="processing_system7_0/FCLK_CLK0", targets=targets)
        assert out["status"] == "success"
        assert out["data"]["count"] == 2
        tcl = _last_tcl(adapter)
        assert tcl.count("connect_bd_net") == 2
        assert "processing_system7_0/FCLK_CLK0" in tcl
        assert "smartconnect_0/aclk" in tcl and "axi_gpio_led/s_axi_aclk" in tcl

    @pytest.mark.asyncio
    async def test_rejects_empty_targets(self):
        with pytest.raises(PlatformError) as ei:
            await platform_connect_clock(_FakeAdapter(), source="a/clk", targets=[])
        assert ei.value.reason_code == "INVALID_ARGUMENT"


class TestConnectReset:
    @pytest.mark.asyncio
    async def test_connects_source_to_each_target(self):
        adapter = _FakeAdapter()
        targets = ["axi_gpio_led/s_axi_aresetn", "smartconnect_0/aresetn"]
        out = await platform_connect_reset(adapter,
            source="rst_ps7_50M/peripheral_aresetn", targets=targets)
        assert out["status"] == "success"
        assert out["data"]["count"] == 2
        assert out["data"]["targets"] == targets
        tcl = _last_tcl(adapter)
        assert tcl.count("connect_bd_net") == 2
        assert "rst_ps7_50M/peripheral_aresetn" in tcl
        assert "axi_gpio_led/s_axi_aresetn" in tcl and "smartconnect_0/aresetn" in tcl
        # every line wraps both pins in get_bd_pins
        for line in tcl.splitlines():
            assert "connect_bd_net [get_bd_pins" in line
            assert line.count("[get_bd_pins") == 2

    @pytest.mark.asyncio
    async def test_rejects_empty_targets(self):
        with pytest.raises(PlatformError) as ei:
            await platform_connect_reset(_FakeAdapter(), source="a/aresetn", targets=[])
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_rejects_non_string_target(self):
        with pytest.raises(PlatformError) as ei:
            await platform_connect_reset(_FakeAdapter(),
                source="a/aresetn", targets=["ok/aresetn", 42])
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_adapter_failure_maps_to_platform_error(self):
        adapter = _RaisingAdapter(RuntimeError("boom"))
        with pytest.raises(PlatformError) as ei:
            await platform_connect_reset(adapter,
                source="a/aresetn", targets=["b/aresetn"])
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


# ═══════════════════════════════════════════════════════════════════
#  5. Address space
# ═══════════════════════════════════════════════════════════════════

class TestSetAddress:
    @pytest.mark.asyncio
    async def test_sets_base_only(self):
        adapter = _FakeAdapter()
        out = await platform_set_address(adapter,
            segment="axi_gpio_led/S_AXI", base="0x41200000")
        assert out["status"] == "success"
        tcl = _last_tcl(adapter)
        # D5: the short segment is resolved via get_bd_addr_segs (direct match
        # first, then the interface pin's child segments), then set_property
        # targets the resolved segment object.
        assert "set __req {axi_gpio_led/S_AXI}" in tcl
        assert "set __segs [get_bd_addr_segs -quiet $__req]" in tcl
        assert 'get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet $__ip]' in tcl
        assert 'error "SEGMENT_NOT_FOUND:{axi_gpio_led/S_AXI}"' in tcl
        assert "set_property CONFIG.C_BASEADDR {0x41200000} $__seg" in tcl
        assert "C_HIGHADDR" not in tcl

    @pytest.mark.asyncio
    async def test_sets_base_and_computes_highaddr(self):
        adapter = _FakeAdapter()
        out = await platform_set_address(adapter,
            segment="axi_gpio_led/S_AXI", base="0x41200000", size=65536)
        assert out["data"]["size"] == 65536
        tcl = _last_tcl(adapter)
        assert "set_property CONFIG.C_BASEADDR {0x41200000} $__seg" in tcl
        assert "set_property CONFIG.C_HIGHADDR {0x4120ffff} $__seg" in tcl

    @pytest.mark.asyncio
    async def test_invalid_base_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_set_address(_FakeAdapter(),
                segment="s/S_AXI", base="nothex", size=4)
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_empty_segment_rejected(self):
        with pytest.raises(PlatformError) as ei:
            await platform_set_address(_FakeAdapter(), segment="", base="0x0")
        assert ei.value.reason_code == "INVALID_ARGUMENT"


class TestAssignAddresses:
    @pytest.mark.asyncio
    async def test_assigns_all_and_returns_address_map(self):
        adapter = _FakeAdapter([
            {"output": ""},  # assign_bd_address result
            {"output": "processing_system7_0/M_AXI_GP0 axi_gpio_led/S_AXI/reg0 "
                       "0x0000000040000000 64K"},  # address map query
        ])
        out = await platform_assign_addresses(adapter)
        assert out["status"] == "success"
        assert out["data"]["assigned"] is True
        assert out["data"]["address_map"]["axi_gpio_led"]["base"] == "0x40000000"
        assert adapter.calls[0][1]["command"] == "assign_bd_address"
        # D8: the map query enumerates interface pins via -of_objects (bare /
        # wildcard / -filter intf-pin forms match nothing on real Vivado) and
        # prints master-side segments that carry an OFFSET.
        tcl = adapter.calls[1][1]["command"]
        assert "get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet *]" in tcl
        assert "get_property OFFSET $mseg" in tcl
        assert "string trimleft $m /" in tcl

    @pytest.mark.asyncio
    async def test_assigns_all_and_parses_master_side_segment_names(self):
        """D8: real-Vivado master-side segment names look like
        'processing_system7_0/Data/SEG_<ip>_Reg' — the parser extracts the
        slave IP as the map key."""
        from mcps.zynq_mcp.domains.platform.platform_atoms import (
            _parse_manifest_address_map,
        )
        amap = _parse_manifest_address_map(
            "processing_system7_0/M_AXI_GP0 "
            "processing_system7_0/Data/SEG_axi_gpio_led_Reg "
            "0x41200000 0x00010000")
        assert amap["axi_gpio_led"]["base"] == "0x41200000"
        assert amap["axi_gpio_led"]["range"] == "0x00010000"
        assert amap["axi_gpio_led"]["master"] == "processing_system7_0/M_AXI_GP0"

    @pytest.mark.asyncio
    async def test_assigns_explicit_segments(self):
        adapter = _FakeAdapter([{"output": ""}, {"output": ""}])
        out = await platform_assign_addresses(adapter,
            segments=["axi_gpio_led/S_AXI", "other_0/S_AXI"])
        assert out["status"] == "success"
        tcl = adapter.calls[0][1]["command"]
        assert tcl == ("assign_bd_address [get_bd_addr_segs {axi_gpio_led/S_AXI}]\n"
                       "assign_bd_address [get_bd_addr_segs {other_0/S_AXI}]")

    @pytest.mark.asyncio
    async def test_empty_segments_list_rejected(self):
        with pytest.raises(PlatformError) as ei:
            await platform_assign_addresses(_FakeAdapter(), segments=[])
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_no_adapter_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_assign_addresses(None)
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


class TestMakeExternal:
    @pytest.mark.asyncio
    async def test_signal_port_created_and_connected(self):
        adapter = _FakeAdapter([{"output": ""}, {"output": "/DDR_addr /led_pins"}])
        out = await platform_make_external(adapter,
            port_name="led_pins", source_pin="axi_gpio_led/gpio_io_o",
            direction="out", width=4)
        assert out["status"] == "success"
        assert out["data"]["port_name"] == "led_pins"
        assert out["data"]["direction"] == "out"
        assert out["data"]["width"] == 4
        tcl = adapter.calls[0][1]["command"]
        # real-Vivado verified: create_bd_port accepts I/O/IO direction
        # letters only (BD 41-78 rejects IN/OUT/INOUT)
        assert "create_bd_port -dir O -from 3 -to 0 led_pins" in tcl
        assert ("connect_bd_net [get_bd_pins axi_gpio_led/gpio_io_o] "
                "[get_bd_ports led_pins]") in tcl
        # D8: port existence verified against the full listing (name queries
        # match nothing on real Vivado)
        assert adapter.calls[1][1]["command"] == "puts [get_bd_ports *]"

    @pytest.mark.asyncio
    async def test_scalar_port_when_width_omitted(self):
        adapter = _FakeAdapter([{"output": ""}, {"output": "/sig"}])
        out = await platform_make_external(adapter,
            port_name="sig", source_pin="ip_0/pin_o", direction="in")
        assert out["data"]["width"] == 1
        assert "-from" not in adapter.calls[0][1]["command"]
        assert "create_bd_port -dir I sig" in adapter.calls[0][1]["command"]

    @pytest.mark.asyncio
    async def test_interface_mode_uses_make_bd_intf_pins_external(self):
        adapter = _FakeAdapter([{"output": "EXT_PORT /M_AXI_GP0"}])
        out = await platform_make_external(adapter,
            port_name="M_AXI_GP0", source_pin="processing_system7_0/M_AXI_GP0",
            interface=True)
        assert out["status"] == "success"
        assert out["data"]["interface"] is True
        assert out["data"]["port_name"] == "M_AXI_GP0"
        tcl = adapter.calls[0][1]["command"]
        # real-Vivado verified: make_bd_pins_external only applies to regular
        # pins (BD 5-407); interface pins use make_bd_intf_pins_external.
        assert "make_bd_intf_pins_external" in tcl
        assert "get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet $__ip]" in tcl
        # B13-M2 ④: the derived name is captured via the interface net
        # (-of_objects on the pin matches nothing — BD 5-233), not guessed
        # from the pin basename
        assert ("set __ext [get_bd_intf_ports -of_objects "
                "[get_bd_intf_nets -of_objects $__pin]]") in tcl
        assert 'puts "EXT_PORT $__ext"' in tcl
        assert len(adapter.calls) == 1

    @pytest.mark.asyncio
    async def test_interface_derived_suffix_name_captured(self):
        # real-Vivado verified: axi_gpio_0/S_AXI externalizes as S_AXI_0,
        # axi_gpio_1/S_AXI as S_AXI_1 — the pin basename ("S_AXI") is wrong
        adapter = _FakeAdapter([{"output": "EXT_PORT /S_AXI_0"}])
        out = await platform_make_external(adapter,
            port_name="S_AXI", source_pin="axi_gpio_0/S_AXI", interface=True)
        assert out["status"] == "success"
        assert out["data"]["port_name"] == "S_AXI_0"

    @pytest.mark.asyncio
    async def test_interface_pin_missing_fails_closed(self):
        adapter = _FakeAdapter([{"output": ""}])
        with pytest.raises(PlatformError) as ei:
            await platform_make_external(adapter,
                port_name="NOPE", source_pin="processing_system7_0/NOPE",
                interface=True)
        assert ei.value.reason_code == "EXTERNAL_PORT_CREATE_FAILED"

    @pytest.mark.asyncio
    async def test_invalid_direction_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_make_external(_FakeAdapter(),
                port_name="p", source_pin="a/b", direction="sideways")
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_port_missing_fails_closed(self):
        adapter = _FakeAdapter([{"output": ""}, {"output": "/DDR_addr"}])
        with pytest.raises(PlatformError) as ei:
            await platform_make_external(adapter,
                port_name="p", source_pin="a/b", direction="out")
        assert ei.value.reason_code == "EXTERNAL_PORT_CREATE_FAILED"

    @pytest.mark.asyncio
    async def test_no_adapter_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_make_external(None,
                port_name="p", source_pin="a/b", direction="out")
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


class TestSynthesize:
    @pytest.mark.asyncio
    async def test_launches_waits_opens_and_reports_status(self):
        adapter = _FakeAdapter([
            {"output": ""},                          # launch/wait/open
            {"output": "synth_design complete!"},    # run STATUS
            {"output": "N/A"},                       # WNS (no timing paths)
        ])
        out = await platform_synthesize(adapter)
        assert out["status"] == "success"
        assert out["data"]["status"] == "synth_design complete!"
        assert out["data"]["wns"] is None
        assert out["data"]["jobs"] == 1  # serial OOC (license-safe default)
        tcl = adapter.calls[0][1]["command"]
        # the BD must be set as the synthesis top (real-Vivado verified)
        assert "set_property top platform_bd [current_fileset]" in tcl
        assert "launch_runs synth_1 -jobs 1" in tcl
        assert "wait_on_run synth_1" in tcl
        assert "open_run synth_1" in tcl
        # the long synthesis eval carries the generous timeout
        assert adapter.calls[0][2] > 1500
        assert adapter.calls[1][1]["command"] == \
            "puts [get_property STATUS [get_runs synth_1]]"

    @pytest.mark.asyncio
    async def test_custom_jobs(self):
        adapter = _FakeAdapter([
            {"output": ""}, {"output": "synth_design complete!"}, {"output": "N/A"}])
        await platform_synthesize(adapter, jobs=2)
        assert "launch_runs synth_1 -jobs 2" in adapter.calls[0][1]["command"]

    @pytest.mark.asyncio
    async def test_non_complete_status_fails_closed(self):
        adapter = _FakeAdapter([
            {"output": ""}, {"output": "synth_design errored!"}, {"output": "N/A"}])
        with pytest.raises(PlatformError) as ei:
            await platform_synthesize(adapter)
        assert ei.value.reason_code == "SYNTHESIS_FAILED"

    @pytest.mark.asyncio
    async def test_parses_wns_when_present(self):
        adapter = _FakeAdapter([
            {"output": ""}, {"output": "synth_design complete!"}, {"output": "-0.123"}])
        out = await platform_synthesize(adapter)
        assert out["data"]["wns"] == -0.123

    @pytest.mark.asyncio
    async def test_invalid_jobs_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_synthesize(_FakeAdapter(), jobs=0)
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_no_adapter_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_synthesize(None)
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


# ═══════════════════════════════════════════════════════════════════
#  6. Validation & export
# ═══════════════════════════════════════════════════════════════════

class TestValidate:
    @pytest.mark.asyncio
    async def test_passes_on_clean_output(self):
        # Frozen B05 semantics: any "error" substring fails validation, so a
        # clean output must not contain it (real Vivado success output does not).
        adapter = _FakeAdapter([{"output": "INFO: [BD 41-231] Design validation successful"}])
        out = await platform_validate(adapter)
        assert out["status"] == "success"
        assert out["data"]["validation"] == "passed"
        # D7: -force invalidates Vivado's "already validated" cache so real
        # errors always surface on a re-validation.
        assert _last_tcl(adapter) == "validate_bd_design -force"

    @pytest.mark.asyncio
    async def test_fails_on_error(self):
        adapter = _FakeAdapter([{"output": "ERROR: [BD 41-217] cell not connected"}])
        with pytest.raises(PlatformError) as ei:
            await platform_validate(adapter)
        assert ei.value.reason_code == "BD_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_fails_on_critical_warning(self):
        adapter = _FakeAdapter([{"output": "CRITICAL WARNING: unconnected port"}])
        with pytest.raises(PlatformError) as ei:
            await platform_validate(adapter)
        assert ei.value.reason_code == "BD_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_second_validate_not_masked_by_cache(self):
        """D7 regression: two consecutive validates against the same fake
        backend; the second output carries a real critical warning (as a
        re-validated design would) and must FAIL — the -force re-validation
        never lets an earlier success mask a later real error."""
        adapter = _FakeAdapter([
            {"output": "INFO: [BD 41-231] Design validation successful"},
            {"output": "CRITICAL WARNING: [BD 41-1356] Slave segment "
                       "</axi_gpio_led/S_AXI/Reg> is not assigned into address "
                       "space </processing_system7_0/Data>."},
        ])
        first = await platform_validate(adapter)
        assert first["status"] == "success"
        with pytest.raises(PlatformError) as ei:
            await platform_validate(adapter)
        assert ei.value.reason_code == "BD_VALIDATION_FAILED"
        # both calls ran the cache-invalidating form
        assert adapter.calls[0][1]["command"] == "validate_bd_design -force"
        assert adapter.calls[1][1]["command"] == "validate_bd_design -force"


class TestGenerateWrapper:
    @pytest.mark.asyncio
    async def test_copies_wrapper_into_hdl(self, tmp_path):
        proj = tmp_path / "proj"
        viv = (proj / "vivado" / "platform" / "platform.gen" / "sources_1" /
               "bd" / "platform_bd" / "hdl")
        viv.mkdir(parents=True)
        (viv / "platform_bd_wrapper.v").write_text("module platform_bd_wrapper(); endmodule")
        adapter = _FakeAdapter()
        out = await platform_generate_wrapper(adapter, project_path=str(proj))
        assert out["status"] == "success"
        assert out["data"]["wrapper_name"] == "platform_bd_wrapper.v"
        assert out["data"]["wrapper_path"] == str(proj / "hdl" / "platform_bd_wrapper.v")
        assert out["data"]["wrapper_sha256"].startswith("sha256:")
        assert os.path.isfile(out["data"]["wrapper_path"])
        assert _last_tcl(adapter) == "make_wrapper -files [get_files *.bd] -top"

    @pytest.mark.asyncio
    async def test_fails_closed_when_wrapper_missing(self, tmp_path):
        adapter = _FakeAdapter()
        with pytest.raises(PlatformError) as ei:
            await platform_generate_wrapper(adapter, project_path=str(tmp_path / "empty"))
        assert ei.value.reason_code == "WRAPPER_EXPORT_FAILED"


class TestExportHardware:
    @staticmethod
    def _dummy_xsa(path):
        # Vivado always emits a zip; B13-M3 normalization expects one.
        import zipfile as _z
        with _z.ZipFile(path, "w", _z.ZIP_DEFLATED) as z:
            info = _z.ZipInfo("design.hwh", date_time=(2023, 1, 1, 0, 0, 0))
            z.writestr(info, b"hwh")

    @pytest.mark.asyncio
    async def test_exports_to_default_path(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        self._dummy_xsa(proj / "platform.xsa")
        adapter = _FakeAdapter()
        out = await platform_export_hardware(adapter, project_path=str(proj))
        assert out["status"] == "success"
        assert out["data"]["xsa_path"] == str(proj / "platform.xsa")
        assert out["data"]["xsa_sha256"].startswith("sha256:")
        # 修复轮#8/#10: export 现为 write_hw_platform + 地址映射查询两次
        # 调用（查询在最后）——write 命令存在于任一调用中即可。
        all_tcl = "\n".join(c[1]["command"] for c in adapter.calls)
        assert "write_hw_platform -fixed -force" in all_tcl
        assert str(proj / "platform.xsa") in all_tcl

    @pytest.mark.asyncio
    async def test_exports_to_explicit_path(self, tmp_path):
        xsa = tmp_path / "out" / "hw.xsa"
        xsa.parent.mkdir()
        self._dummy_xsa(xsa)
        adapter = _FakeAdapter()
        out = await platform_export_hardware(adapter, path=str(xsa))
        assert out["data"]["xsa_path"] == str(xsa)

    @pytest.mark.asyncio
    async def test_fails_closed_when_xsa_absent(self, tmp_path):
        adapter = _FakeAdapter()
        with pytest.raises(PlatformError) as ei:
            await platform_export_hardware(adapter, path=str(tmp_path / "nope.xsa"))
        assert ei.value.reason_code == "XSA_EXPORT_FAILED"


class TestExportManifest:
    """platform_export_manifest — standalone re-export from the open BD.

    A ready BD is simulated with scripted run_tcl outputs; the wrapper and
    XSA must physically exist under {project_path} because the platform
    schema validates their path + SHA256.
    """

    _OUTPUTS = [
        {"output": "1"},  # count_bd_designs (puts form)
        {"output": "processing_system7_0 smartconnect_0 axi_gpio_led rst_ps7_50M"},
        {"output": "processing_system7_0/M_AXI_GP0 axi_gpio_led/S_AXI/reg0 "
                    "0x0000000041200000 64K"},
        # D9: full pin paths ("<cell>/<pin>"), source pin first
        {"output": "processing_system7_0/FCLK_CLK0\n"
                    "processing_system7_0/M_AXI_GP0_ACLK\n"
                    "smartconnect_0/aclk\n"
                    "axi_gpio_led/s_axi_aclk\n"
                    "rst_ps7_50M/slowest_sync_clk"},
        {"output": "2023.1"},  # vivado version
    ]

    @staticmethod
    def _make_project(tmp_path):
        proj = tmp_path / "proj"
        hdl = proj / "hdl"
        hdl.mkdir(parents=True)
        (hdl / "platform_bd_wrapper.v").write_text(
            "module platform_bd_wrapper(); endmodule")
        (proj / "platform.xsa").write_bytes(b"\x78\x73\x61")
        return proj

    @pytest.mark.asyncio
    async def test_publishes_manifest_from_open_bd(self, tmp_path):
        proj = self._make_project(tmp_path)
        adapter = _FakeAdapter(list(self._OUTPUTS))
        out = await platform_export_manifest(
            adapter, project_path=str(proj), board_id=BOARD,
            board_profile_sha256=_SHA)
        assert out["status"] == "success"
        assert out["data"]["publish"] == "published"
        assert out["data"]["platform_revision"].startswith("sha256:")
        assert out["data"]["manifest_sha256"].startswith("sha256:")
        # default path under {project_path}/manifests/platform/sha256_<rev>.json
        assert out["data"]["manifest_path"] == str(
            proj / "manifests" / "platform"
            / f"sha256_{out['data']['platform_revision'][7:]}.json")
        assert os.path.isfile(out["data"]["manifest_path"])
        # live BD data surfaced in the result
        assert out["data"]["ip_list"] == ["processing_system7_0", "smartconnect_0",
                                          "axi_gpio_led", "rst_ps7_50M"]
        assert out["data"]["address_map"]["axi_gpio_led"]["base"] == "0x41200000"
        assert out["data"]["address_map"]["axi_gpio_led"]["master"] == \
            "processing_system7_0/M_AXI_GP0"
        assert "smartconnect_0/aclk" in out["data"]["clock_tree"]["FCLK_CLK0"]
        assert out["data"]["wrapper_name"] == "platform_bd_wrapper.v"
        # Tcl queries were the 5 expected ones (D8: result-returning queries
        # are printed with puts — the bridge captures stdout only; intf pins
        # are enumerated via -of_objects; D9: clock pins print full paths via
        # the object's string form).
        cmds = [c[1]["command"] for c in adapter.calls]
        assert cmds[0] == "puts [llength [get_bd_designs -quiet]]"
        assert cmds[1] == "puts [get_bd_cells *]"
        assert "get_bd_addr_segs" in cmds[2] and "-of_objects" in cmds[2]
        assert "FCLK_CLK0" in cmds[3]
        assert "string trimleft $p /" in cmds[3]  # D9: full pin paths
        assert cmds[4] == "puts [version -short]"
        # the persisted manifest is a valid platform manifest
        with open(out["data"]["manifest_path"], encoding="utf-8") as f:
            manifest = json.load(f)
        assert manifest["manifest_type"] == "platform"
        assert manifest["board_profile_sha256"] == _SHA
        assert manifest["xsa_path"] == "platform.xsa"
        assert manifest["bd_wrapper_path"] == "hdl/platform_bd_wrapper.v"
        assert manifest["address_map"]["axi_gpio_led"]["base"] == "0x41200000"

    @pytest.mark.asyncio
    async def test_republish_is_idempotent(self, tmp_path):
        proj = self._make_project(tmp_path)
        # _FakeAdapter repeats the last scripted output once exhausted, so each
        # call gets a fresh adapter (identical BD state → identical outputs).
        first = await platform_export_manifest(
            _FakeAdapter(list(self._OUTPUTS)), project_path=str(proj),
            board_id=BOARD, board_profile_sha256=_SHA)
        assert first["data"]["publish"] == "published"
        # second call on the same BD → same revision, no overwrite
        again = await platform_export_manifest(
            _FakeAdapter(list(self._OUTPUTS)), project_path=str(proj),
            board_id=BOARD, board_profile_sha256=_SHA)
        assert again["data"]["publish"] == "already_exists_same"
        assert again["data"]["manifest_path"] == first["data"]["manifest_path"]
        assert again["data"]["manifest_sha256"] == first["data"]["manifest_sha256"]

    @pytest.mark.asyncio
    async def test_re_export_versions_on_changed_xsa(self, tmp_path):
        """B12 fix round #2 (item #4C): a re-export with an unchanged BD/board
        profile/preset but a CHANGED XSA must produce a NEW revision and be
        published to its OWN sha256_<rev>.json path — "correct versioning",
        not a ManifestConflictError (the same revision path would collide)."""
        proj = self._make_project(tmp_path)
        first = await platform_export_manifest(
            _FakeAdapter(list(self._OUTPUTS)), project_path=str(proj),
            board_id=BOARD, board_profile_sha256=_SHA)
        assert first["data"]["publish"] == "published"
        rev1 = first["data"]["platform_revision"]

        # Change the XSA bytes (same wrapper / board / preset). The revision
        # now must advance because xsa_sha256 is a revision input.
        (proj / "platform.xsa").write_bytes(b"\x78\x73\x61CHANGED")
        second = await platform_export_manifest(
            _FakeAdapter(list(self._OUTPUTS)), project_path=str(proj),
            board_id=BOARD, board_profile_sha256=_SHA)
        assert second["data"]["publish"] == "published"
        rev2 = second["data"]["platform_revision"]
        assert rev2 != rev1, "changed XSA must advance the platform revision"

        # The new manifest lands at its own revision path, not the old one.
        path2 = proj / "manifests" / "platform" / f"sha256_{rev2[7:]}.json"
        assert os.path.isfile(path2)
        assert path2 != first["data"]["manifest_path"]
        with open(path2, encoding="utf-8") as f:
            manifest2 = json.load(f)
        assert manifest2["manifest_revision"] == rev2
        assert manifest2["manifest_type"] == "platform"
        # the persisted manifest records the NEW xsa_sha256 (versioned).
        assert manifest2["xsa_sha256"] == \
            _sha256_file(str(proj / "platform.xsa"))

    @pytest.mark.asyncio
    async def test_fails_closed_when_bd_not_ready(self, tmp_path):
        proj = self._make_project(tmp_path)
        adapter = _FakeAdapter([{"output": "0"}])
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                adapter, project_path=str(proj), board_id=BOARD,
                board_profile_sha256=_SHA)
        assert ei.value.reason_code == "MANIFEST_GENERATION_FAILED"

    @pytest.mark.asyncio
    async def test_fails_closed_when_wrapper_missing(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "platform.xsa").write_bytes(b"\x78\x73\x61")
        adapter = _FakeAdapter(list(self._OUTPUTS))
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                adapter, project_path=str(proj), board_id=BOARD,
                board_profile_sha256=_SHA)
        assert ei.value.reason_code == "MANIFEST_GENERATION_FAILED"

    @pytest.mark.asyncio
    async def test_fails_closed_when_xsa_missing(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "hdl").mkdir(parents=True)
        (proj / "hdl" / "platform_bd_wrapper.v").write_text(
            "module platform_bd_wrapper(); endmodule")
        adapter = _FakeAdapter(list(self._OUTPUTS))
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                adapter, project_path=str(proj), board_id=BOARD,
                board_profile_sha256=_SHA)
        assert ei.value.reason_code == "MANIFEST_GENERATION_FAILED"

    @pytest.mark.asyncio
    async def test_rejects_explicit_path_with_wrong_filename(self, tmp_path):
        proj = self._make_project(tmp_path)
        adapter = _FakeAdapter(list(self._OUTPUTS))
        bad = str(proj / "manifests" / "platform" / "not_rev.json")
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                adapter, path=bad, project_path=str(proj), board_id=BOARD,
                board_profile_sha256=_SHA)
        assert ei.value.reason_code == "MANIFEST_GENERATION_FAILED"

    @pytest.mark.asyncio
    async def test_requires_project_path(self):
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                _FakeAdapter(), board_id=BOARD, board_profile_sha256=_SHA)
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_requires_board_profile_sha256(self, tmp_path):
        proj = self._make_project(tmp_path)
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                _FakeAdapter(), project_path=str(proj), board_id=BOARD)
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_rejects_invalid_profile_sha(self, tmp_path):
        proj = self._make_project(tmp_path)
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                _FakeAdapter(), project_path=str(proj), board_id=BOARD,
                board_profile_sha256="not-a-sha256")
        assert ei.value.reason_code == "INVALID_ARGUMENT"

    @pytest.mark.asyncio
    async def test_adapter_failure_maps_to_platform_error(self, tmp_path):
        proj = self._make_project(tmp_path)
        adapter = _RaisingAdapter(RuntimeError("boom"))
        with pytest.raises(PlatformError) as ei:
            await platform_export_manifest(
                adapter, project_path=str(proj), board_id=BOARD,
                board_profile_sha256=_SHA)
        assert ei.value.reason_code == "ADAPTER_NOT_READY"


class TestTclErrorClassification:
    """D6: a Tcl-level failure from a healthy backend is TOOL_ERROR/TCL_ERROR,
    never ADAPTER_NOT_READY. ADAPTER_NOT_READY is reserved for genuine
    backend-unready responses."""

    @pytest.mark.asyncio
    async def test_tcl_error_maps_to_tcl_error(self):
        adapter = _ErrorAdapter(
            "ERROR: [Common 17-39] 'create_bd_cell' failed due to earlier errors")
        with pytest.raises(TclError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led")
        assert ei.value.reason_code == "TCL_ERROR"

    @pytest.mark.asyncio
    async def test_tcl_error_with_bridge_rc_maps_to_tcl_error(self):
        adapter = _ErrorAdapter("ERROR: [BD 41-79] Specified object already exists",
                                reason_code="XSDM_TCL_ERROR")
        with pytest.raises(TclError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led")
        assert ei.value.reason_code == "TCL_ERROR"

    @pytest.mark.asyncio
    async def test_backend_not_ready_keeps_adapter_not_ready(self):
        adapter = _ErrorAdapter("Vivado backend not active",
                                reason_code="BACKEND_NOT_ACTIVE")
        with pytest.raises(PlatformError) as ei:
            await platform_add_ip(adapter,
                vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led")
        assert ei.value.reason_code == "ADAPTER_NOT_READY"

    @pytest.mark.asyncio
    async def test_local_fn_maps_tcl_error_to_tool_error_envelope(self):
        """D6 end-to-end envelope: the dispatcher local executor turns a Tcl
        error into TOOL_ERROR + reason_code TCL_ERROR (the old mislabeled
        ADAPTER_NOT_READY is gone for Tcl failures)."""
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        local_fn = _make_platform_atom_local_fn("platform_add_ip")
        adapter = _ErrorAdapter("ERROR: [Common 17-39] 'create_bd_cell' failed")
        out = await local_fn(adapter,
            vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led")
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "TCL_ERROR"


# ═══════════════════════════════════════════════════════════════════
#  Registration / routing consistency (production sources)
# ═══════════════════════════════════════════════════════════════════

class TestRegistrationConsistency:
    def test_atom_count_is_19(self):
        # B11 ③.1: 14 B05-R2 atoms + assign_addresses/make_external/synthesize
        # = 17; B13-M2: platform_package_user_ip/platform_set_bd_object_property = 19
        assert len(PLATFORM_ATOM_TOOL_NAMES) == 19
        assert len(PLATFORM_ATOM_MAP) == 19
        assert PLATFORM_ATOM_COMMAND_TOOL_NAMES | PLATFORM_ATOM_QUERY_TOOL_NAMES \
            == PLATFORM_ATOM_TOOL_NAMES
        assert len(PLATFORM_ATOM_COMMAND_TOOL_NAMES) == 17
        assert len(PLATFORM_ATOM_QUERY_TOOL_NAMES) == 2

    def test_every_atom_registered_in_capabilities(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        names = {t.name for t in ALL_TOOLS}
        assert PLATFORM_ATOM_TOOL_NAMES <= names

    def test_every_atom_routed_in_dispatcher(self):
        from mcps.zynq_mcp.dispatcher import _ALL_KNOWN
        for name in PLATFORM_ATOM_TOOL_NAMES:
            assert name in _ALL_KNOWN, name

    def test_queries_routed_in_query_tools(self):
        from mcps.zynq_mcp.dispatcher import _QUERY_TOOLS
        assert PLATFORM_ATOM_QUERY_TOOL_NAMES <= _QUERY_TOOLS

    def test_commands_routed_in_domain_tools(self):
        from mcps.zynq_mcp.dispatcher import _DOMAIN_TOOLS
        assert PLATFORM_ATOM_COMMAND_TOOL_NAMES <= _DOMAIN_TOOLS


# ═══════════════════════════════════════════════════════════════════
#  CommandRunner adapter injection (production path)
# ═══════════════════════════════════════════════════════════════════

class _FakeWorker:
    """Minimal SingleWorkerController stand-in: ensure_worker() returns the
    configured VivadoAdapter (or None when the worker is absent)."""

    def __init__(self, adapter):
        self._adapter = adapter

    async def ensure_worker(self):
        return self._adapter


class TestCommandRunnerInjection:
    def _setup(self):
        rt = Path(tempfile.mkdtemp())
        g = InstanceGuard(rt, "ws-plat-atom"); g.determine_role()
        lp = rt / "l.json"
        sid = f"session-{uuid.uuid4().hex[:8]}"

        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-plat-atom"
            l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
            l.context["session_id"] = sid; l.context["board_id"] = BOARD
            l.context["board_package_revision"] = _SHA
            l.context["expected_board_revision"] = _SHA
            l.context["current_stage"] = "PLATFORM_DESIGN"
            return l

        ledger_transaction(g, lp, _init)
        return rt, g, lp, sid

    def _teardown(self, rt, g):
        g.release_owner_lock()
        shutil.rmtree(str(rt), ignore_errors=True)

    @staticmethod
    async def _wait_terminal(guard, ledger_path, op_id, timeout_s=5.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            l, _ = ledger_read_shared(guard, ledger_path)
            if l.active_operation is None or l.active_operation.get("operation_id") != op_id:
                return l
            await asyncio.sleep(0.01)
        return None

    @pytest.mark.asyncio
    async def test_adapter_injected_and_no_stage_advance(self):
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            fake_adapter = _FakeAdapter([{"output": "0"}, {"output": ""}])
            runner = CommandRunner(g, lp, oreg, mutex, worker=_FakeWorker(fake_adapter))
            local_fn = _make_platform_atom_local_fn("platform_add_ip")
            r = await runner.run_command(
                "platform_add_ip",
                {"vlnv": "xilinx.com:ip:axi_gpio:2.0", "instance_name": "axi_gpio_led"},
                sid, BOARD, "/tmp/p", executor="local", local_fn=local_fn,
                timeout_s=60.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            assert fake_adapter.calls[0][1]["command"] == "puts [llength [get_bd_cells -quiet axi_gpio_led]]"
            assert "create_bd_cell" in fake_adapter.calls[1][1]["command"]
            # next_stage=None -> workflow stage stays PLATFORM_DESIGN
            assert l2.context["current_stage"] == "PLATFORM_DESIGN"
        finally:
            self._teardown(rt, g)

    @pytest.mark.asyncio
    async def test_fails_closed_when_worker_absent(self):
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            runner = CommandRunner(g, lp, oreg, mutex, worker=_FakeWorker(None))
            local_fn = _make_platform_atom_local_fn("platform_validate")
            r = await runner.run_command(
                "platform_validate", {}, sid, BOARD, "/tmp/p",
                executor="local", local_fn=local_fn,
                timeout_s=60.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_FAILED
            assert "ADAPTER_NOT_READY" in str(l2.previous_operation.get("error"))
        finally:
            self._teardown(rt, g)

    @pytest.mark.asyncio
    async def test_local_fn_maps_platform_error_to_envelope(self):
        """A PlatformError raised inside the atom is converted to the standard
        error envelope (TOOL_ERROR + stable reason_code) before the CommandRunner
        persists it — matching the platform_generate error contract."""
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        local_fn = _make_platform_atom_local_fn("platform_validate")
        adapter = _FakeAdapter([{"output": "ERROR: [BD 41-217] unconnected"}])
        out = await local_fn(adapter)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BD_VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_local_fn_maps_adapter_error_to_envelope(self):
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        local_fn = _make_platform_atom_local_fn("platform_create_design")
        adapter = _RaisingAdapter(RuntimeError("boom"))
        out = await local_fn(adapter, name="p", part="x", project_path="d:/p")
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "ADAPTER_NOT_READY"

    @pytest.mark.asyncio
    async def test_connect_reset_adapter_injected_and_no_stage_advance(self):
        from mcps.zynq_mcp.dispatcher import _make_platform_atom_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            fake_adapter = _FakeAdapter([{"output": ""}])
            runner = CommandRunner(g, lp, oreg, mutex, worker=_FakeWorker(fake_adapter))
            local_fn = _make_platform_atom_local_fn("platform_connect_reset")
            r = await runner.run_command(
                "platform_connect_reset",
                {"source": "rst_ps7_50M/peripheral_aresetn",
                 "targets": ["axi_gpio_led/s_axi_aresetn"]},
                sid, BOARD, "/tmp/p", executor="local", local_fn=local_fn,
                timeout_s=60.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            tcl = fake_adapter.calls[0][1]["command"]
            assert tcl == ("connect_bd_net [get_bd_pins rst_ps7_50M/peripheral_aresetn] "
                           "[get_bd_pins axi_gpio_led/s_axi_aresetn]")
            # next_stage=None -> workflow stage stays PLATFORM_DESIGN
            assert l2.context["current_stage"] == "PLATFORM_DESIGN"
        finally:
            self._teardown(rt, g)


class TestExportManifestCommandDispatch:
    """platform_export_manifest routed through the dispatcher command path.

    Verifies the new board_profile_sha256 context injection: the dispatcher
    strips session transport keys and re-injects project_path / board_id /
    board_profile_sha256 from the ledger context before the atom runs.
    """

    def _dispatch_once(self, tmp_path, stage="PLATFORM_DESIGN"):
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        proj = tmp_path / "proj"
        (proj / "hdl").mkdir(parents=True)
        (proj / "hdl" / "platform_bd_wrapper.v").write_text(
            "module platform_bd_wrapper(); endmodule")
        (proj / "platform.xsa").write_bytes(b"\x78\x73\x61")

        rt = Path(tempfile.mkdtemp())
        g = InstanceGuard(rt, "ws-plat-export"); g.determine_role()
        lp = rt / "l.json"
        sid = f"session-{uuid.uuid4().hex[:8]}"

        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-plat-export"
            l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
            l.context["session_id"] = sid; l.context["board_id"] = BOARD
            l.context["project_path"] = str(proj)
            l.context["board_profile_sha256"] = _SHA
            l.context["board_package_revision"] = _SHA
            l.context["expected_board_revision"] = _SHA
            l.context["current_stage"] = stage
            return l

        ledger_transaction(g, lp, _init)
        oreg = OperationRegistry(); mutex = DomainExecutionMutex()
        fake_adapter = _FakeAdapter([
            {"output": "1"},
            {"output": "processing_system7_0 smartconnect_0 axi_gpio_led"},
            {"output": "processing_system7_0/M_AXI_GP0 axi_gpio_led/S_AXI/reg0 "
                        "0x0000000041200000 64K"},
            {"output": "processing_system7_0/FCLK_CLK0\nsmartconnect_0/aclk"},
            {"output": "2023.1"},
        ])
        runner = CommandRunner(g, lp, oreg, mutex, worker=_FakeWorker(fake_adapter))
        disp = ZynqDispatcher(ExecutionLedger(), oreg, g, lp,
                              _FakeWorker(fake_adapter), cmd_runner=runner)
        return rt, g, lp, disp, proj, fake_adapter

    @pytest.mark.asyncio
    async def test_export_manifest_routes_through_command_path(self, tmp_path):
        rt, g, lp, disp, proj, fake_adapter = self._dispatch_once(tmp_path)
        try:
            msgs = await disp.dispatch("platform_export_manifest", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "success", data
            oid = data["data"]["operation_id"]
            l2 = await TestCommandRunnerInjection._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            # the atom received the session context keys via injection
            assert fake_adapter.calls[0][1]["command"] == "puts [llength [get_bd_designs -quiet]]"
            # the persisted manifest used the injected board_profile_sha256
            mdir = proj / "manifests" / "platform"
            assert mdir.is_dir()
            files = list(mdir.glob("sha256_*.json"))
            assert len(files) == 1
            with open(files[0], encoding="utf-8") as f:
                manifest = json.load(f)
            assert manifest["board_profile_sha256"] == _SHA
            assert manifest["xsa_path"] == "platform.xsa"
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_export_manifest_success_advances_stage(self, tmp_path):
        """B11 phase 2 decision (a): platform_export_manifest advances
        PLATFORM_DESIGN → PL_GENERATE and publishes platform_revision into
        the session context (the revision pl_generate_system_top binds)."""
        rt, g, lp, disp, proj, fake_adapter = self._dispatch_once(tmp_path)
        try:
            msgs = await disp.dispatch("platform_export_manifest", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "success", data
            oid = data["data"]["operation_id"]
            l2 = await TestCommandRunnerInjection._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            assert l2.context["current_stage"] == "PL_GENERATE"
            assert l2.context.get("platform_revision", "").startswith("sha256:")
            ev = l2.previous_operation.get("completion_evidence") or {}
            assert ev.get("stage_advanced_from") == "PLATFORM_DESIGN"
            assert ev.get("stage_advanced_to") == "PL_GENERATE"
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_export_manifest_rejected_when_stage_not_platform_design(self, tmp_path):
        """The stage gate admits platform_export_manifest only from
        PLATFORM_DESIGN — a later-stage call fails closed with
        STAGE_PREREQUISITE_UNMET and the atom never runs (frozen stage
        machine cannot be pushed forward illegally)."""
        rt, g, lp, disp, proj, fake_adapter = self._dispatch_once(
            tmp_path, stage="PL_BUILD")
        try:
            msgs = await disp.dispatch("platform_export_manifest", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "error", data
            assert data["error"]["details"]["reason_code"] == "STAGE_PREREQUISITE_UNMET"
            assert fake_adapter.calls == []  # the atom never ran
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_removed_shortcut_rejected_as_unknown_tool(self, tmp_path):
        """B11 phase 2: the old shortcut platform_generate is gone from the
        public contract — dispatching it fails closed with UNKNOWN_TOOL (the
        stage-advance old path is correctly rejected)."""
        rt, g, lp, disp, proj, fake_adapter = self._dispatch_once(tmp_path)
        try:
            msgs = await disp.dispatch("platform_generate", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "error"
            assert data["error"]["details"]["reason_code"] == "UNKNOWN_TOOL"
            assert fake_adapter.calls == []
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
#  Dispatcher query path (platform_get_status / platform_list_ips)
#  ═══════════════════════════════════════════════════════════════════

class TestDispatcherQueryPath:
    def _make_dispatcher(self, worker_adapter):
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        rt = Path(tempfile.mkdtemp())
        g = InstanceGuard(rt, "ws-plat-q"); g.determine_role()
        lp = rt / "l.json"
        sid = f"session-{uuid.uuid4().hex[:8]}"

        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-plat-q"
            l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
            l.context["session_id"] = sid; l.context["board_id"] = BOARD
            l.context["board_package_revision"] = _SHA
            l.context["expected_board_revision"] = _SHA
            l.context["project_path"] = "D:/proj"
            l.context["current_stage"] = "PLATFORM_DESIGN"
            return l

        ledger_transaction(g, lp, _init)
        oreg = OperationRegistry()
        disp = ZynqDispatcher(ExecutionLedger(), oreg, g, lp, _FakeWorker(worker_adapter))
        return rt, g, disp

    @pytest.mark.asyncio
    async def test_get_status_routes_through_dispatcher(self):
        adapter = _FakeAdapter([{"output": "proj_x"}, {"output": "2"}])
        rt, g, disp = self._make_dispatcher(adapter)
        try:
            msgs = await disp.dispatch("platform_get_status", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "success"
            assert data["data"]["project_name"] == "proj_x"
            assert data["data"]["ip_count"] == 2
            # the adapter was started via the worker controller and run_tcl sent
            assert adapter.calls[0][1]["command"] == "puts [get_property NAME [current_project]]"
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_list_ips_routes_through_dispatcher(self):
        adapter = _FakeAdapter([{"output": "axi_gpio_led"}])
        rt, g, disp = self._make_dispatcher(adapter)
        try:
            msgs = await disp.dispatch("platform_list_ips", {"filter": "VLNV =~ *gpio*"}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "success"
            assert data["data"]["ips"] == ["axi_gpio_led"]
            assert adapter.calls[0][1]["command"] == "puts [get_bd_cells -filter {VLNV =~ *gpio*}]"
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)

    @pytest.mark.asyncio
    async def test_query_fails_closed_without_session(self):
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher
        rt = Path(tempfile.mkdtemp())
        g = InstanceGuard(rt, "ws-plat-q2"); g.determine_role()
        lp = rt / "l.json"

        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-plat-q2"
            l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
            return l

        ledger_transaction(g, lp, _init)
        oreg = OperationRegistry()
        disp = ZynqDispatcher(ExecutionLedger(), oreg, g, lp, _FakeWorker(_FakeAdapter()))
        try:
            msgs = await disp.dispatch("platform_get_status", {}, True)
            data = json.loads(msgs[0].text)
            assert data["status"] == "error"
            assert data["error"]["details"]["reason_code"] == "NO_ACTIVE_SESSION"
        finally:
            g.release_owner_lock(); shutil.rmtree(str(rt), ignore_errors=True)
