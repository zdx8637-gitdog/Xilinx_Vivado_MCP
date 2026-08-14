# B04 R3.1-C Round 10 Manager Audit

> Date: 2026-08-08
> Role: Manager Reviewer independent audit
> Verdict: **NOT READY FOR AGENT3 / NOT FROZEN**
>
> Agent1, Agent2, and Agent3 are external Claude Code agents operated by the user. Codex does not spawn them; the user forwards the labeled prompt.

## Independent clean gate

Fresh Manager provisioning under `D:\tmp\r3_1c_mgr_r10_audit_20260808\prov_eac063f21aac\` passed:

- `verify_readiness.py`: 5/5 PASS.
- `runner.py --scenario all`: 5/5 PASS, 107/107 assertions.
- `executed=5`, `skipped=0`, `missing=0`, `failed=0`.
- No test-launched MCP/Python processes remained.

## Blocking findings

### P1-1: receipt identity is not bound to the manifest entry

`runner.py` validates `manifest.scenarios[scenario].runtime_root` and `receipt_path` as paths inside `run_root`, then reads the receipt and uses `receipt["runtime_root"]` in `_run_one()` to set `ZYNQ_RUNTIME_ROOT`. It does not cross-check receipt `runtime_root`, `project_path`, `session_id`, or stage against the manifest entry/expected contract before starting the MCP server.

Independent reproduction:

- Fresh five-scenario provisioning was created.
- The `success` receipt was changed to point at the `capabilities` runtime while the manifest entry remained unchanged.
- Runner launched the MCP server and executed `pl_generate_system_top` against the capabilities runtime. It later failed the session identity assertions, but the wrong runtime was mutated first: its ledger advanced and `system_top.v` was generated.

Required closure: fail closed before MCP startup when receipt identity does not exactly match the manifest entry and the effective expected contract. Validate at minimum `scenario`, `runtime_root`, `project_path`, `session_id`, `board_id`, `expected_initial_stage`, `platform_revision`, and `effective_expected_sha256`; require all relevant paths to remain within the entry runtime.

### P1-2: manifest `run_root` provenance is not anchored to `base_dir`

`runner.py` trusts `manifest.run_root` as the containment root and does not require it to be a strict descendant of `manifest.base_dir` (nor validate the provision/harness identity). A copied manifest with `run_root=base_dir` and `approved_evidence_root` under that broader root passed the runner and wrote evidence outside the provision run directory:

`D:\tmp\r3_1c_mgr_r10_audit_20260808\escaped_evidence_broad_root\broad_root_escape\summary.json`

Required closure: before any evidence creation, receipt read, or MCP startup, require `base_dir`, `run_root`, and manifest location to be structurally consistent with the Manager provisioning manifest. Mirror cleanup's provenance checks as appropriate: `harness_version`, `provision_run_id`, strict `base_dir -> run_root`, strict `run_root -> approved_evidence_root/effective_expected_dir`, exact scenario set, and receipt/effective-contract cross-checks.

## Scope constraints

- Do not modify production code, frozen tests, `.mcp.json`, board assets, or `Xilinx_Vivado_MCP`.
- Do not call Agent3 or Agent2.
- Do not enter R3.2-R3.5.
- Preserve all existing clean and negative evidence.
- Do not delete unrelated persistent Vivado services.

## Re-review gate

After remediation, Manager Reviewer will independently verify both P1 cases with fresh provisioning, require non-zero exit before evidence/MCP startup, rerun the clean gate, inspect residual processes, and recompute hashes. Only then can routing proceed to an Agent3 fresh-memory prompt.

## Round 11 full-system audit findings (2026-08-08)

Round 11 was independently rechecked as a complete project, not only by its reported tests.

### Clean result

- Fresh provisioning: 5 runtimes.
- `verify_readiness.py`: 5/5 PASS.
- `runner.py --scenario all`: 5/5 PASS, 107/107 assertions.
- No residual test-launched MCP/Python processes.

### Additional blocking findings

#### P1-3: cleanup has partial-deletion behavior for Windows junctions

On Windows, `os.path.islink(junction)` is `False` while `os.path.isjunction(junction)` is `True`. Cleanup v6 scans only `os.path.islink()`. A fresh evidence directory containing a normal child followed by a junction produced:

- normal child deleted;
- junction deletion raised `Cannot call rmtree on a symbolic link`;
- summary and target directory remained;
- outside junction target remained intact.

This is still unsafe partial deletion. Cleanup must reject both symlinks and junctions before collecting/deleting any item, and the negative test must prove all in-tree items remain unchanged after rejection.

#### P1-4: manifest/receipt metadata is still not fully fail-closed

Independent mutations unexpectedly returned runner PASS and executed MCP:

- `manifest.provision_run_id` changed to an unrelated non-empty value;
- `receipt.harness_version` changed;
- `manifest.scenarios.capabilities.expected_stage` changed;
- `manifest.scenarios.capabilities.scenario` changed;
- `receipt.platform_revision` changed for `wrong_stage`;
- receipt file relocated within `run_root` without runner rejection;
- input wrapper bytes tampered for a rejection-only scenario and runner still passed.

Required closure is to bind all metadata that defines the supplied precondition and input contract before MCP startup. At minimum validate:

- `provision_run_id == basename(run_root)` and valid format;
- every entry `scenario == key`;
- entry `expected_stage == receipt.expected_initial_stage` and effective contract stage;
- receipt `harness_version == HARNESS_VERSION`;
- receipt `runtime_root`, `project_path`, `session_id`, `board_id`, stage, platform revision, effective SHA, and receipt path are bound to the manifest entry;
- scenario-specific platform revision is bound to an explicit checked-in/effective contract value, including the intentional empty revision case;
- all receipt input artifacts exist, remain under project, and match their recorded SHA before MCP startup;
- relocated receipt paths are rejected unless they are the canonical manifest-generated path (or the contract explicitly defines a canonical path rule).

Do not treat a later assertion failure as sufficient. All these checks must happen before evidence directory creation and before any MCP server starts.

#### P1-5: runner can read an arbitrary output artifact path

`run_success()` trusts `get_operation_status.result.data.output_path`, hashes it, and copies it without requiring the path to be inside the bound scenario project/runtime. Add a realpath containment check before reading/copying the artifact; out-of-project output must fail without copying external content.

#### P2-1: invalid scenario is checked after evidence creation

Validate `--scenario` against the allowed set before `_safe_mkdir_evidence`. An invalid scenario must exit non-zero without creating evidence or starting MCP.

### Required next gate

The next Agent1 round must implement and test all P1-3 through P1-5 and P2-1 together. Manager Reviewer will then run a fresh end-to-end matrix covering runner, provisioning, readiness, and cleanup. No Agent3 routing is allowed until the full matrix is green and the no-partial-delete property is demonstrated.
