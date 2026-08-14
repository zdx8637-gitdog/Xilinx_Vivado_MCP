"""
platform_domain.py — B05 Platform/AXI Domain minimum vertical slice. v3.0.0
Uses Vivado adapter via worker controller. Produces BD + wrapper + XSA + manifest.
Manifest validated with project-root-resolved paths. publish_manifest for atomic write.
"""
import hashlib, json, logging, os, re, time, uuid
from pathlib import Path

# ═══════════════════════════════════════════
#  Structured exceptions — distinct reason codes
# ═══════════════════════════════════════════

class PlatformError(Exception):
    def __init__(self, message, reason_code):
        super().__init__(message)
        self.reason_code = reason_code

class BoardPackageNotFoundError(PlatformError):
    def __init__(self, msg="Board package not found"):
        super().__init__(msg, "BOARD_PACKAGE_NOT_FOUND")

class BoardProfileMismatchError(PlatformError):
    def __init__(self, msg="Board profile SHA mismatch"):
        super().__init__(msg, "BOARD_PROFILE_MISMATCH")

class AdapterError(PlatformError):
    def __init__(self, msg="Vivado adapter not available"):
        super().__init__(msg, "ADAPTER_NOT_READY")

class BdValidationError(PlatformError):
    def __init__(self, msg):
        super().__init__(msg, "BD_VALIDATION_FAILED")

class WrapperExportError(PlatformError):
    def __init__(self, msg="Wrapper export failed"):
        super().__init__(msg, "WRAPPER_EXPORT_FAILED")

class XsaExportError(PlatformError):
    def __init__(self, msg="XSA export failed"):
        super().__init__(msg, "XSA_EXPORT_FAILED")

class ManifestError(PlatformError):
    def __init__(self, msg="Manifest generation failed"):
        super().__init__(msg, "MANIFEST_GENERATION_FAILED")


# generate_target all generates the BD IP output products (OOC synthesis for
# each BD IP) and can take minutes. It is a NECESSARY but NOT sufficient
# prerequisite for write_hw_platform to emit hardware handoff data (HDF).
# The 30s bridge default would poison the Vivado worker mid-command, so the
# generate_target call passes an explicit generous timeout (seconds) covering
# both the adapter round-trip and the old server's run_tcl completion-marker
# wait.
GENERATE_TARGET_TIMEOUT_S = 600.0

# Top-level synthesis (launch_runs synth_1 → wait_on_run synth_1) produces the
# HDF that write_hw_platform packs into the XSA: the BD hardware handoff
# (platform_bd.hwh), hardware definition (hwdef.xml, *.bda) and ps7_init.*
# files. generate_target alone is insufficient — without synthesis Vivado logs
# "CRITICAL WARNING [Project 1-1924] Failed to write hardware handoff data"
# and the exported XSA contains only xsa.json + xsa.xml, which XSCT rejects
# with [HDF 64-4]. Synthesis of the small platform BD takes a few minutes, so
# this step uses an explicit generous timeout (seconds).
SYNTH_TIMEOUT_S = 1800.0

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
#  Board package resolution
# ═══════════════════════════════════════════

def _resolve_board_package(board_id: str) -> str:
    here = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = here / "boards" / board_id
        if candidate.is_dir():
            return str(candidate.resolve())
        here = here.parent
    raise BoardPackageNotFoundError(f"Board package not found: {board_id}")


def _load_board_profile(board_package_dir: str) -> dict:
    bp_name = Path(board_package_dir).name
    profile_file = Path(board_package_dir) / f"board_profile_{bp_name}.json"
    if not profile_file.is_file():
        raise BoardPackageNotFoundError(f"Board profile not found: {profile_file}")
    with open(str(profile_file), "r", encoding="utf-8") as f:
        return json.load(f)


def _sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return "sha256:" + h.hexdigest()


# ═══════════════════════════════════════════
#  Vivado Tcl helper — cold-start retry only for VIVADO_COLD_START
#  All other Tcl failures propagate as PlatformError subtypes per step.
# ═══════════════════════════════════════════

_PLATFORM_STEP_BY_LABEL = {
    "create_project": "PROJECT_CREATE",
    "create_design": "PROJECT_CREATE",
    "create_bd": "BD_CREATE",
    "create_ps7": "PS7_CONFIGURE",
    "ps7_automation": "PS7_CONFIGURE",
    "source_ps7_preset": "PS7_CONFIGURE",
    "apply_preset": "PS7_CONFIGURE",
    "configure_ps7": "PS7_CONFIGURE",
    "add_axi_gpio": "AXI_CONNECT",
    "add_reset": "AXI_CONNECT",
    "add_smartconnect": "AXI_CONNECT",
    "connect_axi": "AXI_CONNECT",
    "connect_clocks": "AXI_CONNECT",
    "connect_resets": "AXI_CONNECT",
    "gpio_external": "AXI_CONNECT",
    "assign_address": "ADDRESS_ASSIGN",
    "get_addr": "ADDRESS_ASSIGN",
    "validate_bd": "BD_VALIDATE",
    "save_bd": "BD_VALIDATE",
    "generate_target": "GENERATE_TARGET",
    "make_wrapper": "GENERATE_TARGET",
    "add_wrapper_to_project": "GENERATE_TARGET",
    "synthesize": "SYNTHESIS",
    "synth_status": "SYNTHESIS",
    "open_synth_run": "SYNTHESIS",
    "export_xsa": "XSA_EXPORT",
    "vivado_version": "XSA_EXPORT",
}


async def _run_tcl(adapter, command: str, label: str, timeout: float | None = None) -> dict:
    """Send Tcl command through adapter. Returns parsed success dict.
    Cold-start: retries up to 6x with escalating delay.
    All other failures: raises AdapterError with the error message.
    Callers interpret the result text for validation/export errors.
    ``timeout`` (seconds) is optional. When set it is forwarded to the old
    server's run_tcl tool (completion-marker wait) AND to the adapter
    call_tool (MCP round-trip), so long-running Tcl such as ``generate_target
    all`` does not hit the 30s bridge default and poison the worker. When
    None the call behaves exactly as before (bridge default timeout)."""
    import asyncio as _asyncio

    if hasattr(adapter, "set_current_step"):
        adapter.set_current_step(
            _PLATFORM_STEP_BY_LABEL.get(label, str(label).upper()))

    for attempt in range(6):
        try:
            if timeout is not None:
                resp = await adapter.call_tool(
                    "run_tcl",
                    {"command": command.strip(), "timeout": timeout},
                    timeout=timeout,
                )
            else:
                resp = await adapter.call_tool("run_tcl", {"command": command.strip()})
        except Exception as e:
            raise AdapterError(str(e))
        data = resp.to_dict() if hasattr(resp, 'to_dict') else resp
        if not isinstance(data, dict):
            raise AdapterError(f"Bad response for '{label}': not a dict")
        if data.get("status") == "success":
            return data
        err = data.get("error", {})
        msg = str(err.get("message", str(err)))
        details = err.get("details", {})
        rc = details.get("reason_code", "")
        if "cold start" in msg.lower() or rc == "VIVADO_COLD_START":
            await _asyncio.sleep(20.0 + attempt * 10.0)
            continue
        # Non-cold-start error: raise as AdapterError (caller maps to domain error)
        raise AdapterError(msg)
    raise AdapterError(f"'{label}': Vivado cold start not resolved")


def _top_bd_command(bd_name: str, action: str) -> str:
    """Build fail-closed Tcl for exactly the parent Block Design.

    A wildcard containing ``platform_bd`` also matches nested SmartConnect
    sub-design files below ``*.gen/.../bd_0/*.bd`` after IP generation. Vivado
    rejects generating such a nested design directly (12-3563). Selecting the
    exact top-level basename is stable because ``create_bd_design`` owns that
    name and nested generated files have different basenames.
    """
    if (not isinstance(bd_name, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bd_name)):
        raise ValueError("bd_name must be a plain Tcl identifier")
    if action not in ("generate_target", "make_wrapper"):
        raise ValueError("unsupported top-BD action")
    select = (
        f"set __platform_bd [get_files -quiet {{{bd_name}.bd}}]\n"
        "if {[llength $__platform_bd] != 1} {\n"
        f"  error \"PLATFORM_BD_SELECTION_FAILED:{bd_name}:"
        "[llength $__platform_bd]\"\n"
        "}\n"
    )
    if action == "generate_target":
        return select + "generate_target all $__platform_bd"
    return select + "make_wrapper -files $__platform_bd -top"


def _tcl_output(data: dict) -> str:
    d = data.get("data", {})
    return str(d.get("output", "")) if isinstance(d, dict) else str(d)


# ═══════════════════════════════════════════
#  Address map
# ═══════════════════════════════════════════

EXPECTED_GPIO_ADDRESS = "0x41200000"


def _parse_gpio_address(tcl_output: str) -> str | None:
    for line in tcl_output.splitlines():
        if "axi_gpio" in line.lower() and "0x" in line:
            m = re.search(r'(0x[0-9a-fA-F]+)', line)
            if m:
                return m.group(1).lower()
    return None


# ═══════════════════════════════════════════
#  generate_platform — main entry point
# ═══════════════════════════════════════════

async def generate_platform(
    project_path: str,
    board_id: str,
    board_profile_sha256: str,
    board_package_revision: str,
    session_id: str = "",
    adapter=None,
) -> dict:
    if adapter is None:
        raise AdapterError("Vivado adapter not available")

    bp_dir = _resolve_board_package(board_id)
    profile = _load_board_profile(bp_dir)
    part = profile.get("part", "xc7z020clg400-2")
    if not part or not isinstance(part, str):
        raise BoardPackageNotFoundError(f"No valid part in board profile: {bp_dir}")

    pp = str(Path(project_path).resolve())
    hdl_dir = os.path.join(pp, "hdl")
    os.makedirs(hdl_dir, exist_ok=True)
    bd_name = "platform_bd"

    preset_path = os.path.join(bp_dir, "ps7_preset.tcl")
    if not os.path.isfile(preset_path):
        raise BoardPackageNotFoundError(f"PS7 preset not found: {preset_path}")
    preset_tcl = open(preset_path, encoding="utf-8").read().replace("\r\n", "\n")

    proj_dir = os.path.join(pp, "vivado", "platform")

    # Idempotency check: skip Vivado BD creation if platform.xsa + manifest already exist
    xsa_candidate = os.path.join(pp, "platform.xsa")
    manifest_candidate_dir = os.path.join(pp, "manifests", "platform")
    if os.path.isfile(xsa_candidate) and os.path.isdir(manifest_candidate_dir):
        cached_manifests = sorted(
            [p for p in Path(manifest_candidate_dir).glob("sha256_*.json") if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
        if cached_manifests:
            logger.info("Platform XSA and manifest already exist, skipping regeneration")
            cached_mf_path = str(cached_manifests[-1])
            try:
                with open(cached_mf_path, "r", encoding="utf-8") as f:
                    cached_mf = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass  # corrupt manifest — fall through to regeneration
            else:
                cached_revision = cached_mf.get("platform_revision", "")
                cached_wrapper_rel = cached_mf.get("bd_wrapper_path", "")
                cached_wrapper_path = os.path.join(pp, cached_wrapper_rel) if cached_wrapper_rel else ""
                return {
                    "status": "success",
                    "data": {
                        "xsa_path": xsa_candidate,
                        "xsa_sha256": _sha256_file(xsa_candidate),
                        "wrapper_path": cached_wrapper_path,
                        "wrapper_sha256": cached_mf.get("bd_wrapper_sha256", ""),
                        "wrapper_rel": cached_wrapper_rel,
                        "manifest_path": cached_mf_path,
                        "manifest_sha256": _sha256_file(cached_mf_path),
                        "platform_revision": cached_revision,
                        "address_map": cached_mf.get("address_map", {}),
                        "bd_name": bd_name,
                    },
                    "_context_updates": {"platform_revision": cached_revision},
                    "cached": True,
                }

    # ── 1. create_project ──
    await _run_tcl(adapter, f'create_project platform_project {{{proj_dir}}} -part {part} -force',
                   "create_project")

    # ── 2. create_bd ──
    await _run_tcl(adapter, f'create_bd_design "{bd_name}"', "create_bd")

    # ── 3. create PS7 + automation ──
    await _run_tcl(adapter,
        'create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0',
        "create_ps7")
    await _run_tcl(adapter,
        'apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR"} [get_bd_cells processing_system7_0]',
        "ps7_automation")

    # ── 4. source preset, call set_ps_config ──
    await _run_tcl(adapter, preset_tcl, "source_ps7_preset")
    await _run_tcl(adapter, 'set_ps_config processing_system7_0', "apply_preset")

    # ── 5. AXI GPIO: one channel, 4-bit all-output ──
    await _run_tcl(adapter,
        'create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 axi_gpio_led\n'
        'set_property -dict [list CONFIG.C_GPIO_WIDTH {4} CONFIG.C_ALL_OUTPUTS {1} CONFIG.C_IS_DUAL {0}] [get_bd_cells axi_gpio_led]',
        "add_axi_gpio")

    # ── 6. reset ──
    await _run_tcl(adapter,
        'create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_ps7_50M',
        "add_reset")

    # ── 7. SmartConnect ──
    await _run_tcl(adapter,
        'create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 smartconnect_0\n'
        'set_property -dict [list CONFIG.NUM_SI {1}] [get_bd_cells smartconnect_0]',
        "add_smartconnect")

    # ── 8. AXI connections ──
    await _run_tcl(adapter,
        'connect_bd_intf_net [get_bd_intf_pins processing_system7_0/M_AXI_GP0] [get_bd_intf_pins smartconnect_0/S00_AXI]\n'
        'connect_bd_intf_net [get_bd_intf_pins smartconnect_0/M00_AXI] [get_bd_intf_pins axi_gpio_led/S_AXI]',
        "connect_axi")

    # ── 9. Clocks ──
    await _run_tcl(adapter,
        'connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins processing_system7_0/M_AXI_GP0_ACLK]\n'
        'connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins smartconnect_0/aclk]\n'
        'connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins axi_gpio_led/s_axi_aclk]\n'
        'connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] [get_bd_pins rst_ps7_50M/slowest_sync_clk]',
        "connect_clocks")

    # ── 10. Resets ──
    await _run_tcl(adapter,
        'connect_bd_net [get_bd_pins processing_system7_0/FCLK_RESET0_N] [get_bd_pins rst_ps7_50M/ext_reset_in]\n'
        'connect_bd_net [get_bd_pins rst_ps7_50M/peripheral_aresetn] [get_bd_pins axi_gpio_led/s_axi_aresetn]\n'
        'connect_bd_net [get_bd_pins rst_ps7_50M/interconnect_aresetn] [get_bd_pins smartconnect_0/aresetn]',
        "connect_resets")

    # ── 11. GPIO external LED port — single-channel, gpio_io_o → gpio_led[3:0] ──
    await _run_tcl(adapter,
        'create_bd_port -dir O -from 3 -to 0 gpio_led\n'
        'connect_bd_net [get_bd_pins axi_gpio_led/gpio_io_o] [get_bd_ports gpio_led]',
        "gpio_external")

    # ── 12. Assign addresses ──
    try:
        await _run_tcl(adapter, 'assign_bd_address', "assign_address")
    except AdapterError as e:
        raise BdValidationError(f"assign_bd_address failed: {e}")

    # ── 13. Validate BD ──
    try:
        vdata = await _run_tcl(adapter, 'validate_bd_design', "validate_bd")
    except AdapterError as e:
        raise BdValidationError(f"validate_bd_design failed: {e}")
    vtxt = _tcl_output(vdata).lower()
    if "error" in vtxt and "error: 0" not in vtxt.replace(" ", ""):
        raise BdValidationError(vtxt[:400])
    if "critical warning" in vtxt:
        raise BdValidationError(vtxt[:400])

    # ── 14. Verify GPIO address ──
    try:
        addr_data = await _run_tcl(adapter,
            'foreach seg [get_bd_addr_segs -of_objects [get_bd_intf_pins processing_system7_0/M_AXI_GP0]] {\n'
            '  puts "$seg [get_property OFFSET $seg] [get_property RANGE $seg]"\n'
            '}',
            "get_addr")
    except AdapterError as e:
        raise BdValidationError(f"Cannot read address map: {e}")
    gpio_addr = _parse_gpio_address(_tcl_output(addr_data))
    if gpio_addr != EXPECTED_GPIO_ADDRESS:
        raise BdValidationError(f"GPIO address {gpio_addr} != expected {EXPECTED_GPIO_ADDRESS}")

    # ── 15. Save BD ──
    await _run_tcl(adapter, 'save_bd_design', "save_bd")

    # ── 15a. generate_target all — IP output products for wrapper + synth ──
    # Legacy G10/G11 run this before wrapper creation. It generates the BD IP
    # output products (OOC netlists, .hwh handoff under .gen) that
    # make_wrapper and the subsequent synth run consume. It is NECESSARY but
    # not sufficient for HDF in the XSA (see 15d). Runs OOC IP synthesis and
    # can take minutes → explicit generous timeout (GENERATE_TARGET_TIMEOUT_S).
    try:
        await _run_tcl(adapter,
            _top_bd_command(bd_name, "generate_target"),
            "generate_target", timeout=GENERATE_TARGET_TIMEOUT_S)
    except AdapterError as e:
        raise XsaExportError(f"generate_target all failed: {e}")

    # ── 15b. Generate wrapper ──
    try:
        await _run_tcl(adapter,
            _top_bd_command(bd_name, "make_wrapper"), "make_wrapper")
    except AdapterError as e:
        raise WrapperExportError(str(e))

    # Copy wrapper to hdl/ — find generated wrapper via OS-level search (reliable)
    wrapper_src = None
    wrapper_name = f"{bd_name}_wrapper.v"
    for root, dirs, files in os.walk(proj_dir):
        if wrapper_name in files:
            wrapper_src = os.path.join(root, wrapper_name)
            break
    if wrapper_src and os.path.isfile(wrapper_src):
        import shutil as _shutil
        _shutil.copy2(wrapper_src, os.path.join(hdl_dir, wrapper_name))
    else:
        raise WrapperExportError(f"Wrapper not found under {proj_dir}")

    wrapper_path = os.path.join(hdl_dir, f"{bd_name}_wrapper.v")
    if not os.path.isfile(wrapper_path):
        raise WrapperExportError("Wrapper file not created")
    wrapper_sha = _sha256_file(wrapper_path)
    if wrapper_sha == "sha256:" + "0" * 64:
        raise WrapperExportError("Wrapper is empty")

    # ── 15c. Add wrapper to the Vivado project + set top (synthesis needs it) ──
    try:
        await _run_tcl(adapter,
            f'add_files -norecurse {{{wrapper_path}}}\n'
            f'set_property top {bd_name}_wrapper [current_fileset]',
            "add_wrapper_to_project")
    except AdapterError as e:
        raise XsaExportError(f"add wrapper to project failed: {e}")

    # ── 15d. Synthesis — required so write_hw_platform emits HDF ──
    # The hardware handoff files (platform_bd.hwh / hwdef.xml / ps7_init.*)
    # packed into the XSA are produced by top-level synthesis, not by
    # generate_target. Without this step write_hw_platform logs "CRITICAL
    # WARNING [Project 1-1924] Failed to write hardware handoff data" and the
    # XSA contains only xsa.json + xsa.xml, which XSCT rejects with [HDF 64-4].
    if hasattr(adapter, "run_vivado_run"):
        synth_result = await adapter.run_vivado_run(
            run_name="synth_1",
            launch_tcl="launch_runs synth_1 -jobs 4",
            current_step="SYNTHESIS",
            timeout_s=SYNTH_TIMEOUT_S,
            open_run=True,
        )
        if synth_result.get("status") != "success":
            err = synth_result.get("error", {})
            raise XsaExportError(
                f"synthesis failed: {err.get('message', str(err))}")
    else:
        # Historical component-test adapter compatibility.  The production
        # O3 facade always takes the observable run path above.
        try:
            await _run_tcl(adapter,
                'launch_runs synth_1 -jobs 4\nwait_on_run synth_1',
                "synthesize", timeout=SYNTH_TIMEOUT_S)
        except AdapterError as e:
            raise XsaExportError(f"synthesis failed: {e}")
        synth_status = _tcl_output(await _run_tcl(adapter,
            'get_property STATUS [get_runs synth_1]', "synth_status"))
        if "error" in synth_status.lower():
            raise XsaExportError(
                f"synthesis did not complete: {synth_status[:400]}")
        try:
            await _run_tcl(adapter, 'open_run synth_1', "open_synth_run")
        except AdapterError as e:
            raise XsaExportError(f"open synthesis run failed: {e}")

    # ── 16. Export XSA (no bitstream) ──
    xsa_path = os.path.join(pp, "platform.xsa")
    try:
        await _run_tcl(adapter, f'write_hw_platform -fixed -force -file {{{xsa_path}}}', "export_xsa")
    except AdapterError as e:
        raise XsaExportError(str(e))
    if not os.path.isfile(xsa_path):
        raise XsaExportError("XSA file not created")
    xsa_sha = _sha256_file(xsa_path)

    # ── 17. Vivado version ──
    try:
        ver_data = await _run_tcl(adapter, 'puts [version -short]', "vivado_version")
    except AdapterError:
        vivado_version = "2023.1"
    else:
        vivado_version = _tcl_output(ver_data).strip() or "2023.1"

    # ── 18. Build manifest with relative paths, publish via shared publisher ──
    # publish_manifest validates with project-resolved paths (resolve_root)
    # but persists relative paths — B04 _validate_contained() requires relative.
    from mcps.common.revision import compute_revision
    from mcps.common.artifact_schema import publish_manifest as _publish_manifest

    bd_wrapper_rel = f"hdl/{bd_name}_wrapper.v"
    xsa_rel = "platform.xsa"
    preset_rel = f"boards/{board_id}/ps7_preset.tcl"

    address_map = {"axi_gpio_led": {
        "base": EXPECTED_GPIO_ADDRESS, "range": "64K",
        "master": "processing_system7_0/M_AXI_GP0"}}
    clock_tree = {"FCLK_CLK0": ["processing_system7_0/M_AXI_GP0_ACLK",
        "smartconnect_0", "axi_gpio_led", "rst_ps7_50M"]}

    revision_inputs = {
        "board_profile_sha256": board_profile_sha256,
        "tool_versions": {"vivado": vivado_version},
        "source_files": [{"path": bd_wrapper_rel, "sha256": wrapper_sha}],
        "config_files": [{"path": preset_rel, "sha256": _sha256_file(preset_path)}],
    }

    platform_revision = compute_revision(revision_inputs)

    manifest_dir = os.path.join(pp, "manifests", "platform")
    os.makedirs(manifest_dir, exist_ok=True)

    def _rev_to_fn(rev):
        return f"sha256_{rev[7:]}.json" if rev.startswith("sha256:") else f"{rev}.json"

    manifest_path = os.path.join(manifest_dir, _rev_to_fn(platform_revision))

    # Build manifest with RELATIVE paths; publish_manifest validates against
    # project-root-resolved paths but persists the relative-path version.
    published_dict = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": board_profile_sha256,
        "platform_revision": platform_revision, "manifest_revision": platform_revision,
        "revision_inputs": revision_inputs,
        "xsa_path": xsa_rel, "xsa_sha256": xsa_sha,
        "bd_wrapper_path": bd_wrapper_rel, "bd_wrapper_sha256": wrapper_sha,
        "address_map": address_map, "clock_tree": clock_tree,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "locked",
    }

    manifest_json = json.dumps(published_dict, sort_keys=True, ensure_ascii=False)

    try:
        _publish_manifest(manifest_json, manifest_path, resolve_root=pp)
    except ValueError as e:
        raise ManifestError(str(e))

    manifest_sha = _sha256_file(manifest_path)

    # ── 19. Return compact result + context updates ──
    return {
        "status": "success",
        "data": {
            "xsa_path": xsa_path,
            "xsa_sha256": xsa_sha,
            "wrapper_path": wrapper_path,
            "wrapper_sha256": wrapper_sha,
            "wrapper_rel": bd_wrapper_rel,
            "manifest_path": manifest_path,
            "manifest_sha256": manifest_sha,
            "platform_revision": platform_revision,
            "address_map": address_map,
            "bd_name": bd_name,
        },
        "_context_updates": {"platform_revision": platform_revision},
    }
