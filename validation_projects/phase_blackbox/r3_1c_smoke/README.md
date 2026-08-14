# R3.1-C Phase Public Smoke — Agent3 Black-Box Acceptance

> **Status**: READY FOR MANAGER REVIEW / NOT EXECUTED
> **Phase**: B04 R3.1-C | **Type**: PRECONDITIONED_SESSION
> **Agent**: Agent3 (fresh context only)

## Agent3 Start Here

This directory is the complete Agent3 handoff. Read the local files in this order:

1. `CLAUDE.md` — project constraints and MCP boundary.
2. `README.md` — project layout, scenarios, evidence, and runner usage.
3. `AGENT3_EXECUTION_PROMPT.md` — the exact execution task and fixed manifest.

You do not need to read `docs/manager`. The Manager Reviewer has already provisioned the manifest and runtimes listed in the local execution prompt.

## Purpose

Verify the R3.1-C public MCP boundary: `pl_generate_system_top` through a real `zynq_mcp` server, using only MCP SDK ClientSession and public tools. This is a **preconditioned smoke test** — the `PL_GENERATE` stage is provided by Manager Reviewer via a provisioning harness.

This is NOT B09. It does NOT verify a complete GPIO workflow or user-facing `create_session → PL_GENERATE` path.

## Architecture

```
checked-in expected_outputs/*.json   ← TEMPLATES (may contain PLACEHOLDER_*)
         │
         ▼  Manager provision.py
run_root/effective_expected/*.json   ← EFFECTIVE CONTRACTS (no placeholders, SHA-pinned)
         │
         ▼  Manifest: effective_expected_dir + effective_expected_shas
         │
         ▼  Agent3 runner.py (NO expect injection, SHA-validated)
evidence/<run_id>/                    ← RUNNER OUTPUT
```

- `expected_outputs/` is **not** a runner input. It is a version-controlled template source.
- The Manager Reviewer runs `_manager/r3_1c_smoke/provision.py` to create **five** isolated runtimes and generate effective expected contracts.
- Agent3 receives the `scenario_manifest.json` and runs only `runner.py`. Agent3 does **not** run `provision.py` or `verify_readiness.py`.

## Scenarios

| # | Scenario | Stage | Expected Outcome |
|---|----------|-------|-----------------|
| 1 | capabilities | PL_GENERATE | list_tools=10, only pl_generate_system_top as PL tool, schema correct |
| 2 | success | PL_GENERATE + valid revision | SUCCEEDED, stage→PL_BUILD, system_top.v SHA matches |
| 3 | missing_revision | PL_GENERATE + "" revision | FAILED, PLATFORM_MANIFEST_NOT_FOUND, stage unchanged |
| 4 | wrong_stage | PLATFORM_DESIGN + valid revision | Admission rejected, STAGE_PREREQUISITE_UNMET, no operation |
| 5 | invalid_schema | PL_GENERATE + valid revision | MCP schema rejects int/None/dict wrapper_path, no operation |

## Runner Usage

```bash
# Recommended — runner derives everything from manifest:
python runner.py --manifest <scenario_manifest.json> --scenario all --run-id <run-id>

# Single scenario:
python runner.py --manifest <scenario_manifest.json> --scenario success --run-id <run-id>
```

`scenario_manifest.json` is created by the Manager-only provisioning harness. It declares:
- `effective_expected_dir` — directory containing effective expected JSON (no placeholders)
- `effective_expected_shas` — SHA256 of each effective expected file (validated before execution)
- `approved_evidence_root` — where runner writes evidence
- Per-scenario `runtime_root`, `receipt_path`, `effective_expected_sha256`

The runner reads those effective files unchanged and rejects any hash mismatch, missing scenario, extra scenario, or bad-format SHA.

## Evidence

Agent3 must save to `evidence/<run_id>/`:
- `responses/` — raw MCP ToolResult JSON per step
- `state_traces/` — get_execution_state snapshots
- `operation_logs/` — operation_id → terminal tracking
- `artifacts/` — generated system_top.v + SHA
- `summary.json` — overall pass/fail + per-scenario results

## Cleanup

```bash
# Dry-run (default):
python cleanup.py --manifest <scenario_manifest.json> --run-id <run-id>

# Execute:
python cleanup.py --manifest <scenario_manifest.json> --run-id <run-id> --execute
```

Cleanup requires `--manifest` and only deletes the single evidence directory named `run_id` under the manifest-declared `approved_evidence_root`.

## Constraints

- Agent3 MUST start its own `python -m mcps.zynq_mcp.server`.
- Agent3 MUST NOT import `mcps.zynq_mcp` or read `execution_ledger.json`.
- Runner uses only `mcp.client.stdio.stdio_client` + `mcp.ClientSession`.
- All Worker, Vivado, JTAG, and hardware interactions = NOT_APPLICABLE.
