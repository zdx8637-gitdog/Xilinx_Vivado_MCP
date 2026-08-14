"""
platform_atoms.py — Platform atomic APIs (B05-R2).

Implements the 14 composable Platform Domain atoms from B01 §7 Phase 1 /
Architecture §4.3.3. B05-R2 completed the header-table API list by adding
``platform_connect_reset`` and ``platform_export_manifest`` to the original
12 (the two APIs were listed in the §4.3.3 header table but omitted from the
detailed spec segment, so the initial 12-atom delivery skipped them). Each
function is stateless: it receives the Vivado
adapter as its first positional argument (injected by the CommandRunner via
the ``_pl_adapter`` marker for command atoms, or by the dispatcher query
handlers for query atoms) and forwards every Tcl command through the shared
``_run_tcl`` channel — the same channel the removed B05 shortcut
``platform_generate`` used, with the same cold-start retry and the same
error contract.

Command atoms do NOT advance the workflow stage (next_stage=None) except
``platform_export_manifest`` — the terminal atom of the platform sequence —
which advances PLATFORM_DESIGN → PL_GENERATE on success (B11 phase 2
decision (a)). The B05 shortcut ``platform_generate`` was removed in B11
phase 2; these atoms are the replacement path.

Atoms:
  command (12, adapter injected by CommandRunner):
    platform_create_design, platform_add_ps7, platform_configure_ps7,
    platform_add_ip, platform_connect_interface, platform_connect_clock,
    platform_connect_reset, platform_set_address, platform_validate,
    platform_generate_wrapper, platform_export_hardware,
    platform_export_manifest
  query (2, adapter injected by dispatcher query handlers):
    platform_get_status, platform_list_ips

Only existing helpers from platform_domain are reused (no platform_domain
code is modified): ``_run_tcl``, ``_tcl_output``, ``_resolve_board_package``,
``_sha256_file`` and the structured exception classes.
"""
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
    PlatformError,
    BoardPackageNotFoundError,
    AdapterError,
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
    A missing project or BD design fails closed via AdapterError.
    """
    name_data = await _run_tcl(adapter, "get_property NAME [current_project]",
                               "get_project_name")
    project_name = _tcl_output(name_data).strip()
    count_data = await _run_tcl(adapter, "llength [get_bd_cells *]",
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


async def platform_add_ip(adapter, *, vlnv: str, instance_name: str,
                          properties: dict | None = None) -> dict:
    """Instantiate an IP from the catalog (atom API, idempotent).

    If ``instance_name`` already exists, its config is compared against the
    requested ``properties``: matching → unchanged OK; differing → fail closed
    with IP_CONFIG_MISMATCH. No duplicate cell is ever created.
    """
    if not isinstance(vlnv, str) or not vlnv.strip():
        raise PlatformError("vlnv must be a non-empty string", "INVALID_ARGUMENT")
    if not isinstance(instance_name, str) or not instance_name.strip():
        raise PlatformError("instance_name must be a non-empty string", "INVALID_ARGUMENT")
    props = properties if isinstance(properties, dict) else {}

    exists_data = await _run_tcl(adapter,
        f"llength [get_bd_cells -quiet {instance_name}]", "check_ip_exists")
    exists = _tcl_output(exists_data).strip() == "1"

    if exists:
        if props:
            mismatch = {}
            for key, want in props.items():
                rdata = await _run_tcl(adapter,
                    f"get_property CONFIG.{key} [get_bd_cells {instance_name}]",
                    f"get_prop_{key}")
                got = _tcl_output(rdata).strip()
                if _norm_prop_val(got) != _norm_prop_val(want):
                    mismatch[key] = {"expected": want, "actual": got}
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
    return {"status": "success", "data": {
        "instance_name": instance_name, "vlnv": vlnv, "already_exists": False}}


async def platform_list_ips(adapter, *, filter: str | None = None) -> dict:
    """List BD cells in the open design (query atom).

    Tcl: get_bd_cells -filter {filter} (or get_bd_cells * when no filter).
    Output is split on whitespace — Vivado prints the Tcl list one per line
    or space-separated depending on the channel.
    """
    if filter is not None and (not isinstance(filter, str) or not filter.strip()):
        raise PlatformError("filter must be a non-empty string", "INVALID_ARGUMENT")
    cmd = f"get_bd_cells -filter {{{filter}}}" if filter else "get_bd_cells *"
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


# ═══════════════════════════════════════════
#  5. Address space
# ═══════════════════════════════════════════

async def platform_set_address(adapter, *, segment: str, base,
                               size: int | None = None) -> dict:
    """Set a slave segment base address (and optional size) (atom API).

    segment format: "my_slave_0/S_AXI". When ``size`` is given the matching
    CONFIG.C_HIGHADDR is derived (base + size - 1) and written in the same
    Tcl command.
    """
    if not isinstance(segment, str) or not segment.strip():
        raise PlatformError("segment must be a non-empty string", "INVALID_ARGUMENT")
    if base is None or (isinstance(base, str) and not base.strip()):
        raise PlatformError("base must be provided", "INVALID_ARGUMENT")
    base_str = str(base).strip()
    lines = [f"set_property CONFIG.C_BASEADDR {{{base_str}}} "
             f"[get_bd_addr_segs {{{segment}}}]"]
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise PlatformError("size must be a positive integer", "INVALID_ARGUMENT")
        try:
            base_int = int(base_str, 16) if base_str.lower().startswith("0x") else int(base_str, 0)
        except ValueError:
            raise PlatformError(f"Invalid base address: {base!r}", "INVALID_ARGUMENT")
        high = base_int + size - 1
        lines.append(f"set_property CONFIG.C_HIGHADDR {{{hex(high)}}} "
                     f"[get_bd_addr_segs {{{segment}}}]")
    await _run_tcl(adapter, "\n".join(lines), "set_address")
    return {"status": "success", "data": {"segment": segment, "base": base_str,
                                          "size": size}}


# ═══════════════════════════════════════════
#  6. Validation & export
# ═══════════════════════════════════════════

async def platform_validate(adapter) -> dict:
    """Validate the open Block Design (atom API).

    Scans the validate_bd_design output for errors / critical warnings and
    fails closed with BD_VALIDATION_FAILED when any are found.
    """
    try:
        vdata = await _run_tcl(adapter, "validate_bd_design", "validate_bd")
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
    """
    amap = {}
    for line in tcl_output.splitlines():
        tokens = line.split()
        if len(tokens) < 4:
            continue
        master, seg, offset, rng = tokens[0], tokens[1], tokens[2], tokens[3]
        ip = seg.split("/")[0]
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
    bd_data = await _run_tcl(adapter, "llength [get_bd_designs -quiet]",
                             "count_bd_designs")
    if _tcl_output(bd_data).strip() == "0":
        raise ManifestError("No Block Design open — run platform_add_ps7 first")

    # 2. IP list from the open BD.
    ips_data = await _run_tcl(adapter, "get_bd_cells *", "list_bd_cells")
    ip_list = [tok for tok in re.split(r"\s+", _tcl_output(ips_data).strip()) if tok]

    # 3. Address map: every master's segments (OFFSET / RANGE).
    addr_data = await _run_tcl(adapter,
        "foreach master [get_bd_intf_pins -quiet -filter {TYPE == master}] {\n"
        "  foreach seg [get_bd_addr_segs -quiet -of_objects $master] {\n"
        "    puts \"$master $seg [get_property OFFSET $seg] "
        "[get_property RANGE $seg]\"\n"
        "  }\n"
        "}", "get_address_map")
    address_map = _parse_manifest_address_map(_tcl_output(addr_data))

    # 4. Clock tree: fan-out of the PS7 FCLK_CLK0 net (empty when absent).
    clk_data = await _run_tcl(adapter,
        "foreach p [get_bd_pins -quiet -of_objects [get_bd_nets -quiet "
        "-of_objects [get_bd_pins -quiet processing_system7_0/FCLK_CLK0]]] {\n"
        "  puts [get_property NAME $p]\n"
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
    #    shape published by the B05 flow).
    wrapper_rel = f"hdl/{wrapper_name}"
    revision_inputs = {
        "board_profile_sha256": board_profile_sha256,
        "tool_versions": {"vivado": vivado_version},
        "source_files": [{"path": wrapper_rel, "sha256": wrapper_sha}],
        "config_files": [{"path": preset_rel, "sha256": preset_sha}],
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
    "platform_validate": platform_validate,
    "platform_generate_wrapper": platform_generate_wrapper,
    "platform_export_hardware": platform_export_hardware,
    "platform_export_manifest": platform_export_manifest,
}

PLATFORM_ATOM_TOOL_NAMES: frozenset = frozenset(PLATFORM_ATOM_MAP.keys())

# command atoms (routed through the CommandRunner with the VivadoAdapter
# injected via the _pl_adapter marker — same path as PL bridge tools)
PLATFORM_ATOM_COMMAND_TOOL_NAMES: frozenset = frozenset({
    "platform_create_design", "platform_add_ps7", "platform_configure_ps7",
    "platform_add_ip", "platform_connect_interface", "platform_connect_clock",
    "platform_connect_reset", "platform_set_address", "platform_validate",
    "platform_generate_wrapper", "platform_export_hardware",
    "platform_export_manifest",
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
    "platform_validate": (),
    "platform_generate_wrapper": ("project_path",),
    "platform_export_hardware": ("project_path",),
    # board_id + board_profile_sha256 come from the session context; the atom
    # needs them to build the manifest's config_files / board profile fields.
    "platform_export_manifest": ("project_path", "board_id", "board_profile_sha256"),
}

# per-tool outer wait (s). Must exceed the adapter's run_tcl default
# (CALL_TOOL_TIMEOUT=30s + bridge overhead). Project / BD / XSA operations
# are the slow ones; the rest are fast single-command sends.
PLATFORM_ATOM_TIMEOUT: dict[str, float] = {
    "platform_create_design": 300.0,
    "platform_add_ps7": 180.0,
    "platform_configure_ps7": 60.0,
    "platform_add_ip": 60.0,
    "platform_connect_interface": 60.0,
    "platform_connect_clock": 60.0,
    "platform_connect_reset": 60.0,
    "platform_set_address": 60.0,
    "platform_validate": 180.0,
    "platform_generate_wrapper": 180.0,
    "platform_export_hardware": 180.0,
    "platform_export_manifest": 60.0,
}
