"""
test_platform_atoms.py — B05-R2 Platform atomic APIs (unit, no EDA/hardware).

Exercises the 12 production atom functions in
mcps/zynq_mcp/domains/platform/platform_atoms.py against a fake adapter that
records run_tcl calls. Verifies, per API:
  - the adapter is called with the correct Tcl command;
  - the returned envelope format (success data / error contract);
  - fail-closed error paths (adapter failure, invalid args, validation).

Also verifies registration/routing consistency (every atom is in
capabilities.ALL_TOOLS and dispatcher._ALL_KNOWN) and that the CommandRunner
injects the VivadoAdapter through the _pl_adapter marker without advancing
the workflow stage.
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
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, OP_SUCCEEDED, OP_FAILED,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
)
from mcps.zynq_mcp.domains.platform.platform_domain import PlatformError
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    PLATFORM_ATOM_MAP, PLATFORM_ATOM_TOOL_NAMES,
    PLATFORM_ATOM_COMMAND_TOOL_NAMES, PLATFORM_ATOM_QUERY_TOOL_NAMES,
    platform_create_design, platform_get_status,
    platform_add_ps7, platform_configure_ps7,
    platform_add_ip, platform_list_ips,
    platform_connect_interface, platform_connect_clock, platform_connect_reset,
    platform_set_address, platform_validate,
    platform_generate_wrapper, platform_export_hardware, platform_export_manifest,
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
        assert adapter.calls[0][1]["command"] == "get_property NAME [current_project]"
        assert adapter.calls[1][1]["command"] == "llength [get_bd_cells *]"

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


# ═══════════════════════════════════════════════════════════════════
#  3. IP management
# ═══════════════════════════════════════════════════════════════════

class TestAddIp:
    @pytest.mark.asyncio
    async def test_creates_cell_when_absent(self):
        adapter = _FakeAdapter([{"output": "0"}, {"output": ""}])
        out = await platform_add_ip(adapter,
            vlnv="xilinx.com:ip:axi_gpio:2.0", instance_name="axi_gpio_led",
            properties={"C_GPIO_WIDTH": 4, "C_ALL_OUTPUTS": 1})
        assert out["status"] == "success"
        assert out["data"]["already_exists"] is False
        assert adapter.calls[0][1]["command"] == "llength [get_bd_cells -quiet axi_gpio_led]"
        tcl = _last_tcl(adapter)
        assert "create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_led" in tcl
        assert "set_property -dict" in tcl and "C_GPIO_WIDTH {4}" in tcl

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


class TestListIps:
    @pytest.mark.asyncio
    async def test_returns_cells_without_filter(self):
        adapter = _FakeAdapter([{"output": "processing_system7_0 smartconnect_0 axi_gpio_led"}])
        out = await platform_list_ips(adapter)
        assert out["status"] == "success"
        assert out["data"]["ips"] == ["processing_system7_0", "smartconnect_0", "axi_gpio_led"]
        assert out["data"]["count"] == 3
        assert _last_tcl(adapter) == "get_bd_cells *"

    @pytest.mark.asyncio
    async def test_returns_cells_with_filter(self):
        adapter = _FakeAdapter([{"output": "axi_gpio_led"}])
        out = await platform_list_ips(adapter, filter="VLNV =~ *axi_gpio*")
        assert out["data"]["ips"] == ["axi_gpio_led"]
        assert _last_tcl(adapter) == "get_bd_cells -filter {VLNV =~ *axi_gpio*}"


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
        assert "set_property CONFIG.C_BASEADDR {0x41200000}" in tcl
        assert "get_bd_addr_segs {axi_gpio_led/S_AXI}" in tcl
        assert "C_HIGHADDR" not in tcl

    @pytest.mark.asyncio
    async def test_sets_base_and_computes_highaddr(self):
        adapter = _FakeAdapter()
        out = await platform_set_address(adapter,
            segment="axi_gpio_led/S_AXI", base="0x41200000", size=65536)
        assert out["data"]["size"] == 65536
        tcl = _last_tcl(adapter)
        assert "CONFIG.C_BASEADDR {0x41200000}" in tcl
        assert "CONFIG.C_HIGHADDR {0x4120ffff}" in tcl

    @pytest.mark.asyncio
    async def test_invalid_base_fails_closed(self):
        with pytest.raises(PlatformError) as ei:
            await platform_set_address(_FakeAdapter(),
                segment="s/S_AXI", base="nothex", size=4)
        assert ei.value.reason_code == "INVALID_ARGUMENT"


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
        assert _last_tcl(adapter) == "validate_bd_design"

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
    @pytest.mark.asyncio
    async def test_exports_to_default_path(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "platform.xsa").write_bytes(b"\x78\x73\x61")
        adapter = _FakeAdapter()
        out = await platform_export_hardware(adapter, project_path=str(proj))
        assert out["status"] == "success"
        assert out["data"]["xsa_path"] == str(proj / "platform.xsa")
        assert out["data"]["xsa_sha256"].startswith("sha256:")
        tcl = _last_tcl(adapter)
        assert "write_hw_platform -fixed -force" in tcl
        assert str(proj / "platform.xsa") in tcl

    @pytest.mark.asyncio
    async def test_exports_to_explicit_path(self, tmp_path):
        xsa = tmp_path / "out" / "hw.xsa"
        xsa.parent.mkdir()
        xsa.write_bytes(b"x")
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
        {"output": "1"},  # count_bd_designs
        {"output": "processing_system7_0 smartconnect_0 axi_gpio_led rst_ps7_50M"},
        {"output": "processing_system7_0/M_AXI_GP0 axi_gpio_led/S_AXI/reg0 "
                    "0x0000000041200000 64K"},
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
        # Tcl queries were the 5 expected ones
        cmds = [c[1]["command"] for c in adapter.calls]
        assert cmds[0] == "llength [get_bd_designs -quiet]"
        assert cmds[1] == "get_bd_cells *"
        assert "get_bd_addr_segs" in cmds[2] and "TYPE == master" in cmds[2]
        assert "FCLK_CLK0" in cmds[3]
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


# ═══════════════════════════════════════════════════════════════════
#  Registration / routing consistency (production sources)
# ═══════════════════════════════════════════════════════════════════

class TestRegistrationConsistency:
    def test_atom_count_is_14(self):
        assert len(PLATFORM_ATOM_TOOL_NAMES) == 14
        assert len(PLATFORM_ATOM_MAP) == 14
        assert PLATFORM_ATOM_COMMAND_TOOL_NAMES | PLATFORM_ATOM_QUERY_TOOL_NAMES \
            == PLATFORM_ATOM_TOOL_NAMES

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
            assert fake_adapter.calls[0][1]["command"] == "llength [get_bd_cells -quiet axi_gpio_led]"
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

    def _dispatch_once(self, tmp_path):
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
            l.context["current_stage"] = "PLATFORM_DESIGN"
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
            assert fake_adapter.calls[0][1]["command"] == "llength [get_bd_designs -quiet]"
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
            assert adapter.calls[0][1]["command"] == "get_property NAME [current_project]"
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
            assert adapter.calls[0][1]["command"] == "get_bd_cells -filter {VLNV =~ *gpio*}"
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
