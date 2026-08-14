# B04 R3.1-C Round 14 Manager Audit

Date: 2026-08-08  
Status: **BLOCKED — Agent1 remediation required; Agent3/Agent2 not called**

## Clean gate reproduced

Fresh provisioning and the reported clean flow were structurally present. The submitted Round 14 hashes were also independently observed in the workspace. The prior baseline regression remains `694 passed, 1 skipped`.

## Blocking findings

### P1 — Input artifact manifest is not bound to the required platform revision

The shared validator accepts any file under `manifests/platform/*`. On a fresh provisioned runtime, the Manager Reviewer copied the real platform manifest to `manifests/platform/wrong_revision.json`, changed the success receipt's third artifact to that file with its correct disk SHA, and ran the verifier and runner. Both returned success.

The receipt must identify the exact canonical platform manifest filename derived from the scenario's expected platform revision (the same `_revision_to_filename` contract used by provisioning), and the validator must compare the artifact's normalized relative path exactly. A valid SHA for the wrong revision is still invalid.

### P1 — Cleanup skips Ledger stage validation for every scenario

`cleanup.py` calls `validate_all_identity_bindings_reporting(..., check_ledger_stage=False)`. On a fresh provisioned runtime, the Manager Reviewer changed a scenario Ledger `context.current_stage` to an unrelated stage and created a valid cleanup target. Cleanup returned exit 0 and deleted the target for `wrong_stage`, `success`, and `missing_revision`.

Cleanup must not globally skip stage validation. Define an explicit post-execution stage contract per scenario/profile, or require a recorded valid terminal state in the runner summary/evidence and validate that state before deletion. At minimum, malformed/unrecognized stage values and scenario-incompatible stages must reject. Cleanup must preserve all files on failure.

## Required secondary checks

While fixing the two P1 issues, retain and re-run all Round 13 requirements:

- complete input artifact count/shape/path/SHA/no-link validation;
- canonical source-bound fixture verification;
- all receipt expectation and Ledger identity fields;
- effective expected contract/SHA and manifest path provenance;
- pre-existing evidence target and invalid scenario rejection;
- cleanup nested symlink/junction zero-partial-delete behavior;
- fresh clean gate and root regression arithmetic.

R3.1-C is not frozen. Do not call Agent3 or Agent2.

