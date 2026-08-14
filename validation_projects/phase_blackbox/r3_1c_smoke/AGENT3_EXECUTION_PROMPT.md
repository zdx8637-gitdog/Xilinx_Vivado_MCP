# Agent3 Execution Prompt: B04 R3.1-C Phase Black-Box Smoke

## Target

You are **Agent3**, an external Claude Code agent using a fresh memory. Execute the prepared R3.1-C public MCP smoke and return an acceptance report. Agent1, Agent2, and Agent3 are external agents operated by the user; they are not Codex sub-agents.

## Read first

From this project directory, read these files in order:

1. `CLAUDE.md`
2. `README.md`
3. `AGENT3_EXECUTION_PROMPT.md` (this file)

Do not search `docs/manager` for additional instructions. This project-local package is the execution handoff.

## Fixed Runtime And Manifest

Repository: `D:\fpgaproject`  
Project: `D:\fpgaproject\validation_projects\phase_blackbox\r3_1c_smoke`  
Manifest:

```text
D:\tmp\r3_1c_agent3_execution\prov_8465c6b84fb3\scenario_manifest.json
```

The manifest points to five independent preconditioned runtimes and effective expected contracts. Do not edit or repair the manifest, receipts, runtimes, fixtures, or effective expected files.

## MCP Usage

- Do not configure `zynq_mcp` in Claude Code MCP settings.
- Do not restart Claude Code.
- Do not run `provision.py`, `verify_readiness.py`, or `cleanup.py`.
- Run the runner from the repository root; it starts `python -m mcps.zynq_mcp.server` through MCP SDK `stdio_client` and `ClientSession`.
- Do not import internal `mcps.zynq_mcp` modules from test code.

Run:

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\r3_1c_smoke\runner.py `
  --manifest D:\tmp\r3_1c_agent3_execution\prov_8465c6b84fb3\scenario_manifest.json `
  --scenario all `
  --run-id agent3_r3_1c_<unique_id>
```

Use a new unique run id. Preserve the generated evidence directory and do not overwrite prior runs.

## Functional Scenarios

The runner must execute all five scenarios:

1. `capabilities`: public `list_tools` and `get_capabilities`; verify the exposed count, exactly one PL tool (`pl_generate_system_top`), and its public input schema.
2. `success`: call `pl_generate_system_top`, wait for `SUCCEEDED`, verify artifact path/SHA and golden `system_top.v`, and verify `PL_GENERATE -> PL_BUILD`.
3. `missing_revision`: verify terminal `FAILED` with exact `PLATFORM_MANIFEST_NOT_FOUND`, cleanup to IDLE, and no invalid stage advance.
4. `wrong_stage`: verify admission rejection with `STAGE_PREREQUISITE_UNMET`, no operation id, and no public state mutation.
5. `invalid_schema`: submit integer, null, and object `wrapper_path` values; verify schema rejection and unchanged public state.

The effective expected JSON files are the assertion contract. Every expected assertion must be consumed and recorded with `assertion_id`, expected value, actual value, and `PASS`/`FAIL`.

## Scope Boundaries

- This is `PRECONDITIONED_SESSION`; report initial state as `PRECONDITION_OBSERVED`.
- Do not claim the public `create_session` lifecycle was tested.
- No hardware is required. Vivado Worker, hw_server, JTAG, UART, board programming, and LED verification are `NOT_APPLICABLE`.
- Do not modify production code, frozen tests/assets, root `.mcp.json`, Manager harness files, or fixtures.
- Do not call Agent1 or Agent2, start R3.2, or claim R3.1-C frozen.

## Report Requirements

Return the exact command, manifest, run id, exit code, evidence path, per-scenario counts, assertion totals with `expected == consumed`, initial/final public state, success artifact SHA, exceptions, and residual process status.

Use status `EXECUTED - READY FOR MANAGER REVIEW` only when all five scenarios pass. Otherwise use `EXECUTED - FAILED` and preserve all evidence.

