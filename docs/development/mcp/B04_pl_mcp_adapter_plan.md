# B04 — Unified Zynq MCP Plan v0.3.2

> Brick: B04  |  日期: 2026-08-05  |  状态: **Sub-step 0 FROZEN, v0.3.2 规划完成 — R1 实现尚未开始**
> 依赖: B00 ✅ / B01 ✅ / B02 ✅ / B03 ✅ (FROZEN)
> 前版: v0.3.1 → v0.3.2: 拆分两类 OS 锁、Secondary takeover、最终产品配置仅含 zynq、修正串行 Workflow Stage、修正 Lane 超时转换、R1+R2 合并、集中 root resolver、修正重复请求/close_session、ZynqContext 组合
> 关联: `B04_single_channel_audit.md` v0.3.2

---

## 0. Architecture Revision Summary

### 0.1 部署形态

**旧**: 三个独立 MCP + 独立 vivado
**v0.3.2**: 一个 `zynq` MCP。内部三域。最终 `.mcp.json` 仅含 `zynq`。

### 0.2 v0.3.1 → v0.3.2 关键修正

1. **两类 OS 锁**: instance_owner.lock (Primary 终身) + ledger.lock (每事务短持)
2. **Secondary takeover**: 获取 owner lock → 核验 Ledger → 有活动任务 → RECOVERY_REQUIRED
3. **最终 .mcp.json**: 仅 `zynq`；vivado + 三旧全部移除
4. **串行 Stage**: IDLE→BOARD_VALIDATION→...→OBSERVATION 严格顺序；无并行分支
5. **Lane 转换**: TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → RECOVERY_REQUIRED（非 IDLE）
6. **R1+R2 合并**: 一个 Sub-step；所有 9 个工具均有真实行为
7. **root resolver**: 唯一入口；验证 mcps/ + docs/brick_development_plan.md；精确断言 D:\fpgaproject
8. **重复请求**: 相同 RUNNING → deduplicated=true；相同 TERMINAL → CONFIRM_RETRY_REQUIRED
9. **close_session**: 有 active_operation → CHANNEL_BUSY；不隐式取消
10. **ZynqContext 组合**: base: MCPContext；不修改 B02 context.py

---

## 1. Goals & Non-Goals

**Goals**: 统一 `mcps/zynq_mcp/` MCP Server + 统一 Session + Execution Ledger +
Preflight Gate + SingleWorker + Instance Guard（两类锁 + takeover）+
Vivado Bridge 迁移为 PL Adapter + 12 PL 领域 API + B02/B03 回归。

**Non-Goals**: 重写 Vivado/XSim；Platform/AXI API (B05)；PS/ARM API (B06)；
device-live JTAG (B08)。

---

## 2. Unified Tool Naming

| Domain | Count | Prefix | Example |
|--------|-------|--------|---------|
| control | 9 | (no prefix) | `create_session`, `wait_operation` |
| platform | 12 | `platform_` | `platform_create_design` |
| pl | 12 | `pl_` | `pl_synthesize` |
| ps | 19 | `ps_` | `ps_compile` |

### 2.1 Control APIs (9) — R1 全部实现

```
create_session        → 创建 ZynqContext
close_session         → 有 active_operation → CHANNEL_BUSY；否则清理
get_session_info      → 返回 context + stage + revisions
get_capabilities      → domains + instance_role + implemented count
get_operation_status  → 返回 operation 当前状态
wait_operation        → 有界等待（max 300s）
get_execution_state   → lane + stage + worker health + operation progress
diagnose_execution    → 结构化诊断
recover_execution     → 显式恢复（7 个前提全部满足 → IDLE）
```

### 2.2 43 Domain API Mapping

完整映射（43 行）与 v0.3.1 相同。见审计文档 §17 或本文件附录。

---

## 3. Target Directory Structure

```
mcps/zynq_mcp/
├── server.py
├── dispatcher.py
├── control/
│   ├── session.py, context.py
│   ├── execution_gate.py, execution_ledger.py
│   ├── operation_registry.py, single_worker.py
│   ├── instance_guard.py, ledger_lock.py
│   ├── process_guard.py, recovery.py
│   ├── workspace.py (resolve_workspace_root)
│   ├── timeout_config.py, capabilities.py
├── domains/ (platform/pl/ps)
├── adapters/ (vivado/vitis/xsct/jtag/uart)
└── tests/
```

### runtime_root

```
workspace_root = resolve_workspace_root()  → D:\fpgaproject
runtime_root   = ZYNQ_RUNTIME_ROOT or workspace_root / ".zynq_runtime"
```

内含：`execution_ledger.json`, `instance_owner.lock`, `ledger.lock`, `server_instance.json`

---

## 4. Key Design Elements (Summary)

| 元素 | 设计 |
|------|------|
| OS 锁 | instance_owner.lock (终身) + ledger.lock (短持; exclusive=写, shared=读) |
| Secondary | 只读查询；可 takeover（获取 owner lock → 核验 → RECOVERY_REQUIRED） |
| Lane 转换 | SUCCEEDED/明确FAILED/确认CANCELLED → IDLE；TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → RECOVERY_REQUIRED |
| Stage | 严格 B01 串行顺序；FORWARD/RETRY_SAME/ROLLBACK_FIX/diagnose-recover/BLOCKED 五类 |
| 重复请求 | 相同+RUNNING → deduplicated=true；相同+TERMINAL → CONFIRM_RETRY_REQUIRED；不同+冲突 → CHANNEL_BUSY |
| close_session | active_operation 存在 → CHANNEL_BUSY；不隐式取消/不关 Worker/不删 Context |
| 自动 rebuild | = 0；普通 query 不创建新 Worker |
| ZynqContext | 组合 base: MCPContext；不修改 B02 context.py |
| 最终 .mcp.json | `{"mcpServers": {"zynq": {...}}}` |

---

## 5. Sub-Step Implementation Plan

| Sub-step | Content | Tools | Gate |
|----------|---------|-------|------|
| **R0** ✅ | Audit v0.3.2 | — | 审核方确认 |
| **R1** | Skeleton + Session + Ledger + Preflight + SingleWorker + Instance Guard + Process Guard + Recovery (R1+R2 merged) | 9 | 两类锁验证；takeover；TIMED_OUT→RECOVERY_REQUIRED |
| **R2** | Migrate Vivado Bridge → PL Adapter | 9 | 54 tests pass；auto-rebuild=0 |
| **R3** | PL Domain API | 9+N | Preflight → SingleWorker → Adapter |
| **R4** | Agent2 Black-Box Gate | — | list_tools 无 bypass；最终 .mcp.json 验证 |

---

## 6. Migration: Three Skeletons → One MCP

| Phase | .mcp.json |
|-------|-----------|
| C0–C3 (dev) | vivado + zynq_platform + zynq_pl + zynq_ps + zynq (dev) |
| C4 (after B09) | **zynq only** |
| C5 (after B10) | zynq only；旧目录标记历史基线 |

---

## 7. B02 Compatibility

**不动**: tool_response, error_codes, api_category, revision, artifact_schema,
project_lock, jtag_lock, board_profile, board_package, env_probe。

**不修改**: context.py（B02 frozen）。ZynqContext 采用组合（base: MCPContext）。

---

## 8. Declaration

**统一 Zynq MCP 实现尚未开始。R1 尚未开始。**
