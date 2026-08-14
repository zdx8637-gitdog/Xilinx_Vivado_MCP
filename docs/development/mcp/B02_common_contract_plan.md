# B02 — MCP Common Contract & Three-Service Framework v0.2.2

> Brick: B02
> 日期: 2026-08-04
> 状态: **B02 COMPLETE / FROZEN ✅**
> 依赖: B01 ✅
> 架构依据: `docs/architecture_ai_zynq7020.md` v2.3.1 (FROZEN)

---

## 0. v0.2.1 → v0.2.2 修订

| 项 | v0.2.1 | v0.2.2 |
|----|--------|--------|
| MCP 握手测试 | 手写 JSON-RPC (`subprocess` + `json.loads(stdout.readline)`) | 使用 MCP SDK 1.28.1 `StdioServerParameters` + `stdio_client` + `ClientSession` |
| `get_capabilities()` 断言 | `caps["mcp_name"]` 裸 dict | `response.status == "success"` → `response.data["mcp_name"]` ToolResponse envelope |
| 硬编码路径 | `cwd="D:\fpgaproject"`, `/tmp/test_proj` | `tmp_path` fixture; `Path(__file__).parents[2]` 求项目根 |
| 跨进程测试 import | 缺少 `os`, `json` | 子进程代码包含 `import os, json` |
| TTL 测试 | 活进程持锁+等待TTL→期望新 acquire 成功 | 拆为三条独立测试: 1) 活进程持锁不破 2) 进程退出后 OS 锁释放+过期 metadata 可回收 3) 过期 metadata 注入 (避免 sleep) |
| Batch 0 依赖倒置 | Context 测试需要 `TEST_AX7020_MINIMAL` (Batch 1 fixture) | Board Profile Loader + fixture 前移到 Batch 0 |
| `publish_manifest()` 竞态 | `os.path.exists` + `os.replace` 两步非原子 | 同目录 temp → O_EXCL create + complete write + fsync → Windows rename no-replace / POSIX link no-replace → existing final semantic comparison |
| `generated_at` 导致相同 revision 冲突 | 同一 revision 重跑时 `generated_at` 不同→误判为不同内容 | 比较前剥离 `generated_at`；相同 revision 已存在时返回 `already_exists_same` (不要求字节全同) |
| PL bd_wrapper 未跨 manifest 检验 | 仅 `check_consistency` 中未比较 PL vs Platform wrapper SHA256 | 增加 `BD_WRAPPER_MISMATCH` invariant |
| PS built-from 测试 | 缺失 | 增加独立测试 |
| 公共控制 API 测试 | 仅验证 `tools/list` 中包含名称 | 实际调用 5 个 API 并验证行为 |
| Batch 0 文件 | 6 个 (无 fixture/board_profile) | 8 个: +`board_profile.py`, +fixture |

---

## 1. Purpose

Establish the shared behavioral contract for all three MCPs. B02 produces no platform-specific logic — only the skeleton each MCP inherits and the rules they all obey.

---

## 2. Existing Asset Inventory

| File | B02 Action |
|------|------------|
| `Xilinx_Vivado_MCP/server.py` | **Do not touch** |
| `Xilinx_Vivado_MCP/models.py` | Extract `ToolResponse` pattern |
| `Xilinx_Vivado_MCP/vivado_process.py`, `xsim_process.py`, `tcl_templates.py`, `version_guard.py` | Keep as-is |
| `Xilinx_Vivado_MCP/vivado_tools.py`, `sim_tools.py`, `hw_tools.py` | Refactor in B04 |
| `Xilinx_Vivado_MCP/requirements.txt` (`mcp==1.28.1`) | **Do not change** |

---

## 3. MCP Registration

| MCP | Name | Command |
|-----|------|---------|
| Platform | `zynq_platform` | `python -m mcps.platform_mcp.server` |
| PL | `zynq_pl` | `python -m mcps.pl_mcp.server` |
| PS | `zynq_ps` | `python -m mcps.ps_mcp.server` |

Keep existing `vivado` registration. B02 PL skeleton does NOT import `Xilinx_Vivado_MCP`.

---

## 4. Shared Contract

### 4.1 ToolResponse v2

```python
@dataclass
class ToolResponse:
    status: str                # "success" | "error"
    request_id: str            # UUID, always present
    data: Any | None
    error: ErrorDetail | None
    warnings: list[str]
    context_ref: str | None

@dataclass
class OperationStatus:
    operation_id: str
    status: str                # "accepted" | "running" | "succeeded" | "failed"
    result: ToolResponse | None
    progress_pct: int | None
    created_at: str
    updated_at: str
```

### 4.2 API Categories

```python
@query     → idempotent, no side effects, request_id only
@set       → idempotent, modifies state, request_id only
@command   → NOT idempotent, request_id + operation_id
```

### 4.3 Common Control APIs (5)

| # | API | Category |
|---|-----|----------|
| C1 | `create_session(board_id, project_path)` | command |
| C2 | `close_session(session_id)` | command |
| C3 | `get_session_info(session_id)` | query |
| C4 | `get_capabilities()` | query |
| C5 | `get_operation_status(operation_id)` | query |

Shared in `mcps/common/control_api.py`. Three servers import, no duplication.

### 4.4 Context

```python
@dataclass
class MCPContext:
    session_id: str
    board_id: str
    project_path: str
    lease_holder: str | None
    created_at: str
```

### 4.5 Error Codes (13)

| Code | Category | Recoverable |
|------|----------|-------------|
| `ENV_ERROR` | Environment | Usually no |
| `TOOL_ERROR` | EDA tool | Sometimes |
| `PLATFORM_ERROR` | Platform design | No |
| `PL_BUILD_ERROR` | PL build | No |
| `PS_BUILD_ERROR` | PS build | No |
| `JTAG_ERROR` | JTAG/deployment | Yes |
| `UART_ERROR` | Observation | Yes |
| `ARTIFACT_STALE` | Consistency | No |
| `INTERNAL_ERROR` | MCP bug | No |
| `CONTEXT_INVALID` | Bad session/board/project | No |
| `LOCK_BUSY` | Resource contention | Yes (retry) |
| `OPERATION_NOT_FOUND` | Unknown operation_id | No |
| `INVALID_ARGUMENT` | Bad API params | No |

---

## 5. Artifact Schema v1

### 5.1 Revision Algorithm

```
revision = "sha256:" + SHA256(canonical_json(input_digest))

canonical_json(obj):
    return json.dumps(obj, sort_keys=True, indent=None,
                      separators=(',', ':'), ensure_ascii=False).encode('utf-8')
```

### 5.2 Manifest Types & Immutability

| Manifest | Producer | Path pattern |
|----------|----------|-------------|
| Platform | Platform MCP | `manifests/platform/<revision>.json` |
| PL Build | PL MCP | `manifests/pl/<revision>.json` |
| PS Build | PS MCP | `manifests/ps/<revision>.json` |

**`manifest_revision`**: `= compute_revision(revision_inputs)` — hash of inputs only, not of the file itself (prevents self-reference).

**Content comparison**: When checking if a manifest already exists for the same revision, compare the *semantic content* (all fields except `generated_at`). If semantic content matches → return `already_exists_same`. If semantic content differs → `ManifestConflictError`. This allows re-generating the same revision without failing on timestamp differences.

**Atomic commit**: O_EXCL create temp → complete write loop + `os.fsync()` → Windows: `os.rename(tmp, final)` (raises `FileExistsError` if target exists) → POSIX: `os.link(tmp, final)` then `os.unlink(tmp)` (EEXIST if target exists). Never uses `os.replace`. If final already exists, compare semantic content (excluding `generated_at`): same → `already_exists_same`; different → `ManifestConflictError`. Final path never contains partial content (temp is fully written before the no-replace rename/link). No overwrite ever occurs.

**Windows filename**: Manifest stored as `sha256_<64hex>.json` (colon replaced with underscore for Windows compatibility). Revision format remains `sha256:<64hex>` in all manifest fields. `_revision_to_filename()` handles the mapping.

### 5.3 `validate_manifest()` + `check_consistency()`

```python
@dataclass
class ValidationIssue:
    code: str       # MISSING_FIELD, INVALID_TYPE, BAD_REVISION, PATH_NOT_FOUND, SHA256_MISMATCH
    manifest: str
    field: str | None
    expected: Any | None
    actual: Any | None

def validate_manifest(manifest: dict, manifest_type: str) -> list[ValidationIssue]:
    """Validates schema, required fields, revision self-consistency, and
    declared file existence + SHA256 for ALL declared paths
    (xsa, bd_wrapper, bitstream, elf, xparameters.h, xdc)."""

@dataclass
class ConsistencyIssue:
    code: str       # BOARD_PROFILE_MISMATCH, PLATFORM_REVISION_MISMATCH,
                    # XSA_SHA256_MISMATCH, ADDRESS_MISMATCH, BD_WRAPPER_MISMATCH
    artifact: str
    field: str
    expected: str
    actual: str

def check_consistency(platform: dict, pl: dict, ps: dict, board_profile: dict) -> list[ConsistencyIssue]:
    """Invariants checked:
    1. Platform board_profile_sha256
    2. PL board_profile_sha256
    3. PS board_profile_sha256
    4. PL built_from_platform_revision
    5. PS built_from_platform_revision
    6. PS platform_xsa_sha256
    7. PL bd_wrapper_sha256 == Platform bd_wrapper_sha256
    8. xparameters_addrs (every platform.address_map key: must exist in ps, value must match)
    """
```

---

## 6. Lock Libraries

### 6.1 Interface

```python
@dataclass
class LockAcquireResult:
    status: str           # "acquired" | "busy" | "timeout"
    lease: Lease | None

def acquire(lock_key: str, owner: str, ttl_s: int = 300, wait_s: float = 0) -> LockAcquireResult
def acquire_read(lock_key: str) -> LockAcquireResult
def heartbeat(lease: Lease) -> None
def release(lease: Lease) -> None
```

### 6.2 TTL Semantics (three cases)

1. **Live process holds lock**: OS lock active → `acquire()` returns `busy` regardless of metadata TTL. TTL cannot break a live lock.
2. **Process exited without release**: OS lock freed by OS. Metadata shows expired TTL → next `acquire()` reclaims the stale metadata and succeeds.
3. **Injected metadata test**: For unit testing TTL, directly construct an expired `Lease` record rather than `sleep()`.

Heartbeat writes timestamp to metadata sidecar. It is an observability signal, not a lock mechanism.

---

## 7. Skeleton Servers & Shared Handler Location

### 7.1 Directory Structure (33 new files, 1 modified)

```
mcps/                                          ← new
├── __init__.py
├── requirements.txt                           ← mcp==1.28.1
├── common/
│   ├── __init__.py
│   ├── tool_response.py
│   ├── error_codes.py
│   ├── context.py
│   ├── api_category.py
│   ├── control_api.py                        ← shared handler implementations
│   ├── revision.py
│   ├── artifact_schema.py
│   ├── board_profile.py
│   ├── project_lock.py
│   ├── jtag_lock.py
│   └── tests/
│       ├── __init__.py
│       ├── fixtures/
│       │   └── board_profile_TEST_AX7020_MINIMAL.json
│       ├── test_tool_response.py
│       ├── test_error_codes.py
│       ├── test_context.py
│       ├── test_api_category.py
│       ├── test_revision.py
│       ├── test_artifact_schema.py
│       ├── test_board_profile.py
│       ├── test_project_lock.py
│       └── test_jtag_lock.py
├── platform_mcp/
│   ├── __init__.py
│   ├── server.py
│   └── tests/
│       └── test_capability.py
├── ps_mcp/
│   ├── __init__.py
│   ├── server.py
│   └── tests/
│       └── test_capability.py
└── pl_mcp/
    ├── __init__.py
    ├── server.py
    └── tests/
        └── test_capability.py
```

Modified: `.mcp.json`.

---

## 8. API Assignment to Bricks

| Brick | Domain APIs Added | Cumulative | Common APIs |
|-------|-------------------|------------|-------------|
| B02 | 0 | 0 | 5 |
| B04 | 12 (PL) | 12 | reuse |
| B05 | 12 (Platform) | 24 | reuse |
| B06 | 19 (PS) | 43 | reuse |

---

## 9. Execution Batches

| Batch | Files | Tests | Key Dependency | Status |
|-------|-------|-------|----------------|--------|
| 0 | 15 (7 modules + 6 tests + 1 fixture + 1 config) | 5 (39 test cases) | Board Profile loader included | ✅ FROZEN |
| 1 | revision.py + artifact_schema.py + 2 test files | 2 (39+59 tests) | | ✅ FROZEN |
| 2 | project_lock.py + jtag_lock.py + 2 test files | 2 (43+16 tests) | | ✅ FROZEN |
| 3 | control_api.py + 3 MCP skeletons + 4 test files | 4 (5+5+5=15 SDK tests + 7 unit tests) | MCP SDK ClientSession, fail-closed args | ✅ FROZEN |
| 4 | `.mcp.json` + registration test | 1 test file (3 SDK + 4 config + 1 PATH = 8 tests) | All tests pass, config verified | ✅ FROZEN |

---

## 10. Gate Checklist — B02 最终门禁

### 子步骤 4 ✅ (MCP 注册)

- [x] `.mcp.json` 保留 `vivado` 字段不变，新增 `zynq_platform` / `zynq_pl` / `zynq_ps`
- [x] 三个注册的 `command` + `args` 可通过 MCP SDK 启动服务器
- [x] 配置静态检查: 参数化验证名称、command、args 正确性
- [x] `old vivado entry` 配置内容未被修改

### 子步骤 3 ✅ (FROZEN)

- [x] 三个 MCP skeleton 通过 `python -m mcps.<name>.server` 启动
- [x] MCP SDK `ClientSession` stdio handshake 通过（27 tests）
- [x] `get_capabilities()` 返回 `ToolResponse` envelope
- [x] 5 common control APIs: 全部行为验证
- [x] PL skeleton: AST scan 确认 0 处 `Xilinx_Vivado_MCP` import
- [x] `ToolDispatcher`: 统一 schema + dispatch，三个 server 0 处 handler 重复
- [x] fail-closed: `dispatch()` 检查 `isinstance(arguments, dict)`, `_check_str_arg` 拒绝非字符串
- [x] 7 个 dispatch 单元测试（无需启动 MCP server）
- [x] `PYTHONPATH` 从 `__file__` 推导，timeout 30s 最外层

### 子步骤 0-2 ✅ (FROZEN)

- [x] ToolResponse v2: fail-closed `to_dict()`, 13 ErrorCode, OperationStatus
- [x] Context: frozen MCPContext, `create_session/close_session/get_context`
- [x] Board Profile: cache by source path, deep copy, no `_source_path` leak
- [x] Revision: deterministic, path normalized, no absolute/UNC/drive-relative
- [x] Manifest: `validate_manifest()` + `check_consistency()` + atomic no-replace publish
- [x] Project Lock: Windows LockFileEx via ctypes, heartbeat under RLock, live TTL not broken
- [x] JTAG Lock: canonical URL+cable length-delimited key, independent from project lock

### 最终统计

```
234 passed, 1 skipped in 29.00s
```

| 子步骤 | 内容 | 测试文件 | 测试数 |
|--------|------|---------|--------|
| 0 | Foundation + Board Profile | 5 | 43 |
| 1 | Revision + Artifact Schema | 2 | 98 |
| 2 | Project Lock + JTAG Lock | 2 | 59 |
| 3 | Control API + 3 MCP skeletons | 4 | 27 |
| 4 | .mcp.json 注册 + 黑盒 | 1 | 8 |
| **Total** | | **14** | **235** |

### Batch 0 ✅ (FROZEN)

- [x] 15 files (7 modules + 6 tests + 1 fixture + 1 config)
- [x] Board Profile: cache by source path, validates internal board_id, deep copy, no `_source_path` leak
- [x] Context: normalizes project_path, frozen MCPContext, get_context entry point
- [x] Context: rejects fake session, unknown board
- [x] ToolResponse v2: fail-closed `to_dict()`; full UUID; operation_id only on `@command`; 13 ErrorCode values
- [x] B01 unchanged (SHA256 verified)
- [x] Xilinx_Vivado_MCP unchanged (SHA256 verified)

### Batch 1 ✅ (FROZEN)

- [x] 141 tests collected, 140 passed, 1 skipped (test_revision=39, test_artifact_schema=59, Batch 0=43). POSIX link test skipped on Windows.
- [x] `manifest_revision = compute_revision(revision_inputs)` — no self-reference
- [x] Manifest atomic commit: temp exclusive create → complete write → fsync → Windows rename no-replace / POSIX link no-replace. Never uses `os.replace`.
- [x] Manifest filename: `sha256_<64hex>.json` (colon-safe for Windows), revision stays `sha256:<64hex>`
- [x] `validate_manifest()`: schema, types, required fields, revision xref, files, timing — structured `ValidationIssue`
- [x] `check_consistency()`: 8 cross-manifest invariants, structured `ConsistencyIssue`
- [x] Schema v1: `schema_version = "1.0"` in all manifests (distinct from architecture doc version)
- [x] Revision path normalization: POSIX, no absolute/UNC/drive-relative, no `..`, duplicate detection
- [x] Cross-process race tests: different content (published + conflict), same content (published + already_exists_same), timeout protection
- [x] Zero active-algorithm uses of `os.replace`; historical revision-table references retained

### Batch 2 ✅ (FROZEN)

- [x] 200 collected, 199 passed, 1 skipped (project=43, jtag=16, Batch 0+1=141)
- [x] OS file lock: Windows LockFileEx (shared/exclusive) via ctypes, POSIX fcntl.flock (LOCK_SH/LOCK_EX)
- [x] Canonical key separation: `project:<normpath>` vs length-delimited `jtag:<url>:<serial>`
- [x] Active lease registry with `threading.RLock` guard + `_pending` reservation set
- [x] heartbeat: entire operation (validate→replace→_write_meta→return) under registry RLock
- [x] release: validate+mark "releasing" under lock → best-effort _del_meta → unlock OS handle → close → pop
- [x] Token validation: 8-field comparison (excl heartbeat_at) on every release/heartbeat
- [x] heartbeat(read): explicit RuntimeError
- [x] release exception-safety: metadata delete failure does not prevent OS lock release
- [x] Re-entrancy: same owner + same resource in any mode → busy
- [x] Cross-process mutual exclusion: 2+ subprocess tests with ready markers + communicate(timeout)
- [x] Cross-process crash recovery: child exit → OS lock freed → next acquire succeeds
- [x] Heartbeat/release deterministic race: release blocks until heartbeat completes under RLock
- [x] `set_lock_dir` check+assign in single critical section
- [x] ctypes signatures: `CreateFileW.restype=HANDLE`, `LockFileEx.restype=BOOL`, etc.
