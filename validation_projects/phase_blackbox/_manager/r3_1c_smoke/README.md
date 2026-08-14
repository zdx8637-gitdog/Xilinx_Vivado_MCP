# Manager Provisioning Harness — R3.1-C Smoke

> **Run by**: Manager Reviewer only
> **NOT provided to**: Agent3

## Purpose

Creates isolated, preconditioned `PL_GENERATE` runtime environments for the **five** R3.1-C phase black-box smoke scenarios. Uses production `ledger_transaction` and fixture files — NO manual JSON edits.

Generates **effective expected contracts** from checked-in templates by substituting `PLACEHOLDER_SID`, `PLACEHOLDER_PROJ`, and `PLACEHOLDER_STAGE` with real per-scenario values.

## Usage

```bash
# Provision all five scenarios:
python provision.py --scenario all --base-dir <output_parent_dir>

# Provision a single scenario:
python provision.py --scenario success --base-dir <output_parent_dir>
```

Produces:
- One `_provisioned/` directory per scenario, each with an isolated `execution_ledger.json`, project structure, and `provision_receipt.json`.
- `effective_expected/` — per-scenario effective expected JSON with real values, no placeholders.
- `scenario_manifest.json` — declares `effective_expected_dir`, `effective_expected_shas`, `approved_evidence_root`, and per-scenario entries.

## Workflow

1. `provision.py` — create five runtimes + effective expected contracts + manifest.
2. `verify_readiness.py` — validate paths, ledger state, identity, artifact SHA, and effective expected SHA cross-checks.
3. Hand `scenario_manifest.json` to Agent3.
4. Agent3 runs `runner.py --manifest <manifest> --scenario all --run-id <run-id>`.
5. `cleanup.py --manifest <manifest> --run-id <run-id> --execute` (after review).

## Scenarios

| ID | Stage | platform_revision | Purpose |
|----|-------|-------------------|---------|
| `capabilities` | PL_GENERATE | valid sha256 | Public tool enumeration |
| `success` | PL_GENERATE | valid sha256 | Positive path |
| `missing_revision` | PL_GENERATE | "" | Negative: missing revision |
| `wrong_stage` | PLATFORM_DESIGN | valid sha256 | Negative: admission rejection |
| `invalid_schema` | PL_GENERATE | valid sha256 | Negative: MCP schema rejection |

## Cleanup

`cleanup.py` is dry-run by default. It requires `--manifest` and only operates on the manifest-declared `approved_evidence_root`. Deletion is restricted to a single `run_id` subdirectory that passes all validation (path containment, basename match, summary.json presence, summary.run_id match, no symlinks). Use `--execute` to perform actual deletion.
