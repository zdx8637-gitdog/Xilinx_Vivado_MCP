"""
platform_atoms.py — Platform atomic APIs (B05-R2 + B11 phase ③.1).

Implements the 17 composable Platform Domain atoms from B01 §7 Phase 1 /
Architecture §4.3.3. B05-R2 completed the header-table API list by adding
``platform_connect_reset`` and ``platform_export_manifest`` to the original
12 (the two APIs were listed in the §4.3.3 header table but omitted from the
detailed spec segment, so the initial 12-atom delivery skipped them). B11
phase ③.1 (D1/D2/D3) added the three missing BD-design atoms
``platform_assign_addresses`` (address assignment), ``platform_make_external``
(port externalization) and ``platform_synthesize`` (top-level synthesis so the
exported XSA contains HDF). Each function is stateless: it receives the Vivado
adapter as its first positional argument (injected by the CommandRunner via
the ``_pl_adapter`` marker for command atoms, or by the dispatcher query
handlers for query atoms) and forwards every Tcl command through the shared
``_run_tcl`` channel — the same channel the removed B05 shortcut
``platform_generate`` used, with the same cold-start retry and the same
error contract.

Tcl capture contract (D8, verified against real Vivado 2023.1): the Tcl
shell bridge captures only stdout — Tcl command RETURN VALUES are not echoed.
Every query atom therefore prints its result with ``puts`` (e.g.
``puts [get_bd_cells *]``); a bare result-returning command would come back
empty and silently corrupt the manifest (the D8 symptom).

Command atoms do NOT advance the workflow stage (next_stage=None) except
``platform_export_manifest`` — the terminal atom of the platform sequence —
which advances PLATFORM_DESIGN → PL_GENERATE on success (B11 phase 2
decision (a)). The B05 shortcut ``platform_generate`` was removed in B11
phase 2; these atoms are the replacement path. The three added atoms are
admitted only in PLATFORM_DESIGN (execution_gate._check_stage) and never
advance the stage.

Atoms:
  command (15, adapter injected by CommandRunner):
    platform_create_design, platform_add_ps7, platform_configure_ps7,
    platform_add_ip, platform_connect_interface, platform_connect_clock,
    platform_connect_reset, platform_set_address, platform_assign_addresses,
    platform_make_external, platform_validate, platform_generate_wrapper,
    platform_synthesize, platform_export_hardware, platform_export_manifest
  query (2, adapter injected by dispatcher query handlers):
    platform_get_status, platform_list_ips

Only existing helpers from platform_domain are reused (no platform_domain
code is modified): ``_run_tcl``, ``_tcl_output``, ``_resolve_board_package``,
``_sha256_file`` and the structured exception classes.
"""
import asyncio
import json
import os
import re
import shutil
import time
from pathlib import Path

from mcps.common.artifact_schema import (
    ManifestConflictError,
    publish_manifest as _publish_manifest,
)
from mcps.common.revision import compute_revision, is_sha256
from mcps.zynq_mcp.domains.platform.platform_domain import (
    _run_tcl,
    _tcl_output,
    _resolve_board_package,
    _sha256_file,
    SYNTH_TIMEOUT_S,
    PlatformError,
    BoardPackageNotFoundError,
    AdapterError,
    TclError,
    SynthesisError,
    BdValidationError,
    WrapperExportError,
    XsaExportError,
    ManifestError,
)


# ═══════════════════════════════════════════
#  1. Design lifecycle
# ═══════════════════════════════════════════

async def platform_create_design(adapter, *, name: str, part: str,
                                 project_path: str) -> dict:
    """Create a Vivado project for a Block Design (atom API).

    Tcl: create_project {name}_bd {{project_path}/vivado/{name}} -part {part} -force
    Postcondition: the Vivado project directory exists.
    The BD design itself is created on demand (see platform_add_ps7).
    """
    if not isinstance(name, str) or not name.strip():
        raise PlatformError("name must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(part, str) or not part.strip():
        raise PlatformError("part must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(project_path, str) or not project_path.strip():
        raise PlatformError("project_path must be a non-empty string", "INVALID_ARGUMENT")
    proj_dir = f"{project_path}/vivado/{name}"
    cmd = f"create_project {name}_bd {{{proj_dir}}} -part {part} -force"
    await _run_tcl(adapter, cmd, "create_design")
    return {"status": "success", "data": {"name": name, "part": part,
                                          "project_dir": proj_dir}}


async def platform_get_status(adapter) -> dict:
    """Query the open Vivado project name and BD cell count (query atom).

    Pure read: get_property NAME [current_project] + llength [get_bd_cells *].
    A missing project or BD design fails closed via AdapterError. Results are
    printed with ``puts`` — the Tcl bridge captures stdout only, never a bare
    command return value (D8).
    """
    name_data = await _run_tcl(adapter,
                               "puts [get_property NAME [current_project]]",
                               "get_project_name")
    project_name = _tcl_output(name_data).strip()
    count_data = await _run_tcl(adapter,
                                "puts [llength [get_bd_cells *]]",
                                "count_bd_cells")
    count_text = _tcl_output(count_data).strip()
    ip_count = int(count_text) if count_text.isdigit() else None
    return {"status": "success", "data": {
        "project_name": project_name,
        "ip_count": ip_count,
        "has_project": bool(project_name),
    }}


# ═══════════════════════════════════════════
#  2. PS7 hardware configuration
# ═══════════════════════════════════════════

async def platform_add_ps7(adapter, *, board_id: str,
                           preset_name: str | None = None) -> dict:
    """Instantiate and configure the Zynq PS7 from the board preset (atom API).

    Sequence (mirrors the proven Tcl of the B05 platform_generate shortcut,
    removed in B11 phase 2):
      1. ensure a BD design exists (create_design only makes the project)
      2. create_bd_cell processing_system7_0
      3. apply_bd_automation (externalize FIXED_IO / DDR)
      4. source ps7_preset.tcl from the board package
      5. set_ps_config processing_system7_0
    """
    if not isinstance(board_id, str) or not board_id.strip():
        raise PlatformError("board_id must be a non-empty string", "INVALID_ARGUMENT")
    bp_dir = _resolve_board_package(board_id)
    preset_rel = preset_name if isinstance(preset_name, str) and preset_name.strip() else "ps7_preset.tcl"
    preset_path = os.path.join(bp_dir, preset_rel)
    if not os.path.isfile(preset_path):
        raise BoardPackageNotFoundError(f"PS7 preset not found: {preset_path}")
    with open(preset_path, encoding="utf-8") as f:
        preset_tcl = f.read().replace("\r\n", "\n")

    await _run_tcl(adapter,
        'if {[llength [get_bd_designs -quiet]] == 0} {\n'
        '  create_bd_design platform_bd\n'
        '}',
        "ensure_bd")
    await _run_tcl(adapter,
        'create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0',
        "create_ps7")
    await _run_tcl(adapter,
        'apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 '
        '-config {make_external "FIXED_IO, DDR"} [get_bd_cells processing_system7_0]',
        "ps7_automation")
    await _run_tcl(adapter, preset_tcl, "source_ps7_preset")
    await _run_tcl(adapter, "set_ps_config processing_system7_0", "apply_preset")

    return {"status": "success", "data": {
        "instance": "processing_system7_0",
        "bd": "platform_bd",
        "preset": preset_rel,
    }}


# logical PS7 config key -> (PCW property, value kind)
_PS7_CONFIG_TO_PCW = {
    "m_axi_gp0": ("PCW_USE_M_AXI_GP0", "bool"),
    "m_axi_gp1": ("PCW_USE_M_AXI_GP1", "bool"),
    "s_axi_hp0": ("PCW_USE_S_AXI_HP0", "bool"),
    "s_axi_hp1": ("PCW_USE_S_AXI_HP1", "bool"),
    "s_axi_acp": ("PCW_USE_S_AXI_ACP", "bool"),
    "irq_f2p": ("PCW_USE_FABRIC_INTERRUPT", "bool"),
    "fclk0_mhz": ("PCW_FPGA0_PERIPHERAL_FREQMHZ", "int"),
    "fclk1_mhz": ("PCW_FPGA1_PERIPHERAL_FREQMHZ", "int"),
    "uart1_enable": ("PCW_UART1_PERIPHERAL_ENABLE", "bool"),
    "uart1_io": ("PCW_UART1_GRP_FULL_IO", "mio"),
    "ddr": ("PCW_UIPARAM_DDR_PARTNO", "str"),
    # B11 phase ③.1 (D0): EMIO GPIO route. Nested key ``gpio: {emio_enable,
    # width, io}`` flattens to gpio_emio_enable / gpio_width / gpio_io.
    "gpio_emio_enable": ("PCW_EN_EMIO_GPIO", "bool"),
    "gpio_width": ("PCW_GPIO_EMIO_GPIO_WIDTH", "int"),
    "gpio_io": ("PCW_GPIO_EMIO_GPIO_IO", "mio"),
}


async def platform_configure_ps7(adapter, *, config: dict,
                                 instance: str | None = None) -> dict:
    """Update PS7 CONFIG.PCW_* properties (atom API, idempotent partial update).

    Only the fields present in ``config`` are written. Nested dicts (e.g.
    ``uart1: {enable, io}``) are flattened to ``uart1_enable`` / ``uart1_io``.
    Unknown keys fail closed with INVALID_ARGUMENT.
    """
    if not isinstance(config, dict) or not config:
        raise PlatformError("config must be a non-empty dict", "INVALID_ARGUMENT")
    inst = instance if isinstance(instance, str) and instance.strip() else "processing_system7_0"

    flat = {}
    for key, value in config.items():
        if isinstance(value, dict):
            for subk, subv in value.items():
                flat[f"{key}_{subk}"] = subv
        else:
            flat[key] = value
    if not flat:
        raise PlatformError("config must contain at least one field", "INVALID_ARGUMENT")

    prop_parts = []
    updated = []
    for key, value in flat.items():
        entry = _PS7_CONFIG_TO_PCW.get(key)
        if entry is None:
            raise PlatformError(f"Unknown PS7 config key: {key}", "INVALID_ARGUMENT")
        pcw, kind = entry
        if kind == "bool":
            if isinstance(value, bool):
                tcl_val = "1" if value else "0"
            elif value in (0, 1) and not isinstance(value, bool):
                tcl_val = str(value)
            else:
                raise PlatformError(f"Config {key} expects a boolean, got {value!r}",
                                    "INVALID_ARGUMENT")
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PlatformError(f"Config {key} expects a non-negative integer, got {value!r}",
                                    "INVALID_ARGUMENT")
            tcl_val = str(value)
        elif kind == "mio":
            if not isinstance(value, str):
                raise PlatformError(f"Config {key} expects an MIO range string, got {value!r}",
                                    "INVALID_ARGUMENT")
            tcl_val = "1" if value.strip() else "0"
        else:  # str
            tcl_val = str(value)
        prop_parts.append(f"CONFIG.{pcw} {{{tcl_val}}}")
        updated.append(key)

    cmd = f"set_property -dict [list {' '.join(prop_parts)}] [get_bd_cells {inst}]"
    await _run_tcl(adapter, cmd, "configure_ps7")
    return {"status": "success", "data": {"instance": inst, "updated": updated}}


# ═══════════════════════════════════════════
#  3. IP management
# ═══════════════════════════════════════════

def _norm_prop_val(v) -> str:
    """Normalize a property value for idempotent comparison.

    bool True/False, and the Tcl strings "true"/"false"/"1"/"0" collapse to
    "1"/"0" so the caller's intent compares equal to the queried property.
    """
    if isinstance(v, bool):
        return "1" if v else "0"
    s = str(v).strip().lower()
    if s == "true":
        return "1"
    if s == "false":
        return "0"
    return s


async def _verify_ip_props(adapter, instance_name: str,
                           props: dict) -> dict:
    """Read back each requested CONFIG.* and return the mismatch map.

    Every query is printed with ``puts`` — the Tcl bridge captures stdout
    only, never a bare command return value (D8). This is the D-A fix: a bare
    ``get_property`` (no ``puts``) comes back empty on real Vivado, so the
    readback would report ``actual=''`` for every property and misclassify a
    correctly-applied IP as a mismatch — and, worse, never prove a real
    mismatch on a silent drop. Returns ``{}`` when every requested property
    matched.

    B12 fix round #2 (item #5): Vivado **silently ignores** a non-existent
    ``CONFIG.<key>`` on a ``set_property`` (e.g. a wrong parameter name like
    ``C_DATA_WIDTH`` on ``axi_bram_ctrl``, whose real parameter is
    ``C_S_AXI_DATA_WIDTH``), so the write "succeeds" but the property never
    lands. The readback then returns ``''`` for that key. Mark such entries
    with ``recognized: False`` so the caller can distinguish an unknown
    property name (a name/spec error) from a value that was applied to a real
    parameter but with a different value.
    """
    mismatch = {}
    for key, want in props.items():
        rdata = await _run_tcl(adapter,
            f"puts [get_property CONFIG.{key} [get_bd_cells {instance_name}]]",
            f"get_prop_{key}")
        got = _tcl_output(rdata).strip()
        if _norm_prop_val(got) != _norm_prop_val(want):
            recognized = bool(got)
            mismatch[key] = {"expected": want, "actual": got,
                             "recognized": recognized}
    return mismatch


async def platform_add_ip(adapter, *, vlnv: str, instance_name: str,
                          properties: dict | None = None) -> dict:
    """Instantiate an IP from the catalog (atom API, idempotent).

    If ``instance_name`` already exists, its config is compared against the
    requested ``properties``: matching → unchanged OK; differing → fail closed
    with IP_CONFIG_MISMATCH. No duplicate cell is ever created.

    The config is ALWAYS verified after the write (D-A): the properties are
    either really applied (readback matches → success) or an explicit
    IP_CONFIG_MISMATCH is raised with the non-empty ``actual`` value — never a
    silent success. This is what makes dual-channel AXI GPIO channel-2 params
    (C_IS_DUAL / C_GPIO2_WIDTH / C_ALL_INPUTS_2) provable instead of silently
    dropped.
    """
    if not isinstance(vlnv, str) or not vlnv.strip():
        raise PlatformError("vlnv must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(instance_name, str) or not instance_name.strip():
        raise PlatformError("instance_name must be a non-empty string", "INVALID_ARGUMENT")
    props = properties if isinstance(properties, dict) else {}

    exists_data = await _run_tcl(adapter,
        f"puts [llength [get_bd_cells -quiet {instance_name}]]", "check_ip_exists")
    exists = _tcl_output(exists_data).strip() == "1"

    if exists:
        if props:
            mismatch = await _verify_ip_props(adapter, instance_name, props)
            if mismatch:
                raise PlatformError(
                    f"IP {instance_name} already exists with differing config: {mismatch}",
                    "IP_CONFIG_MISMATCH")
        return {"status": "success", "data": {
            "instance_name": instance_name, "vlnv": vlnv,
            "already_exists": True, "status": "unchanged"}}

    cmd = f"create_bd_cell -type ip -vlnv {vlnv} {instance_name}"
    if props:
        parts = [f"CONFIG.{key} {{{val}}}" for key, val in props.items()]
        cmd += f"\nset_property -dict [list {' '.join(parts)}] [get_bd_cells {instance_name}]"
    await _run_tcl(adapter, cmd, "add_ip")
    # D-A: verify the requested config really stuck before returning success.
    # B12 fix round #2 (item #5): if a requested CONFIG.* reads back as '' on a
    # fresh add, Vivado silently ignored it — the property name is not a real
    # parameter of this IP (e.g. C_DATA_WIDTH on axi_bram_ctrl, whose real name
    # is C_S_AXI_DATA_WIDTH). Raise a distinct IP_PROPERTY_NOT_RECOGNIZED so
    # the caller knows the name is wrong, instead of a generic mismatch.
    if props:
        mismatch = await _verify_ip_props(adapter, instance_name, props)
        if mismatch:
            unknown = {k: v for k, v in mismatch.items()
                       if v.get("recognized") is False}
            if unknown:
                raise PlatformError(
                    f"IP {instance_name} property(ies) not recognized by the "
                    f"catalog (Vivado silently ignored them): "
                    f"{ {k: v for k, v in unknown.items()} }",
                    "IP_PROPERTY_NOT_RECOGNIZED")
            raise PlatformError(
                f"IP {instance_name} config not applied: {mismatch}",
                "IP_CONFIG_MISMATCH")
    return {"status": "success", "data": {
        "instance_name": instance_name, "vlnv": vlnv, "already_exists": False}}


async def platform_list_ips(adapter, *, filter: str | None = None) -> dict:
    """List BD cells in the open design (query atom).

    Tcl: get_bd_cells -filter {filter} (or get_bd_cells * when no filter),
    printed with puts (the Tcl bridge captures stdout only — D8).
    Output is split on whitespace — Vivado prints the Tcl list one per line
    or space-separated depending on the channel.
    """
    if filter is not None and (not isinstance(filter, str) or not filter.strip()):
        raise PlatformError("filter must be a non-empty string", "INVALID_ARGUMENT")
    cmd = f"puts [get_bd_cells -filter {{{filter}}}]" if filter else "puts [get_bd_cells *]"
    data = await _run_tcl(adapter, cmd, "list_ips")
    output = _tcl_output(data)
    ips = [tok for tok in re.split(r"\s+", output.strip()) if tok]
    return {"status": "success", "data": {"ips": ips, "count": len(ips)}}


# ═══════════════════════════════════════════
#  4. Interface, clock & reset connection
# ═══════════════════════════════════════════

async def platform_connect_interface(adapter, *, source: str,
                                     destination: str) -> dict:
    """Connect two AXI bus interfaces (atom API).

    Example: source="processing_system7_0/M_AXI_GP0",
             destination="smartconnect_0/S00_AXI".
    """
    if not isinstance(source, str) or not source.strip():
        raise PlatformError("source must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(destination, str) or not destination.strip():
        raise PlatformError("destination must be a non-empty string", "INVALID_ARGUMENT")
    cmd = (f"connect_bd_intf_net [get_bd_intf_pins {source}] "
           f"[get_bd_intf_pins {destination}]")
    await _run_tcl(adapter, cmd, "connect_interface")
    return {"status": "success", "data": {"source": source, "destination": destination}}


async def platform_connect_clock(adapter, *, source: str,
                                 targets: list) -> dict:
    """Connect one clock source to a list of clock inputs (atom API).

    Example: source="processing_system7_0/FCLK_CLK0",
             targets=["smartconnect_0/aclk", "my_slave_0/s_axi_aclk"].
    """
    if not isinstance(source, str) or not source.strip():
        raise PlatformError("source must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(targets, list) or not targets:
        raise PlatformError("targets must be a non-empty list", "INVALID_ARGUMENT")
    clean = []
    for t in targets:
        if not isinstance(t, str) or not t.strip():
            raise PlatformError("each target must be a non-empty string", "INVALID_ARGUMENT")
        clean.append(t.strip())
    lines = [f"connect_bd_net [get_bd_pins {source}] [get_bd_pins {t}]" for t in clean]
    await _run_tcl(adapter, "\n".join(lines), "connect_clock")
    return {"status": "success", "data": {"source": source, "targets": clean,
                                          "count": len(clean)}}


async def platform_connect_reset(adapter, *, source: str, targets: list) -> dict:
    """Connect one reset source to a list of reset inputs (atom API).

    Reset polarity is NOT inspected — the caller selects the correct source /
    target pins (e.g. SmartConnect expects ``interconnect_aresetn`` while
    peripheral AXI slaves take ``peripheral_aresetn``). This atom only wires
    the nets with ``connect_bd_net``.

    Example: source="rst_ps7_50M/peripheral_aresetn",
             targets=["my_slave_0/s_axi_aresetn"].
    """
    if not isinstance(source, str) or not source.strip():
        raise PlatformError("source must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(targets, list) or not targets:
        raise PlatformError("targets must be a non-empty list", "INVALID_ARGUMENT")
    clean = []
    for t in targets:
        if not isinstance(t, str) or not t.strip():
            raise PlatformError("each target must be a non-empty string", "INVALID_ARGUMENT")
        clean.append(t.strip())
    lines = [f"connect_bd_net [get_bd_pins {source}] [get_bd_pins {t}]" for t in clean]
    await _run_tcl(adapter, "\n".join(lines), "connect_reset")
    return {"status": "success", "data": {"source": source, "targets": clean,
                                          "count": len(clean)}}


# Vivado 2023.1 create_bd_port direction letters (real-Vivado verified:
# "IN"/"OUT"/"INOUT" are rejected with BD 41-78; only I/O/IO are accepted).
_BD_DIRECTION_LETTERS = {"in": "I", "out": "O", "inout": "IO"}


def _bd_port_names(adapter_output: str) -> set:
    """Tokens of a ``get_bd_ports *`` / ``get_bd_intf_ports *`` listing,
    normalized by stripping the leading '/' (Vivado prints object paths)."""
    tokens = re.split(r"\s+", adapter_output.strip())
    return {t.lstrip("/") for t in tokens if t}


async def platform_make_external(adapter, *, port_name: str, source_pin: str,
                                 direction: str | None = None,
                                 width: int | None = None,
                                 interface: bool = False) -> dict:
    """Externalize a BD pin/interface as a top-level port (atom API).

    Architecture §4.3.3 第五类 (D2). Two modes:
      - ``interface=true``: the interface pin is resolved via ``-of_objects``
        (bare / ``-filter`` intf-pin queries match nothing on real Vivado
        2023.1 — D8) and externalized with ``make_bd_intf_pins_external``
        (real-Vivado verified: ``make_bd_pins_external`` only applies to
        regular pins — BD 5-407). Vivado derives the port name itself and
        may append a suffix (B13-M2 ④: axi_gpio_0/S_AXI → S_AXI_0); the
        actual name is captured via ``get_bd_intf_ports -of_objects
        [get_bd_intf_nets -of_objects <pin>]`` (real-Vivado verified; the
        pin-basename guess is wrong).
      - signal mode (default): ``create_bd_port -dir <I|O|IO> [-from w-1 -to
        0] <port_name>`` then ``connect_bd_net [get_bd_pins <source_pin>]
        [get_bd_ports <port_name>]``.
    ``direction`` (in|out|inout) is required for signal mode; ``width``
    >1 creates a vector port (omitted/1 → scalar). The created port's
    existence is verified against the ``get_bd_ports *`` listing (name
    queries match nothing on real Vivado — D8) and the port facts returned
    (fail-closed EXTERNAL_PORT_CREATE_FAILED otherwise). Only admitted in
    PLATFORM_DESIGN and never advances the stage.
    """
    if not isinstance(port_name, str) or not port_name.strip():
        raise PlatformError("port_name must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(source_pin, str) or not source_pin.strip():
        raise PlatformError("source_pin must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(interface, bool):
        raise PlatformError("interface must be a boolean", "INVALID_ARGUMENT")

    if interface:
        resolve_tcl = (
            f"set __src {{{source_pin}}}\n"
            f"set __parts [split {{{source_pin}}} /]\n"
            "set __ip [lindex $__parts 0]\n"
            "set __pin {}\n"
            "foreach __p [get_bd_intf_pins -quiet -of_objects "
            "[get_bd_cells -quiet $__ip]] {\n"
            "  if {[string trimleft $__p /] eq $__src} {\n"
            "    set __pin $__p\n"
            "    break\n"
            "  }\n"
            "}\n"
            "if {$__pin eq \"\"} {\n"
            f"  error \"INTF_PIN_NOT_FOUND:{{{source_pin}}}\"\n"
            "}\n"
            "make_bd_intf_pins_external $__pin\n"
            # Vivado derives the external port name itself and may add a
            # suffix (real-Vivado verified: axi_gpio_0/S_AXI → S_AXI_0,
            # axi_gpio_1/S_AXI → S_AXI_1 — B13-M2 ④). The pin-basename
            # guess is wrong; capture the truth via the interface net:
            # -of_objects on the PIN matches nothing (BD 5-233), but
            # -of_objects on the NET of the pin returns the port.
            "set __ext [get_bd_intf_ports -of_objects "
            "[get_bd_intf_nets -of_objects $__pin]]\n"
            'puts "EXT_PORT $__ext"')
        res = await _run_tcl(adapter, resolve_tcl, "make_external")
        m = re.search(r"EXT_PORT\s+(\S+)", _tcl_output(res))
        if not m or not m.group(1).lstrip("/"):
            raise PlatformError(
                f"External interface port for {source_pin} was not created "
                "(no EXT_PORT capture)", "EXTERNAL_PORT_CREATE_FAILED")
        derived = m.group(1).lstrip("/")
        return {"status": "success", "data": {
            "port_name": derived, "source_pin": source_pin,
            "interface": True, "direction": "interface"}}

    if not isinstance(direction, str) or direction.strip().lower() \
            not in _BD_DIRECTION_LETTERS:
        raise PlatformError("direction must be one of in|out|inout",
                            "INVALID_ARGUMENT")
    d = direction.strip().lower()
    if width is not None and (isinstance(width, bool) or not isinstance(width, int)
                              or width <= 0):
        raise PlatformError("width must be a positive integer", "INVALID_ARGUMENT")

    parts = [f"create_bd_port -dir {_BD_DIRECTION_LETTERS[d]}"]
    if width is not None and width > 1:
        parts.append(f"-from {width - 1} -to 0")
    parts.append(port_name)
    cmd = " ".join(parts)
    cmd += (f"\nconnect_bd_net [get_bd_pins {source_pin}] "
            f"[get_bd_ports {port_name}]")
    await _run_tcl(adapter, cmd, "make_external")

    verify = await _run_tcl(adapter, "puts [get_bd_ports *]",
                            "verify_external_port")
    if port_name not in _bd_port_names(_tcl_output(verify)):
        raise PlatformError(f"Port {port_name} was not created",
                            "EXTERNAL_PORT_CREATE_FAILED")
    return {"status": "success", "data": {
        "port_name": port_name, "source_pin": source_pin,
        "interface": False, "direction": d,
        "width": width if width is not None else 1}}


# ═══════════════════════════════════════════
#  5. Address space
# ═══════════════════════════════════════════

async def platform_set_address(adapter, *, segment: str, base,
                               size: int | None = None) -> dict:
    """Set a slave segment base address (and optional size) (atom API).

    segment format: ``"<ip>/<interface>"``, e.g. ``"my_slave_0/S_AXI"``. The
    short form is resolved automatically to the real address segment (e.g.
    ``"my_slave_0/S_AXI"`` → ``"my_slave_0/S_AXI/Reg"``) by querying
    ``get_bd_addr_segs`` directly and, when no match, the child segments of
    the named interface pin (B11 phase ③.1 D5). An unresolvable segment fails
    closed. When ``size`` is given the matching CONFIG.C_HIGHADDR is derived
    (base + size - 1) and written in the same Tcl command.

    Note (D1): on Vivado 2023.1 the CONFIG.C_BASEADDR / C_HIGHADDR properties
    of a BD address segment are read-only once the segment exists — explicit
    set_property calls are silently rejected by the tool. The address
    assignment path is ``platform_assign_addresses`` (assign_bd_address);
    this atom remains for explicit override attempts and fails closed when the
    Tcl rejects them.
    """
    if not isinstance(segment, str) or not segment.strip():
        raise PlatformError("segment must be a non-empty string", "INVALID_ARGUMENT")
    if base is None or (isinstance(base, str) and not base.strip()):
        raise PlatformError("base must be provided", "INVALID_ARGUMENT")
    base_str = str(base).strip()
    try:
        base_int = int(base_str, 16) if base_str.lower().startswith("0x") else int(base_str, 0)
    except ValueError:
        raise PlatformError(f"Invalid base address: {base!r}", "INVALID_ARGUMENT")
    lines = [
        # D5: resolve "<ip>/<intf>" to the real segment name ("<ip>/<intf>/Reg")
        # via get_bd_addr_segs, falling back to enumerating the interface pins
        # of the named cell with -of_objects (bare/-filter intf-pin queries
        # match nothing on real Vivado 2023.1 — D8) and taking the child
        # segments of the matching interface pin. Unresolvable → hard Tcl
        # error → TCL_ERROR (D6).
        f"set __req {{{segment}}}\n"
        "set __segs [get_bd_addr_segs -quiet $__req]\n"
        "if {[llength $__segs] == 0} {\n"
        f"  set __parts [split {{{segment}}} /]\n"
        "  set __ip [lindex $__parts 0]\n"
        "  set __intf [lindex $__parts 1]\n"
        "  set __pins [get_bd_intf_pins -quiet -of_objects "
        "[get_bd_cells -quiet $__ip]]\n"
        "  set __segs {}\n"
        "  foreach __p $__pins {\n"
        "    if {[string trimleft $__p /] eq \"$__ip/$__intf\"} {\n"
        "      set __segs [get_bd_addr_segs -quiet -of_objects $__p]\n"
        "      break\n"
        "    }\n"
        "  }\n"
        "}\n"
        "if {[llength $__segs] == 0} {\n"
        f"  error \"SEGMENT_NOT_FOUND:{{{segment}}}\"\n"
        "}\n"
        "set __seg [lindex $__segs 0]\n"
        f"set_property CONFIG.C_BASEADDR {{{base_str}}} $__seg",
    ]
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise PlatformError("size must be a positive integer", "INVALID_ARGUMENT")
        high = base_int + size - 1
        lines.append(f"set_property CONFIG.C_HIGHADDR {{{hex(high)}}} $__seg")
    await _run_tcl(adapter, "\n".join(lines), "set_address")
    return {"status": "success", "data": {"segment": segment, "base": base_str,
                                          "size": size}}


# Per-master address map query, shared by platform_assign_addresses and
# platform_export_manifest. Each line is "<master> <segment> <OFFSET> <RANGE>"
# printed with puts (the Tcl bridge captures stdout only — D8).
#
# Verified against real Vivado 2023.1: bare `get_bd_intf_pins` / wildcard `*`
# / `-filter` forms match NOTHING for bd pins — only `-of_objects` (on cells)
# enumerates interface pins. The addressable (master-side) segments live under
# the master intf pin and carry OFFSET/RANGE; segments without an OFFSET
# (unassigned / slave-side) are skipped so the map reports only assigned
# addresses. Master-side segment names look like
# "processing_system7_0/Data/SEG_<ip>_Reg" — the parser extracts <ip>.
_ADDRESS_MAP_QUERY_TCL = (
    "foreach m [get_bd_intf_pins -quiet -of_objects [get_bd_cells -quiet *]] {\n"
    "  foreach mseg [get_bd_addr_segs -quiet -of_objects $m] {\n"
    "    set __off [get_property OFFSET $mseg]\n"
    "    if {$__off ne \"\"} {\n"
    "      puts \"[string trimleft $m /] [string trimleft $mseg /] $__off "
    "[get_property RANGE $mseg]\"\n"
    "    }\n"
    "  }\n"
    "}")


async def platform_assign_addresses(adapter, *,
                                    segments: list | None = None) -> dict:
    """Auto-assign BD slave address segments (atom API, idempotent).

    Architecture §4.3.3 第七类 (D1). Tcl:
      - ``segments`` omitted → ``assign_bd_address`` (assign every unassigned
        address segment in the design);
      - ``segments`` given → ``assign_bd_address [get_bd_addr_segs {<seg>}]``
        per entry (short segment names such as ``<ip>/S_AXI`` are resolved by
        ``get_bd_addr_segs`` itself).
    Returns the resulting address_map summary (per-master OFFSET/RANGE parsed
    from get_bd_addr_segs output, same shape as platform_export_manifest).
    Idempotent: already-assigned segments are a no-op and the returned map
    reflects the current state. Only admitted in PLATFORM_DESIGN and never
    advances the stage.
    """
    if segments is not None and (not isinstance(segments, list) or not segments):
        raise PlatformError("segments must be a non-empty list", "INVALID_ARGUMENT")
    if segments:
        lines = []
        for seg in segments:
            if not isinstance(seg, str) or not seg.strip():
                raise PlatformError("each segment must be a non-empty string",
                                    "INVALID_ARGUMENT")
            lines.append(
                f"assign_bd_address [get_bd_addr_segs {{{seg.strip()}}}]")
        await _run_tcl(adapter, "\n".join(lines), "assign_address")
    else:
        await _run_tcl(adapter, "assign_bd_address", "assign_address")
    addr_data = await _run_tcl(adapter, _ADDRESS_MAP_QUERY_TCL,
                               "get_address_map")
    address_map = _parse_manifest_address_map(_tcl_output(addr_data))
    return {"status": "success", "data": {
        "address_map": address_map,
        "assigned": bool(address_map),
        "segments": segments,
    }}


# ═══════════════════════════════════════════
#  6. Validation & export
# ═══════════════════════════════════════════

async def platform_validate(adapter) -> dict:
    """Validate the open Block Design (atom API).

    Runs ``validate_bd_design -force`` — the ``-force`` flag invalidates the
    tool's "already validated" cache so real errors / critical warnings always
    surface on every call (B11 phase ③.1 D7: without it a second validate can
    falsely pass while the design is still broken). Scans the output for
    errors / critical warnings and fails closed with BD_VALIDATION_FAILED
    when any are found.
    """
    try:
        vdata = await _run_tcl(adapter, "validate_bd_design -force", "validate_bd")
    except AdapterError as e:
        raise BdValidationError(f"validate_bd_design failed: {e}")
    vtxt = _tcl_output(vdata).lower()
    issues = []
    if "error" in vtxt and "error: 0" not in vtxt.replace(" ", ""):
        issues.append("validate_bd_design reported errors")
    if "critical warning" in vtxt:
        issues.append("validate_bd_design reported critical warnings")
    if issues:
        raise BdValidationError("; ".join(issues))
    return {"status": "success", "data": {"validation": "passed"}}


async def platform_synthesize(adapter, *, jobs: int | None = None) -> dict:
    """Run top-level synthesis so the exported XSA contains HDF (atom API).

    Architecture §4.3.3 第八类 / B11 勘误 §4 (D3): ``write_hw_platform`` only
    packs hardware handoff data (platform_bd.hwh, hwdef.xml, ps7_init.*) into
    the XSA after the design is synthesized. The BD must first be set as the
    synthesis top (real-Vivado verified: without it launch_runs fails with
    "Top module not set for synthesis run"). Tcl:

      set_property top platform_bd [current_fileset]
      launch_runs synth_1 -jobs <N>
      wait_on_run synth_1
      open_run synth_1

    The run STATUS is queried afterwards (``get_property STATUS [get_runs
    synth_1]``) and a non-complete status fails closed with SYNTHESIS_FAILED.
    WNS is reported when timing paths exist (an unconstrained platform BD
    usually has none → wns=None). Long-running (SYNTH_TIMEOUT_S). Only
    admitted in PLATFORM_DESIGN and never advances the stage.

    ``jobs`` defaults to 1: real-Vivado verified on this install, a multi-IP
    BD with ``-jobs > 1`` launches the IP OOC synthesis runs in parallel and
    the extra concurrent vivado processes exceed the license's feature
    capacity ("Failed to load feature 'core'"); ``-jobs 1`` runs them serially
    and reliably completes. Callers may raise it on machines with enough
    concurrent-license headroom.
    """
    if jobs is not None and (isinstance(jobs, bool) or not isinstance(jobs, int)
                             or jobs <= 0):
        raise PlatformError("jobs must be a positive integer", "INVALID_ARGUMENT")
    n = jobs if jobs is not None else 1
    cmd = ("set_property top platform_bd [current_fileset]\n"
           f"launch_runs synth_1 -jobs {n}\n"
           "wait_on_run synth_1\n"
           "open_run synth_1")
    try:
        await _run_tcl(adapter, cmd, "synthesize", timeout=SYNTH_TIMEOUT_S)
    except TclError as e:
        # D6: a Tcl-level synthesis failure is SYNTHESIS_FAILED; an adapter
        # failure (AdapterError) keeps ADAPTER_NOT_READY and propagates.
        raise SynthesisError(f"launch_runs synth_1 failed: {e}")

    status_data = await _run_tcl(adapter,
                                 "puts [get_property STATUS [get_runs synth_1]]",
                                 "synth_status")
    status = _tcl_output(status_data).strip()
    if "complete" not in status.lower():
        raise SynthesisError(f"synth_1 status not complete: {status!r}")

    wns = None
    wns_data = await _run_tcl(adapter,
        "if {[llength [get_timing_paths -quiet -setup -max_paths 1]] > 0} {\n"
        "  puts [get_property SLACK [get_timing_paths -setup -max_paths 1]]\n"
        "} else {\n"
        "  puts N/A\n"
        "}", "synth_wns")
    wns_text = _tcl_output(wns_data).strip()
    if wns_text and wns_text != "N/A":
        try:
            wns = float(wns_text)
        except ValueError:
            wns = None
    return {"status": "success", "data": {
        "run": "synth_1", "status": status, "wns": wns, "jobs": n}}


async def platform_generate_wrapper(adapter, *, project_path: str) -> dict:
    """Generate the BD wrapper HDL and copy it under {project_path}/hdl (atom API).

    Tcl: make_wrapper -files [get_files *.bd] -top, then the generated
    *_{bd}_wrapper.v is located under {project_path}/vivado and copied to hdl/.
    """
    if not isinstance(project_path, str) or not project_path.strip():
        raise PlatformError("project_path must be a non-empty string", "INVALID_ARGUMENT")
    try:
        await _run_tcl(adapter, "make_wrapper -files [get_files *.bd] -top",
                       "make_wrapper")
    except AdapterError as e:
        raise WrapperExportError(str(e))

    hdl_dir = os.path.join(project_path, "hdl")
    os.makedirs(hdl_dir, exist_ok=True)
    wrapper_src = None
    wrapper_name = None
    vivado_dir = os.path.join(project_path, "vivado")
    if os.path.isdir(vivado_dir):
        for root, _dirs, files in os.walk(vivado_dir):
            for f in files:
                if f.endswith("_wrapper.v"):
                    wrapper_src = os.path.join(root, f)
                    wrapper_name = f
                    break
            if wrapper_src:
                break
    if not wrapper_src or not os.path.isfile(wrapper_src):
        raise WrapperExportError(f"Wrapper not found under {vivado_dir}")
    dest = os.path.join(hdl_dir, wrapper_name)
    shutil.copy2(wrapper_src, dest)
    if not os.path.isfile(dest):
        raise WrapperExportError("Wrapper file not created")
    return {"status": "success", "data": {
        "wrapper_path": dest,
        "wrapper_name": wrapper_name,
        "wrapper_sha256": _sha256_file(dest),
    }}


async def platform_export_hardware(adapter, *, path: str | None = None,
                                   project_path: str | None = None) -> dict:
    """Export a hardware platform (.xsa) (atom API).

    When ``path`` is omitted the default {project_path}/platform.xsa is used.
    The produced file is verified to exist and hashed (fail-closed).
    """
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise PlatformError("path must be a non-empty string", "INVALID_ARGUMENT")
    out_path = path
    if not out_path:
        if not isinstance(project_path, str) or not project_path.strip():
            raise PlatformError("path or project_path required", "INVALID_ARGUMENT")
        out_path = os.path.join(project_path, "platform.xsa")
    try:
        await _run_tcl(adapter,
                       f"write_hw_platform -fixed -force -file {{{out_path}}}",
                       "export_hardware")
    except AdapterError as e:
        raise XsaExportError(str(e))
    if not os.path.isfile(out_path):
        raise XsaExportError("XSA file not created")
    # B13-M3: deterministic normalization — content-equivalent exports must be
    # byte-identical so the manifest revision depends on CONTENT only (the
    # real-board manifest drift 307130c4 -> 6bf2e166 is thereby eliminated).
    from mcps.zynq_mcp.domains.platform.xsa_normalize import normalize_xsa
    normalize_xsa(out_path)
    return {"status": "success", "data": {
        "xsa_path": out_path,
        "xsa_sha256": _sha256_file(out_path),
    }}


def _parse_manifest_address_map(tcl_output: str) -> dict:
    """Parse per-master ``get_bd_addr_segs`` output into an address_map dict.

    Each output line is "<master> <segment> <OFFSET> <RANGE>", e.g.
    ``processing_system7_0/M_AXI_GP0 my_slave_0/S_AXI/reg0 0x0000000040000000 64K``.
    The OFFSET is normalized (``0x0000000040000000`` -> ``0x40000000``) so
    addresses are canonical 0x-prefixed hex in the published manifest. Lines
    with fewer than 4 tokens are ignored (fail-soft on partial output).

    B11 ③.1 (D8, real-Vivado verified): the query reports master-side segment
    names of the form ``processing_system7_0/Data/SEG_<ip>_Reg``; the map key
    is the slave IP extracted from that name (falling back to the first path
    component for the historical ``<ip>/<intf>/<seg>`` form).
    """
    amap = {}
    for line in tcl_output.splitlines():
        tokens = line.split()
        if len(tokens) < 4:
            continue
        master, seg, offset, rng = tokens[0], tokens[1], tokens[2], tokens[3]
        m = re.search(r"SEG_(.+)_Reg$", seg)
        ip = m.group(1) if m else seg.split("/")[0].lstrip("/")
        base = offset
        if base.lower().startswith("0x"):
            try:
                base = hex(int(base, 16))
            except ValueError:
                pass
        amap[ip] = {"base": base, "range": rng, "master": master}
    return amap


async def platform_export_manifest(adapter, *, path: str | None = None,
                                   project_path: str | None = None,
                                   board_id: str | None = None,
                                   board_profile_sha256: str | None = None) -> dict:
    """Re-export the structured platform manifest JSON from the open BD (atom API).

    Standalone query/set: unlike the removed B05 shortcut ``platform_generate``
    (which exported the manifest internally), this atom re-publishes the
    platform manifest on demand from the CURRENT Block Design state. The BD
    must be ready (a design open) and the wrapper + XSA must already exist
    under ``{project_path}`` — the platform schema requires both files (path +
    SHA256), so missing artifacts fail closed with
    MANIFEST_GENERATION_FAILED.

    Live data extracted from the BD:
      - ip_list:     get_bd_cells *
      - address_map: per-master get_bd_addr_segs (OFFSET / RANGE)
      - clock_tree:  fan-out of the processing_system7_0/FCLK_CLK0 net
    ``board_id`` / ``board_profile_sha256`` are injected from the session
    context (the authoritative board profile hash used by verify_consistency).

    ``path`` (optional) is the exact output file. When omitted the default
    ``{project_path}/manifests/platform/sha256_<rev>.json`` is used. The
    filename must match the computed revision (publish_manifest enforces it).
    Reuses publish_manifest() — an unchanged re-export returns
    ``"already_exists_same"`` without overwriting (idempotent).

    Stage machine (B11 phase 2 decision (a)): this is the terminal atom of
    the platform sequence. On success the CommandRunner advances the workflow
    stage PLATFORM_DESIGN → PL_GENERATE and publishes ``platform_revision``
    into the session context (``_context_updates``) — the revision
    pl_generate_system_top binds against. Both effects replace the removed
    B05 shortcut platform_generate.
    """
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise PlatformError("path must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(project_path, str) or not project_path.strip():
        raise PlatformError("project_path must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(board_id, str) or not board_id.strip():
        raise PlatformError("board_id must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(board_profile_sha256, str) or not board_profile_sha256.strip():
        raise PlatformError("board_profile_sha256 must be a non-empty string", "INVALID_ARGUMENT")
    if not is_sha256(board_profile_sha256):
        raise PlatformError("board_profile_sha256 must be a valid sha256", "INVALID_ARGUMENT")

    pp = str(Path(project_path).resolve())

    # 1. BD must be ready — fail closed when no design is open.
    bd_data = await _run_tcl(adapter, "puts [llength [get_bd_designs -quiet]]",
                             "count_bd_designs")
    if _tcl_output(bd_data).strip() == "0":
        raise ManifestError("No Block Design open — run platform_add_ps7 first")

    # 2. IP list from the open BD. Results are printed with puts — the Tcl
    #    bridge captures stdout only, never a bare command return value (D8).
    #    Vivado prints object paths with a leading '/' — stripped so the
    #    manifest carries plain cell names.
    ips_data = await _run_tcl(adapter, "puts [get_bd_cells *]", "list_bd_cells")
    ip_list = [tok.lstrip("/") for tok in
               re.split(r"\s+", _tcl_output(ips_data).strip()) if tok]

    # 3. Address map: every master's segments (OFFSET / RANGE).
    addr_data = await _run_tcl(adapter, _ADDRESS_MAP_QUERY_TCL, "get_address_map")
    address_map = _parse_manifest_address_map(_tcl_output(addr_data))

    # 4. Clock tree: fan-out of the PS7 FCLK_CLK0 net (empty when absent).
    #    Full pin paths ("<cell>/<pin>") are printed via the object's string
    #    form trimmed of the leading '/' — B11 ③.1 D9 restores the B09
    #    manifest readability that short pin names broke (verified: bd pins
    #    have no PARENT property, so the path is taken from the object name).
    clk_data = await _run_tcl(adapter,
        "foreach p [get_bd_pins -quiet -of_objects [get_bd_nets -quiet "
        "-of_objects [get_bd_pins -quiet processing_system7_0/FCLK_CLK0]]] {\n"
        "  puts [string trimleft $p /]\n"
        "}", "get_clock_tree")
    clk_pins = [tok for tok in re.split(r"\s+", _tcl_output(clk_data).strip()) if tok]
    clock_tree = {"FCLK_CLK0": clk_pins} if clk_pins else {}

    # 5. Wrapper + XSA must exist under the project (schema requires them).
    hdl_dir = os.path.join(pp, "hdl")
    wrapper_name = None
    if os.path.isdir(hdl_dir):
        for f in sorted(os.listdir(hdl_dir)):
            if f.endswith("_wrapper.v"):
                wrapper_name = f
                break
    if not wrapper_name:
        raise ManifestError(f"No BD wrapper under {hdl_dir} — run "
                            "platform_generate_wrapper first")
    wrapper_path = os.path.join(hdl_dir, wrapper_name)
    wrapper_sha = _sha256_file(wrapper_path)
    xsa_path = os.path.join(pp, "platform.xsa")
    if not os.path.isfile(xsa_path):
        raise ManifestError("platform.xsa not found — run "
                            "platform_export_hardware first")
    xsa_sha = _sha256_file(xsa_path)

    # 6. Preset from the board package (config_files revision input).
    bp_dir = _resolve_board_package(board_id)
    preset_path = os.path.join(bp_dir, "ps7_preset.tcl")
    if not os.path.isfile(preset_path):
        raise BoardPackageNotFoundError(f"PS7 preset not found: {preset_path}")
    preset_rel = f"boards/{board_id}/ps7_preset.tcl"
    preset_sha = _sha256_file(preset_path)

    # 7. Vivado version (fallback mirrors the platform manifest publisher).
    try:
        ver_data = await _run_tcl(adapter, "puts [version -short]", "vivado_version")
        vivado_version = _tcl_output(ver_data).strip() or "2023.1"
    except AdapterError:
        vivado_version = "2023.1"

    # 8. Compute revision + build the manifest (mirror the platform manifest
    #    shape published by the B05 flow). The XSA sha is a revision input:
    #    a re-export produces the SAME revision only when BOTH the wrapper and
    #    the XSA are unchanged. If only the XSA changed (same wrapper / board
    #    profile / preset), the revision advances and the new manifest is
    #    published to its own sha256_<rev>.json path — "correct versioning"
    #    (B12 fix round #2 item #4C). A platform revision that ignored
    #    xsa_sha would collide and raise ManifestConflictError instead of
    #    versioning the re-export.
    wrapper_rel = f"hdl/{wrapper_name}"
    revision_inputs = {
        "board_profile_sha256": board_profile_sha256,
        "tool_versions": {"vivado": vivado_version},
        "source_files": [{"path": wrapper_rel, "sha256": wrapper_sha}],
        "config_files": [{"path": preset_rel, "sha256": preset_sha}],
        "xsa_sha256": xsa_sha,
    }
    platform_revision = compute_revision(revision_inputs)

    manifest_dir = os.path.join(pp, "manifests", "platform")
    os.makedirs(manifest_dir, exist_ok=True)

    def _rev_to_fn(rev):
        return f"sha256_{rev[7:]}.json" if rev.startswith("sha256:") else f"{rev}.json"

    out_path = path if path else os.path.join(manifest_dir,
                                              _rev_to_fn(platform_revision))

    published_dict = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": board_profile_sha256,
        "platform_revision": platform_revision, "manifest_revision": platform_revision,
        "revision_inputs": revision_inputs,
        "xsa_path": "platform.xsa", "xsa_sha256": xsa_sha,
        "bd_wrapper_path": wrapper_rel, "bd_wrapper_sha256": wrapper_sha,
        "address_map": address_map, "clock_tree": clock_tree,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "locked",
    }
    manifest_json = json.dumps(published_dict, sort_keys=True, ensure_ascii=False)

    try:
        publish_result = _publish_manifest(manifest_json, out_path, resolve_root=pp)
    except (ValueError, ManifestConflictError) as e:
        raise ManifestError(str(e))

    if not os.path.isfile(out_path):
        raise ManifestError("Manifest file not created")
    return {"status": "success", "data": {
        "manifest_path": out_path,
        "manifest_sha256": _sha256_file(out_path),
        "platform_revision": platform_revision,
        "publish": publish_result,
        "ip_list": ip_list,
        "address_map": address_map,
        "clock_tree": clock_tree,
        "wrapper_name": wrapper_name,
    }, "_context_updates": {"platform_revision": platform_revision}}


# ── user IP packaging helpers (B13-M2) ────────────────────────────────
# The ipx flow needs its own Vivado project, but the session's persistent
# Vivado may hold the OPEN design project whose BD lives in memory (no atom
# ever calls save_bd_design). create_project in that session would close the
# design and silently discard the BD — so packaging runs in a THROWAWAY
# ``vivado -mode batch`` subprocess; only the non-destructive repo
# registration (ip_repo_paths + update_ip_catalog) runs in-session.
# Same executable-resolution order the Vivado adapter uses (VIVADO_EXEC →
# $VIVADO_ROOT/bin → default install → PATH).

_DEFAULT_VIVADO_BIN = "D:/Xilinx/Vivado/2023.1/bin"
_VIVADO_NAME_VARIANTS = ("vivado.bat", "vivado.exe", "vivado")


def _find_vivado_batch_exe() -> str | None:
    val = os.environ.get("VIVADO_EXEC", "").strip()
    if val:
        if os.path.isfile(val):
            return val
        for variant in _VIVADO_NAME_VARIANTS:
            p = os.path.join(os.path.dirname(val), variant)
            if os.path.isfile(p):
                return p
    root = os.environ.get("VIVADO_ROOT", "").strip()
    if root:
        for variant in _VIVADO_NAME_VARIANTS:
            p = os.path.join(root, "bin", variant)
            if os.path.isfile(p):
                return p
    for variant in _VIVADO_NAME_VARIANTS:
        p = os.path.join(_DEFAULT_VIVADO_BIN, variant)
        if os.path.isfile(p):
            return p
    return shutil.which("vivado")


def _windows_launch_cmd(exe_path: str, args: list[str]) -> list[str]:
    """On Windows, .bat wrappers must run under cmd.exe /d /c (the child
    process cannot be spawned directly — CreateProcess rejects batch files)."""
    if os.name == "nt" and exe_path.lower().endswith((".bat", ".cmd")):
        return ["cmd.exe", "/d", "/c", exe_path, *args]
    return [exe_path, *args]


def _vendor_subprocess_env() -> dict:
    """Vivado's Windows loader.bat exits silently without
    PROCESSOR_ARCHITECTURE; restore it when a launcher provided a narrow env."""
    env = os.environ.copy()
    if os.name == "nt":
        env.setdefault(
            "PROCESSOR_ARCHITECTURE",
            env.get("PROCESSOR_ARCHITEW6432", "") or "AMD64")
    return env


def _package_user_ip_tcl(sources, part, save_dir, pkg_proj, ip_name,
                         vendor, library) -> str:
    """Deterministic packaging script (real-Vivado verified 2023.1): the
    ``-in_memory`` non-project mode is deprecated and ``ipx::save_core_as``
    is not a valid 2023.1 command — file-based project + ``ipx::save_core``
    is the working flow. Re-packaging deletes only this IP's save_dir and
    the throwaway project (idempotent re-run)."""
    add_files = " ".join(f"{{{s}}}" for s in sources)
    return (
        f"file delete -force {{{pkg_proj}}}\n"
        f"file delete -force {{{save_dir}}}\n"
        f"file mkdir {{{pkg_proj}}}\n"
        f"create_project -force m2_pkg {{{pkg_proj}}} -part {{{part}}}\n"
        f"add_files {add_files}\n"
        f"ipx::package_project -root_dir {{{save_dir}}} "
        f"-vendor {vendor} -library {library} -taxonomy /UserIP -import_files "
        "-force_update_compile_order\n"
        "set_property core_revision 1 [ipx::current_core]\n"
        f"set_property name {{{ip_name}}} [ipx::current_core]\n"
        f"set_property display_name {{{ip_name}}} [ipx::current_core]\n"
        "ipx::update_checksums [ipx::current_core]\n"
        "ipx::save_core\n"
        'puts "PACKAGE_DONE"\n'
    )


async def _run_vivado_batch(script_path, log_path, *, cwd=None,
                            timeout_s=600.0) -> tuple[int, str]:
    """Run a standalone ``vivado -mode batch`` subprocess for throwaway
    packaging. Returns (returncode, combined stdout). Fail-closed on
    missing executable, launch failure, or timeout (timeout kills the whole
    Windows process tree via taskkill /T)."""
    exe = _find_vivado_batch_exe()
    if not exe:
        raise PlatformError("vivado executable not found "
                            "(VIVADO_EXEC/VIVADO_ROOT/PATH)",
                            "VIVADO_NOT_FOUND")
    if cwd is not None and not os.path.isdir(cwd):
        raise PlatformError(f"batch working dir does not exist: {cwd}",
                            "INVALID_ARGUMENT")
    cmd = _windows_launch_cmd(
        exe, ["-mode", "batch", "-source", script_path,
              "-log", log_path, "-journal", log_path + ".jou"])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=_vendor_subprocess_env())
    except OSError as e:
        raise PlatformError(f"failed to launch vivado batch: {e}",
                            "VIVADO_LAUNCH_FAILED")
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(),
                                           timeout=timeout_s)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except OSError:
            pass  # best-effort; the tree kill below is the real cleanup
        if os.name == "nt" and proc.pid:
            try:
                await asyncio.wait_for(asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL).wait(), 30)
            except (OSError, asyncio.TimeoutError):
                pass  # tree already gone or taskkill unavailable
        raise PlatformError(
            f"vivado batch timed out after {timeout_s:.0f}s",
            "USER_IP_PACKAGE_TIMEOUT")
    return proc.returncode, (stdout or b"").decode("utf-8", "replace")


def _register_user_ip_tcl(root_dir: str, vlnv: str) -> str:
    """In-session, non-destructive repo registration: append root_dir to
    ip_repo_paths (existing repos preserved, idempotent via lsearch),
    rebuild the catalog, print the VLNV read-back. Prints NO_OPEN_PROJECT
    when no project is open (the atom maps that fail-closed).

    Paths are normalized to forward slashes: an unbraced Windows path
    substituted into Tcl has its backslashes stripped (``C:\\Users`` →
    ``C:Users`` — real-Vivado probe evidence: Common 17-161 via empty
    get_ipdefs), forward slashes are inert and Vivado accepts them."""
    root_dir = root_dir.replace("\\", "/")
    return (
        "set __c [current_project -quiet]\n"
        "if {[llength $__c] == 0} {\n"
        '  puts "NO_OPEN_PROJECT"\n'
        "} else {\n"
        "  set __repos [get_property ip_repo_paths [current_project]]\n"
        f"  if {{[lsearch -exact $__repos {{{root_dir}}}] < 0}} {{\n"
        f"    set_property ip_repo_paths [concat $__repos {{{root_dir}}}] "
        "[current_project]\n"
        "  }\n"
        "  update_ip_catalog -rebuild\n"
        f"  puts \"VLNV [get_property VLNV [lindex [get_ipdefs -all {vlnv}] 0]]\"\n"
        "}\n"
    )


async def platform_package_user_ip(adapter, *, sources, ip_name,
                                   vendor="user.org", library="user",
                                   part=None, root_dir=None) -> dict:
    """Package RTL sources into a user IP and register its repo in the open
    project (B13-M2). The packaged IP becomes instantiable via
    ``platform_add_ip`` with VLNV ``<vendor>:<library>:<ip_name>:1.0``.

    Two halves (real-Vivado verified 2023.1):
      1. packaging — a throwaway ``vivado -mode batch`` subprocess creates
         its own project under ``{root_dir}/.pkg_proj``, runs the ipx flow
         (``ipx::package_project`` → core identity → ``ipx::save_core``) and
         writes the IP directory under
         ``{root_dir}/{vendor}/{library}/{ip_name}/1.0``. It NEVER touches
         the session's persistent Vivado (in-session create_project would
         close the open design project and discard the in-memory BD — no
         atom persists the BD to disk).
      2. registration — in the open project: append ``root_dir`` to
         ``ip_repo_paths`` (existing repos preserved, idempotent) +
         ``update_ip_catalog -rebuild`` + VLNV visibility check via
         ``get_ipdefs`` (fail-closed, no silent no-ops).

    ``sources`` paths are absolutized against the server cwd. Requires an
    open project (USER_IP_NO_OPEN_PROJECT otherwise — run
    platform_create_design first). Re-running re-packages the same IP
    idempotently.
    """
    if not isinstance(sources, list) or not sources or \
            not all(isinstance(s, str) and s.strip() for s in sources):
        raise PlatformError("sources must be a non-empty list of paths",
                            "INVALID_ARGUMENT")
    if not isinstance(ip_name, str) or not ip_name.strip():
        raise PlatformError("ip_name must be a non-empty string",
                            "INVALID_ARGUMENT")
    if not isinstance(root_dir, str) or not root_dir.strip():
        raise PlatformError("root_dir must be a non-empty string",
                            "INVALID_ARGUMENT")
    if not isinstance(part, str) or not part.strip():
        raise PlatformError("part is required (device part number)",
                            "INVALID_ARGUMENT")
    sources = [os.path.abspath(s) for s in sources]
    for s in sources:
        if not os.path.isfile(s):
            raise PlatformError(f"source file not found: {s}",
                                "SOURCE_NOT_FOUND")
    ip_name = ip_name.strip()
    vendor = vendor.strip() or "user.org"
    library = library.strip() or "user"
    vlnv = f"{vendor}:{library}:{ip_name}:1.0"
    # forward slashes everywhere the path lands in Tcl substitution
    # (backslash stripping in unbraced Tcl words corrupts Windows paths)
    root_dir = root_dir.strip().replace("\\", "/")
    save_dir = os.path.join(root_dir, vendor, library, ip_name, "1.0")
    pkg_proj = os.path.join(root_dir, ".pkg_proj")
    pkg_log_dir = os.path.join(root_dir, ".pkg_log")
    os.makedirs(pkg_log_dir, exist_ok=True)
    script_path = os.path.join(pkg_log_dir, "package_user_ip.tcl")
    log_path = os.path.join(pkg_log_dir, "vivado.log")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_package_user_ip_tcl(sources, part, save_dir, pkg_proj,
                                     ip_name, vendor, library))
    rc, stdout = await _run_vivado_batch(script_path, log_path,
                                         cwd=root_dir)
    if rc != 0 or "PACKAGE_DONE" not in stdout:
        raise PlatformError(
            f"user IP packaging failed (rc={rc}); tail: {stdout[-400:]}",
            "USER_IP_PACKAGE_FAILED")
    if not os.path.isfile(os.path.join(save_dir, "component.xml")):
        raise PlatformError("component.xml not created under repo",
                            "USER_IP_COMPONENT_MISSING")
    reg_tcl = _register_user_ip_tcl(root_dir, vlnv)
    try:
        res = await _run_tcl(adapter, reg_tcl, "register_user_ip")
    except AdapterError as e:
        raise PlatformError(str(e), "USER_IP_REGISTER_FAILED")
    out = (res or {}).get("output", "")
    if "NO_OPEN_PROJECT" in out:
        raise PlatformError(
            "no Vivado project open — run platform_create_design before "
            "packaging user IP (the repo must be registered in the design "
            "project)", "USER_IP_NO_OPEN_PROJECT")
    if vlnv not in out:
        raise PlatformError(
            f"VLNV {vlnv} not visible in catalog after registration "
            f"(catalog verification failed)", "USER_IP_CATALOG_VERIFY_FAILED")
    return {"status": "success", "data": {
        "vlnv": vlnv,
        "save_dir": save_dir,
        "repo_root": root_dir,
    }}


async def platform_set_bd_object_property(adapter, *, bd_object, property,
                                          value) -> dict:
    """Set a property on a BD object with read-back verification (B13-M2).

    The object kind is auto-detected: ``get_bd_ports`` is tried first, then
    ``get_bd_pins``, then ``get_bd_intf_pins`` (real-Vivado verified: a bare
    name query of the wrong kind matches nothing — D8). Real-Vivado verified
    uses (Vivado 2023.1, xc7z020):

      - port (clock-type):   ``CONFIG.FREQ_HZ``        (m_clk_port → 100000000)
      - pin (IP clock pin):  ``CONFIG.FREQ_HZ`` / ``CONFIG.ASSOCIATED_BUSIF``
        (m2_probe_0/aclk → S_AXI — the real home of ASSOCIATED_BUSIF; it is
        NOT settable on bd_ports, BD 41-1642)
      - interface pin:       ``CONFIG.PROTOCOL`` (axi_gpio_0/S_AXI → AXI4LITE)

    The set is verified by reading the property back (D-A lesson: no silent
    no-op success). Read-only parameters raise CRITICAL WARNING and read back
    empty → BD_OBJECT_PROPERTY_VERIFY_FAILED (fail-closed). Multiple matches
    are rejected as ambiguous (BD_OBJECT_AMBIGUOUS).
    """
    for arg_name, v in (("bd_object", bd_object), ("property", property)):
        if not isinstance(v, str) or not v.strip():
            raise PlatformError(f"{arg_name} must be a non-empty string",
                                "INVALID_ARGUMENT")
    if not isinstance(value, str):
        raise PlatformError("value must be a string", "INVALID_ARGUMENT")
    bd_object = bd_object.strip()
    prop = property.strip()
    tcl = (
        f"set __objs [get_bd_ports -quiet {{{bd_object}}}]\n"
        f"if {{[llength $__objs] == 0}} {{ set __objs [get_bd_pins -quiet "
        f"{{{bd_object}}}] }}\n"
        f"if {{[llength $__objs] == 0}} {{ set __objs [get_bd_intf_pins "
        f"-quiet {{{bd_object}}}] }}\n"
        f"if {{[llength $__objs] == 0}} {{ error \"BD_OBJECT_NOT_FOUND:"
        f"{{{bd_object}}}\" }}\n"
        f"if {{[llength $__objs] > 1}} {{ error \"BD_OBJECT_AMBIGUOUS:"
        f"{{{bd_object}}}\" }}\n"
        "set __obj [lindex $__objs 0]\n"
        f"set_property -dict [list {{{prop}}} {{{value}}}] $__obj\n"
        f"puts \"OBJVAL [get_property {{{prop}}} $__obj]\"\n"
    )
    try:
        res = await _run_tcl(adapter, tcl, "set_bd_object_property")
    except AdapterError as e:
        msg = str(e)
        if "BD_OBJECT_NOT_FOUND" in msg:
            raise PlatformError(msg, "BD_OBJECT_NOT_FOUND")
        if "BD_OBJECT_AMBIGUOUS" in msg:
            raise PlatformError(msg, "BD_OBJECT_AMBIGUOUS")
        raise PlatformError(msg, "BD_OBJECT_PROPERTY_FAILED")
    out = (res or {}).get("output", "")
    if f"OBJVAL {value}" not in out:
        raise PlatformError(
            f"property {prop} on {bd_object} did not read back as {value!r} "
            f"(read-only or nonexistent parameter)",
            "BD_OBJECT_PROPERTY_VERIFY_FAILED")
    return {"status": "success", "data": {
        "object": bd_object, "property": prop, "value": value,
    }}


# ═══════════════════════════════════════════
#  Dispatch registry — single source for the dispatcher
# ═══════════════════════════════════════════

PLATFORM_ATOM_MAP: dict[str, object] = {
    "platform_create_design": platform_create_design,
    "platform_get_status": platform_get_status,
    "platform_add_ps7": platform_add_ps7,
    "platform_configure_ps7": platform_configure_ps7,
    "platform_add_ip": platform_add_ip,
    "platform_list_ips": platform_list_ips,
    "platform_connect_interface": platform_connect_interface,
    "platform_connect_clock": platform_connect_clock,
    "platform_connect_reset": platform_connect_reset,
    "platform_set_address": platform_set_address,
    "platform_assign_addresses": platform_assign_addresses,
    "platform_make_external": platform_make_external,
    "platform_validate": platform_validate,
    "platform_generate_wrapper": platform_generate_wrapper,
    "platform_synthesize": platform_synthesize,
    "platform_export_hardware": platform_export_hardware,
    "platform_export_manifest": platform_export_manifest,
    "platform_package_user_ip": platform_package_user_ip,
    "platform_set_bd_object_property": platform_set_bd_object_property,
}

PLATFORM_ATOM_TOOL_NAMES: frozenset = frozenset(PLATFORM_ATOM_MAP.keys())

# command atoms (routed through the CommandRunner with the VivadoAdapter
# injected via the _pl_adapter marker — same path as PL bridge tools)
PLATFORM_ATOM_COMMAND_TOOL_NAMES: frozenset = frozenset({
    "platform_create_design", "platform_add_ps7", "platform_configure_ps7",
    "platform_add_ip", "platform_connect_interface", "platform_connect_clock",
    "platform_connect_reset", "platform_set_address",
    "platform_assign_addresses", "platform_make_external",
    "platform_validate", "platform_generate_wrapper", "platform_synthesize",
    "platform_export_hardware", "platform_export_manifest",
    "platform_package_user_ip", "platform_set_bd_object_property",
})

# query atoms (read directly by the dispatcher query handlers)
PLATFORM_ATOM_QUERY_TOOL_NAMES: frozenset = frozenset({
    "platform_get_status", "platform_list_ips",
})

# context keys injected from the session for each command atom
PLATFORM_ATOM_CONTEXT_ARGS: dict[str, tuple] = {
    "platform_create_design": ("project_path",),
    "platform_add_ps7": ("board_id",),
    "platform_configure_ps7": (),
    "platform_add_ip": (),
    "platform_connect_interface": (),
    "platform_connect_clock": (),
    "platform_connect_reset": (),
    "platform_set_address": (),
    "platform_assign_addresses": (),
    "platform_make_external": (),
    "platform_validate": (),
    "platform_generate_wrapper": ("project_path",),
    "platform_synthesize": (),
    "platform_export_hardware": ("project_path",),
    # board_id + board_profile_sha256 come from the session context; the atom
    # needs them to build the manifest's config_files / board profile fields.
    "platform_export_manifest": ("project_path", "board_id", "board_profile_sha256"),
    "platform_package_user_ip": (),
    "platform_set_bd_object_property": (),
}

# per-tool outer wait (s). Must exceed the adapter's run_tcl default
# (CALL_TOOL_TIMEOUT=30s + bridge overhead). Project / BD / XSA / synthesis
# operations are the slow ones; the rest are fast single-command sends.
PLATFORM_ATOM_TIMEOUT: dict[str, float] = {
    "platform_create_design": 300.0,
    "platform_add_ps7": 180.0,
    "platform_configure_ps7": 60.0,
    "platform_add_ip": 60.0,
    "platform_connect_interface": 60.0,
    "platform_connect_clock": 60.0,
    "platform_connect_reset": 60.0,
    "platform_set_address": 60.0,
    "platform_assign_addresses": 120.0,
    "platform_make_external": 60.0,
    "platform_validate": 180.0,
    "platform_generate_wrapper": 180.0,
    # top-level BD synthesis (launch_runs synth_1 → wait_on_run synth_1 →
    # open_run synth_1) can legitimately run several minutes; the outer wait
    # must exceed the run_tcl timeout (SYNTH_TIMEOUT_S = 1800).
    "platform_synthesize": 1860.0,
    "platform_export_hardware": 180.0,
    "platform_export_manifest": 60.0,
    # ipx packaging + catalog rebuild runs several minutes on first use
    "platform_package_user_ip": 600.0,
    "platform_set_bd_object_property": 60.0,
}
