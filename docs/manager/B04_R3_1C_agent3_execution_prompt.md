# Prompt for Agent3 — B04 R3.1-C Phase Black-Box Smoke

## Target and memory

**Target: Agent3, external Claude Code, fresh memory.** The user manually forwards this prompt. Agent1, Agent2, and Agent3 are external Claude Code agents; they are not Codex sub-agents.

## Objective

Execute the prepared R3.1-C phase black-box smoke against the public MCP boundary and return the acceptance report. This is the first functional phase black-box execution. Focus on the PL MCP public behavior and the serial workflow state transitions. Do not extend the test infrastructure or perform a security audit.

## Fixed project and manifest

Repository: `D:\fpgaproject`  
Test project: `D:\fpgaproject\validation_projects\phase_blackbox\r3_1c_smoke`  
Manager manifest:

```text
D:\tmp\r3_1c_agent3_execution\prov_8465c6b84fb3\scenario_manifest.json
```

This manifest contains five independent preconditioned runtimes and effective expected contracts. Do not edit it or repair any runtime.

## MCP execution model

- Do **not** configure `zynq_mcp` in Claude Code MCP settings.
- Do **not** restart Claude Code.
- `runner.py` starts its own `python -m mcps.zynq_mcp.server` subprocess using MCP SDK `stdio_client` and `ClientSession`.
- Run from the repository root so `mcps` is importable.
- Do not import internal `mcps.zynq_mcp` modules from the test project.

Run exactly:

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\r3_1c_smoke\runner.py `
  --manifest D:\tmp\r3_1c_agent3_execution\prov_8465c6b84fb3\scenario_manifest.json `
  --scenario all `
  --run-id agent3_r3_1c_<unique_id>
```

Use a unique run id and preserve the generated evidence directory. Do not run `provision.py`, `verify_readiness.py`, or `cleanup.py` as Agent3. Those are Manager-only operations.

## Scenarios to execute

The runner must execute all five scenarios:

1. `capabilities`: use public `list_tools` and `get_capabilities`; verify the exposed tool count, exactly one PL tool (`pl_generate_system_top`), and its public schema.
2. `success`: call public `pl_generate_system_top`, wait for terminal `SUCCEEDED`, verify the returned artifact path/SHA and golden `system_top.v`, and verify the serial stage transition `PL_GENERATE -> PL_BUILD`.
3. `missing_revision`: call the public tool with the prepared missing revision input; verify terminal `FAILED` with exact `PLATFORM_MANIFEST_NOT_FOUND`, cleanup to IDLE, and no invalid stage advance.
4. `wrong_stage`: call the public tool from the prepared `PLATFORM_DESIGN` state; verify admission rejection with `STAGE_PREREQUISITE_UNMET`, no operation id, no ledger/state mutation.
5. `invalid_schema`: submit integer, null, and object values for `wrapper_path`; verify MCP schema errors and unchanged public state.

The effective expected JSON files are the contract. Every assertion must be consumed and recorded with `assertion_id`, expected value, actual value, and `PASS`/`FAIL`. Any mismatch or runner exception is a failed acceptance.

## Preconditions and boundaries

- This is `PRECONDITIONED_SESSION`; report the initial public state as `PRECONDITION_OBSERVED`.
- Do not claim that `create_session` lifecycle was tested.
- No hardware is required: no Vivado Worker, hw_server, JTAG, UART, board programming, or LED verification.
- A software PASS is not hardware acceptance.
- Do not modify production code, frozen tests/assets, root `.mcp.json`, Manager harness files, or project fixtures.

## Evidence and report

Return:

- exact command, manifest path, run id, start/end time, and exit code;
- per-scenario requested/executed/skipped/missing/failed/passed counts;
- assertion totals and `expected == consumed` for every scenario;
- evidence path and a short result for `capabilities`, `success`, `missing_revision`, `wrong_stage`, and `invalid_schema`;
- initial and final public state observations, including stage/lane/worker/active operation;
- artifact path and SHA evidence for the success scenario;
- any exception or residual process, with no claim beyond the evidence.

Required status: `EXECUTED — READY FOR MANAGER REVIEW` if all five scenarios pass; otherwise `EXECUTED — FAILED` with the failing scenario and preserved evidence. Do not call Agent2 and do not start R3.2.

