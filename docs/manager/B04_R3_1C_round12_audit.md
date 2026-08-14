# B04 R3.1-C Round 12 Manager Audit

Date: 2026-08-08  
Reviewer: Manager Reviewer (Codex)  
Status: **BLOCKED — Agent1 remediation required; Agent3/Agent2 not called**

## 1. Reproduced clean gate

Fresh Manager provisioning under `D:\tmp\manager_r12_audit2\prov_ee7b1db570e5\` passed:

- `provision.py --scenario all`: five independent runtimes created.
- `verify_readiness.py`: 5/5 PASS.
- `runner.py --scenario all`: 5/5 PASS, 107/107 assertions, `executed=5`, `skipped=0`, `missing=0`, `failed=0`.
- Root regression: `694 passed, 1 skipped` with `python -m pytest mcps -q -W error::RuntimeWarning`.
- No residual test-launched MCP/Python process observed.

Cleanup also passed for a normal dry-run and execute path. A nested Windows junction was rejected before deletion and all evidence remained intact.

## 2. Blocking findings

These are not hypothetical. Each was reproduced by temporarily changing one provision receipt and restoring the file afterward.

### P1 — Missing or empty `input_artifacts` is accepted by provenance

`_provenance.validate_all_identity_bindings()` iterates `receipt.get("input_artifacts", [])`. Removing the field or replacing it with `[]` therefore passes pre-validation. `verify_readiness.py` also reports PASS. The runner creates an evidence directory, starts the MCP server, and can pass a capabilities-only run. This violates the stated contract that the receipt must contain the complete immutable input artifact inventory and that provenance failures happen before evidence/MCP startup.

Required behavior: the field must be required, a non-empty list, with exactly the expected three artifacts for every scenario; each entry must have a relative path, `sha256:<64 hex>`, canonical project containment, no symlink/junction, and a disk SHA match. The verifier, runner, and cleanup must all use the same fail-closed rule.

### P1 — Fixture provenance is self-reported rather than source-bound

Changing only `receipt.fixture_provenance.source` to `D:\outside` still passes both verifier and runner. The fixed fixture hashes are checked, but the source path is not canonicalized and the actual source files are not re-read and compared against the frozen fixture directory. A receipt can therefore claim the correct hashes while naming an unrelated source.

Required behavior: define the canonical checked-in fixture directory and expected source paths in one shared contract; require `fixture_provenance.source` to resolve exactly to that directory; re-hash the canonical source files and compare to `FROZEN_FIXTURE_SHA`; reject missing/extra fixture provenance fields where appropriate. Do not trust receipt-provided hashes as the only source of truth.

### P1 — Several receipt expectation fields are not bound

Changing `platform_revision_public_expected`, `expected_initial_lane`, or `expected_worker_state/expected_worker_pid` in a receipt still passes verifier and runner. These fields are part of the precondition receipt and must be consistent with the ledger and the per-scenario contract. At minimum:

- `expected_initial_stage` == manifest entry == ledger current stage == effective expected stage.
- `expected_initial_lane` == `IDLE` == ledger execution lane.
- `expected_worker_state` == `ABSENT`, `expected_worker_pid` == `null`, and ledger worker state/PID match.
- `platform_revision` == the scenario revision contract and ledger context revision.
- `platform_revision_public_expected` == the platform manifest revision for non-empty revision scenarios and `""` for `missing_revision`.
- `board_id` and all session identity fields remain cross-bound to ledger and effective expected data.

All checks must run before `_safe_mkdir_evidence()` and before MCP server launch. The runner must not execute a subset scenario while provenance validation silently accepts an incomplete receipt for another required scenario.

## 3. Required implementation and evidence

Implement the complete remediation in one Agent1 round. Keep production code, frozen tests/assets, root `.mcp.json`, Vivado/Vitis MCP projects, and `docs/brick_development_plan.md` unchanged unless a narrowly necessary documentation correction is explicitly justified. Do not call Agent3 or Agent2.

The returned report must include:

1. Exact modified-file list and SHA256 values.
2. A clean fresh flow: provisioning, readiness, runner all five scenarios, and exact assertion arithmetic.
3. Negative tests for every missing/empty/malformed artifact inventory case, fixture source/path/hash tampering, all unbound receipt expectation fields, effective-contract identity/SHA tampering, manifest path/provenance tampering, invalid scenario, evidence pre-existence, and cleanup symlink/junction atomicity.
4. For every startup-precondition negative test: non-zero exit, no evidence directory or `summary.json`, MCP server not started, runtime ledger/artifacts unchanged, and no residual process.
5. Cleanup dry-run, execute, forged manifest, outside path, nested symlink, and nested junction evidence. Junction rejection must preserve every file.
6. Root regression arithmetic: `695 collected = 694 passed + 1 skipped + 0 failed`.

Do not report “READY FOR Agent3” or freeze R3.1-C until a fresh Manager Reviewer audit reproduces all of the above.

