# B04 R2 Repair Handoff

> 日期：2026-08-06
> 目标：向全新上下文的白盒 Agent1 准确移交 R2 状态
> 状态：IMPLEMENTED / PENDING REVIEW — R2 未冻结
> 本文件接收方：白盒 Agent1

---

## 1. 当前 R2 生产代码和测试文件清单

### 生产代码

| 文件 | 行数 | 职责 |
|------|------|------|
| `mcps/zynq_mcp/adapters/vivado_adapter.py` | ~280 | VivadoBridge(owner_task queue) + VivadoAdapter(子进程管理) |
| `mcps/zynq_mcp/control/single_worker.py` | ~280 | SingleWorkerController(唯一生命周期所有者: start/execute_tool/crash/timeout/shutdown/heartbeat) |
| `mcps/zynq_mcp/control/capabilities.py` | +5 | adapter_status 字段 |
| `mcps/zynq_mcp/server.py` | +3 | `worker = SingleWorkerController(ledger, instance_guard, ledger_path)` (控制器现在接收 guard+ledger_path) |
| `mcps/zynq_mcp/dispatcher.py` | +35 | `close_session` 改为异步，包含 worker shutdown + lease release 步骤 |
| `mcps/zynq_mcp/control/workspace.py` | +2 | `resolve_workspace_root(start_path=...)` 参数用于测试注入 |

### 测试代码

| 文件 | 数量 |
|------|------|
| `mcps/zynq_mcp/tests/test_r1_workspace.py` | 5 |
| `mcps/zynq_mcp/tests/test_r1_guard.py` | 7 |
| `mcps/zynq_mcp/tests/test_r1_ledger.py` | 14 |
| `mcps/zynq_mcp/tests/test_r1_operation.py` | 10 |
| `mcps/zynq_mcp/tests/test_r1_gate.py` | 12 |
| `mcps/zynq_mcp/tests/test_r1_recovery.py` | 7 |
| `mcps/zynq_mcp/tests/test_r1_session.py` | 5 |
| `mcps/zynq_mcp/tests/test_r1_mcp_sdk.py` | 7 |
| `mcps/zynq_mcp/tests/test_r1_pkg_lock.py` | 14 |
| `mcps/zynq_mcp/tests/test_r1_wait_operation.py` | 8 |
| `mcps/zynq_mcp/tests/test_r2_adapter.py` | 18 |
| **R1 total** | **89** |
| **R2 total** | **18** |
| **Grand total** | **107** |

### 辅助文件

| 文件 | 用途 |
|------|------|
| `mcps/zynq_mcp/tests/helpers/fake_mcp.py` | 可运行 MCP server（ping/hang_forever/get_capabilities）用于 timeout 和 deterministic tool call 测试 |
| `mcps/zynq_mcp/tests/helpers/child_check_lock.py` | 跨进程 instance_owner.lock 继承测试 |

---

## 2. R1 冻结基线

```
89 tests, 0 skipped, 0 xfail, 0 空 pass
全量回归: 548 passed, 1 skipped (唯一 skip: B02 POSIX test_posix_link_no_overwrite)
```

R1 测试**不得减少**。如果全量回归中 R1 专项数量少于 89，必须停止并排查。

---

## 3. R2 当前测试

R2 专项 18 tests，当前 107 passed。但 R2 尚未通过审核。18 项中有部分无效测试，部分 P0 未关闭。

### R201—R215 映射表（当前状态）

| ID | 场景 | 生产入口 | 测试函数 | 层 | 状态 |
|----|------|---------|---------|-----|------|
| R201 | Adapter via Controller | `ensure_worker()` | `test_r201_start_via_controller` | Real MCP | ✅ PASS |
| R202 | PID capture | `ledger_read_shared` | `test_r202_pid_capture` | Real MCP | ✅ PASS |
| R203 | Tool call deterministic | `adapter.call_tool("ping")` | `test_r203_tool_call_fake` | Fake MCP | ✅ PASS |
| R204 | Crash→OUTCOME_UNKNOWN | `execute_tool()` after kill | `test_r204_crash_outcome_unknown` | Real MCP | ✅ PASS |
| R205 | Hang→Timeout→TIMED_OUT | `execute_tool("hang_forever")` | `test_r205_timeout_hang_forever` | Fake MCP | ⚠️ 测试接受 TIMED_OUT/OUTCOME_UNKNOWN 二选一 |
| R206 | context_ref forwarded | `adapter.call_tool()` | `test_r206_context_ref` | Fake MCP | ✅ PASS |
| R207 | Server path from workspace | `_resolve_server_path()` | `test_r207_server_path` | Mock | ✅ PASS |
| R208 | Workspace root from temp | `resolve_workspace_root(start_path=...)` | `test_r208_workspace_root_from_temp` | Mock | ✅ PASS |
| R209 | Zero candidates fail-closed | `resolve_workspace_root(start_path=...)` | `test_r209_zero_candidates` | Mock | ✅ PASS |
| R210 | Ambiguous fail-closed | `resolve_workspace_root(start_path=...)` | `test_r210_ambiguous` | Mock | ✅ PASS |
| R211 | .mcp.json not read | `_resolve_server_path()` | `test_r211_mcp_json_not_read` | Mock | ✅ PASS |
| R212 | Real MCP 27 tools | `list_tools()` | `test_r212_real_handshake` | Real MCP | ✅ PASS |
| R213 | close_session via Dispatcher | `dispatcher.dispatch("close_session")` | `test_r213_close_session_via_dispatcher` | Fake MCP | ⚠️ 未验证失败保留/Lease/Context最终状态 |
| R214 | Shutdown PID verified | `sw.shutdown()` | `test_r214_shutdown_pid_gone` | Real MCP | ✅ PASS |
| R215 | No second worker after poison | `ensure_worker()` → `BridgeError` | `test_r215_no_second_worker_after_poison` | Real MCP | ✅ PASS |

---

## 4. 已真实完成的项目

1. **并发锁**: `SingleWorkerController.ensure_worker()` 整体 check-create-start-commit 处于 `asyncio.Lock` 临界区。两个 Barrier 同步的并发调用 → factory 调用恰好 1 次、单一 PID、单一 generation。

2. **PID 捕获**: `VivadoBridge._owner_loop` 通过 SDK hook 在 `_sdk_pid_lock` 保护下捕获真实 PID。已通过 real MCP 测试验证。

3. **Fake MCP 确定性调用**: `tests/helpers/fake_mcp.py` 提供 ping/get_capabilities/hang_forever 三个工具。ping 返回 `{"status": "success", "data": {"pong": True}}`。R203 精确断言 `resp.status == "success"` 和 `resp.data["pong"] is True`。

4. **Crash/Timeout 处理骨架**: `_do_crash()` 和 `_do_timeout()` 在 `SingleWorkerController` 中实现。crash → OUTCOME_UNKNOWN, timeout → TIMED_OUT, 均进入 RECOVERY_REQUIRED。`kill_process_tree_exact(pid)` 被调用。

5. **Capability 读取 adapter 状态**: `_get_capabilities` 从 `disp._worker.adapter_status` 读取真实状态。Query 不启动 Worker。

6. **`resolve_workspace_root(start_path=...)`**: 测试注入参数可用。R208/R209/R210 通过该参数验证。

---

## 5. 未关闭 P0

### P0-1: `server.py` finally 中 `asyncio.run(worker.shutdown())`

**文件**: `mcps/zynq_mcp/server.py`, line 179

```python
finally:
    if worker is not None:
        try: asyncio.run(worker.shutdown())
```

`_main()` 本身通过 `asyncio.run(_main())` 调用。finally 块执行在 `_main` 的异步上下文中，此时已有运行中的 event loop。`asyncio.run()` 尝试创建新 event loop 会抛出 `RuntimeError: asyncio.run() cannot be called from a running event loop`。

**修复方向**: 使用 `asyncio.get_event_loop().run_until_complete(worker.shutdown())` 或者在 `_main()` 正常退出路径（`async with stdio_server` 之后）调用 `await worker.shutdown()`，而不是在 finally 中。

### P0-2: close_session 先删除 Context 再关闭 Worker

**文件**: `mcps/zynq_mcp/dispatcher.py`, `_close_session_async`

当前顺序:
1. ledger_cleared（Context/Session 已从 Ledger 删除）— line 116
2. worker.shutdown — line 126
3. leases_released (no-op) — line 135
4. `disp._ledger = ledger` — line 140

正确顺序必须是: active op → Worker → Lease → Context。当前**先清 Context，后关 Worker**，违反了 CLAUDE.md 规定的关闭顺序。

### P0-3: close_session 不检查 shutdown success=False

**文件**: `mcps/zynq_mcp/dispatcher.py`, line 130

```python
except Exception as e:
    events.append(f"worker_shutdown_failed:{e}")
    return error(...)
```

正常路径（line 126-127）:
```python
sw_result = await disp._worker.shutdown()
events.append(f"worker_shutdown:{sw_result['pid_cleaned']}")
```

正常路径不检查 `sw_result["pid_cleaned"]`。如果 `shutdown()` 返回 `{"success": True, "pid_cleaned": False}`，代码继续执行，不会返回 error。

### P0-4: Project/JTAG Lease callback 没有真正接入

**文件**: `mcps/zynq_mcp/dispatcher.py`, lines 134-137

```python
# 3. Release leases (Project before JTAG)
try:
    events.append("leases_released")
except Exception as e:
    events.append(f"leases_failed:{e}")
```

`events.append("leases_released")` 是一个 no-op。没有调用 B02 `project_lock.release()` 或 `jtag_lock.release()`。

### P0-5: Ledger BUSY/READY/ABSENT/crash/timeout 写失败被吞掉

**文件**: `mcps/zynq_mcp/control/single_worker.py`

| 位置 | 代码 | 问题 |
|------|------|------|
| `ensure_worker` ledger READY commit | `except Exception: ... self._adapter = None; raise BridgeError(...)` | ✅ 正确（失败后关闭 Worker 并抛异常） |
| `execute_tool` BUSY write | `except Exception: pass` | ❌ 吞掉 BUSY 写入失败 |
| `execute_tool` READY restore | `except Exception: pass` | ❌ 吞掉 READY 恢复失败 |
| `_do_crash` | `except Exception as e: logger.error(...)` | ⚠️ 记录日志但继续返回 error dict（逻辑正确，但 ledger 未更新） |
| `_do_timeout` | `except Exception as e: logger.error(...)` | ⚠️ 同上 |
| `shutdown` ABSENT write | `except Exception: pass` | ❌ 吞掉 ABSENT 写入失败 |
| `_start_heartbeat` | `except Exception as e: self._last_heartbeat_error = str(e)` | ⚠️ 记录错误但继续声称健康 |

### P0-6: heartbeat cancel 后没有 await 退出

**文件**: `mcps/zynq_mcp/control/single_worker.py`, lines 260-263

```python
def _stop_heartbeat(self) -> None:
    if self._heartbeat_task is not None and not self._heartbeat_task.done():
        self._heartbeat_task.cancel()
        self._heartbeat_task = None
```

`cancel()` 发送取消信号后立即将 task 设为 None。被取消的 task 可能还在执行最后一次 heartbeat 写 Ledger。应该在 `cancel()` 后 `await self._heartbeat_task` 等待其退出，或使用 `asyncio.wait_for(task, timeout)`。

### P0-7: server 退出清理没有有效生产测试

server.py 的 finally 块：
1. `asyncio.run(worker.shutdown())` — 如 P0-1 所述会在运行时崩溃
2. `release_owner_lock()` — 在 `except Exception as e: logger.error(...)` 中吞掉异常

没有任何测试验证真实 `python -m mcps.zynq_mcp.server` 进程退出时：
- Worker 子进程 PID 消失
- owner lock 被释放
- 没有僵尸进程残留

---

## 6. 无效或不足测试

### R205: 允许 TIMED_OUT/OUTCOME_UNKNOWN 二选一

**文件**: `test_r2_adapter.py`, line 231

```python
assert result["status"] == "error"
assert "TIMED_OUT" in result["error"]["details"]["reason_code"] or "timeout" in str(result).lower()
```

测试断言 `reason_code` 包含 "TIMED_OUT" **或** 响应字符串包含 "timeout"。这允许了 OUTCOME_UNKNOWN（crash 路径）通过 timeout 测试。Timeout 路径必须精确返回 `VIVADO_TIMEOUT` 或 `OP_TIMED_OUT`。

### R213: 没有验证失败保留、真实 Lease callback 和 Context 最终状态

`test_r213_close_session_via_dispatcher` 只验证:
- `data["status"] == "success"`
- events 中包含 "ledger_cleared" 和 "worker_shutdown"

未验证:
- Worker 关闭失败时 Context **不得删除**（Ledger context 应保留）
- Lease callback 是否真实调用
- 关闭后 Ledger worker.state == ABSENT

---

## 7. 机械扫描结果

### except:pass 统计（生产代码，共 7 处）

```
mcps/zynq_mcp/adapters/vivado_adapter.py:214    except asyncio.CancelledError: pass
mcps/zynq_mcp/adapters/vivado_adapter.py:218    except Exception: pass
mcps/zynq_mcp/adapters/vivado_adapter.py:221    except Exception: pass
mcps/zynq_mcp/adapters/vivado_adapter.py:276    except Exception: pass
mcps/zynq_mcp/control/session.py:81              except Exception: pass
mcps/zynq_mcp/control/session.py:101             except SessionError: pass
mcps/zynq_mcp/control/instance_guard.py:132      except Exception: pass
```

其中:
- `vivado_adapter.py:214` — MCP SDK 退出清理的 `CancelledError`，可接受
- `vivado_adapter.py:218,221` — shutdown 时的 ctx/session 清理异常吞噬
- `vivado_adapter.py:276` — adapter.shutdown 中 bridge.shutdown 异常吞噬
- `session.py:81` — create_session 中 B02 close_session 回滚异常吞噬
- `session.py:101` — close_session 中 B02 SessionError 吞噬
- `instance_guard.py:132` — UnlockFile 异常吞噬

### 硬门禁状态

| 检查 | 结果 |
|------|------|
| `assert True` in test code | 0 ✅ |
| `status in (success, error)` 在 test_r2_adapter.py | 0 ✅ |
| 测试 R203 精确断言 `status=="success"` + `data["pong"] is True` | ✅ |
| R208 使用生产 `resolve_workspace_root(start_path=...)` | ✅ |
| R209 精确抛出 `WorkspaceNotFoundError` | ✅ |
| R210 精确抛出 `WorkspaceAmbiguousError` | ✅ |

---

## 8. 冻结资产 SHA256

| 资产 | SHA256 前缀 |
|------|------------|
| `.mcp.json` | `f48fc9a82bad9882` |
| `Xilinx_Vivado_MCP/server.py` | `9fa66a0ca56389b7` |
| `mcps/common/context.py` | `37bb0d1ad7ec8538` |
| Board Package `manifest_revision` | `sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7` |

---

## 9. 工作区当前状态

| 项目 | 状态 |
|------|------|
| R1 | **COMPLETE / FROZEN** — 89 tests, 0 skipped |
| R2 | **IMPLEMENTED / PENDING REVIEW** — 未冻结 |
| R3 | **未开始** |
| Agent2 | **未调用** |
| .mcp.json | **未修改**（仍为 4 entries: vivado + 3 旧 zynq_*） |
| Xilinx_Vivado_MCP/ | **未修改** |
| mcps/pl_mcp/ | **历史基线**，未修改 |
| Board Package 六文件 | **未修改** |

### 未提交文件

未纳入版本控制的文件：
- `mcps/zynq_mcp/adapters/vivado_adapter.py`
- `mcps/zynq_mcp/control/single_worker.py`（修改）
- `mcps/zynq_mcp/dispatcher.py`（修改）
- `mcps/zynq_mcp/server.py`（修改）
- `mcps/zynq_mcp/control/capabilities.py`（修改）
- `mcps/zynq_mcp/control/workspace.py`（修改）
- `mcps/zynq_mcp/tests/helpers/fake_mcp.py`
- `mcps/zynq_mcp/tests/test_r2_adapter.py`
- `mcps/zynq_mcp/tests/helpers/fake_mcp_server.py`（未使用）
- `docs/brick_development_plan.md`（修改）
- `docs/development/B04_R2_repair_handoff.md`（本文件）

---

## 10. 声明

- **R2 未冻结** — 状态为 IMPLEMENTED / PENDING REVIEW
- **R3 未开始** — 12 个 PL 领域 API 仍为 0 实现
- **Agent2 未调用**
- 全量回归: 548 passed, 1 skipped（一致）
- R1 专项 89 tests 全部保留
- 本文件只记录状态，不修改任何生产代码或测试
- 交接对象为全新上下文白盒 Agent1

### 建议修复顺序

1. **P0-1** (`server.py` finally `asyncio.run`) — 阻塞 server 正常退出
2. **P0-2** (close_session 顺序) + **P0-3** (shutdown success 检查)
3. **P0-5** (Ledger 写失败不吞)
4. **P0-6** (heartbeat cancel+await)
5. **P0-4** (Lease callback 接入)
6. **P0-7** (server 退出清理生产测试)
7. 修正 R205 和 R213 测试断言
