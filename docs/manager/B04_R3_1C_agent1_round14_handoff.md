# B04 R3.1-C Agent1 Round 14 Handoff

## Target

**Target: Agent1, external Claude Code, fresh memory.** The user manually forwards this prompt. Agent1/Agent2/Agent3 are external agents, not Codex sub-agents.

## Read first

Read:

1. `D:\fpgaproject\docs\manager\B04_R3_1C_round13_audit.md`
2. `D:\fpgaproject\docs\manager\B04_R3_1C_agent1_round13_handoff.md`
3. Current `_provenance.py`, `runner.py`, `verify_readiness.py`, and `cleanup.py`.

## Blocking task

Complete one unified fail-closed remediation. The Round 13 shared validator works in `runner.py`, but `verify_readiness.py` and `cleanup.py` still accept tampered receipts and cleanup can delete evidence. Make all three consumers enforce the same provenance contract.

### Mandatory behavior

- Add a shared non-exiting/report-producing validation API, or a carefully wrapped equivalent, so readiness can return per-scenario failures without accepting any invalid scenario.
- `verify_readiness.py` must fail (overall FAIL, non-zero exit) for missing/empty/malformed/duplicate/extra/escaped/symlinked/junctioned/tampered `input_artifacts`; fixture source/key/hash tampering; all receipt expectation fields (`stage`, lane, worker state/PID, revision, public revision, session, board, project); ledger mismatches; effective expected identity/SHA mismatches; and manifest/path provenance errors.
- `cleanup.py` must run the complete validation before checking/deleting a run-id. Any invalid receipt or fixture must exit non-zero and preserve every evidence file. No local subset may bypass the shared checks.
- Runner behavior and existing path/junction protections must remain intact.

## Required fresh verification

Use a different temporary base directory for every negative case. For each case run all three entrypoints and record:

- exact exit code;
- whether evidence target/`summary.json` was created;
- whether cleanup deleted anything;
- ledger/artifact SHA and state before/after;
- residual MCP/Python process count.

At minimum cover the eight tamper cases listed in the Round 13 audit, plus artifact count/path/SHA variants, fixture variants, effective contract/SHA, manifest escape, invalid scenario, evidence pre-existence, and cleanup nested symlink/junction atomicity. Then run the clean flow and:

```powershell
python -m pytest mcps -q -W error::RuntimeWarning
```

Report exact `695 collected = 694 passed + 1 skipped + 0 failed` arithmetic, modified-file SHA256 values, evidence locations, and status `READY FOR MANAGER RE-REVIEW / NOT EXECUTED`. Do not call Agent3 or Agent2 and do not freeze R3.1-C.

