"""
platform_domain.py — B05 Platform/AXI Domain minimum vertical slice. v3.0.0
Uses Vivado adapter via worker controller. Produces BD + wrapper + XSA + manifest.
Manifest validated with project-root-resolved paths. publish_manifest for atomic write.
"""
import hashlib, json, logging, re
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

class TclError(PlatformError):
    """B11 阶段③.1 (D6): the Vivado backend answered but the Tcl itself
    failed. This is a TOOL_ERROR (reason_code TCL_ERROR), NOT ADAPTER_NOT_READY
    — the backend is up; the command was rejected. Only genuine backend-not-
    ready responses keep the ADAPTER_NOT_READY classification."""
    def __init__(self, msg="Vivado Tcl command failed"):
        super().__init__(msg, "TCL_ERROR")

class SynthesisError(PlatformError):
    def __init__(self, msg="Synthesis failed"):
        super().__init__(msg, "SYNTHESIS_FAILED")

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
    "add_reset": "AXI_CONNECT",
    "add_smartconnect": "AXI_CONNECT",
    "connect_axi": "AXI_CONNECT",
    "connect_clocks": "AXI_CONNECT",
    "connect_resets": "AXI_CONNECT",
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
    Other failures: raises TclError (the backend answered but the Tcl failed —
    D6, reason_code TCL_ERROR) except for genuine backend-unready responses,
    which raise AdapterError (reason_code ADAPTER_NOT_READY).
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
        # B11 阶段③.1 (D6): a Tcl-level failure is a TOOL_ERROR, not an
        # ADAPTER_NOT_READY — the backend is up and answered (it rejected the
        # command). Only genuine backend-unready responses keep the
        # ADAPTER_NOT_READY classification.
        if rc in _BACKEND_NOT_READY_REASON_CODES:
            raise AdapterError(msg)
        raise TclError(f"{label}: {msg}")
    raise AdapterError(f"'{label}': Vivado cold start not resolved")


# B11 阶段③.1 (D6): reason codes that genuinely mean the Vivado backend is not
# ready (vs. a Tcl-level error from a healthy backend).
_BACKEND_NOT_READY_REASON_CODES = frozenset({
    "ADAPTER_NOT_READY", "BRIDGE_NOT_READY", "BACKEND_NOT_ACTIVE",
    "VIVADO_PROCESS_DEAD", "BACKEND_PROCESS_DEAD", "VIVADO_NOT_FOUND",
    "VIVADO_VERSION_MISMATCH",
})


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
#  generate_platform — REMOVED in B11 phase 2
# ═══════════════════════════════════════════
# The B05 shortcut ``generate_platform`` (hard-coded PS7 + AXI GPIO Block
# Design + fixed 0x41200000 address) was removed with its tool
# registration ``platform_generate`` (see
# docs/development/mcp/B11_platform_generate_erratum.md). Its proven Tcl
# sequence is mirrored by the B05-R2 platform atoms
# (domains/platform/platform_atoms.py), which the 6-LED workflow composes
# instead. The shared helpers retained above (board-package resolution,
# _run_tcl, _top_bd_command, structured exceptions) remain the single
# implementation home the atoms import from.
