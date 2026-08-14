# B04 R3.1-C Agent1 Round 15 Handoff

## Target

**Agent1, external Claude Code, fresh memory.** The user forwards this prompt manually. Codex does not call Agent1/Agent2/Agent3 as sub-agents.

## Read first

Read `D:\fpgaproject\docs\manager\B04_R3_1C_round14_audit.md` and inspect the current phase black-box project before editing.

## Blocking fixes

1. **Bind the exact platform manifest artifact.** The validator currently accepts any `manifests/platform/*` path. Derive the required filename with the same canonical `_revision_to_filename` rule used by the Manager provisioning harness, using the scenario's expected platform revision. Require the normalized receipt `relative_path` to equal exactly `manifests/platform/<expected_filename>` for all applicable scenarios. Verify the file's platform/manifest revision fields and disk SHA; a renamed or wrong-revision JSON must fail before evidence/MCP startup. Keep `missing_revision`'s expected contract explicit.
2. **Make cleanup stage-safe.** Do not globally use `check_ledger_stage=False`. Define and validate a documented post-execution stage/status contract per scenario, using the runner summary/evidence and Ledger. Cleanup must reject malformed, unrelated, or scenario-incompatible stages and preserve every file. A fresh pre-execution runtime with an arbitrary Ledger stage must not be deletable.

## Scope and prohibitions

- Modify only the phase black-box preparation project, Manager harness verifier/cleanup, and narrowly necessary project-local docs/tests.
- Do not modify production code, frozen tests/assets, root `.mcp.json`, Vivado/Vitis MCP projects, or hardware integrations.
- Do not call Agent3 or Agent2. Do not claim R3.1-C frozen.
- Keep runner on MCP SDK `stdio_client` + `ClientSession`; no internal `mcps.zynq_mcp` imports.

## Required verification

Use separate fresh provisioning roots. Prove:

- clean five-scenario flow: readiness 5/5, runner 107/107;
- wrong manifest filename/revision rejected by runner, verifier, and cleanup before side effects;
- every wrong/unknown Ledger stage rejected by cleanup, with evidence preserved;
- all Round 13 artifact, fixture, receipt identity, manifest path, evidence pre-existence, and junction/symlink negative cases still fail closed;
- cleanup valid post-run target dry-run and execute behavior;
- no residual test-launched MCP/Python process;
- `python -m pytest mcps -q -W error::RuntimeWarning` with exact `695 collected = 694 passed + 1 skipped + 0 failed`.

Return exact modified files and SHA256 values, commands, exit codes, evidence paths, negative-test counts, and status `READY FOR MANAGER RE-REVIEW / NOT EXECUTED`. Do not execute Agent3.

