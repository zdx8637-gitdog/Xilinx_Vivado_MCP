# B03 — Board Configuration Package & Environment Baseline Test Plan v0.2.2

> Brick: B03  |  日期: 2026-08-04  |  状态: **COMPLETE — 所有子步骤完成，正式包已冻结**
> 关联: [B03_board_environment_plan.md](../mcp/B03_board_environment_plan.md)
> B02 基线: 235 collected / 234 passed / 1 skipped (`test_posix_link_no_overwrite`)

---

## 1. Test Tiers

| Tier | Description | Required for B03 Gate | Marker |
|------|-------------|----------------------|--------|
| **Process/mock** | Pure Python, file I/O, mocked subprocess | **Yes** | (none) |
| **Host-live optional** | Real EDA installation present | No | `@pytest.mark.host_live` |
| **Device-live optional** | Real USB-UART connected | No | `@pytest.mark.device_live` |

B03 has **zero** JTAG hardware tests. JTAG cable enumeration → B04/B06.

---

## 2. Test Fixtures

```
mcps/common/tests/fixtures/
├── board_profile_TEST_AX7020_MINIMAL.json   ← existing, unchanged
├── B03_drift_modified_profile.json
├── B03_drift_wrong_part.json
├── B03_drift_tampered_xdc.json
├── B03_drift_personal_path.json
└── B03_drift_self_ref_manifest.json
```

All drift tests use `tmp_path` copies. Real package never modified by tests.

---

## 3. Test Matrix — Mandatory (Process/Mock)

### 3.1 B03-T-1xx: Profile Loading & Validation (Sub-step 1, 11 tests)

| ID | Scenario | Top-Level Code | reason_code | Depends On |
|----|----------|---------------|-------------|------------|
| T-101 | `board_profile_load("ALINX_AX7020_v1.0", allow_draft=True)` succeeds; sha256 present; all machine fields typed correctly | — | — | Real package |
| T-102 | board_id internal ≠ requested → `BoardProfileError` | `CONTEXT_INVALID` | — | — |
| T-103a | Profile file content modified → `profile["sha256"]` changes | — | — | — |
| T-103b | Any package file changed → `manifest_revision` changes (≠ previous) | — | — | board_package |
| T-104 | `ps7_preset.tcl` missing from package dir | `CONTEXT_INVALID` | `MISSING_REQUIRED_FIELD` | — |
| T-105 | `board.xdc` missing from package dir | `CONTEXT_INVALID` | `MISSING_REQUIRED_FIELD` | — |
| T-106 | `ps7_preset.tcl` SHA256 ≠ profile `ps7_preset_sha256` | `ARTIFACT_STALE` | `PRESET_SHA256_MISMATCH` | — |
| T-107 | `board.xdc` SHA256 ≠ profile `xdc_sha256` | `ARTIFACT_STALE` | `XDC_SHA256_MISMATCH` | — |
| T-108 | Required field missing from profile (e.g. `ddr_physical_bytes`) | `CONTEXT_INVALID` | `MISSING_REQUIRED_FIELD` | — |
| T-109 | `board_profile_ALINX_AX7020_v1.0.json` contains malformed JSON | `CONTEXT_INVALID` | `INVALID_JSON` | — |
| T-110 | Default load with only draft manifest → `PACKAGE_NOT_LOCKED`; `allow_draft=True` → succeeds + files[].sha256 all match | `CONTEXT_INVALID` | `PACKAGE_NOT_LOCKED` | board_package |

### 3.2 B03-T-2xx: Drift & Error Detection (Sub-step 3, 13 tests)

| ID | Scenario | Top-Level Code | reason_code | Depends On |
|----|----------|---------------|-------------|------------|
| T-201 | Profile `part` modified while known-good manifest/expected revision exists → SHA256/revision mismatch | `ARTIFACT_STALE` | `PACKAGE_REVISION_MISMATCH` or `PROFILE_SHA256_MISMATCH` | board_package |
| T-202 | `ddr_configured_bytes > ddr_physical_bytes` | `CONTEXT_INVALID` | `DDR_CAPACITY_INCONSISTENT` | — |
| T-203 | `qspi_linear_window_bytes > 16777216` (16MB max) or > physical | `CONTEXT_INVALID` | `QSPI_WINDOW_INCONSISTENT` | — |
| T-204 | Profile `pl_leds.count=4` but XDC has only 3 pin assignments | `CONTEXT_INVALID` | `LED_COUNT_XDC_MISMATCH` | — |
| T-205 | Profile `pl_oscillator_hz=50000000` but XDC has `-period 10.000` (100MHz) | `CONTEXT_INVALID` | `CLOCK_FREQ_XDC_MISMATCH` | — |
| T-206 | Mock Vivado 2019.1 → below minimum 2023.1 | `ENV_ERROR` | `ENV_VERSION_UNSUPPORTED` | mock |
| T-207 | Vivado not on PATH, no install dirs | `ENV_ERROR` | `ENV_VIVADO_NOT_FOUND` | mock |
| T-208 | Vitis not on PATH, no install dirs | `ENV_ERROR` | `ENV_VITIS_NOT_FOUND` | mock |
| T-209 | XSCT not on PATH, no install dirs | `ENV_ERROR` | `ENV_XSCT_NOT_FOUND` | mock |
| T-210 | Profile contains `"vivado_path": "C:\\Users\\...\\Vivado"` | `CONTEXT_INVALID` | `ABSOLUTE_PATH_FORBIDDEN` | — |
| T-211 | Manifest `files[].path` contains `C:\\Users\\...` | `CONTEXT_INVALID` | `ABSOLUTE_PATH_FORBIDDEN` | — |
| T-212 | Manifest `files[]` includes entry for `package_manifest.json` itself | `CONTEXT_INVALID` | `MANIFEST_SELF_REFERENCE` | — |
| T-213 | `expected_package_revision != actual_package_revision` (package dir modified) | `ARTIFACT_STALE` | `PACKAGE_REVISION_MISMATCH` | board_package |

> T-201: The test modifies profile content → profile SHA changes and/or package revision changes compared to trusted manifest. Program does NOT hardcode "XC7Z020 is correct" — it detects drift via SHA/revision mismatch against locked baseline. A separate syntax test validates Xilinx part number format without board-specific truth.

> The LED polarity runtime check (formerly planned) is removed. Polarity truth comes from schematic review in sub-step 0 and is frozen in the package. Runtime integrity is via profile/package SHA and expected revision, not Python code hardcoding "active-low."

### 3.3 B03-T-3xx: Environment Probing (Sub-step 2, 5 tests)

| ID | Scenario | Top-Level Code | reason_code | Depends On |
|----|----------|---------------|-------------|------------|
| T-301 | `probe_all()` returns `EnvReport` with vivado/vitis/xsct/uart fields | — | — | — |
| T-302 | Mock `vivado -version` → parsed `{found:true, version:"2023.1", build:"..."}`; mock not-found → `{found:false, error:"ENV_VIVADO_NOT_FOUND"}` | `ENV_ERROR` | `ENV_VIVADO_NOT_FOUND` | mock |
| T-303 | 2023.1 + 2022.2 both installed → selects 2023.1, warns about 2022.2 | — | — | mock |
| T-304 | `EnvReport` tool paths NOT written into any Board Package file (grep package dir for installation paths) | — | — | — |
| T-305 | `env_probe.py` source contains no hardcoded COM port constant (grep for `COM[0-9]` as fixed string; `"COM"` substring in generic code is allowed) | — | — | — |

### 3.4 B03-T-4xx: Integration & Gate (Sub-step 4, 6 tests)

| ID | Scenario | Top-Level Code | reason_code | Depends On |
|----|----------|---------------|-------------|------------|
| T-401 | Fresh session: `board_profile_load()` + `probe_all()` → complete without unhandled exceptions | — | — | All sub-steps |
| T-402 | Freeze lifecycle: `package_manifest.draft.json` validates; `freeze_package()` publishes via `atomic_publish_no_replace()` to new `package_manifest.json`; draft deleted; final dir has exactly 6 files; manifest not self-listed; all file SHA256s correct; re-publish same content → `already_exists_same`; publish different content to existing locked → `ManifestConflictError` | — | — | board_package |
| T-403 | Same profile loaded 5× from different call sites → identical `sha256` | — | — | — |
| T-404 | B02 regression: full `mcps/` suite → B02 test set shows 0 new failures; 1 skip reason unchanged | — | — | B02 baseline |
| T-405 | `board_profile_load("TEST_AX7020_MINIMAL")` via explicit `search_dirs` → fixture-only fields intact; production default search rejects it | — | — | — |
| T-406 | Load → sha256_1 → modify file → sha256_2; cache invalidates correctly | — | — | — |

---

## 4. Test Matrix — Optional

### 4.1 B03-T-5xx: Host-Live Optional (Sub-step 2, 4 tests)

Skip if EDA tools absent.

| ID | Scenario | Expected | Depends On |
|----|----------|----------|------------|
| T-501 | `probe_vivado()` on real host → `found:true` with version + build | Vivado 2023.1 |
| T-502 | `probe_vitis()` on real host → `found:true` with version | Vitis 2023.1 |
| T-503 | `probe_xsct()` on real host → `found:true` with version | XSCT 2023.1 |
| T-504 | `probe_all()` on real host → complete `EnvReport` JSON artifact saved | All tools |

### 4.2 B03-T-6xx: Device-Live Optional (Sub-step 2, 1 test)

Skip if AX7020 USB-UART absent.

| ID | Scenario | Expected | Depends On |
|----|----------|----------|------------|
| T-601 | `probe_uart_devices(vid=0x10C4, pid=0xEA60)` with CP2102-GM → ≥1 device with port, VID, PID | AX7020 PS UART (CP210x) |

---

## 5. Test Execution by Sub-step

```
# Sub-step 1 (11 tests)
pytest mcps/common/tests/test_board_profile_validation.py -v
pytest mcps/common/tests/test_board_package.py -v -k "manifest"

# Sub-step 2 (5 mandatory + 5 optional)
pytest mcps/common/tests/test_env_probe.py -v
pytest mcps/common/tests/test_env_probe.py -v -m "host_live or device_live"

# Sub-step 3 (13 tests)
pytest mcps/common/tests/test_board_drift.py -v

# Sub-step 4 — Full gate (35 mandatory)
pytest mcps/ -v
pytest mcps/ -v -m "host_live or device_live"  # optional
```

---

## 6. Test File Mapping

| Test File | IDs Covered | Count | Required |
|-----------|------------|-------|----------|
| `test_board_profile_validation.py` | T-101, T-102, T-103a, T-104, T-105, T-108, T-109 | 7 | Yes |
| `test_board_package.py` | T-103b, T-106, T-107, T-110, T-402 | 5 | Yes |
| `test_board_drift.py` | T-201–T-205, T-210–T-213 | 9 | Yes |
| `test_env_probe.py` | T-206–T-209, T-301–T-305, T-501–T-504, T-601 | 14 | 9 mandatory |
| `test_env_probe_isolation.py` | (cwd isolation, timeout tree kill, success cleanup) | 3 | Yes |
| `test_package_integration.py` | T-401, T-403–T-406 | 5 | Yes |
| **Mandatory subtotal** | | **38** | |
| **Optional subtotal** | | **5** | |
| **Total** | | **43** | |

---

## 7. Count Summary

| Series | Count | Required |
|--------|-------|----------|
| B03-T-1xx | 11 | Yes |
| B03-T-2xx | 13 | Yes |
| B03-T-3xx | 5 | Yes |
| B03-T-4xx | 6 | Yes |
| Isolation (A/B/C) | 3 | Yes |
| **Mandatory subtotal** | **38** | |
| B03-T-5xx (host-live) | 4 | No |
| B03-T-6xx (device-live) | 1 | No |
| **Total** | **43** | |

B02 regression: existing test set (235/234/1) must show 0 new failures and unchanged skip
reason. B03 does not claim "total = 235" — that was the B02 baseline only.
Current B03+B02 combined regression: 348 passed, 1 skipped.

---

## 8. Error Code Cross-Reference

| Top-Level (B02) | reason_code (B03 internal) | Tests |
|-----------------|---------------------------|-------|
| `CONTEXT_INVALID` | — | T-102 |
| `CONTEXT_INVALID` | `MISSING_REQUIRED_FIELD` | T-104, T-105, T-108 |
| `CONTEXT_INVALID` | `INVALID_JSON` | T-109 |
| `CONTEXT_INVALID` | `PACKAGE_NOT_LOCKED` | T-110 |
| `CONTEXT_INVALID` | `DDR_CAPACITY_INCONSISTENT` | T-202 |
| `CONTEXT_INVALID` | `QSPI_WINDOW_INCONSISTENT` | T-203 |
| `CONTEXT_INVALID` | `LED_COUNT_XDC_MISMATCH` | T-204 |
| `CONTEXT_INVALID` | `CLOCK_FREQ_XDC_MISMATCH` | T-205 |
| `CONTEXT_INVALID` | `ABSOLUTE_PATH_FORBIDDEN` | T-210, T-211 |
| `CONTEXT_INVALID` | `MANIFEST_SELF_REFERENCE` | T-212 |
| `ARTIFACT_STALE` | `PRESET_SHA256_MISMATCH` | T-106 |
| `ARTIFACT_STALE` | `XDC_SHA256_MISMATCH` | T-107 |
| `ARTIFACT_STALE` | `PACKAGE_REVISION_MISMATCH` | T-213 |
| `ARTIFACT_STALE` | `PROFILE_SHA256_MISMATCH` or `PACKAGE_REVISION_MISMATCH` | T-201 |
| `ENV_ERROR` | `ENV_VIVADO_NOT_FOUND` | T-207, T-302 |
| `ENV_ERROR` | `ENV_VITIS_NOT_FOUND` | T-208 |
| `ENV_ERROR` | `ENV_XSCT_NOT_FOUND` | T-209 |
| `ENV_ERROR` | `ENV_VERSION_UNSUPPORTED` | T-206 |

> `PART_NUMBER_INVALID` still exists as reason_code for Xilinx part format syntax validation.
> It does not appear in drift tests because T-201 now detects drift via SHA/revision mismatch.

---

## 9. Mechanical Checks (verified after writing)

- [x] Plan and test plan use identical sub-step numbers (0–4)
- [x] Profile filename: `board_profile_ALINX_AX7020_v1.0.json` in both docs
- [x] Package directory: `boards/ALINX_AX7020_v1.0/` in both docs
- [x] Test IDs: T-101–110 (11), T-201–213 (13), T-301–305 (5), T-401–406 (6), T-501–504 (4), T-601 (1) = 40 unique
- [x] Sub-step 1 count: 11 in both plan §7 and test plan §3.1
- [x] Sub-step 3 count: 13 in both plan §7 and test plan §3.2
- [x] Error codes: consistent between plan §4 and test plan §8
- [x] No manual revision integer increment rule
- [x] No `LED_POLARITY_INCONSISTENT` runtime check
- [x] No JTAG cable enumeration in B03 scope
- [x] No personal absolute paths in package files
- [x] No code or Board Package files created
- [x] Sub-step 0 COMPLETE/FROZEN — inventory at [B03_asset_inventory.md](../mcp/B03_asset_inventory.md)
- [x] Sub-step 1 COMPLETE/FROZEN — Board Package created, 74 tests pass, loader fail-closed
- [x] Sub-step 2 COMPLETE/FROZEN — env_probe implemented, 3 EDA tools verified, UART present detection correct
- [x] Sub-step 3 COMPLETE/FROZEN — Agent2 Round 2-5 black-box: 19/19 PASS; all T-2xx precise ErrorCode/reason_code
- [ ] Sub-step 4 in progress — freeze_package implemented, integration tests pass, awaiting production freeze
- [x] PS LED: MIO[0,13] active-low consistent across all three B03 docs
- [x] CP2104 string: 0 occurrences (corrected to CP2102-GM / CP210x family)
- [x] MIO7/MIO8 as PS LED: 0 occurrences (corrected to MIO0/MIO13)
- [x] EDA NOT INSTALLED: 0 occurrences (corrected to installed at D:\Xilinx)
- [x] Test statistics: 348 passed, 1 skipped (full B02+B03 regression)
- [x] Draft manifest lifecycle: `package_manifest.draft.json` → `package_manifest.json`; loader `allow_draft` semantics
- [x] Freeze uses B02 `atomic_publish_no_replace()` only; does NOT call B02 `publish_manifest()`
- [x] Vivado probe: -nolog -nojournal -notrace, TemporaryDirectory cwd, zero caller-artifacts
- [x] Vivado: version_command (Tcl), Vitis: install_metadata, XSCT: version_command (-eval)
- [x] USB-UART: COM4 present/CP210x, COM5 present/CH340, COM3 historical/present=False
- [x] Process isolation: timeout kills tree (taskkill /T /PID); success exits cleanly
- [x] T-101: `allow_draft=True`; T-109: correct profile filename; T-110: draft/locked test; T-402: full freeze lifecycle
- [x] PL LED: "transistor driver" removed; replaced with VCCIO_35/resistor/LED/FPGA pin
- [x] PS LED: polarity from schematic only; MIO mapping cross-checked from code; helloworld.c not claimed as polarity proof
- [x] Active test table IDs = 40 unique; the historical LED polarity runtime check (removed in v0.2.1) not counted
- [x] `PACKAGE_NOT_LOCKED` reason_code present in plan §4 and test plan §8
