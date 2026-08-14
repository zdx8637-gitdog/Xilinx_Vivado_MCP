# B04 R3.1-C Codex Audit Handoff

> Manager canonical location: `docs/manager/B04_R3_1C_codex_audit_handoff.md`  
> For the latest review status and prompt-routing rules, read `docs/manager/manager_reviewer_workflow.md` first.

> Date: 2026-08-07  
> Role: independent white-box review handoff  
> Verdict: **R3.1-C IMPLEMENTED / AUDIT NOT PASSED / NOT FROZEN**  
> R3.2+: **NOT STARTED**  
> Agent2: **NOT CALLED**

## 1. Fast recovery

The project uses one unified `zynq_mcp`, one process-wide execution channel, and the persistent Execution Ledger as the lifecycle source of truth.

Current accepted baseline:

- B00-B03: COMPLETE / FROZEN.
- B04 R1: COMPLETE / FROZEN (89 tests).
- B04 R2: COMPLETE / FROZEN (35 tests).
- B04 R3.0: COMPLETE / FROZEN (36 tests).
- B04 R3.1-A: COMPLETE / FROZEN (18 tests).
- B04 R3.1-B: COMPLETE / FROZEN (50 tests).
- B04 R3.1-C: implementation exists, but this audit found blocking regressions and evidence gaps. It is not frozen.
- B04 R3.2-R3.5: not started.

The current production server exposes 10 tools: 9 control APIs plus `pl_generate_system_top`. This exposure is real, but the new domain API's full MCP SDK behavior is not independently covered by the submitted R3.1-C tests.

## 2. Mandatory reading order for the next reviewer

1. `CLAUDE.md`
2. `docs/brick_development_plan.md`
3. `docs/development/mcp/B04_R3_implementation_plan.md`
4. `docs/development/tests/B04_R3_test_plan.md`
5. `docs/development/B04_R3_1B_to_R3_1C_handoff.md`
6. This document
7. Submitted report: `C:\Users\zdx86\.codex\attachments\916dd691-6f2f-4824-a961-59034c47d640\pasted-text.txt`

`docs/brick_development_plan.md` still says R3.1-C is next. That is a pre-review status and must not be interpreted as proof that R3.1-C has not been implemented. Do not update the Brick status to frozen until all blockers below are closed and the full regression is green.

## 3. Current R3.1-C file state

Production and test files changed by R3.1-C:

| File | Current SHA256 |
|---|---|
| `mcps/zynq_mcp/control/domain_runner.py` | `ec13afbf7c23e3918b43db4fbca4b9f7188dae0cc256011e27db09efe9732c94` |
| `mcps/zynq_mcp/dispatcher.py` | `f4175fe18437dd4070a557abe941a4c08a28681dd77f9a56f71e0bb250710f9d` |
| `mcps/zynq_mcp/control/capabilities.py` | `27a6158143c20b16f5df480b1a19464b9b72a6ab5c63347194a89490adc40d7c` |
| `mcps/zynq_mcp/server.py` | `a426b2111ca5771e5db9aad872d88c29dc92717348131a73868ed844472bb828` |
| `mcps/zynq_mcp/tests/test_r3_runner.py` | `512d466e34de239061ee39dbdf486d55be8de1ca56364b32d22e6b8aa8250d55` |
| `mcps/zynq_mcp/tests/test_r3_1c_public.py` | `f488b69b8d1c8995fcac2a731eea23c2dee00949427cadc8c14c87374a59a3c6` |

R3.1-B frozen files were unchanged during this audit:

| File | Frozen SHA256 |
|---|---|
| `mcps/zynq_mcp/domains/pl/system_top.py` | `7ffe07bc77578548ebd3af66a6df3b4ff4f72f316123ff5d26c99817e2d642c1` |
| `mcps/zynq_mcp/tests/test_r3_1b_pl.py` | `ff7529e9ea6c71c8a94c2891ee31a8f62bc80192136437283b80bd803dab1be9` |

The R3.0 test file had a one-line baseline change from frozen SHA256 `32ae422309f28f0c21fa8bfff7f47b1c4f61ab930d76ea699d550821fbc82cc3` to the current value above. This requires an explicit controlled test-baseline erratum; it must not be described as an unchanged frozen test.

## 4. Independent commands and results

Run from `D:\fpgaproject`, not from `D:\fpgaproject\mcps`:

```powershell
python -m pytest mcps/zynq_mcp/tests/test_r3_1c_public.py -q -W error::RuntimeWarning
```

Result: `15 passed`.

```powershell
python -m pytest mcps/zynq_mcp/tests -q -W error::RuntimeWarning
```

Result: `241 passed, 2 failed`.

```powershell
python -m pytest mcps -q -W error::RuntimeWarning
```

Result: `682 passed, 2 failed, 1 skipped`.

The two failures are deterministic capability-count regressions, not `ModuleNotFoundError`:

1. `mcps/zynq_mcp/tests/test_r1_mcp_sdk.py:111` expects `total_tools == 9`, actual value is 10.
2. `mcps/zynq_mcp/tests/test_r2_adapter.py:756` expects `total_tools == 9`, actual value is 10.

The submitted report's explanation that these were pre-existing subprocess import failures is factually incorrect when the required regression command is run from the project root.

Mechanical collection:

- `mcps/zynq_mcp/tests`: 243 tests.
- `mcps` total: 685 outcomes = 682 passed + 2 failed + 1 skipped.
- R3.1-C file: 15 tests.

## 5. Audit findings

### P0-1: full regression is red

R3.1-C cannot freeze while the two real MCP SDK tests fail. Progressive capability changes must update all authorized capability assertions through a controlled baseline patch, then the root full regression must produce zero failures.

Evidence:

- `mcps/zynq_mcp/tests/test_r1_mcp_sdk.py:111`
- `mcps/zynq_mcp/tests/test_r2_adapter.py:756`
- `mcps/zynq_mcp/tests/test_r3_runner.py:714-717` still names the test `test_Xlist_tools_stays_nine` while asserting 10.

### P0-2: the submitted “Public” tests are component tests

`test_r3_1c_public.py` imports `CommandRunner`, `_pl_generate_local_fn`, `ALL_TOOLS`, and `DOMAIN_TOOLS` directly. It does not import MCP SDK `ClientSession` or `stdio_client`, launch `python -m mcps.zynq_mcp.server`, or call the public API through `ClientSession.call_tool()`.

The four required public cases are therefore not public MCP tests:

- R313 directly constructs `CommandRunner` at `test_r3_1c_public.py:308-347`.
- R321 directly constructs `CommandRunner` at `test_r3_1c_public.py:376-395`.
- R3S13 directly constructs `CommandRunner` at `test_r3_1c_public.py:401-417`.
- R3S14 reads `ALL_TOOLS` directly at `test_r3_1c_public.py:422-428`.

The file's module docstring claims “MCP SDK ClientSession entry,” but no such path exists in the file. Registration is partly evidenced by the old SDK tests seeing 10 tools, but the new tool's end-to-end accepted/wait/terminal behavior remains unproved.

### P0-3: Contract A says immutable snapshot; implementation passes a mutable dict

`domain_runner.py:314-317` creates a plain `dict`, and `domain_runner.py:372-438` passes that same mutable object to the local handler. There is no immutable wrapper or defensive immutable representation.

This does capture all fields from one Ledger transaction, which is good, but it does not satisfy the handoff's explicit immutable-snapshot contract. A test must attempt mutation and prove the snapshot cannot be changed, or the contract must be explicitly renegotiated before code changes.

### P1-1: Contract B's no-false-dedup behavior is not tested

`test_r3c03_different_revision_different_signature` at `test_r3_1c_public.py:186-204` calls `request_signature()` twice directly and compares two strings. It does not admit two operations and does not prove both operations succeed independently as required by the handoff.

### P1-2: deterministic error-mapping coverage is incomplete and imprecise

The report says all four component exception classes have dedicated tests. The test file does not contain dedicated `WrapperParseError`, `PathSafetyError`, or `AtomicWriteError` runner tests.

Additional evidence-quality problems:

- `test_r3c06_manifest_binding_error_failed_idle` only asserts `FAILED` and `IDLE`; it does not assert the exact reason code (`test_r3_1c_public.py:257-277`).
- Missing/invalid revision tests search for a substring in a serialized error instead of asserting an exact structured reason code (`test_r3_1c_public.py:249-253`, `393`).
- Unknown-exception handling accepts either `INTERRUPTED` or `OUTCOME_UNKNOWN`, although the injected `RuntimeError` should have one deterministic result (`test_r3_1c_public.py:279-298`).

### P1-3: the concurrency test does not prove clean terminal behavior

`test_r3c08_two_domain_commands_serialized` puts an `asyncio.Barrier(2)` inside the admitted operation's local handler (`test_r3_1c_public.py:472-476`). The second command is rejected before its handler runs, so only one participant can reach the two-party barrier. The admitted background task cannot complete normally.

The test then verifies only one admission plus one `CHANNEL_BUSY` and exits without waiting for the admitted operation to reach a terminal state or proving task cleanup (`test_r3_1c_public.py:490-498`). The event loop can cancel the orphaned task during test teardown. This is not valid single-channel lifecycle evidence.

The same file also contradicts its own “No asyncio.sleep guessing” docstring: `asyncio.sleep` appears at lines 118 and 475.

### P1-4: success-result verification is incomplete

R313 checks that the output path exists and validates module/instance/port count, but does not compute and compare `system_top_sha256`, despite the report claiming the SHA was verified (`test_r3_1c_public.py:343-347`). It also does not prove that large `output` or `ports` payloads are absent from the persisted compact result.

### P1-5: success-stage mapping is duplicated and mutable

- `domain_runner.py:278-280` defines `_PL_SUCCESS_STAGE` as a normal mutable dict.
- `dispatcher.py:53-55` defines a second normal mutable dict `_DOMAIN_NEXT_STAGE`.
- The dispatcher uses `_DOMAIN_NEXT_STAGE` at `dispatcher.py:409`; `_PL_SUCCESS_STAGE` is not the production source used there.

The docs describe one immutable internal mapping. Two mutable sources of truth can drift and do not satisfy that contract.

### P1-6: frozen-test governance was not mechanically closed

`test_r3_runner.py` is a frozen R3.0 file and was changed from 9 to 10. The product change makes a progressive capability baseline update reasonable, but the change needs an explicit frozen-test erratum, a renamed assertion/test, updated R1/R2 SDK expectations, and a new recorded SHA. The current half-update created the two full-regression failures.

## 6. What is implemented and appears structurally present

The audit does not conclude that all R3.1-C production code is unusable. The following implementation pieces are present:

- `pl_generate_system_top` is registered with a wrapper-path-only public schema.
- `DOMAIN_APIS_IMPLEMENTED` is 1 and `total_tools` is 10.
- Dispatcher routing exists and uses the process-scoped `CommandRunner`.
- The local handler calls the frozen R3.1-B generator and maps the four component exception classes.
- The result stored by the local handler is compact and includes `system_top_sha256`.
- The input revision is selected from `platform_revision` inside admission.
- Stage advancement to `PL_BUILD` is passed internally, not exposed in the MCP tool schema.
- This local command does not require a Vivado worker.

These facts are code-inspection findings. They are not substitutes for the missing real MCP SDK tests.

## 7. Required closure evidence before R3.1-C can freeze

The next reviewer should require all of the following, without implementing fixes unless separately authorized:

1. Four real public tests using MCP SDK `stdio_client` + `ClientSession.call_tool()` against `python -m mcps.zynq_mcp.server`:
   - R313 accepted -> wait -> SUCCEEDED -> PL_BUILD, output SHA verified.
   - R321 accepted -> wait -> FAILED with exact `PLATFORM_MANIFEST_NOT_FOUND`.
   - R3S13 direct admission rejection with exact `STAGE_PREREQUISITE_UNMET` and no operation created.
   - R3S14 real `list_tools()` returns exactly 10 and only one PL tool.
2. Immutable snapshot behavior, not merely same-transaction capture.
3. Two independently admitted operations with different `platform_revision` values and no false dedup.
4. Exact runner-level tests for `ManifestBindingError`, `WrapperParseError`, `PathSafetyError`, and `AtomicWriteError`.
5. Deterministic unknown-exception assertion: exactly `OUTCOME_UNKNOWN`.
6. A concurrency test whose admitted operation is explicitly released, awaited to terminal, and leaves zero registered tasks.
7. One immutable success-stage source of truth.
8. Controlled frozen-test erratum for all capability-count assertions and test names.
9. Root full regression: zero failures, with exact collected/passed/skipped arithmetic.
10. A mechanically checked report whose claims match the code and test layer actually used.

## 8. Scope and safety constraints

- Do not enter R3.2 or implement any of the remaining 11 PL APIs.
- Do not call Agent2 while R3.1-C has no green white-box gate.
- Do not modify R3.1-A or R3.1-B frozen assets.
- Do not modify `.mcp.json` or `Xilinx_Vivado_MCP/`.
- Do not kill unrelated existing Vivado MCP processes.
- Do not mark R3.1-C frozen based only on the 15 submitted tests passing.
- This handoff is for a new reviewing Codex, not a repair prompt for Agent1.

## 9. Review handoff status

This audit is complete enough to transfer to a fresh Codex reviewer. The next reviewer must independently reproduce the evidence and review any later repair; it must not inherit the implementation report's conclusions as facts.
