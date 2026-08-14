# B04 单一 Zynq MCP 架构审计与重构规划 v0.3.2

> Brick: B04  |  日期: 2026-08-05  |  版本: v0.3.2
> 范围: 上下文恢复 + 机械核对 + 三域合并 + 单执行通道 + 迁移规划
> 状态: **规划阶段 — 统一 Zynq MCP 实现尚未开始，R1 尚未开始**
> 前版: v0.3.1 → v0.3.2: 拆分两类 OS 锁、Secondary takeover 流程、最终产品配置不含旧入口、修正串行 Workflow Stage、修正 Lane 超时转换、R1/R2 渐进工具暴露、集中 root resolver、修正重复请求/close_session 行为、ZynqContext 组合关系

---

## 0. v0.3.1 → v0.3.2 修正对照

| # | 问题 | v0.3.1 | v0.3.2 修正 |
|---|------|--------|------------|
| 1 | OS 锁设计 | instance.lock 同时承担 Primary 生命周期所有权 + Ledger RMW | 拆分为 instance_owner.lock（终身持有）和 ledger.lock（每事务短持） |
| 2 | Secondary takeover | 无 | 定义完整流程：获取 owner lock → 核验 Ledger → 重建进程身份 → 无法确认 → RECOVERY_REQUIRED |
| 3 | 最终 .mcp.json | 保留 vivado 注册 | 最终仅保留 zynq；vivado + 三旧全部移除；内部 Adapter 复用旧代码 |
| 4 | Workflow Stage | PLATFORM_DESIGN→PS_BUILD、PL_BITSTREAM→CONSISTENCY_CHECK（跳过 PS_BUILD） | 严格 B01 串行：固定顺序，无并行分支；分别定义正常前进/重试/回退修复/diagnose-recover/禁止跳跃 |
| 5 | Lane 超时转换 | TIMED_OUT → IDLE | TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → RECOVERY_REQUIRED；仅 SUCCEEDED/明确 FAILED(安全)/确认 CANCELLED → IDLE |
| 6 | R1/R2 工具渐进暴露 | R1 注册 9 个控制工具（含未实现的 wait/recover） | R1 仅注册已有真实行为的工具；缺 Ledger 则合并 R1+R2；无 NOT_IMPLEMENTED 占位 |
| 7 | workspace root resolver | 分散使用 parents[N] | 唯一入口 resolve_workspace_root()；验证 mcps/ 目录 + docs/brick_development_plan.md；fail-closed；不依赖 cwd |
| 8 | 重复请求行为 | 直接返回 CHANNEL_BUSY | 相同请求 → 返回 existing_operation_id + deduplicated=true；不同参数但冲突 → CHANNEL_BUSY |
| 9 | close_session 行为 | 取消后台任务 + 关闭 Worker | 有 active_operation → CHANNEL_BUSY；不取消、不关闭、不删 Context；取消需显式操作 |
| 10 | 自动 rebuild | D8 待定 | 确认自动 Worker rebuild = 0；普通 query 不创建新 Worker；允许同一存活连接上的纯传输重试（记录、不启动新进程） |
| 11 | ZynqContext | 可能修改 B02 context.py | 采用组合：ZynqContext 包含 base: MCPContext；不修改已冻结 context.py |
| 12 | 新增测试 | 89 tests | +16 tests = 105 tests；含两类锁测试、takeover、TIMED_OUT→RECOVERY_REQUIRED、close_session 拒绝、dedup、串行阶段、最终 .mcp.json 黑盒验证 |

---

## 1. Context Recovery Conclusions

### 1.1 项目状态

| Brick | Status |
|-------|--------|
| B00 | FROZEN |
| B01 | FROZEN (SHA256: `65080485...` unchanged) |
| B02 | FROZEN (367 passed / 1 skipped — identical to frozen baseline) |
| B03 | FROZEN (Board package locked; `manifest_revision: sha256:72191212...`) |
| B04 Sub-step 0 | FROZEN |
| B04 Sub-step 1 | In progress — 54 tests passing, 0 domain APIs |
| B04 Sub-step 2+ | Not started |
| B05–B10 | Not started |

### 1.2 冻结资产未修改

`.mcp.json` SHA256 `f48fc9a8...` 不变；`Xilinx_Vivado_MCP/` 不变；
B01/B02/B03 交付物不变；三个旧 MCP skeleton 原位。

### 1.3 当前 Sub-step 1 能力摘要

BridgeOwner 生命周期、Vivado MCP stdio 桥接、PID 捕获（SDK hook）、ShutdownResult PID 验证、
ToolResponse 转换（27 种错误分类）、Operation 状态机（accepted→running→succeeded→failed）、
后台任务注册/取消、Worker in_flight 串行化、崩盘检测/poison、close_session 四步清理、
进程树 kill、PID liveness、Tombstone、PLControlAdapter 路由、渐进式能力声明、
`__file__` 路径推导、.mcp.json 隔离。

**54 passed (16.84s)** — 代码现状未修改。

---

## 2. 修正 1: 拆分两类 OS 锁

### 2.1 问题

v0.3.1 的 `instance.lock` 同时承担两个冲突角色：
1. Primary Server 整个生命周期所有权（长持）
2. Ledger 每次 read-modify-write 的并发保护（短持）

Primary 终身持有 instance.lock，则 ledger 每次 RMW 不需要重复获取。
但 Secondary 需要读 ledger（查询状态）、需要尝试升级为 Primary（takeover），
这两个操作与 Primary 的 ledger 写入需要互斥。

**分离为两种锁**。

### 2.2 instance_owner.lock — Primary 生存期所有权

| 属性 | 值 |
|------|-----|
| 文件 | `<runtime_root>/instance_owner.lock` |
| 持有者 | Primary 实例（整个 Server 生命周期） |
| Secondary | 不持有；尝试获取意味着 takeover |
| 释放 | Primary 正常退出时释放；Primary 崩溃后 OS 自动释放 |
| 实现 | OS 排他文件锁 (Windows `LockFileEx` LOCKFILE_EXCLUSIVE_LOCK + no byte range → 整文件; POSIX `fcntl.flock(LOCK_EX)`) |

### 2.3 ledger.lock — Ledger 事务保护

| 属性 | 值 |
|------|-----|
| 文件 | `<runtime_root>/ledger.lock` |
| Primary 写入 | 获取 exclusive（短持，仅覆盖 RMW 周期） |
| Secondary 读取 | 获取 shared/read（短持，仅覆盖读取周期） |
| Secondary takeover | 获取 exclusive 后进行第一次 RMW |
| 持有时间 | 毫秒级（单次 ledger 读或写） |
| 实现 | Windows `LockFileEx` exclusive/shared; POSIX `fcntl.flock(LOCK_EX/LOCK_SH)` |

### 2.4 两种锁的生命周期

```
Primary 启动:
  1. 获取 instance_owner.lock (排他) → 成功 → 我是 Primary
     ├── 终身持有（直到 exit 或崩溃）
     ├── Ledger 每次读写:
     │     ├── 获取 ledger.lock (exclusive)
     │     ├── RMW
     │     ├── 释放 ledger.lock
     │     └── ...
     └── exit: 释放 instance_owner.lock → 释放 ledger.lock（最后写入后不再持有）

Secondary 启动:
  1. 获取 instance_owner.lock (排他) → 失败 → 我是 Secondary
  2. 读取 Ledger:
     ├── 获取 ledger.lock (shared)
     ├── 读取 execution_ledger.json
     ├── 释放 ledger.lock
     └── 进入只读 service loop

Primary 崩溃:
  → OS 释放 instance_owner.lock
  → OS 释放 ledger.lock (如果正持有)
  → Secondary 检测到 owner lock 可获取 → takeover 流程
```

### 2.5 Secondary Takeover 流程（v0.3.3 修正：孤儿 Worker）

```
Secondary 检测到 instance_owner.lock 可获取:
  1. 获取 instance_owner.lock (exclusive) → 成功
     [不再释放！成为新 Primary。系统不得处于无主状态。]
  2. 获取 ledger.lock (exclusive)
  3. 读取完整 Ledger
  4. 分析 active_operation:
     ├── active_operation == null → 安全，标记自己为 Primary
     ├── active_operation.status in (SUCCEEDED, FAILED, CANCELLED)
     │     → 安全，标记自己为 Primary
     ├── active_operation.status in (TIMED_OUT, INTERRUPTED, OUTCOME_UNKNOWN)
     │     → 设置 execution_lane = RECOVERY_REQUIRED
     │     → 标记自己为 Primary
     │     → 返回结构化状态（不启动新任务）
     └── active_operation.status == RUNNING
           ├── 检查旧 worker.pid 是否存活
           │     ├── 存活 → 旧 Worker 是孤儿进程（Owner 已死，stdio/owner 通道不可恢复）
           │     │     → worker.state = ORPHANED
           │     │     → active_operation → OUTCOME_UNKNOWN
           │     │     → execution_lane = RECOVERY_REQUIRED
           │     │     → 返回 ORPHANED_WORKER_DETECTED
           │     │     → [不自动接管旧 Worker]
           │     │     → [不自动杀死旧进程]
           │     │     → [不释放 owner lock — 新 Primary 已建立]
           │     └── 已消失 → operation → INTERRUPTED (非命令) 或 OUTCOME_UNKNOWN (命令)
           │           → execution_lane = RECOVERY_REQUIRED
           │           → 标记自己为 Primary
           │           → 返回结构化状态
  5. 更新 ledger（新 instance_id, worker.state）
  6. 释放 ledger.lock
  7. 进入 Primary service loop（但 Lane = RECOVERY_REQUIRED，直到显式 recover）

关键规则（v0.3.3 修正）:
  - 始终持有 owner lock，始终建立新 Primary；系统不得无主
  - 旧 Worker PID 仍存活 → worker.state = ORPHANED；不接管、不自动杀
  - 只有显式 recover_execution 在验证五字段进程身份后，才能安全终止孤儿进程或要求用户处理
  - 禁止 takeover 后直接启动新任务
```

### 2.6 Instance Lock Handle 不可被子进程继承（v0.3.3 新增）

**问题**: 如果 Primary 持有的 instance_owner.lock 或 ledger.lock 的 OS Handle 被子进程继承，
Primary 崩溃后子进程仍持有锁引用，OS 认为锁未释放，Secondary 无法获取 owner lock。

**修正**: 所有 Lock Handle 创建时必须设置不可继承标志。

```
Windows:
  使用 CreateFileW 时 dwFlagsAndAttributes 包含
  FILE_FLAG_OVERLAPPED 且不包含 FILE_FLAG_DELETE_ON_CLOSE；
  然后显式 SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)

  或使用 msvcrt.locking 时在 open() 后:
  import msvcrt
  handle = msvcrt.get_osfhandle(fd)
  SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0)

POSIX:
  open() 默认不继承；fcntl.flock 不受 fork 影响（POSIX 语义）
  flock 不跨 fork 继承：子进程 fork 后锁被释放
  但 open fd 可能泄漏 → 使用 O_CLOEXEC
```

**测试要求**:
- Primary 持有 owner lock
- Primary 启动 fake EDA child
- Child 不继承 owner lock（验证 child 进程无法看到锁）
- 强制结束 Primary（保留 child 存活）
- 新实例能够获取 owner lock（证明 child 未持有锁）
- 新实例进入 ORPHANED/RECOVERY_REQUIRED
- Child 仍存活（证明不是依靠杀 child 才释放锁）
- ledger.lock Handle 同样不得泄漏给子进程

---

## 3. 修正 2: 最终产品 .mcp.json

### 3.1 问题

v0.3.1 的 Phase C4 保留独立 vivado 注册。旧 `Xilinx_Vivado_MCP` 暴露 27 个执行型工具
（create_project, synth_design, program_device 等），继续注册会绕过统一 Execution Gate。

### 3.2 最终产品配置

```json
{
  "mcpServers": {
    "zynq": {
      "command": "python",
      "args": ["-m", "mcps.zynq_mcp.server"]
    }
  }
}
```

**唯一的 MCP Server 注册**。无 `vivado`、无 `zynq_platform`、无 `zynq_pl`、无 `zynq_ps`。

### 3.3 修订后的关闭阶段

| Phase | .mcp.json 内容 | zynq | 旧 vivado | 旧 zynq_* (3) |
|-------|---------------|------|----------|-------------|
| **C0** (当前) | 4 entries | — | 注册 | 注册 |
| **C1** (B04 R1–R2) | 4 entries + zynq (dev) | 创建 skeleton | 注册（诊断） | 注册 |
| **C2** (B04 R3–R4) | 4 entries + zynq (dev) | PL 域接入 | 注册（诊断） | 注册 |
| **C3** (B05–B06) | 4 entries + zynq (dev) | 全域接入 | 注册（诊断） | 注册 |
| **C4** (B09 Agent2 验收后) | **1 entry: zynq** | 唯一入口 | **移除** | **移除** |
| **C5** (B10 后) | 1 entry: zynq | 产品入口 | 代码保留为内部 Adapter | 目录标记历史基线 |

### 3.4 C4 切换条件

1. 统一 zynq MCP Agent2 黑盒验收通过
2. 43 领域 API 在统一 MCP 全部可用
3. 旧 Vivado MCP 27 个工具中所需诊断能力通过 zynq 的只读 API 提供
4. 旧 skeleton 全量测试在统一 MCP 有等效覆盖
5. Agent2 验证 `list_tools` 中不包含可绕过 Gate 的旧执行入口
6. 审核方明确批准

### 3.5 最终保证

Phase C4 后：
- 智能体通过 `.mcp.json` 只能看到 `zynq` 一个 MCP Server
- 不存在任何可绕过统一 Execution Gate 的执行型工具入口
- `Xilinx_Vivado_MCP/` 代码保留在磁盘，作为统一 MCP 内部的 Vivado Adapter 复用
- 旧的直接 stdin/stdout 桥接通道在统一 MCP 内部使用，不对外暴露

---

## 4. 修正 3: 串行 Workflow Stage

### 4.1 问题

v0.3.1 的 Workflow Stage 表允许 `PLATFORM_DESIGN → PS_BUILD`（跳过 PL）和
`PL_BITSTREAM → CONSISTENCY_CHECK`（跳过 PS_BUILD）。这与 B01 标准 Zynq 流程冲突。

### 4.2 修正后的串行主线

依据 B01 冻结的 Phase 0–6：

```
IDLE
  → BOARD_VALIDATION
    → PLATFORM_DESIGN
      → PL_GENERATE
        → PL_BUILD
          → PL_IMPLEMENT
            → PL_TIMING
              → PL_BITSTREAM
                → PS_BUILD
                  → CONSISTENCY_CHECK
                    → DEPLOYMENT
                      → OBSERVATION
```

**这是唯一的正常前进路径。不允许分支或并行。**

### 4.3 四类转换定义

每个 Stage 有四类合法转换：

**(a) 正常前进**（FORWARD）— 唯一后继

| 当前 Stage | → | 后继 Stage | 前提 |
|-----------|----|-----------|------|
| `IDLE` | → | `BOARD_VALIDATION` | create_session SUCCEEDED |
| `BOARD_VALIDATION` | → | `PLATFORM_DESIGN` | board_profile validated |
| `PLATFORM_DESIGN` | → | `PL_GENERATE` | Platform XSA + Manifest; platform_export_hardware SUCCEEDED |
| `PL_GENERATE` | → | `PL_BUILD` | system_top.v generated; pl_generate_system_top SUCCEEDED |
| `PL_BUILD` | → | `PL_IMPLEMENT` | pl_synthesize SUCCEEDED |
| `PL_IMPLEMENT` | → | `PL_TIMING` | pl_place_and_route SUCCEEDED |
| `PL_TIMING` | → | `PL_BITSTREAM` | timing_met = true; pl_analyze_timing SUCCEEDED |
| `PL_BITSTREAM` | → | `PS_BUILD` | PL Build Manifest; pl_generate_bitstream SUCCEEDED |
| `PS_BUILD` | → | `CONSISTENCY_CHECK` | PS Build Manifest; ps_compile SUCCEEDED |
| `CONSISTENCY_CHECK` | → | `DEPLOYMENT` | Run Manifest status="ready" |
| `DEPLOYMENT` | → | `OBSERVATION` | device programmed |
| `OBSERVATION` | — | (terminal) | GPIO_E2E_PASS |

**(b) 同阶段重试**（RETRY_SAME）— 不变更 stage

| 条件 | 行为 |
|------|------|
| 同一 tool + 相同 canonical args + Operation FAILED/CANCELLED/TIMED_OUT → 已终态 | 显式 retry 意图（args 中 `retry: true`）→ 允许提交新 Operation；stage 不变 |
| 无显式 retry → 相同请求已终态 | 返回 CONFIRM_RETRY_REQUIRED |

**(c) 回退修复**（ROLLBACK_FIX）— 向更早 stage 移动

| 当前 Stage | 允许回退到 | 前提 |
|-----------|----------|------|
| `PL_TIMING` (timing_met=false) | `PL_BUILD` | 修改约束后重新综合 |
| `PL_BITSTREAM` (生成失败) | `PL_BUILD` | 修复后重新综合 |
| `PS_BUILD` (编译失败) | `PS_BUILD` (同阶段) | 修改源码后重新编译 |
| `CONSISTENCY_CHECK` (fail) | `PL_BUILD` 或 `PS_BUILD` | consistency errors 指向哪个域 |

回退需要显式设置 stage（`set_stage` 或通过 domain API 的 `retry`/`recovery` 参数）。

**(d) diagnose/recover** — 中断后恢复

| 触发 | 行为 |
|------|------|
| OUTCOME_UNKNOWN / INTERRUPTED / UNRESPONSIVE | Lane → RECOVERY_REQUIRED |
| recover_execution() SUCCEEDED | Lane → IDLE；stage 不变或回退到安全起点 |
| recover_execution() 无法确认 | 保持 RECOVERY_REQUIRED；返回结构化诊断 |

**(e) 禁止跳跃**（BLOCKED）

| 跳跃 | 原因 |
|------|------|
| `PL_BUILD` synthesis 未 SUCCEEDED → `PL_IMPLEMENT` | 没有综合结果 |
| `PL_IMPLEMENT` place_and_route 未 SUCCEEDED → `PL_TIMING` | 没有实现结果 |
| `PL_TIMING` 未执行 / timing_met=false → `PL_BITSTREAM` | 时序未关闭 |
| `PLATFORM_DESIGN` 未完成 → `PL_BUILD` | 没有 wrapper / XSA |
| `PL_BITSTREAM` 跳过 `PS_BUILD` → `CONSISTENCY_CHECK` | 缺少 PS 产物 |
| `CONSISTENCY_CHECK` 未通过 → `DEPLOYMENT` | Artifact 不一致 |

**MCP 只校验安全前提和 Artifact 一致性，不替 Skill 决定采用哪条路线。
Skill 可以合法选择 ROLLBACK_FIX，但必须在 Ledger 中记录。**

---

## 5. 修正 4: 超时和 Lane 转换

### 5.1 问题

v0.3.1 允许 `BUSY → IDLE (TIMED_OUT)`。TIMED_OUT 意味着底层任务状态未知——
不能安全回到 IDLE。

### 5.2 修正后的 Lane 转换

```
IDLE → BUSY                          (submit_command ACCEPTED)

BUSY → IDLE:
  SUCCEEDED                          (确认成功 → 进入 IDLE；等待下一工具调用)
  明确 FAILED 且资源安全              (工具返回明确错误 + Worker/Lease 确认安全 → IDLE)
  确认 CANCELLED 且底层完全停止       (显式 cancel 完成 + 进程/Lease 确认释放 → IDLE)

BUSY → RECOVERY_REQUIRED:
  TIMED_OUT                          (超过 operation deadline；底层状态未知)
  INTERRUPTED                        (Worker 进程消失；底层状态未知)
  OUTCOME_UNKNOWN                    (Worker 崩溃/通信丢失；底层状态未知)
  仅取消等待 Task 但底层状态未知       (取消的是 wait，不是底层任务 → OUTCOME_UNKNOWN)

RECOVERY_REQUIRED → IDLE:
  recover_execution() 返回 SUCCEEDED   (必须满足全部条件，见 §5.3)

RECOVERY_REQUIRED → RECOVERY_REQUIRED:
  diagnose_execution()                (只诊断，不改变 Lane)
```

### 5.3 recover_execution() 恢复 IDLE 的前提

```
recover_execution() 必须获得以下全部证据后才能恢复 IDLE:

1. 无活动 EDA 子进程 — 或已确认归属本 workspace 并安全停止
2. Project 锁已释放 — 验证 lock file 不存在或属于本 workspace
3. JTAG 锁已释放 — 验证 lock file 不存在或属于本 workspace
4. 串口资源安全 — 无已打开的占用
5. Ledger active_operation 已转为明确终态 (SUCCEEDED/FAILED/CANCELLED/INTERRUPTED/OUTCOME_UNKNOWN)
6. Artifact 未被错误标记为成功 — 验证 artifact 路径 + SHA256 vs Ledger 声明
7. recovery_log 已写入 — 记录 recovery action + 证据 + 决定

任一条件不满足 → RECOVERY_REQUIRED 保持
所有条件满足 → Lane = IDLE；previous_operation 归档
```

---

## 6. 修正 5: R1/R2 工具渐进暴露

### 6.1 问题

v0.3.1 的 R1 注册 9 个控制工具，但 wait_operation、diagnose_execution、recover_execution
在没有 Execution Ledger、Operation Registry 和 Worker 管理的情况下没有真实行为。

### 6.2 修正：R1 和 R2 合并为 R1，或 R1 最小化

**决定**：合并 R1+R2 为单一 Sub-step R1。原因：
- wait_operation 需要 Operation Registry
- diagnose_execution / recover_execution 需要 Execution Ledger + Worker 状态
- get_execution_state 需要 Execution Ledger
- Instance Guard 需要 Ledger 判断 takeover 时的旧任务状态

分离 R1（无 Ledger）和 R2（有 Ledger）会导致 R1 的 Secondary 无法正确查询 Primary 状态。

### 6.3 R1 合并后的工具

| # | 工具 | 行为 | 依赖 |
|---|------|------|------|
| 1 | `create_session` | 创建统一 ZynqContext | Board Profile, Context |
| 2 | `close_session` | 有 active_operation → CHANNEL_BUSY；否则清理 Session | Session, Ledger |
| 3 | `get_session_info` | 返回 context + stage + revisions | Context |
| 4 | `get_capabilities` | 返回 domains + instance_role | Capabilities |
| 5 | `get_operation_status` | 返回 operation 当前状态 | Operation Registry |
| 6 | `wait_operation` | 有界等待 operation 完成 | Operation Registry |
| 7 | `get_execution_state` | 返回 lane + stage + worker health | Execution Ledger |
| 8 | `diagnose_execution` | 返回结构化诊断 | Execution Ledger + Process Guard |
| 9 | `recover_execution` | 显式恢复 | Execution Ledger + Process Guard + Recovery |

**9 个真实行为的工具**。0 个 NOT_IMPLEMENTED 占位。
`get_capabilities().implemented` = 9。`list_tools` = 9。

### 6.4 后续添加

| Sub-step | 新增工具 | 新增数量 | domain_apis_implemented |
|----------|---------|---------|------------------------|
| R3 | （无新控制工具；PL Adapter 接入） | 0 | 0 |
| R4 | pl_generate_system_top, pl_create_project, ... | +N | N |
| R5 | （Agent2 验收） | 0 | N |

---

## 7. 修正 6: workspace root resolver

### 7.1 问题

v0.3.1 使用 `Path(__file__).resolve().parent.parent.parent` 分散在多个模块，
且无验证机制。

### 7.2 唯一入口

```python
# mcps/zynq_mcp/control/workspace.py

def resolve_workspace_root() -> Path:
    """
    返回 canonical workspace root 的绝对路径。

    算法:
      1. 从当前模块文件向上遍历目录
      2. 对每个候选目录验证:
         a. mcps/ 子目录存在
         b. docs/brick_development_plan.md 存在（稳定项目标志）
      3. 零个候选 → raise WorkspaceNotFoundError
      4. 多个候选 → raise WorkspaceAmbiguousError
      5. 规范化: resolve() + normcase() (Windows)
      6. 不依赖 os.getcwd()
      7. 不依赖活动 project_path
      8. 不依赖环境变量（ZYNQ_RUNTIME_ROOT 是 overlay，不是依赖）
    """
```

### 7.3 验证规则

| 检查 | 失败行为 |
|------|---------|
| mcps/ 子目录不存在 | WorkspaceNotFoundError: "not a workspace: missing mcps/" |
| docs/brick_development_plan.md 不存在 | WorkspaceNotFoundError: "not a workspace: missing development plan" |
| 多个候选满足 | WorkspaceAmbiguousError: "ambiguous workspace" |
| 路径不可读 | WorkspaceNotFoundError |

### 7.4 workspace_id

```
workspace_id = "ws-" + sha256_hex(resolved_normcase_path)[:16]
```

例如：`D:\fpgaproject` → `ws-a1b2c3d4e5f6g7h8`

### 7.5 runtime_root

```
ZYNQ_RUNTIME_ROOT 环境变量（可选 override）
  → 用于测试环境
  → 生产配置不得引用测试目录
  → 默认: resolve_workspace_root() / ".zynq_runtime"
```

### 7.6 路径汇总

```
workspace_root = resolve_workspace_root()               # D:\fpgaproject (canonical)
runtime_root   = ZYNQ_RUNTIME_ROOT or (workspace_root / ".zynq_runtime")
ledger_path    = runtime_root / "execution_ledger.json"
owner_lock_path= runtime_root / "instance_owner.lock"
ledger_lock_path=runtime_root / "ledger.lock"
metadata_path  = runtime_root / "server_instance.json"
workspace_id   = "ws-" + SHA256(normcase(workspace_root))[:16]
```

### 7.7 测试要求

- Process/mock: 从临时目录结构创建项目标志（mcps/ + docs/brick_development_plan.md）；root 由测试逻辑独立计算；不依赖当前机器盘符
- Host-live: 当前机器可断言解析结果为 `D:\fpgaproject`；单独标记 host-live；不作为跨机器 process gate 前提
- 测试验证 normcase 后的路径一致性
- 测试验证 Zero/Ambiguous 候选 fail-closed

---

## 8. 修正 7: 重复请求行为

### 8.1 问题

v0.3.1 对所有 "相同 tool+args+session+stage" 的请求返回 DUPLICATE_REQUEST，不区分
"正在运行的任务"和"已经终态的任务"。

### 8.2 修正后的三路分支

**Case 1: 相同请求，Operation 正在 RUNNING**

```
返回:
  status: "success"              ← 不是 error
  data: {
    operation_id: "<existing>",
    status: "running",
    deduplicated: true,
    elapsed_s: 120,
    recommended_action: "Poll get_operation_status(<id>) or wait_operation(<id>, timeout_s)",
    poll_after_s: 10
  }
```

不启动第二个任务。返回现有 operation_id。

**Case 2: 相同请求，Operation 已终态**

```
返回:
  error:
    code: "LOCK_BUSY"
    details:
      reason_code: "CONFIRM_RETRY_REQUIRED"
      previous_operation_id: "<id>"
      previous_status: "FAILED"
      recommended_action: "Retry with retry:true to re-execute, or diagnose first"
```

终态不可自动重放。需要显式 retry 意图（参数中 `retry: true`）。

**Case 3: 不同请求，但资源冲突（不同 tool / 不同 args）**

```
返回:
  error:
    code: "LOCK_BUSY"
    details:
      reason_code: "CHANNEL_BUSY"
      active_operation_id: "<id>"
      ...
```

这是经典的 CHANNEL_BUSY——不同于重复请求。

---

## 9. 修正 8: close_session 行为

### 9.1 问题

v0.3.1 的 `close_session` 在存在 active_operation 时取消后台任务并关闭 Worker。
取消运行任务必须通过独立、显式、可审计的操作完成。

### 9.2 修正

```
close_session(session_id):

  1. 查询 Ledger:
     active_operation != null 且 status 为 RUNNING/ACCEPTED?
       → 返回 CHANNEL_BUSY
       → reason_code: "ACTIVE_OPERATION_PRESENT"
       → recommended_action: "Cancel or await operation first, then close session"
       → 不取消后台任务
       → 不关闭 Worker
       → 不删除 Context

  2. active_operation == null 或 status 为终态:
     → 正常关闭（参照 §13.3 shutdown 流程）
     → 释放此 Session 持有的所有资源
     → 删除 Context

取消任务通过独立 API（未来: cancel_operation），不嵌入 close_session。
如果底层工具不支持安全取消 → diagnose/recover，不能伪造 CANCELLED。
```

### 9.3 独立取消路径（规划）

```
cancel_operation(operation_id):
  1. 操作存在且 status != terminal?
     是 → 尝试安全取消
     否 → 返回 ALREADY_TERMINAL

  2. 底层工具支持取消?
     是 → 发送取消命令 → 等待确认 → CANCELLED
     否 → 返回 CANCEL_NOT_SUPPORTED
            → recommended_action: "await completion or use diagnose/recover"

  3. 取消失败/超时?
     → OUTCOME_UNKNOWN
     → Lane → RECOVERY_REQUIRED
```

B04 范围：当前不实现 `cancel_operation`，但 close_session 不得隐式取消。
Agent 必须通过显式操作管理任务生命周期。

---

## 10. 修正 9: 自动 Worker rebuild = 0

### 10.1 决定

D8 已确认：**自动 Worker rebuild 次数 = 0**。

### 10.2 规则

1. **普通 query 调用**: 不创建新 Worker。Worker DEAD → 返回结构化错误 + recover_execution 建议。
2. **同一存活连接上的纯传输重试**: 允许（TCP/stdio 重连），但：
   a. 必须记录 recovery event 到 Ledger
   b. 不启动新 EDA 子进程
   c. 不改变工程状态
   d. 仅重建 MCP SDK stdio session 到同一 Vivado 子进程
   e. 子进程已 DEAD → 返回错误；不启动新进程
3. **所有恢复动作**: 显式记录到 Ledger recovery_log。

---

## 11. 修正 10: ZynqContext 组合关系

### 11.1 问题

v0.3.1 可能需要在 B02 已冻结的 `mcps/common/context.py` 中添加字段。
**不得为统一 MCP 修改 B02 已冻结文件。**

### 11.2 组合方案

```python
# mcps/common/context.py — B02 FROZEN, 不修改

@dataclass
class MCPContext:
    session_id: str
    board_id: str
    project_path: str
    lease_holder: str | None
    created_at: str

# mcps/zynq_mcp/control/context.py — 新文件

from mcps.common.context import MCPContext

@dataclass
class ZynqContext:
    base: MCPContext                    # 包含 B02 所有冻结字段

    # Zynq 扩展字段（仅在统一 MCP 内部）
    board_package_revision: str
    current_stage: str                  # Workflow Stage
    platform_revision: str | None
    pl_revision: str | None
    ps_revision: str | None
    worker_generation: int
```

**ZynqContext.base.session_id 等字段通过 base 访问。B02 的 `create_session()` 返回 `MCPContext`；统一 MCP 的 `create_session()` 返回 `ZynqContext`（包装）。**

如果确实发现 B02 context.py 无法兼容统一 MCP 需求，先报告阻塞、列出具体冲突字段和原因，不得自行修改。

---

## 12. Execution Ledger 可变原子更新模型（保持 v0.3.1）

### 12.1 协议

```
获取 ledger.lock (exclusive)
  → 读取当前 ledger（若存在）
  → 内存构建新状态
  → 写入 <ledger_path>.tmp（完整写入）
  → os.fsync() 确保落盘
  → os.replace(<ledger_path>.tmp, <ledger_path>)
  → 释放 ledger.lock
```

`os.replace` 不是 `os.rename`。允许覆盖。`ledger_sequence` monotonic counter。

### 12.2 Ledger Schema（保持 v0.3.1 核心结构）

新增（v0.3.2）:
- `primary_instance_id`: 当前持有 owner lock 的 instance UUID
- `owner_lock_held_since`: ISO8601
- `takeover_count`: 累计 takeover 次数

---

## 13. 四套状态机

### 13.1 Execution Lane

```
IDLE → BUSY                          (submit_command ACCEPTED)

BUSY → IDLE:
  SUCCEEDED
  明确 FAILED + Worker/资源安全
  确认 CANCELLED + 底层完全停止

BUSY → RECOVERY_REQUIRED:
  TIMED_OUT
  INTERRUPTED
  OUTCOME_UNKNOWN
  仅取消等待但底层状态未知

RECOVERY_REQUIRED → IDLE:
  recover_execution() 返回 SUCCEEDED（全部 7 个前提满足）

RECOVERY_REQUIRED → RECOVERY_REQUIRED:
  diagnose_execution()

拒绝:
  BUSY + submit_command → CHANNEL_BUSY 或 deduplicated=true
  RECOVERY_REQUIRED + submit_command → PREVIOUS_OPERATION_UNRESOLVED
  IDLE + recover → ALREADY_IDLE
```

### 13.2 Operation

```
ACCEPTED → RUNNING → SUCCEEDED / FAILED / CANCELLED / TIMED_OUT / INTERRUPTED / OUTCOME_UNKNOWN
ACCEPTED → CANCELLED (显式 cancel)

终态不可再转换。
RETRY 是创建新 Operation（新 operation_id），不是旧 Operation 状态转换。
```

### 13.3 Worker

```
ABSENT → STARTING → READY ↔ BUSY
READY/BUSY → UNRESPONSIVE
UNRESPONSIVE → POISONED / READY (心跳恢复) / DEAD
READY/BUSY/UNRESPONSIVE/POISONED → STOPPING → DEAD → ABSENT
```

### 13.4 Workflow Stage（严格串行）

见 §4。

---

## 14. 健康/进度分离（保持 v0.3.1）

五维：worker_process_health / worker_heartbeat_health / operation_progress_state / tool_reported_status / outcome_confidence。

---

## 15. 配置化超时（保持 v0.3.1；Session 不得突破安全上限）

新增: Session 参数可以**降低** deadline（例如智能体希望更早超时），但**不得提高**超过代码默认值或环境变量上限。`deadline_max_s` 是硬上限。

---

## 16. 旧工具绕过关闭方案（修订）

最终产品 `.mcp.json` = `{"mcpServers": {"zynq": {...}}}`。无 `vivado`，无 `zynq_platform`，无 `zynq_pl`，无 `zynq_ps`。

C4 前可保留开发注册。C4 必须 Agent2 黑盒验证 `list_tools` 中无绕过 Gate 的入口。
旧 Vivado MCP 代码不删除，作为统一 MCP 内部 Adapter 复用。

---

## 17. 43 领域 API → 统一 MCP 映射（保持 v0.3.1）

不变。完整映射见 `B04_pl_mcp_adapter_plan.md` v0.3.2 §2.3。

---

## 18. 统一 zynq_mcp 目录树

```
mcps/zynq_mcp/
├── server.py
├── dispatcher.py
├── control/
│   ├── session.py, context.py
│   ├── execution_gate.py             # 10 点 Preflight Gate
│   ├── execution_ledger.py           # 持久化执行账本
│   ├── operation_registry.py        # Operation 状态机 + wait_operation
│   ├── single_worker.py             # SingleWorkerController
│   ├── instance_guard.py            # owner lock + takeover
│   ├── ledger_lock.py               # ledger.lock (shared/exclusive)
│   ├── process_guard.py             # 进程所有权验证
│   ├── recovery.py                  # diagnose / recover
│   ├── workspace.py                 # resolve_workspace_root() 唯一入口
│   ├── timeout_config.py
│   └── capabilities.py
├── domains/
│   ├── platform/ (B05), pl/ (B04 R4), ps/ (B06)
├── adapters/
│   ├── vivado/ (bridge.py, process.py, tcl_bridge.py)
│   ├── vitis/ (B06), xsct/ (B06), jtag/, uart/
└── tests/
```

---

## 19. R1 合并后的子步骤安排

| Sub-step | 内容 | 工具数 | 门禁关键 |
|----------|------|--------|---------|
| **R0** | 审计与迁移分类 ✅ | — | 审核方确认 v0.3.2 架构方向 |
| **R1** | 统一 skeleton + Session + Ledger + Preflight + SingleWorker + Instance Guard + Process Guard + Recovery（原 R1+R2 合并） | 9 | Ledger 可变原子更新；两类锁验证；Secondary takeover；TIMED_OUT→RECOVERY_REQUIRED；close_session 无隐式取消 |
| **R2** | 迁移 Vivado Bridge → PL Adapter | 9 | 54 tests 迁移通过；auto-rebuild=0 |
| **R3** | PL 领域 API 接入 | 9+N | pl_generate_system_top + Preflight + SingleWorker |
| **R4** | Agent2 黑盒验收 | — | list_tools 无绕过 Gate 入口；最终 .mcp.json 验证 |

---

## 20. 测试矩阵

### 20.1 R1xx: Skeleton + Session + Ledger + Preflight + Instance Guard — 30 tests

```
R101  zynq_mcp/server.py starts + MCP SDK handshake succeeds              Stdio
R102  create_session creates ZynqContext (composition over MCPContext)    Mock
R103  Two create_session: second shares same execution channel            Mock
R104  get_session_info returns context (stage, revisions, generation)     Mock
R105  get_capabilities returns domains + instance_role                    Mock
R106  list_tools: 9 tools = count match get_capabilities.implemented      Mock
R107  No NOT_IMPLEMENTED placeholder tools in list_tools                  Mock

R108  Instance Guard: instance_owner.lock + ledger.lock are separate files Mock
R109  Primary holds owner lock for entire server lifetime (multiple ledger writes while owner lock held) Mock
R110  Primary: ledger RMW under ledger.lock exclusive (short-held)        Mock
R111  Secondary: reads ledger under ledger.lock shared                    Mock
R112  Secondary: set/command → INSTANCE_ALREADY_RUNNING + primary info    Mock
R113  Secondary: does NOT create EDA Worker                               Mock

R114  Ledger: atomic RMW → close MCP → restart → state recovered         Stdio
R115  Ledger: crash during tmp write → old complete ledger intact         Mock
R116  Ledger: ledger_sequence monotonic increment                         Mock
R117  Ledger: os.replace not os.rename (allows overwrite for mutable)     Mock

R118  Preflight P1: active operation → CHANNEL_BUSY + structured busy     Mock
R119  Preflight P5: heartbeat stale → WORKER_UNRESPONSIVE                 Mock
R120  Preflight P6: previous OUTCOME_UNKNOWN → PREVIOUS_OPERATION_UNRESOLVED Mock
R121  Preflight P7: synthesis not SUCCEEDED → STAGE_PREREQUISITE_UNMET    Mock
R122  Preflight P7: legal ROLLBACK_FIX (timing fail→PL_BUILD) ALLOWED    Mock
R123  Preflight P7: illegal skip (PL_BITSTREAM→CONSISTENCY_CHECK without PS_BUILD) REJECTED Mock
R124  Preflight P7: illegal skip (PLATFORM_DESIGN→PL_BUILD without PL_GENERATE) REJECTED Mock
R125  Preflight P10: same request RUNNING → existing_operation_id + deduplicated=true Mock
R126  Preflight: same request TERMINAL → CONFIRM_RETRY_REQUIRED           Mock
R127  Preflight: different request but resource conflict → CHANNEL_BUSY   Mock

R128  TIMED_OUT: Lane → RECOVERY_REQUIRED (NOT IDLE)                      Mock
R129  OUTCOME_UNKNOWN blocks all domain set/command                       Mock
R130  recover_execution: 全部 7 个前提满足 → IDLE                          Mock
```

### 20.2 R2xx: PL Adapter Migration — 15 tests

```
R201  Adapter: BridgeOwner starts via SingleWorkerController             Stdio
R202  Adapter: PID captured via SDK hook (migrated from test_t001_t002)  Stdio
R203  Adapter: tool call forwarded; B02 ToolResponse returned            Mock
R204  Adapter: crash → worker POISONED → Operation OUTCOME_UNKNOWN      Mock
R205  Adapter: timeout → worker tree killed; PID verified gone           Mock
R206  Adapter: context_ref=session_id in ToolResponse                    Mock
R207  Adapter: server path from resolve_workspace_root()                 Mock
R208  resolve_workspace_root(): from temp dir structure with project markers → correct canonical result (MOCK: no hardcoded D:\fpgaproject) Mock
R209  resolve_workspace_root(): fail-closed on zero candidates           Mock
R210  resolve_workspace_root(): fail-closed on ambiguous candidates      Mock
R211  Adapter: .mcp.json not read/written                                Mock
R212  Adapter: real MCP handshake → list_tools=27 → shutdown clean       Stdio
R213  Adapter: close_session cleanup order                               Mock
R214  Adapter: shutdown PID verified not alive after shutdown            Stdio
R215  Adapter: auto-rebuild = 0 (query-stateless no new worker)          Mock
```

### 20.3 R3xx: PL Domain API — 11 tests

```
R301  pl_generate_system_top: valid Verilog output                       Mock
R302  pl_generate_system_top: instantiates BD wrapper by correct name     Mock
R303  pl_generate_system_top: port direction + bus width preserved        Mock
R304  pl_generate_system_top: escaped identifiers handled                 Mock
R305  pl_generate_system_top: malformed wrapper → fail-closed             Mock
R306  pl_generate_system_top: deterministic output                        Mock
R307  pl_generate_system_top: Platform Manifest binding (single match)    Mock
R308  pl_generate_system_top: Platform Manifest binding (zero/multi/board) Mock
R309  pl_generate_system_top: through preflight → SingleWorker → Adapter  Mock
R310  PL domain: list_tools count incremented                             Mock
R311  PL domain: get_capabilities domains.pl.implemented incremented      Mock
```

### 20.4 R4xx: Integration & Regression — 15 tests

```
R401  Full unified flow: create_session → PL domain API → real handshake Stdio
R402  close_session with active_operation → CHANNEL_BUSY (not cancel)    Mock
R403  close_session with ACCEPTED/RUNNING → CHANNEL_BUSY (task NOT cancelled; worker NOT closed; context NOT deleted; ledger NOT written CANCELLED) Mock
R404  Same request RUNNING → deduplicated=true; call count still 1       Mock
R405  B02+B03 regression: 367 passed, 1 skipped, 0 new failures          Mock
R406  Old mcps/pl_mcp/ Sub-step 1 tests still pass (54 tests)            Stdio
R407  workspace_root: different cwd → same resolve_workspace_root()      Stdio
R408  workspace_root: different project_path → shared execution channel  Mock
R409  Primary crash → Secondary takeover → RECOVERY_REQUIRED              Stdio
R410  Takeover: worker PID alive → abandon takeover; return WORKER_STILL_ALIVE Mock
R411  Takeover: old active operation RUNNING → ledger → INTERRUPTED/OUTCOME_UNKNOWN Mock
R412  Takeover: no active operation → safe takeover                       Mock
R413  wait_operation: PID alive + no progress → operation_progress_state=UNKNOWN Mock
R414  wait_operation: returns on SUCCEEDED within timeout                 Mock
R415  wait_operation: returns still_running on timeout (background continues) Mock
```

### 20.5 R5xx: Agent2 Black-Box Gate — 14 tests

```
R501  Agent2 discovers unified zynq capabilities (domains grouped)       Stdio
R502  Agent2 creates session, verifies unified context                   Stdio
R503  Agent2 calls all available domain APIs, verifies ToolResponse      Stdio
R504  Agent2 verifies command → operation_id → get_operation_status      Stdio
R505  Agent2 verifies wait_operation with timeout                         Stdio
R506  Agent2 verifies preflight: duplicate request → deduplicated=true   Stdio
R507  Agent2 verifies preflight: active operation → structured busy      Stdio
R508  Agent2 verifies instance guard: secondary rejected                  Stdio
R509  Agent2 verifies takeover: RECOVERY_REQUIRED after Primary crash     Stdio
R510  Agent2 uses public fixture: generate_system_top                     Host-live
R511  Agent2 closes session, verifies cleanup                             Stdio
R512  [DEFERRED to C4/B09] Final .mcp.json has only zynq entry — B04 R4 validates the migration plan statically; actual .mcp.json switch deferred to C4
R513  [DEFERRED to C4/B09] list_tools contains NO bypass entry — B04 cannot modify the frozen .mcp.json; C4 verifies the final product config
R514  Agent2 verifies: capabilities.implemented == list_tools count == handler count Stdio
```

### 20.6 数量汇总

| Series | Count | Mandatory | Mock | Stdio | Host-live |
|--------|-------|-----------|------|-------|-----------|
| R1xx | 30 | 30 | 28 | 2 | 0 |
| R2xx | 15 | 15 | 11 | 4 | 0 |
| R3xx | 11 | 11 | 11 | 0 | 0 |
| R4xx | 15 | 15 | 10 | 3 | 0 |
| R5xx | 14 | 14 | 0 | 12 | 1 (+1 Agent2) |
| **Total** | **85** | **85** | **60** | **21** | **1 (+3)** |

**30+15+11+15+14 = 85 ✅**

### 20.7 现有 54 Tests 处置（不变）

| 类别 | 数量 |
|------|------|
| 保留（迁移至新路径） | 20 |
| 适应（修改后使用） | 15 |
| 废弃（pool/并发/auto-retry） | 8 |
| NA / 不涉及 | 2 |
| Parse/Convert（保留原位） | 9 |
| **Total** | **54** ✅ |

### 20.8 关键机器可判定条件

| # | Condition | Test |
|---|-----------|------|
| 1 | instance_owner.lock 与 ledger.lock 是不同文件 | R108 |
| 2 | Primary 终身持有 owner lock，但可多次更新 Ledger | R109 |
| 3 | Secondary 可使用 ledger read lock 查询 | R111 |
| 4 | Primary 崩溃后 Secondary 可争抢 owner lock | R409 |
| 5 | Takeover 遇到旧 RUNNING → RECOVERY_REQUIRED | R411 |
| 6 | TIMED_OUT 不得回到 IDLE | R128 |
| 7 | close_session does NOT cancel active task (CHANNEL_BUSY; background task still running; worker alive) | R403 |
| 8 | close_session 不取消活动任务 | R402 |
| 9 | 相同请求返回 existing_operation_id，调用次数仍为 1 | R404 |
| 10 | PL_BITSTREAM 后必须进入 PS_BUILD | R123 |
| 11 | RESOLVE_WORKSPACE_ROOT() 精确得到 D:\fpgaproject | R208 |
| 12 | R1 每个已注册工具都有真实行为 | R106 |
| 13 | get_capabilities 数量与 list_tools/handler 数量机械一致 | R106, R514 |
| 14 | 最终 .mcp.json 不含 vivado 和三个旧 zynq_* 注册 | R513 |
| 15 | 最终产品 list_tools 无绕过 Gate 的工具 | R512 |
| 16 | B02+B03 回归: 367 passed, 1 skipped, 0 new failures | R405 |

---

## 21. 待审核决定确认

| # | 决定 | v0.3.2 结论 |
|---|------|-----------|
| D1 | runtime_root | 通过 resolve_workspace_root() 推导；默认 `.zynq_runtime` |
| D2 | workspace_id | SHA256 of normcase(resolved workspace_root path) |
| D3 | Secondary 只读 | 允许 |
| D4 | 旧入口最终移除 | C4：移除所有旧注册（含 vivado） |
| D5 | wait max timeout | 300s 可调上限 |
| D6 | 心跳 | 30s/60s 默认 |
| D7 | deadline | 可配置默认值 |
| D8 | 自动 rebuild | **= 0** |
| D9 | timeout 配置 | 默认→环境变量→Session（不得突破安全上限） |
| D10 | .mcp.json 最终保留 | 仅 zynq |
| D11 | B04 定义 | 统一 Zynq MCP 基础入口 + PL 首个接入 |
| D12 | 本轮实现 | 继续不创建生产代码 |

---

## 22. 冻结资产未修改证明

| Asset | Verified |
|-------|---------|
| B00/B01/B02/B03 交付物 | 未触碰 |
| `Xilinx_Vivado_MCP/` | 未触碰 |
| `.mcp.json` (SHA256 `f48fc9a8...`) | 未触碰 |
| `mcps/platform_mcp/`, `mcps/pl_mcp/`, `mcps/ps_mcp/` | 未触碰 |
| `mcps/common/context.py` (B02 frozen) | 未触碰 |
| B02+B03 回归: 367 passed, 1 skipped | ✅ |
| B04 Sub-step 1: 54 passed | ✅ |
| 全量 mcps/: 441 passed, 1 skipped | ✅ |

---

## 23. 修改的文档

| Document | Change | Version |
|----------|--------|---------|
| `docs/development/mcp/B04_single_channel_audit.md` | 完全重写（本文档） | v0.3.2 |
| `docs/development/mcp/B04_pl_mcp_adapter_plan.md` | 更新至 v0.3.2 | v0.3.2 |
| `docs/development/tests/B04_pl_mcp_adapter_test_plan.md` | 更新至 v0.3.2 | v0.3.2 |
| `docs/reference/synthpilot_comparison_report.md` | 保持 v0.3 更新 | v0.3 |

---

## 24. Declaration

**统一 Zynq MCP 实现尚未开始。R1 尚未开始。当前仅完成 v0.3.2 规划和审计修正。**

本轮：
- ✗ 未创建 `mcps/zynq_mcp/` 生产实现
- ✗ 未修改 `.mcp.json`
- ✗ 未删除三个旧 MCP skeleton
- ✗ 未迁移 PL Bridge
- ✗ 未实现任何领域 API
- ✗ 未实现 Execution Ledger / Preflight Gate / Instance Guard
- ✗ 未进入 B05/B06
- ✗ 未调用 Agent2
- ✗ 未冻结 B04 Sub-step 1
- ✗ 未修改任何生产代码
- ✗ 未修改 B02 frozen context.py
