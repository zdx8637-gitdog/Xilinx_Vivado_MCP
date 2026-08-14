# Agent3 Execution Prompt — B05 Platform/AXI Black-Box Acceptance

## Target

You are **Agent3**, an external Claude Code agent using a fresh memory. Execute the B05 Platform/AXI black-box acceptance and return an acceptance report. Agent1, Agent2, and Agent3 are external agents operated by the user; they are not Codex sub-agents.

## Read first

From this project directory, read these files in order:

1. `CLAUDE.md`
2. `README.md`
3. `public_contract.md`
4. `AGENT3_EXECUTION_PROMPT.md` (this file)

Do not search `docs/manager` for additional instructions. This project-local package is the execution handoff.

## Fixed Environment

Repository: `D:\fpgaproject`
Project: `D:\fpgaproject\validation_projects\phase_blackbox\b05_platform_axi`
Board: `ALINX_AX7020_v1.0`
Session type: **FRESH_SESSION** — no Manager provisioning required.

Runner creates its own `create_session` from `PLATFORM_DESIGN`. No preconditioned runtimes. No `scenario_manifest.json`.

## MCP Usage

- Do NOT configure `zynq_mcp` as a Claude Code MCP server.
- Do NOT restart Claude Code.
- The runner starts its own `python -m mcps.zynq_mcp.server` subprocess through MCP SDK `stdio_client` and `ClientSession`.
- Do NOT import internal `mcps.zynq_mcp` or `mcps.common` modules.
- Do NOT read or modify `execution_ledger.json`.

Run:

```powershell
Set-Location D:\fpgaproject
python validation_projects\phase_blackbox\b05_platform_axi\runner.py --run-id agent3_b05_<unique_id>
```

Replace `<unique_id>` with a timestamp like `20260808_220000`. Use a new unique run id. Do NOT overwrite a previous run.

If the runner exits non-zero, preserve its evidence directory and report the failure. Do NOT edit the runner, expected outputs, or any production code.

## Functional Scenarios

The runner must execute all three scenarios:

1. **`discovery`**: public `list_tools` and `get_capabilities`; verify `platform_generate` is present in tool list with empty input schema (`{}`, `additionalProperties: false`), total_tools count, and domain API implementation count.

2. **`success`**: call `create_session` → `platform_generate {}` → `wait_operation` for terminal `SUCCEEDED` → verify stage advance `PLATFORM_DESIGN → PL_GENERATE` → validate XSA/wrapper/manifest artifacts exist with matching disk SHAs → verify manifest contains `0x41200000` address map → call `pl_generate_system_top` with returned `wrapper_rel` to prove Platform-to-PL handoff.

3. **`stage_rejection`**: after success (stage is `PL_GENERATE` or `PL_BUILD`), call `platform_generate` again → verify admission is rejected with `STAGE_PREREQUISITE_UNMET` or `CHANNEL_BUSY` → verify stage unchanged.

The checked-in `expected_outputs/*.json` files are the assertion contract. Every assertion must be evaluated and recorded with `assertion_id`, expected value, actual value, and `PASS`/`FAIL`. A mismatch is a test failure. The runner enforces `expected_assertion_count == consumed_assertions`.

## Evidence

The runner writes to `evidence/<run_id>/`:
- `summary.json` — overall pass/fail + per-scenario results
- `discovery_result.json`, `success_result.json`, `stage_rejection_result.json` — per-scenario assertions
- `discovery/`, `success/`, `stage_rejection/` — per-scenario evidence subdirectories

Agent3 must review all evidence files and report:
- Exact command executed
- Run ID
- Exit code
- Per-scenario counts (expected/consumed assertions, PASS/FAIL)
- Total assertions: expected == consumed?
- Generated artifact paths and SHAs
- Initial and final public state (stage, lane)
- Any exceptions or residual process issues

## Scope Boundaries

- This is **FRESH_SESSION** — the runner handles the full public lifecycle from `create_session`.
- Do NOT claim the `PRECONDITIONED_SESSION` lifecycle was tested.
- Vivado Worker is started by the server on demand — the runner does NOT start Vivado itself. If Vivado is unavailable, the success scenario will fail with `ADAPTER_NOT_READY` and that is a valid failure, not a skip.
- No hardware is required. hw_server, JTAG, UART, board programming, and LED verification are `NOT_APPLICABLE`.
- Do NOT modify production code, frozen tests/assets, root `.mcp.json`, Manager harness files, or fixtures.
- Do NOT call Agent1 or Agent2, start B06, or claim B05 frozen.

## Report Requirements

Use status `EXECUTED - READY FOR MANAGER REVIEW` only when all three scenarios pass. Otherwise use `EXECUTED - FAILED` and preserve all evidence.

Report format:

```markdown
## B05 Agent3 Black-Box Acceptance Report

**Status**: EXECUTED - READY FOR MANAGER REVIEW | EXECUTED - FAILED
**Run ID**: <run_id>
**Command**: <exact command>
**Exit code**: <code>

### Scenario Results

| Scenario | Assertions | Pass | Fail | Consumed |
|----------|-----------|------|------|----------|
| discovery | 8 | 8 | 0 | 8 |
| success | 31 | ... | ... | ... |
| stage_rejection | 3 | ... | ... | ... |

### Evidence Path
evidence/<run_id>/

### Artifacts
- XSA: <path> SHA256: <sha>
- Wrapper: <path> SHA256: <sha>
- Manifest: <path> platform_revision: <rev>

### Public State
- Initial stage: PLATFORM_DESIGN
- Final stage: ...

### Exceptions
<list or "none">
```
