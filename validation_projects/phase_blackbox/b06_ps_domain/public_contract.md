# B06 PS Domain — Public Contract

The public contract of the PS domain as exercised by the Agent3 black-box
runner. Everything here is observable through the public MCP boundary of the
`zynq_mcp` server — no internal module is imported by the runner.

## Tool surface (42 `ps_*` tools)

| Batch | Tools | Count |
|-------|-------|:--:|
| JTAG connect | `ps_connect_hw_server`, `ps_disconnect_hw_server`, `ps_list_targets`, `ps_select_target`, `ps_get_target_status`, `ps_get_device_info` | 6 |
| Target control | `ps_reset_target`, `ps_initialize_ps`, `ps_run_target`, `ps_halt_target`, `ps_step_target`, `ps_wait_for_state` | 6 |
| Mem/Reg | `ps_reg_read`, `ps_reg_write`, `ps_mem_read`, `ps_mem_write` | 4 |
| Recovery | `ps_recover_target`, `ps_reconnect_target`, `ps_clear_debug_session`, `ps_diagnose_dap` | 4 |
| UART | `ps_read_uart`, `ps_list_serial_ports` | 2 |
| BSP/Build (XSCT) | `ps_import_hardware`, `ps_create_platform`, `ps_create_bsp`, `ps_update_hardware`, `ps_get_bsp_status`, `ps_create_app`, `ps_add_sources`, `ps_set_compiler_options`, `ps_compile`, `ps_get_build_status`, `ps_read_elf_info` | 11 |
| Download/Debug/Write | `ps_download_elf`, `ps_write_uart`, `ps_debug_start`, `ps_debug_close`, `ps_breakpoint_add`, `ps_breakpoint_remove`, `ps_read_register`, `ps_write_register`, `ps_stack_trace` | 9 |
| **Total** | | **42** |

`capabilities.domains.ps = {"implemented": 42, "planned": 19}`.

**Boundary**: `ps_download_elf` is registered and the `jtag_deploy`
download/run/UART sub-flow is **live**. `discovery` asserts
`ps_download_elf_present == true`. The debug-session tools
(`ps_debug_*`, `ps_read/write_register`, `ps_stack_trace`, `ps_write_uart`)
are registered but are NOT exercised by this acceptance (they are the
debug-session slice, outside this project's scenarios).

## Calling convention

- Every `ps_*` call carries `session_id` in the arguments. The dispatcher
  extracts it and strips it before the domain function runs; it is NOT part
  of the tool input schema (so the MCP input-schema validator never sees it).
- **BSP/Build tools additionally take `project_path` as a real argument**
  (the XSCT workspace). It must equal the session's `project_path`, which is
  the workspace the XSCT shell was started with.
- PS tools are **asynchronous**: the call returns
  `{"status":"success","data":{"operation_id":"op-...","status":"accepted"}}`
  and the domain function runs in the background. Poll with
  `wait_operation {operation_id, timeout_s}` (max 300s) or
  `get_operation_status`.

## Response envelope

```json
{"status":"success","data":{...}}
{"status":"error","error":{"code":"<ErrorCode>","message":"...","details":{"reason_code":"<REASON>",...}}}
```

For a waited operation the op record (from `wait_operation`) has the same
shape plus `status: "SUCCEEDED"|"FAILED"|...`, `result.data` (on success) and
`reason_code` (on failure).

## Error model (fail-closed)

Top-level `error.code` is a stable canonical ErrorCode; the domain-specific
cause lives in `error.details.reason_code`. Codes exercised by this project:

| Condition | `code` | `details.reason_code` |
|-----------|--------|------------------------|
| No active session | `LOCK_BUSY` | `NO_ACTIVE_SESSION` |
| `session_id` empty / non-string | `INVALID_ARGUMENT` | `SESSION_ID_REQUIRED` |
| `session_id` != active session | `LOCK_BUSY` | `SESSION_ID_MISMATCH` |
| Stage-gated tool at wrong stage | `LOCK_BUSY` | `STAGE_PREREQUISITE_UNMET` |
| Missing required schema param | MCP `isError` (`Input validation error: ...`) | n/a |
| Nonexistent XSA | `INVALID_ARGUMENT` | `XSA_NOT_FOUND` |
| XSDB/XSCT shell unavailable | `TOOL_ERROR` | `BRIDGE_NOT_READY` |
| hw_server unreachable | `ENV_ERROR` | `HW_SERVER_UNREACHABLE` |
| JTAG preconditions | `JTAG_ERROR` | `NOT_CONNECTED` / `NO_TARGET_SELECTED` / etc. |

## Stage model

PS tools are **stage-agnostic**: they never advance the workflow stage
(`next_stage = None`) and pass the shared P7 stage gate unconditionally. The
`STAGE_PREREQUISITE_UNMET` code is exercised in `error_paths` through the
stage-gated `pl_generate_system_top` tool at `PLATFORM_DESIGN`, proving the
shared gate itself is live while the PS domain stays off the stage graph.

## Hardware prerequisites and SKIP semantics

`runner.py` probes the environment once and records it in
`evidence/<run_id>/environment.json`. A scenario **SKIPs** (with a recorded
gate + reason) when its prerequisites are absent; it **FAILs**, never
silently passes, when it runs and an assertion is unmet.

| Scenario | Gate | Requires |
|----------|------|----------|
| `discovery` | — | none |
| `error_paths` | lane IDLE | none |
| `bsp_build` | xsct | XSCT shell + `ax7020_base.xsa` + `ps_led_test/src/main.c` |
| `jtag_connect` | xsdb + hw_server | XSDB shell + reachable `tcp:localhost:3121` + board on JTAG |
| `jtag_deploy` | xsdb + hw_server | same as jtag_connect + PS7 target |

UART: `COM4` (CP2102, 115200) is the expected board UART; the deploy
sub-flow only asserts the `AX7020 ARM Test G11` marker when it actually runs.
