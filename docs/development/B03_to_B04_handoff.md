# B03 → B04 Comprehensive Handoff

> 日期: 2026-08-05
> 版本: v2.3 (B04 v0.2.2 Sub-step 0 FROZEN, Sub-step 1 进行中)
> 范围: B00–B03冻结基线 + B04 v0.2.2规划 + Sub-step 0完成

---

## — Agent1 Fast Recovery Zone —

**Project goal**: AI Agent-driven Zynq-7020 (XC7Z020CLG400-2, ALINX AX7020)
standard development flow — from requirement description + board data to
complete GPIO LED vertical slice.

**Approach**: Brick-by-brick with per-brick freeze gates.

**Agent1 role**: Long-context white-box implementer. Implements code, writes tests,
runs all regression suites, and freezes deliverables.

**Agent2 role**: Fresh-context zero-memory black-box validator. Independently
confirms each brick's deliverables from specifications alone.

**Current state**:
- B00 COMPLETE/FROZEN — Project clean-up and organization
- B01 COMPLETE/FROZEN — Standard Zynq flow + GPIO acceptance spec
- B02 COMPLETE/FROZEN — MCP common contract + three empty skeletons
- B03 COMPLETE/FROZEN — Board Configuration Package + environment baseline
- B04 Sub-step 0 COMPLETE/FROZEN, Sub-step 1 in progress — ready for review; **implementation NOT started**
- B05–B10 not started

**Immediate work**: Review and approve B04 planning; then begin B04 implementation.

**Forbidden**: Implement B04 code before plan approved; enter B05/B06;
modify frozen B01/B02/B03 deliverables, Vivado MCP, or `.mcp.json`.

---

## 1. B03 Frozen Baseline

### 1.1 Board Configuration Package (Locked)

**Path**: `boards/ALINX_AX7020_v1.0/`

| File | SHA256 | Size (bytes) |
|------|--------|------|
| `board_profile_ALINX_AX7020_v1.0.json` | `sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18` | 3,419 |
| `ps7_preset.tcl` | `sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299` | 25,482 |
| `board.xdc` | `sha256:055a3aaaaaf26a8be37aabd07710b4d4bab9d9b1aacc49d6438461723acaece2` | 904 |
| `SOURCES.md` | `sha256:62a1c2ea77f07b55b112444d4e0831f9c84c1dfac7142907996a767b815c9524` | 2,256 |
| `README.md` | `sha256:8cf4cc70ffa6d07dd06b08f63fbf291375a430e5742e5de63446e298edb33710` | 1,513 |
| `package_manifest.json` (locked) | `sha256:ca931987a5843a0bbc627faa40d8842c15e774662dc51e945dafaf03999c97fb` | 1,466 |

**manifest_revision**: `sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`

`package_manifest.draft.json` no longer exists — production package is locked.

### 1.2 Backups

| Purpose | Path |
|---------|------|
| Pre-freeze backup | `D:\_b00_backup\B03_prefreeze_ALINX_AX7020_v1.0_20260805_120130\` |
| Black-box reports | `D:\_b00_backup\B03_blackbox_reports_20260805_135210\` |

### 1.3 Black-Box Acceptance Evidence Chain

**Round 1** (Agent2 fresh context, broad sweep):
- 100 items directly PASS
- 4 items INCONCLUSIVE: T-202/T-203/T-204/T-205

The 4 INCONCLUSIVE items were **NOT** product defects. Agent2's test fixture
copies modified profile/XDC content but did not rebuild the full SHA/revision
chain. The product detection logic was correct; the test harness was incomplete.

**Round 2–5** (Agent2 narrow re-verification):
- Fresh tmp_path packages with full SHA/revision self-consistency
- All 4 items independently confirmed:

| Test | ErrorCode | reason_code | Result |
|------|-----------|-------------|--------|
| T-202 | `CONTEXT_INVALID` | `DDR_CAPACITY_INCONSISTENT` | **PASS** |
| T-203 | `CONTEXT_INVALID` | `QSPI_WINDOW_INCONSISTENT` | **PASS** |
| T-204 | `CONTEXT_INVALID` | `LED_COUNT_XDC_MISMATCH` | **PASS** |
| T-205 | `CONTEXT_INVALID` | `CLOCK_FREQ_XDC_MISMATCH` | **PASS** |

**Final**: 19/19 acceptance items PASS. No PUBLIC_CONTRACT_GAP. No blocking items.

Project-root temporary report `tmp_b03_acceptance_report.md` has been cleaned
up (SHA256: `sha256:a036d068...` at time of backup).

### 1.4 Final Test Baseline

```
387 passed total (382 mandatory + 5 optional), 1 skipped
```

- B02 baseline: 234 passed → 0 new failures
- 1 skipped: `test_posix_link_no_overwrite` (POSIX-only, unchanged)
- Two tests (`test_uart_device_live`, `test_concurrent_freeze`) are non-gating
  optional tests that may fail on some runs — not required for gate

### 1.5 Frozen Files — No Modifications

| File | SHA256 | Notes |
|------|--------|-------|
| `docs/development/skill/B01_standard_zynq_flow.md` | `sha256:650804854fa8d00d2c8d52b473171f84ddac20c5f555abc69fbc237559a8ae80` | B01 FROZEN |
| `docs/development/tests/B01_gpio_acceptance_spec.md` | `sha256:8cefa1e78cca6c9b21caabde093695f59b765161e7caba16e681e29602befdd1` | B01 FROZEN |
| `Xilinx_Vivado_MCP/server.py` | `sha256:9fa66a0ca56389b73fb49cd17492306bf470f3d0b0964eb7fac0724c27b7d47b` | FROZEN |
| `Xilinx_Vivado_MCP/models.py` | `sha256:c7583ce79f4e8f0ff81e5376f24643369ca3701bbbfd4050b4dec114ef6c9a55` | FROZEN |
| `Xilinx_Vivado_MCP/requirements.txt` | `sha256:59f9f112b90ea7b1a4ec255972de0a673f3aecbb93a8f924cac1a8fe1f5e184f` | FROZEN |
| `.mcp.json` | `sha256:f48fc9a82bad9882f67fb80ae7f242a52512d3b904d5958d40b3222e84dc7736` | FROZEN |

---

## 2. B03 Key Deliverables

### 2.1 Production Code

| File | Purpose |
|------|---------|
| `mcps/common/board_profile.py` | Fail-closed board profile loader, draft/locked state machine, `expected_package_revision` contract |
| `mcps/common/board_package.py` | Package validation, SHA cross-references, semantic checks (DDR/QSPI/LED/clock), `freeze_package()` state machine, `FreezeCleanupError` |
| `mcps/common/env_probe.py` | Vivado/Vitis/XSCT discovery + version verification, USB-UART registry enumeration, `probe_all()` EnvReport |
| `mcps/conftest.py` | pytest fixture injection (ZYNQ_BOARD_PROFILE_DIRS + host_live/device_live markers) |

### 2.2 Test Files (B03 Created/Modified)

| File | Tests | Purpose |
|------|-------|---------|
| `mcps/common/tests/test_board_profile_validation.py` | 43 | Profile loading + schema validation |
| `mcps/common/tests/test_board_package.py` | 33 | Manifest validation, SHA checks, backward compat |
| `mcps/common/tests/test_env_probe.py` | 37 | EDA probing + USB-UART (mandatory + optional) |
| `mcps/common/tests/test_board_drift.py` | 22 | T-2xx drift detection (all precise ErrorCode/reason_code) |
| `mcps/common/tests/test_package_integration.py` | 17 | T-4xx integration: freeze lifecycle, concurrent freeze, recovery |
| `mcps/common/tests/test_env_probe_isolation.py` | 3 | cwd isolation, timeout process-tree kill, success cleanup |

---

## 3. B04 Planning Status

### 3.1 Planning Documents

| Document | Path | Version |
|----------|------|---------|
| B04 Implementation Plan | `docs/development/mcp/B04_pl_mcp_adapter_plan.md` | v0.2.2 |
| B04 Test Plan | `docs/development/tests/B04_pl_mcp_adapter_test_plan.md` | v0.2.2 |

Both are in planning state. **No B04 code has been written.** No B04 production files
have been created or modified.

v0.1 audit (Agent1, 2026-08-05): 2 P0 + 6 P1 found; implementation not approved.
v0.2 closes all P0/P1, adds: Artifact/operation/JTAG/manifest/ownership semantics.

### 3.2 12 PL Domain API Classification (v0.2.1 -- Post Hardware Audit)

Per B01 specification, these are the 12 PL domain APIs B04 must implement.
Classification updated after Agent1 line-level audit of old Vivado MCP
(vivado_tools.py, hw_tools.py, server.py).

Two independent dimensions (must NOT be conflated):
- **Implementation strategy**: how B04 adapts the old MCP
- **B02 behavior category**: query | set | command

| # | B01 API | Implementation Strategy | B02 Category | Old Vivado Tool(s) | Key Adaptation |
|---|---------|------------------------|-------------|---------------------|----------------|
| 1 | `pl.generate_system_top(wrapper_path)` | New implementation | `set` | None | Pure Python; writes to `generated/system_top.v`; atomic write; Vivado syntax gate |
| 2 | `pl.create_project(name, part, sources, constraints)` | Direct bridge | `set` | `create_project` | Fills `project_dir` from session context |
| 3 | `pl.set_top(module)` | Tcl wrapper | `set` | `run_tcl` | `set_property top {module} [current_fileset]` |
| 4 | `pl.synthesize()` | Direct bridge | `command` | `synth_design` | Returns operation_id immediately |
| 5 | `pl.place_and_route()` | Composite call | `command` | `place_design` + `route_design` | Single operation_id; place fail -> skip route |
| 6 | `pl.analyze_timing()` | Direct bridge | `query` | `report_timing_summary` | timing_met = (WNS >= 0 AND TNS == 0) |
| 7 | `pl.generate_bitstream(path)` | Direct bridge + Manifest | `command` | `write_bitstream` | Assembles + atomically publishes PL Build Manifest |
| 8 | `pl.connect_hw_server()` | Direct bridge | `set` | `connect_hw_server` | URL/cable from env vars, no API params |
| 9 | `pl.open_hw_target()` | Tcl wrapper | `set` | `run_tcl` | Cable auto-detect; fail-closed on 0 or >1 cables |
| 10 | `pl.select_device(id)` | New adapter | `set` | `run_tcl` + session state | 0-based index; stores stable string identity (name+part+idcode) |
| 11 | `pl.program(bitstream)` | Adapted bridge | `command` | `program_device` (restructured) | Uses session-bound device; no auto-select; per-call JTAG write lock |
| 12 | `pl.get_device_status()` | Adapted bridge | `query` | `get_device_status` (extended) | Returns DONE+INIT+IDCODE; per-call JTAG read lock |

**By implementation strategy**: 5 direct bridge + 1 composite + 2 Tcl wrapper + 1 new adapter + 2 adapted bridge + 1 new implementation = **12**

**By B02 category**: 2 query + 6 set + 4 command = **12** ✅

v0.1 claimed "8 direct + 2 Tcl + 2 new" -- this was incorrect on multiple counts.

### 3.3 Existing Vivado MCP Assets (Read-Only)

The old `Xilinx_Vivado_MCP` exposes 27 tools:

**VivadoTools** (17 tools): `close_design`, `connect_hw_server`, `get_capabilities`,
`get_clocks`, `get_device_status`, `get_ports`, `get_property`, `get_vivado_info`,
`open_checkpoint`, `place_design`, `program_device`, `report_utilization`,
`route_design`, `run_tcl`, `session`, `validate_design`, `write_bitstream`

**Server-level** (5 tools): `create_project`, `synth_design`, `compile_sim`,
`elaborate_sim`, `run_simulation` (plus `get_cells`, `get_nets`, `get_clocks`,
`list_serial_ports`, `open_checkpoint`, `connect_hw_server`, `program_device`)

**SimTools** (1): `parse_sim_log`

**HwTools** (1): `list_serial_ports`

Full inventory at `Xilinx_Vivado_MCP/server.py` lines 50-400.

Key reusable assets:
- `vivado_process.py` — Vivado subprocess lifecycle (long-lived Tcl session)
- `xsim_process.py` — XSim subprocess lifecycle
- `tcl_templates.py` — Tcl command generation
- `version_guard.py` — Vivado version compatibility
- `vivado_tools.py` — Build/query structured tools
- `sim_tools.py` — Simulation assertion parsing
- `hw_tools.py` — Hardware target tools

### 3.4 Bridge Design Decision (B02 Frozen)

Per B02 decision (§2 of `B02_common_contract_plan.md`):
- B04 adapts via **subprocess/stdio bridge**
- `zynq_pl` starts old Vivado MCP as a child process using MCP SDK
  `StdioServerParameters` + `ClientSession`
- Old Vivado MCP code is **never imported**, **never copied**, **never modified**
- B02 `ToolResponse` envelope wraps old MCP responses
- Bridge handles: start, forward, crash/restart, timeout, response wrapping

**Strict prohibition**: Do not `import Xilinx_Vivado_MCP` into `mcps/pl_mcp/`.
The bridge is a protocol adapter, not a code dependency.

### 3.5 Coexistence Principle

Old `vivado` MCP registration in `.mcp.json` is **preserved unchanged**.
`zynq_pl` is a **separate MCP server** registered alongside it. Agent2
sees `zynq_pl` in normal workflows. The old `vivado` registration remains
for direct diagnostics. Both servers can run simultaneously.

### 3.6 Lock Boundaries

| API Group | Lock Required | Key Pattern | Source |
|-----------|--------------|-------------|--------|
| Project write (create, synth, p&r, set_top, bitstream) | Project write lock | `project:<normpath>` | `mcps/common/project_lock.py` |
| Project read (timing, query) | Project read lock | Same | Same |
| HW connect/program | JTAG lock | `jtag:<url>:<serial>` | `mcps/common/jtag_lock.py` |

### 3.7 Test Plan

| Tier | Count | Gate | Description |
|------|-------|------|-------------|
| B04-T-0xx (Bridge) | 7 | Yes | Bridge start/stop, forward, wrap, crash, timeout |
| B04-T-1xx (Direct APIs) | 12 | Yes | Per-API bridging, lock, ToolResponse, operation_id |
| B04-T-2xx (system_top) | 4 | Yes | Verilog generation + compilation |
| B04-T-3xx (HW target) | 3 | No (device-live) | JTAG open/select/program |
| B04-T-4xx (Integration) | 5 | Yes | Full PL flow, B02/B03 regression |
| **Gate mandatory** | **28** | | |
| **Device-live optional** | **3** | | |

### 3.8 Existing Test & Hardware Assets

| Asset | Path | Notes |
|-------|------|-------|
| hello_fpga (golden PL project) | `hello_fpga/` | Verified breath_led build — T00 gold baseline |
| g9_hw_test | `g9_hw_test/` | PL hardware closed-loop validation |
| validation_projects | `validation_projects/` | 12 fault-injection designs |
| Old Vivado MCP tests | `Xilinx_Vivado_MCP/tests/` | test_golden, test_process, test_errors, test_protocol |
| AX7020 board | Physical | JTAG + UART available (not used in B04 gate) |

### 3.9 B04 Production Files — Currently NOT Created

B04 has NOT yet created or modified any of these planned files:
- `mcps/pl_mcp/vivado_bridge.py` (new)
- `mcps/pl_mcp/server.py` (must replace skeleton)
- `mcps/pl_mcp/tests/test_pl_domain.py` (new)
- `mcps/common/control_api.py` (must extend)
- No existing files in `Xilinx_Vivado_MCP/` modified
- No `.mcp.json` changes

---

## 4. Three Pending Decisions for B04 -- ALL DECIDED in v0.2

These were open in v0.1. Agent1 audited actual Vivado MCP code
(Xilinx_Vivado_MCP/session.py, vivado_process.py, vivado_tools.py,
hw_tools.py) and made final decisions. See B04 v0.2 plan for full rationale.

### D1: Old Vivado MCP Session Model -> **DECIDED: Per-session, lazy start**

Each `pl_mcp.create_session()` creates only B02 context. Old Vivado MCP
subprocess is started lazily on first domain API call. `close_session()`
terminates worker process tree. Session workers are isolated. Max 2
concurrent workers. See B04 v0.2 plan §6.

### D2: generate_system_top Implementation -> **DECIDED: Pure Python + Vivado mandatory syntax gate**

Pure Python reads BD wrapper .v, fail-closed parses module/port/direction/width/
escaped identifiers, generates deterministic system_top.v. Unrecognized syntax
is rejected (no guessing). Generated file must pass Vivado xvlog syntax check.
See B04 v0.2 plan §13 sub-step 3.

### D3: B04 Sub-Step Execution Order -> **DECIDED: Strictly sequential 0 -> 1 -> 2 -> 3 -> 4**

Sub-step 0 (inventory) -> 1 (bridge + worker + operation) -> 2 (build APIs +
Manifest) -> 3 (system_top + Platform binding) -> 4 (HW APIs + integration +
Agent2 gate). Sub-steps 2 and 3 are logically independent but executed
sequentially for single-threaded review. See B04 v0.2 plan §13.

---

## 5. Workspace State (Read-Only Record)

### 5.1 Repository Status

| Repository | HEAD | Modified | Untracked |
|-----------|------|----------|-----------|
| `D:\fpgaproject` (root) | **Not a Git repository** | — | — |
| `Xilinx_Vivado_MCP/` | `59f2abba` | 0 tracked | 8 untracked (`vivado.jou`, `vivado.log`, `_g10_prog.bat`, 5 test files) |
| `Xilinx_Vitis_MCP/` | `c3348660` | 0 | 0 |
| `zynq_platforms/` | `2f249761` | 1 tracked (`create_platform.tcl` CRLF modified) | ~73 untracked (build artifacts, temp scripts) |

### 5.2 mcps/ File Inventory

86 files total (source + cache). Key non-B02 files added in B03:
- `mcps/common/board_package.py`
- `mcps/common/env_probe.py`
- `mcps/conftest.py`
- `mcps/common/tests/test_board_drift.py`
- `mcps/common/tests/test_board_profile_validation.py` (replaced)
- `mcps/common/tests/test_board_package.py` (replaced)
- `mcps/common/tests/test_env_probe.py`
- `mcps/common/tests/test_env_probe_isolation.py`
- `mcps/common/tests/test_package_integration.py`

### 5.3 Board Package

```
boards/ALINX_AX7020_v1.0/
├── board_profile_ALINX_AX7020_v1.0.json   ← sha256:a7cb97a5...
├── package_manifest.json                    ← sha256:ca931987...  (LOCKED)
├── ps7_preset.tcl                           ← sha256:14222186...
├── board.xdc                                ← sha256:055a3aaa...
├── SOURCES.md                               ← sha256:62a1c2ea...
└── README.md                                ← sha256:8cf4cc70...
```

`manifest_revision`: `sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`

### 5.4 Tooling

| Tool | Path | Status |
|------|------|--------|
| Python | `C:\Users\zdx86\AppData\Local\Programs\Python\Python312\python.exe` | v3.12.9 |
| Vivado 2023.1 | `D:\Xilinx\Vivado\2023.1\bin\vivado.bat` | Installed, confirmed via Tcl mode |
| Vitis 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\vitis.bat` | Installed, confirmed via metadata |
| XSCT 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\xsct.bat` | Installed, confirmed via `-eval 'puts [version]'` |

### 5.5 .mcp.json

```json
{
  "mcpServers": {
    "vivado":         { "command": "...python.exe", "args": ["...server.py", "--log-level", "WARNING"] },
    "zynq_platform":  { "command": "python", "args": ["-m", "mcps.platform_mcp.server"] },
    "zynq_pl":        { "command": "python", "args": ["-m", "mcps.pl_mcp.server"] },
    "zynq_ps":        { "command": "python", "args": ["-m", "mcps.ps_mcp.server"] }
  }
}
```

SHA256: `sha256:f48fc9a82bad9882f67fb80ae7f242a52512d3b904d5958d40b3222e84dc7736`
(B02 frozen — never modified by B03)

---

## 6. Mandatory Reading Order for New Agent

1. **This document** — `docs/development/B03_to_B04_handoff.md`
2. `docs/brick_development_plan.md` — Brick status and gate rules
3. `docs/development/mcp/B04_pl_mcp_adapter_plan.md` — B04 implementation plan
4. `docs/development/tests/B04_pl_mcp_adapter_test_plan.md` — B04 test plan
5. `docs/development/skill/B01_standard_zynq_flow.md` — 12 PL API definitions (§7)
6. `docs/development/mcp/B02_completion_report.md` — B02 frozen baseline
7. `docs/development/mcp/B03_completion_report.md` — B03 frozen baseline
8. `Xilinx_Vivado_MCP/` — `README.md`, `server.py`, `models.py`, `vivado_tools.py`,
   `hw_tools.py`, `sim_tools.py`, `vivado_process.py`, `tcl_templates.py`
9. `mcps/pl_mcp/server.py` — Current skeleton (0 domain APIs)
10. `mcps/common/control_api.py` — `PL_CAPABILITIES` + `ToolDispatcher`

---

## 7. B04 Actions NOT Started

| Action | Status |
|--------|--------|
| B04 plan review and gate approval | NOT started |
| Sub-step 0 asset inventory | NOT started |
| Sub-step 1 Vivado bridge | NOT started |
| Sub-step 2 direct-bridge APIs | NOT started |
| Sub-step 3 generate_system_top | NOT started |
| Sub-step 4 HW + integration | NOT started |
| Create `mcps/pl_mcp/vivado_bridge.py` | NOT created |
| Replace `mcps/pl_mcp/server.py` skeleton | NOT modified |
| Extend `mcps/common/control_api.py` | NOT modified |
| Modify `.mcp.json` | NOT modified |
| Modify `Xilinx_Vivado_MCP/` any file | NOT modified |
| Full B02+B03+B04 regression | NOT run |

**B04 implementation has not started. B01/B02/B03 deliverables are frozen.**
