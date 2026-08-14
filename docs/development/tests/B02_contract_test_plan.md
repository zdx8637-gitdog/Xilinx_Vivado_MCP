# B02 — Contract Test Plan v0.2.2

> Brick: B02
> 日期: 2026-08-04
> 状态: **B02 待最终审核 — 全部 5 子步骤完成**

## 最终测试结果

```
235 collected, 234 passed, 1 skipped in 29.00s
```

| 测试文件 | 数量 | 子步骤 |
|---------|------|--------|
| test_tool_response.py | 16 | 0 |
| test_error_codes.py | 3 | 0 |
| test_board_profile.py | 9 | 0 |
| test_context.py | 12 | 0 |
| test_api_category.py | 3 | 0 |
| test_revision.py | 39 | 1 |
| test_artifact_schema.py | 59 | 1 |
| test_project_lock.py | 43 | 2 |
| test_jtag_lock.py | 16 | 2 |
| test_control_api.py | 7 | 3 |
| test_platform_capability.py | 7 | 3 |
| test_pl_capability.py | 7 | 3 |
| test_ps_capability.py | 6 | 3 |
| test_mcp_registration.py | 8 | 4 |
| **Total** | **235** | — |

## MCP Registration Tests

`test_mcp_registration.py` (8 tests):
- `test_vivado_entry_unchanged`: 完整 dict equality 断言
- `test_registration_entry_exists` ×3: 参数化验证名称/command/args
- `test_python_on_path`: 记录 `shutil.which("python")` 解析结果
- `test_*_starts_from_raw_config` ×3: 使用原始 `.mcp.json` 值通过 `StdioServerParameters` 启动，不注入 PYTHONPATH，不替换 command

> 关联: [B02_common_contract_plan.md](../mcp/B02_common_contract_plan.md)

---

## 0. v0.2.1 → v0.2.2 修订

| 项 | v0.2.1 | v0.2.2 |
|----|--------|--------|
| MCP 握手 | 手写 JSON-RPC `subprocess` + `json.loads(stdout.readline)` | MCP SDK 1.28.1 `StdioServerParameters` + `stdio_client` + `ClientSession` |
| Capability 断言 | `caps["mcp_name"]` 裸 dict | `response["status"] == "success"` → `response["data"]["mcp_name"]` |
| 硬编码路径 | `cwd="D:\fpgaproject"`, `/tmp/test_proj` | `Path(__file__).parents[2]` 求项目根；测试用 `tmp_path` |
| 跨进程 import | 缺少 `os`, `json` | 子进程代码完整 `import os, json` |
| TTL 测试 | 活进程持锁+等 TTL→期望新 acquire 成功 | 拆三条: 活锁不破 / 进程退出后回收 / 过期 metadata 注入 (无 sleep) |
| Batch 0 依赖 | Context 依赖 Batch 1 Board Profile | Board Profile Loader + fixture 前移到 Batch 0 |
| `publish_manifest()` | `os.path.exists` + `os.replace` 竞态 | temp exclusive create + complete write + fsync → Windows rename no-replace / POSIX link no-replace → semantic compare. Never uses `os.replace` |
| PL wrapper check | 缺失 | `BD_WRAPPER_MISMATCH` invariant |
| 公共 API 测试 | 仅验 `tools/list` 含名称 | 实际调用 5 个 API 并验行为 |
| PS built-from 测试 | 缺失 | 独立测试 |
| 基线文件 | 缺 xdc, xparameters.h 实体 | 补全所有六个文件基线 |

---

## 1. Scope

Process-level only. No Vivado, Vitis, XSCT, board, JTAG cable.

---

## 2. Helper: Project Root

```python
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]   # D:\fpgaproject from mcps/<name>/tests/test_*.py
```

All path references use `PROJECT_ROOT` or `tmp_path`. No hardcoded `D:\fpgaproject` or `/tmp/test_proj`.

---

## 3. Test Files (12)

### Batch 0: Foundation + Board Profile (5 tests)

#### T-B02-001: ToolResponse — `test_tool_response.py`

```python
import json
from mcps.common.tool_response import success, error, command_accepted, OperationStatus

def test_success_query_response():
    r = success(data={"version": "2023.1"})
    d = r.to_dict()
    assert d["status"] == "success"
    assert "request_id" in d
    assert d["request_id"].startswith("req-")
    assert "operation_id" not in d
    assert d["data"] == {"version": "2023.1"}

def test_error_response():
    r = error("Vivado not found", code="ENV_ERROR")
    d = r.to_dict()
    assert d["status"] == "error"
    assert d["error"]["code"] == "ENV_ERROR"
    assert d["error"]["message"] == "Vivado not found"
    assert "request_id" in d

def test_command_response_has_operation_id():
    r = command_accepted(operation_id="op-001")
    d = r.to_dict()
    assert d["status"] == "success"
    assert d["data"]["operation_id"] == "op-001"
    assert d["data"]["status"] == "accepted"
    assert "request_id" in d

def test_operation_status_lifecycle():
    op = OperationStatus(operation_id="op-001", status="accepted")
    assert op.status == "accepted"
    op.status = "running"; assert op.status == "running"
    op.status = "succeeded"
    op.result = success(data={"done": True}).to_dict()
    op_dict = op.__dict__
    assert op_dict["status"] == "succeeded"
    assert op_dict["result"]["status"] == "success"
```

#### T-B02-002: Error Codes — `test_error_codes.py`

```python
from mcps.common.error_codes import ErrorCode

def test_all_error_codes_defined():
    required = {"ENV_ERROR", "TOOL_ERROR", "PLATFORM_ERROR", "PL_BUILD_ERROR",
                "PS_BUILD_ERROR", "JTAG_ERROR", "UART_ERROR", "ARTIFACT_STALE",
                "INTERNAL_ERROR", "CONTEXT_INVALID", "LOCK_BUSY",
                "OPERATION_NOT_FOUND", "INVALID_ARGUMENT"}
    defined = {e.value for e in ErrorCode}
    assert required.issubset(defined)

def test_error_codes_unique():
    values = [e.value for e in ErrorCode]
    assert len(values) == len(set(values))

def test_error_codes_count():
    assert len(list(ErrorCode)) == 13
```

#### T-B02-003: Board Profile — `test_board_profile.py`

```python
import json
from mcps.common.board_profile import board_profile_load

def test_load_test_fixture():
    profile = board_profile_load("TEST_AX7020_MINIMAL")
    assert profile["board_id"] == "TEST_AX7020_MINIMAL"
    assert "sha256" in profile
    assert profile["sha256"].startswith("sha256:")

def test_sha256_deterministic():
    p1 = board_profile_load("TEST_AX7020_MINIMAL")
    p2 = board_profile_load("TEST_AX7020_MINIMAL")
    assert p1["sha256"] == p2["sha256"]

def test_sha256_changes_when_file_changes(tmp_path):
    profile_json = tmp_path / "test_profile.json"
    original = {"board_id": "TEST_MODIFY", "part": "xc7z020clg400-2"}
    profile_json.write_text(json.dumps(original))
    p1 = board_profile_load(str(profile_json))
    original["part"] = "xc7z010clg225-1"
    profile_json.write_text(json.dumps(original))
    p2 = board_profile_load(str(profile_json))
    assert p1["sha256"] != p2["sha256"]

def test_reject_unknown_board():
    with pytest.raises(FileNotFoundError):
        board_profile_load("NONEXISTENT_BOARD_ID")
```

#### T-B02-004: Context — `test_context.py`

```python
import pytest
from mcps.common.context import create_session, close_session, get_session_info
from mcps.common.context import SessionError, BoardProfileError

def test_valid_session():
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    assert ctx.session_id is not None
    info = get_session_info(ctx.session_id)
    assert info["board_id"] == "TEST_AX7020_MINIMAL"
    close_session(ctx.session_id)

def test_reject_fake_session_id():
    with pytest.raises(SessionError):
        get_session_info("nonexistent-00000000-0000-0000-0000-000000000000")

def test_reject_unknown_board():
    with pytest.raises(BoardProfileError):
        create_session(board_id="NONEXISTENT_BOARD", project_path=str(tmp_path))

def test_close_cleans_up():
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    sid = ctx.session_id
    close_session(sid)
    with pytest.raises(SessionError):
        get_session_info(sid)

def test_project_path_stored(tmp_path):
    proj = tmp_path / "my_project"
    proj.mkdir()
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(proj))
    info = get_session_info(ctx.session_id)
    assert info["project_path"] == str(proj)
    close_session(ctx.session_id)
```

#### T-B02-005: API Categories — `test_api_category.py`

```python
from mcps.common.api_category import query, set_op, command

@query
def read_something(): return "data"

@set_op
def update_something(): return "ok"

@command
def do_something(): return "accepted"

def test_query_marked():
    assert getattr(read_something, "_api_category") == "query"

def test_set_marked():
    assert getattr(update_something, "_api_category") == "set"

def test_command_marked():
    assert getattr(do_something, "_api_category") == "command"
```

---

### Batch 1: Artifact (2 tests)

#### T-B02-006: Revision — `test_revision.py`

```python
from mcps.common.revision import compute_revision, canonical_json

def test_same_input_same_hash():
    digest = {"board_profile_sha256": "abc", "tool_versions": {"vivado": "2023.1"}}
    h1 = compute_revision(digest)
    h2 = compute_revision(digest)
    assert h1 == h2
    assert h1.startswith("sha256:")
    assert len(h1) == 71

def test_different_input_different_hash():
    assert compute_revision({"a": "1"}) != compute_revision({"a": "2"})

def test_key_order_normalized():
    assert compute_revision({"a": 1, "b": 2}) == compute_revision({"b": 2, "a": 1})

def test_path_sorting_normalized():
    d1 = {"source_files": [{"path": "b.v"}, {"path": "a.v"}]}
    d2 = {"source_files": [{"path": "a.v"}, {"path": "b.v"}]}
    assert compute_revision(d1) == compute_revision(d2)

def test_relative_posix_paths():
    raw = canonical_json({"source_files": [{"path": "rtl\\top.v"}]}).decode('utf-8')
    assert "rtl/top.v" in raw
```

#### T-B02-007: Artifact Schema — `test_artifact_schema.py`

```python
import json, os
from mcps.common.artifact_schema import (
    validate_manifest, check_consistency, publish_manifest, ManifestConflictError
)
from mcps.common.revision import compute_revision, sha256_file

def baseline_files(tmp_path):
    """Create real files for all 6 artifact types."""
    files = {}
    for name in ["platform.xsa", "wrapper.v", "design.bit", "app.elf",
                 "xparameters.h", "board.xdc"]:
        p = tmp_path / name; p.write_text(f"content of {name}")
        files[name] = str(p)
    return files

def baseline_manifests(tmp_path):
    f = baseline_files(tmp_path)
    bp = {"board_id": "TEST", "sha256": "bp_sha", "part": "xc7z020clg400-2"}

    plat_inputs = {"board_profile_sha256": "bp_sha",
        "tool_versions": {"vivado": "2023.1"}, "source_files": [], "config_files": []}
    plat = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": "bp_sha",
        "platform_revision": compute_revision(plat_inputs),
        "manifest_revision": compute_revision(plat_inputs),
        "revision_inputs": plat_inputs,
        "xsa_path": f["platform.xsa"], "xsa_sha256": sha256_file(f["platform.xsa"]),
        "bd_wrapper_path": f["wrapper.v"],
        "bd_wrapper_sha256": sha256_file(f["wrapper.v"]),
        "address_map": {"axi_gpio_0": {"base": "0x41200000", "range": "64K"}},
        "clock_tree": {}, "generated_at": "2026-08-04T00:00:00Z", "status": "locked"
    }

    pl_inputs = {"board_profile_sha256": "bp_sha", "tool_versions": {},
                 "source_files": [], "config_files": []}
    pl = {
        "schema_version": "1.0", "manifest_type": "pl_build",
        "board_profile_sha256": "bp_sha",
        "built_from_platform_revision": plat["platform_revision"],
        "manifest_revision": compute_revision(pl_inputs),
        "revision_inputs": pl_inputs,
        "bitstream_path": f["design.bit"], "bitstream_sha256": sha256_file(f["design.bit"]),
        "bd_wrapper_sha256": sha256_file(f["wrapper.v"]),
        "xdc_path": f["board.xdc"], "xdc_sha256": sha256_file(f["board.xdc"]),
        "timing_met": True, "wns_ns": 0.12, "tns_ns": 0.0,
        "generated_at": "2026-08-04T00:01:00Z", "status": "locked"
    }

    ps_inputs = {"board_profile_sha256": "bp_sha", "tool_versions": {},
                 "source_files": [{"path": "main.c", "sha256": "abc"}], "config_files": []}
    ps = {
        "schema_version": "1.0", "manifest_type": "ps_build",
        "board_profile_sha256": "bp_sha",
        "built_from_platform_revision": plat["platform_revision"],
        "platform_xsa_sha256": sha256_file(f["platform.xsa"]),
        "manifest_revision": compute_revision(ps_inputs),
        "revision_inputs": ps_inputs,
        "elf_path": f["app.elf"], "elf_sha256": sha256_file(f["app.elf"]),
        "xparameters_h_path": f["xparameters.h"],
        "xparameters_h_sha256": sha256_file(f["xparameters.h"]),
        "xparameters_addrs": {"XPAR_AXI_GPIO_0_BASEADDR": "0x41200000"},
        "source_files_sha256": "abc",
        "generated_at": "2026-08-04T00:02:00Z", "status": "locked"
    }
    return bp, plat, pl, ps

# === validate_manifest ===

def test_validate_all_valid(tmp_path):
    _, plat, pl, ps = baseline_manifests(tmp_path)
    assert validate_manifest(plat, "platform") == []
    assert validate_manifest(pl, "pl_build") == []
    assert validate_manifest(ps, "ps_build") == []

def test_validate_missing_required_field():
    issues = validate_manifest({"manifest_type": "platform"}, "platform")
    assert len(issues) > 0
    assert any(i.code == "MISSING_FIELD" and i.field == "schema_version" for i in issues)

def test_validate_bad_revision(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["manifest_revision"] = "sha256:" + "ff" * 32
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "BAD_REVISION" for i in issues)

def test_validate_file_missing(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["bitstream_path"] = "/nonexistent/file.bit"
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "PATH_NOT_FOUND" for i in issues)

def test_validate_sha256_mismatch(tmp_path):
    _, _, _, ps = baseline_manifests(tmp_path)
    ps["elf_sha256"] = "sha256:" + "ee" * 32
    issues = validate_manifest(ps, "ps_build")
    assert any(i.code == "SHA256_MISMATCH" for i in issues)

def test_validate_xdc_path(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    issues = validate_manifest(pl, "pl_build")
    assert len(issues) == 0  # xdc file exists + SHA256 matches

# === check_consistency ===

def test_consistency_all_match(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    issues = check_consistency(plat, pl, ps, bp)
    assert len(issues) == 0

def test_consistency_platform_bp_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    plat["board_profile_sha256"] = "wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.artifact == "platform" and i.code == "BOARD_PROFILE_MISMATCH" for i in issues)

def test_consistency_pl_bp_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["board_profile_sha256"] = "wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.artifact == "pl_build" and i.code == "BOARD_PROFILE_MISMATCH" for i in issues)

def test_consistency_ps_bp_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["board_profile_sha256"] = "wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.artifact == "ps_build" and i.code == "BOARD_PROFILE_MISMATCH" for i in issues)

def test_consistency_pl_revision_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["built_from_platform_revision"] = "sha256:wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.artifact == "pl_build" and i.code == "PLATFORM_REVISION_MISMATCH" for i in issues)

def test_consistency_ps_revision_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["built_from_platform_revision"] = "sha256:wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.artifact == "ps_build" and i.code == "PLATFORM_REVISION_MISMATCH" for i in issues)

def test_consistency_xsa_sha256_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["platform_xsa_sha256"] = "sha256:wrong"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.code == "XSA_SHA256_MISMATCH" for i in issues)

def test_consistency_bd_wrapper_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["bd_wrapper_sha256"] = "sha256:deadbeef"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.code == "BD_WRAPPER_MISMATCH" for i in issues)

def test_consistency_address_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["xparameters_addrs"]["XPAR_AXI_GPIO_0_BASEADDR"] = "0x50000000"
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.code == "ADDRESS_MISMATCH" for i in issues)

def test_consistency_address_missing(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["xparameters_addrs"] = {}
    issues = check_consistency(plat, pl, ps, bp)
    assert any(i.code == "ADDRESS_MISMATCH" for i in issues)

# === publish_manifest ===

def test_publish_new(tmp_path):
    manifests_dir = tmp_path / "manifests" / "platform"
    manifests_dir.mkdir(parents=True)
    result = publish_manifest('{"test": true}', str(manifests_dir / "rev1.json"))
    assert result == "published"

def test_publish_idempotent_same_semantic_content(tmp_path):
    manifests_dir = tmp_path / "manifests" / "platform"
    manifests_dir.mkdir(parents=True)
    path = str(manifests_dir / "rev2.json")
    publish_manifest('{"test": 1, "generated_at": "t1"}', path)
    result = publish_manifest('{"test": 1, "generated_at": "t2"}', path)
    assert result == "already_exists_same"

def test_publish_reject_different_semantic_content(tmp_path):
    manifests_dir = tmp_path / "manifests" / "platform"
    manifests_dir.mkdir(parents=True)
    path = str(manifests_dir / "rev3.json")
    publish_manifest('{"test": 1, "generated_at": "t1"}', path)
    with pytest.raises(ManifestConflictError):
        publish_manifest('{"test": 2, "generated_at": "t3"}', path)

def test_atomic_commit_no_race(tmp_path):
    """Atomic no-replace commit: cannot overwrite existing final file."""
    manifests_dir = tmp_path / "manifests" / "platform"
    manifests_dir.mkdir(parents=True)
    path = str(manifests_dir / "rev4.json")
    # Two concurrent attempts: first succeeds, second gets EEXIST→then compare
    publish_manifest('{"test": 1}', path)
    result = publish_manifest('{"test": 1}', path)
    assert result == "already_exists_same"
```

---

### Batch 2: Lock Libraries (2 tests)

#### T-B02-008: Project Lock — `test_project_lock.py`

```python
import os, json, subprocess, sys, time, pytest
from pathlib import Path
from mcps.common.project_lock import acquire, acquire_read, release, heartbeat

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert Path(PROJECT_ROOT).name == "fpgaproject", f"PROJECT_ROOT wrong: {PROJECT_ROOT}"

def test_acquire_release(tmp_path):
    lock_path = str(tmp_path / "proj")
    result = acquire(lock_path, owner="TEST", ttl_s=10)
    assert result.status == "acquired"
    assert result.lease.owner == "TEST"
    release(result.lease)
    result2 = acquire(lock_path, owner="TEST2", ttl_s=10)
    assert result2.status == "acquired"
    release(result2.lease)

def test_heartbeat(tmp_path):
    result = acquire(str(tmp_path / "proj_hb"), owner="T", ttl_s=10)
    old_hb = result.lease.heartbeat_at
    time.sleep(0.1)
    heartbeat(result.lease)
    assert result.lease.heartbeat_at > old_hb
    release(result.lease)

def test_live_lock_not_broken_by_ttl(tmp_path):
    """A live process holding the OS lock cannot have its lock broken, even if TTL expired."""
    lock_path = str(tmp_path / "live_lock")
    result = acquire(lock_path, owner="HOLDER", ttl_s=1)
    assert result.status == "acquired"
    time.sleep(1.2)  # TTL expired in metadata
    # This process still holds the OS lock → contender must get BUSY
    result2 = acquire(lock_path, owner="CONTENDER", ttl_s=5, wait_s=0.1)
    assert result2.status == "busy", f"Live OS lock must not be broken by TTL expiry"
    release(result.lease)

def test_stale_metadata_reclaimed_after_process_exit(tmp_path):
    """Process exits without releasing → OS frees lock → stale metadata → reclaimed."""
    lock_path = str(tmp_path / "stale_reclaim")
    holder = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os
from mcps.common.project_lock import acquire
r = acquire('{lock_path}', owner='HOLDER', ttl_s=1)
print(json.dumps({{'status': r.status, 'pid': os.getpid()}}), flush=True)
# Exit immediately without release — simulates crash
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT_ROOT)
    try:
        line = holder.stdout.readline()
        assert json.loads(line)["status"] == "acquired"
        holder.wait(timeout=5)  # Process exited, OS lock released
        time.sleep(0.2)  # Brief settle
        result = acquire(lock_path, owner="RECLAIMER", ttl_s=10)
        assert result.status == "acquired", f"Stale metadata should be reclaimable after process exit"
        release(result.lease)
    finally:
        holder.terminate(); holder.wait(timeout=5)

def test_cross_process_mutex(tmp_path):
    lock_path = str(tmp_path / "mutex")
    holder = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys, time
from mcps.common.project_lock import acquire, release
r = acquire('{lock_path}', owner='HOLDER', ttl_s=10)
print(json.dumps({{'status': r.status, 'pid': os.getpid()}}), flush=True)
sys.stdout.flush()
time.sleep(3)
release(r.lease)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT_ROOT)
    try:
        line = holder.stdout.readline()
        assert json.loads(line)["status"] == "acquired"
        result = acquire(lock_path, owner="CONTENDER", ttl_s=5, wait_s=0.1)
        assert result.status == "busy"
    finally:
        holder.terminate(); holder.wait(timeout=5)

def test_read_lock_coexistence(tmp_path):
    lock_path = str(tmp_path / "read_test")
    r1 = acquire_read(lock_path)
    assert r1.status == "acquired" and r1.lease.mode == "read"
    r2 = acquire_read(lock_path)
    assert r2.status == "acquired" and r2.lease.mode == "read"
    w = acquire(lock_path, owner="W", ttl_s=5, wait_s=0.1)
    assert w.status == "busy"
    release(r1.lease); release(r2.lease)
```

#### T-B02-009: JTAG Lock — `test_jtag_lock.py`

```python
import json, subprocess, sys
from pathlib import Path
from mcps.common.jtag_lock import acquire, release

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])   # D:\fpgaproject from mcps/<name>/tests/test_*.py

def test_jtag_acquire_release():
    result = acquire("localhost:3121", "D1234567", owner="TEST", ttl_s=10)
    assert result.status == "acquired"
    assert result.lease.lock_key == "localhost:3121:D1234567"
    assert result.lease.scope == "jtag"
    release(result.lease)

def test_jtag_cross_process_mutex():
    holder = subprocess.Popen(
        [sys.executable, "-c", f"""
import json, os, sys, time
from mcps.common.jtag_lock import acquire, release
r = acquire('localhost:3121', 'D9999', owner='HOLDER', ttl_s=10)
print(json.dumps({{'status': r.status, 'pid': os.getpid()}}), flush=True)
sys.stdout.flush()
time.sleep(3)
release(r.lease)
"""],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PROJECT_ROOT)
    try:
        line = holder.stdout.readline()
        assert json.loads(line)["status"] == "acquired"
        result = acquire("localhost:3121", "D9999", owner="CONTENDER", ttl_s=5, wait_s=0.1)
        assert result.status == "busy"
    finally:
        holder.terminate(); holder.wait(timeout=5)
```

---

### Batch 3: Skeleton Servers + Common Control APIs (3 tests)

#### T-B02-010: Platform MCP Capability + Control APIs — `test_capability.py`

Uses MCP SDK 1.28.1 `StdioServerParameters` + `stdio_client` + `ClientSession`. No manual JSON-RPC.

```python
import json, pytest, sys, pytest_asyncio
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]   # D:\fpgaproject from mcps/<name>/tests/test_*.py

@pytest_asyncio.fixture
async def platform_session():
    server_params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcps.platform_mcp.server"]
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session

@pytest.mark.asyncio
async def test_capability_returns_tool_response_envelope(platform_session):
    result = await platform_session.call_tool("get_capabilities", {})
    text = result.content[0].text
    caps = json.loads(text)
    # Verify it's a ToolResponse envelope
    assert caps["status"] == "success"
    assert "request_id" in caps
    assert caps["data"]["mcp_name"] == "zynq_platform"
    assert caps["data"]["domain_apis_implemented"] == 0
    assert "platform.create_design" in caps["data"]["planned_domain_apis"]

@pytest.mark.asyncio
async def test_all_5_control_apis_present(platform_session):
    tools_result = await platform_session.list_tools()
    names = [t.name for t in tools_result.tools]
    for api in ["create_session", "close_session", "get_session_info",
                 "get_capabilities", "get_operation_status"]:
        assert api in names, f"Missing control API: {api}"

@pytest.mark.asyncio
async def test_create_and_close_session(platform_session):
    r1 = await platform_session.call_tool("create_session", {
        "board_id": "TEST_AX7020_MINIMAL",
        "project_path": str(PROJECT_ROOT / "test_work")
    })
    d1 = json.loads(r1.content[0].text)
    assert d1["status"] == "success"
    sid = d1["data"]["session_id"]

    r2 = await platform_session.call_tool("get_session_info", {"session_id": sid})
    d2 = json.loads(r2.content[0].text)
    assert d2["status"] == "success"
    assert d2["data"]["board_id"] == "TEST_AX7020_MINIMAL"

    r3 = await platform_session.call_tool("close_session", {"session_id": sid})
    d3 = json.loads(r3.content[0].text)
    assert d3["status"] == "success"

@pytest.mark.asyncio
async def test_get_operation_status_not_found(platform_session):
    r = await platform_session.call_tool("get_operation_status",
        {"operation_id": "nonexistent-op"})
    d = json.loads(r.content[0].text)
    assert d["status"] == "error"
    assert d["error"]["code"] == "OPERATION_NOT_FOUND"
```

#### T-B02-011: PS MCP — `test_capability.py`

Identical pattern to T-B02-010. Assertions:
- `caps["data"]["mcp_name"] == "zynq_ps"`
- `caps["data"]["domain_apis_implemented"] == 0`
- `"ps.create_app"` and `"ps.compile"` in planned APIs
- 5 control APIs registered and callable (create_session, close_session, get_session_info, get_capabilities, get_operation_status)
- `get_operation_status(unknown)` returns `OPERATION_NOT_FOUND`
- All responses are ToolResponse envelopes

#### T-B02-012: PL MCP — `test_capability.py`

Identical pattern to T-B02-010. Assertions:
- `caps["data"]["mcp_name"] == "zynq_pl"`
- `caps["data"]["domain_apis_implemented"] == 0`
- `"pl.synthesize"` and `"pl.generate_system_top"` in planned APIs
- 5 control APIs registered and callable
- All responses are ToolResponse envelopes

Plus import guard:

```python
import ast

def test_pl_skeleton_does_not_import_vivado():
    with open(PROJECT_ROOT / "mcps" / "pl_mcp" / "server.py", "r") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "Xilinx_Vivado_MCP" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "Xilinx_Vivado_MCP" not in node.module
```

---

## 4. Test Execution

```bash
# All from D:\fpgaproject root
python -m pytest mcps/ -v

# By batch
python -m pytest mcps/common/tests/test_tool_response.py mcps/common/tests/test_error_codes.py mcps/common/tests/test_board_profile.py mcps/common/tests/test_context.py mcps/common/tests/test_api_category.py -v
python -m pytest mcps/common/tests/test_revision.py mcps/common/tests/test_artifact_schema.py -v
python -m pytest mcps/common/tests/test_project_lock.py mcps/common/tests/test_jtag_lock.py -v
python -m pytest mcps/platform_mcp/tests/ mcps/ps_mcp/tests/ mcps/pl_mcp/tests/ -v
```

---

## 5. Pass/Fail Criteria

| Test | Pass if |
|------|---------|
| T1 | ToolResponse serializes; request_id always present; operation_id only on command |
| T2 | 13 error codes defined, unique |
| T3 | Board profile loads; sha256 computed by loader; file change → sha256 change |
| T4 | Session create/close/get; rejects fake id, unknown board |
| T5 | @query/@set/@command decorators correctly categorized |
| T6 | Revision deterministic; key+path order normalized |
| T7 | Validate: all valid → 0 issues; all error types detected. Consistency: all match → 0 issues; 9 invariant violations detected individually |
| T8 | Live lock not broken by TTL; stale metadata reclaimed after exit; cross-process mutex; read coexistence |
| T9 | JTAG lock same lifecycle |
| T10 | Platform MCP: ClientSession handshake; get_capabilities returns ToolResponse; 5 control APIs called + verified |
| T11 | PS MCP: same pattern |
| T12 | PL MCP: same pattern + no Xilinx_Vivado_MCP import |
