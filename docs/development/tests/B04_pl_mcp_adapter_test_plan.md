# B04 — Unified Zynq MCP Test Plan v0.3.2

> Brick: B04  |  日期: 2026-08-05  |  状态: **规划中 — R1 实现尚未开始**
> 关联: `B04_pl_mcp_adapter_plan.md` v0.3.2, `B04_single_channel_audit.md` v0.3.2

## 1. Scope

Tests for: unified zynq_mcp skeleton, dual OS locks, secondary takeover,
execution ledger, preflight gate, single worker, process guard, PL adapter migration,
PL domain API, serial workflow stage enforcement, close_session without implicit cancel,
and B02/B03 regression.

## 2. Test Tiers

| Tier | Requires | Gate |
|------|----------|------|
| Process/mock | Python only, mocked Vivado MCP | **Yes** |
| Stdio black-box | Real old MCP subprocess | **Yes** |
| Host-live | Vivado 2023.1 | **Yes** |
| Device-live | JTAG + AX7020 board | **No** (deferred to B08) |

## 3. Test Matrix

### 3.1 R1xx: Skeleton + Session + Ledger + Preflight + Instance Guard — 30 tests

| ID | Scenario | Tier |
|----|----------|------|
| R101 | `zynq_mcp/server.py` starts + MCP SDK handshake succeeds | Stdio |
| R102 | `create_session` creates ZynqContext (composition: base MCPContext) | Mock |
| R103 | Two `create_session` calls share same execution channel (same runtime_root) | Mock |
| R104 | `get_session_info` returns context: stage + revisions + worker_generation | Mock |
| R105 | `get_capabilities` returns domains grouped + instance_role + implemented count | Mock |
| R106 | `list_tools` = 9 tools; count matches `get_capabilities().implemented`; 0 NOT_IMPLEMENTED | Mock |
| R107 | Each of the 9 control tools has real behavior (call + verify response not "not implemented") | Mock |
| R108 | `instance_owner.lock` and `ledger.lock` are separate files with different purposes | Mock |
| R109 | Primary holds `instance_owner.lock` for full server lifetime; multiple ledger writes succeed while owner lock held | Mock |
| R110 | Primary: ledger RMW under `ledger.lock` exclusive (acquire→write→release, short-held) | Mock |
| R111 | Secondary: reads ledger under `ledger.lock` shared (acquire→read→release) | Mock |
| R112 | Secondary: `create_session`/`close_session`/all set/command → `INSTANCE_ALREADY_RUNNING` + primary info | Mock |
| R113 | Secondary: does NOT create EDA Worker process | Mock |
| R114 | Ledger: atomic RMW → close MCP → restart → state recovered correctly | Stdio |
| R115 | Ledger: crash during .tmp write (kill process mid-write) → old complete ledger intact | Mock |
| R116 | Ledger: `ledger_sequence` increments monotonically on each successful write | Mock |
| R117 | Ledger: `os.replace` used, not `os.rename` (allows overwrite for mutable state) | Mock |
| R118 | Preflight P1: active operation → `CHANNEL_BUSY` + structured busy response | Mock |
| R119 | Preflight P5: heartbeat stale (>60s) → `WORKER_UNRESPONSIVE` | Mock |
| R120 | Preflight P6: previous `OUTCOME_UNKNOWN` → `PREVIOUS_OPERATION_UNRESOLVED` | Mock |
| R121 | Preflight P7: synthesis not SUCCEEDED → place_and_route blocked (`STAGE_PREREQUISITE_UNMET`) | Mock |
| R122 | Preflight P7: legal ROLLBACK_FIX (PL_TIMING fail → PL_BUILD → re-synthesize) ALLOWED | Mock |
| R123 | Preflight P7: illegal skip (PL_BITSTREAM → CONSISTENCY_CHECK without PS_BUILD) REJECTED | Mock |
| R124 | Preflight P7: illegal skip (PLATFORM_DESIGN → PL_BUILD without PL_GENERATE) REJECTED | Mock |
| R125 | Dedup: same request with RUNNING operation → `existing_operation_id` + `deduplicated: true` + call count still 1 | Mock |
| R126 | Dedup: same request with TERMINAL operation → `CONFIRM_RETRY_REQUIRED` (no auto-replay) | Mock |
| R127 | Conflict: different request but resource occupied → `CHANNEL_BUSY` | Mock |
| R128 | Lane: `TIMED_OUT` → `RECOVERY_REQUIRED` (NOT `IDLE`) | Mock |
| R129 | Lane: `OUTCOME_UNKNOWN` blocks all domain set/command calls | Mock |
| R130 | `recover_execution`: all 7 preconditions satisfied → Lane `IDLE`; recovery_log written | Mock |

### 3.2 R2xx: PL Adapter Migration — 15 tests

| ID | Scenario | Tier |
|----|----------|------|
| R201 | Adapter: BridgeOwner starts via SingleWorkerController (not standalone) | Stdio |
| R202 | Adapter: PID captured via SDK hook (migrated from test_t001_t002) | Stdio |
| R203 | Adapter: tool call forwarded; B02 ToolResponse envelope returned | Mock |
| R204 | Adapter: crash → Worker POISONED → Operation `OUTCOME_UNKNOWN` | Mock |
| R205 | Adapter: timeout → worker process tree killed; PID verified gone | Mock |
| R206 | Adapter: `context_ref=session_id` in B02 ToolResponse envelope | Mock |
| R207 | Adapter: server path resolved from `resolve_workspace_root()` (not hardcoded) | Mock |
| R208 | `resolve_workspace_root()`: returns canonical `D:\fpgaproject` (exact path assertion) | Mock |
| R209 | `resolve_workspace_root()`: fail-closed — zero candidates → `WorkspaceNotFoundError` | Mock |
| R210 | `resolve_workspace_root()`: fail-closed — ambiguous candidates → `WorkspaceAmbiguousError` | Mock |
| R211 | Adapter: `.mcp.json` not read or written during bridge lifecycle | Mock |
| R212 | Adapter: real MCP handshake → list_tools=27 → get_capabilities → shutdown clean | Stdio |
| R213 | Adapter: `close_session` cleanup order: operations → worker → leases → context | Mock |
| R214 | Adapter: shutdown PID verified not alive after natural shutdown | Stdio |
| R215 | Adapter: auto-rebuild count = 0 (query-stateless does NOT create new Worker process) | Mock |

### 3.3 R3xx: PL Domain API — 11 tests

| ID | Scenario | Tier |
|----|----------|------|
| R301 | `pl_generate_system_top`: produces valid Verilog output | Mock |
| R302 | `pl_generate_system_top`: instantiates BD wrapper by correct module name | Mock |
| R303 | `pl_generate_system_top`: port direction (input/output/inout) preserved | Mock |
| R304 | `pl_generate_system_top`: bus width preserved (e.g., [7:0], [31:0]) | Mock |
| R305 | `pl_generate_system_top`: escaped identifiers (\\...\\ ) handled without corruption | Mock |
| R306 | `pl_generate_system_top`: malformed wrapper → fail-closed, no guessing | Mock |
| R307 | `pl_generate_system_top`: deterministic output (same inputs → byte-identical) | Mock |
| R308 | `pl_generate_system_top`: Platform Manifest binding — single match binds | Mock |
| R309 | `pl_generate_system_top`: Platform Manifest binding — zero/multiple/board-mismatch → fail | Mock |
| R310 | PL domain: `list_tools` count incremented when PL APIs registered | Mock |
| R311 | PL domain: `get_capabilities` `domains.pl.implemented` field incremented | Mock |

### 3.4 R4xx: Integration & Regression — 15 tests

| ID | Scenario | Tier |
|----|----------|------|
| R401 | Full unified flow: create_session → PL domain API → real handshake | Stdio |
| R402 | `close_session` with active operation (RUNNING) → `CHANNEL_BUSY` + `ACTIVE_OPERATION_PRESENT`; task NOT cancelled; worker NOT closed | Mock |
| R403 | `close_session` with ACCEPTED/RUNNING → `CHANNEL_BUSY` + `ACTIVE_OPERATION_PRESENT`; task NOT cancelled; worker NOT closed; context NOT deleted; ledger NOT written CANCELLED | Mock |
| R404 | Same request RUNNING → `deduplicated: true`; actual call count = 1 (not 2) | Mock |
| R405 | B02+B03 regression: `mcps/common/tests/` → 367 passed, 1 skipped, 0 new failures | Mock |
| R406 | Three old MCP skeletons still functional (not broken by zynq_mcp existence) | Stdio |
| R407 | Old `mcps/pl_mcp/` Sub-step 1 tests still pass (54 tests) | Stdio |
| R408 | `resolve_workspace_root()`: different `os.getcwd()` → same result (does not depend on cwd) | Stdio |
| R409 | `resolve_workspace_root()`: different `project_path` (session property) → same workspace root → shared execution channel | Mock |
| R410 | Primary crash → Secondary detects owner lock released → takeover → Ledger has old RUNNING → `RECOVERY_REQUIRED` | Stdio |
| R411 | Takeover: old worker PID alive → worker.state = ORPHANED; active_operation → OUTCOME_UNKNOWN; Lane → RECOVERY_REQUIRED; [owner lock NOT released; system NOT left unowned] | Mock |
| R412 | Takeover: old active operation RUNNING + worker dead → operation → INTERRUPTED/OUTCOME_UNKNOWN; Lane → RECOVERY_REQUIRED | Mock |
| R413 | Takeover: no active operation (idle) → safe takeover; new Primary | Mock |
| R414 | PID alive + no progress evidence → `operation_progress_state` = UNKNOWN; `outcome_confidence` = NONE | Mock |
| R415 | `wait_operation`: returns on SUCCEEDED within timeout | Mock |

### 3.5 R5xx: Agent2 Black-Box Gate — 14 tests

| ID | Scenario | Tier |
|----|----------|------|
| R501 | Agent2 discovers unified zynq capabilities (domains grouped by platform/pl/ps/control/observation/recovery) | Stdio |
| R502 | Agent2 creates session, verifies unified ZynqContext fields (stage, revisions, generation) | Stdio |
| R503 | Agent2 calls all available domain APIs, verifies ToolResponse envelope for each | Stdio |
| R504 | Agent2 verifies command → operation_id → get_operation_status lifecycle | Stdio |
| R505 | Agent2 verifies `wait_operation` with timeout → returns `still_running` or completed result | Stdio |
| R506 | Agent2 verifies preflight: duplicate request → `deduplicated: true` + `existing_operation_id` | Stdio |
| R507 | Agent2 verifies preflight: active operation → structured busy response (all fields present) | Stdio |
| R508 | Agent2 verifies instance guard: secondary MCP instance rejected for set/command | Stdio |
| R509 | Agent2 verifies takeover: Primary crash → Secondary becomes Primary → `RECOVERY_REQUIRED` | Stdio |
| R510 | Agent2 uses public fixture: `pl_generate_system_top` → valid output | Host-live |
| R511 | Agent2 closes session, verifies cleanup (no zombie processes, locks released) | Stdio |
| R512 | [DEFERRED to C4/B09] Final `.mcp.json` has only `zynq` entry — B04 validates migration plan statically; .mcp.json NOT modified in R4 | — |
| R513 | [DEFERRED to C4/B09] `list_tools` contains NO bypass entry — B04 cannot modify the frozen `.mcp.json`; C4 verifies final product config | — |
| R514 | Agent2 verifies: `get_capabilities().implemented` count == `list_tools` count == actual handler count (mechanical consistency) | Stdio |

## 4. Test Count Summary

| Series | Count | Mandatory | Mock | Stdio | Host-live |
|--------|-------|-----------|------|-------|-----------|
| R1xx | 30 | 30 | 28 | 2 | 0 |
| R2xx | 15 | 15 | 11 | 4 | 0 |
| R3xx | 11 | 11 | 11 | 0 | 0 |
| R4xx | 15 | 15 | 10 | 3 | 0 |
| R5xx | 14 (12 active + 2 deferred) | 12 (+2 def.) | 0 | 11 | 1 (+1 Agent2) |
| **Active total** | **83** | **83** | **60** | **20** | **1 (+3)** |
| **Deferred to C4** | **2** | — | — | — | — |

**Mechanical verification**: 30+15+11+15+14 = 85 ✅
**Tier verification**: 60+21+1+3 = 85 ✅

## 5. Existing Sub-step 1 Test Disposition (unchanged from v0.3.1)

| Category | Count | Tests |
|----------|-------|-------|
| Keep (migrate to new path) | 20 | PathMCPJson(4) + ParseConvert(9) + shutdown_cleaned + timeout(2) + context_ref + t014_lazy + EnvCwd(4) + CloseOrder(2) + server_not_found + Tombstone(2) + RealMCP + t022 |
| Adapt (modify, keep purpose) | 15 | t001_t002 + t004 + submit_command(4) + t013_busy + close_session(3) + t021 + t024 + list + t027 + t028 |
| Discard (no equivalent) | 8 | test_t012_two + concurrent_start_two + t019_max_workers + auto_retry(2) + rebuild(2) + deterministic_race |
| NA / Unrelated | 2 | test_t025_t026 |
| Total | **54** | 20+15+8+2+9 = 54 ✅ |

## 6. Key Machine-Decidable Conditions

| # | Condition | Test |
|---|-----------|------|
| 1 | `instance_owner.lock` and `ledger.lock` are separate files | R108 |
| 2 | Primary holds owner lock for lifetime; multiple ledger writes succeed | R109 |
| 3 | Secondary reads ledger under shared lock | R111 |
| 4 | Primary crash → Secondary detects owner lock released → takeover | R410 |
| 5 | Takeover: old RUNNING → `RECOVERY_REQUIRED` | R412 |
| 6 | `TIMED_OUT` → `RECOVERY_REQUIRED` (NOT `IDLE`) | R128 |
| 7 | `close_session` does NOT cancel active task (CHANNEL_BUSY; task still running; worker alive) | R402, R403 |

| 9 | Same request RUNNING → `existing_operation_id` + call count still 1 | R404 |
| 10 | `PL_BITSTREAM` → must enter `PS_BUILD` (not skip to `CONSISTENCY_CHECK`) | R123 |
| 11 | `resolve_workspace_root()` → exact `D:\fpgaproject` (not just "contains mcps") | R208 |
| 12 | Every R1 tool has real behavior (not `NOT_IMPLEMENTED`) | R107 |
| 13 | `get_capabilities().implemented` == `list_tools` count == handler count | R106, R514 |
| 14 | Final `.mcp.json` has only `zynq` entry (no vivado, no zynq_*) | R513 |
| 15 | Final `list_tools` has no bypass entry for old execution tools | R512 |
| 16 | B02+B03 regression: 367 passed, 1 skipped, 0 new failures | R405 |

## 7. B02/B03 Regression Baseline

```
mcps/common/tests/ — 367 passed, 1 skipped (B02+B03 combined, FROZEN, verified 2026-08-05)
```

| Artifact | Expected | Status |
|----------|---------|--------|
| B02+B03 common tests | 367 passed / 1 skipped | ✅ |
| Board Package | `manifest_revision: sha256:72191212...` | ✅ FROZEN |
| `.mcp.json` | SHA256: `f48fc9a8...` | ✅ 未修改 |
| `mcps/common/context.py` (B02 frozen) | 未修改 | ✅ |

## 8. Declaration

**统一 Zynq MCP 实现尚未开始。R1 尚未开始。所有测试 ID 和数量为规划值。**
