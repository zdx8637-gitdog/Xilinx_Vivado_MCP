# Brick 分阶段测试工作流架构 v1.3

> Date: 2026-08-07 | Status: DRAFT (Revision 4)
> Depends: `docs/brick_development_plan.md` v0.4, `docs/architecture_ai_zynq7020.md` v2.3.1
> Scope: 三 Agent 角色分离、阶段矩阵、证据等级、黑盒测试项目结构、跨域交接门禁、Precondition Provisioning

---

## 1. 目的

本项目采用多 Agent 合作模式，将白盒实现、阶段黑盒验收、最终黑盒复现分离为三个独立 Agent 角色。本文档定义三者的职责边界、交接门禁、证据等级、黑盒测试项目的生命周期契约和前置状态供应规则。

---

## 2. Agent 角色定义

### 2.1 Agent1 — 白盒实现者（长期上下文）

**职责**：
- 生产实现、白盒契约测试、故障注入、回归。
- 负责阶段黑盒测试项目的代码、fixtures、runner、证据收集和报告模板。
- 向 Agent3 提供：公开输入契约、预期输出规范、项目目录、Fixtures、runner 脚本和报告模板。
- Agent1 **不得**把自己的黑盒测试结果视为独立黑盒验收。
- Agent1 **不得**替 Agent3 运行黑盒测试并声称结果有效。
- Agent1 可以使用 CommandRunner、Ledger 内部读取、内部 handler 和所有冻结的内部 API。

**边界**：
- Agent1 的上下文可能包含多轮对话、内部实现细节和设计决策。
- Agent1 可以为 Agent3 准备 fixtures 和 runner，但 fixtures 中不得包含 Agent1 的私密上下文路径或内部会话状态。

### 2.2 Agent3 — 阶段黑盒验收者（全新上下文）

**职责**：
- 每个阶段完成后，由 Manager Reviewer 以全新上下文调用。
- 只根据公开契约、统一 zynq_mcp、Skill、Board Profile 和干净输入进行阶段黑盒验收。
- 收集原始 MCP 响应、Operation ID、artifact revision、manifest SHA、进程 PID、UART 日志等证据。
- 根据预期输出规范判定 PASS/FAIL，提交结构化验收报告。

**通用禁止规则（所有阶段）**：
- 不得读取 Agent1 对话日志。
- 不得读取 Agent1 内部测试代码（test_r3_*.py 等）。
- 不得直接调用 CommandRunner 或内部 handler。
- 不得使用 `run_tcl` 或隐藏 Tcl 脚本。
- 不得导入 `mcps.zynq_mcp` 内部模块。
- 不得使用 Agent1 私有对话、私有 token、隐藏 context 或内部测试状态。
- 不得创建、修改、读取或验证 Ledger 文件。Agent3 只能通过公开 MCP API（如 `get_execution_state`）观察运行时状态。

**Session 建立规则（按阶段类型区分）**：

| 阶段类型 | Session 建立方式 | 适用阶段示例 |
|---------|----------------|------------|
| `FRESH_SESSION` | Agent3 必须通过公开 `create_session` 建立会话。不得使用任何预置 session/context。 | B05, B06, B07, B08, B09 |
| `PRECONDITIONED_SESSION` | 允许使用 Manager Reviewer 或受控 harness 交付的隔离 runtime 和必要的预置 Ledger/context 状态。Agent3 不得创建、修改、读取或验证 Ledger 文件，也不得自行创建 precondition。Agent3 必须通过公开 MCP API（`get_execution_state`、`get_session_info`）观察初始 stage、lane、worker 和 session/context 状态，并在报告中标注为 `PRECONDITION_OBSERVED`。 | B04 R3.1-C |

**至少可用的公开入口**：
- `python -m mcps.zynq_mcp.server`（MCP Server 启动）
- `mcp.client.stdio.stdio_client` + `mcp.ClientSession`（MCP SDK 连接）
- `ClientSession.call_tool()`（所有已注册公开工具的调用）
- `ClientSession.list_tools()`（能力发现）
- `Board Profile JSON`（只读输入）
- Agent1 提供的 Fixture 目录（如 `b04_pl_ready/`）
- Agent1 提供的预期输出规范（如 `expected_outputs/` 目录）
- Manager Reviewer 交付的 preconditioned runtime（仅限 `PRECONDITIONED_SESSION` 阶段）

### 2.3 Agent2 — 最终黑盒复现者（全新上下文）

**职责**：
- 只在 B08 完成后调用。
- 对应 B09：从干净工作目录，根据需求、统一 Skill、已注册 zynq_mcp 和板卡资料，独立复现完整 T02 GPIO 工作流。
- Agent2 收到的是 Skill 文档、MCP API 文档和板卡配置说明——不是 Agent1 的实现或对话。

**禁止提前调用**：
- Agent2 不得被用于 R3.1-C 或单个 Domain 的阶段验收。
- Agent2 不等同于 Agent3。
- Agent2 只在 B08 全面完成后由 Manager Reviewer 显式调用。

**与 Agent3 的区别**：
| | Agent3 | Agent2 |
|---|--------|--------|
| 调用时机 | 每个阶段完成后 | B09 only |
| 范围 | 单阶段公开 MCP API | 完整 GPIO Workflow |
| 输入 | Agent1 准备的 fixtures + 公开契约 | 需求 + Skill + MCP 文档 + 板卡资料 |
| 输出 | 阶段验收报告 | T02 完整复现 + 故障注入验证 |
| 是否可以依赖 Agent1 fixtures | 是 | 否（从零开始） |

---

## 3. 阶段测试矩阵

### 3.1 完整矩阵

| # | 阶段 | 开发交付 | Agent1 白盒门禁 | Agent3 阶段黑盒 | 用户硬件验收 | 输入 Artifact 来源 | 失败回退 | 进入下一阶段条件 |
|---|------|---------|----------------|----------------|------------|-------------------|---------|----------------|
| 1 | **B04 R3.1-C** | PL public API (1 tool) | Contract + SDK tests (25 passed) | R3.1-C public MCP smoke（Agent3；preconditioned） | 否 | Precondition fixture (see §4) | R3.1-C Agent1 | R3.1-C freeze confirmed + Agent3 smoke PASS |
| 2 | **B04 R3.2** | Build Pipeline: create_project, set_top, synthesize, place_and_route, analyze_timing (5 APIs) | worker/stage/recovery/evidence tests (~16) | PL host-live black-box（Agent3；需 Vivado Worker） | 否 | R3.1-B system_top.v + preconditioned PL_GENERATE stage → PL_BUILD | R3.2 Agent1 | Agent1 白盒通过 + Agent3 host-live PASS |
| 3 | **B04 R3.3** | Bitstream + PL Build Manifest: generate_bitstream (1 API) | manifest schema/revision/cross-ref tests (~12) | PL host-live black-box（Agent3；需 Vivado Worker + 时序闭环） | 否 | R3.2 output: synthesized DCP + timing evidence | R3.3 Agent1 | Agent1 白盒通过 + Agent3 host-live PASS |
| 4 | **B04 R3.4** | JTAG: connect_hw_server, open_hw_target, select_device, program, get_device_status (5 APIs) | JTAG lease/P9/recovery tests (~14) | JTAG hardware-live black-box（Agent3；需 hw_server + 板卡） | 是（需用户确认识别物理 DONE LED） | R3.3 output: .bit + PL Build Manifest | R3.4 Agent1 | Agent1 白盒通过 + Agent3 HW-live PASS + user confirms |
| 5 | **B04 R3.5** | Integration Gate | 全量回归 + list_tools=21 | R3.5 Agent3 integration black-box gate（见 §3.3） | 否（不触发硬件时）；是（如重新编程板卡则需用户确认） | R3.2–R3.4 Agent3-accepted artifacts | R3.2–R3.5 Agent1 | Agent1 白盒通过 + Agent3 integration PASS；如涉及硬件另需 user confirms |
| 6 | **B05** | Platform/AXI/GPIO Domain | BD/XSA/Manifest tests | Platform public workflow（Agent3；需 Vivado IPI） | 通常否 | Board Profile + PS7 preset + B01 skill spec | B05 Agent1 | Agent1 白盒通过 + Agent3 PASS |
| 7 | **B06** | PS/ARM/JTAG/UART Domain | build/deploy/recovery tests | PS public workflow（Agent3；需 hw_server + UART） | 是（用户确认 UART 输出） | B05 Platform XSA + Manifest | B06 Agent1 | Agent1 白盒通过 + Agent3 HW-live PASS + user confirms UART |
| 8 | **B07** | 统一 Skill GPIO Workflow | Skill + MCP contract tests | Skill phase black-box（Agent3；综合全部 MCP APIs） | 可需 | B05 + B06 artifacts; Skill documents | B07 Agent1 或上游 | Agent1 白盒通过 + Agent3 PASS |
| 9 | **B08** | Agent1 GPIO 白盒验收 + 故障注入 | T00/T01/T02 + F001–F006 | 不替代 Agent3 | 用户确认真实硬件结果 | All prior artifacts | B08 Agent1 | All required tests pass + user confirms HW results |
| 10 | **B09** | Agent2 独立黑盒复现 | 不适用 | Agent2 final black-box | 用户确认硬件结果 | Clean workspace + Skill + MCP docs + Board Profile | B08 Agent1 | Agent2 passes T02 independently |
| 11 | **B10** | GPIO v1 冻结 | 全部证据归档 | B09 通过 | 用户确认基线 | All B09 evidence | B10 (archive only) | User confirms GPIO v1 as stable baseline |

### 3.2 R3.1-C 特殊约束

- R3.1-C 是 `PRECONDITIONED_SESSION` 阶段。Agent3 使用 Manager Reviewer 或受控 harness 交付的 runtime（已预置 `PL_GENERATE` stage 的 `execution_ledger.json`）。
- Agent3 不得创建、修改、读取或验证 Ledger 文件。Agent3 通过公开 `get_execution_state` / `get_session_info` 观察初始状态，并在报告中标注 `PRECONDITION_OBSERVED`。
- 该测试只能称为 **"R3.1-C preconditioned public MCP smoke"**（Agent3 执行）。
- 它不得称为 B09；不得声称覆盖 `create_session → PL_GENERATE` 完整用户流程。
- 只有 B05–B08 完成后，Agent2 才能执行 B09。
- 前置状态由 Manager Reviewer 或受控验收 harness 预置，见 §4。

### 3.3 B04 R3.5 — Agent3 Integration Black-Box Gate

R3.5 是 PL Domain 的集成门禁。它不新增公开 API，但必须验证全链路累计行为。R3.5 拆分为两个 profile：

#### 3.3.1 R3.5_HOST_INTEGRATION（默认、必须执行）

```
validation_projects/phase_blackbox/r3_5_integration/
├── README.md
├── fixture_manifest.md
├── expected_outputs/
├── runner.py
├── evidence/
│   ├── responses/
│   ├── artifacts/
│   ├── operation_logs/
│   └── report.md
└── cleanup.py
```

**Agent3 执行规则**：

- 自行启动 `python -m mcps.zynq_mcp.server`（FRESH_SESSION，通过公开 `create_session` 建立会话）。
- 仅通过 MCP SDK ClientSession 调用公开 MCP 工具。
- 输入必须来自 R3.2、R3.3、R3.4 已被 Agent3 接受的 artifact。

**默认通过判据**（全部必须满足；不调用硬件操作）：

1. 调用 `pl_create_project` → `pl_set_top` → `pl_synthesize` → `pl_place_and_route` → `pl_analyze_timing` → `pl_generate_bitstream`（不含 `pl_program`），每步经历完整 Operation 生命周期，每步后 `execution_lane == IDLE`，stage 原子推进。
2. `list_tools == 21`。
3. 9 个控制 API 仍可用并返回合法响应。
4. 12 个 PL API 的公开 inputSchema 与 B01 签名一致。
5. 构造一个确定性 stage rejection 场景，验证 `LOCK_BUSY + STAGE_PREREQUISITE_UNMET`，lane 回到 `IDLE`，不进入 `RECOVERY_REQUIRED`。
6. 跨 R-step artifact SHA 链一致。
7. 原始 MCP 响应、operation 日志、execution_state 快照完整保存。

#### 3.3.2 R3.5_HARDWARE_REPLAY（可选附加 profile）

**前提条件**：

- R3.4 已完成并通过用户确认。
- 用户明确授权重新操作硬件。
- Manager Reviewer 在 Agent3 prompt 中显式启用。

**附加操作**：

- 可调用 `pl_program`、`pl_get_device_status` 等硬件工具。
- 需要保存完整 MCP response、operation log、device status 和硬件日志。
- Agent3 只能报告软件/协议结果（工具返回的 JSON、operation terminal status）。
- 物理现象（DONE LED、板卡状态）必须由用户确认——Agent3 不得单独宣称硬件通过。

**报告要求**：

- 如果 `R3.5_HARDWARE_REPLAY` 未执行，R3.5 报告必须写明 `HARDWARE_REPLAY=NOT_RUN`，不能写成硬件通过。
- 如果 `R3.5_HARDWARE_REPLAY` 执行了，报告应标注 `HARDWARE_REPLAY=RUN, USER CONFIRMATION PENDING`。

**进入条件判定**：

- `R3.5_HOST_INTEGRATION` PASS + `R3.5_HARDWARE_REPLAY` (NOT_RUN 或 PASS with user confirm) → B04 complete。
- 只以实际执行的 profile 结果判定——未运行的 profile 不构成阻塞。

---

## 4. Precondition Provisioning

### 4.1 定义

部分阶段在 Agent3 执行时，所需的 Workflow Stage 无法通过当前已实现的公开 API 达到（例如 R3.1-C 的 `PL_GENERATE` 需要 Platform API 才能生成，但 Platform API 在 B05 才实现）。

此类阶段允许使用 **受控前置状态（preconditioned state）**——由 Manager Reviewer 或受控验收 harness 在 Agent3 启动前通过生产 `ledger_transaction` 预置 `execution_ledger.json`。

### 4.2 责任边界

| 角色 | 职责 |
|------|------|
| **Manager Reviewer / 验收 harness** | 创建隔离的 runtime/workspace；使用生产 `ledger_transaction` 写入前置状态；记录 workspace 路径、board profile SHA、platform revision、project input SHA、初始 worker/lane 状态；将 workspace 交付给 Agent3 |
| **Agent1** | 在 `expected_outputs.json` 中标注哪些场景依赖 precondition；提供预期 precondition 状态值的规范；Runner 和测试说明不得包含 precondition 创建逻辑 |
| **Agent3** | 只能使用已交付的 workspace 启动 MCP Server 并调用公开 API；不得读取、修改或验证 Ledger 文件；不得创建自己的 precondition；证据报告中必须记录初始 `get_execution_state` 返回的 stage/lane/worker 状态 |

### 4.3 Precondition 记录规范

验收 harness 在交付 workspace 时必须记录：

```
- workspace/runtime 路径
- board_profile_sha256
- platform_revision（如果设置）
- project_path
- 初始 current_stage（从 get_execution_state 读取）
- 初始 execution_lane（从 get_execution_state 读取）
- 初始 worker_state / worker_pid（从 get_execution_state 读取）
- project input SHA（如 bd_wrapper SHA、manifest revision）
- precondition 生成命令/脚本引用（供复审）
```

### 4.4 R3.1-C Smoke 证据范围

- R3.1-C smoke 的证据范围必须明确标注为 **"preconditioned public MCP smoke"**。
- 它不是完整的 `create_session` → `PL_GENERATE` 用户流程——`PL_GENERATE` 是前置状态。
- Agent3 报告必须记录初始 stage `PL_GENERATE` 来自 precondition，非其自身调用。
- `expected_outputs.json` 中不得同时要求 Agent3 调用 `create_session`，除非该场景确实从空 Ledger 开始并能通过已实现的公开 API 进入所需阶段。

### 4.5 Fixture 标注

所有 fixtures 必须标注其来源类型：

| 标注 | 含义 | 例 |
|------|------|---|
| `frozen reference` | 已冻结、不变的测试 fixture | `b04_pl_ready/bd_wrapper_realistic.v` |
| `precondition fixture` | 由验收 harness 预置的 runtime/Ledger 状态 | 预置 `PL_GENERATE` 的 `execution_ledger.json` |
| `Agent3 accepted artifact` | 由 Agent3 在某阶段验收中生成并通过的产物 | B05 输出的 Platform Manifest |
| `Agent2 clean-input artifact` | 由 Agent2 从零生成的产物 | B09 输出的 system_top.v |

---

## 5. Agent3 Runner 规则

### 5.1 Agent1 提供 Runner

Agent1 可以为 Agent3 提供参考 runner（`runner.py`），但：

- Runner 只能作为可审查的启动辅助，**不是黑盒证据**。
- Runner 必须声明其全部 MCP 调用（每个 call_tool 的工具名和参数）。
- Runner 不得导入 `mcps.zynq_mcp` 内部模块。
- Runner 不得调用 `CommandRunner`、内部 handler、`DomainExecutionMutex`。
- Runner 不得读取或写入 `execution_ledger.json`。
- Runner 不得使用 `run_tcl`。

### 5.2 Agent3 自行验证

- Agent3 可以自行编写最小 MCP SDK runner。
- Agent1 runner 的执行结果不能单独证明黑盒通过。
- 黑盒证据必须来自 Agent3 **自己启动的 server** 和**自己的公开 MCP 调用**。
- Agent3 应审查 runner 代码，确认无违规调用后方可使用。

---

## 6. 证据等级体系

每个能力在交付时必须标记为以下等级之一，不得越级描述：

| 等级 | 含义 | 确认方式 |
|------|------|---------|
| `NOT_IMPLEMENTED` | 尚未实现 | 无 |
| `IMPLEMENTED` | 生产代码存在 | 代码审查 |
| `WHITEBOX_TESTED` | 通过 Agent1 白盒测试（可直接调用内部 API） | pytest 通过 |
| `PUBLIC_MCP_SDK_TESTED` | 通过真实 MCP SDK ClientSession 调用 | test_r3_1c_public.py 通过 |
| `PHASE_BLACKBOX_HOST_LIVE` | 通过 Agent3 阶段黑盒（无需硬件） | Agent3 验收报告 |
| `PHASE_BLACKBOX_HARDWARE_LIVE` | 通过 Agent3 阶段黑盒（需要真实硬件） | Agent3 验收报告 + 硬件日志 |
| `FINAL_BLACKBOX_B09` | 通过 Agent2 B09 完整黑盒复现 | Agent2 验收报告 |
| `HARDWARE_USER_ACCEPTED` | 用户确认真实硬件现象 | 用户签字/确认记录 |
| `DEFERRED` | 明确延后到指定阶段 | — |

---

## 7. 阶段黑盒测试项目结构

每个阶段需要接受 Agent3 黑盒验收时，Agent1 必须在以下目录创建黑盒测试项目：

```
validation_projects/
├── golden/                        # 黄金参考设计（breath_led）
├── faults/                        # 故障注入设计
└── phase_blackbox/                # 阶段黑盒测试
    └── r3_1c_smoke/
        ├── README.md              # 入口说明、Agent3 prompt、前置条件、precondition 标注
        ├── inputs/                # 干净输入（fixtures、board profile 引用）
        │   ├── fixture_manifest.md
        │   └── b04_pl_ready/      # 从冻结 fixture 目录纯复制
        ├── expected_outputs/      # 机器可判定的预期输出
        │   ├── expected_success.json
        │   ├── expected_fail_missing_revision.json
        │   └── expected_stage_blocked.json
        ├── runner.py              # 仅使用 MCP SDK ClientSession 的公开 runner
        ├── evidence/              # Agent3 运行后填充
        │   ├── responses/         # 原始 MCP JSON 响应
        │   ├── artifacts/         # system_top.v 等生成文件
        │   ├── operation_logs/    # operation_id → terminal 追踪
        │   └── report.md          # Agent3 验收报告模板
        └── cleanup.py             # 运行后资源清理
```

### 7.1 干净输入目录策略

Agent3 的工作目录在每次验收启动前由 Manager Reviewer 分配。Agent1 准备的 `inputs/` 目录不得包含：

- Agent1 对话日志
- 内部测试代码引用
- Ledger 快照文件
- 预置 context/session token
- 硬编码路径（所有路径通过 Board Profile 或环境变量注入）

允许的输入：
- 从冻结 fixture 目录（如 `b04_pl_ready/`）纯复制的 fixture 文件
- Board Profile 引用（通过 `ZYNQ_BOARD_PROFILE_DIRS`）
- 公开契约文档引用
- 阶段验收 prompt（写入 README.md）

### 7.2 固定公开输入和预期输出

每个阶段黑盒项目必须定义：
- 至少一个正向成功场景的完整输入和预期输出
- 至少一个负向失败场景的完整输入和预期 reason_code
- 至少一个 admission 拒绝场景的预期 reason_code

预期输出规范格式（`expected_outputs/*.json`）：

```json
{
  "scenario": "r3_1c_success",
  "precondition": true,
  "precondition_stage": "PL_GENERATE",
  "description": "pl_generate_system_top with valid wrapper and manifest",
  "calls": [
    {"tool": "pl_generate_system_top", "args": {"wrapper_path": "hdl/bd_wrapper_realistic.v"}, "expect": {"status": "success", "data.status": "accepted"}},
    {"tool": "wait_operation", "args": {"operation_id": "$prev.operation_id"}, "expect": {"status": "success", "data.status": "SUCCEEDED"}},
    {"tool": "get_execution_state", "args": {}, "expect": {"data.execution_lane": "IDLE", "data.current_stage": "PL_BUILD", "data.worker_state": "ABSENT"}}
  ]
}
```

带有 `"precondition": true` 的场景不使用 `create_session`——初始状态由验收 harness 提供。

### 7.3 证据保存规则

Agent3 在执行后必须将以下证据保存到 `evidence/` 目录：

| 证据类型 | 目录 | 格式 |
|---------|------|------|
| 原始 MCP JSON 响应 | `evidence/responses/` | `{step_index}_{tool_name}_{oid}.json` |
| Artifact 文件（如 system_top.v） | `evidence/artifacts/` | 原始文件 + `{filename}.sha256` |
| Operation 追踪日志 | `evidence/operation_logs/` | `{operation_id}.json` 包含 accepted/running/terminal 时间 |
| Stage/Lane 快照 | `evidence/operation_logs/` | 每个关键步骤后的 `get_execution_state` 响应 |
| 进程 PID | 手动记录在 `evidence/report.md` | 从 `get_execution_state.worker_pid` 或 OS 命令 |

### 7.4 报告格式

Agent3 的验收报告（`evidence/report.md`）必须包含：

```markdown
# Phase Black-Box Acceptance Report — [阶段名称]

- **Agent**: Agent3
- **Date**: ...
- **Phase**: B04 R3.1-C
- **Precondition**: Yes, stage=PL_GENERATE provided by harness (see §4)
- **Runner**: runner.py (MCP SDK ClientSession only) / or custom Agent3 runner
- **Board Profile**: ALINX_AX7020_v1.0 (verified SHA: ...)
- **list_tools before**: [列出所有工具名]
- **list_tools after**: [列出所有工具名，确认无变化]

## Results

| # | Scenario | Expected reason_code | Actual reason_code | PASS/FAIL |
|---|----------|---------------------|-------------------|-----------|

## Evidence

- Operation IDs: ...
- Artifact SHAs: ...
- Worker PID: ...

## Declarations

- Agent3 was invoked with fresh context.
- Only public MCP APIs were used.
- No internal modules, Ledger files, or hidden scripts were used.
- Preconditioned state was accepted as-is; Agent3 did not create or verify it.
```

---

## 8. 阶段黑盒生命周期证据要求

Agent3 必须根据阶段类型覆盖适用的生命周期节点。每个节点定义了 `required`/`conditional`/`not-applicable` 三种适用性。

| # | 生命周期节点 | 公开 MCP 调用 | 适用性 | 说明 |
|---|-----------|-------------|--------|------|
| 1 | Session / Context 身份 | `create_session` + `get_session_info` | `required` for FRESH_SESSION; `conditional` for PRECONDITIONED_SESSION | FRESH_SESSION：Agent3 自行 create_session。PRECONDITIONED_SESSION：不调用 create_session；Agent3 通过 `get_session_info` / `get_execution_state` 观察已交付状态并标注 `PRECONDITION_OBSERVED`。不得声称覆盖完整 Session 创建生命周期 |
| 2 | Execution Ledger 原子准入 | `call_tool` (command) 返回 | `required` | operation_id 非空，status="accepted"，ledger_sequence 递增 |
| 3 | Preflight stage / revision / evidence | `call_tool` 返回（如被拒绝） | `required` | 构造一个确定性 admission 拒绝场景，验证 LOCK_BUSY + 精确 reason_code + 无 operation_id |
| 4 | 单执行通道 | 同时调用两个 command（不同参数） | `conditional`（若阶段有≥2 command API） | 一个 accepted，一个 CHANNEL_BUSY |
| 5 | Worker / local executor | `get_execution_state` | `required` | worker_state 匹配预期；local command → ABSENT，worker command → READY/BUSY |
| 6 | Operation terminal status | `wait_operation`, `get_operation_status` | `required` | SUCCEEDED/FAILED 精确匹配；completion_evidence 包含 stage_advanced_to（如适用） |
| 7 | Artifact 发布 | `get_operation_status` result.data | `conditional`（若该阶段生产 artifact） | 文件存在，sha256_file(path) == result.system_top_sha256 |
| 8 | Stage 原子推进 | `get_execution_state` | `conditional`（若该阶段推进 stage） | 一次读取即观察到新 stage，不出现中间状态 |
| 9 | Lane 清理 | `get_execution_state` | `required` | 终态后 lane = IDLE |
| 10 | Worker / Task / Lease 清理 | `get_execution_state` + `close_session` | `conditional`（若阶段涉及 Worker 或硬件资源） | active_operation=None，worker_state=ABSENT（local），close_session 成功 |
| 11 | diagnose / recover | `diagnose_execution`, `recover_execution` | `conditional`（仅当操作出现 RECOVERY_REQUIRED 时） | recover_execution 后 lane=IDLE |

**PRECONDITIONED_SESSION 报告标注**：如 R3.1-C，节点 1 必须标注为 `PRECONDITION_OBSERVED`。报告不得声称覆盖了 `create_session → target_stage` 完整流程。其余节点（2-11）按适用性要求正常执行。具体的通过/失败判据和是否需要用户硬件验收见各阶段 `expected_outputs/` 和阶段矩阵。

---

## 9. 跨域交接门禁

### 9.1 Platform → PL

1. **Platform Manifest 一致性**：`platform_revision`、`board_profile_sha256`、`bd_wrapper_sha256` 与 Platform 黑盒验收记录一致。
2. **XSA 一致性**：`xsa_path` 指向的文件存在且 `xsa_sha256` 匹配。
3. **bd_wrapper 一致性**：`bd_wrapper_path` 指向的文件存在且 SHA256 匹配。
4. **PL 的黑盒输入** 必须引用已通过 Agent3 验收的 Platform 资产，不得使用 Agent1 内部 fixture 或未经验收的中间产物。

### 9.2 PL → PS

1. **PL Build Manifest**：所有 16 个 B02 必填字段存在且 `validate_manifest(manifest, "pl_build")` 通过。
2. **交叉引用**：`built_from_platform_revision` 等于已验收 Platform Manifest 的 `platform_revision`。
3. **Bitstream**：文件存在且 SHA256 与 Manifest 一致。
4. **PS 必须拒绝**：stale ELF、错误 Platform XSA、地址映射不一致（Machine-decidable assertion）。

### 9.3 PS → GPIO Run

1. **PS Build Manifest** 完整并通过 `validate_manifest(manifest, "ps_build")`。
2. **ELF** 文件存在且 SHA256 匹配。
3. **Run Manifest** 的 `consistency` 字段无 errors。
4. **JTAG 锁释放**：`close_session` 后确认 lease 已释放。
5. **UART 捕获**：`start_uart_capture` → `wait_uart_capture` → `stop_uart_capture` 完整生命周期。
6. **机器可判定 PASS/FAIL**：UART 输出必须包含确定的 PASS 或 FAIL 标记。

---

## 10. 跨阶段 Artifact 来源规则

| 阶段 | 可用的 Artifact 输入 | 来源类型 |
|------|-------------------|---------|
| B04 R3.1-C | `b04_pl_ready/` fixtures（wrapper + manifest） | frozen reference |
| B04 R3.1-C | Preconditioned `execution_ledger.json` (stage=PL_GENERATE) | precondition fixture |
| B04 R3.2 | B04 R3.1-B system_top.v（frozen reference）+ preconditioned PL_GENERATE stage | frozen reference + precondition fixture |
| B04 R3.3 | R3.2 synthesized DCP + timing evidence | Agent3 accepted artifact |
| B04 R3.4 | R3.3 .bit + PL Build Manifest | Agent3 accepted artifact |
| B04 R3.5 | R3.2-R3.4 accumulated artifacts | Agent3 accepted artifacts |
| B05 | Board Profile + PS7 preset + B01 skill spec | frozen reference |
| B06 | B05 Platform XSA + Manifest（Agent3 accepted） | Agent3 accepted artifact |
| B07 | B05 + B06 artifacts + Skill documents | Agent3 accepted artifacts |
| B08 | All prior Agent3 accepted artifacts | Agent3 accepted artifacts |
| B09 | Clean workspace only — no Agent1 artifacts | Agent2 clean-input artifact |

**禁止**：B07/B08/B09 使用 Agent1 未经阶段验收的内部黄金工程作为成功证据。所有跨阶段交接的 Artifact 必须标注其来源类型。

---

## 11. 失败返回规则

阶段黑盒验收失败时，Agent3 必须报告具体失败步骤和证据。Manager Reviewer 决定回退策略：

| 失败位置 | 返回阶段 | 原因 |
|---------|---------|------|
| B04 R3.1-C Agent3 黑盒失败 | B04 R3.1-C Agent1 | 实现问题 |
| B04 R3.2–R3.5 Agent3 黑盒失败 | 对应 R-step Agent1 | 该步实现问题 |
| B05 Agent3 黑盒失败 | B05 Agent1 | Platform 实现问题 |
| B06 Agent3 黑盒失败 | B06 Agent1 | PS 实现问题，可能涉及 Platform 接口 |
| B07 Agent3 黑盒失败 | B07 Agent1 或上游 | 根因分析后决定 |
| B08 Agent1 验收失败 | B08 Agent1 | 实现缺陷、故障注入不可达或证据不充分 |
| B09 Agent2 黑盒失败 | B08 Agent1 | 实现未支持公开契约 |

**禁止**：失败后不修复而直接进入下一阶段。

---

## 12. prompt 路由规则

Manager Reviewer 在调用 Agent 时应遵循：

1. **Agent1 prompt**：包含当前阶段需求、冻结资产、内部 API 和前一阶段交接。
2. **Agent3 prompt**：只包含阶段公开契约、干净项目目录、Agent1 准备的 fixtures/runner、预期输出规范和证据保存要求。不得包含 Agent1 上下文。
3. **Agent2 prompt**：只在 B09 调用。只包含需求、统一 Skill、MCP API 文档、板卡配置说明和干净工作目录。

---

## 13. 声明

- 本文档为 Revision 4 (v1.3)，docs-only。
- 0 行生产代码和测试代码修改。
- 所有 R3.1-C Controlled Freeze Package Revision 3 的 17 个文件 SHA 未改变。
- R3.1-C 仍为 PENDING FREEZE CONFIRMATION。
- R3.2–R3.5 未开始。
- B05–B10 未开始。
- Agent3 尚未由 Manager Reviewer 调用。
- Agent2 尚未调用。
- 本文档由 Manager Reviewer 审核后生效。
