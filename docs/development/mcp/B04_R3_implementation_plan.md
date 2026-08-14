# B04 R3 — 12 Frozen PL Domain API Implementation Plan v0.3.3.1

> Brick: B04 R3 | Date: 2026-08-07 | Status: **R3.0 COMPLETE / FROZEN | R3.1 docs-only v0.3.3.1 | R3.1 implementation NOT started**
> Depends: B04 R2 (FROZEN: 35 tests)
> Previous: v0.3.3 (superseded — contaminated addendum, wrong instantiation, wrong E005 field name, inconsistent counts)

## v0.3.3.1 Changes

| # | Change | Reason |
|---|--------|--------|
| 1 | system_top instantiates **wrapper module** not internal BD module | Real Vivado wrapper: `module ax7020_base_wrapper` → system_top: `ax7020_base_wrapper ax7020_base_wrapper_i`. Internal `ax7020_base ax7020_base_i` belongs to wrapper file body — generator ignores it |
| 2 | E005: single `board_profile_load()` → `profile["package_revision"]` + `profile["sha256"]` | `expected_package_revision` does not exist on profile dict; `package_revision` is the correct key |
| 3 | Parser table: one consistent table — non-ANSI=PRIMARY, ANSI=SECONDARY, body=IGNORED | Removed "non-ANSI old-style deferred" contradiction |
| 4 | completion_evidence: handle None → new dict; record both `stage_advanced_from` and `stage_advanced_to` | Must not overwrite existing evidence; must record full transition |
| 5 | op_transition error contract: returns `{status:"error", reason_code:"ILLEGAL_STAGE_TRANSITION"}` | ChannelBusyError is caught internally; R3S12c asserts ToolResponse error |
| 6 | R3S12b: verify original file unchanged, .tmp handled, no half-state | Previous only checked post-success state |
| 7 | Test counts: 9 formal + 24 supplemental = 33 | Unique non-overlapping IDs, mechanically enumerated |
| 8 | Addendum v0.3.3.1: full rewrite, zero internal reasoning | v0.3.3 was contaminated with ~160 lines of DSML/self-talk |

---

## R3.0 Freeze Baseline (unchanged)

| Item | Value |
|------|-------|
| R3.0 tests | 36 passed, 0 warnings |
| zynq_mcp/tests/ collected | 160 |
| mcps full regression | 602 collected |
| `list_tools` | 9 (0 PL handlers) |
| PL handlers implemented | 0 |

### R3.0 Frozen File SHA256 (unchanged)

| File | SHA256 |
|------|--------|
| `domain_runner.py` | `5fffcf23ac45d0b5c048c726c3441cdae96161750e7c861e7c6dfe386bbebe43` |
| `operation_service.py` | `30630bd679dc15e256266e428410f9f7a673b68995f7bf265d8ce09d381e4560` |
| `operation_registry.py` | `57a9375f6f043be7eed4a8fe7728096745de4e0f6c770f46a57bd974a9499b4c` |
| `test_r3_runner.py` | `32ae422309f28f0c21fa8bfff7f47b1c4f61ab930d76ea699d550821fbc82cc3` |

---

## 1. Two Revision Types

Board Configuration Package (`boards/ALINX_AX7020_v1.0/package_manifest.json`) defines two independent values:

| Name | Value | Source |
|------|-------|--------|
| `board_profile_sha256` | `sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18` | SHA256 of `board_profile_ALINX_AX7020_v1.0.json` |
| `board_package_revision` | `sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7` | `compute_revision(revision_inputs)` over 5 package files |

Platform Manifest.`board_profile_sha256` must equal `board_profile_sha256` (file SHA), NOT `board_package_revision`.

## 2. Platform Manifest Binding

Binding by revision path, not directory scan:

```
path = {project_path}/manifests/platform/{_revision_to_filename(platform_revision)}
```

`platform_revision` from `LedgerContext.platform_revision` is the sole truth.

Path safety: validate `platform_revision` matches `^sha256:[0-9a-f]{64}$` before constructing path. After construction, verify resolved path is within `{project_path}/manifests/platform/` via `os.path.commonpath`.

## 3. Manifest validate_manifest Resolution

B02 `artifact_schema.py` is frozen — no modification. R3.1 binder resolves manifest file paths to absolute before calling `validate_manifest`:

1. Deep-copy manifest dict (original never modified)
2. For each file-path key (`xsa_path`, `bd_wrapper_path`):
   - Validate: not absolute, not UNC, not drive-relative, no `..`
   - Resolve: `os.path.realpath(os.path.join(project_path, rel))`
   - Containment: `os.path.commonpath([real_project, resolved]) == real_project`
   - On `ValueError` from `commonpath` (cross-drive) → `MANIFEST_PATH_ESCAPE`
   - Replace path in temp copy with resolved absolute path
3. Call `validate_manifest(temp_copy, "platform")`
4. Discard temp copy

No `chdir()`. No dependency on server cwd.

## 4. wrapper_path Contract

Public signature preserved: `pl_generate_system_top(wrapper_path: str)`.

`project_path` from `LedgerContext.project_path`.

| # | Check | Reject reason_code |
|---|-------|-------------------|
| 1 | `isinstance(wrapper_path, str)` and non-empty | `INVALID_ARGUMENT` |
| 2 | Normalize `\` to `/` | N/A |
| 3 | `os.path.isabs()` → reject | `PATH_ABSOLUTE` |
| 4 | Drive-relative `C:foo` → reject | `PATH_DRIVE_RELATIVE` |
| 5 | `resolved = os.path.realpath(os.path.join(project_path, normalized))` | N/A |
| 6 | `os.path.commonpath([real_project, resolved]) == real_project` | `PATH_ESCAPE` |
| 7 | Windows: `os.path.normcase(resolved).startswith(os.path.normcase(real_project))` | `PATH_ESCAPE` |

No `str.startswith` for containment.

## 5. Workflow Stage

Per `B04_single_channel_audit.md` §4.3 (FROZEN) and `context.py:15-17`:

| Stage | Constant |
|-------|----------|
| Pre-stage | `PL_GENERATE` exactly |
| Success post-stage | `PL_BUILD` |
| FAILED | Stage unchanged (`PL_GENERATE`), Lane → `IDLE` |
| TIMED_OUT/OUTCOME_UNKNOWN/INTERRUPTED | Stage unchanged, Lane → `RECOVERY_REQUIRED` |

Erratum E003: `execution_gate.py:118-119` restricts pre-stage to `"PL_GENERATE"` only.

## 6. Real Vivado 2023.1 BD Wrapper Analysis

Three real wrappers examined (SHA256 in addendum). All share identical format:

| Feature | Present |
|---------|---------|
| `` `timescale 1 ps / 1 ps `` | Yes |
| Non-ANSI port list: `module name (ports);` then `input/output/inout ...;` | Yes |
| `wire` declarations mirroring all ports | Yes |
| Internal BD module instance: `<bd_module> <bd_module>_i (...)` | Yes |
| `endmodule` | Yes |
| Escaped identifiers | No |
| Parameter list `#(...)` | No |
| ANSI port format | No |

### 6.1 system_top Instantiation — Contract

The wrapper file declares:

```verilog
module ax7020_base_wrapper (DDR_addr, ..., led_pins);
```

Internally it instantiates the BD module:

```verilog
ax7020_base ax7020_base_i (.DDR_addr(DDR_addr), ...);
```

system_top MUST instantiate the **wrapper module**, NOT the internal BD module:

```verilog
// system_top.v — CORRECT
ax7020_base_wrapper ax7020_base_wrapper_i
   (.DDR_addr(DDR_addr),
    ...
    .led_pins(led_pins));
```

The internal `ax7020_base ax7020_base_i` belongs to the wrapper file's body. The generator ignores it.

**Iron rule**: `wrapper_module_name` = the name after `module` in the input file. `instance_name` = `wrapper_module_name + "_i"`.

R315 must assert the exact module type and instance name, not a fuzzy substring.

### 6.2 Parser Support Table (Single Consistent Table)

| Feature | Status | Notes |
|---------|--------|-------|
| Non-ANSI: `module name (ports);` then direction declarations | **PRIMARY / REQUIRED** | Real Vivado 2023.1 format |
| `input`/`output`/`inout` directions with optional `[N:0]` | **REQUIRED** | All three directions |
| Scalar ports (no bus) | **REQUIRED** | |
| `` `timescale `` directive | **RECOGNIZED AND IGNORED** | Stripped before parsing |
| `//` and `/* */` comments | **RECOGNIZED AND IGNORED** | Stripped before parsing |
| `wire`/`tri` declarations | **RECOGNIZED AND IGNORED** | Skipped — not part of module interface |
| Internal module instance body | **RECOGNIZED AND IGNORED** | Everything after wire declarations is skipped |
| ANSI ports: `module name (input clk, output [7:0] data);` | **SECONDARY / TESTED** | R3S15 covers this |
| Escaped identifiers: `\name ` (single `\`, whitespace-terminated) | **REQUIRED** | Per Verilog IEEE 1364-2001 §2.7.1 |
| Parameter list `#(...)` | **DEFERRED to R3.2+** | Not present in Vivado BD wrappers |
| `reg`/`assign`/`always` behavioral body | **DEFERRED to R3.2+** | Not needed for BD wrapper parsing |
| `generate` blocks | **DEFERRED to R3.2+** | Not present in Vivado BD wrappers |
| SystemVerilog constructs | **DEFERRED to R3.2+** | |

### 6.3 Parse Algorithm

```
1. Strip `timescale and other backtick directives
2. Strip // and /* */ comments
3. Locate `module <name>` — extract wrapper_module_name
4. Locate port list between `(` and `);`
5. For each port direction declaration: `(input|output|inout)\s+(\[N:0\])?\s+(\w+|\\.*?\s);`
   - Extract direction, width (default 1), name
   - Handle escaped identifiers: `\name ` → name="name"
6. Stop extracting ports at first `wire`/`tri`/`assign`/instance keyword after direction block
7. For `wire`/`tri` lines: skip (not part of interface)
8. Remaining body (internal instance, etc.): skip (only interface is extracted)
9. Verify `endmodule` exists
```

### 6.4 Malformed Wrapper Fail-Closed

| Input | reason_code |
|-------|-------------|
| Empty file | `PARSE_ERROR` / `EMPTY_FILE` |
| No `module` keyword | `PARSE_ERROR` / `NO_MODULE` |
| No module name | `PARSE_ERROR` / `NO_MODULE_NAME` |
| No port list `(...)` | `PARSE_ERROR` / `NO_PORT_LIST` |
| No `);` closing port list | `PARSE_ERROR` / `UNCLOSED_PORT_LIST` |
| No port direction declarations after `);` | `PARSE_ERROR` / `NO_PORT_DIRECTIONS` |
| Unrecognized direction keyword | `PARSE_ERROR` / `UNKNOWN_DIRECTION` |
| Bus range malformed | `PARSE_ERROR` / `MALFORMED_BUS` |
| Port in list has no matching direction declaration | `PARSE_ERROR` / `UNDECLARED_PORT` |
| No `endmodule` | `PARSE_ERROR` / `UNCLOSED_MODULE` |
| Multiple `module` keywords | `PARSE_ERROR` / `MULTIPLE_MODULES` |
| Duplicate port name in list | `PARSE_ERROR` / `DUPLICATE_PORT` |

---

## 7. next_stage Complete Internal Chain

### 7.1 Immutable Contract Table

```python
# domain_runner.py — ADD after PL_API_CONTRACTS
_PL_SUCCESS_STAGE: dict[str, Optional[str]] = {
    "pl_generate_system_top": "PL_BUILD",
    "pl_create_project": None,
    "pl_set_top": None,
    "pl_synthesize": None,
    "pl_place_and_route": None,
    "pl_analyze_timing": None,
    "pl_generate_bitstream": None,
    "pl_connect_hw_server": None,
    "pl_open_hw_target": None,
    "pl_program": None,
    "pl_select_device": None,
    "pl_get_device_status": None,
}
```

Only `pl_generate_system_top` has a non-None success stage.

### 7.2 Pass-Through Chain

```
Dispatcher reads _PL_SUCCESS_STAGE[tool_name] → "PL_BUILD"
  → CommandRunner.run_command(..., next_stage="PL_BUILD")
    → _execute(..., next_stage="PL_BUILD")
      → _terminal_success(op_id, result, next_stage="PL_BUILD")
        → op_transition(op_id, OP_SUCCEEDED, next_stage="PL_BUILD")
```

All in one `ledger_transaction`.

### 7.3 op_transition Mutator Logic

```python
if new_status == OP_SUCCEEDED and next_stage is not None:
    current_stage = ledger.context.get("current_stage", "")
    if not is_valid_forward(current_stage, next_stage):
        raise ChannelBusyError("ILLEGAL_STAGE_TRANSITION")
    ledger.context["current_stage"] = next_stage
    # completion_evidence handling (see §7.4)
```

### 7.4 completion_evidence Atomic Rules

`active_operation["completion_evidence"]` may be `None`, a `dict`, or (in malformed ledger) another type.

| State | Action |
|-------|--------|
| `None` | Create new dict: `{"stage_advanced_from": cur, "stage_advanced_to": next}` |
| `dict` | Deep-copy, then merge: `copy["stage_advanced_from"] = cur`, `copy["stage_advanced_to"] = next` |
| Any other type | `raise ChannelBusyError("COMPLETION_EVIDENCE_CORRUPT")` → transaction rejected |

Existing completion evidence keys are preserved (e.g., a future `system_top_sha256` added by local_fn).

Example evidence after R3.1 success:

```json
{
  "stage_advanced_from": "PL_GENERATE",
  "stage_advanced_to": "PL_BUILD"
}
```

Evidence, SUCCEEDED status, and stage advance are in the **same single `ledger_transaction`**.

### 7.5 op_transition Error Response Contract

When `next_stage` is invalid, `ChannelBusyError("ILLEGAL_STAGE_TRANSITION")` is raised inside the mutator. `op_transition` catches it and returns:

```python
{"status": "error", "error": {
    "code": "LOCK_BUSY",
    "message": "ILLEGAL_STAGE_TRANSITION",
    "details": {"reason_code": "ILLEGAL_STAGE_TRANSITION"}
}}
```

The `ledger_transaction` did not succeed — Ledger bytes are unchanged.

### 7.6 Safety Constraints

| Rule | Enforcement |
|------|-------------|
| next_stage not in tool args | Derived from `_PL_SUCCESS_STAGE` at dispatch time; not in MCP tool `inputSchema` |
| Invalid next_stage → entire transaction fails | `is_valid_forward()` returns False → `ChannelBusyError` in mutator → `op_transition` returns error dict |
| SUCCEEDED + stage advance atomic | Single `ledger_transaction` |
| No second write to fix stage | Design forbids it |
| completion_evidence corruption → transaction fails | Type check on `ao["completion_evidence"]` |

---

## 8. E005: `board_profile_sha256` — Design

### 8.1 Single-Load Design

`create_session_mutator` calls `board_profile_load(board_id)` **exactly once**. Both values come from the same validated profile object:

```python
# session.py — create_session_mutator
profile = board_profile_load(board_id)
board_package_revision = profile["package_revision"]
board_profile_sha256 = profile["sha256"]
```

No second call. No TOCTOU.

The existing helper `load_board_package_revision()` is retained for other callers (e.g., `verify_board_revision`). But `create_session_mutator` performs its own authoritative `board_profile_load()` — from which both `package_revision` and `sha256` are extracted. B02 `create_session` makes an independent compatibility validation load (1 authoritative + 1 B02 compatibility = 2 total loads).

### 8.2 Affected Files

| File | Change |
|------|--------|
| `session.py` | `create_session_mutator`: single `board_profile_load()`, extract both `package_revision` and `sha256` |
| `context.py` | Add `board_profile_sha256: str = ""` to `ZynqContext` dataclass |
| `context.py` | Add `"board_profile_sha256": self.board_profile_sha256` to `to_dict()` |
| `dispatcher.py` | Add `"board_profile_sha256"` to `_create_session` return |
| `operation_service.py` | Add `"board_profile_sha256"` to `op_admit_create_session` return |

### 8.3 Ledger Context Schema

```python
ledger.context = {
    "session_id": ...,
    "board_id": ...,
    "project_path": ...,
    "board_package_revision": "sha256:72191212...",     # unchanged
    "expected_board_revision": "sha256:72191212...",     # unchanged
    "board_profile_sha256": "sha256:a7cb97...",          # NEW
    "current_stage": "PL_GENERATE",
    "platform_revision": "sha256:...",
    "pl_revision": None,
    "ps_revision": None,
}
```

### 8.4 Test

- Spy/monkeypatch `board_profile_load` → `create_session` → assert `call_count == 1`
- Assert `board_package_revision` and `board_profile_sha256` in Ledger context come from same spy return value

---

## 9. Updated Workflow Stage Contract

| Field | Value |
|-------|-------|
| Pre-stage | `PL_GENERATE` exactly |
| Success post-stage | `PL_BUILD` (atomically with SUCCEEDED) |
| FAILED | Stage = `PL_GENERATE`, Lane = `IDLE` |
| TIMED_OUT/OUTCOME_UNKNOWN | Stage = `PL_GENERATE`, Lane = `RECOVERY_REQUIRED` |
| Stage pre-check | P7 in `_shared_preflight_check` → `STAGE_PREREQUISITE_UNMET` at admission (E003) |

---

## 10. Capability Progression

| Sub-step | R3.0 | R3.1 |
|----------|------|------|
| control tools | 9 | 9 |
| `pl_generate_system_top` | [0] | ✅ |
| 11 other PL APIs | [0] | [0] |
| **list_tools total** | **9** | **10** |
| **control implemented** | 9 | 9 |
| **PL implemented** | 0 | 1 |

---

## 11. Test Matrix

### 11.1 Formal Tests (R313–R321)

| ID | Layer | Scenario | Key Assertion |
|----|-------|----------|---------------|
| R313 | Public | Real Vivado non-ANSI wrapper → full chain | call_tool→accepted→wait_operation→SUCCEEDED, stage=PL_BUILD, system_top.v exists |
| R314 | Component | Same wrapper twice → byte-identical | sha256(out1)==sha256(out2) in isolated temp dirs |
| R315 | Component | Instance by **wrapper** module name | Output contains `ax7020_base_wrapper ax7020_base_wrapper_i (...)` exactly (not internal `ax7020_base ax7020_base_i`) |
| R316 | Component | Port directions preserved | input/output/inout match wrapper declarations |
| R317 | Component | Bus widths preserved | `[14:0]`, `[3:0]` annotations in output |
| R318 | Component | Escaped identifier `\name ` | Identifier preserved, whitespace terminator handled |
| R319 | Component | Missing endmodule → fail | `PARSE_ERROR` / `UNCLOSED_MODULE` |
| R320 | Contract | Platform Manifest single match → SUCCEEDED | OP_SUCCEEDED, lane=IDLE |
| R321 | Public | platform_revision absent → fail | call_tool→accepted→wait_operation→FAILED, reason=PLATFORM_MANIFEST_NOT_FOUND |

### 11.2 Supplemental Tests

**Caller argument validation (5 Contract)**:

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S01 | wrapper_path non-string | `INVALID_ARGUMENT` |
| R3S02 | wrapper_path empty | `INVALID_ARGUMENT` |
| R3S03 | wrapper_path absolute | `PATH_ABSOLUTE` |
| R3S04 | wrapper_path drive-relative | `PATH_DRIVE_RELATIVE` |
| R3S05 | wrapper_path `..` escape | `PATH_ESCAPE` |

**Manifest cross-reference (6 Contract)**:

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S06 | wrapper_path not matching manifest bd_wrapper_path | `BD_WRAPPER_PATH_MISMATCH` |
| R3S07 | bd_wrapper SHA on disk ≠ manifest | `BD_WRAPPER_SHA_MISMATCH` |
| R3S08 | manifest board_profile_sha256 ≠ ledger | `BOARD_PROFILE_MISMATCH` |
| R3S09 | manifest platform_revision ≠ ledger | `PLATFORM_REVISION_MISMATCH` |
| R3S10 | manifest bd_wrapper_path empty | `MANIFEST_INCOMPLETE` |
| R3S11 | manifest bd_wrapper_sha256 invalid | `MANIFEST_INCOMPLETE` |

**Atomic stage commit (3 Contract)**:

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R3S12a | Success atomic proof | Single `ledger_read_shared`: `po.status==SUCCEEDED` AND `ctx.current_stage==PL_BUILD` |
| R3S12b | Write-failure in `_atomic_write` | Original `execution_ledger.json` SHA256 unchanged; `ledger_read_shared` returns valid original Ledger; residual `.tmp` must NOT be parsed as official Ledger; subsequent successful `ledger_transaction` overwrites/cleans `.tmp`; after that success, `.tmp` no longer exists; NO state SUCCEEDED+PL_GENERATE or RUNNING+PL_BUILD |
| R3S12c | Illegal next_stage → rejected | `op_transition` returns `{status:"error", details:{reason_code:"ILLEGAL_STAGE_TRANSITION"}}`; Ledger bytes unchanged; active_operation status unchanged; `current_stage` still `PL_GENERATE` |

**Public lifecycle (2 Public)**:

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R3S13 | Stage not PL_GENERATE → admission rejection | `call_tool` returns direct error: LOCK_BUSY/STAGE_PREREQUISITE_UNMET; no operation created |
| R3S14 | list_tools=10, only pl_generate_system_top | Exactly 1 PL tool present; 11 absent; no NOT_IMPLEMENTED |

**Component (1 Component)**:

| ID | Scenario | Key Assertion |
|----|----------|---------------|
| R3S15 | ANSI port format wrapper → parse correctly | All ports extracted; same result as equivalent non-ANSI input |

**Manifest path safety (7 Contract)**:

| ID | Scenario | Error reason_code |
|----|----------|-------------------|
| R3S16 | Ledger platform_revision not `sha256:<hex>` | `INVALID_PLATFORM_REVISION` |
| R3S17 | Ledger platform_revision = `"../etc/passwd"` | `INVALID_PLATFORM_REVISION` |
| R3S18 | Constructed manifest path escapes `manifests/platform/` | `MANIFEST_PATH_ESCAPE` |
| R3S19 | Manifest's own bd_wrapper_path absolute | `MANIFEST_PATH_ESCAPE` |
| R3S20 | Manifest's own bd_wrapper_path contains `..` | `MANIFEST_PATH_ESCAPE` |
| R3S21 | Manifest's own bd_wrapper_path resolves outside project | `MANIFEST_PATH_ESCAPE` |
| R3S22 | next_stage not injectable via tool args | Static: inputSchema has no next_stage property |

### 11.3 Mechanical Count (Unique Non-Overlapping IDs)

Enumerated:
- Formal: R313, R314, R315, R316, R317, R318, R319, R320, R321 = **9**
- Supplemental: R3S01, R3S02, R3S03, R3S04, R3S05, R3S06, R3S07, R3S08, R3S09, R3S10, R3S11 = **11**
- Supplemental: R3S12a, R3S12b, R3S12c = **3**
- Supplemental: R3S13, R3S14 = **2**
- Supplemental: R3S15 = **1**
- Supplemental: R3S16, R3S17, R3S18, R3S19, R3S20, R3S21, R3S22 = **7**
- Supplemental total = 11 + 3 + 2 + 1 + 7 = **24**
- Grand total = 9 + 24 = **33**

| Layer | Formal | Supplemental | Total |
|-------|--------|-------------|-------|
| Public | 2 | 2 | **4** |
| Component | 6 | 1 | **7** |
| Contract | 1 | 21 | **22** |
| **Total** | **9** | **24** | **33** |

---

## 12. Fixture Design

```
mcps/zynq_mcp/tests/fixtures/b04_pl_ready/
├── README.md
├── board_profile_fixture.json
├── platform_manifest.json
├── bd_wrapper_realistic.v          # Faithful Vivado 2023.1 non-ANSI:
│                                   #   `timescale, header comments, non-ANSI ports,
│                                   #   input/output/inout declarations, wire declarations,
│                                   #   internal module instance, endmodule
│                                   #   Module name: "design_1_wrapper"
│                                   #   Internal BD: "design_1 design_1_i (...)" (IGNORED by generator)
├── bd_wrapper_realistic_tampered.v # Same filename, different content (R3S07)
├── bd_wrapper_escaped.v           # Non-ANSI with \escaped_port  (single \, whitespace terminated)
├── bd_wrapper_bus.v               # Explicit [7:0], [31:0] bus tests
├── bd_wrapper_ansi.v              # ANSI port format (R3S15)
├── bd_wrapper_malformed_no_end.v  # Missing endmodule (R319)
├── bd_wrapper_malformed_dup.v     # Duplicate port name
├── bd_wrapper_malformed_multi.v   # Two module definitions
├── platform_manifest_bad_bp.json
├── platform_manifest_bad_rev.json
├── platform_manifest_incomplete.json
├── platform_manifest_bad_path_abs.json
├── platform_manifest_bad_path_escape.json
├── system_top_expected.v          # Expected output: instantiates design_1_wrapper design_1_wrapper_i
└── .gitkeep
```

### Key Fixture Rule

`bd_wrapper_realistic.v` declares `module design_1_wrapper (...)` and internally instantiates `design_1 design_1_i (...)`.

`system_top_expected.v` instantiates `design_1_wrapper design_1_wrapper_i (...)` — the **wrapper** module, NOT the internal BD module.

---

## 13. Errata Registry

| ID | File | Scope | Change |
|----|------|-------|--------|
| E003 | `execution_gate.py:118-119` | 1 line | Pre-stage for `pl_generate_system_top`: `("PLATFORM_DESIGN", "PL_GENERATE")` → `"PL_GENERATE"` |
| E004 | `operation_service.py:61-93` | ~12 lines | Add `next_stage` parameter; on SUCCEEDED + valid forward, atomically set `context.current_stage`; handle `completion_evidence` None/corrupt |
| E004 | `domain_runner.py:273-279,393-394,471-478` | ~6 lines | Pass `next_stage` through `run_command` → `_execute` → `_terminal_success` → `op_transition` |
| E005 | `context.py:49,76` | 2 lines | Add `board_profile_sha256` field |
| E005 | `session.py:56-72,83` | ~6 lines | Single `board_profile_load()`; extract `package_revision` + `sha256` |
| E005 | `dispatcher.py:123-127` | 1 line | Add to `_create_session` return |
| E005 | `operation_service.py:120-125` | 1 line | Add to `op_admit_create_session` return |

---

## 14. Remaining BLOCKERS

None.

---

## 15. Declarations

- R3.1 implementation NOT started — 0 production lines, 0 test lines
- R3.2+ NOT started
- Agent2 NOT called
- All R3.0 frozen SHA256 verified unchanged
- No frozen code modified (docs only)
- Awaiting review before R3.1 implementation
