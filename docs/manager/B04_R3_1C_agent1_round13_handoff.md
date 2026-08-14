# B04 R3.1-C Agent1 Round 13 Handoff

## Target and memory

**Target: Agent1 (external Claude Code). Use a fresh Agent1 memory.** The user manually forwards this prompt. Agent1/2/3 are not Codex sub-agents.

## Current status

R3.1-C is implemented but **not frozen**. Agent3 and Agent2 have not been called. The Manager Reviewer independently reproduced the clean gate and found three blocking provenance gaps documented in [`B04_R3_1C_round12_audit.md`](D:\fpgaproject\docs\manager\B04_R3_1C_round12_audit.md).

## Task

Complete and prove a single, fail-closed remediation round for the R3.1-C phase black-box preparation project. Read the audit document first, then inspect the current workspace. Do not merely add a test that detects the issue; fix the shared contract and make runner, `verify_readiness.py`, and `cleanup.py` enforce it consistently before any runner evidence directory or MCP server is created.

### Mandatory fixes

1. Require `receipt.input_artifacts` to exist, be a non-empty list, and exactly match the three provisioned project artifacts. Validate entry shape, relative/canonical path, no symlink/junction, SHA format, disk SHA, and expected artifact set. Missing, empty, duplicate, extra, malformed, escaped, symlinked, junctioned, missing, or tampered artifacts must fail closed.
2. Bind fixture provenance to the canonical checked-in fixture directory. Validate the source path, canonical expected filenames, and actual source-file SHA against the shared frozen constants. Receipt self-reported hashes alone are insufficient.
3. Bind all receipt precondition expectations: stage, lane, worker state/PID, `platform_revision`, `platform_revision_public_expected`, session identity, board identity, and project path to the manifest, effective expected contract, and ledger. Reject contradictory or missing values before evidence/MCP startup.
4. Preserve the existing canonical manifest/effective-contract/SHA/path validation, safe evidence creation, invalid scenario rejection, and cleanup all-or-nothing symlink/junction scan. Do not regress these protections.

## Scope and prohibitions

- Modify only the phase black-box preparation project, its Manager harness verifier/cleanup, and narrowly necessary project-local docs/tests.
- Do not modify production code under `mcps/zynq_mcp/`, frozen tests/assets, root `.mcp.json`, Vivado/Vitis MCP projects, or start hardware/Vivado Worker/JTAG/board operations.
- Do not run `provision.py` or `verify_readiness.py` as Agent3 work; this is still Agent1 preparation.
- Do not call Agent3 or Agent2. Do not claim R3.1-C frozen.
- Keep the runner on MCP SDK `stdio_client` + `ClientSession`; no internal `mcps.zynq_mcp` imports.

## Required verification

Use a new temporary base directory and preserve evidence. Run:

```powershell
python validation_projects/phase_blackbox/_manager/r3_1c_smoke/provision.py --scenario all --base-dir <new-base>
python validation_projects/phase_blackbox/_manager/r3_1c_smoke/verify_readiness.py --manifest <manifest> --approved-base <new-base>
python validation_projects/phase_blackbox/r3_1c_smoke/runner.py --manifest <manifest> --scenario all --run-id <clean-id>
python -m pytest mcps -q -W error::RuntimeWarning
```

For each startup-precondition negative case, prove non-zero exit before evidence/MCP startup and show no evidence target/summary, no ledger or artifact mutation, and no residual test process. Cover at least:

- missing, empty, duplicate, extra, malformed, escaped, symlink, junction, missing, and SHA-tampered `input_artifacts`;
- fixture source outside canonical directory, missing/extra fixture fields, and source-file tampering;
- every receipt expectation field listed in the audit;
- receipt/manifest/effective-contract identity and SHA mismatches;
- manifest path escapes and invalid `--scenario`;
- pre-existing evidence directory/file/symlink/junction;
- cleanup dry-run, execute, forged manifest, outside target, nested symlink, and nested junction with zero partial deletion.

## Report format

Return exact modified files and SHA256 values, clean counts, negative-test counts and commands, exit codes, evidence paths, process cleanup result, and exact regression arithmetic (`695 collected = 694 passed + 1 skipped + 0 failed`). Status must remain `READY FOR MANAGER RE-REVIEW / NOT EXECUTED` until the Manager Reviewer independently verifies it.

