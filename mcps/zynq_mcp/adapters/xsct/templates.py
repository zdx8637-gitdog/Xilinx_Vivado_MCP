"""Tcl command templates for XSDB/XSCT operations.

NOT exhaustive — only the templates needed by library-phase PS domain modules.
Each template is a function that returns a Tcl string. Pure string
constructors; they do not talk to any process.
"""


def connect(url: str = "localhost:3121") -> str:
    return f"connect -url tcp:{url}"


def targets() -> str:
    return "targets"


def target_select(target_id: int) -> str:
    # Positional syntax (``targets <id>``). The filter syntax
    # ``targets -set -filter {id == <id>}`` does not work on this
    # XSDB version because the property name differs per target type.
    return f"targets {target_id}"


def get_target_properties(target_id: int) -> str:
    # ``-filter`` syntax is unreliable on Vitis 2023.1 XSDB.
    # ``targets <id>`` selects by position; ``-target-properties``
    # reads the current (selected) target's properties.
    return f"targets {target_id}\ntargets -target-properties"


def device_info() -> str:
    # Vitis 2023.1 XSDB: ``device properties`` does not work.
    # ``targets -target-properties`` is the correct command.
    return "targets -target-properties"


def rst(scope: str = "processor") -> str:
    """scope: 'processor' or 'system'."""
    return f"rst -{scope}"


def load_hardware(xsa_path: str) -> str:
    """Register PL hardware design (AXI memory map) with the PS.

    ``loadhw <xsa>`` tells the ARM core about the PL peripherals in the
    address space so that Xil_Out32 / Xil_In32 to PL addresses work.
    Must be called AFTER ps7_init / ps7_post_config and BEFORE dow.
    """
    return f"loadhw {xsa_path}"


def ps7_init() -> str:
    return "ps7_init"


def ps7_post_config() -> str:
    return "ps7_post_config"


def source_tcl(tcl_path: str) -> str:
    """Source a Tcl file into the XSDB shell: ``source <path>``."""
    return f"source {tcl_path}"


def dow(elf_path: str) -> str:
    return f"dow {elf_path}"


def fpga_program(bitstream_path: str) -> str:
    """Program the FPGA configuration logic with XSDB `fpga -f`.

    The canonical Zynq-7020 program flow. Unlike the Vivado hw_manager
    device programming (which needs to resolve the FPGA device on the JTAG
    chain), `fpga -f` programs the configuration logic directly and works
    even when the chain exposes the ARM DAP before the FPGA target.
    """
    return f"fpga -f {bitstream_path}"


def con() -> str:
    return "con"


def stop() -> str:
    return "stop"


def stp() -> str:
    return "stp"


def mrd(address: str, length: int = 1) -> str:
    return f"mrd {address} {length}"


def mwr(address: str, value: str) -> str:
    return f"mwr {address} {value}"


def rrd(register: str) -> str:
    return f"rrd {register}"


def rwr(register: str, value: str) -> str:
    return f"rwr {register} {value}"


def bpadd(addr_or_symbol: str) -> str:
    return f"bpadd {addr_or_symbol}"


def bpremove(bp_id: int) -> str:
    return f"bpremove {bp_id}"


def bplist() -> str:
    return "bplist"


def backtrace() -> str:
    return "backtrace"


def disconnect() -> str:
    return "disconnect"


def after(delay_ms: int) -> str:
    return f"after {delay_ms}"


# Build-related (for integration phase).
def setws(workspace: str) -> str:
    return f"setws {workspace}"


def import_hw(xsa_path: str) -> str:
    return f"importhw {xsa_path}"


def platform_create(name: str, hw: str, cpu: str = "ps7_cortexa9_0",
                    os: str = "standalone") -> str:
    return f"platform create -name {name} -hw {hw} -proc {cpu} -os {os}"


def bsp_create(platform: str, name: str = "bsp") -> str:
    return f"bsp create -platform {platform} -name {name}"


def app_create(name: str, platform: str, template: str = "empty_application") -> str:
    return f"app create -name {name} -platform {platform} -template {template}"


def app_build(name: str) -> str:
    return f"app build -name {name}"


def app_config_define_symbol(app: str, symbol: str) -> str:
    """Add one compiler define symbol to an app's build configuration.

    Vitis 2023.1 XSCT has NO ``app build -defines`` option (verified on the
    real tool: `bad option '-defines': -name -all -help`). The supported
    path is ``app config -name <app> -add define-compiler-symbols <sym>``,
    which appends ``-D<sym>`` to the app's compiler options (Vitis 2023.1
    sdk.tcl command reference: `app config -name test
    define-compiler-symbols FSBL_DEBUG_INFO` → "Add -DFSBL_DEBUG_INFO to the
    compiler options, while building the test application"). One call per
    symbol; braces keep the symbol a single Tcl word.
    """
    return f"app config -name {app} -add define-compiler-symbols {{{symbol}}}"


# BSP/Build (B06 second batch — integration phase).
# NOTE: Vitis 2023.1 XSCT has no `importhw`/`updatehw`/`bsp create`/
# `*-get-systems`; hardware is imported by `platform create -hw <xsa>` and
# the platform's software is materialized by `platform generate`. The legacy
# templates above (import_hw, bsp_create, updatehw, platform_list, bsp_list,
# app_list) are kept for Agent A's frozen unit tests but are NOT used by the
# ps_bsp domain functions.
def platform_activate(name: str) -> str:
    return f"platform active {name}"


def platform_write() -> str:
    return "platform write"


def platform_generate() -> str:
    # XSCT Vitis command template: ``platform generate`` materializes the
    # BSP/FSBL software for an active Vitis platform (called by
    # ps_bsp.create_bsp). KEPT by design — it is the XSCT Vitis platform
    # generate template, only name-similar to the Vivado BD shortcut tool
    # ``platform_generate`` removed in B11 phase 2; the two are unrelated
    # (see docs/development/mcp/B11_platform_generate_erratum.md §残留清单 B15).
    return "platform generate"


def platform_config_updatehw(xsa_path: str) -> str:
    """Update the active platform's hardware specification."""
    return f"platform config -updatehw {xsa_path}"


def platform_list() -> str:
    return "platform list"


def app_list() -> str:
    return "app list"


def app_create_basic(name: str, platform: str) -> str:
    """`app create` for Vitis 2023.1.

    Uses ``-template "Empty Application"`` (proven in
    ``build_g11_vitis.tcl`` line 30).  ``-domain standalone_domain``
    is required so the app has BSP linkage.
    """
    return (f'app create -name {name} -platform {platform} '
            f'-domain standalone_domain -template "Empty Application"')


def app_config(app: str, *options: str) -> str:
    """Build `app config -name <app> <options...>` (compiler/linker opts)."""
    cmd = f"app config -name {app}"
    opts = " ".join(o for o in options if o)
    return f"{cmd} {opts}".rstrip() if opts else cmd


def app_config_add(app: str, files: list[str]) -> str:
    """Build `app config -name <app> -add <file>...` (source add).

    DEPRECATED: Vitis 2023.1 XSCT does not support `app config -add`.
    Use ``importsources -name <app> -path <path>`` instead.
    Kept for frozen-test compatibility (Agent A tests import this symbol).
    """
    args = " ".join(f"-add {f}" for f in files)
    return f"app config -name {app} {args}"


def importsources(app: str, path: str) -> str:
    """Build `importsources -name <app> -path <path>` (Vitis 2023.1 XSCT).

    This is the proven, working syntax per the legacy reference
    ``zynq_platforms/ax7020_base/build_g11_vitis.tcl`` line 31.
    """
    return f"importsources -name {app} -path {path}"
