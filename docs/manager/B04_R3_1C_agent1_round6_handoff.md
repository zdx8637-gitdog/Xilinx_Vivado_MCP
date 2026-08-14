# B04 R3.1-C Agent1 Handoff: Phase Black-Box Round 6

Date: 2026-08-08

Status: ACTIVE HANDOFF. R3.1-C is not frozen. The phase black-box project is not executed by Agent3.

## 1. Roles and routing

- Manager Reviewer (Codex): independently audits Agent1 work and decides the next prompt.
- Agent1: external Claude Code continuation-memory implementation and white-box/black-box project preparation agent.
- Agent3: external Claude Code fresh-memory independent phase black-box executor; not called yet.
- Agent2: external Claude Code fresh-memory final black-box executor; forbidden until B08 is complete.
- User: human hardware acceptance when physical hardware results are required.

Codex does not spawn these agents. The next task after this handoff is an **Agent1 prompt**, with fresh Agent1 memory, which the Manager Reviewer gives to the user for manual forwarding. Do not route this state to Agent2 or Agent3.

## 2. Scope and immutable boundaries

Current scope is only the R3.1-C phase black-box preparation project:

- `validation_projects/phase_blackbox/r3_1c_smoke/`
- `validation_projects/phase_blackbox/_manager/r3_1c_smoke/`
- `validation_projects/README.md` only when an index update is required

Do not modify:

- production code
- `mcps/zynq_mcp/tests`
- R3.1-A, R3.1-B, or R3.1-C frozen assets
- root `.mcp.json`
- `docs/manager` during normal Agent1 work
- `validation_projects/phase_blackbox/r3_1c_smoke/CLAUDE.md`

Agent3 must use the supplied runner and its own stdio MCP server. Claude Code MCP configuration and a Claude Code restart are not required for this phase.

## 3. Project and execution model

The Manager-only `provision.py` creates four isolated preconditioned runtimes. Each runtime contains an execution ledger, a small project fixture, a platform manifest, a wrapper, a dummy XSA, and a provision receipt. This is a test fixture, not a Vivado-created hardware project and not a public `create_session` lifecycle test.

`verify_readiness.py` checks those provisioned runtimes before Agent3 receives them. Agent3 must not run provisioning, read the ledger, or use internal `mcps.zynq_mcp` imports.

`runner.py` starts `python -m mcps.zynq_mcp.server` through the MCP SDK stdio transport and calls only public tools. The five scenarios are:

1. `capabilities`
2. `success`
3. `missing_revision`
4. `wrong_stage`
5. `invalid_schema`

This is a preconditioned R3.1-C public MCP smoke, not B09. No Vivado Worker or real hardware is required.

## 4. Work completed before context overflow

The previous Agent1 context overflowed while running the Round 6 negative suite. The following changes are present in the workspace:

- `runner.py`: `_observe()` now calls public `get_session_info`, saves `responses/get_session_info.json`, and emits facts for success, session ID, board ID, project path, and stage.
- All five expected JSON files contain the new `session_info_*` assertions.
- `verify_readiness.py`: Ledger context now checks `board_id` and `project_path` against the receipt in addition to session ID and platform revision.
- `validation_projects/phase_blackbox/r3_1c_smoke/CLAUDE.md`: project-local Agent3 instructions are present and must remain unchanged.
- The expected-output/facts architecture from Round 5 remains in place: scenario functions collect actual facts and `_run_expected()` compares them with JSON `expect`/`expect_not_contains` values.

Current workspace hashes after the partial Round 6 edits:

| File | SHA256 prefix | Note |
|---|---|---|
| `validation_projects/phase_blackbox/r3_1c_smoke/runner.py` | `84EC41C324B63A9...` | session-info facts added |
| `validation_projects/phase_blackbox/_manager/r3_1c_smoke/verify_readiness.py` | `161EED7E1BFAB9D...` | board/project identity checks added |
| `expected_outputs/capabilities.json` | `8BAA8085422A560...` | session-info assertions |
| `expected_outputs/success.json` | `C267EF11A56E18C...` | session-info assertions |
| `expected_outputs/missing_revision.json` | `5D14A82124DE148...` | session-info assertions |
| `expected_outputs/wrong_stage.json` | `7AF24370E79657F...` | session-info assertions |
| `expected_outputs/invalid_schema.json` | `28D03559CE62052...` | session-info assertions |

These hashes are a handoff snapshot, not a freeze declaration. Recompute full hashes after the next changes.

## 5. Reported but not yet independently closed

Agent1 reported a fresh provisioning, readiness run, and runner v6 clean run with all five scenarios passing and expected/consumed counts equal. The negative suite then failed to complete because the Agent1 context exceeded the model context limit. Manager Reviewer has not accepted the Round 6 report as closed.

The following evidence is still required:

- clean provisioning and readiness after the partial Round 6 edits
- clean runner all five scenarios after the partial Round 6 edits
- expected-value tampering for all five scenarios, each non-zero
- missing expected file, missing receipt, and missing manifest entry classification
- Ledger tampering for `board_id`, `project_path`, `session_id`, and `platform_revision`
- `get_session_info` failure or wrong-field propagation to runner failure
- Artifact `../`, absolute, and symlink escape rejection
- approved-base escape rejection
- cleanup invalid `run_id` rejection
- runner import boundary and frozen SHA verification

Do not call the project READY FOR AGENT3 until all required evidence is independently reproducible.

## 6. Required continuation prompt for new Agent1

Target: **Agent1**

Memory: **fresh memory; read this handoff first**

Task: finish and prove Round 6 remediation for the R3.1-C phase black-box preparation. Do not call Agent3 or Agent2.

1. Read this file, `docs/manager/manager_reviewer_workflow.md`, the current phase black-box README/`CLAUDE.md`, and the current runner, expected JSON, provisioner, verifier, and cleanup files.
2. Preserve all scope restrictions in §2. Do not modify production, frozen assets, root `.mcp.json`, or this handoff document.
3. Validate the current partial edits:
   - public `get_session_info` facts are emitted and compared by expected JSON in all five scenarios;
   - verifier checks Ledger `session_id`, `board_id`, `project_path`, and `platform_revision` against the receipt;
   - path failures stop the affected scenario before Ledger/Artifact reads.
4. Run the complete clean path in a fresh output directory:
   - `provision.py --scenario all`
   - `verify_readiness.py --manifest <manifest> --approved-base <approved-base>`
   - `runner.py --manifest <manifest> --scenario all --evidence-base <new-evidence> --expected-dir validation_projects/phase_blackbox/r3_1c_smoke/expected_outputs`
5. Run the complete negative matrix in isolated copies. Every test must have a non-zero exit where applicable and an explicit evidence check:
   - tamper one `expect` in each of capabilities, success, missing_revision, wrong_stage, and invalid_schema;
   - delete an expected file;
   - remove a manifest entry and remove a receipt; confirm skipped/missing classification and that skipped scenarios are absent from executed;
   - tamper Ledger `board_id`, `project_path`, `session_id`, and `platform_revision`; verifier must fail;
   - make `project_path` leave runtime root; verifier must fail before Artifact reads;
   - use Artifact `../escape`, absolute path, and symlink escape; verifier must report path containment failure;
   - move `run_root` outside `--approved-base`; verifier must fail;
   - pass invalid cleanup run IDs (`.`, `..`, separators, absolute paths, spaces); all must fail;
   - force or simulate `get_session_info` failure/wrong fields and show runner failure through facts/expected assertions.
6. Inspect `summary.json` and every scenario result. Confirm `expected_assertion_count == consumed_assertions`, no unexpected facts, and assertion results contain ID, field, expected, actual, and status.
7. Verify runner imports only Python standard library plus MCP SDK. Verify all R3.1-C frozen SHAs and `.mcp.json` are unchanged.
8. Submit a report with exact commands, exit codes, negative evidence, full SHA256 values, and status:
   `READY FOR MANAGER RE-REVIEW / NOT EXECUTED`.

Do not claim Agent3 readiness, do not execute Agent3, and do not claim R3.1-C frozen.

## 7. Manager review gate after continuation

Manager Reviewer will independently rerun clean and negative tests. Only after all P1/P2 findings are closed may the next routing be an **Agent3 prompt, fresh memory**. Agent2 remains prohibited until B08.

## 8. External-Agent Handoff Rule

This document is a handoff for an external Claude Code Agent1, not a Codex sub-agent task. The user supplies the prompt to Agent1 and returns its report. Codex reviews the resulting workspace and evidence, then issues the next labeled prompt. No internal sub-agent invocation is an acceptable substitute.

## 9. Current Audit Caveat

Later continuation edits may have changed the files and hashes listed above. Recompute hashes from the workspace. Do not accept unresolved `PLACEHOLDER_*` values in checked-in expected JSON, and do not accept runner-side silent mutation of expected values from a receipt as proof that the JSON is the sole source of truth. The continuation prompt must explicitly close this contract and audit both cleanup implementations for approved-root containment before Agent3 routing.

## 10. Routing Correction

Agent1, Agent2, and Agent3 are external Claude Code agents operated by the user. Codex must not spawn them as internal sub-agents. The Manager Reviewer supplies the labeled prompt to the user for manual forwarding and reviews the returned report. Any prior internal sub-agent invocation is invalid routing and must not be treated as Agent1, Agent3, or Agent2 work.
