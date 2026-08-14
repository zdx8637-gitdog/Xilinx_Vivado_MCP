# B03 — Board Configuration Package & Environment Baseline Plan v0.2.2

> Brick: B03  |  日期: 2026-08-04  |  状态: **COMPLETE — 所有子步骤完成，正式包已冻结，等待Agent2黑盒验收**
> 依赖: B00 ✅ / B01 ✅ / B02 ✅ (FROZEN)
> 架构: `docs/architecture_ai_zynq7020.md` v2.3.1 Appendix B

---

## 1. Goals & Non-Goals

**Goals**: Single SHA256-guarded board data source; Board Configuration Package with
machine-verifiable integrity; Vivado/Vitis/XSCT environment probing; drift/error-config
rejection; fresh-session pre-check using only the package.

**Non-Goals**: Domain APIs (B04/B05/B06); Vivado MCP adapt (B04); BD/synth/bitstream
(B05); PS code/ELF (B06); JTAG cable enumeration or hw_server connection (B04/B06);
`TEST_AX7020_MINIMAL` upgrade (never); Vivado GUI (never).

---

## 2. Board Configuration Package

### 2.1 Directory & Files

**Draft** (sub-steps 1–3):
```
boards/ALINX_AX7020_v1.0/
├── board_profile_ALINX_AX7020_v1.0.json   ← machine-readable board facts
├── package_manifest.draft.json              ← draft manifest (editable)
├── ps7_preset.tcl                           ← full vendor ps_config.tcl (537 params)
├── board.xdc                                ← PL clock + 4 PL LED pins only
├── SOURCES.md                               ← provenance record per constraint group
└── README.md                                ← human-readable description
```

**Locked** (sub-step 4 freeze):
```
boards/ALINX_AX7020_v1.0/
├── board_profile_ALINX_AX7020_v1.0.json   ← machine-readable board facts
├── package_manifest.json                    ← LOCKED manifest (immutable)
├── ps7_preset.tcl                           ← full vendor ps_config.tcl (537 params)
├── board.xdc                                ← PL clock + 4 PL LED pins only
├── SOURCES.md                               ← provenance record per constraint group
└── README.md                                ← human-readable description
```

At sub-step 4, `freeze_package()` atomically promotes the draft to
`package_manifest.json` and deletes `package_manifest.draft.json`.
The final locked directory contains exactly 6 files (no residual draft).
Profile file name MUST match `board_profile_<board_id>.json` per existing loader rule.

### 2.2 Package Integrity Model

Uses B02 `compute_revision()` and `canonical_json()`. No manual revision integers.
Does NOT call B02 `publish_manifest()` — that function validates only
`manifest_type ∈ {platform, pl_build, ps_build}` and enforces revision-based
filenames. The board configuration package has its own schema, its own validator,
and publishes to a fixed filename.

```
package_manifest.json / package_manifest.draft.json:
  schema_version, manifest_type = "board_configuration", board_id
  package_version = "1.0"                     ← human release label only
  manifest_revision = compute_revision(revision_inputs)
  revision_inputs = {
    board_profile_sha256, ps7_preset_sha256, board_xdc_sha256,
    sources_md_sha256, readme_md_sha256
  }
  status, generated_at, files = [...]
```

Rules:
- `manifest_revision` is content-derived; every package file change → new revision
- `package_version` and `manifest_revision` are independent
- Package manifest never lists its own SHA256
- Profile `sha256` = profile file only; `manifest_revision` = whole package
- Locked `package_manifest.json` is immutable; uses B02 `atomic_publish_no_replace()`
- A locked package whose content has changed → subsequent validate reports `ARTIFACT_STALE`
- If a locked package must change, create a new package version directory (e.g.,
  `boards/ALINX_AX7020_v1.1/`); never overwrite a locked v1.0
- `board_profile_load()` returns `package_revision` (compatible extension)

### 2.3 Draft → Locked Lifecycle

| State | Filename | When | Loader Behavior |
|-------|----------|------|-----------------|
| **draft** | `package_manifest.draft.json` | Created in sub-step 1; used during sub-steps 2–3 | Rejected by default (`CONTEXT_INVALID` + `PACKAGE_NOT_LOCKED`); accepted only with explicit `allow_draft=True` |
| **locked** | `package_manifest.json` | Atomically published in sub-step 4; draft deleted | Accepted by default; no `allow_draft` needed |

**Freeze procedure** (sub-step 4; `freeze_package()` not called in this sub-step):
1. Read and validate `package_manifest.draft.json` via `board_package.validate_package_manifest()`
2. Set `status = "locked"` on an in-memory copy; re-validate locked content
3. Serialize via B02 `canonical_json()`; call B02 `atomic_publish_no_replace()` to
   `package_manifest.json` (must not already exist)
4. If target exists with same semantic content → `already_exists_same`
5. If target exists with different content → `ManifestConflictError`
6. On successful publish: confirm final content matches, then delete `package_manifest.draft.json`
7. Final directory has exactly 6 files; no residual draft
8. Must NEVER modify `package_manifest.json` after publishing

**Default loader rule**: `board_profile_load()` only accepts locked
`package_manifest.json`. Attempting to load a directory containing only a draft
manifest raises `BoardProfileError` with `reason_code=PACKAGE_NOT_LOCKED`.
Tests in sub-steps 1–3 explicitly pass `allow_draft=True`. Production MCP and
Workflow code never sets `allow_draft=True` — draft packages are invisible to them.

Draft may be freely modified during development. Locked manifest is immutable —
same revision cannot be re-published.

### 2.4 Data Format: Machine Fields

Use deterministic numeric types, not free text. Addresses as decimal integers in JSON.

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `ddr_physical_bytes` | int | `1073741824` | From vendor (2×4Gbit) |
| `ddr_configured_bytes` | int | `536870912` | From HIGHADDR `0x1FFFFFFF` + 1 |
| `ddr_configured_highaddr` | int | `536870911` | Decimal `0x1FFFFFFF` |
| `ddr_frequency_hz` | int | `533333333` | Only if source provides exact Hz; if source has only "533.333 MHz", record conversion rule in inventory |
| `ddr_bus_width_bits` | int | `32` | |
| `qspi_physical_bytes` | int | `33554432` | 256 Mbit = 32 MB |
| `qspi_linear_window_bytes` | int | `16777216` | Zynq-7000 max x4 linear window |
| `qspi_base_address` | int | `4227858432` | Decimal `0xFC000000` |
| `pl_oscillator_hz` | int | `50000000` | 50 MHz |
| `pl_oscillator_pin` | str | `"U18"` | |
| `ps_clock_hz` | int | `33333333` | 33.333 MHz; document rounding |
| `pl_leds.pins` | str[] | `["J16","K16","M15","M14"]` | Ordered LED[3]..LED[0] |
| `pl_leds.polarity` | str | `"active-low"` | Frozen from schematic review in sub-step 0; NOT validated at runtime against XDC |
| `uart.controller` | str | `"UART1"` | |
| `uart.mio_pins` | int[] | `[48, 49]` | Array, not free text |
| `uart.default_baud` | int | `115200` | |

README.md may display hex or human units. Parameter provenance is centralized in
`source_catalog`, not repeated per field.

**Frequency conversion rules**: If vendor source provides only fractional MHz (e.g.,
`533.333 MHz`), sub-step 0 must record: source value, conversion formula, precision loss,
and resulting integer. Do not silently round.

### 2.5 Source & Path Rules

Board Configuration Package files MUST NOT contain:
- `D:\BaiduNetdiskDownload...`, `C:\Users...`, any username, host absolute paths

Use stable source IDs with distribution-relative paths:

```json
"source_catalog": [
  {"source_id": "ALINX_AX7020_2023_1_PS_CONFIG",
   "distribution_path": "course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl",
   "sha256": "sha256:...",
   "role": "authoritative_ps7_preset"}
]
```

Host external media root recorded only in `B03_asset_inventory.md`, never in the package.

### 2.6 ps7_preset.tcl — Full Copy

Vendor `ps_config.tcl` header permits use/redistribution with copyright retained.
Sub-step 1 copies entire original file:
- Retain copyright and disclaimer header verbatim
- Byte-identical content
- Record both original SHA256 and in-package SHA256 in `SOURCES.md`
- Do NOT extract only `set_property -dict` block

### 2.7 board.xdc — Controlled Derived File

Only PL 50MHz clock pin/constraint + 4 PL LED pin assignments + IOSTANDARD.
NO PL UART pin, NO expansion pins. `SOURCES.md` records vendor source per constraint group.

---

## 3. B02 Loader Compatibility

### 3.1 Production/Test Isolation

Production default `_SEARCH_DIRS` is the exact package directory:

```python
_SEARCH_DIRS = [
    Path(__file__).resolve().parent.parent.parent / "boards" / "ALINX_AX7020_v1.0",
]
```

Not `boards/` alone — loader does not recurse. `TEST_AX7020_MINIMAL` is NOT reachable
via production default.

Isolation mechanisms (in priority order):
1. **Explicit `search_dirs` argument** — callers pass explicit list; always honored first
2. **Production default** — `_SEARCH_DIRS` as above; only real package
3. **Environment variable `ZYNQ_BOARD_PROFILE_DIRS`** — colon/semicolon-separated paths; appended after explicit but before default; allows test/admin injection
4. **pytest `conftest.py`** — sets `ZYNQ_BOARD_PROFILE_DIRS` to include `mcps/common/tests/fixtures/`
5. **MCP SDK subprocess tests** — inherit `ZYNQ_BOARD_PROFILE_DIRS` from parent process
6. **Production `.mcp.json`** — does NOT set `ZYNQ_BOARD_PROFILE_DIRS`

Added test: `board_profile_load("TEST_AX7020_MINIMAL")` with only production default → raises `FileNotFoundError`.

### 3.2 Allowed B03 Modifications to board_profile.py

Two compatible extensions only:
1. Search directory mechanism as described in §3.1
2. For non-fixture real profiles, call `board_package.validate_*` and return `package_revision` in profile dict

BoardProfileError backward-compatible extension:
```python
class BoardProfileError(Exception):
    def __init__(self, message: str, code: str = "CONTEXT_INVALID",
                 reason_code: str | None = None):
        self.code = code
        self.reason_code = reason_code
        super().__init__(message)
```
- Existing `code`/`message` behavior unchanged; existing callers need no modification
- `reason_code` is an optional keyword-only attribute (default `None`)
- T-110 verifies `reason_code == "PACKAGE_NOT_LOCKED"` when draft is rejected

Must NOT change: cache semantics, deep copy, `_source_path` leak prevention,
`board_id` validation, B02 `BoardProfileError` semantics.

### 3.3 New: board_package.py

```
mcps/common/board_package.py
  - validate_package_manifest(manifest) → list[ValidationIssue]
    (validates board_configuration schema — NOT using B02 validate_manifest()
     which requires manifest_type ∈ {platform, pl_build, ps_build})
  - compute_package_revision(package_dir) → revision string
    (uses B02 compute_revision() + canonical_json())
  - freeze_package(package_dir) → str
    (validates draft → atomic_publish_no_replace() to package_manifest.json
     → deletes draft on success → returns "published")
  - check_package_integrity(package_dir) → list[PackageIssue]
  - validate_relative_paths(package_dir) → list[ValidationIssue]
  - verify_preset_xdc_sha(profile, package_dir) → list[ValidationIssue]
```

Uses B02 `compute_revision()`, `canonical_json()`, `atomic_publish_no_replace()`.
Does NOT call B02 `publish_manifest()` — incompatible manifest_type.

---

## 4. Error Model

B03 does NOT add reason codes to `ErrorCode` enum. Existing top-level codes reused:

| Top-Level Code | B03 Trigger |
|---------------|-------------|
| `CONTEXT_INVALID` | Board profile structure invalid, board_id mismatch, missing fields |
| `ARTIFACT_STALE` | SHA256/revision mismatch, package revision mismatch |
| `ENV_ERROR` | Vivado/Vitis/XSCT not found or unsupported version |
| `INVALID_ARGUMENT` | Bad API parameters |
| `INTERNAL_ERROR` | Unexpected internal failure |

Internal `reason_code` in `error.details`:

| reason_code | Meaning |
|-------------|---------|
| `PRESET_SHA256_MISMATCH` | ps7_preset.tcl hash ≠ profile field |
| `XDC_SHA256_MISMATCH` | board.xdc hash ≠ profile field |
| `PACKAGE_REVISION_MISMATCH` | expected ≠ actual package manifest_revision |
| `PROFILE_SHA256_MISMATCH` | profile file hash ≠ recorded value |
| `DDR_CAPACITY_INCONSISTENT` | physical < configured |
| `QSPI_WINDOW_INCONSISTENT` | window > 16MB max or > physical |
| `LED_COUNT_XDC_MISMATCH` | profile count ≠ XDC pin count |
| `CLOCK_FREQ_XDC_MISMATCH` | profile frequency ≠ XDC period |
| `PART_NUMBER_INVALID` | Part syntax invalid (not a recognized Xilinx format); never validates board-specific truth |
| `ABSOLUTE_PATH_FORBIDDEN` | personal/absolute path in package |
| `MANIFEST_SELF_REFERENCE` | manifest lists its own SHA256 |
| `MISSING_REQUIRED_FIELD` | required field absent |
| `INVALID_JSON` | malformed JSON |
| `PACKAGE_NOT_LOCKED` | package_manifest.json not found; only draft present |

Tests assert both: top-level `ErrorCode` + internal `reason_code`.

---

## 5. Environment Probing

### 5.1 Scope (B03 Only)

| Do | Don't |
|----|-------|
| Vivado/Vitis/XSCT executable discovery with injectable search roots (`ZYNQ_EDA_SEARCH_ROOTS`, default: `C:\Xilinx;D:\Xilinx`) | Start or connect to hw_server |
| Version query + 2023.1 compatibility check | JTAG cable enumeration (`get_hw_targets`) |
| Windows USB-UART read-only enumeration via registry: PS UART (CP2102-GM, 10C4:EA60) + PL UART (CH340, marked `LAB_FIXTURE`) | Connect to real JTAG |
| Structured `EnvReport` | Become third JTAG Lock consumer |

JTAG cable serial and target enumeration → B04/B06.
JTAG interface (FT232HL, schematic page 3) recorded in asset inventory for B04 handoff.

**USB three-way distinction** (see [B03_asset_inventory.md](B03_asset_inventory.md) §2.8):
- PS UART: CP2102-GM on-board, bidirectional UART1 MIO[48,49], VID/PID 10C4:EA60
- PL UART: CH340 external, FPGA TX only, `LAB_FIXTURE` — not a board static fact
- JTAG: FT232HL — recorded for B04, no enumeration in B03

### 5.2 Version Policy

Project preferred/supported version: **2023.1**.
- 2023.1 found → `supported`
- Only other versions → `unsupported`
- Multiple versions → prefer exact 2023.1 match, report others as warnings
- No "auto-select latest"

### 5.3 Path Handling

`EnvReport` MAY contain real tool absolute paths at runtime. It MUST NOT write those
paths into any Board Package file. Test evidence may sanitize paths before saving.

---

## 6. Sub-Step Implementation Plan

### Sub-step 0: Authoritative Asset & Source Inventory

**Goal**: Verified de-duplicated parameter table with SHA256 evidence.

**Allowed**: Read-only search; SHA256 computation; Windows USB/PnP read-only enumeration;
lightweight version queries; CREATE/MODIFY `docs/development/mcp/B03_asset_inventory.md`;
UPDATE this plan and test plan sub-step 0 status.

**Forbidden**: Create `boards/`; copy `ps_config.tcl`; generate `board.xdc`; modify `mcps/`;
start Vivado GUI; start/connect hw_server; JTAG enumeration; board programming; driver
changes; enter sub-step 1.

**Deliverables**: `B03_asset_inventory.md` with per-parameter table:

| Parameter | Candidate Value | Authoritative Source ID | Distribution-Relative Path | Source SHA256 | Exact Locator | Secondary Cross-Check | Conversion Rule | Verification Status | Notes |

**Required static facts**:
- Chip full model + Vivado part; board_id
- PS7 preset canonical source (explain why chosen from 50+ copies)
- DDR chip model, count, physical bytes, configured bytes, highaddr, frequency, bus width
- QSPI model, physical bytes, linear window, base address
- PL 50MHz clock pin
- 4 PL LED pins, order, IOSTANDARD, polarity (from schematic, not XDC)
- 2 PS LED exact MIO/signal and polarity (from schematic: MIO[0,13], active-low; cross-checked with ps_led helloworld.c)
- UART controller + MIO pins
- ps_config.tcl license/copyright retention requirements
- board.xdc per-constraint-group authoritative source

**Duplicate vendor files**: SHA256-group all 50+ `ps_config.tcl` files; compare key PCW
fields across groups; explain canonical selection; distinguish "DDR fields same" from
"entire file same."

**USB-UART**: On-board CP2102-GM (CP210x family), VID/PID `10C4:EA60` verified via live
enumeration. PL UART CH340 external — `LAB_FIXTURE` only. COM port = runtime state, never in package.

**Gate**: All static fields for Board Profile have authoritative sources; PS LED MIO[0,13]
active-low confirmed 2-way (schematic + helloworld.c); canonical ps_config.tcl selected
with SHA256; machine numeric conversion rules documented; USB VID/PID verified; three USB
interfaces distinguished; EDA tools discovered at `D:\Xilinx` (host baseline); JTAG serial
and EDA build NOT required for gate; no production files or code created.

### Sub-step 1: Board Configuration Package & Schema

**Goal**: Create draft Board Package (NOT locked).

**Allowed**: CREATE `boards/ALINX_AX7020_v1.0/` + all package files;
MODIFY `mcps/common/board_profile.py` (§3.2 scope only);
CREATE `mcps/common/board_package.py` + tests;
CREATE `mcps/common/tests/test_board_profile_validation.py`;
CREATE `mcps/common/tests/test_board_package.py`.

**Forbidden**: Modify `TEST_AX7020_MINIMAL` fixture; modify B01/B02 frozen code beyond
§3.2 scope; set `status: "locked"` on package; create `package_manifest.json` (only
`package_manifest.draft.json` is created in this sub-step).

**Gate**: 11 tests (T-101 through T-110, with T-103a/T-103b counted separately).
Profile loads; SHA256s verified; required fields validated; package manifest validates
without self-reference; production default search rejects TEST_AX7020_MINIMAL.

### Sub-step 2: Environment Probing & Diagnostics

**Goal**: Host tool discovery + USB-UART enumeration. Zero JTAG.

**Allowed**: CREATE `mcps/common/env_probe.py`; CREATE `mcps/common/tests/test_env_probe.py`.

**Forbidden**: hw_server connection; JTAG cable enumeration; MCP server modification.

**Gate**: 5 mandatory tests pass (T-301–T-305). Structured results for found + not-found;
version policy enforced; no personal paths in package.

### Sub-step 3: Drift & Error Configuration Tests

**Goal**: Prove modified/tampered/inconsistent packages are rejected.

**Allowed**: CREATE drift fixtures; CREATE `mcps/common/tests/test_board_drift.py`.

**Forbidden**: Modify real `boards/ALINX_AX7020_v1.0/` after Sub-step 1 creation.

**Tests**: All tamper tests use `tmp_path` copies. Verify profile SHA and package revision
levels independently. 13 tests.

**Gate**: All drift tests return correct top-level ErrorCode + reason_code; no false
positives against clean candidate package.

### Sub-step 4: B03 Final Gate & Freeze

**Goal**: Lock package, verify regression, update docs.

**Actions**: Run full test suite; verify B01/Vivado-MCP frozen SHA256s; verify `.mcp.json`
unchanged; call `board_package.freeze_package()` which validates draft, then
`atomic_publish_no_replace()` to `package_manifest.json`, then deletes draft;
set `status: "locked"` in published manifest; freeze SHA256s and revisions;
update `brick_development_plan.md` B03 → "已完成";
write `B03_completion_report.md` + `B03_to_B04_handoff.md`.

**Gate**: All 35 mandatory tests pass; B02 test set shows 0 new failures, skip reason
unchanged; frozen files verified; candidate promoted to locked.

---

## 7. Test Summary (mirrors test plan)

| Series | Sub-step | Count | Layer | Required |
|--------|----------|-------|-------|----------|
| B03-T-1xx | 1 | 11 | Process/mock | Yes |
| B03-T-2xx | 3 | 13 | Process/mock | Yes |
| B03-T-3xx | 2 | 5 | Process/mock | Yes |
| B03-T-4xx | 4 | 6 | Process/mock | Yes |
| **Total mandatory** | | **35** | | |
| B03-T-5xx | 2 | 4 | Host-live optional | No |
| B03-T-6xx | 2 | 1 | Device-live optional | No |
| **Total including optional** | | **40** | | |

File mapping:

| Test File | IDs Covered | Count | Required |
|-----------|------------|-------|----------|
| `test_board_profile_validation.py` | T-101, T-102, T-103a, T-104, T-105, T-108, T-109 | 7 | Yes |
| `test_board_package.py` | T-103b, T-106, T-107, T-110, T-402 | 5 | Yes |
| `test_board_drift.py` | T-201–T-205, T-210–T-213 | 9 | Yes |
| `test_env_probe.py` | T-206–T-209, T-301–T-305, T-501–T-504, T-601 | 14 | 9 mandatory |
| `test_package_integration.py` | T-401, T-403–T-406 | 5 | Yes |
| **Mandatory** | | **35** | |
| **Optional** | | **5** | |

---

## 8. Risk & Rollback

| Risk | Mitigation |
|------|-----------|
| Vivado not on PATH | Probe returns structured `ENV_ERROR`; tests pass with mock |
| PS LED source not found in schematic | Block sub-step 0; do not guess |
| USB VID/PID differs from CP210x datasheet | Report actual value from enumeration; mark verified only after cross-check |
| `_SEARCH_DIRS` change breaks B02 | Full regression run in sub-step 4 gate |

Each sub-step independently revertible.

---

## 9. Open Unknowns (resolved in sub-step 0)

| # | Unknown | Resolution |
|---|---------|-----------|
| U1 | USB-UART VID/PID actual value | USB enumeration; keep `candidate` if no device |
| U2 | PS LED count + MIO pins | Vendor schematic or PS7 MIO config |
| U3 | Vivado 2023.1 build number | `vivado -version` |
| U4 | Which ps_config.tcl is canonical among 50+ copies | SHA256-group + PCW field comparison |
| U5 | DDR frequency exact Hz vs MHz conversion | Source value + formula + rounding documented |

---

## 10. Gate Checklist

- [x] B00/B01/B02 confirmed frozen
- [x] This plan v0.2.2 written
- [x] Test plan v0.2.2 written
- [x] Directory: `boards/ALINX_AX7020_v1.0/`
- [x] Filename: `board_profile_ALINX_AX7020_v1.0.json`
- [x] Integrity: `compute_revision()` on `revision_inputs`
- [x] Error model: top-level codes + internal `reason_code`; no runtime LED polarity check
- [x] Loader: production/test isolated via env var + explicit search_dirs
- [x] Machine fields: decimal integers for addresses; arrays for pins/mio
- [x] Candidate/locked state distinction
- [x] Draft manifest: `package_manifest.draft.json` (sub-steps 1–3) → `package_manifest.json` (sub-step 4 freeze)
- [x] Freeze: `atomic_publish_no_replace()` (not B02 `publish_manifest()`); draft deleted on success
- [x] Loader: default rejects draft (`PACKAGE_NOT_LOCKED`); `allow_draft=True` for tests only
- [x] Env probe: no JTAG cable enumeration
- [x] Board Package: no absolute paths, no self-referencing SHA256
- [x] Sub-step 0 COMPLETE/FROZEN — see [B03_asset_inventory.md](B03_asset_inventory.md)
- [x] PS LED: MIO[0,13], active-low, 2-way verified (schematic + helloworld.c)
- [x] PS UART: CP2102-GM, VID/PID 10C4:EA60
- [x] Three USB interfaces distinguished (PS UART/lab fixture/JTAG)
- [x] EDA tools discovered at `D:\Xilinx` (host baseline, not in package)
- [x] env_probe search roots: injectable, defaults cover `C:\Xilinx;D:\Xilinx`
- [ ] Sub-step 1 not yet started — requires explicit authorization
