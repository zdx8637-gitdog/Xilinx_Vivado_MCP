"""
pl_bridge_tools.py — PL domain bridge tools. B08: migrate the PL bridge tools
off the old two-layer MCP stdio channel onto the direct VivadoTclBridge.

Every function here is a thin, typed wrapper over ``bridge.eval(tcl, ...)``
(the VivadoTclBridge — a direct ``vivado -mode tcl`` subprocess with the same
sentinel-marker pattern as XsdbBridge). No tool reimplements Vivado logic and
no tool depends on the old Xilinx_Vivado_MCP server (which is NOT modified).
The Tcl each tool composes mirrors the old MCP's proven commands (see
Xilinx_Vivado_MCP/tcl_templates.py) but parameterized and parsed fail-closed.

Exceptions kept on the OLD adapter (VivadoAdapter -> old MCP server):
  - pl_program_fpga: runs on the XsdbBridge (`fpga -f`), the canonical
    Zynq-7020 flow (the Vivado hw_manager path cannot find the xc7z020 on the
    ARM-first JTAG chain). domain_runner._execute routes it via
    `_PL_XSDB_TOOLS` (before the `_pl_bridge` branch).
  - the 4 simulation tools (pl_compile_sim / pl_elaborate_sim /
    pl_run_simulation / pl_parse_sim_log): xvlog/xelab/xsim run OUTSIDE
    vivado.exe, so they still need the old Vivado MCP adapter.
    # DEFERRED: migrate to a standalone XSim adapter.
    They fail closed with ADAPTER_NOT_AVAILABLE if only the VivadoTclBridge is
    injected (no call_tool interface).

Error model (fail-closed):
  - Success: the bridge's data is parsed into a structured dict.
  - Tool failure: an `ERROR:` line in Vivado output, or a raised bridge error,
    maps to a canonical ErrorCode (ENV_ERROR / TOOL_ERROR / PL_BUILD_ERROR /
    JTAG_ERROR / INVALID_ARGUMENT) plus a stable `details.reason_code`.
  - Bridge unavailable (not started / PID dead / timeout): explicit TOOL_ERROR
    with a distinct reason_code. Unexpected exceptions surface as
    INTERNAL_ERROR / BRIDGE_CALL_FAILED — never a silent success.

Timeout contract: short query commands use a 30-180s eval timeout; long-run
tools (synth/place/route) use 3600s (the bridge's default). The outer
operation timeout (dispatcher `_PL_BRIDGE_TIMEOUT + 60`) always exceeds the
eval timeout.
"""
from __future__ import annotations

import os
import re

from mcps.common.error_codes import ErrorCode
from mcps.common.tool_response import success, error
from mcps.zynq_mcp.adapters.vivado.vivado_bridge import VivadoBridgeError
from mcps.zynq_mcp.adapters.vivado_adapter import (
    AdapterNotReadyError, BridgeError,
)

_VALID_CODES = {e.value for e in ErrorCode}


def _args(**kwargs) -> dict:
    """Drop None-valued kwargs so omitted schema params are not forwarded."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _invalid(message: str) -> dict:
    return error(message=message, code="INVALID_ARGUMENT",
                 details={"reason_code": "INVALID_ARGUMENT"}).to_dict()


def _tcl_path(p) -> str:
    """Normalize a Windows path to forward slashes for embedding in Tcl."""
    if isinstance(p, str):
        return p.replace("\\", "/")
    return p


def _safe_float(s) -> float | None:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _safe_int(s) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# ── bridge plumbing (direct VivadoTclBridge) ───────────────────────────────

def _eval_error_result(res: dict) -> dict:
    """Convert a bridge eval() error dict to the canonical zynq envelope.

    The bridge reports its own codes (XSDM_EVAL_ERROR / XSDM_PROCESS_DEAD /
    XSDM_TCL_ERROR / XSDM_STDERR_OUTPUT). We re-map them onto the canonical
    ErrorCode + reason_code model. Codes outside the set fall back to
    TOOL_ERROR so `tool_response.error()` validation never crashes.
    """
    err = res.get("error") or {}
    details = err.get("details") or {}
    rc = details.get("reason_code", "VIVADO_TCL_ERROR")
    if rc in ("XSDM_PROCESS_DEAD", "XSDM_WRITE_FAILED"):
        code, out_rc = "ENV_ERROR", "VIVADO_PROCESS_DEAD"
    elif rc == "XSDM_STDERR_OUTPUT":
        code, out_rc = "TOOL_ERROR", "VIVADO_STDERR_OUTPUT"
    else:
        code, out_rc = "TOOL_ERROR", "VIVADO_TCL_ERROR"
    return error(message=err.get("message", "Vivado Tcl error"), code=code,
                 details={"reason_code": out_rc}).to_dict()


async def _bridge_call(bridge, tcl: str, *, timeout_s: float) -> dict:
    """Run one Vivado Tcl command through the bridge, fail-closed.

    Returns a zynq ToolResponse-style dict. Raises are never propagated to
    the caller; bridge-level failures become explicit error envelopes.
    """
    if not hasattr(bridge, "eval"):
        return error(
            message="VivadoTclBridge required (missing eval); is the Vivado "
                    "bridge configured?",
            code="TOOL_ERROR",
            details={"reason_code": "BRIDGE_NOT_READY"}).to_dict()
    try:
        res = await bridge.eval(tcl, timeout_s=timeout_s)
    except VivadoBridgeError as e:
        return error(message=str(e), code="TOOL_ERROR",
                     details={"reason_code": "BRIDGE_UNAVAILABLE"}).to_dict()
    except Exception as e:
        return error(message=str(e), code="INTERNAL_ERROR",
                     details={"reason_code": "BRIDGE_CALL_FAILED"}).to_dict()
    if res.get("status") != "success":
        return _eval_error_result(res)
    return {"status": "success", "data": res.get("data", "")}


# ── old-MCP compat path (simulation tools only) ────────────────────────────

async def _call_old(adapter, tool_name: str, arguments: dict, timeout: float) -> dict:
    """Generic bridge: call the old MCP tool and convert to a zynq envelope."""
    try:
        resp = await adapter.call_tool(tool_name, arguments, timeout=timeout)
    except AdapterNotReadyError:
        return error(message="Vivado worker not ready", code="TOOL_ERROR",
                     details={"reason_code": "ADAPTER_NOT_READY"}).to_dict()
    except BridgeError as e:
        return error(message=str(e), code="TOOL_ERROR",
                     details={"reason_code": "BRIDGE_UNAVAILABLE"}).to_dict()
    except Exception as e:
        return error(message=str(e), code="INTERNAL_ERROR",
                     details={"reason_code": "BRIDGE_CALL_FAILED"}).to_dict()

    if resp.status == "success":
        tr = success(data=resp.data)
        if resp.warnings:
            tr.warnings = list(resp.warnings)
        return tr.to_dict()

    err = resp.error
    msg = err.message if err is not None else "Vivado tool error"
    code = err.code if err is not None else "TOOL_ERROR"
    if code not in _VALID_CODES:
        code = "TOOL_ERROR"
    details = err.details if err is not None else {}
    if not isinstance(details, dict):
        details = {}
    return error(message=msg, code=code,
                 details={"reason_code": details.get("reason_code", "VIVADO_TOOL_ERROR")}).to_dict()


async def _call_old_adapter(bridge_or_adapter, tool_name: str, arguments: dict,
                            timeout: float) -> dict:
    """Compat path for the 4 simulation tools (xvlog/xelab/xsim).

    # DEFERRED: migrate to a standalone XSim adapter.
    These tools run OUTSIDE vivado.exe and still need the old Vivado MCP
    adapter. The injected object is a VivadoTclBridge (which has no
    call_tool); when that happens the tool fails closed with
    ADAPTER_NOT_AVAILABLE instead of crashing.
    """
    if not hasattr(bridge_or_adapter, "call_tool"):
        return error(
            message="Simulation tools require the old Vivado MCP adapter; "
                    "the standalone XSim adapter is DEFERRED",
            code="TOOL_ERROR",
            details={"reason_code": "ADAPTER_NOT_AVAILABLE"}).to_dict()
    return await _call_old(bridge_or_adapter, tool_name, arguments, timeout)


# ── parse helpers (mirror old MCP vivado_tools.py) ─────────────────────────

def _parse_line_markers(raw: str) -> dict[str, str]:
    """Parse `__LINE__ key=value` pairs. Values may be quoted."""
    result: dict[str, str] = {}
    for m in re.finditer(r"__LINE__\s+(\S+)=(.*)", raw):
        result[m.group(1)] = m.group(2).strip()
    return result


def _parse_pipe_lines(raw: str, prefix: str) -> list[list[str]]:
    """Parse batched output lines like `__CELL__|a|b|c` into field lists."""
    rows: list[list[str]] = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(f"{prefix}|"):
            rows.append(s[len(prefix) + 1:].split("|"))
    return rows


_TIMING_ROW_RE = re.compile(
    r"([\d.-]+)\s+([\d.-]+)\s+(\d+)\s+([\d.-]+)\s+([\d.-]+)\s+(\d+)")


def _parse_timing(text: str) -> dict:
    """Parse the WNS/TNS summary row from report_timing_summary text."""
    m = _TIMING_ROW_RE.search(text)
    if not m:
        return {"wns_ns": None, "tns_ns": None, "whs_ns": None,
                "ths_ns": None, "num_endpoints": 0, "num_failing": 0}
    return {
        "wns_ns": _safe_float(m.group(1)),
        "tns_ns": _safe_float(m.group(2)),
        "num_failing": _safe_int(m.group(3)),
        "whs_ns": _safe_float(m.group(4)),
        "ths_ns": _safe_float(m.group(5)),
        "num_endpoints": 0,
    }


_UTIL_ROW_RE = re.compile(
    r"\|\s*(.+?)\s*\|"
    r"\s*(\d+)\s*\|"           # Used
    r"\s*(?:\d+\s*\|)?"        # Fixed (optional — 6-col only)
    r"\s*(?:\d+\s*\|)?"        # Prohibited (optional — 6-col only)
    r"\s*(\d+)\s*\|"           # Available
    r"\s*<?([\d.]+)\s*\|")     # Util% — allow "<0.01"

_UTIL_KEY_MAP = {
    "slice luts": "slice_lut", "slice luts*": "slice_lut",
    "slice lut": "slice_lut",
    "slice registers": "slice_reg", "slice register": "slice_reg",
    "slice reg": "slice_reg",
    "block ram tile": "block_ram", "block ram": "block_ram",
    "ramb36/fifo": "block_ram", "ramb36/fifo*": "block_ram",
    "dsps": "dsp", "dsp": "dsp", "dsp48e1": "dsp",
    "bufgctrl": "bufg", "bufg": "bufg",
    "bonded iob": "io", "bonded io": "io", "iob": "io",
}


def _parse_utilization(text: str) -> dict:
    """Parse report_utilization table text into {resource: {used, available, pct}}."""
    result: dict = {}
    for m in _UTIL_ROW_RE.finditer(text):
        name = m.group(1).strip().lower().rstrip("*")
        used = _safe_int(m.group(2))
        avail = _safe_int(m.group(3))
        pct = _safe_float(m.group(4)) or 0.0
        key = _UTIL_KEY_MAP.get(name)
        if key:
            result[key] = {"used": used, "available": avail, "pct": pct}
    return result


def _parse_cells(text: str) -> list[dict]:
    cells = []
    for fields in _parse_pipe_lines(text, "__CELL__"):
        if len(fields) < 3:
            continue
        extra = {}
        if len(fields) >= 4 and fields[3]:
            for pair in fields[3].split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    extra[k.strip()] = v.strip()
        cells.append({
            "name": fields[0].strip(),
            "cell_type": fields[1].strip(),
            "is_sequential": fields[2].strip().lower() in ("true", "1"),
            "properties": extra,
        })
    return cells


def _parse_nets(text: str) -> list[dict]:
    nets = []
    for fields in _parse_pipe_lines(text, "__NET__"):
        if len(fields) < 4:
            continue
        nets.append({
            "name": fields[0].strip(),
            "driver_pin": fields[1].strip() or None,
            "load_count": _safe_int(fields[2].strip()),
            "is_clock": fields[3].strip().lower() == "true",
        })
    return nets


def _parse_clocks(text: str) -> list[dict]:
    clocks = []
    for fields in _parse_pipe_lines(text, "__CLOCK__"):
        if len(fields) < 2:
            continue
        period = _safe_float(fields[1].strip()) or 0.0
        clocks.append({
            "name": fields[0].strip(),
            "period_ns": period,
            "frequency_mhz": round(1000.0 / period, 3) if period > 0 else 0.0,
            "waveform_rise_ns": _safe_float(fields[2].strip()) or 0.0
                if len(fields) > 2 and fields[2].strip() else 0.0,
            "waveform_fall_ns": _safe_float(fields[3].strip()) or 0.0
                if len(fields) > 3 and fields[3].strip() else 0.0,
            "source_pin": fields[4].strip() if len(fields) > 4
                and fields[4].strip() else None,
        })
    return clocks


def _parse_ports(text: str) -> list[dict]:
    ports = []
    for fields in _parse_pipe_lines(text, "__PORT__"):
        if len(fields) < 3:
            continue
        ports.append({
            "name": fields[0].strip(),
            "direction": fields[1].strip() if len(fields) > 1 else "UNKNOWN",
            "location": fields[2].strip() if len(fields) > 2
                and fields[2].strip() else None,
            "iostandard": fields[3].strip() if len(fields) > 3
                and fields[3].strip() else None,
        })
    return ports


def _parse_property(text: str) -> tuple:
    """Return (value, value_type) — numeric when the value parses as a number."""
    val_str = text.strip()
    try:
        if "." in val_str:
            return float(val_str), "number"
        return int(val_str), "number"
    except ValueError:
        return val_str, "string"


def _parse_devices(text: str) -> dict:
    """Parse DEVICE=/PART=/DONE= lines from get_hw_devices output."""
    devices = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("DEVICE="):
            devices.append({"name": s.split("=", 1)[1]})
        elif s.startswith("PART=") and devices:
            devices[-1]["part"] = s.split("=", 1)[1]
        elif s.startswith("DONE=") and devices:
            devices[-1]["programmed"] = s.split("=", 1)[1]
    return {"device_count": len(devices), "devices": devices}


# ── Tcl templates (mirror old MCP tcl_templates.py, parameterized) ─────────

_GET_CELLS_TCL = r"""
set cells [get_cells {filter} {hierarchical}]
set result {{}}
foreach cell $cells {{
    set name [get_property NAME $cell]
    set prim [get_property PRIMITIVE_TYPE $cell]
    set is_seq [get_property IS_SEQUENTIAL $cell]
    set extra ""
{extra_prop_loop}
    lappend result "__CELL__|$name|$prim|$is_seq|$extra"
}}
puts [join $result "\n"]
"""

_GET_NETS_TCL = r"""
set nets [get_nets {filter} {hierarchical}]
set result {{}}
set count 0
foreach net $nets {{
    if {{$count >= {max_items}}} {{ break }}
    set name [get_property NAME $net]
    set flat [get_property FLAT_PIN_COUNT $net]
    set driver ""
    set pins [get_pins -quiet -of_objects $net -filter {{DIRECTION == OUT}}]
    if {{[llength $pins] > 0}} {{
        set driver [get_property NAME [lindex $pins 0]]
    }}
    set is_clock false
    set clks [get_clocks -quiet -of_objects $net]
    if {{[llength $clks] > 0}} {{ set is_clock true }}
    lappend result "__NET__|$name|$driver|$flat|$is_clock"
    incr count
}}
puts [join $result "\n"]
"""

_GET_CLOCKS_TCL = r"""
set clocks [get_clocks -quiet]
set result {}
foreach clk $clocks {
    set name [get_property NAME $clk]
    set period [get_property PERIOD $clk]
    set waveform [get_property WAVEFORM $clk]
    set rise [lindex $waveform 0]
    set fall [lindex $waveform 1]
    set src ""
    set src_pins [get_pins -quiet -of_objects $clk]
    if {[llength $src_pins] > 0} {
        set src [get_property NAME [lindex $src_pins 0]]
    }
    if {$period > 0} { set freq [expr {1000.0 / $period}] } else { set freq 0 }
    lappend result "__CLOCK__|$name|$period|$rise|$fall|$src"
}
puts [join $result "\n"]
"""

_GET_PORTS_TCL = r"""
set ports [get_ports {filter}]
set result {{}}
foreach port $ports {{
    set name [get_property NAME $port]
    set dir [get_property DIRECTION $port]
    set loc [get_property LOCATION $port]
    set ios [get_property IOSTANDARD $port]
    lappend result "__PORT__|$name|$dir|$loc|$ios"
}}
puts [join $result "\n"]
"""

_OPEN_CHECKPOINT_TCL = r"""
set dcp_file {{{dcp_path}}}
if {{[catch {{open_checkpoint $dcp_file}} err]}} {{
    puts "ERROR: $err"
}} else {{
    set design [current_design]
    puts "PART=[get_property PART $design]"
    puts "NAME=[get_property NAME $design]"
    puts "OPENED=1"
}}
"""

_VIVADO_INFO_TCL = r"""
puts "__LINE__ version=[version -short]"
set vlines [split [version] \n]
puts "__LINE__ build=[lindex $vlines 1]"
puts "__LINE__ edition=Vivado"
"""

_PROGRAM_DEVICE_TCL = r"""
set dev [lindex [get_hw_devices -filter {{PART =~ *xc7z020*}}] 0]
if {{[llength $dev] == 0}} {{
    puts "ERROR: No xc7z020 FPGA device found in JTAG chain"
}} else {{
    current_hw_device $dev
    refresh_hw_device -update_hw_probes false $dev
    set_property PROGRAM.FILE {{{bitstream_path}}} $dev
    set result [program_hw_devices $dev]
    refresh_hw_device $dev
    set done_status [get_property PROGRAM.HW_PROGRAM $dev]
    puts "DONE_STATUS=$done_status"
    puts "PROGRAMMING_COMPLETE"
}}
"""

_DEVICE_LIST_TCL = r"""
open_hw_target
set devs [get_hw_devices]
puts "N_DEVICES=[llength $devs]"
foreach d $devs {
    puts "DEVICE=[get_property NAME $d]"
    catch { puts "PART=[get_property PART $d]" }
}
"""

_DEVICE_STATUS_TCL = r"""
open_hw_target
set devs [get_hw_devices]
puts "N_DEVICES=[llength $devs]"
foreach d $devs {
    puts "DEVICE=[get_property NAME $d]"
    catch { puts "PART=[get_property PART $d]" }
    catch { puts "DONE=[get_property PROGRAM.HW_PROGRAM $d]" }
}
"""

_VALIDATE_TCL = r"""
set d [current_design -quiet]
if {$d eq ""} {
    puts "ERROR: no design is open"
} else {
    set nclk [llength [get_clocks -quiet]]
    puts "__CHECK__|clocks_defined|$nclk"
    puts "__CHECK__|part|[get_property PART $d]"
    set t [report_timing_summary -return_string]
    if {[regexp -line {^\s*[\d.-]+\s+[\d.-]+\s+\d+\s+[\d.-]+\s+[\d.-]+\s+\d+} $t]} {
        puts "__CHECK__|timing_summary|ok"
    } else {
        puts "__CHECK__|timing_summary|none"
    }
    set drc [report_drc -return_string]
    set nerr [regexp -all -line {^ERROR} $drc]
    puts "__CHECK__|drc_errors|$nerr"
}
"""


def _build_query_cells(filter_expr, hierarchical, extra_properties) -> str:
    hier = "-hierarchical" if hierarchical else ""
    filt = f'-filter {{{filter_expr}}}' if filter_expr else ""
    extra_loop = ""
    if extra_properties:
        for prop in extra_properties:
            extra_loop += f'    lappend extra "{prop}="\n'
            extra_loop += f'    catch {{ lappend extra [get_property {prop} $cell] }}\n'
    return _GET_CELLS_TCL.format(filter=filt, hierarchical=hier,
                                 extra_prop_loop=extra_loop)


def _build_query_nets(filter_expr, hierarchical, max_items) -> str:
    hier = "-hierarchical" if hierarchical else ""
    filt = f'-filter {{{filter_expr}}}' if filter_expr else ""
    return _GET_NETS_TCL.format(filter=filt, hierarchical=hier,
                                max_items=int(max_items or 500))


def _build_query_ports(direction) -> str:
    if direction:
        filt = f'-filter {{DIRECTION == "{direction}"}}'
    else:
        filt = ""
    return _GET_PORTS_TCL.format(filter=filt)


def _build_timing_summary(clock) -> str:
    if clock:
        return f"report_timing_summary -return_string -to [get_clocks {{{clock}}}]"
    return "report_timing_summary -return_string"


def _build_synth_run(top) -> str:
    top_cmd = f"set_property top {{{top}}} [current_fileset]\n" if top else ""
    return (f"{top_cmd}launch_runs synth_1 -jobs 4\n"
            f"wait_on_run synth_1\n"
            f'set status [get_property STATUS [get_runs synth_1]]\n'
            f'if {{![string match "*Complete!*" $status]}} '
            f'{{puts "ERROR: synth_1 failed with status: $status"}}\n'
            f"open_run synth_1")


def _build_impl_run(to_step, open_run) -> str:
    lines = [f"launch_runs impl_1 -to_step {to_step}",
             "wait_on_run impl_1",
             'set status [get_property STATUS [get_runs impl_1]]',
             'if {![string match "*Complete!*" $status]} '
             '{puts "ERROR: impl_1 failed with status: $status"}']
    if open_run:
        lines.append("open_run impl_1")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  Engineering (project / checkpoint)
# ═══════════════════════════════════════════════════════════════════

async def pl_create_project(bridge, *, name, part, sources=None, constraints=None,
                            project_dir=None, top=None, force=None) -> dict:
    """Create a Vivado project via direct Tcl.

    project_dir is required (it is not derivable here); it is typically
    `{session.project_path}/vivado/{name}`.
    """
    if not isinstance(name, str) or not name.strip():
        return _invalid("name must be a non-empty string")
    if not isinstance(part, str) or not part.strip():
        return _invalid("part must be a non-empty string")
    if not isinstance(project_dir, str) or not project_dir.strip():
        return _invalid("project_dir must be a non-empty string")
    if sources is not None and (not isinstance(sources, list)
                                or not all(isinstance(s, str) for s in sources)):
        return _invalid("sources must be a list of strings")
    if constraints is not None and (not isinstance(constraints, list)
                                    or not all(isinstance(s, str) for s in constraints)):
        return _invalid("constraints must be a list of strings")
    if force is not None and not isinstance(force, bool):
        return _invalid("force must be a bool")
    # Preserve the old server's create_project default (force=True overwrites
    # an existing project); only an explicit False disables it.
    force = True if force is None else force
    force_flag = " -force" if force else ""
    cmds = [f"create_project{force_flag} {{{name}}} "
            f"{{{_tcl_path(project_dir)}}} -part {{{part}}}"]
    if sources:
        # Each path is its own braced Tcl word. An extra wrapping brace
        # (``{{a}}``) would collapse to the literal string ``{a}`` for a
        # single-element list, which Vivado rejects with "Illegal file or
        # directory name '{...}'" (Vivado 12-385). Dropping the outer brace
        # yields ``add_files {a} {b}`` — one filename per word, correct for
        # both one and many files.
        src_str = " ".join(f"{{{_tcl_path(s)}}}" for s in sources)
        cmds.append(f"add_files -fileset sources_1 {src_str}")
    if top:
        cmds.append(f"set_property top {{{top}}} [current_fileset]")
    if constraints:
        cstr_str = " ".join(f"{{{_tcl_path(c)}}}" for c in constraints)
        cmds.append(f"add_files -fileset constrs_1 {cstr_str}")

    result = await _bridge_call(bridge, "\n".join(cmds), timeout_s=180.0)
    if result["status"] != "success":
        return result
    return success(data={
        "project_name": name, "part": part, "top": top or "top",
        "project_dir": project_dir, "output": result["data"],
    }).to_dict()


async def pl_open_checkpoint(bridge, *, dcp_path) -> dict:
    """Open a Vivado Design Checkpoint (.dcp)."""
    if not isinstance(dcp_path, str) or not dcp_path.strip():
        return _invalid("dcp_path must be a non-empty string")
    if not os.path.isfile(dcp_path):
        return error(message=f"Checkpoint file not found: {dcp_path}",
                     code="INVALID_ARGUMENT",
                     details={"reason_code": "FILE_NOT_FOUND",
                              "dcp_path": dcp_path}).to_dict()
    tcl = _OPEN_CHECKPOINT_TCL.format(dcp_path=_tcl_path(dcp_path))
    result = await _bridge_call(bridge, tcl, timeout_s=360.0)
    if result["status"] != "success":
        return result
    data = result["data"]
    part = ""
    name = ""
    for line in data.splitlines():
        s = line.strip()
        if s.startswith("PART="):
            part = s.split("=", 1)[1].strip()
        elif s.startswith("NAME="):
            name = s.split("=", 1)[1].strip()
    if not part:
        return error(message=f"Failed to open checkpoint: "
                             f"could not determine part for {dcp_path}",
                     code="TOOL_ERROR",
                     details={"reason_code": "VIVADO_TCL_ERROR"}).to_dict()
    return success(data={"part": part, "design_name": name,
                         "checkpoint_path": dcp_path}).to_dict()


async def pl_close_design(bridge) -> dict:
    """Close the open design and clear session state."""
    result = await _bridge_call(bridge, "close_design", timeout_s=90.0)
    if result["status"] != "success":
        return result
    return success(data={"closed": True, "output": result["data"]}).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  Synthesis & implementation
# ═══════════════════════════════════════════════════════════════════

# Vivado `generate_target` target types (UG894). The bridge validates
# target_type against this set before composing raw Tcl (fail-closed).
_TARGET_TYPES = frozenset({
    "all", "synthesis", "implementation", "simulation",
    "instantiation_template",
})


async def pl_generate_target(bridge, *, target_type: str = "synthesis") -> dict:
    """Generate output products for Block Design sources.

    Vivado Tcl: `generate_target {target_type} [get_files *.bd]`.

    This runs OOC synthesis for the BD IPs and produces the output products
    (netlists, constraints) that the BD wrapper references during synthesis.
    Without it, synth_design fails with 'Synth 8-439 module <bd> not found'.
    target_type defaults to 'synthesis'; pass 'all' to also generate
    simulation and instantiation-template products.
    """
    if not isinstance(target_type, str) or target_type not in _TARGET_TYPES:
        return error(message="target_type must be one of: all|synthesis|"
                             "implementation|simulation|instantiation_template",
                     code="INVALID_ARGUMENT",
                     details={"reason_code": "INVALID_ARGUMENT"}).to_dict()
    tcl = f"generate_target {target_type} [get_files *.bd]"
    result = await _bridge_call(bridge, tcl, timeout_s=300.0)
    if result["status"] != "success":
        return result
    return success(data={"target_type": target_type,
                         "output": result["data"]}).to_dict()


async def pl_synthesize(bridge, *, top=None, flatten=None) -> dict:
    """Run synthesis in async (long-run) mode — bridge-safe for 5-30 min runs.

    Composes a multi-line Tcl block:
        set_property top {<top>} [current_fileset]  # when top is supplied
        launch_runs synth_1 -jobs 4
        wait_on_run synth_1
        open_run synth_1
    `launch_runs` + `wait_on_run` keep the Vivado subprocess busy inside its
    own Tcl interpreter, so the bridge never blocks on the run and can never
    hit a sentinel timeout (the synchronous `synth_design` bridge call would
    time out and poison the shell).

    `flatten` is accepted for signature compatibility (the dispatcher may
    forward it) but not forwarded: launch_runs has no flatten option.
    """
    if top is not None and (not isinstance(top, str)
                            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top)
                            is None):
        return _invalid("top must be a plain Verilog module identifier")
    if hasattr(bridge, "run_vivado_run"):
        top_cmd = f"set_property top {{{top}}} [current_fileset]\n" if top else ""
        result = await bridge.run_vivado_run(
            run_name="synth_1",
            launch_tcl=f"{top_cmd}launch_runs synth_1 -jobs 4",
            current_step="SYNTHESIS",
            timeout_s=3600.0,
            open_run=True,
        )
    else:
        # Historical component-test compatibility.  Production uses the O3
        # observer path above and never blocks inside wait_on_run.
        tcl = _build_synth_run(top)
        result = await _bridge_call(bridge, tcl, timeout_s=3600.0)
    if result["status"] != "success":
        return result
    return success(data={"status": "completed", "output": result["data"]}).to_dict()


async def pl_place(bridge, *, directive=None) -> dict:
    """Run placement in async (long-run) mode — bridge-safe for long runs.

    Composes a multi-line Tcl block:
        launch_runs impl_1 -to_step place_design
        wait_on_run impl_1
    The impl_1 run is advanced to the placement step and waited on inside
    Vivado. `directive` is accepted for signature compatibility but not
    forwarded (implementation strategies are set on the run).
    """
    if hasattr(bridge, "run_vivado_run"):
        result = await bridge.run_vivado_run(
            run_name="impl_1",
            launch_tcl="launch_runs impl_1 -to_step place_design",
            current_step="PLACE",
            timeout_s=3600.0,
            open_run=False,
        )
    else:
        tcl = _build_impl_run("place_design", open_run=False)
        result = await _bridge_call(bridge, tcl, timeout_s=3600.0)
    if result["status"] != "success":
        return result
    return success(data={"status": "placed", "output": result["data"]}).to_dict()


async def pl_route(bridge, *, directive=None) -> dict:
    """Run routing in async (long-run) mode — bridge-safe for long runs.

    Composes a multi-line Tcl block:
        launch_runs impl_1 -to_step route_design
        wait_on_run impl_1
        open_run impl_1
    Continues the impl_1 run from placement to routing, waits for completion,
    then opens the routed design so downstream queries
    (pl_analyze_timing / pl_analyze_utilization) can read it.
    `directive` is accepted for signature compatibility but not forwarded.
    """
    if hasattr(bridge, "run_vivado_run"):
        result = await bridge.run_vivado_run(
            run_name="impl_1",
            launch_tcl="launch_runs impl_1 -to_step route_design",
            current_step="ROUTE",
            timeout_s=3600.0,
            open_run=True,
        )
    else:
        tcl = _build_impl_run("route_design", open_run=True)
        result = await _bridge_call(bridge, tcl, timeout_s=3600.0)
    if result["status"] != "success":
        return result
    return success(data={"status": "routed", "output": result["data"]}).to_dict()


async def pl_generate_bitstream(bridge, *, path, force=None) -> dict:
    """Generate a bitstream file."""
    if not isinstance(path, str) or not path.strip():
        return _invalid("path must be a non-empty string")
    if force is not None and not isinstance(force, bool):
        return _invalid("force must be a bool")
    # The public path is an output contract.  Create its parent inside the MCP
    # so black-box callers never need an out-of-band filesystem command merely
    # to stage an otherwise valid output destination.
    output_path = os.path.normpath(path.strip())
    output_parent = os.path.dirname(os.path.abspath(output_path))
    try:
        os.makedirs(output_parent, exist_ok=True)
    except OSError as exc:
        return error(
            message=f"cannot create bitstream output directory: {exc}",
            code="ARTIFACT_STALE",
            details={"reason_code": "BITSTREAM_OUTPUT_UNWRITABLE",
                     "bitstream_path": output_path}).to_dict()
    force_flag = " -force" if force else ""
    if hasattr(bridge, "run_vivado_run"):
        result = await bridge.run_vivado_run(
            run_name="impl_1",
            launch_tcl="launch_runs impl_1 -to_step write_bitstream",
            current_step="BITSTREAM_WRITE",
            timeout_s=1800.0,
            open_run=False,
        )
        if result.get("status") == "success":
            bridge.set_current_step("BITSTREAM_VERIFY")
            # The run writes its canonical bit file in the run directory.
            # Copy that verified run output to the public API's requested
            # path only after STATUS reports Complete!.
            copy_tcl = (
                "set __o3_run [get_runs {impl_1}]\n"
                "set __o3_dir [get_property DIRECTORY $__o3_run]\n"
                "set __o3_top [get_property TOP [get_filesets sources_1]]\n"
                "set __o3_bit [file join $__o3_dir \"$__o3_top.bit\"]\n"
                "if {![file exists $__o3_bit]} {error \"BITSTREAM_NOT_FOUND\"}\n"
                f"if {{[catch {{file copy{force_flag} $__o3_bit "
                f"{{{_tcl_path(output_path)}}}}} __o3_copy_err]}} {{\n"
                "  puts __O3_BIT_COPY_FAILED\n"
                "} else {\n"
                "  puts BIT_DONE\n"
                "}"
            )
            result = await _bridge_call(bridge, copy_tcl, timeout_s=120.0)
            if result.get("status") == "success":
                output = str(result.get("data") or "")
                if "BIT_DONE" not in output or not os.path.isfile(output_path):
                    return error(
                        message="Vivado did not publish the requested bitstream",
                        code="ARTIFACT_STALE",
                        details={"reason_code": "BITSTREAM_NOT_FOUND",
                                 "bitstream_path": output_path}).to_dict()
    else:
        # Compatibility for historical direct-bridge tests.
        tcl = (f"write_bitstream{force_flag} "
               f"{{{_tcl_path(output_path)}}}\nputs BIT_DONE")
        result = await _bridge_call(bridge, tcl, timeout_s=360.0)
    if result["status"] != "success":
        return result
    return success(data={"bitstream_path": output_path,
                         "output": result["data"]}).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  Timing & utilization
# ═══════════════════════════════════════════════════════════════════

async def pl_analyze_timing(bridge, *, clock=None, max_paths=None) -> dict:
    """Timing summary (WNS/TNS/WHS/THS) via report_timing_summary.

    On success, derives `timing_met` (setup slack closed: WNS >= 0) from the
    reported WNS and attaches it to the result data. The CommandRunner
    surfaces a strict-bool `data.timing_met` as completion evidence — this is
    the P7 evidence the execution gate requires before pl_generate_bitstream
    (B04_single_channel_audit §4.3: PL_TIMING → PL_BITSTREAM needs timing_met=true).
    """
    tcl = _build_timing_summary(clock)
    result = await _bridge_call(bridge, tcl, timeout_s=120.0)
    if result["status"] != "success":
        return result
    data = _parse_timing(result["data"])
    wns = data.get("wns_ns")
    if isinstance(wns, (int, float)) and not isinstance(wns, bool):
        data["timing_met"] = bool(wns >= 0)
    else:
        # Design has no user timing constraints (e.g. no clocks defined) — no
        # create_clock paths exist, so report_timing_summary produces no
        # numeric WNS/TNS row. Treat as timing-met with a note.
        data["timing_met"] = True
        data["wns_ns"] = 0.0
        data["tns_ns"] = 0.0
        data["note"] = "no_user_timing_constraints"
    return success(data=data).to_dict()


async def pl_analyze_utilization(bridge, *, hierarchical=None) -> dict:
    """Resource utilization report via report_utilization."""
    hier = " -hierarchical" if hierarchical else ""
    result = await _bridge_call(bridge, f"report_utilization{hier}",
                                timeout_s=120.0)
    if result["status"] != "success":
        return result
    return success(data=_parse_utilization(result["data"])).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  Design queries
# ═══════════════════════════════════════════════════════════════════

async def pl_query_cells(bridge, *, filter=None, hierarchical=None, properties=None) -> dict:
    """List logic cells via get_cells."""
    if filter is not None and not isinstance(filter, str):
        return _invalid("filter must be a string")
    if properties is not None and (not isinstance(properties, list)
                                   or not all(isinstance(p, str) for p in properties)):
        return _invalid("properties must be a list of strings")
    tcl = _build_query_cells(filter, bool(hierarchical), properties)
    result = await _bridge_call(bridge, tcl, timeout_s=90.0)
    if result["status"] != "success":
        return result
    return success(data={"cells": _parse_cells(result["data"])}).to_dict()


async def pl_query_nets(bridge, *, filter=None, hierarchical=None, max_items=None) -> dict:
    """List signal nets via get_nets."""
    if filter is not None and not isinstance(filter, str):
        return _invalid("filter must be a string")
    tcl = _build_query_nets(filter, bool(hierarchical), max_items)
    result = await _bridge_call(bridge, tcl, timeout_s=90.0)
    if result["status"] != "success":
        return result
    return success(data={"nets": _parse_nets(result["data"])}).to_dict()


async def pl_query_clocks(bridge) -> dict:
    """List clocks via get_clocks."""
    result = await _bridge_call(bridge, _GET_CLOCKS_TCL, timeout_s=90.0)
    if result["status"] != "success":
        return result
    return success(data={"clocks": _parse_clocks(result["data"])}).to_dict()


async def pl_query_ports(bridge, *, direction=None) -> dict:
    """List top-level IO ports via get_ports."""
    if direction is not None and not isinstance(direction, str):
        return _invalid("direction must be a string")
    tcl = _build_query_ports(direction)
    result = await _bridge_call(bridge, tcl, timeout_s=90.0)
    if result["status"] != "success":
        return result
    return success(data={"ports": _parse_ports(result["data"])}).to_dict()


async def pl_get_property(bridge, *, object, property) -> dict:
    """Get a single Vivado property via get_property."""
    if not isinstance(object, str) or not object.strip():
        return _invalid("object must be a non-empty string")
    if not isinstance(property, str) or not property.strip():
        return _invalid("property must be a non-empty string")
    tcl = f"get_property {{{property}}} {{{object}}}"
    result = await _bridge_call(bridge, tcl, timeout_s=90.0)
    if result["status"] != "success":
        return result
    value, vtype = _parse_property(result["data"])
    return success(data={"object": object, "property": property,
                         "value": value, "value_type": vtype}).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  Validation & info
# ═══════════════════════════════════════════════════════════════════

async def pl_validate_design(bridge) -> dict:
    """Run post-condition checks (clocks, part, timing, DRC).

    Fail-closed: each check that fails contributes a ``passed: false`` entry;
    DRC errors make the overall status "failed".
    """
    result = await _bridge_call(bridge, _VALIDATE_TCL, timeout_s=120.0)
    if result["status"] != "success":
        return result
    raw = {}
    for line in result["data"].splitlines():
        s = line.strip()
        if s.startswith("__CHECK__|"):
            fields = s[len("__CHECK__|"):].split("|", 1)
            if len(fields) == 2:
                raw[fields[0]] = fields[1]
    checks = []
    nclk = _safe_int(raw.get("clocks_defined", "0"))
    checks.append({"name": "clocks_defined", "passed": nclk > 0,
                   "detail": f"{nclk} clocks found"})
    part = raw.get("part", "")
    checks.append({"name": "part_known", "passed": bool(part) and part != "unknown",
                   "detail": part or "unknown"})
    timing_ok = raw.get("timing_summary", "none") == "ok"
    checks.append({"name": "timing_valid", "passed": timing_ok,
                   "detail": "timing report produced" if timing_ok
                             else "no timing results (unconstrained?)"})
    nerr = _safe_int(raw.get("drc_errors", "-1"))
    checks.append({"name": "drc_clean", "passed": nerr == 0,
                   "detail": f"{nerr} DRC errors"})
    failed = [c for c in checks if not c["passed"]]
    if failed:
        status = "failed"
        summary = f"{len(failed)} checks failed: {[c['name'] for c in failed]}"
    else:
        status = "passed"
        summary = "all checks passed"
    return success(data={"status": status, "summary": summary, "checks": checks,
                         "failed_count": len(failed),
                         "warning_count": 0}).to_dict()


async def pl_get_vivado_info(bridge) -> dict:
    """Vivado version/build/edition info. Requires no open design."""
    result = await _bridge_call(bridge, _VIVADO_INFO_TCL, timeout_s=90.0)
    if result["status"] != "success":
        return result
    m = _parse_line_markers(result["data"])
    build = m.get("build", "").strip()
    return success(data={
        "version": m.get("version", "unknown"),
        "build_id": build.split()[-1] if build else "",
        "build_date": build,
        "edition": m.get("edition", "Vivado"),
    }).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  JTAG hardware
# ═══════════════════════════════════════════════════════════════════

async def pl_connect_hw_server(bridge) -> dict:
    """Connect to hw_server for JTAG."""
    tcl = "open_hw_manager\nconnect_hw_server"
    result = await _bridge_call(bridge, tcl, timeout_s=120.0)
    if result["status"] != "success":
        return result
    data = result["data"]
    lowered = data.lower()
    if "could not connect" in lowered or "failed to connect" in lowered:
        return error(message="connect_hw_server failed to reach hw_server",
                     code="JTAG_ERROR",
                     details={"reason_code": "HW_SERVER_UNREACHABLE"}).to_dict()
    return success(data={"hw_server": "localhost:3121", "connected": True,
                         "output": data[:300]}).to_dict()


async def pl_get_device_status(bridge) -> dict:
    """Device status (DONE/programming) on the JTAG chain."""
    result = await _bridge_call(bridge, _DEVICE_STATUS_TCL, timeout_s=120.0)
    if result["status"] != "success":
        return result
    return success(data=_parse_devices(result["data"])).to_dict()


async def pl_program_device(bridge, *, bitstream_path) -> dict:
    """Program a bitstream via JTAG (hw_manager path)."""
    if not isinstance(bitstream_path, str) or not bitstream_path.strip():
        return _invalid("bitstream_path must be a non-empty string")
    tcl = _PROGRAM_DEVICE_TCL.format(bitstream_path=_tcl_path(bitstream_path))
    result = await _bridge_call(bridge, tcl, timeout_s=180.0)
    if result["status"] != "success":
        return result
    data = result["data"]
    done_status = "LOW/UNKNOWN"
    for line in data.splitlines():
        s = line.strip()
        if s.startswith("DONE_STATUS="):
            done_status = s.split("=", 1)[1]
    return success(data={
        "bitstream": bitstream_path,
        "programmed": "PROGRAMMING_COMPLETE" in data,
        "done_status": "HIGH" if done_status == "1" else done_status,
        "output_tail": data[-500:],
    }).to_dict()


async def pl_program_fpga(bridge, *, bitstream_path) -> dict:
    """Program the FPGA via XSDB `fpga -f` (Zynq-7020 standard path).

    pl_program_device (the old Vivado hw_manager path) reports "No xc7z020
    FPGA device found in JTAG chain" on this board: the Zynq JTAG chain
    exposes the ARM DAP as early targets and the FPGA later, and the
    hw_manager device match cannot resolve it. XSDB's `fpga -f` programs
    the configuration logic directly and is the canonical Zynq-7020 flow.

    Runs on the XsdbBridge (injected by domain_runner._execute for
    _PL_XSDB_TOOLS), NOT the VivadoTclBridge. The shell is auto-connected to
    the default hw_server (tcp:localhost:3121) when it is not already
    connected (a prior ps_connect_hw_server may have connected it).

    Errors (fail-closed): INVALID_ARGUMENT (path missing / not a file),
    TOOL_ERROR (bridge not ready), ENV_ERROR (hw_server unreachable),
    JTAG_ERROR (program or bridge-level failure).
    """
    # Lazily imported so an xsct-adapter import problem can never crash the
    # PL bridge module import (mirrors server.py's fail-soft bridge setup).
    from mcps.zynq_mcp.adapters.xsct import templates as xsdb_templates
    from mcps.zynq_mcp.adapters.xsct.xsdb_bridge import XsdbBridgeError

    if not isinstance(bitstream_path, str) or not bitstream_path.strip():
        return error(message="bitstream_path must be a non-empty string",
                     code="INVALID_ARGUMENT",
                     details={"reason_code": "INVALID_ARGUMENT"}).to_dict()
    if not os.path.isfile(bitstream_path):
        return error(message=f"Bitstream not found: {bitstream_path}",
                     code="INVALID_ARGUMENT",
                     details={"reason_code": "FILE_NOT_FOUND",
                              "bitstream_path": bitstream_path}).to_dict()
    if not getattr(bridge, "ready", False):
        return error(message="XsdbBridge is not ready; pl_program_fpga "
                             "requires an XSDB shell (xsdb on PATH)",
                     code="TOOL_ERROR",
                     details={"reason_code": "BRIDGE_NOT_READY"}).to_dict()
    try:
        if not getattr(bridge, "hw_connected", False):
            conn = await bridge.eval(xsdb_templates.connect("localhost:3121"))
            if conn.get("status") != "success":
                err = conn.get("error") or {}
                return error(
                    message="failed to connect to hw_server: "
                            f"{err.get('message', 'connect failed')}",
                    code="ENV_ERROR",
                    details={"reason_code": "HW_SERVER_UNREACHABLE"}).to_dict()
        # P1-C: Tcl interprets backslash escapes (e.g. ``\f`` = form feed,
        # ``\b`` = backspace) inside unquoted command words, so a Windows
        # bitstream path like ``D:\fpga\demo.bit`` would be corrupted
        # before it ever reaches the filesystem. Normalize to forward slashes
        # before embedding the path in the `fpga -f` Tcl command.
        result = await bridge.eval(
            xsdb_templates.fpga_program(_tcl_path(bitstream_path)),
            timeout_s=120.0)
    except XsdbBridgeError as e:
        return error(message=str(e), code="JTAG_ERROR",
                     details={"reason_code": "BRIDGE_UNAVAILABLE"}).to_dict()
    except Exception as e:
        return error(message=str(e), code="INTERNAL_ERROR",
                     details={"reason_code": "BRIDGE_CALL_FAILED"}).to_dict()
    if result.get("status") != "success":
        err = result.get("error") or {}
        details = err.get("details") or {}
        return error(message=err.get("message", "fpga -f failed"),
                     code="JTAG_ERROR",
                     details={"reason_code":
                              details.get("reason_code", "PROGRAM_FAILED")}).to_dict()
    return success(data={"status": "programmed",
                         "bitstream_path": bitstream_path,
                         "output": result.get("data", "")}).to_dict()


async def pl_list_devices(bridge) -> dict:
    """List devices on the JTAG chain via get_hw_devices."""
    result = await _bridge_call(bridge, _DEVICE_LIST_TCL, timeout_s=120.0)
    if result["status"] != "success":
        return result
    return success(data=_parse_devices(result["data"])).to_dict()


# ═══════════════════════════════════════════════════════════════════
#  Simulation (xvlog/xelab/xsim) — DEFERRED: standalone XSim adapter.
#  These tools run OUTSIDE vivado.exe and keep the old MCP adapter bridge
#  (see _call_old_adapter; domain_runner routes them to the old VivadoAdapter
#  via _PL_OLD_ADAPTER_TOOLS).
# ═══════════════════════════════════════════════════════════════════

async def pl_compile_sim(bridge, *, sources, sim_dir) -> dict:
    """Compile RTL/testbench with xvlog. Bridges old `compile_sim`."""
    return await _call_old_adapter(bridge, "compile_sim",
        _args(sources=sources, sim_dir=sim_dir), timeout=180.0)


async def pl_elaborate_sim(bridge, *, top, sim_dir) -> dict:
    """Elaborate with xelab. Bridges old `elaborate_sim`."""
    return await _call_old_adapter(bridge, "elaborate_sim",
        _args(top=top, sim_dir=sim_dir), timeout=180.0)


async def pl_run_simulation(bridge, *, top, sim_dir, vcd_path=None) -> dict:
    """Run the elaborated simulation with xsim. Bridges old `run_simulation`."""
    return await _call_old_adapter(bridge, "run_simulation",
        _args(top=top, sim_dir=sim_dir, vcd_path=vcd_path), timeout=180.0)


async def pl_parse_sim_log(bridge, *, log_path) -> dict:
    """Parse a simulation log for PASS/FAIL. Bridges old `parse_sim_log`."""
    return await _call_old_adapter(bridge, "parse_sim_log",
        _args(log_path=log_path), timeout=90.0)


# ═══════════════════════════════════════════════════════════════════
#  Registry used by the dispatcher: zynq tool name → (bridge fn, call timeout)
# ═══════════════════════════════════════════════════════════════════

PL_TOOL_MAP: dict[str, tuple] = {
    "pl_create_project": (pl_create_project, 180.0),
    "pl_open_checkpoint": (pl_open_checkpoint, 360.0),
    "pl_close_design": (pl_close_design, 90.0),
    "pl_generate_target": (pl_generate_target, 360.0),
    "pl_synthesize": (pl_synthesize, 3660.0),
    "pl_place": (pl_place, 3660.0),
    "pl_route": (pl_route, 3660.0),
    "pl_generate_bitstream": (pl_generate_bitstream, 360.0),
    "pl_analyze_timing": (pl_analyze_timing, 120.0),
    "pl_analyze_utilization": (pl_analyze_utilization, 120.0),
    "pl_query_cells": (pl_query_cells, 90.0),
    "pl_query_nets": (pl_query_nets, 90.0),
    "pl_query_clocks": (pl_query_clocks, 90.0),
    "pl_query_ports": (pl_query_ports, 90.0),
    "pl_get_property": (pl_get_property, 90.0),
    "pl_validate_design": (pl_validate_design, 120.0),
    "pl_get_vivado_info": (pl_get_vivado_info, 90.0),
    "pl_connect_hw_server": (pl_connect_hw_server, 120.0),
    "pl_get_device_status": (pl_get_device_status, 120.0),
    "pl_program_device": (pl_program_device, 180.0),
    # pl_program_fpga runs on the XsdbBridge (`fpga -f`), not the Vivado
    # bridge — it is excluded from the Vivado bridge forwarding tests.
    # 120s bounds the bitstream download; the CommandRunner adds +60s.
    "pl_program_fpga": (pl_program_fpga, 120.0),
    "pl_list_devices": (pl_list_devices, 120.0),
    # Simulation tools: still routed to the old Vivado MCP adapter
    # (# DEFERRED: standalone XSim adapter).
    "pl_compile_sim": (pl_compile_sim, 180.0),
    "pl_elaborate_sim": (pl_elaborate_sim, 180.0),
    "pl_run_simulation": (pl_run_simulation, 180.0),
    "pl_parse_sim_log": (pl_parse_sim_log, 90.0),
}
