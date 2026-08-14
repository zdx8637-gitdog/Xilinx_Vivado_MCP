# B04 R3.0 → R3.1 White-Box Handoff

> Date: 2026-08-06 | To: New context Agent1 (white-box implementer)
> Status: **R3.0 COMPLETE / FROZEN | R3.1 NOT STARTED**

---

## 1. Quick Recovery (READ FIRST)

### Project Goal

AI Agent-driven Zynq-7020 (ALINX AX7020, `xc7z020clg400-2`) full-flow FPGA development.
Claude Code operates Vivado/XSim/Vitis via MCP servers using the
"three-domain, four-layer" architecture.

### Product Architecture

```
Single unified zynq_mcp server (mcps/zynq_mcp/)
  └── Single instance (instance_guard.py)
  └── Single execution channel (domain_runner.py)
  └── Execution Ledger = persistent source of truth (execution_ledger.py)
  └── All commands serialized, preflight-gated, fail-fast
  └── NO three-parallel-MCP; three legacy skeletons exist only as B02 historical baseline
```

### Roles

- **Agent1** (you): white-box implementer, long context, plans/implement/tests/documents
- **Agent2**: black-box acceptance agent, fresh context, runs only what gets exposed publicly

### Current Progress

| Brick | Status | Tests |
|-------|--------|-------|
| B00–B03 | COMPLETE / FROZEN | — |
| B04 R1 | COMPLETE / FROZEN | 89 |
| B04 R2 | COMPLETE / FROZEN | 35 |
| B04 R3.0 | COMPLETE / FROZEN | 36 |
| B04 R3.1–R3.5 | NOT STARTED | 0 |

`list_tools = 9`. PL handler count = 0. Agent2 never called.

### Your First Round

**Read-only recovery + R3.1 readiness audit.** Do NOT implement in the same
round you recover context. Output an audit before writing any R3.1 code.

### Forbidden

- Don't overturn frozen conclusions (B00–B03, R1, R2, R3.0).
- Don't enter R3.2.
- Don't call Agent2.
- Don't modify old `Xilinx_Vivado_MCP/`.
- Don't modify `.mcp.json`.

---

## 2. Mandatory Reading Order

1. `CLAUDE.md` (auto-loaded by Claude Code; confirm the rules on pseudo-tests and mechanical stats)
2. **This file** (`docs/development/B04_R3_0_to_R3_1_handoff.md`)
3. `docs/brick_development_plan.md` (Brick status index)
4. `docs/development/mcp/B04_R3_implementation_plan.md` (v0.3.1, 12 API mapping tables)
5. `docs/development/tests/B04_R3_test_plan.md` (v0.3.1, formal R313-R321)
6. `docs/development/mcp/B04_R2_completion_report.md`
7. `docs/development/B03_to_B04_handoff.md`
8. `mcps/zynq_mcp/control/domain_runner.py` (R3.0 — 722 lines)
9. `mcps/zynq_mcp/control/operation_service.py` (R3.0 — 125 lines, **modified** ACCEPTED transitions)
10. `mcps/zynq_mcp/control/operation_registry.py` (R3.0 — 169 lines, **modified** ACCEPTED transitions + `admit_cache`/`remove_cache`/`has_task`/`task_count`/`shutdown_tasks`)
11. `mcps/zynq_mcp/control/single_worker.py` (R2, heartbeat lifecycle)
12. `mcps/zynq_mcp/adapters/vivado_adapter.py` (R2, old Vivado MCP bridge)
13. `mcps/zynq_mcp/dispatcher.py` (R2, CLOSING lane, lease release)
14. `mcps/zynq_mcp/tests/test_r3_runner.py` (R3.0 — 716 lines, 36 tests)

`CLAUDE.md` rules on "test truthfulness, mechanical statistics, forbid report inflation" are binding. Read the exact text.

---

## 3. Brick vs. R-Substep

- **Brick** = project phase (B04 = "Unified zynq_mcp").
- **R** = implementation sub-step within a brick (R1, R2, R3.0, R3.1…).
- R3.0 is NOT "Brick 30." R3.1 is the next small step within B04.
- Execution Ledger serialization = product runtime behavior.
- Brick sequential gate = development process management.
- These two notions of "serial" are independent.

---

## 4. R1 Frozen Capability Summary

R1 (`mcps/zynq_mcp/control/*`, 89 tests):

- Single-instance Instance Guard (owner.lock + ledger.lock)
- Second instance exits immediately with structured diagnostic
- Execution Ledger (JSON, atomic RMW, os.replace)
- Session/Context (ZynqContext; composition over B02 MCPContext)
- Operation state machine (ACCEPTED/RUNNING/SUCCEEDED/FAILED/TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN/CANCELLED)
- `wait_operation` with bounded poll loop
- P1–P10 Execution Gate (CHANNEL_BUSY, dedup, PID, identity, heartbeat, stage, board revision, resources)
- `diagnose_execution` / `recover_execution`
- Package Lock (B03 crash safety)
- 9 public control APIs (`create_session` through `recover_execution`)

---

## 5. R2 Frozen Capability Summary

R2 (`vivado_adapter.py`, `single_worker.py`, 35 tests):

- `VivadoAdapter` + `VivadoBridge` — stdio MCP SDK subprocess to old `Xilinx_Vivado_MCP`
- `SingleWorkerController` — sole lifecycle owner for the global EDA Worker
- Real subprocess PID capture via SDK hook
- Heartbeat (30s interval, identity verification, BUSY preservation)
- Crash → OUTCOME_UNKNOWN + RECOVERY_REQUIRED
- Timeout → VIVADO_TIMEOUT + RECOVERY_REQUIRED
- Shutdown → PID verification + ABSENT ledger write
- No auto-rebuild/retry
- Worker identity fields: pid, process_start_time, executable_path, worker_generation, instance_id

---

## 6. R3.0 Frozen Contract Summary

### 6.1 DomainExecutionMutex

`domain_runner.py:95-110` — **Synchronous** `try_acquire()`. No await.
Shared by CommandRunner, SetRunner, QueryRunner.
Second request → immediate CHANNEL_BUSY (not queued).
Busy response includes `active_category`, `active_tool_name`, `elapsed_s`, `poll_after_s`.

### 6.2 Command Runner

`CommandRunner.run_command()`:
1. `try_acquire` mutex → fail-fast
2. `ledger_transaction` containing: signature computation (from transaction-internal stage/rev), P10 dedup, P1-P9 shared preflight, ACCEPTED
3. `admit_cache` / `ensure_future(coro)` / `register_task` under mutex
4. Background `_execute`: ACCEPTED→RUNNING→terminal
5. No auto-retry. `lp=True` syncs; `lp=False` writes; non-bool `lp` → INCONSISTENT

### 6.3 Set Runner

`SetRunner.run_set()`:
- Mutex → preflight + BUSY in one transaction → worker call → result
- Success → IDLE
- Worker error/timeout/crash → RECOVERY_REQUIRED (not IDLE)

### 6.4 Query Runner

`QueryRunner.run_query()`:
- Mutex → `ledger_read_shared` → preflight → worker call → release
- Zero ledger_sequence bump
- Worker absent → ADAPTER_NOT_READY

### 6.5 Shared Preflight

`_shared_preflight_check()` (domain_runner.py:179-255):
- Session ID must match ledger exactly
- Active worker state: ALL identity fields validated (pid=int>0, start_time=finite>0, exe=nonempty, gen=int>=0, iid=nonempty, heartbeat=valid timestamp)
- worker.instance_id must match primary_instance_id (missing/empty owner → fail-closed)
- P1 active op, P2 dead PID, P3 identity mismatch, P4 generation stale, P5 heartbeat unresponsive, P6 unresolved previous, P7 invalid stage, P8 board revision, P9 JTAG lease

### 6.6 State Machine Changes

`operation_service.py:66` — ACCEPTED now accepts: `RUNNING, FAILED, CANCELLED, INTERRUPTED, OUTCOME_UNKNOWN` (added FAILED/INTERRUPTED/OUTCOME_UNKNOWN for task-admission failure rollback).

`operation_registry.py:18` — same in-memory `_LEGAL` sync.

New public methods: `admit_cache()`, `remove_cache()`, `has_task()`, `task_count()`, `shutdown_tasks()`.

### 6.7 PL_API_CONTRACTS

`domain_runner.py:718-740` — single source of truth: 12 entries, command=9/set=2/query=1. Import-time assertion validates invariants. R3.1–R3.5 must reuse this, not hand-write duplicate lists.

### 6.8 derive_project_dir()

`domain_runner.py:743-756` — production function. Rejects ".", "..", "", "a/b", absolute paths.

---

## 7. R3.0 Frozen SHA256

**Must mechanically re-verify before proceeding.**

| File | SHA256 |
|------|--------|
| `domain_runner.py` | `5fffcf23ac45d0b5c048c726c3441cdae96161750e7c861e7c6dfe386bbebe43` |
| `operation_service.py` | `30630bd679dc15e256266e428410f9f7a673b68995f7bf265d8ce09d381e4560` |
| `operation_registry.py` | `57a9375f6f043be7eed4a8fe7728096745de4e0f6c770f46a57bd974a9499b4c` |
| `test_r3_runner.py` | `32ae422309f28f0c21fa8bfff7f47b1c4f61ab930d76ea699d550821fbc82cc3` |

**If your sha256sum output differs from above → STOP. Report conflict. Do not update baseline.**

---

## 8. R3.1 Strict Scope

From `B04_R3_implementation_plan.md` v0.3.1 and `B04_R3_test_plan.md` v0.3.1:

### Allowed (R3.1 only)

- Implement `pl_generate_system_top(wrapper_path)` in `mcps/zynq_mcp/domains/pl/system_top.py`
- Register as a local command (no Vivado needed) via `CommandRunner.run_command(executor="local")`
- Update Progressive Capability: `list_tools` changes from 9 → 10
- Tests R313–R321 (all Mock/Component, no Vivado)
- Populate `mcps/zynq_mcp/tests/fixtures/b04_pl_ready/` with synthetic test files

### Forbidden (all deferred to R3.2+)

- `pl_create_project`, `pl_synthesize`, `pl_place_and_route`, `pl_analyze_timing`
- Any connection to real Vivado/VivadoAdapter
- JTAG APIs
- PL Build Manifest
- 12 APIs registered as implemented
- NOT_IMPLEMENTED placeholder handlers
- Modifying old `Xilinx_Vivado_MCP/`

---

## 9. pl_generate_system_top Contract

| Field | Value |
|-------|-------|
| Public signature | `pl_generate_system_top(wrapper_path: str)` |
| Category | command |
| Executor | local (no Worker, no Vivado) |
| `wrapper_path` source | Platform Manifest (passed by caller, not read from Context) |
| Input validation | wrapper_path is file, exists, within project, SHA256 matches Platform Manifest bd_wrapper_sha256 |
| Output file | `{project_path}/rtl/system_top.v` (deterministic location) |
| Determinism | Same wrapper_path twice → byte-identical output |
| Verilog handling | Parse module name, ports, direction (input/output/inout), bus width ([7:0], [31:0]), escaped identifiers (\\...\\ ) |
| Platform Manifest binding | Single match binds; zero/multiple/board-mismatch → fail |
| Failure | FAILED + IDLE (deterministic error: malformed wrapper, file not found, manifest mismatch) |
| Stage pre | PLATFORM_DESIGN SUCCEEDED |
| Stage post | PL_BUILD |
| Artifact | Not in R3.1 (source_files entry deferred to R3.3 Manifest) |

### Undecided (audit by new Agent1)

- Whether `project_path` comes from Session Context or caller argument
- Exact Stage name for "PLATFORM_DESIGN SUCCEEDED" (verify against execution_gate._check_stage)
- Whether the Platform Manifest file itself needs to be in Session Context, or is injected per-call

---

## 10. R3.1 Formal Test Matrix (R313–R321)

From `B04_R3_test_plan.md` v0.3.1:

| ID | Scenario | Entry | Tier | Assertion |
|----|----------|-------|------|-----------|
| R313 | Valid BD wrapper → deterministic system_top.v output | `CommandRunner.run_command` | Mock | `OP_SUCCEEDED+IDLE`, byte-identical on second call |
| R314 | Same wrapper twice → byte-identical output | `CommandRunner.run_command` | Mock | Both outputs sha256 equal |
| R315 | Instantiates BD wrapper by correct module name | `CommandRunner.run_command` | Mock | Output contains correct instantiation |
| R316 | Port direction (input/output/inout) preserved | `CommandRunner.run_command` | Mock | Output port list matches |
| R317 | Bus width preserved (e.g., [7:0], [31:0]) | `CommandRunner.run_command` | Mock | Width annotation present |
| R318 | Escaped identifiers (\\...\\ ) handled without corruption | `CommandRunner.run_command` | Mock | Escape preserved |
| R319 | Malformed wrapper (missing module port) → fail-closed | `CommandRunner.run_command` | Mock | `OP_FAILED+IDLE` |
| R320 | Platform Manifest: single match binds | `CommandRunner.run_command` | Mock | Success |
| R321 | Platform Manifest: zero/multiple matches → fail | `CommandRunner.run_command` | Mock | `OP_FAILED+IDLE` |

R313-R321 require fixture files in `mcps/zynq_mcp/tests/fixtures/b04_pl_ready/` (currently empty).

---

## 11. Current Production Files

### R3.0 Frozen Files (EXIST)

```
mcps/zynq_mcp/control/domain_runner.py          (722 lines)
mcps/zynq_mcp/control/operation_service.py       (125 lines, modified)
mcps/zynq_mcp/control/operation_registry.py      (169 lines, modified)
mcps/zynq_mcp/tests/test_r3_runner.py            (716 lines, 36 tests)
```

### R3.1 Target Files (DO NOT EXIST)

```
mcps/zynq_mcp/domains/pl/system_top.py           — NOT EXISTS
mcps/zynq_mcp/domains/pl/build.py                — NOT EXISTS
mcps/zynq_mcp/domains/pl/bitstream.py            — NOT EXISTS
mcps/zynq_mcp/domains/pl/jtag.py                 — NOT EXISTS
mcps/zynq_mcp/domains/pl/manifest.py             — NOT EXISTS
mcps/zynq_mcp/tests/test_r3_pl.py                — NOT EXISTS
```

### Fixture Directory

```
mcps/zynq_mcp/tests/fixtures/b04_pl_ready/       — EXISTS but EMPTY
```

### Capability

```
list_tools = 9
PL handler files = 0 (only __init__.py in domains/pl/)
zynq_mcp/tests/ collected = 160 (89+35+36)
mcps full regression = 601 passed, 1 skipped
```

---

## 12. Workspace State

| Item | State |
|------|-------|
| Root directory | NOT a git repository |
| `Xilinx_Vivado_MCP/` | Git repo, HEAD `59f2abb` (Platform Architecture Spec) |
| `Xilinx_Vitis_MCP/` | Git repo, HEAD `c334866` (stub) |
| `zynq_platforms/` | Git repo, HEAD `2f24976` (AX7020 Base Platform) |
| `.zynq_runtime/` | Runtime state directory (may contain stale lock/ledger files) |
| `.mcp.json` | `f48fc9a82bad9882...` (unchanged) |
| `CLAUDE.md` | `a53f1935c0053b1...` (unchanged) |

Do NOT treat dirty/untracked files as your own changes.

---

## 13. Test Commands

### Formal frozen regression
```
cd mcps && python -m pytest -q -W error::RuntimeWarning
```

### R3.0 suite only
```
cd mcps && python -m pytest zynq_mcp/tests/test_r3_runner.py -q -W error
```

### Collection
```
cd mcps && python -m pytest zynq_mcp/tests --collect-only -q
```

### Do NOT run from project root
```
python -m pytest mcps/   # OK (collects only mcps/)
python -m pytest          # BAD (may collect docs/reference, old Vivado tests)
```

---

## 14. Agent1 Historical Behavior Risk — MANDATORY GATES

These rules were violated multiple times during R3.0. They are binding.

1. **Do NOT change user-specified product semantics to make tests pass.**
2. Test name, docstring, assert, and report conclusion must be identical.
3. `assert True` is forbidden.
4. `status in ("success", "error")` is forbidden.
5. Helper tests cannot impersonate production-entry tests.
6. Hard-coded string self-comparison is forbidden.
7. Hand-written list length cannot impersonate production contract verification.
8. Do NOT rewrite the entire test file to delete old coverage.
9. Report test counts from `pytest --collect-only` output, never by hand.
10. Do NOT ignore warnings. `-W error` must produce 0 warnings.
11. `PytestUnraisableExceptionWarning` is a failure.
12. Every "verified" claim must include: requirement → production entry → test → exact assertion → raw result.
13. Uncertain or untested = explicitly marked, not claimed as done.
14. **Agent1 does NOT have authority to freeze.** Freeze requires explicit authorization.

---

## 15. Your First Task

**Complete read-only context recovery + R3.1 readiness audit. Output:**

1. Frozen SHA256 consistency check (compare your sha256sum against §7)
2. R313–R321 self-consistency check
3. R3.1 target file inventory (confirm all NOT EXIST)
4. Fixture source analysis (what files must go into b04_pl_ready/)
5. Unresolved design decisions (document in §9)
6. Proof: 0 lines of R3.1 production code written this round

**Wait for review before starting R3.1 implementation.**
