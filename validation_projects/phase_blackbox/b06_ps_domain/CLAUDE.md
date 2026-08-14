# Agent3 Instructions: B06 PS Domain Black-Box Acceptance

## Role and scope

- You are Agent3, using a fresh context for independent black-box acceptance.
- This project verifies the B06 PS Domain public MCP boundary: the 42 `ps_*`
  tools through a real `zynq_mcp` server, using ONLY the MCP SDK
  (`ClientSession`) and public tools.
- This is NOT the complete GPIO workflow. Hardware scenarios (JTAG/UART)
  SKIP when the prerequisites are absent; that is a valid result, not a
  failure. Only report a hardware PASS when the scenario actually ran.
- Do NOT modify production code, frozen tests, root `.mcp.json`, Manager
  harness files, or the runner/expected outputs.

## How MCP is used

- Do NOT configure or load `zynq_mcp` as a Claude Code MCP server.
- Do NOT restart Claude Code for this test.
- `runner.py` starts its own `python -m mcps.zynq_mcp.server` subprocess
  through MCP SDK stdio transport, with a unique `ZYNQ_RUNTIME_ROOT` temp dir.
- Run the command from the repository root so `mcps` is importable.

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\b06_ps_domain\runner.py --run-id agent3_b06_<timestamp>
```

Use a fresh, unique run id. Do NOT overwrite a previous run. If the runner
exits non-zero, preserve its evidence directory and report the failure.

## Session type

**FRESH_SESSION**. The runner creates its own `create_session(board_id,
project_path)` per scenario and closes it afterwards. No Manager provisioning,
no preconditioned runtimes, no `scenario_manifest.json`.

## Scenarios

| # | Scenario | Gate | What it proves |
|---|----------|------|----------------|
| 1 | `discovery` | — | ≥33 `ps_*` tools registered (currently 42); `ps.implemented ≥ 33`; key schemas (`ps_connect_hw_server`, `ps_list_targets`, `ps_import_hardware`, `ps_compile`); `ps_download_elf` registered |
| 2 | `bsp_build` | xsct | Full BSP/Build pipeline: import XSA → platform → BSP → app → add sources → compiler opts → compile → ELF produced and valid |
| 3 | `jtag_connect` | xsdb + hw_server | hw_server connect → targets → ARM Cortex-A9 DAP present → select → target status → device info → disconnect |
| 4 | `jtag_deploy` | xsdb + hw_server | connect → select ARM DAP → ps7_init → halt → reg_read pc → run → disconnect; download/run/UART sub-flow is live (`ps_download_elf` registered) and asserts the UART marker |
| 5 | `error_paths` | lane IDLE | Error envelope + reason codes: `NO_ACTIVE_SESSION`, `SESSION_ID_REQUIRED`, `SESSION_ID_MISMATCH`, schema rejection, `STAGE_PREREQUISITE_UNMET`, `XSA_NOT_FOUND`; channel stays clean |

## Expected outputs

The checked-in `expected_outputs/*.json` files are the assertion contracts:

| File | Assertions |
|------|-----------|
| `discovery.json` | 16 |
| `bsp_build.json` | 25 |
| `jtag_connect.json` | 15 |
| `jtag_deploy.json` | 18 |
| `error_paths.json` | 20 |
| **Total** | **94** |

The runner loads them, executes scenarios, collects facts, and compares.
Every assertion must be consumed; a mismatch is a failure.

## Evidence and report

- The runner writes `evidence/<run_id>/`: `summary.json`,
  `environment.json`, per-scenario `*_result.json`, and `<scenario>/` dirs.
- Review the evidence; report exact command, run id, exit code, per-scenario
  status (PASS/FAIL/SKIP + reason), assertion counts, and the environment
  probe summary.
- A SKIP must be reported as a SKIP with its gate + reason, never as a PASS.

## Public API only

The runner uses ONLY public MCP tools: `list_tools`, `get_capabilities`,
`create_session`, `close_session`, `get_execution_state`,
`pl_generate_system_top`, the `ps_*` tools, and `wait_operation`. The
debug-session tools (`ps_debug_*`, `ps_stack_trace`, `ps_write_uart`) are
registered but outside this acceptance's scenarios.
It does NOT import `mcps.zynq_mcp` internals, read/modify the ledger, call
`run_tcl`, or use any non-public tool.

## Hardware boundary

`NOT_APPLICABLE` until the hardware is actually present and the scenario runs:
hw_server, JTAG chain, board programming, UART, LED verification. If
`jtag_connect`/`jtag_deploy` SKIP, do not report them as hardware-accepted.
