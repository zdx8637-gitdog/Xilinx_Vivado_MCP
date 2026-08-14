# B04 R3.1-C Round 13 Manager Audit

Date: 2026-08-08  
Status: **BLOCKED — Agent1 remediation required; Agent3/Agent2 not called**

## Verified

Fresh provisioning under `D:\tmp\manager_r13_audit\prov_c5aa12e0295b\` passed. The Manager Reviewer reproduced:

- `provision.py --scenario all`: 5 runtimes.
- `verify_readiness.py`: 5/5 PASS on clean input.
- `runner.py --scenario all`: 5/5 PASS, 107/107 assertions.
- No hardware/Vivado Worker/JTAG/board operation.

The runner's new shared provenance path rejects the tested tampered receipts before creating the requested evidence directory. The checks include missing/empty artifact inventory, fixture source/hash/key, public revision, lane, and worker fields.

## Blocking P1: verifier and cleanup do not enforce the shared contract

The clean result is insufficient because only `runner.py` calls `_provenance.validate_all_identity_bindings()`.

On separate fresh provisioned runtimes, the Manager Reviewer changed one `success/provision_receipt.json` field at a time:

| Tamper | `verify_readiness.py` | `cleanup.py` |
|---|---:|---:|
| Remove `input_artifacts` | exit 0 / overall PASS | exit 0; cleanup allowed |
| Empty `input_artifacts` | exit 0 / overall PASS | exit 0; cleanup allowed |
| External `fixture_provenance.source` | exit 0 / overall PASS | exit 0; cleanup allowed |
| Missing fixture key | exit 0 / overall PASS | exit 0; cleanup allowed |
| Tampered fixture hash | exit 0 / overall PASS | exit 0; cleanup allowed |
| Tampered `platform_revision_public_expected` | exit 0 / overall PASS | exit 0; cleanup allowed |
| Tampered `expected_initial_lane` | exit 0 / overall PASS | exit 0; cleanup allowed |
| Tampered worker state/PID | exit 0 / overall PASS | exit 0; cleanup allowed |

For runner, each corresponding case exited non-zero before evidence creation. This proves the shared validator is effective but the other two consumers are incomplete. `verify_readiness.py` still uses a local subset loop (`receipt.get("input_artifacts", [])`), and cleanup's `_validate_cleanup_manifest()` checks only a subset of receipt fields.

## Required closure

Make `_provenance.validate_all_identity_bindings()` reusable in non-exiting/reporting form, or add an equivalent single shared fail-closed API. `verify_readiness.py` and `cleanup.py` must invoke it before reporting PASS or deleting any evidence. They must reject every case above, plus all artifact shape/count/path/SHA cases, fixture source/key/hash cases, ledger identity/precondition cases, effective-contract identity/SHA cases, and manifest/path provenance cases.

For readiness, preserve per-scenario structured failure details but ensure overall is FAIL and exit code non-zero. For cleanup, reject before any deletion and preserve all files. Do not catch/ignore the shared validator's failure.

## Other gates

- Root regression from the submitted report is not independently rerun after this round; prior baseline remains `694 passed, 1 skipped`.
- R3.1-C is not frozen.
- Agent3 and Agent2 are not called.
- Do not modify production code, frozen tests/assets, root `.mcp.json`, or hardware integrations.

