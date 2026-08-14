# B04 R3 — PL Domain API Test Plan v0.3.3.1

> Brick: B04 R3 | Date: 2026-08-07 | Status: **R3.0 COMPLETE / FROZEN (36 tests) | R3.1 docs-only v0.3.3.1 | R3.1 tests NOT started**
> Depends: B04 R2 (FROZEN: 35 tests)
> Previous: v0.3.3 (superseded)

## v0.3.3.1 Changes

| # | Change |
|---|--------|
| 1 | R315 assertion: exact wrapper module type + instance name (e.g. `design_1_wrapper design_1_wrapper_i`), not internal BD module |
| 2 | R3S12a/b/c precise assertions for atomic commit, write-failure, illegal stage |
| 3 | E005 test: single `board_profile_load()` call per `create_session` |
| 4 | manifest `validate_manifest` temp-copy rules and outside-cwd test |
| 5 | R3S15 ANSI secondary format test |
| 6 | R3S16–R3S22 manifest path safety and next_stage non-injectable tests |
| 7 | Mechanical count: 9 formal + 24 supplemental = 33, all unique IDs |

---

## R3.0 Delivered Tests (unchanged, 36 tests)

R301–R312 (12) + R3X01–R3X23 (24). Baseline: `zynq_mcp/tests/` 160 collected; `mcps/` 602 collected.

---

## 1. Layer Definitions

| Layer | Production Entry | Verifies |
|-------|-----------------|----------|
| **Public** | `ClientSession.call_tool("pl_generate_system_top", ...)` → dispatcher → CommandRunner → async lifecycle → Ledger terminal | Full MCP chain: routing, admission, preflight, execution, stage advance, persistence |
| **Component** | `generate_system_top(wrapper_abs_path)` direct call | Parsing, byte-identical output, error handling — no dispatcher/Ledger/mutex |
| **Contract** | `CommandRunner.run_command(executor="local", local_fn=gen_fn)` | Manifest binding, path containment, SHA validation, stage gating, error codes, atomic commit |

---

## 2. Formal Tests (R313–R321, 9 tests)

### 2.1 Public (2 tests)

| ID | Scenario | Pre-condition | Key Assertion |
|----|----------|--------------|---------------|
| R313 | Real Vivado 2023.1 non-ANSI BD wrapper → full chain success | Ledger: PL_GENERATE, Platform Manifest valid, bd_wrapper on disk | `call_tool` → accepted → `wait_operation` → SUCCEEDED, stage=PL_BUILD, lane=IDLE, `system_top.v` at `{project_path}/rtl/system_top.v` |
| R321 | `platform_revision` absent → fail | Ledger: PL_GENERATE, platform_revision=None | `call_tool` → accepted → `wait_operation` → FAILED, reason=`PLATFORM_MANIFEST_NOT_FOUND`, stage=PL_GENERATE |

### R313 Lifecycle

```
1. call_tool("pl_generate_system_top", {"wrapper_path": "hdl/wrapper.v"})
   → {"operation_id": "...", "status": "accepted"}

2. wait_operation(operation_id, timeout_s=30)
   → {"status": "SUCCEEDED",
      "completion_evidence": {"stage_advanced_from":"PL_GENERATE",
                              "stage_advanced_to":"PL_BUILD"}}

3. get_execution_state({})
   → {"execution_lane": "IDLE", "current_stage": "PL_BUILD"}

4. get_operation_status({"operation_id": operation_id})
   → {"status": "SUCCEEDED", ...}  ← persisted in Ledger
```

### R321 Lifecycle

```
1. call_tool("pl_generate_system_top", {"wrapper_path": "hdl/wrapper.v"})
   → {"operation_id": "...", "status": "accepted"}

2. wait_operation(operation_id, timeout_s=30)
   → {"status": "FAILED",
      "error": {"details": {"reason_code": "PLATFORM_MANIFEST_NOT_FOUND"}}}

3. get_execution_state({})
   → {"current_stage": "PL_GENERATE"}  ← unchanged
```

### R3S13 Lifecycle (admission rejection)

```
call_tool("pl_generate_system_top", {"wrapper_path": "hdl/wrapper.v"})
  → {"status": "error",
     "error": {"code": "LOCK_BUSY",
               "details": {"reason_code": "STAGE_PREREQUISITE_UNMET"}}}
# No operation_id, no background task, lane = IDLE
```

### 2.2 Component (6 tests)

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R314 | Same non-ANSI wrapper twice in isolated temp dirs → byte-identical | `sha256(out1) == sha256(out2)` |
| R315 | Instance by wrapper module name | Output contains `design_1_wrapper design_1_wrapper_i (` — NOT internal `design_1 design_1_i`. Match must be on the exact full string, not substring |
| R316 | Port directions (input/output/inout) preserved | All direction keywords in output match wrapper declarations |
| R317 | Bus widths preserved | `[14:0]`, `[3:0]` annotations present in output |
| R318 | Escaped identifier `\escaped_id ` (single `\`, whitespace terminated) | Identifier preserved; no closing `\` |
| R319 | Missing `endmodule` → fail-closed | `PARSE_ERROR` / `UNCLOSED_MODULE` |

### 2.3 Contract (1 test)

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R320 | Platform Manifest single match via revision path → SUCCEEDED | `OP_SUCCEEDED`, lane=IDLE |

---

## 3. Supplemental Tests (R3S01–R3S22, 24 tests)

### 3.1 Caller Argument Validation (Contract — 5 tests)

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S01 | `wrapper_path` is int/None/dict | `INVALID_ARGUMENT` |
| R3S02 | `wrapper_path` is `""` | `INVALID_ARGUMENT` |
| R3S03 | `wrapper_path` is `/abs/path.v` | `PATH_ABSOLUTE` |
| R3S04 | `wrapper_path` is `C:rtl/wrapper.v` (drive-relative) | `PATH_DRIVE_RELATIVE` |
| R3S05 | `wrapper_path` contains `..` escaping outside project | `PATH_ESCAPE` |

### 3.2 Manifest Cross-Reference (Contract — 6 tests)

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S06 | `wrapper_path` resolves to different file than manifest `bd_wrapper_path` | `BD_WRAPPER_PATH_MISMATCH` |
| R3S07 | bd_wrapper SHA on disk ≠ manifest `bd_wrapper_sha256` | `BD_WRAPPER_SHA_MISMATCH` |
| R3S08 | manifest `board_profile_sha256` ≠ Ledger `board_profile_sha256` | `BOARD_PROFILE_MISMATCH` |
| R3S09 | manifest `platform_revision` ≠ Ledger `platform_revision` | `PLATFORM_REVISION_MISMATCH` |
| R3S10 | manifest `bd_wrapper_path` is `""` | `MANIFEST_INCOMPLETE` |
| R3S11 | manifest `bd_wrapper_sha256` is `"not-a-sha"` | `MANIFEST_INCOMPLETE` |

### 3.3 Atomic Stage Commit (Contract — 3 tests)

| ID | Scenario | Pre-condition / Method | Key Assertion |
|----|----------|----------------------|---------------|
| **R3S12a** | Success atomic proof | Normal success flow | Single `ledger_read_shared`: assert `previous_operation["status"] == "SUCCEEDED"` AND `context["current_stage"] == "PL_BUILD"` in same read result |
| **R3S12b** | Write-failure in `_atomic_write` (monkey-patch to raise after `json.dumps`, before `os.replace`) | Stage=PL_GENERATE, valid manifest | Original `execution_ledger.json` SHA256 unchanged; `ledger_read_shared` still returns valid original Ledger; if `.tmp` file remains after simulated failure, it is NOT parsed as the official Ledger; a subsequent successful `ledger_transaction` overwrites and removes `.tmp`; after that success, `.tmp` no longer exists; NO state SUCCEEDED+PL_GENERATE or RUNNING+PL_BUILD at any point |
| **R3S12c** | Illegal `next_stage="OBSERVATION"` passed to `op_transition` from PL_GENERATE | Direct `op_transition` call with invalid stage | `op_transition` returns `{"status":"error", "error":{"details":{"reason_code":"ILLEGAL_STAGE_TRANSITION"}}}`; `ledger_read_shared` confirms Ledger bytes semantically unchanged; `active_operation` status unchanged; `context.current_stage` = `PL_GENERATE` |

### 3.4 Public Lifecycle (Public — 2 tests)

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R3S13 | Stage not PL_GENERATE → direct admission rejection | `call_tool` returns `{status:"error", error:{code:"LOCK_BUSY", details:{reason_code:"STAGE_PREREQUISITE_UNMET"}}}`; lane=IDLE; no active_operation |
| R3S14 | `list_tools` after R3.1 | `len(tools)==10`; `pl_generate_system_top` is the only PL tool; 11 other PL names absent; no NOT_IMPLEMENTED stubs |

### 3.5 Component — ANSI Support (Component — 1 test)

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R3S15 | ANSI port format wrapper: `module name (input clk, output [7:0] data);` | All ports extracted with correct directions and widths; output identical to equivalent non-ANSI input |

### 3.6 Manifest Path Safety (Contract — 7 tests)

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S16 | Ledger `platform_revision` = `12345` (not sha256:... format) | `INVALID_PLATFORM_REVISION` |
| R3S17 | Ledger `platform_revision` = `"../etc/passwd"` | `INVALID_PLATFORM_REVISION` |
| R3S18 | Valid SHA but constructed path escapes `manifests/platform/` (symlink or filesystem trick) | `MANIFEST_PATH_ESCAPE` |
| R3S19 | Manifest's own `bd_wrapper_path` is absolute `/etc/wrapper.v` | `MANIFEST_PATH_ESCAPE` |
| R3S20 | Manifest's own `bd_wrapper_path` contains `..` | `MANIFEST_PATH_ESCAPE` |
| R3S21 | Manifest's own `bd_wrapper_path` resolves outside `project_path` | `MANIFEST_PATH_ESCAPE` |
| R3S22 | `next_stage` not present in `pl_generate_system_top` tool `inputSchema` | Static: schema check confirms no `next_stage` property in inputSchema |

### 3.7 E005 Single-Load Test

E005 is verified by enhancing the existing R1 test:

`test_r1_session.py::TestSession::test_create_returns_real_session_id`

The enhanced test: monkeypatch-spy `board_profile_load`; call `create_session`; assert `call_count == 1`; assert both `board_package_revision` and `board_profile_sha256` in Ledger context trace to same spy return value; assert `create_session` response includes `board_profile_sha256`.

This is an enhancement to an existing R1 test, not a new test function. R1 test count remains 89. R3.1 planned tests remain 33.

---

## 4. Mechanical Count

Enumerated unique non-overlapping IDs:

**Formal (9)**:
R313, R314, R315, R316, R317, R318, R319, R320, R321

**Supplemental (24)**:
R3S01, R3S02, R3S03, R3S04, R3S05, R3S06, R3S07, R3S08, R3S09, R3S10, R3S11 (11)
R3S12a, R3S12b, R3S12c (3)
R3S13, R3S14 (2)
R3S15 (1)
R3S16, R3S17, R3S18, R3S19, R3S20, R3S21, R3S22 (7)

**By layer**:

| Layer | Formal | Supplemental | Total |
|-------|--------|-------------|-------|
| Public | R313, R321 | R3S13, R3S14 | **4** |
| Component | R314–R319 | R3S15 | **7** |
| Contract | R320 | R3S01–R3S11, R3S12a/b/c, R3S16–R3S22 | **22** |
| **Total** | **9** | **24** | **33** |

---

## 5. Public Fixture Ledger Pre-population

Public tests (R313, R321, R3S13, R3S14) cannot reach PL_GENERATE through normal tools. Fixture method:

1. Create temp directories (project + runtime)
2. Use production `ledger_transaction()` to write `execution_ledger.json` with:
   - `context.current_stage = "PL_GENERATE"`
   - `context.platform_revision = <matches fixture manifest>`
   - `context.board_profile_sha256 = <matches fixture board profile>`
   - `context.board_package_revision`, `context.session_id`, etc.
   - `worker.state = "ABSENT"` (no EDA Worker needed)
   - `execution_lane = "IDLE"`, `active_operation = None`
3. Set `ZYNQ_RUNTIME_ROOT` env var to temp runtime dir
4. Launch MCP server
5. `start_reconcile` runs `_safe()` mutator — preserves context, stage, lane

Forbidden: test-only MCP handlers, JSON direct edit after server start, `set_stage_for_test` API.

---

## 6. Baselines (pre-R3.1)

| Baseline | Value |
|----------|-------|
| R3.0 zynq_mcp/tests/ collected | 160 |
| Full mcps/ collected | 602 |
| B02+B03 | 367 passed, 1 skipped (FROZEN) |

---

## 7. Declarations

- R3.1 tests NOT started — 0 test functions written
- R3.2+ NOT started
- Agent2 NOT called
- 33 unique non-overlapping test IDs, mechanically enumerated
- No skip/xfail/empty pass placeholders permitted
- Every test traces: requirement → production entry → exact assertion → raw result
