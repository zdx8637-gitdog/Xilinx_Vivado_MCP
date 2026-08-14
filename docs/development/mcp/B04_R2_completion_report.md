# B04 R2 Completion Report

> Brick: B04 R2 | Date: 2026-08-06 | Status: **COMPLETE / FROZEN**
> Previous: B04 R1 (FROZEN, 89 tests) | Next: B04 R3 (PL Domain API)

## 1. Summary

B04 R2 将现有 Vivado Bridge 迁移为统一 zynq_mcp 内部的 PL Adapter。
SingleWorkerController 作为唯一 Worker 生命周期所有者，接入 Execution Ledger、
Instance Guard、Process Guard、Preflight/Recovery。全局单 Worker，无 pool，无
per-session Worker，无自动 rebuild。

## 2. 交付能力

| Capability | Status | Tests |
|------------|--------|-------|
| SingleWorkerController.ensure_worker() | IMPLEMENTED_AND_TESTED | R201, R202, concurrent |
| PID capture via SDK hook | IMPLEMENTED_AND_TESTED | R201, R202 |
| Fake MCP deterministic tool call | IMPLEMENTED_AND_TESTED | R203, R206 |
| Crash → OUTCOME_UNKNOWN + RECOVERY_REQUIRED | IMPLEMENTED_AND_TESTED | R204 |
| Timeout → VIVADO_TIMEOUT + PID killed + auto_retry=0 | IMPLEMENTED_AND_TESTED | R205 |
| close_session via ZynqDispatcher (CLOSING lane) | IMPLEMENTED_AND_TESTED | R213, R216 |
| close_session double-lease release (Project→JTAG order) | IMPLEMENTED_AND_TESTED | R213, R214, R215 |
| CLOSING deterministic concurrency | IMPLEMENTED_AND_TESTED | R216 |
| heartbeat_once() 5-field identity verification | IMPLEMENTED_AND_TESTED | R217-R228 |
| Heartbeat BUSY preservation | IMPLEMENTED_AND_TESTED | R218 |
| Heartbeat shutdown failure chain | IMPLEMENTED_AND_TESTED | R229 |
| Persist RECOVERY_REQUIRED on shutdown failure | IMPLEMENTED_AND_TESTED | R229, R230 |
| Persist failure → no owner lock release | IMPLEMENTED_AND_TESTED | R230 |
| Server exit with no Worker (production subprocess) | IMPLEMENTED_AND_TESTED | R231 |
| HeartbeatResult.ledger_persisted extraction | IMPLEMENTED_AND_TESTED | R217-R228 |
| _crash_persisted() shared helper — component-level解析验证, NOT real Ledger 写失败注入 | IMPLEMENTED_AND_TESTED | R232 |
| Workspace root resolution | IMPLEMENTED_AND_TESTED | R208, R209, R210 |

## 3. Test Statistics

```
zyng_mcp/tests/ total: 124 collected
  R1: 89 collected (unchanged, FROZEN)
  R2: 35 collected

Full regression: 566 collected, 565 passed, 1 skipped, 0 failed
```

### R2 test breakdown by tier

| Tier | Count | Tests |
|------|-------|-------|
| production | 6 | R213, R214, R215, R216, R231, R216 |
| component | 24 | R201-R206, R212, R217-R230, R232, capability, concurrent, controller |
| mock | 3 | R208, R209, R210 |
| static | 2 | R207, R211 |

### R2 test ID mapping

| ID | Scenario | Tier | Heartbeat fields |
|----|----------|------|-------------------|
| R201 | start via controller | component | — |
| R202 | PID capture | component | — |
| R203 | fake MCP ping | component | — |
| R204 | crash → OUTCOME_UNKNOWN | component | — |
| R205 | timeout → VIVADO_TIMEOUT | component | — |
| R206 | context_ref | component | — |
| R207 | server path | static | — |
| R208 | workspace root | mock | — |
| R209 | zero candidates | mock | — |
| R210 | ambiguous | mock | — |
| R211 | .mcp.json not read | static | — |
| R212 | real MCP handshake | component | — |
| R213 | close_session double lease | production | — |
| R214 | Project lease release fail | production | — |
| R215 | JTAG lease release fail | production | — |
| R216 | CLOSING concurrency | production | — |
| R217 | all 5 fields match | component | pid,start,exe,gen,iid |
| R218 | BUSY preserved | component | (state check) |
| R219 | pid missing | component | pid→None |
| R220 | start_time missing | component | start→None |
| R221 | executable missing | component | exe→None |
| R222 | generation missing | component | gen→None |
| R223 | instance_id missing | component | iid→None |
| R224 | pid mismatch | component | pid=99999 |
| R225 | start_time mismatch | component | start-100s |
| R226 | executable mismatch | component | exe=/wrong |
| R227 | generation mismatch | component | gen=99 |
| R228 | instance_id mismatch | component | iid=wrong |
| R229 | shutdown→finalizer chain | component | — |
| R230 | persist fail→no lock | component | — |
| R231 | server exit no worker | production | — |
| R232 | _crash_persisted helper 解析验证 (NOT real Ledger 写失败注入) | component | — |

## 4. Key Design Decisions

- `heartbeat_once()` — public production method. All 5 identity fields MUST exist;
  any missing → WORKER_IDENTITY_MISSING; any mismatch → precise reason_code.
- `_crash_persisted()` — shared helper extracts ledger_persisted from _do_crash()
  return value. No hard-coded True/False.
- States BUSY/POISONED/DEAD/STARTING/ABSENT/ORPHANED/UNRESPONSIVE/STOPPING
  are never overwritten to READY by heartbeat.
- `_server_finalizer()` — unified exit: Worker shutdown → persist if failed →
  release owner lock (only if persist succeeded).
- CLOSING lane: all commands read fresh ledger, reject CLOSING with CHANNEL_CLOSING.

## 5. Modified Files

| File | Change |
|------|--------|
| `mcps/zynq_mcp/control/single_worker.py` | heartbeat_once(), _crash_persisted(), _stop_heartbeat() |
| `mcps/zynq_mcp/server.py` | _server_finalizer(), _persist_shutdown_failure() returns bool |
| `mcps/zynq_mcp/dispatcher.py` | CLOSING lane, _close_session_atomic(), _release_session_leases() |
| `mcps/zynq_mcp/control/execution_ledger.py` | +EXECUTION_LANE_CLOSING |
| `mcps/common/project_lock.py` | +list_leases_for_owner(), +release_lease_safe() (Erratum E002) |
| `mcps/zynq_mcp/adapters/vivado_adapter.py` | ShutdownResult.cleanup_errors propagation |
| `mcps/zynq_mcp/tests/test_r2_adapter.py` | 35 R2 tests |

## 6. Frozen Assets

| Asset | SHA256 | Status |
|------|--------|--------|
| `.mcp.json` | `f48fc9a82bad9882...` | unchanged |
| `Xilinx_Vivado_MCP/server.py` | `9fa66a0ca56389b7...` | unchanged |
| `CLAUDE.md` | `a53f1935c0053b1...` | unchanged |

## 7. Known Exceptions (P2, R1 frozen)

| File | Line | Pattern | Risk |
|------|------|---------|------|
| `instance_guard.py` | 132 | `except Exception: pass` (UnlockFile cleanup) | P2 |
| `session.py` | 81 | `except Exception: pass` (B02 rollback) | P2 |

## 8. Declarations

- **B04 R2 = COMPLETE / FROZEN**
- **B04 R3 = 未实现** (12 PL 领域 API)
- **Agent2 = 未调用**
- Full regression: 566 collected, 565 passed, 1 skipped, 0 failed
