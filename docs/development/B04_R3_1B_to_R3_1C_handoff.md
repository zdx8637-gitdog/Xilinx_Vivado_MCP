# B04 R3.1-B → R3.1-C White-Box Handoff

> Date: 2026-08-07 | Status: **R3.1-B ✅ COMPLETE / FROZEN | R3.1-C NOT STARTED**
> To: New context Agent1 (white-box implementer)

## 1. R3.1-B Frozen SHA256

| File | SHA256 | Lines |
|------|--------|-------|
| `mcps/zynq_mcp/domains/pl/system_top.py` | `7ffe07bc77578548ebd3af66a6df3b4ff4f72f316123ff5d26c99817e2d642c1` | 612 |
| `mcps/zynq_mcp/tests/test_r3_1b_pl.py` | `ff7529e9ea6c71c8a94c2891ee31a8f62bc80192136437283b80bd803dab1be9` | 709 |

Do NOT modify these files. If SHA256 differs → STOP, report conflict.

---

## 2. R3.1-A Frozen Assets

| File | SHA256 |
|------|--------|
| `mcps/zynq_mcp/control/execution_gate.py` | `cab3ee78f4af5595f3c3185743c11eee9573ef5c012bb3c17ce07a058fbf9faa` |
| `mcps/zynq_mcp/control/operation_service.py` | `1bf83ee53596b63bfb6acd91018ccb8151009f070760b572516e1b61973faf20` |
| `mcps/zynq_mcp/control/domain_runner.py` | `92708247768ce6aa92a33151b3062816ebf1d350318bff8d4a79579338856033` |
| `mcps/zynq_mcp/control/session.py` | `806f30fb30642b98f73ec0729454825255919c5d5144f7a2ce892f2bcc1fab53` |
| `mcps/zynq_mcp/control/context.py` | `d714eba80b1be4605a6a81ed6d38d6bf4a0fd73c3d19e2349c21e638e224ac92` |
| `mcps/zynq_mcp/dispatcher.py` | `bf29d6302e23abec08b4186a803b3a661fd1c4836434fe034f64150aa28ca0ae` |
| `mcps/zynq_mcp/tests/test_r3_1a_errata.py` | `23e5fae9051a64859776c1e7494eeb4bd8390fb6813a58ae0e9b1e16a157806b` |
| `mcps/zynq_mcp/tests/test_r1_session.py` | `6e58045f83c60d55e5acda7354df031abf31b7a673d88dc38023d71e1bb7f2c5` |

8 files. Do NOT modify.

---

## 3. R3.1-B Delivered Capabilities

### 3.1 Platform Manifest Binder (`_validate_and_bind_manifest`)
- Path: `{project_path}/manifests/platform/{_revision_to_filename(platform_revision)}`
- Cross-checks: `platform_revision`, `board_profile_sha256`, `bd_wrapper_path`, `bd_wrapper_sha256`
- `validate_manifest()` on deep copy (B02 `artifact_schema.py` unchanged)
- Priority-based issue mapping (UNSUPPORTED_SCHEMA → MISSING_FIELD → BAD_REVISION → …)
- Path safety: realpath/commonpath/normcase + junction/symlink detection

### 3.2 Verilog Wrapper Parser (`_parse_wrapper`)
- Primary: Vivado 2023.1 non-ANSI; Secondary: ANSI
- Port model: `{semantic_name, emitted_token, escaped, direction, width, msb, lsb}`
- Escaped: `\name ` → `semantic_name=name`, `emitted_token=\name ` (canonical)
- Instance name: `wrapper_module_name + "_i"` (wrapper module, NOT internal BD)

### 3.3 system_top Generator (`generate_system_top`)
- Output: `{project_path}/rtl/system_top.v` — atomic write (tmp→flush→fsync→replace)
- Junction on `rtl/` rejected; deterministic byte-identical
- Exceptions: `ManifestBindingError`, `WrapperParseError`, `PathSafetyError`, `AtomicWriteError`

---

## 4. R3.1-C Mandatory Contracts

### Contract A: Atomic Execution Context Snapshot

During `CommandRunner.run_command` admission `ledger_transaction`, the dispatcher MUST capture all context fields from the **same Ledger read** into an immutable snapshot:

```python
snapshot = {
    "session_id":     ledger.context["session_id"],
    "board_id":       ledger.context["board_id"],
    "project_path":   ledger.context["project_path"],
    "current_stage":  ledger.context["current_stage"],
    "platform_revision":  ledger.context.get("platform_revision", ""),
    "board_profile_sha256": ledger.context.get("board_profile_sha256", ""),
    "board_package_revision": ledger.context.get("board_package_revision", ""),
}
```

The `local_fn` called by `CommandRunner._execute` MUST receive only this snapshot — it must NOT read Ledger context fields from a `_admit` closure that captures variables outside the ledger_transaction. The snapshot is frozen inside the admission transaction.

**Forbidden**: reading `project_path`/`platform_revision`/`board_profile_sha256` from a dispatcher-level closure and passing them into `run_command` alongside arguments. All three values must originate from within the same `_admit` mutator that writes ACCEPTED.

### Contract B: Domain Input Revision

`pl_generate_system_top` uses `platform_revision` (not `board_package_revision`) as its input artifact revision:

```python
# Inside _admit mutator:
sig = request_signature(session_id, stage, tool_name, arguments, platform_revision)
# NOT: request_signature(session_id, stage, tool_name, arguments, board_package_revision)
```

And in the `active_operation` dict:
```python
"input_artifact_revision": platform_revision
```

**Test**: same `wrapper_path`, same `stage`, same `session_id`, same `board_package_revision`, but **different** `platform_revision` → `request_signature` differs → no false dedup. Both operations succeed independently.

### Contract C: Missing vs Invalid Revision

Two distinct error paths with distinct reason_codes:

| Ledger `platform_revision` | Error |
|---------------------------|-------|
| `None`, `""`, or key absent from context | `PLATFORM_MANIFEST_NOT_FOUND` |
| Non-empty but not `sha256:<64 lowercase hex>` | `INVALID_PLATFORM_REVISION` |

Both must have independent Public or Contract tests with precise reason_code assertions.

### Contract D: Deterministic Error Mapping

Component exceptions must be caught and mapped — NEVER allowed to propagate into `CommandRunner._execute`'s generic `except Exception` branch.

| Component Exception | Operation Status | Lane | reason_code |
|---------------------|-----------------|------|-------------|
| `ManifestBindingError` | FAILED | IDLE | `.reason_code` as-is |
| `WrapperParseError` | FAILED | IDLE | `.reason_code` as-is |
| `PathSafetyError` | FAILED | IDLE | `.reason_code` as-is |
| `AtomicWriteError` | FAILED | IDLE | `ATOMIC_WRITE_FAILED` |
| `asyncio.TimeoutError` | TIMED_OUT | RECOVERY_REQUIRED | `OP_TIMED_OUT` |
| `asyncio.CancelledError` | INTERRUPTED | RECOVERY_REQUIRED | `OP_INTERRUPTED` |
| Truly unknown exception | OUTCOME_UNKNOWN | RECOVERY_REQUIRED | `OP_OUTCOME_UNKNOWN` |

The `local_fn` passed to `CommandRunner.run_command(executor="local")` must catch all four component exception types and convert them to deterministic error ToolResponse dicts:

```python
async def _local_fn(snapshot):
    try:
        result = generate_system_top(...)
        return {"status": "success", "data": result}
    except ManifestBindingError as e:
        return {"status": "error", "error": {"code": "TOOL_ERROR",
            "message": str(e), "details": {"reason_code": e.reason_code}}}
    except WrapperParseError as e:
        return {"status": "error", "error": {"code": "TOOL_ERROR",
            "message": str(e), "details": {"reason_code": e.reason_code}}}
    except PathSafetyError as e:
        return {"status": "error", "error": {"code": "TOOL_ERROR",
            "message": str(e), "details": {"reason_code": e.reason_code}}}
    except AtomicWriteError as e:
        return {"status": "error", "error": {"code": "TOOL_ERROR",
            "message": str(e), "details": {"reason_code": "ATOMIC_WRITE_FAILED"}}}
```

---

## 5. R3.1-C Strict Scope

From `B04_R3_implementation_plan.md` v0.3.3.1 and `B04_R3_test_plan.md` v0.3.3.1:

**Allowed in R3.1-C**:
1. Register `pl_generate_system_top` in `capabilities.py` (`DOMAIN_TOOLS` += 1, `DOMAIN_APIS_IMPLEMENTED` = 1)
2. Route it in `dispatcher.py` (add to `_ALL_KNOWN`, create dispatch path using Contract A snapshot, Contract B revision, Contract C validation, Contract D error mapping)
3. Wire `DomainExecutionMutex` as process singleton in `server.py`
4. Implement `_local_fn` adapter per Contract D
5. Pre-populate Ledger to `PL_GENERATE` in Public test fixture
6. 4 Public tests: R313, R321, R3S13, R3S14

**Forbidden in R3.1-C**:
- Other 11 PL APIs
- Vivado/VivadoAdapter connection
- Modifying R3.1-A or R3.1-B frozen files
- R3.2+

`list_tools` must change from 9 → 10.

---

## 6. Test Baselines

| Suite | Collected | Passed | Skipped | Failed |
|-------|-----------|--------|---------|--------|
| R3.1-B (test_r3_1b_pl.py) | 50 | 50 | 0 | 0 |
| R3.1-A (test_r3_1a_errata.py) | 18 | 18 | 0 | 0 |
| R3.0 (test_r3_runner.py) | 36 | 36 | 0 | 0 |
| zynq_mcp/tests/ total | 228 | 228 | 0 | 0 |
| mcps/ total | 670 | 669 | 1 | 0 |

1 skip = `test_posix_link_no_overwrite` (B02 pre-existing, POSIX-only).

Regression: `python -m pytest mcps -q -W error::RuntimeWarning` → 669 passed, 1 skipped.

---

## 7. Current State

| Item | Value |
|------|-------|
| `list_tools` | 9 |
| `DOMAIN_TOOLS` | 0 |
| PL handlers (registered MCP tools) | 0 |
| `system_top.py` | EXISTS, internal only, not registered |
| `dispatcher.py` | NOT modified (no domain routing) |
| `server.py` | NOT modified |
| `capabilities.py` | NOT modified |
| `b04_pl_ready/` fixtures | 21 files populated |

---

## 8. R3.1-B Test Inventory (50 tests, actual pytest function names)

### Parser Component — 13 tests

| # | Full pytest function name |
|---|--------------------------|
| 1 | `TestParser::test_r314_deterministic_output` |
| 2 | `TestParser::test_r314b_two_isolated_projects` |
| 3 | `TestParser::test_r315_wrapper_module_instance` |
| 4 | `TestParser::test_r316_port_directions` |
| 5 | `TestParser::test_r317_bus_widths` |
| 6 | `TestParser::test_r318_escaped_identifiers` |
| 7 | `TestParser::test_r319_missing_endmodule` |
| 8 | `TestParser::test_r3b01_non_ansi_primary` |
| 9 | `TestParser::test_r3b02_multi_module_rejected` |
| 10 | `TestParser::test_r3b03_duplicate_port_rejected` |
| 11 | `TestParser::test_r3s15_ansi_format` |
| 12 | `TestParser::test_r3s15b_ansi_bus_widths` |
| 13 | `TestParser::test_r3b28_ansi_escaped_identifier` |

### Manifest Binder Contract — 19 tests

| # | Full pytest function name |
|---|--------------------------|
| 14 | `TestManifestBinding::test_r320_manifest_single_match` |
| 15 | `TestManifestBinding::test_r3b10_manifest_not_found` |
| 16 | `TestManifestBinding::test_r3b11_bad_schema` |
| 17 | `TestManifestBinding::test_r3b12_missing_field` |
| 18 | `TestManifestBinding::test_r3b13_inconsistent_revision` |
| 19 | `TestManifestBinding::test_r3b14_xsa_not_found` |
| 20 | `TestManifestBinding::test_r3b15_xsa_sha_mismatch` |
| 21 | `TestManifestBinding::test_r3b16_multi_issue_priority_deterministic` |
| 22 | `TestManifestBinding::test_r3s08_board_profile_mismatch` |
| 23 | `TestManifestBinding::test_r3s09_platform_revision_mismatch` |
| 24 | `TestManifestBinding::test_r3s10_bd_wrapper_path_empty` |
| 25 | `TestManifestBinding::test_r3s11_bd_wrapper_sha_invalid` |
| 26 | `TestManifestBinding::test_r3s16_invalid_revision` |
| 27 | `TestManifestBinding::test_r3s17_revision_path_injection` |
| 28 | `TestManifestBinding::test_r3s18_manifest_dir_junction_escape` |
| 29 | `TestManifestBinding::test_r3s18b_manifest_junction_same_project` |
| 30 | `TestManifestBinding::test_r3s19_manifest_bd_wrapper_absolute` |
| 31 | `TestManifestBinding::test_r3s20_manifest_bd_wrapper_dotdot` |
| 32 | `TestManifestBinding::test_r3s21_manifest_bd_wrapper_outside` |

### Caller Argument Validation — 7 tests

| # | Full pytest function name |
|---|--------------------------|
| 33 | `TestCallerArgValidation::test_r3s01_non_string` |
| 34 | `TestCallerArgValidation::test_r3s02_empty` |
| 35 | `TestCallerArgValidation::test_r3s03_absolute` |
| 36 | `TestCallerArgValidation::test_r3s04_drive_relative` |
| 37 | `TestCallerArgValidation::test_r3s05_dotdot_escape` |
| 38 | `TestCallerArgValidation::test_r3s06_wrapper_path_differs` |
| 39 | `TestCallerArgValidation::test_r3s07_sha_mismatch` |

### File Output — 11 tests

| # | Full pytest function name |
|---|--------------------------|
| 40 | `TestFileOutput::test_r3b20_output_matches_expected` |
| 41 | `TestFileOutput::test_r3b21_file_written_with_correct_sha` |
| 42 | `TestFileOutput::test_r3b22_output_within_project` |
| 43 | `TestFileOutput::test_r3b23_byte_identical` |
| 44 | `TestFileOutput::test_r3b24_verilog_structure` |
| 45 | `TestFileOutput::test_r3b25_rtl_dir_junction_escape` |
| 46 | `TestFileOutput::test_r3b25b_rtl_junction_same_project` |
| 47 | `TestFileOutput::test_r3b26_atomic_write_old_file_preserved` |
| 48 | `TestFileOutput::test_r3b26b_atomic_write_double_fault` |
| 49 | `TestFileOutput::test_r3b27_escaped_identifier_output` |
| 50 | `TestFileOutput::test_r3b28_ansi_escaped_output_format` |

**Total: 13 + 19 + 7 + 11 = 50.** Note: `test_r3b28_ansi_escaped_identifier` (Parser) and `test_r3b28_ansi_escaped_output_format` (FileOutput) share the `R3B28` prefix but are distinct pytest functions in different test classes. All 50 full names are unique.

---

## 9. Fixture Reference

`mcps/zynq_mcp/tests/fixtures/b04_pl_ready/` — 21 files.

Key values:
- `board_profile_sha256`: `sha256:3c95da56a6a9264ef42b6902f184d7d01c7229eafa70d1061cfd24cc0af0c90a`
- Correct `platform_revision`: `sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3`

---

## 10. Declarations

- R3.1-B = COMPLETE / FROZEN
- R3.1-C = NOT started (0 production lines, 0 test lines)
- R3.2 = NOT started
- Agent2 = NOT called
- All R3.1-A frozen SHA256 verified unchanged
- All R3.1-B frozen SHA256 match
- Full regression: 669 passed, 1 skipped (B02 pre-existing), 0 failed
- 0 RuntimeWarning
- Awaiting review before R3.1-C
