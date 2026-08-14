"""
capabilities.py — Unified capability declaration and tool schema list.
"""
from __future__ import annotations

from mcp.types import Tool

MCP_NAME = "zynq"
MCP_VERSION = "0.4.0"
PLANNED_DOMAIN_APIS = 43
DOMAIN_APIS_IMPLEMENTED = 91  # R3.1-C(1) + B05(1) + B06 first batch(22 PS) + B06 second batch(11 BSP) + B06 third batch(9 download/debug) + B07 PL bridge(26) + B01 UART capture(3) + B01 Phase 4 verify_consistency(1) + B01 UART diagnostics(1) + B01 Phase 6 observation(1) + B05-R2 platform atoms(14) + ps_ensure_arm_accessible(1)

CONTROL_TOOLS = [
    Tool(name="create_session", description="Create a new Zynq development session",
         inputSchema={"type": "object", "properties": {"board_id": {"type": "string", "minLength": 1}, "project_path": {"type": "string", "minLength": 1}}, "required": ["board_id", "project_path"]}),
    Tool(name="close_session", description="Close a Zynq session (refused if active operation present)",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string", "minLength": 1}}, "required": ["session_id"]}),
    Tool(name="get_session_info", description="Get metadata for an active Zynq session",
         inputSchema={"type": "object", "properties": {"session_id": {"type": "string", "minLength": 1}}, "required": ["session_id"]}),
    Tool(name="get_capabilities", description="Get Zynq MCP capability declaration",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="get_operation_status", description="Get status of an operation by ID",
         inputSchema={"type": "object", "properties": {"operation_id": {"type": "string", "minLength": 1}}, "required": ["operation_id"]}),
    Tool(name="wait_operation", description="Wait for an operation to complete (bounded, max 900s)",
         inputSchema={"type": "object", "properties": {"operation_id": {"type": "string", "minLength": 1}, "timeout_s": {"type": "number", "minimum": 5, "maximum": 900}}, "required": ["operation_id"]}),
    Tool(name="get_execution_state", description="Get full execution state: lane, stage, worker health, operation progress",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="diagnose_execution", description="Return structured diagnosis of current execution state",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="recover_execution", description="Attempt to recover from RECOVERY_REQUIRED to IDLE",
         inputSchema={"type": "object", "properties": {}}),
]

DOMAIN_TOOLS: list[Tool] = [
    Tool(name="platform_generate", description="Generate Platform BD (PS7 + AXI GPIO) and export XSA + Manifest. Inputs are derived from active session context.",
         inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
    Tool(name="pl_generate_system_top", description="Generate system_top.v instantiating BD wrapper from Platform Manifest",
         inputSchema={"type": "object",
             "properties": {"wrapper_path": {"type": "string", "minLength": 1}},
             "required": ["wrapper_path"],
             "additionalProperties": False}),
    # B06 PS Domain first batch (22 tools). Schemas derive from the domain
    # function signatures (domains/ps/). session_id is passed by the caller
    # and stripped by the dispatcher before reaching the domain function.
    Tool(name="ps_connect_hw_server", description="Connect to the JTAG hw_server (idempotent)",
         inputSchema={"type": "object", "properties": {"url": {"type": "string"}}}),
    Tool(name="ps_disconnect_hw_server", description="Disconnect from the JTAG hw_server (idempotent)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_list_targets", description="List all targets on the JTAG chain",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_select_target", description="Select a target on the JTAG chain by id (from ps_list_targets)",
         inputSchema={"type": "object", "properties": {"target_id": {"type": "integer"}}, "required": ["target_id"]}),
    Tool(name="ps_get_target_status", description="Query the current selected target's state (running|halted|reset|unknown)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_get_device_info", description="Query ARM DAP device info (idcode, irmask, ...)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_reset_target", description="Reset the target (scope: processor|system)",
         inputSchema={"type": "object", "properties": {"scope": {"type": "string"}}}),
    Tool(name="ps_initialize_ps", description="Run the PS7 init sequence: source ps7_init.tcl → ps7_init → ps7_post_config. Initializes clocks/PLLs/MIO/DDR so ARM cores can be accessed via JTAG.",
         inputSchema={"type": "object", "properties": {"tcl_path": {"type": "string"}}}),
    Tool(name="ps_load_hardware", description="Register PL hardware design (AXI memory map) with the PS via XSDB `loadhw <xsa>`. Must be called after ps_initialize_ps and before ps_download_elf, otherwise PL peripherals (AXI GPIO etc.) are invisible to ARM code.",
         inputSchema={"type": "object", "properties": {"xsa_path": {"type": "string", "minLength": 1}}, "required": ["xsa_path"]}),
    Tool(name="ps_ensure_arm_accessible", description="Ensure ARM cores are visible on the JTAG chain. After a board power-cycle the ARM DAP can be in a 'power-up not acknowledged' state (DAP status 0x30000021) with only DAP + xc7z020 enumerated and no ARM Cortex-A9 cores. Selects the ARM DAP and runs `rst -system` to bring the cores back; recovery_needed=false when the cores already enumerate.",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_run_target", description="Start processor execution (con); confirms running state",
         inputSchema={"type": "object", "properties": {"core": {"type": "integer"}}}),
    Tool(name="ps_halt_target", description="Halt the processor (stop), idempotent",
         inputSchema={"type": "object", "properties": {"core": {"type": "integer"}}}),
    Tool(name="ps_step_target", description="Single-step execution (stp); target must be halted",
         inputSchema={"type": "object", "properties": {"core": {"type": "integer"}}}),
    Tool(name="ps_wait_for_state", description="Wait until the target reaches a state (halted|running)",
         inputSchema={"type": "object", "properties": {"state": {"type": "string"}, "timeout_s": {"type": "number"}}, "required": ["state"]}),
    Tool(name="ps_reg_read", description="Read a CPU register (r0-r15/sp/lr/pc/cpsr)",
         inputSchema={"type": "object", "properties": {"register": {"type": "string"}}, "required": ["register"]}),
    Tool(name="ps_reg_write", description="Write a CPU register (value: int or hex string)",
         inputSchema={"type": "object", "properties": {"register": {"type": "string"}, "value": {"type": ["integer", "string"]}}, "required": ["register", "value"]}),
    Tool(name="ps_mem_read", description="Read memory (address: int or 0x... string; length in words, default 4)",
         inputSchema={"type": "object", "properties": {"address": {"type": ["integer", "string"]}, "length": {"type": "integer"}}, "required": ["address"]}),
    Tool(name="ps_mem_write", description="Write memory (data: single word, list of words, or bytes as list)",
         inputSchema={"type": "object", "properties": {"address": {"type": ["integer", "string"]}, "data": {"type": ["integer", "array"], "items": {"type": "integer"}}}, "required": ["address", "data"]}),
    Tool(name="ps_recover_target", description="Automatically recover the target connection (cascade: halt->reset->ps7_init)",
         inputSchema={"type": "object", "properties": {"strategy": {"type": "string"}}}),
    Tool(name="ps_reconnect_target", description="Reconnect to the already-open JTAG target (disconnect->connect->select ARM DAP)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_clear_debug_session", description="Clear residual debugger state (best-effort cleanup)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_diagnose_dap", description="Diagnose the DAP state and report likely causes",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_read_uart", description="Read data from a serial (UART) port for a duration",
         inputSchema={"type": "object", "properties": {"port": {"type": "string"}, "baudrate": {"type": "integer"}, "duration_ms": {"type": "integer"}}, "required": ["port"]}),
    Tool(name="ps_list_serial_ports", description="Enumerate all available serial ports",
         inputSchema={"type": "object", "properties": {}}),
    # B06 second batch (11 BSP/Build tools, XSCT). Schemas derive from the
    # domain function signatures (domains/ps/ps_bsp.py). BSP tools that are
    # workspace-bound take project_path as a real argument (the xsct
    # workspace); session_id is still passed by the caller and stripped by
    # the dispatcher.
    Tool(name="ps_import_hardware", description="Import a hardware definition (.xsa) into the XSCT workspace",
         inputSchema={"type": "object", "properties": {"xsa_path": {"type": "string", "minLength": 1}, "project_path": {"type": "string", "minLength": 1}}, "required": ["xsa_path", "project_path"]}),
    Tool(name="ps_create_platform", description="Create a software platform in the XSCT workspace",
         inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "project_path": {"type": "string", "minLength": 1}}, "required": ["name", "project_path"]}),
    Tool(name="ps_create_bsp", description="Create a BSP for a platform in the XSCT workspace",
         inputSchema={"type": "object", "properties": {"platform_name": {"type": "string", "minLength": 1}, "project_path": {"type": "string", "minLength": 1}}, "required": ["platform_name", "project_path"]}),
    Tool(name="ps_update_hardware", description="Update the active platform's hardware specification",
         inputSchema={"type": "object", "properties": {"xsa_path": {"type": "string", "minLength": 1}}, "required": ["xsa_path"]}),
    Tool(name="ps_get_bsp_status", description="Query the BSPs present in the XSCT workspace",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_create_app", description="Create an application in the XSCT workspace",
         inputSchema={"type": "object", "properties": {"name": {"type": "string", "minLength": 1}, "project_path": {"type": "string", "minLength": 1}}, "required": ["name", "project_path"]}),
    Tool(name="ps_add_sources", description="Add source files to a named app in the XSCT workspace (copied into {workspace}/{app_name}/src/)",
         inputSchema={"type": "object", "properties": {"app_name": {"type": "string", "minLength": 1}, "files": {"type": "array", "items": {"type": "string", "minLength": 1}}}, "required": ["app_name", "files"]}),
    Tool(name="ps_set_compiler_options", description="Set compiler/linker options on the app in the XSCT workspace",
         inputSchema={"type": "object", "properties": {"opts": {"type": "object", "additionalProperties": {"type": "string"}}}, "required": ["opts"]}),
    Tool(name="ps_compile", description="Build the app (XSCT app build)",
         inputSchema={"type": "object", "properties": {"app_name": {"type": "string", "minLength": 1}}, "required": ["app_name"]}),
    Tool(name="ps_get_build_status", description="Query the apps present in the XSCT workspace",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="ps_read_elf_info", description="Read ELF header metadata (readelf -h equivalent)",
         inputSchema={"type": "object", "properties": {"elf_path": {"type": "string", "minLength": 1}}, "required": ["elf_path"]}),
    # B06 third batch — download + debug (registered post B05 freeze)
    Tool(name="ps_download_elf", description="JTAG download ELF to DDR (xsdb dow)",
         inputSchema={"type": "object", "properties": {"elf_path": {"type": "string", "minLength": 1}}, "required": ["elf_path"]}),
    Tool(name="ps_write_uart", description="Write data to PS UART serial port",
         inputSchema={"type": "object", "properties": {"port": {"type": "string", "minLength": 1}, "baudrate": {"type": "integer", "minimum": 1}, "data": {"type": "string", "minLength": 1}}, "required": ["port", "data"]}),
    # B01 §5 Phase 5 — UART capture lifecycle (start → wait → stop). The
    # capture window opens before ps.download/ps.run so no output is lost;
    # wait_uart_capture matches expected markers instead of guessing a read
    # duration. Schemas mirror domains/ps/uart_capture.py signatures.
    Tool(name="ps_start_uart_capture", description="Open a UART capture window before CPU execution; returns capture_id (B01 Phase 5)",
         inputSchema={"type": "object", "properties": {"port": {"type": "string", "minLength": 1}, "baudrate": {"type": "integer", "minimum": 1}}, "required": ["port"]}),
    Tool(name="ps_wait_uart_capture", description="Wait until all markers appear in captured output or timeout; returns matched|partial|timeout (B01 Phase 5/6)",
         inputSchema={"type": "object", "properties": {"capture_id": {"type": "string", "minLength": 1}, "markers": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}, "timeout_s": {"type": "number", "minimum": 0.1}}, "required": ["capture_id", "markers"]}),
    Tool(name="ps_stop_uart_capture", description="Close a UART capture and return the full accumulated text (B01 Phase 5)",
         inputSchema={"type": "object", "properties": {"capture_id": {"type": "string", "minLength": 1}}, "required": ["capture_id"]}),
    # B01 §5 Phase 7 — UART diagnostics. Reads SLCR UART_CLK_CTRL + UART1
    # BAUDGEN/BAUDDIV, computes the actual baud rate and compares it to the
    # expected value. Requires the target to be halted (the caller halts).
    # Schema mirrors domains/ps/uart_diagnostics.py signature.
    Tool(name="ps_diagnose_uart_clock", description="Diagnose UART baud-rate mismatch: read SLCR UART_CLK_CTRL + UART1 BAUDGEN/BAUDDIV, compute the actual baud rate and compare to expected_baud (B01 Phase 7 diagnosis cascade; target must be halted)",
         inputSchema={"type": "object", "properties": {"expected_baud": {"type": "integer", "minimum": 1}}}),
    Tool(name="ps_debug_start", description="Start a JTAG debug session: halt, download ELF, return debug_session_id",
         inputSchema={"type": "object", "properties": {"elf_path": {"type": "string", "minLength": 1}, "target_id": {"type": "integer"}}, "required": ["elf_path"]}),
    Tool(name="ps_debug_close", description="Close a debug session: clear breakpoints, halt if running",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}}, "required": ["debug_session_id"]}),
    Tool(name="ps_breakpoint_add", description="Set a breakpoint at address or symbol",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}, "location": {"type": "string", "minLength": 1}}, "required": ["debug_session_id", "location"]}),
    Tool(name="ps_breakpoint_remove", description="Remove a breakpoint by ID",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}, "bp_id": {"type": "integer"}}, "required": ["debug_session_id", "bp_id"]}),
    Tool(name="ps_read_register", description="Read CPU register (r0-r15, sp, lr, pc, cpsr)",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}, "register": {"type": "string", "minLength": 1}}, "required": ["debug_session_id", "register"]}),
    Tool(name="ps_write_register", description="Write CPU register value",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}, "register": {"type": "string", "minLength": 1}, "value": {"type": ["integer", "string"]}}, "required": ["debug_session_id", "register", "value"]}),
    Tool(name="ps_stack_trace", description="Get ARM call stack (xsdb backtrace)",
         inputSchema={"type": "object", "properties": {"debug_session_id": {"type": "string", "minLength": 1}}, "required": ["debug_session_id"]}),
    # B07 PL Domain bridge tools (25, each wraps the old Vivado MCP tool of
    # the same signature through VivadoAdapter.call_tool(); schemas mirror the
    # old server's TOOL_SCHEMAS so no old-MCP capability is lost) + B07-fix
    # pl_program_fpga (26th PL tool) which instead runs on the XSDB shell —
    # see domain_runner._PL_XSDB_TOOLS. Session context keys
    # (session_id/board_id/project_path) are stripped by the dispatcher before
    # the bridge function runs.
    Tool(name="pl_create_project", description="Create a Vivado project (bridges old Vivado MCP create_project; project_dir is usually {session.project_path}/vivado/{name})",
         inputSchema={"type": "object", "properties": {
             "name": {"type": "string"}, "part": {"type": "string"},
             "sources": {"type": "array", "items": {"type": "string"}},
             "constraints": {"type": "array", "items": {"type": "string"}},
             "project_dir": {"type": "string"}, "top": {"type": "string"},
             "force": {"type": "boolean"}},
             "required": ["name", "part", "sources", "constraints", "project_dir"]}),
    # B07 addendum: pl_generate_target generates the BD IP output products
    # (OOC netlists + constraints) that the BD wrapper references during
    # synthesis. Required between pl_create_project and pl_synthesize for a
    # BD-based project, otherwise synth_design fails with 'Synth 8-439 module
    # <bd> not found'. target_type mirrors Vivado generate_target; the default
    # 'synthesis' produces the minimum needed before synthesis ('all' also
    # generates simulation / instantiation templates). Bridges old run_tcl.
    Tool(name="pl_generate_target", description="Generate output products for Block Design sources (OOC IP netlists + constraints the wrapper needs at synthesis; bridges run_tcl: generate_target [get_files *.bd])",
         inputSchema={"type": "object", "properties": {
             "target_type": {"type": "string", "enum": [
                 "all", "synthesis", "implementation", "simulation",
                 "instantiation_template"], "default": "synthesis"}},
             "additionalProperties": False}),
    Tool(name="pl_open_checkpoint", description="Open a Vivado Design Checkpoint (.dcp) (bridges open_checkpoint)",
         inputSchema={"type": "object", "properties": {"dcp_path": {"type": "string"}}, "required": ["dcp_path"]}),
    Tool(name="pl_close_design", description="Close the open design and clear session state (bridges close_design)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_synthesize", description="Launch synthesis and poll real Vivado run STATUS into the Execution Ledger (top optional, flatten accepted but not forwarded)",
         inputSchema={"type": "object", "properties": {"top": {"type": "string"}, "flatten": {"type": "string"}}}),
    Tool(name="pl_place", description="Launch placement and poll real Vivado run STATUS into the Execution Ledger (directive accepted but not forwarded)",
         inputSchema={"type": "object", "properties": {"directive": {"type": "string"}}}),
    Tool(name="pl_route", description="Launch routing, poll real Vivado run STATUS, then open the completed run (directive accepted but not forwarded)",
         inputSchema={"type": "object", "properties": {"directive": {"type": "string"}}}),
    Tool(name="pl_generate_bitstream", description="Generate a bitstream file (bridges write_bitstream)",
         inputSchema={"type": "object", "properties": {"path": {"type": "string"}, "force": {"type": "boolean"}}, "required": ["path"]}),
    Tool(name="pl_analyze_timing", description="Timing summary WNS/TNS/WHS/THS (bridges report_timing_summary)",
         inputSchema={"type": "object", "properties": {"clock": {"type": "string"}, "max_paths": {"type": "number"}}}),
    Tool(name="pl_analyze_utilization", description="Resource utilization report (bridges report_utilization)",
         inputSchema={"type": "object", "properties": {"hierarchical": {"type": "boolean"}}}),
    Tool(name="pl_query_cells", description="List logic cells (bridges get_cells)",
         inputSchema={"type": "object", "properties": {
             "filter": {"type": "string"}, "hierarchical": {"type": "boolean"},
             "properties": {"type": "array", "items": {"type": "string"}}}}),
    Tool(name="pl_query_nets", description="List signal nets (bridges get_nets)",
         inputSchema={"type": "object", "properties": {
             "filter": {"type": "string"}, "hierarchical": {"type": "boolean"},
             "max_items": {"type": "number"}}}),
    Tool(name="pl_query_clocks", description="List clocks (bridges get_clocks)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_query_ports", description="List top-level IO ports (bridges get_ports)",
         inputSchema={"type": "object", "properties": {"direction": {"type": "string"}}}),
    Tool(name="pl_get_property", description="Get a single Vivado property (bridges get_property)",
         inputSchema={"type": "object", "properties": {"object": {"type": "string"}, "property": {"type": "string"}}, "required": ["object", "property"]}),
    Tool(name="pl_validate_design", description="Run post-condition checks (bridges validate_design)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_get_vivado_info", description="Vivado version/build/edition info (bridges get_vivado_info)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_connect_hw_server", description="Connect to hw_server for JTAG (bridges connect_hw_server)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_get_device_status", description="Device status on the JTAG chain (bridges get_device_status)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_program_device", description="Program a bitstream via JTAG (bridges program_device)",
         inputSchema={"type": "object", "properties": {"bitstream_path": {"type": "string"}}, "required": ["bitstream_path"]}),
    # B07 fix: Zynq-7020 standard program flow. The Vivado hw_manager path
    # (pl_program_device) cannot find the xc7z020 device on the ARM-first
    # JTAG chain; XSDB `fpga -f` programs the configuration logic directly.
    # Runs on the XSDB shell, NOT the Vivado adapter (see _PL_XSDB_TOOLS).
    Tool(name="pl_program_fpga", description="Program the FPGA via XSDB `fpga -f` (Zynq-7020 standard path; the Vivado hw_manager path cannot find the xc7z020 on the ARM-first JTAG chain). Runs on the XSDB shell, not the Vivado adapter.",
         inputSchema={"type": "object", "properties": {"bitstream_path": {"type": "string"}}, "required": ["bitstream_path"]}),
    Tool(name="pl_list_devices", description="List devices on the JTAG chain (reuses get_device_status)",
         inputSchema={"type": "object", "properties": {}}),
    Tool(name="pl_compile_sim", description="Compile RTL/testbench with xvlog (bridges compile_sim)",
         inputSchema={"type": "object", "properties": {
             "sources": {"type": "array", "items": {"type": "string"}},
             "sim_dir": {"type": "string"}}, "required": ["sources", "sim_dir"]}),
    Tool(name="pl_elaborate_sim", description="Elaborate with xelab (bridges elaborate_sim)",
         inputSchema={"type": "object", "properties": {"top": {"type": "string"}, "sim_dir": {"type": "string"}}, "required": ["top", "sim_dir"]}),
    Tool(name="pl_run_simulation", description="Run the elaborated simulation with xsim (bridges run_simulation)",
         inputSchema={"type": "object", "properties": {
             "top": {"type": "string"}, "sim_dir": {"type": "string"},
             "vcd_path": {"type": "string"}}, "required": ["top", "sim_dir"]}),
    Tool(name="pl_parse_sim_log", description="Parse a simulation log for PASS/FAIL (bridges parse_sim_log)",
         inputSchema={"type": "object", "properties": {"log_path": {"type": "string"}}, "required": ["log_path"]}),
    # B01 §5 Phase 4 — cross-domain manifest consistency verification (query).
    # Pure read of Platform/PL Build/PS Build manifests + board profile sha256;
    # no side effects, always idempotent. resolve_root resolves project-relative
    # artifact paths for the file-existence/SHA256 rule.
    Tool(name="verify_consistency", description="B01 Phase 4: verify cross-domain manifest consistency (revisions, board profile, address map, artifact SHA256)",
         inputSchema={"type": "object", "properties": {
             "platform_manifest_path": {"type": "string", "minLength": 1},
             "pl_build_manifest_path": {"type": "string", "minLength": 1},
             "ps_build_manifest_path": {"type": "string", "minLength": 1},
             "board_profile_sha256": {"type": "string", "minLength": 1},
             "resolve_root": {"type": "string", "minLength": 1}},
             "required": ["platform_manifest_path"],
             "additionalProperties": False}),
    # B01 §5 Phase 6 — Observation & Pass/Fail adjudication (query). Pure text
    # analysis over the UART capture output already produced by
    # ps_stop_uart_capture / ps_wait_uart_capture. No hardware, no side
    # effects, always idempotent. Empty uart_text is a valid TIMEOUT input so
    # the schema allows "" (no minLength) — markers default to the B01
    # GPIO_E2E_* tokens in domains/verification/observation.py.
    Tool(name="evaluate_observation", description="B01 Phase 6: machine-decidable PASS/FAIL/TIMEOUT/INCOMPLETE verdict from UART capture text (pure text analysis, no hardware)",
         inputSchema={"type": "object", "properties": {
             "uart_text": {"type": "string"},
             "pass_marker": {"type": "string", "minLength": 1},
             "fail_marker": {"type": "string", "minLength": 1}},
             "required": ["uart_text"],
             "additionalProperties": False}),
    # B05-R2 Platform Domain atomic APIs (14). Composable building blocks from
    # B01 §7 Phase 1 / Architecture §4.3.3. Each sends Tcl through the shared
    # _run_tcl channel (same as platform_generate) and never advances stage.
    # The shortcut platform_generate {} is UNCHANGED (registered above).
    # Command atoms (12): session context keys (board_id/project_path/
    # board_profile_sha256) are injected by the dispatcher; the VivadoAdapter
    # is injected by the CommandRunner (_pl_adapter marker). Query atoms (2):
    # read directly.
    Tool(name="platform_create_design", description="Create a Vivado project for a Block Design (atom API, no stage advance)",
         inputSchema={"type": "object", "properties": {
             "name": {"type": "string", "minLength": 1},
             "part": {"type": "string", "minLength": 1}},
             "required": ["name", "part"], "additionalProperties": False}),
    Tool(name="platform_get_status", description="Query the open Vivado project name and BD cell count (query atom)",
         inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
    Tool(name="platform_add_ps7", description="Instantiate and configure the Zynq PS7 from the board ps7 preset (atom API)",
         inputSchema={"type": "object", "properties": {
             "preset_name": {"type": "string"}}, "additionalProperties": False}),
    Tool(name="platform_configure_ps7", description="Update PS7 CONFIG.PCW_* properties; idempotent partial update (only listed fields)",
         inputSchema={"type": "object", "properties": {
             "config": {"type": "object", "properties": {
                 "m_axi_gp0": {"type": "boolean"},
                 "m_axi_gp1": {"type": "boolean"},
                 "s_axi_hp0": {"type": "boolean"},
                 "s_axi_hp1": {"type": "boolean"},
                 "s_axi_acp": {"type": "boolean"},
                 "irq_f2p": {"type": "boolean"},
                 "fclk0_mhz": {"type": "integer", "minimum": 0},
                 "fclk1_mhz": {"type": "integer", "minimum": 0},
                 "uart1": {"type": "object", "properties": {
                     "enable": {"type": "boolean"}, "io": {"type": "string"}},
                     "additionalProperties": False},
                 "ddr": {"type": "string"}},
                 "additionalProperties": True}},
             "required": ["config"], "additionalProperties": False}),
    Tool(name="platform_add_ip", description="Instantiate an IP from the catalog; idempotent (existing instance is compared, never duplicated)",
         inputSchema={"type": "object", "properties": {
             "vlnv": {"type": "string", "minLength": 1},
             "instance_name": {"type": "string", "minLength": 1},
             "properties": {"type": "object", "additionalProperties": True}},
             "required": ["vlnv", "instance_name"], "additionalProperties": False}),
    Tool(name="platform_list_ips", description="List BD cells in the open design, optional filter (query atom)",
         inputSchema={"type": "object", "properties": {
             "filter": {"type": "string"}}, "additionalProperties": False}),
    Tool(name="platform_connect_interface", description="Connect two AXI interfaces, e.g. source='processing_system7_0/M_AXI_GP0' destination='smartconnect_0/S00_AXI'",
         inputSchema={"type": "object", "properties": {
             "source": {"type": "string", "minLength": 1},
             "destination": {"type": "string", "minLength": 1}},
             "required": ["source", "destination"], "additionalProperties": False}),
    Tool(name="platform_connect_clock", description="Connect one clock source to a list of clock inputs, e.g. source='processing_system7_0/FCLK_CLK0' targets=['smartconnect_0/aclk']",
         inputSchema={"type": "object", "properties": {
             "source": {"type": "string", "minLength": 1},
             "targets": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}},
             "required": ["source", "targets"], "additionalProperties": False}),
    Tool(name="platform_connect_reset", description="Connect one reset source to a list of reset inputs, e.g. source='rst_ps7_50M/peripheral_aresetn' targets=['axi_gpio_led/s_axi_aresetn']; polarity is NOT auto-detected — caller picks the right pins (SmartConnect uses interconnect_aresetn)",
         inputSchema={"type": "object", "properties": {
             "source": {"type": "string", "minLength": 1},
             "targets": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1}},
             "required": ["source", "targets"], "additionalProperties": False}),
    Tool(name="platform_set_address", description="Set a slave segment base address (and optional size), segment format 'axi_gpio_led/S_AXI'",
         inputSchema={"type": "object", "properties": {
             "segment": {"type": "string", "minLength": 1},
             "base": {"type": ["integer", "string"], "minLength": 1},
             "size": {"type": "integer", "minimum": 1}},
             "required": ["segment", "base"], "additionalProperties": False}),
    Tool(name="platform_validate", description="Validate the open Block Design; errors / critical warnings fail the call",
         inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
    Tool(name="platform_generate_wrapper", description="Generate the BD wrapper HDL and copy it under {project_path}/hdl",
         inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
    Tool(name="platform_export_hardware", description="Export a hardware platform (.xsa); default path {project_path}/platform.xsa",
         inputSchema={"type": "object", "properties": {
             "path": {"type": "string"}}, "additionalProperties": False}),
    Tool(name="platform_export_manifest", description="Re-export the structured platform manifest JSON from the open BD (standalone, idempotent); requires a ready BD plus existing wrapper + XSA under {project_path}; default path {project_path}/manifests/platform/sha256_<rev>.json",
         inputSchema={"type": "object", "properties": {
             "path": {"type": "string"}}, "additionalProperties": False}),
]  # R3.1-C + B05 + B06 first batch (22 PS) + B06 second batch (11 BSP) + B06 third batch (9 download/debug) + B07 PL bridge (26) + B01 UART capture (3) + B01 Phase 4 verify_consistency (1) + B01 UART diagnostics (1) + B01 Phase 6 observation (1) + B05-R2 platform atoms (14) + ps_ensure_arm_accessible (1)

def _inject_ps_session_schema(tool: Tool) -> Tool:
    """Expose the transport session contract that dispatcher enforces.

    O7 R1 found that every ps_* runtime call requires ``session_id`` while the
    public schemas omitted it.  Keep domain-specific schemas as the source for
    their own arguments, then add the common transport field mechanically so
    new ps_* tools cannot repeat the mismatch.
    """
    if not tool.name.startswith("ps_"):
        return tool
    schema = dict(tool.inputSchema or {})
    properties = dict(schema.get("properties") or {})
    properties["session_id"] = {
        "type": "string",
        "minLength": 1,
        "description": (
            "Required active session id. When omitted, the dispatcher returns "
            "INVALID_ARGUMENT / SESSION_ID_REQUIRED."
        ),
    }
    schema["type"] = "object"
    schema["properties"] = properties
    # Deliberately do not add this transport field to JSON Schema `required`.
    # MCP SDK pre-validation would otherwise intercept the call before the
    # dispatcher can preserve the frozen structured SESSION_ID_REQUIRED error.
    return tool.model_copy(update={"inputSchema": schema}, deep=True)


DOMAIN_TOOLS = [_inject_ps_session_schema(tool) for tool in DOMAIN_TOOLS]
ALL_TOOLS: list[Tool] = CONTROL_TOOLS + DOMAIN_TOOLS


def build_capabilities(instance_role: str = "primary",
                       adapter_status: str = "absent") -> dict:
    return {
        "mcp_name": MCP_NAME,
        "version": MCP_VERSION,
        "status": "adapter_ready" if adapter_status == "ready" else "single_channel_ready",
        "instance_role": instance_role,
        "domains": {
            "platform":   {"implemented": 15, "planned": 14, "status": "adapter_ready" if adapter_status == "ready" else "bridge_ready"},  # B05 platform_generate(1) + B05-R2 atoms(14)
            "pl":         {"implemented": 27, "planned": 12, "status": "adapter_ready" if adapter_status == "ready" else "bridge_ready"},
            "ps":         {"implemented": 47, "planned": 19, "status": "bridge_ready"},
            "control":    {"implemented": len(CONTROL_TOOLS), "total": len(CONTROL_TOOLS)},
            "observation": {"implemented": 1, "total": 4},
            "recovery":   {"implemented": 2, "total": 2},
        },
        "adapter": {
            "status": adapter_status,
            "type": "vivado_mcp_stdio",
        },
        "total_tools": len(ALL_TOOLS),
        "control_apis": len(CONTROL_TOOLS),
        "domain_apis_implemented": DOMAIN_APIS_IMPLEMENTED,
        "domain_apis_planned": PLANNED_DOMAIN_APIS,
        "planned_domain_apis": PLANNED_DOMAIN_APIS,
    }
