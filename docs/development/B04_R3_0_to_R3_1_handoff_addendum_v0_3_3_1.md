# B04 R3.0 → R3.1 — v0.3.3.1 Contract Resolution Addendum

> Date: 2026-08-07 | Status: **R3.1 NOT STARTED | docs-only, no code modified**
> Replaces: v0.3.3 (superseded — contaminated with internal reasoning, wrong field names, wrong instantiation, inconsistent counts)

---

## Purpose

This addendum records the v0.3.3.1 docs-only contract resolution. It supersedes all previous addenda. No production code, test code, or frozen R1/R2/R3.0 files were modified.

---

## 1. Real Vivado 2023.1 BD Wrapper Evidence

Three real BD wrappers in the repository, mechanically examined:

| File | SHA256 | Module | Port Count |
|------|--------|--------|-----------|
| `g11_pl_uart_build/.../ax7020_base_wrapper.v` | `994e9659eaa726b45...` | `ax7020_base_wrapper` | 21 |
| `g11_build/.../ax7020_base_wrapper.v` | `865a61d1d759b54bb...` | `ax7020_base_wrapper` | 21 |
| `g10_build/.../ax7020_base_wrapper.v` | `ed58cb95e231b5c0...` | `ax7020_base_wrapper` | 21 |

Consistent format across all three:

```
[header comments]
`timescale 1 ps / 1 ps
module ax7020_base_wrapper (port_list);
  inout [N:0]...;
  output [N:0]...;
  wire [N:0]...;
  ax7020_base ax7020_base_i (...);
endmodule
```

Features present: non-ANSI port format, `` `timescale ``, `wire` declarations, internal BD instance.
Features absent: ANSI ports, escaped identifiers, parameter lists, `input`.

---

## 2. Parser Contract — Single Consistent Table

| Feature | Status |
|---------|--------|
| Non-ANSI port format: `module name (ports);` then `input/output/inout [N:0] name;` | PRIMARY / REQUIRED |
| `input`, `output`, `inout` directions with optional packed bus `[N:0]` | REQUIRED |
| Scalar ports (no bus) | REQUIRED |
| `wire` / `tri` declarations | RECOGNIZED AND IGNORED |
| Internal module instance body | RECOGNIZED AND IGNORED |
| `` `timescale `` directive | RECOGNIZED AND IGNORED |
| `//` and `/* */` comments | RECOGNIZED AND IGNORED |
| ANSI port format: `module name (input clk, output [7:0] data);` | SECONDARY / TESTED (R3S15) |
| Escaped identifiers: `\name ` (single `\`, terminated by first whitespace) | REQUIRED (R318) |
| Parameter list `#(...)` | DEFERRED to R3.2+ |
| `reg`/`assign`/`always`/`generate`/SystemVerilog | DEFERRED to R3.2+ |

---

## 3. system_top Instantiation — Iron Rule

Input file declares: `module <wrapper_name> (...);`

system_top MUST instantiate the wrapper module:

```
<wrapper_name> <wrapper_name>_i (...);
```

The internal BD module instance inside the wrapper file (e.g., `ax7020_base ax7020_base_i`) is part of the wrapper file body. The generator ignores it.

Example with real file names:

- Wrapper file declares: `module ax7020_base_wrapper (DDR_addr, ..., led_pins);`
- Wrapper file internally instantiates: `ax7020_base ax7020_base_i (.DDR_addr(DDR_addr), ...);`
- system_top.v instantiates: `ax7020_base_wrapper ax7020_base_wrapper_i (.DDR_addr(DDR_addr), ..., .led_pins(led_pins));`

Fixture example:

- Wrapper file declares: `module design_1_wrapper (clk_in, reset_n, led_pins, data_in, data_out);`
- Wrapper file internally instantiates: `design_1 design_1_i (.clk_in(clk_in), ...);`
- system_top.v instantiates: `design_1_wrapper design_1_wrapper_i (.clk_in(clk_in), ...);`

R315 must assert the exact string `design_1_wrapper design_1_wrapper_i (` — not a substring match.

---

## 4. E005: board_profile_sha256 — Single-Load Design

### 4.1 Approach

`create_session_mutator` calls `board_profile_load(board_id)` exactly once. Both values come from the same validated profile:

```python
profile = board_profile_load(board_id)
board_package_revision = profile["package_revision"]
board_profile_sha256 = profile["sha256"]
```

The existing `load_board_package_revision()` helper is retained for other callers (e.g., `verify_board_revision`). But `create_session_mutator` performs its own authoritative `board_profile_load()` — both `package_revision` and `sha256` extracted from the same profile object. B02 `create_session` makes an independent compatibility validation load (1 authoritative + 1 B02 compatibility = 2 total calls).

### 4.2 Keys on profile dict

`board_profile_load()` returns a dict with:
- `profile["sha256"]` — file SHA256 of `board_profile_<board_id>.json` (computed at `board_profile.py:192`)
- `profile["package_revision"]` — `manifest_revision` from `package_manifest.json` (set at `board_profile.py:218` or `236`)

The key `expected_package_revision` does not exist on the profile dict. It is only a parameter name in `board_profile_load(expected_package_revision=...)`.

### 4.3 Test

Spy `board_profile_load` → `create_session` → `call_count == 1` → both `board_package_revision` and `board_profile_sha256` in Ledger context trace to same spy return.

---

## 5. completion_evidence Atomic Rules

`active_operation["completion_evidence"]` at SUCCEEDED time:

| State | Action |
|-------|--------|
| `None` | Create `{"stage_advanced_from": current, "stage_advanced_to": next}` |
| `dict` | Deep-copy, set `"stage_advanced_from"` and `"stage_advanced_to"` (preserve existing keys) |
| Any other type | Fail transaction: `ChannelBusyError("COMPLETION_EVIDENCE_CORRUPT")` |

After R3.1 success:
```json
{"stage_advanced_from": "PL_GENERATE", "stage_advanced_to": "PL_BUILD"}
```

Evidence, SUCCEEDED status, and stage advance are in one `ledger_transaction`.

---

## 6. op_transition Error Contract

`op_transition` catches `ChannelBusyError` from its internal mutator and returns a ToolResponse-style error dict:

```python
{"status": "error", "error": {
    "code": "LOCK_BUSY",
    "message": "ILLEGAL_STAGE_TRANSITION",
    "details": {"reason_code": "ILLEGAL_STAGE_TRANSITION"}
}}
```

The error does NOT propagate as a Python exception — `CallerRunner._terminal_success` calls `_check_trans` which returns False, triggering OUTCOME_UNKNOWN fallback. But for R3S12c, the direct `op_transition` call returns this error dict directly. The caller must check `result["status"] == "error"`.

**Contract for R3S12c**: Call `op_transition(guard, lp, op_id, OP_SUCCEEDED, next_stage="OBSERVATION")` directly. Assert return is `{"status": "error", ...}` with `reason_code == "ILLEGAL_STAGE_TRANSITION"`. Then `ledger_read_shared` confirms `current_stage` still `PL_GENERATE`, `active_operation` status unchanged.

---

## 7. R3S12b Atomic Failure — Precise Assertions

Monkey-patch `execution_ledger._atomic_write` to raise `OSError` after `json.dumps` and before `os.replace`.

Verify:

1. Original `execution_ledger.json` SHA256 unchanged; `ledger_read_shared` returns valid original Ledger
2. If `.tmp` file remains after simulated failure, it is NOT parsed as the official Ledger by any reader
3. `current_stage == "PL_GENERATE"` — stage not advanced
4. `active_operation` is not `SUCCEEDED`
5. A subsequent successful `ledger_transaction` completes normally and removes any `.tmp`
6. After that subsequent success, `.tmp` no longer exists
7. NO state: `SUCCEEDED + PL_GENERATE` at any point
8. NO state: `RUNNING + PL_BUILD` at any point

---

## 8. Manifest validate_manifest Temp-Copy Rules

B02 `artifact_schema.py` is frozen. R3.1 binder resolves paths before calling `validate_manifest`:

1. Deep-copy manifest dict (original never modified, never written to disk)
2. For each file-path key (`bd_wrapper_path`, `xsa_path`):
   - Validate: not absolute, not UNC, not drive-relative, no `..`
   - `resolved = os.path.realpath(os.path.join(project_path, rel))`
   - `os.path.commonpath([real_project, resolved]) == real_project`
   - `ValueError` from `commonpath` (cross-drive) → `MANIFEST_PATH_ESCAPE`
   - Replace in temp copy with resolved absolute path
3. Call `validate_manifest(temp_copy, "platform")`
4. Discard temp copy

The manifest JSON file on disk is never modified. No `chdir()`. No dependency on server cwd.

---

## 9. Test Count — Mechanical Enumeration

**Formal: 9**
R313, R314, R315, R316, R317, R318, R319, R320, R321

**Supplemental: 24**
R3S01, R3S02, R3S03, R3S04, R3S05, R3S06, R3S07, R3S08, R3S09, R3S10, R3S11 (11)
R3S12a, R3S12b, R3S12c (3)
R3S13, R3S14 (2)
R3S15 (1)
R3S16, R3S17, R3S18, R3S19, R3S20, R3S21, R3S22 (7)

**Grand total: 33**

| Layer | Count |
|-------|-------|
| Public | R313, R321, R3S13, R3S14 = 4 |
| Component | R314, R315, R316, R317, R318, R319, R3S15 = 7 |
| Contract | R320, R3S01–R3S11, R3S12a/b/c, R3S16–R3S22 = 22 |
| **Total** | **33** |

---

## 10. Remaining BLOCKERS

None.

---

## 11. Documentation Hygiene

This addendum contains no internal reasoning, no drafting artifacts, and no intermediate conclusions that conflict with final conclusions. All figures are mechanically verified and internally consistent across implementation plan, test plan, and addendum.

---

## 12. Declarations

- R3.1 implementation NOT started — 0 production lines, 0 test lines
- R3.2+ NOT started
- Agent2 NOT called
- No production code, test code, or frozen asset modified
- All R3.0 frozen SHA256 verified unchanged
- Awaiting review before R3.1 implementation
