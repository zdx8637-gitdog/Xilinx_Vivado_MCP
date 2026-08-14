"""
test_pl_bridge.py — B08 PL bridge tools (unit, no EDA tools / hardware).

Exercises the production bridge functions in
mcps/zynq_mcp/domains/pl/pl_bridge_tools.py against a fake bridge that records
eval() calls (mirroring the VivadoTclBridge.eval interface). Verifies:
  - the envelope conversion (_bridge_call) for success / Tcl error /
    bridge-failure / missing-bridge;
  - each bridge tool composes the correct Vivado Tcl command and passes the
    correct eval timeout;
  - optional args are dropped when omitted (never forwarded as None);
  - the 4 simulation tools still use the old MCP adapter path
    (_call_old_adapter) and fail closed when only the VivadoTclBridge is
    injected;
  - registration consistency: every pl_* tool in capabilities.ALL_TOOLS has a
    bridge function in PL_TOOL_MAP and vice versa, and every bridge tool is
    routed by the dispatcher (_ALL_KNOWN);
  - the CommandRunner injects the VivadoTclBridge (via the `_pl_bridge`
    marker) and fails closed when no bridge is configured.

These are real behavior tests (production entry points called directly with a
fake transport), not mock-only: the Tcl composition, parsing and envelope
conversion are fully exercised.
"""
import asyncio
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from mcps.common.tool_response import (
    ToolResponse, ErrorDetail, success, error,
)
from mcps.zynq_mcp.adapters.vivado_adapter import (
    AdapterNotReadyError, BridgeError, BridgeTimeoutError,
)
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, OP_ACCEPTED, OP_RUNNING, OP_SUCCEEDED, OP_FAILED,
)
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
)
from mcps.zynq_mcp.domains.pl.pl_bridge_tools import (
    PL_TOOL_MAP, _bridge_call, _call_old, _call_old_adapter,
    pl_create_project, pl_open_checkpoint, pl_close_design,
    pl_generate_target, pl_synthesize, pl_place, pl_route,
    pl_generate_bitstream,
    pl_analyze_timing, pl_analyze_utilization,
    pl_query_cells, pl_query_nets, pl_query_clocks, pl_query_ports,
    pl_get_property, pl_validate_design, pl_get_vivado_info,
    pl_connect_hw_server, pl_get_device_status, pl_program_device,
    pl_program_fpga,
    pl_list_devices, pl_compile_sim, pl_elaborate_sim, pl_run_simulation,
    pl_parse_sim_log,
)


def _reqid():
    return str(uuid.uuid4())


class _FakeVivadoBridge:
    """Records (tcl, timeout_s) for each eval(); returns a canned response.

    Mirrors the VivadoTclBridge.eval() interface used by the PL bridge tools
    (a real bridge is exercised in adapters/vivado/tests/test_vivado_bridge.py
    through the fake-shell subprocess). This recording fake verifies the Tcl
    command each tool composes and the envelope conversion deterministically.
    """

    def __init__(self, response=None, fail_eval=False):
        self.calls = []
        self.response = response or {"status": "success", "data": ""}
        self.fail_eval = fail_eval

    @property
    def ready(self):
        return True

    async def eval(self, tcl, timeout_s=None):
        self.calls.append((tcl, timeout_s))
        if self.fail_eval:
            from mcps.zynq_mcp.adapters.vivado.vivado_bridge import VivadoBridgeError
            raise VivadoBridgeError("simulated vivado crash")
        return self.response


class _RaisingAdapter:
    """Old-adapter stand-in that raises (exercises _call_old error paths)."""

    def __init__(self, exc):
        self._exc = exc

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        raise self._exc


class _FakeAdapter:
    """Old-adapter stand-in for the sim-tool compat path: records
    (name, args, timeout) for each call_tool invocation."""

    def __init__(self, response=None):
        self.calls = []
        self.response = response or success(data={"ok": True})

    async def call_tool(self, name, arguments, *, timeout=30.0, session_id=None):
        self.calls.append((name, dict(arguments), timeout))
        return self.response


class _FakeXsdbBridge:
    """Records eval() calls and returns canned responses, mirroring the
    XsdbBridge interface used by pl_program_fpga (start/stop, ready,
    hw_connected, eval). No real xsdb process is launched."""

    def __init__(self, *, ready=False, hw_connected=False, response=None,
                 fail_eval=False):
        self.calls = []          # (tcl, timeout_s) per eval
        self._started = False
        self._ready = ready
        self._hw_connected = hw_connected
        self.response = response or {"status": "success", "data": "Done"}
        self.fail_eval = fail_eval

    async def start(self, hw_server_url: str = "localhost:3121") -> None:
        self._started = True
        self._ready = True
        if hw_server_url:
            self._hw_connected = True

    async def stop(self) -> None:
        self._started = False
        self._ready = False
        self._hw_connected = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def hw_connected(self) -> bool:
        return self._hw_connected

    async def eval(self, tcl, timeout_s=None):
        self.calls.append((tcl, timeout_s))
        if tcl.lstrip().startswith("connect"):
            self._hw_connected = True
        if self.fail_eval:
            from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError
            raise XsdbBridgeError("simulated xsdb crash")
        return self.response


# ── _bridge_call envelope conversion ───────────────────────────────────────

class TestBridgeCall:
    @pytest.mark.asyncio
    async def test_success_passthrough(self):
        bridge = _FakeVivadoBridge(
            response={"status": "success", "data": "line1\nline2"})
        out = await _bridge_call(bridge, "get_clocks", timeout_s=90.0)
        assert out["status"] == "success"
        assert out["data"] == "line1\nline2"

    @pytest.mark.asyncio
    async def test_bridge_raises_maps_to_bridge_unavailable(self):
        bridge = _FakeVivadoBridge(fail_eval=True)
        out = await _bridge_call(bridge, "get_clocks", timeout_s=90.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_tcl_error_maps_to_tool_error(self):
        bridge = _FakeVivadoBridge(response={
            "status": "error",
            "error": {"code": "XSDM_EVAL_ERROR", "message": "ERROR: synth failed",
                      "details": {"reason_code": "XSDM_TCL_ERROR"}}})
        out = await _bridge_call(bridge, "launch_runs synth_1", timeout_s=3600.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TCL_ERROR"

    @pytest.mark.asyncio
    async def test_process_dead_maps_to_env_error(self):
        bridge = _FakeVivadoBridge(response={
            "status": "error",
            "error": {"code": "XSDM_EVAL_ERROR", "message": "process dead",
                      "details": {"reason_code": "XSDM_PROCESS_DEAD"}}})
        out = await _bridge_call(bridge, "get_clocks", timeout_s=90.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "ENV_ERROR"
        assert out["error"]["details"]["reason_code"] == "VIVADO_PROCESS_DEAD"

    @pytest.mark.asyncio
    async def test_missing_eval_maps_to_bridge_not_ready(self):
        out = await _bridge_call(object(), "get_clocks", timeout_s=90.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_NOT_READY"

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_internal_error(self):
        class _ExplodingBridge:
            async def eval(self, tcl, timeout_s=None):
                raise RuntimeError("boom")
        out = await _bridge_call(_ExplodingBridge(), "get_clocks", timeout_s=90.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "INTERNAL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_CALL_FAILED"


# ── _call_old / _call_old_adapter (old-MCP compat path, sim tools) ────────

class TestCallOldAdapter:
    @pytest.mark.asyncio
    async def test_call_old_success_passthrough(self):
        adapter = _FakeAdapter(response=success(data={"ok": True}))
        out = await _call_old(adapter, "compile_sim", {"sources": []}, 180.0)
        assert out["status"] == "success"
        assert out["data"] == {"ok": True}

    @pytest.mark.asyncio
    async def test_call_old_error_code_and_reason_preserved(self):
        adapter = _FakeAdapter(response=error(
            message="sim failed", code="PL_BUILD_ERROR",
            details={"reason_code": "SIM_COMPILE_FAILED"}))
        out = await _call_old(adapter, "compile_sim", {}, 180.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "PL_BUILD_ERROR"
        assert out["error"]["details"]["reason_code"] == "SIM_COMPILE_FAILED"

    @pytest.mark.asyncio
    async def test_call_old_invalid_error_code_falls_back_to_tool_error(self):
        raw = ToolResponse(status="error", request_id=_reqid(),
                           error=ErrorDetail(code="NOT_A_CODE", message="boom"))
        adapter = _FakeAdapter(response=raw)
        out = await _call_old(adapter, "parse_sim_log", {}, 90.0)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TOOL_ERROR"

    @pytest.mark.asyncio
    async def test_call_old_adapter_not_ready_is_explicit(self):
        adapter = _RaisingAdapter(AdapterNotReadyError("not started"))
        out = await _call_old(adapter, "compile_sim", {}, 180.0)
        assert out["error"]["details"]["reason_code"] == "ADAPTER_NOT_READY"

    @pytest.mark.asyncio
    async def test_call_old_bridge_error_is_explicit(self):
        adapter = _RaisingAdapter(BridgeTimeoutError("'compile_sim' timed out"))
        out = await _call_old(adapter, "compile_sim", {}, 180.0)
        assert out["error"]["details"]["reason_code"] == "BRIDGE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_call_old_unexpected_exception_is_internal_error(self):
        adapter = _RaisingAdapter(RuntimeError("boom"))
        out = await _call_old(adapter, "compile_sim", {}, 180.0)
        assert out["error"]["code"] == "INTERNAL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_CALL_FAILED"

    @pytest.mark.asyncio
    async def test_call_old_adapter_guard_without_call_tool(self):
        # A VivadoTclBridge (no call_tool) cannot run the old-MCP sim tools.
        out = await _call_old_adapter(_FakeVivadoBridge(), "compile_sim", {}, 180.0)
        assert out["status"] == "error"
        assert out["error"]["details"]["reason_code"] == "ADAPTER_NOT_AVAILABLE"


# ── per-tool Tcl composition + timeout ─────────────────────────────────────

# Tools whose Tcl is a composed command (not a simple pass-through fragment):
# covered by the dedicated TestGenerateTarget / TestAsyncMode classes.
_COMMAND_CONSTRUCTING = frozenset(
    {"pl_generate_target", "pl_synthesize", "pl_place", "pl_route"})

# pl_program_fpga runs on the XsdbBridge (`fpga -f`), not the Vivado bridge —
# covered by TestProgramFpga.
_XSDB_BRIDGE_TOOLS = frozenset({"pl_program_fpga"})

# Simulation tools keep the old MCP adapter path (DEFERRED XSim adapter).
_SIM_TOOLS = frozenset({"pl_compile_sim", "pl_elaborate_sim",
                        "pl_run_simulation", "pl_parse_sim_log"})

# tool name -> (Tcl substring that must be present, expected eval timeout)
_TCL_EXPECT = {
    "pl_create_project": ("create_project", 180.0),
    "pl_open_checkpoint": ("open_checkpoint", 360.0),
    "pl_close_design": ("close_design", 90.0),
    "pl_generate_bitstream": ("write_bitstream", 360.0),
    "pl_analyze_timing": ("report_timing_summary", 120.0),
    "pl_analyze_utilization": ("report_utilization", 120.0),
    "pl_query_cells": ("get_cells", 90.0),
    "pl_query_nets": ("get_nets", 90.0),
    "pl_query_clocks": ("get_clocks", 90.0),
    "pl_query_ports": ("get_ports", 90.0),
    "pl_get_property": ("get_property", 90.0),
    "pl_validate_design": ("report_drc", 120.0),
    "pl_get_vivado_info": ("version -short", 90.0),
    "pl_connect_hw_server": ("connect_hw_server", 120.0),
    "pl_get_device_status": ("get_hw_devices", 120.0),
    "pl_program_device": ("program_hw_devices", 180.0),
    "pl_list_devices": ("get_hw_devices", 120.0),
}

_REPR_ARGS = {
    "pl_create_project": {"name": "proj", "part": "xc7z020clg400-2",
                          "sources": ["a.v"], "constraints": ["c.xdc"],
                          "project_dir": "d:/p/vivado/proj"},
    "pl_open_checkpoint": {"dcp_path": "<REALFILE>"},  # replaced per-test
    "pl_close_design": {},
    "pl_generate_target": {"target_type": "synthesis"},
    "pl_synthesize": {"top": "system_top", "flatten": "rebuilt"},
    "pl_place": {"directive": "Explore"},
    "pl_route": {"directive": "Explore"},
    "pl_generate_bitstream": {"path": "d:/p/design.bit", "force": True},
    "pl_analyze_timing": {"clock": "clk_50", "max_paths": 10},
    "pl_analyze_utilization": {"hierarchical": True},
    "pl_query_cells": {"filter": "IS_SEQUENTIAL == true", "hierarchical": True,
                       "properties": ["LOC"]},
    "pl_query_nets": {"filter": "NAME =~ *clk*", "max_items": 100},
    "pl_query_clocks": {},
    "pl_query_ports": {"direction": "IN"},
    "pl_get_property": {"object": "[current_design]", "property": "PART"},
    "pl_validate_design": {},
    "pl_get_vivado_info": {},
    "pl_connect_hw_server": {},
    "pl_get_device_status": {},
    "pl_program_device": {"bitstream_path": "d:/p/design.bit"},
    "pl_list_devices": {},
    "pl_compile_sim": {"sources": ["tb.v"], "sim_dir": "d:/p/sim"},
    "pl_elaborate_sim": {"top": "tb", "sim_dir": "d:/p/sim"},
    "pl_run_simulation": {"top": "tb", "sim_dir": "d:/p/sim",
                          "vcd_path": "d:/p/sim/tb.vcd"},
    "pl_parse_sim_log": {"log_path": "d:/p/sim/tb.log"},
}


class TestBridgeForwarding:
    @pytest.mark.asyncio
    async def test_each_tool_emits_correct_tcl_and_timeout(self, tmp_path):
        dcp = tmp_path / "design.dcp"
        dcp.write_bytes(b"dcp")
        assert (set(PL_TOOL_MAP.keys()) - _XSDB_BRIDGE_TOOLS
                == set(_TCL_EXPECT.keys()) | _COMMAND_CONSTRUCTING | _SIM_TOOLS)
        assert set(PL_TOOL_MAP.keys()) - _XSDB_BRIDGE_TOOLS == set(_REPR_ARGS.keys())
        for tool_name, (fn, default_timeout) in PL_TOOL_MAP.items():
            if tool_name in _COMMAND_CONSTRUCTING or tool_name in _XSDB_BRIDGE_TOOLS \
                    or tool_name in _SIM_TOOLS:
                continue
            if tool_name == "pl_open_checkpoint":
                # pl_open_checkpoint parses the part/name out of the output.
                bridge = _FakeVivadoBridge(response={
                    "status": "success",
                    "data": "PART=xc7z020clg400-2\nNAME=system_top\nOPENED=1"})
                args = {"dcp_path": str(dcp)}
            else:
                bridge = _FakeVivadoBridge()
                args = dict(_REPR_ARGS[tool_name])
            out = await fn(bridge, **args)
            assert out["status"] == "success", f"{tool_name} failed: {out}"
            (tcl, timeout_s) = bridge.calls[-1]
            expect_frag, expect_to = _TCL_EXPECT[tool_name]
            assert expect_frag in tcl, tool_name
            assert timeout_s == expect_to, tool_name

    @pytest.mark.asyncio
    async def test_omitted_optional_args_not_forwarded(self):
        # query tools: omitted filter/hierarchical -> no -filter/-hierarchical
        bridge = _FakeVivadoBridge()
        await pl_query_cells(bridge)
        tcl = bridge.calls[-1][0]
        assert "-filter" not in tcl
        assert "-hierarchical" not in tcl
        bridge = _FakeVivadoBridge()
        await pl_query_ports(bridge)
        assert "-filter" not in bridge.calls[-1][0]
        # analyze_utilization: hierarchical omitted -> no -hierarchical
        bridge = _FakeVivadoBridge()
        await pl_analyze_utilization(bridge)
        assert "-hierarchical" not in bridge.calls[-1][0]

    @pytest.mark.asyncio
    async def test_required_args_forwarded_even_when_others_omitted(self):
        # pl_get_property requires object + property only.
        bridge = _FakeVivadoBridge()
        await pl_get_property(bridge, object="[current_design]", property="PART")
        assert "get_property {PART} {[current_design]}" in bridge.calls[-1][0]


# ── per-tool behavior ──────────────────────────────────────────────────────

class TestToolBehaviors:
    @pytest.mark.asyncio
    async def test_create_project_composes_sources_constraints_top(self):
        bridge = _FakeVivadoBridge()
        await pl_create_project(bridge, name="proj", part="xc7z020clg400-2",
                                sources=["a.v", "b.v"], constraints=["c.xdc"],
                                project_dir="d:/p/vivado/proj", top="system_top")
        tcl = bridge.calls[-1][0]
        assert ("create_project -force {proj} {d:/p/vivado/proj} "
                "-part {xc7z020clg400-2}" in tcl)
        # the sources/constraints lists are braced Tcl lists
        assert "add_files -fileset sources_1" in tcl
        assert "a.v" in tcl and "b.v" in tcl
        assert "set_property top {system_top} [current_fileset]" in tcl
        assert "add_files -fileset constrs_1" in tcl
        assert "c.xdc" in tcl

    @pytest.mark.asyncio
    async def test_create_project_single_files_no_double_brace(self):
        # Regression: wrapping the already-braced path list in an extra brace
        # (``{{a.v}}``) collapses to the literal string ``{a.v}`` for a
        # one-element list, which Vivado rejects with "Illegal file or
        # directory name '{...}'" (Vivado 12-385). Each path must be its own
        # single-braced Tcl word.
        bridge = _FakeVivadoBridge()
        await pl_create_project(bridge, name="proj", part="xc7z020clg400-2",
                                sources=["a.v"], constraints=["c.xdc"],
                                project_dir="d:/p/vivado/proj")
        tcl = bridge.calls[-1][0]
        assert "add_files -fileset sources_1 {a.v}" in tcl
        assert "add_files -fileset constrs_1 {c.xdc}" in tcl
        assert "{{" not in tcl

    @pytest.mark.asyncio
    async def test_create_project_rejects_missing_project_dir(self):
        bridge = _FakeVivadoBridge()
        out = await pl_create_project(bridge, name="p", part="x")
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_open_checkpoint_rejects_missing_file(self):
        bridge = _FakeVivadoBridge()
        out = await pl_open_checkpoint(bridge, dcp_path="d:/p/missing.dcp")
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert out["error"]["details"]["reason_code"] == "FILE_NOT_FOUND"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_open_checkpoint_parses_part_and_name(self, tmp_path):
        dcp = tmp_path / "design.dcp"
        dcp.write_bytes(b"dcp")
        bridge = _FakeVivadoBridge(response={
            "status": "success",
            "data": "PART=xc7z020clg400-2\nNAME=system_top\nOPENED=1"})
        out = await pl_open_checkpoint(bridge, dcp_path=str(dcp))
        assert out["status"] == "success"
        assert out["data"]["part"] == "xc7z020clg400-2"
        assert out["data"]["design_name"] == "system_top"

    @pytest.mark.asyncio
    async def test_open_checkpoint_no_part_fails_closed(self, tmp_path):
        dcp = tmp_path / "design.dcp"
        dcp.write_bytes(b"dcp")
        bridge = _FakeVivadoBridge(response={"status": "success", "data": "OPENED=1"})
        out = await pl_open_checkpoint(bridge, dcp_path=str(dcp))
        assert out["status"] == "error"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TCL_ERROR"

    @pytest.mark.asyncio
    async def test_analyze_timing_derives_timing_met(self):
        row = ("WNS(ns)      TNS(ns)  TNS Failing Endpoints  WHS(ns)      THS(ns)  "
               "THS Failing Endpoints\n"
               "-------      -------  ---------------------  -------      -------  "
               "---------------------\n"
               "  0.120        0.000        0                  0.050        0.000        0")
        # WNS >= 0 -> timing_met True
        b1 = _FakeVivadoBridge(response={"status": "success", "data": row})
        out1 = await pl_analyze_timing(b1)
        assert out1["status"] == "success"
        assert out1["data"]["wns_ns"] == 0.120
        assert out1["data"]["timing_met"] is True
        # WNS < 0 -> timing_met False (bitstream gate must reject)
        b2 = _FakeVivadoBridge(
            response={"status": "success", "data": row.replace("0.120", "-0.500")})
        out2 = await pl_analyze_timing(b2)
        assert out2["data"]["timing_met"] is False
        # No WNS row (unconstrained) -> timing_met True + note
        b3 = _FakeVivadoBridge(
            response={"status": "success", "data": "no timing results"})
        out3 = await pl_analyze_timing(b3)
        assert out3["status"] == "success"
        assert out3["data"]["timing_met"] is True
        assert out3["data"]["note"] == "no_user_timing_constraints"

    @pytest.mark.asyncio
    async def test_analyze_timing_error_passthrough(self):
        bridge = _FakeVivadoBridge(response={
            "status": "error",
            "error": {"code": "XSDM_EVAL_ERROR", "message": "no design open",
                      "details": {"reason_code": "XSDM_TCL_ERROR"}}})
        out = await pl_analyze_timing(bridge)
        assert out["status"] == "error"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TCL_ERROR"

    @pytest.mark.asyncio
    async def test_analyze_utilization_parses_table(self):
        table = (
            "+-------------------------+------+-------+------------+-----------+-------+\n"
            "| Site Type               | Used | Fixed | Prohibited | Available | Util% |\n"
            "+-------------------------+------+-------+------------+-----------+-------+\n"
            "| Slice LUTs              |  2106 |     0 |          0 |     53200 |   3.96 |\n"
            "| Slice Registers         |  1234 |     0 |          0 |    106400 |   1.16 |\n"
            "| Block RAM Tile          |     2 |     0 |          0 |       140 |   1.43 |\n"
            "| DSPs                    |     0 |     0 |          0 |       220 |   0.00 |\n"
            "| BUFGCTRL                |     1 |     0 |          0 |        32 |   3.13 |\n"
            "| bonded IOB              |   100 |     0 |          0 |       200 |  50.00 |\n"
            "+-------------------------+------+-------+------------+-----------+-------+\n")
        bridge = _FakeVivadoBridge(response={"status": "success", "data": table})
        out = await pl_analyze_utilization(bridge)
        assert out["status"] == "success"
        d = out["data"]
        assert d["slice_lut"]["used"] == 2106
        assert d["slice_reg"]["available"] == 106400
        assert d["block_ram"]["pct"] == 1.43
        assert d["dsp"]["used"] == 0
        assert d["bufg"]["pct"] == 3.13
        assert d["io"]["used"] == 100

    @pytest.mark.asyncio
    async def test_validate_design_parses_checks(self):
        text = ("__CHECK__|clocks_defined|3\n"
                "__CHECK__|part|xc7z020clg400-2\n"
                "__CHECK__|timing_summary|ok\n"
                "__CHECK__|drc_errors|0")
        bridge = _FakeVivadoBridge(response={"status": "success", "data": text})
        out = await pl_validate_design(bridge)
        assert out["status"] == "success"
        assert out["data"]["status"] == "passed"
        assert out["data"]["failed_count"] == 0
        # a DRC error flips the status to failed
        bad = text.replace("drc_errors|0", "drc_errors|2")
        bridge2 = _FakeVivadoBridge(response={"status": "success", "data": bad})
        out2 = await pl_validate_design(bridge2)
        assert out2["data"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_query_cells_parses_rows(self):
        data = ("__CELL__|u_ff|FDRE|1|LOC=SLICE_X0Y0\n"
                "__CELL__|u_lut|LUT6|0|")
        bridge = _FakeVivadoBridge(response={"status": "success", "data": data})
        out = await pl_query_cells(bridge)
        assert out["status"] == "success"
        cells = out["data"]["cells"]
        assert cells[0]["name"] == "u_ff"
        assert cells[0]["is_sequential"] is True
        assert cells[0]["properties"] == {"LOC": "SLICE_X0Y0"}
        assert cells[1]["is_sequential"] is False

    @pytest.mark.asyncio
    async def test_query_cells_forwards_filter_and_hierarchical(self):
        bridge = _FakeVivadoBridge()
        await pl_query_cells(bridge, filter="IS_SEQUENTIAL == true",
                             hierarchical=True, properties=["LOC"])
        tcl = bridge.calls[-1][0]
        assert "-filter {IS_SEQUENTIAL == true}" in tcl
        assert "-hierarchical" in tcl

    @pytest.mark.asyncio
    async def test_query_ports_forwards_direction_filter(self):
        bridge = _FakeVivadoBridge()
        await pl_query_ports(bridge, direction="IN")
        assert '-filter {DIRECTION == "IN"}' in bridge.calls[-1][0]

    @pytest.mark.asyncio
    async def test_connect_hw_server_detects_failed_connect(self):
        bridge = _FakeVivadoBridge(response={
            "status": "success",
            "data": "ERROR: could not connect to server"})
        out = await pl_connect_hw_server(bridge)
        assert out["status"] == "error"
        assert out["error"]["code"] == "JTAG_ERROR"
        assert out["error"]["details"]["reason_code"] == "HW_SERVER_UNREACHABLE"


# ── pl_generate_target ─────────────────────────────────────────────────────

class TestGenerateTarget:
    @pytest.mark.asyncio
    async def test_default_synthesis_command(self):
        bridge = _FakeVivadoBridge()
        out = await pl_generate_target(bridge)
        assert out["status"] == "success"
        (tcl, timeout_s) = bridge.calls[-1]
        assert "generate_target synthesis [get_files *.bd]" in tcl
        assert timeout_s == 300.0

    @pytest.mark.asyncio
    async def test_explicit_target_type_all(self):
        bridge = _FakeVivadoBridge()
        await pl_generate_target(bridge, target_type="all")
        assert "generate_target all [get_files *.bd]" in bridge.calls[-1][0]

    @pytest.mark.asyncio
    async def test_invalid_target_type_fails_closed(self):
        bridge = _FakeVivadoBridge()
        out = await pl_generate_target(bridge, target_type="not-a-target")
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert out["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert bridge.calls == []  # never reached the bridge

    @pytest.mark.asyncio
    async def test_non_string_target_type_fails_closed(self):
        bridge = _FakeVivadoBridge()
        out = await pl_generate_target(bridge, target_type=123)
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_bridge_error_envelope_passthrough(self):
        bridge = _FakeVivadoBridge(response={
            "status": "error",
            "error": {"code": "XSDM_EVAL_ERROR", "message": "BD generate failed",
                      "details": {"reason_code": "XSDM_TCL_ERROR"}}})
        out = await pl_generate_target(bridge)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TCL_ERROR"


# ── pl_synthesize / pl_place / pl_route: async (long-run) mode ────────────

class TestAsyncMode:
    """Synth/place/route run in async long-run mode — a composed
    launch_runs/wait_on_run Tcl block (NOT the blocking synth_design /
    place_design / route_design bridge calls, which would time out the bridge
    on a 5-30 min run)."""

    @pytest.mark.asyncio
    async def test_synthesize_uses_async_mode(self):
        bridge = _FakeVivadoBridge()
        out = await pl_synthesize(bridge)
        assert out["status"] == "success"
        (tcl, timeout_s) = bridge.calls[-1]
        assert "launch_runs synth_1 -jobs 4" in tcl
        assert "wait_on_run synth_1" in tcl
        assert "open_run synth_1" in tcl
        assert "synth_design" not in tcl
        assert "place_design" not in tcl and "route_design" not in tcl
        assert timeout_s == 3600.0

    @pytest.mark.asyncio
    async def test_synthesize_forwards_top_and_drops_flatten(self):
        bridge = _FakeVivadoBridge()
        await pl_synthesize(bridge, top="system_top")
        tcl = bridge.calls[-1][0]
        assert "set_property top {system_top} [current_fileset]" in tcl
        assert "launch_runs synth_1 -jobs 4" in tcl
        assert "launch_runs synth_1 -jobs 4 -top" not in tcl
        bridge2 = _FakeVivadoBridge()
        await pl_synthesize(bridge2)
        tcl2 = bridge2.calls[-1][0]
        assert "launch_runs synth_1 -jobs 4\nwait_on_run synth_1" in tcl2
        assert "-top" not in tcl2 and "flatten" not in tcl2

    @pytest.mark.asyncio
    async def test_synthesize_observer_path_sets_top_before_launch(self):
        class _ObserverBridge:
            def __init__(self):
                self.kwargs = None

            async def run_vivado_run(self, **kwargs):
                self.kwargs = kwargs
                return {"status": "success", "data": {"vendor_status":
                                                         "synth_design Complete!"}}

        bridge = _ObserverBridge()
        out = await pl_synthesize(bridge, top="system_top")
        assert out["status"] == "success"
        assert bridge.kwargs["launch_tcl"] == (
            "set_property top {system_top} [current_fileset]\n"
            "launch_runs synth_1 -jobs 4")
        assert bridge.kwargs["run_name"] == "synth_1"

    @pytest.mark.asyncio
    async def test_bitstream_observer_creates_public_output_parent(self,
                                                                   tmp_path):
        class _ObserverBridge:
            async def run_vivado_run(self, **kwargs):
                return {"status": "error", "error": {
                    "code": "TOOL_ERROR", "message": "stop after parent gate"}}

        output = tmp_path / "new" / "bitstream" / "gpio.bit"
        result = await pl_generate_bitstream(
            _ObserverBridge(), path=str(output), force=True)

        assert result["status"] == "error"
        assert output.parent.is_dir()

    @pytest.mark.asyncio
    async def test_bitstream_observer_fails_if_requested_file_was_not_copied(
            self, tmp_path):
        class _ObserverBridge:
            def set_current_step(self, step):
                self.step = step

            async def run_vivado_run(self, **kwargs):
                return {"status": "success", "data": {}}

            async def eval(self, tcl, timeout_s=None):
                return {"status": "success", "data": "BIT_DONE"}

        output = tmp_path / "bitstream" / "gpio.bit"
        result = await pl_generate_bitstream(
            _ObserverBridge(), path=str(output), force=True)

        assert result["status"] == "error"
        assert result["error"]["details"]["reason_code"] == \
            "BITSTREAM_NOT_FOUND"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_top", ["", "a b", "a}", "a;exit", True])
    async def test_synthesize_rejects_unsafe_top(self, bad_top):
        bridge = _FakeVivadoBridge()
        out = await pl_synthesize(bridge, top=bad_top)
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_place_uses_async_mode(self):
        bridge = _FakeVivadoBridge()
        out = await pl_place(bridge)
        assert out["status"] == "success"
        (tcl, timeout_s) = bridge.calls[-1]
        assert "launch_runs impl_1 -to_step place_design" in tcl
        assert "wait_on_run impl_1" in tcl
        assert "route_design" not in tcl
        assert timeout_s == 3600.0

    @pytest.mark.asyncio
    async def test_route_uses_async_mode(self):
        bridge = _FakeVivadoBridge()
        out = await pl_route(bridge)
        assert out["status"] == "success"
        (tcl, timeout_s) = bridge.calls[-1]
        assert "launch_runs impl_1 -to_step route_design" in tcl
        assert "wait_on_run impl_1" in tcl
        assert "open_run impl_1" in tcl
        assert timeout_s == 3600.0

    @pytest.mark.asyncio
    async def test_synthesize_failure_maps_to_error(self):
        bridge = _FakeVivadoBridge(response={
            "status": "error",
            "error": {"code": "XSDM_EVAL_ERROR", "message": "ERROR: synth failed",
                      "details": {"reason_code": "XSDM_TCL_ERROR"}}})
        out = await pl_synthesize(bridge)
        assert out["status"] == "error"
        assert out["error"]["details"]["reason_code"] == "VIVADO_TCL_ERROR"


# ── pl_program_fpga: XSDB `fpga -f` (B07 fix, Zynq-7020 standard path) ────

class TestProgramFpga:
    """pl_program_fpga programs the FPGA via XSDB `fpga -f` on the
    XsdbBridge (NOT the Vivado bridge). Verifies the bridge call sequence
    (auto-connect then fpga -f), the envelope conversion, and the fail-closed
    error paths."""

    @staticmethod
    def _make_bitstream(tmp_path):
        p = tmp_path / "design.bit"
        p.write_bytes(b"\xff\x00\x01bit")
        return str(p)

    @pytest.mark.asyncio
    async def test_success_connects_then_programs(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(ready=True, hw_connected=False,
                                 response={"status": "success", "data": "Done"})
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "success"
        assert out["data"]["status"] == "programmed"
        assert out["data"]["bitstream_path"] == bit
        assert out["data"]["output"] == "Done"
        assert bridge.calls[0][0] == "connect -url tcp:localhost:3121"
        assert bridge.calls[0][1] is None  # default eval timeout for connect
        # tmp_path is backslash-form on Windows; the Tcl must use forward
        # slashes (P1-C: Tcl would otherwise treat ``\f``/``\b`` as escapes).
        assert bridge.calls[1][0] == f"fpga -f {bit.replace('\\', '/')}"
        assert bridge.calls[1][1] == 120.0

    @pytest.mark.asyncio
    async def test_already_connected_skips_connect(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(ready=True, hw_connected=True)
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "success"
        assert len(bridge.calls) == 1
        assert bridge.calls[0][0] == f"fpga -f {bit.replace('\\', '/')}"

    @pytest.mark.asyncio
    async def test_file_not_found_fails_closed(self):
        bridge = _FakeXsdbBridge(ready=True, hw_connected=True)
        out = await pl_program_fpga(bridge, bitstream_path="d:/p/missing.bit")
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert out["error"]["details"]["reason_code"] == "FILE_NOT_FOUND"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_empty_path_fails_closed(self):
        bridge = _FakeXsdbBridge(ready=True, hw_connected=True)
        out = await pl_program_fpga(bridge, bitstream_path="")
        assert out["status"] == "error"
        assert out["error"]["code"] == "INVALID_ARGUMENT"
        assert out["error"]["details"]["reason_code"] == "INVALID_ARGUMENT"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_bridge_not_ready_fails_closed(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(ready=False)
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "error"
        assert out["error"]["code"] == "TOOL_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_NOT_READY"
        assert bridge.calls == []

    @pytest.mark.asyncio
    async def test_connect_failure_is_hw_server_unreachable(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(
            ready=True, hw_connected=False,
            response={"status": "error", "error": {
                "code": "XSDM_EVAL_ERROR",
                "message": "could not connect", "details": {}}})
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "error"
        assert out["error"]["code"] == "ENV_ERROR"
        assert out["error"]["details"]["reason_code"] == "HW_SERVER_UNREACHABLE"
        assert len(bridge.calls) == 1  # never reached fpga -f

    @pytest.mark.asyncio
    async def test_tcl_failure_is_program_failed(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(
            ready=True, hw_connected=True,
            response={"status": "error", "error": {
                "code": "XSDM_EVAL_ERROR",
                "message": "fpga -f failed: ERROR", "details": {}}})
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "error"
        assert out["error"]["code"] == "JTAG_ERROR"
        assert out["error"]["details"]["reason_code"] == "PROGRAM_FAILED"

    @pytest.mark.asyncio
    async def test_bridge_crash_is_jtag_error(self, tmp_path):
        bit = self._make_bitstream(tmp_path)
        bridge = _FakeXsdbBridge(ready=True, hw_connected=True, fail_eval=True)
        out = await pl_program_fpga(bridge, bitstream_path=bit)
        assert out["status"] == "error"
        assert out["error"]["code"] == "JTAG_ERROR"
        assert out["error"]["details"]["reason_code"] == "BRIDGE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_windows_backslash_path_normalized_in_tcl(self, tmp_path):
        # P1-C: a Windows bitstream path with `\f` / `\b` segments (e.g.
        # `D:\fpga\gpio_b08.bit`) must be normalized to forward slashes before
        # it is embedded in the `fpga -f` Tcl word. Tcl interprets backslash
        # escapes inside unquoted command words — `\f` becomes form feed
        # (0x0C), `\b` backspace (0x08) — so the un-normalized path can never
        # be opened by xsdb. On Windows the ``fpga`` subdir exercises the `\f`
        # corruption; on POSIX the path has no backslashes and the assertion
        # reduces to identity (still valid).
        bit_dir = tmp_path / "fpga"
        bit_dir.mkdir()
        bit = bit_dir / "gpio_b08.bit"
        bit.write_bytes(b"\xff\x00\x01bit")
        bridge = _FakeXsdbBridge(ready=True, hw_connected=True)
        out = await pl_program_fpga(bridge, bitstream_path=str(bit))
        assert out["status"] == "success"
        normalized = str(bit).replace("\\", "/")
        tcl = bridge.calls[0][0]
        assert tcl == f"fpga -f {normalized}"
        assert "\\" not in tcl


# ── simulation tools (old-MCP compat path, DEFERRED XSim adapter) ──────────

class TestSimToolsOldAdapter:
    @pytest.mark.asyncio
    async def test_compile_sim_uses_old_adapter(self):
        adapter = _FakeAdapter()
        out = await pl_compile_sim(adapter, sources=["tb.v"], sim_dir="d:/p/sim")
        assert out["status"] == "success"
        assert adapter.calls[-1] == ("compile_sim",
                                     {"sources": ["tb.v"], "sim_dir": "d:/p/sim"},
                                     180.0)

    @pytest.mark.asyncio
    async def test_elaborate_sim_uses_old_adapter(self):
        adapter = _FakeAdapter()
        await pl_elaborate_sim(adapter, top="tb", sim_dir="d:/p/sim")
        assert adapter.calls[-1] == ("elaborate_sim",
                                     {"top": "tb", "sim_dir": "d:/p/sim"}, 180.0)

    @pytest.mark.asyncio
    async def test_run_simulation_optional_vcd_dropped(self):
        adapter = _FakeAdapter()
        await pl_run_simulation(adapter, top="tb", sim_dir="d:/p/sim")
        assert adapter.calls[-1] == ("run_simulation",
                                     {"top": "tb", "sim_dir": "d:/p/sim"}, 180.0)

    @pytest.mark.asyncio
    async def test_parse_sim_log_uses_old_adapter(self):
        adapter = _FakeAdapter()
        await pl_parse_sim_log(adapter, log_path="d:/p/sim/tb.log")
        assert adapter.calls[-1] == ("parse_sim_log",
                                     {"log_path": "d:/p/sim/tb.log"}, 90.0)

    @pytest.mark.asyncio
    async def test_sim_tools_fail_closed_without_old_adapter(self):
        # A VivadoTclBridge (no call_tool) cannot run xvlog/xelab/xsim.
        bridge = _FakeVivadoBridge()
        out = await pl_compile_sim(bridge, sources=["tb.v"], sim_dir="d:/p/sim")
        assert out["status"] == "error"
        assert out["error"]["details"]["reason_code"] == "ADAPTER_NOT_AVAILABLE"
        assert bridge.calls == []


# ── registration / routing consistency (production sources) ───────────────

class TestRegistrationConsistency:
    def test_pl_bridge_tools_match_capabilities(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        pl_registered = {t.name for t in ALL_TOOLS if t.name.startswith("pl_")}
        assert len(PL_TOOL_MAP) == 26
        # every pl_* tool except pl_generate_system_top must have a bridge fn
        assert set(pl_registered) - {"pl_generate_system_top"} == set(PL_TOOL_MAP.keys())

    def test_bridge_tools_routed_in_dispatcher(self):
        from mcps.zynq_mcp.dispatcher import _ALL_KNOWN, _PL_BRIDGE_TOOL_NAMES
        assert _PL_BRIDGE_TOOL_NAMES == frozenset(PL_TOOL_MAP.keys())
        for name in PL_TOOL_MAP:
            assert name in _ALL_KNOWN

    def test_total_tools_is_100(self):
        from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
        # B11 phase 2: platform_generate removed → 100 total (9 control + 91
        # domain); was 101 with the shortcut. B11 ③.1: + assign_addresses /
        # make_external / synthesize → 103 (9 control + 94 domain).
        assert len(ALL_TOOLS) == 103


# ── _execute bridge injection (production CommandRunner path) ─────────────

_SHA = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
_BOARD = "ALINX_AX7020_v1.0"


class TestExecuteBridgeInjection:
    def _setup(self):
        rt = Path(tempfile.mkdtemp())
        g = InstanceGuard(rt, "ws-pl-bridge"); g.determine_role()
        lp = rt / "l.json"
        sid = f"session-{uuid.uuid4().hex[:8]}"
        def _init(l):
            l.instance_id = g.instance_id; l.workspace_id = "ws-pl-bridge"
            l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
            l.context["session_id"] = sid; l.context["board_id"] = _BOARD
            l.context["board_package_revision"] = _SHA
            l.context["expected_board_revision"] = _SHA
            l.context["current_stage"] = "PL_BUILD"
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
    async def test_pl_bridge_injected_and_success_no_stage_advance(self):
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            fake_bridge = _FakeVivadoBridge()
            runner = CommandRunner(g, lp, oreg, mutex, worker=None,
                                   vivado_bridge=fake_bridge)
            local_fn = _make_pl_bridge_local_fn("pl_synthesize")
            r = await runner.run_command(
                "pl_synthesize", {"top": "system_top"},
                sid, _BOARD, "/tmp/p",
                executor="local", local_fn=local_fn,
                timeout_s=720.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            # the VivadoTclBridge was injected; pl_synthesize runs in async
            # mode (launch_runs, NOT the blocking synth_design) and forwards
            # top into the launch command
            (tcl, _to) = fake_bridge.calls[-1]
            assert "set_property top {system_top} [current_fileset]" in tcl
            assert "launch_runs synth_1 -jobs 4" in tcl
            assert "launch_runs synth_1 -jobs 4 -top" not in tcl
            assert "synth_design" not in tcl
            # next_stage=None -> workflow stage stays PL_BUILD
            assert l2.context["current_stage"] == "PL_BUILD"
        finally:
            self._teardown(rt, g)

    @pytest.mark.asyncio
    async def test_pl_bridge_fails_closed_when_bridge_absent(self):
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            runner = CommandRunner(g, lp, oreg, mutex, worker=None,
                                   vivado_bridge=None)
            local_fn = _make_pl_bridge_local_fn("pl_synthesize")
            r = await runner.run_command(
                "pl_synthesize", {},
                sid, _BOARD, "/tmp/p",
                executor="local", local_fn=local_fn,
                timeout_s=720.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_FAILED
            assert "BRIDGE_NOT_READY" in str(l2.previous_operation.get("error"))
        finally:
            self._teardown(rt, g)

    @pytest.mark.asyncio
    async def test_pl_xsdb_tool_injects_xsdb_bridge(self, tmp_path):
        """B07 fix: pl_program_fpga runs on the XsdbBridge (NOT the
        VivadoTclBridge) even though it is registered in PL_TOOL_MAP. The
        bridge is lazy-started by _execute and the fpga -f command reaches
        the xsdb shell."""
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            bit = tmp_path / "design.bit"
            bit.write_bytes(b"bitstream-bytes")
            fake_bridge = _FakeXsdbBridge(hw_connected=False,
                                          response={"status": "success",
                                                    "data": "Done"})
            runner = CommandRunner(g, lp, oreg, mutex, worker=None,
                                   xsdb_bridge=fake_bridge)
            local_fn = _make_pl_bridge_local_fn("pl_program_fpga")
            r = await runner.run_command(
                "pl_program_fpga", {"bitstream_path": str(bit)},
                sid, _BOARD, "/tmp/p",
                executor="local", local_fn=local_fn,
                timeout_s=180.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_SUCCEEDED
            # The XsdbBridge was injected: the fpga -f command was sent to
            # the xsdb shell (auto-connect first, then fpga -f).
            assert fake_bridge.calls[0][0] == "connect -url tcp:localhost:3121"
            # P1-C: Windows backslashes normalized to forward slashes in the
            # `fpga -f` Tcl word (tmp_path is backslash-form on Windows).
            assert fake_bridge.calls[1][0] == f"fpga -f {str(bit).replace('\\', '/')}"
            assert fake_bridge.calls[1][1] == 120.0
        finally:
            self._teardown(rt, g)

    @pytest.mark.asyncio
    async def test_pl_xsdb_tool_fails_closed_when_bridge_absent(self):
        """B07 fix: no XsdbBridge configured -> pl_program_fpga fails closed
        with BRIDGE_NOT_READY (it must not require the VivadoTclBridge)."""
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        rt, g, lp, sid = self._setup()
        try:
            oreg = OperationRegistry(); mutex = DomainExecutionMutex()
            runner = CommandRunner(g, lp, oreg, mutex, worker=None,
                                   xsdb_bridge=None)
            local_fn = _make_pl_bridge_local_fn("pl_program_fpga")
            r = await runner.run_command(
                "pl_program_fpga", {"bitstream_path": "d:/p/x.bit"},
                sid, _BOARD, "/tmp/p",
                executor="local", local_fn=local_fn,
                timeout_s=180.0, next_stage=None)
            oid = r["data"]["operation_id"]
            l2 = await self._wait_terminal(g, lp, oid)
            assert l2 is not None
            assert l2.previous_operation["status"] == OP_FAILED
            assert "BRIDGE_NOT_READY" in str(l2.previous_operation.get("error"))
        finally:
            self._teardown(rt, g)
