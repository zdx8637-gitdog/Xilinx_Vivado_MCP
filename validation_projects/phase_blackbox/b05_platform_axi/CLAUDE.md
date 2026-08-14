# Agent3 Instructions: B05 Platform/AXI Black-Box Acceptance

## Role and scope

- You are Agent3, using a fresh context for independent black-box acceptance.
- This project is the B05 Platform/AXI domain minimum vertical slice.
- This is NOT B09. It does NOT verify the complete GPIO workflow, PS software, or hardware LED output.
- Do NOT modify production code, frozen tests, root `.mcp.json`, or Manager harness files.

## How MCP is used

- Do NOT configure or load `zynq_mcp` as a Claude Code MCP server.
- Do NOT restart Claude Code for this test.
- `runner.py` starts its own `python -m mcps.zynq_mcp.server` subprocess through MCP SDK stdio transport and calls public tools through `ClientSession`.
- The server spawns a Vivado Worker subprocess on demand. The runner does NOT start Vivado.
- Run the command from the repository root so `mcps` is importable.

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\b05_platform_axi\runner.py --run-id agent3_b05_<timestamp>
```

## Session type

This is a **FRESH_SESSION** project. The runner creates a fresh session via public `create_session(board_id, project_path)`, which initializes at `PLATFORM_DESIGN`. No Manager provisioning or preconditioned runtimes.

## Scenarios

Run all three scenarios in order:

| # | Scenario | Stage In | What It Proves |
|---|----------|----------|----------------|
| 1 | `discovery` | PLATFORM_DESIGN | Public tool discovery: `platform_generate` present, correct schema, correct tool count |
| 2 | `success` | PLATFORM_DESIGN → PL_GENERATE | Full create-session + Vivado BD generation + XSA/wrapper/manifest + Platform→PL handoff via `pl_generate_system_top` |
| 3 | `stage_rejection` | PL_GENERATE or later | Wrong-stage admission rejected with `STAGE_PREREQUISITE_UNMET`, public state unchanged |

## Expected outputs

The checked-in `expected_outputs/*.json` files are the assertion contracts:
- `discovery.json` — 8 assertions
- `success.json` — 31 assertions
- `stage_rejection.json` — 3 assertions

The runner loads them, executes scenarios, collects facts, and compares. Every assertion must be consumed. `expected_assertion_count == consumed_assertions` for each scenario, or it fails.

## Evidence and report

- The runner preserves evidence under `evidence/<run_id>/`.
- Review `summary.json`, per-scenario result files, and generated artifact paths.
- Report exact command, per-scenario counts, exit code, artifact SHAs, initial/final public state.
- Do NOT report a software PASS as physical hardware acceptance.

## Public API only

The runner uses ONLY these public MCP tools:
- `list_tools`
- `get_capabilities`
- `create_session`
- `platform_generate`
- `pl_generate_system_top`
- `wait_operation`
- `get_operation_status`
- `get_execution_state`
- `get_session_info`

It does NOT import `mcps.zynq_mcp` internals, read/modify the ledger, call `run_tcl`, or use any non-public tool.

## Hardware boundary

Vivado is required for the success scenario (BD generation, XSA export). No hw_server, JTAG, UART, board programming, or physical hardware is involved. If Vivado is unavailable, the success scenario will fail with `ADAPTER_NOT_READY` — that is a VALID failure; do NOT skip or fake it.

`NOT_APPLICABLE`: hw_server, JTAG, UART, board programming, LED verification, any physical hardware.
