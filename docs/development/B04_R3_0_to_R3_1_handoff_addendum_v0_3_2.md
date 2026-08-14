# B04 R3.0 → R3.1 White-Box Handoff — v0.3.2 Contract Resolution Addendum

> Date: 2026-08-07 | Appends to: v0.3.1 handoff (2026-08-06)
> Status: **docs-only contract resolution | R3.1 NOT STARTED**

---

## Addendum Purpose

This addendum records the v0.3.2 docs-only contract resolution performed by Agent1 on 2026-08-07. It corrects and replaces the "Undecided" items in §9 of the original v0.3.1 handoff and resolves all contract gaps identified in the R3.1 readiness audit.

No production code, test code, or frozen R1/R2/R3.0 files were modified.

---

## A.1 Two Revision Types Resolved

### A.1.1 Definitions

The Board Configuration Package (`boards/ALINX_AX7020_v1.0/package_manifest.json`) defines two independent values:

| Name | Value | What It Is |
|------|-------|-----------|
| `board_profile_sha256` | `sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18` | SHA256 of `board_profile_ALINX_AX7020_v1.0.json` file |
| `board_package_revision` (`manifest_revision`) | `sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7` | `compute_revision(revision_inputs)` over all 5 package files |

### A.1.2 Which Is Used Where

| Comparison | Uses |
|-----------|------|
| Platform Manifest.`board_profile_sha256` == ? | Must equal Board Profile file SHA (`a7cb97...`), NOT `board_package_revision` |
| Ledger Context P8 drift check | Uses `board_package_revision` (`72191212...`) — unchanged |
| Artifact cross-reference in PL/PS Manifest | Uses `board_profile_sha256` (`a7cb97...`) — the immutable content hash |

### A.1.3 Ledger Context Extension

R3.1 requires adding `board_profile_sha256` to the Ledger Context dict (narrow-scope Erratum E005 to `session.py`). The value is obtained from `board_profile_load()` which already computes `profile["sha256"]` at `board_profile.py:192`.

| Field | Current | R3.1 |
|-------|---------|------|
| `board_package_revision` | `sha256:72191212...` | Unchanged |
| `expected_board_revision` | `sha256:72191212...` | Unchanged |
| `board_profile_sha256` | **Not present** | **NEW: `sha256:a7cb97...`** |

---

## A.2 Platform Manifest Binding Resolved

### A.2.1 Architecture Authority

Per frozen `architecture_ai_zynq7020.md` §6.2 (line 1264):

> Platform Manifest → `manifests/platform/<revision>.json`

### A.2.2 Final Binding Rule

```
manifest_path = f"{project_path}/manifests/platform/{_revision_to_filename(platform_revision)}"
```

Where `_revision_to_filename("sha256:<64hex>")` → `"sha256_<64hex>.json"` (from `artifact_schema.py:566-570`).

Binding uses `LedgerContext.platform_revision` as the sole truth — NOT a scan of all files in a directory.

| Case | Outcome |
|------|---------|
| `platform_revision` is None/empty in Ledger | `PLATFORM_MANIFEST_NOT_FOUND` |
| File missing at computed path | `PLATFORM_MANIFEST_NOT_FOUND` |
| File exists, `validate_manifest()` fails | `MANIFEST_VALIDATION_FAILED` |
| `manifest.platform_revision` ≠ `LedgerContext.platform_revision` | `PLATFORM_REVISION_MISMATCH` |
| `manifest.board_profile_sha256` ≠ `LedgerContext.board_profile_sha256` | `BOARD_PROFILE_MISMATCH` |
| `manifest.bd_wrapper_path` empty | `MANIFEST_INCOMPLETE` |
| `manifest.bd_wrapper_sha256` empty/invalid | `MANIFEST_INCOMPLETE` |
| All checks pass | Single match binds |

Ambigous matches do not occur with this design — the binding is by exact revision filename, not by content search.

### A.2.3 Test Fixture Isolation

Tests inject synthetic Platform Manifests by creating temp directories with `manifests/platform/<revision>.json`. Board profile fixtures are injected via the existing `ZYNQ_BOARD_PROFILE_DIRS` env var mechanism. No production paths are modified.

---

## A.3 wrapper_path Contract Resolved

### A.3.1 Final Resolution

B01 frozen signature is preserved exactly:
```
pl_generate_system_top(wrapper_path: str)
```

`project_path` comes from `LedgerContext.project_path` — injected by the dispatcher, NOT from tool arguments.

### A.3.2 Validation Sequence

1. Type check: must be `str`, non-empty
2. Normalize slashes
3. Reject: `os.path.isabs()` (absolute, UNC)
4. Reject: drive-relative `C:foo` (Windows)
5. Resolve: `os.path.realpath(os.path.join(project_path, normalized))`
6. Containment: `os.path.commonpath([real_project, resolved]) == real_project`
7. On Windows: `os.path.normcase(resolved).startswith(os.path.normcase(real_project))`

**String `startswith` is forbidden for path containment.** Only structured path methods.

### A.3.3 Manifest Cross-Validation

After path safety:
1. `resolved` must equal `os.path.realpath(os.path.join(project_path, manifest.bd_wrapper_path))`
2. `sha256_file(resolved)` must equal `manifest.bd_wrapper_sha256`

### A.3.4 Rejection Codes

| Condition | reason_code |
|-----------|-------------|
| Non-string / empty | `INVALID_ARGUMENT` |
| Absolute path | `PATH_ABSOLUTE` |
| Drive-relative `C:foo` | `PATH_DRIVE_RELATIVE` |
| Escape beyond project_path | `PATH_ESCAPE` |
| Path doesn't match Manifest | `BD_WRAPPER_PATH_MISMATCH` |
| SHA doesn't match Manifest | `BD_WRAPPER_SHA_MISMATCH` |
| File not found | `BD_WRAPPER_NOT_FOUND` |

---

## A.4 Workflow Stage Corrected

### A.4.1 Frozen Source

Per `B04_single_channel_audit.md` §4.3:

```
PLATFORM_DESIGN → PL_GENERATE    (Platform XSA + Manifest SUCCEEDED)
PL_GENERATE     → PL_BUILD        (pl_generate_system_top SUCCEEDED)
```

Test R124: "illegal skip (PLATFORM_DESIGN→PL_BUILD without PL_GENERATE) REJECTED"

### A.4.2 Corrected R3.1 Contract

| Field | v0.3.1 (wrong) | v0.3.2 (correct) |
|-------|---------------|-----------------|
| Pre-stage | "PLATFORM_DESIGN SUCCEEDED" | `PL_GENERATE` |
| Post-stage | "PL_BUILD" | `PL_BUILD` |
| FAILED | implicit | Stage = `PL_GENERATE`, Lane = `IDLE` |
| TIMED_OUT/OUTCOME_UNKNOWN | implicit | Stage = `PL_GENERATE`, Lane = `RECOVERY_REQUIRED` |

### A.4.3 execution_gate Erratum

`execution_gate.py:118-119` currently permits `PLATFORM_DESIGN` as a valid pre-stage for `pl_generate_system_top`. This must be narrowed to `PL_GENERATE` only (Erratum E003).

---

## A.5 Atomic Stage Advancement Design

### A.5.1 Problem

`operation_service.py` `op_transition(SUCCEEDED)` does not update `context.current_stage`. A second unprotected write would create a window where Operation=SUCCEEDED but stage is not advanced.

### A.5.2 Design (No Implementation)

Extend `op_transition` with optional `next_stage`:

- On `new_status == OP_SUCCEEDED` and `next_stage is not None`:
  - Validate `is_valid_forward(current_stage, next_stage)`
  - Set `context.current_stage = next_stage`
  - Record stage transition in `completion_evidence`
- All within the single `ledger_transaction` that writes SUCCEEDED

If the ledger write fails, neither OPERATION=SUCCEEDED nor stage advance takes effect.

### A.5.3 Prohibited

- local_fn modifying stage (no Ledger access)
- Second ledger_transaction after SUCCEEDED (non-atomic window)
- Catching LedgerWriteError and retrying (duplicate risk)

### A.5.4 Affected Frozen Files

| File | Change |
|------|--------|
| `operation_service.py:61-93` | Add optional `next_stage` parameter |
| `domain_runner.py:471-478` | `_terminal_success` passes `next_stage` |

---

## A.6 Errata Registry (R3.1 Scope)

| ID | File | Lines | Change |
|----|------|-------|--------|
| E003 | `execution_gate.py` | 118-119 | Pre-stage for `pl_generate_system_top`: `("PLATFORM_DESIGN", "PL_GENERATE")` → `"PL_GENERATE"` |
| E004 | `operation_service.py` + `domain_runner.py` | ~10 lines | Add `next_stage` to `op_transition`; wire from `_terminal_success` |
| E005 | `session.py` | 69-72 | Add `board_profile_sha256` field to Ledger context dict |

---

## A.7 Updated File Inventory

### A.7.1 R3.1 Target Files (CREATE)

```
mcps/zynq_mcp/domains/pl/system_top.py        — generator + parser + manifest binder
mcps/zynq_mcp/tests/test_r3_pl.py              — 25 test functions
```

### A.7.2 R3.1 Target Fixtures (CREATE)

```
mcps/zynq_mcp/tests/fixtures/b04_pl_ready/
  README.md, board_profile_fixture.json, platform_manifest.json,
  bd_wrapper.v, bd_wrapper_escaped.v, bd_wrapper_bus.v,
  bd_wrapper_malformed_1.v, bd_wrapper_malformed_2.v, bd_wrapper_malformed_3.v,
  bd_wrapper_tampered.v,
  platform_manifest_bad_bp.json, platform_manifest_bad_rev.json,
  platform_manifest_incomplete.json,
  system_top_expected.v
```

### A.7.3 Frozen Files Requiring Narrow Errata (MODIFY)

```
mcps/zynq_mcp/control/execution_gate.py        — E003 (1 line)
mcps/zynq_mcp/control/operation_service.py      — E004 (~8 lines)
mcps/zynq_mcp/control/domain_runner.py          — E004 (~3 lines)
mcps/zynq_mcp/control/session.py                — E005 (1 field)
mcps/zynq_mcp/control/capabilities.py           — Progressive (1 Tool + constants)
mcps/zynq_mcp/dispatcher.py                     — domain routing
mcps/zynq_mcp/server.py                         — mutex wiring
```

### A.7.4 Files NOT Modified

```
mcps/zynq_mcp/control/execution_ledger.py       — UNCHANGED
mcps/zynq_mcp/control/operation_registry.py     — UNCHANGED
mcps/zynq_mcp/control/instance_guard.py         — UNCHANGED
mcps/zynq_mcp/control/single_worker.py          — UNCHANGED
mcps/zynq_mcp/control/context.py                — UNCHANGED
mcps/zynq_mcp/adapters/vivado_adapter.py        — UNCHANGED
mcps/common/*                                   — UNCHANGED (all B02/B03 frozen)
boards/ALINX_AX7020_v1.0/*                      — UNCHANGED
.mcp.json                                       — UNCHANGED
Xilinx_Vivado_MCP/                              — UNCHANGED
```

---

## A.8 Verilog v0.1 Grammar Scope

### Supported (has test)
- Single module per file
- ANSI port declaration: `module name (ports);`
- `input`, `output`, `inout` scalar ports
- Packed bus width: `[N:0]`
- Escaped identifiers: `\name\ `
- `//` and `/* */` comments (ignored)
- Arbitrary whitespace/blanks (normalized)

### Explicitly Deferred (no test → no claim)
- Non-ANSI port declaration (`module name; input clk;`)
- Parameter list (`#(...)`)
- `wire`/`reg`/`assign`/`always`
- `generate` blocks
- SystemVerilog

---

## A.9 Mechanical Verification (read-only, 2026-08-07)

| Check | Result |
|-------|--------|
| R3.0 frozen SHA256 ×4 | ✅ All match |
| `.mcp.json` SHA256 | ✅ `f48fc9a82bad...` unchanged |
| `zynq_mcp/tests/` collected | 160 |
| `mcps/` full collected | 602 |
| R3.1 production files (system_top.py etc.) | 0 exist |
| `test_r3_pl.py` | 0 exist |
| `b04_pl_ready/` | Exists, empty |
| Agent2 called | No |
| Production code modified | No (docs only) |
| Test code modified | No |
| Frozen assets modified | No |

---

## A.10 Remaining BLOCKERS (all resolved by this addendum)

No architectural blockers remain. All issues resolved:
- ✅ project_path origin: Ledger Context
- ✅ Platform Manifest binding: `manifests/platform/<revision>.json`
- ✅ wrapper_path semantics: validated relative to project_path, cross-referenced with Manifest
- ✅ Workflow Stage: pre=PL_GENERATE, post=PL_BUILD
- ✅ Atomic stage advancement: design specified (E004)
- ✅ Two revision types: distinguished and mapped
- ✅ execution_gate Erratum: E003
- ✅ Verilog v0.1 scope: bounded and test-covered

Former implementation-only tasks (not blockers):
- Dispatcher routing for domain tools
- Server DomainExecutionMutex wiring
- Capabilities update
- Fixture population

## A.11 Declarations

- **R3.1 implementation = NOT started** — 0 production lines, 0 test lines
- **R3.2+ = NOT started**
- **Agent2 = NOT called**
- **This addendum is docs-only** — no code was modified
- **Awaiting review before R3.1 implementation**
