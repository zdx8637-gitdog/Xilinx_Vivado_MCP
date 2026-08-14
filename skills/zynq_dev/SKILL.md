# Zynq 通用工程开发框架 Skill

> Brick: B11 阶段① | 定位: 面向任意 Zynq 工程开发的**通用框架** | 具体项目 = 需求文档输入

## 定位

我是面向任意 Zynq 工程开发的**通用开发框架 Skill**。我不绑定任何具体外设、
不预设任何具体项目——每一个真实项目都是一份**需求文档**（`<REQUIREMENT_DOC>`），
由用户提供。我把「需求 → 物理事实 → 预算 → 选型 → 提案 → 实现 → 一致性 →
部署观测 → 判定恢复」的通用工程流程固化为 S0–S8 九个阶段；具体外设怎么连、
软件怎么写、什么算成功，全部来自需求文档与板卡物理事实，**不由本 Skill 臆造**。

能力声明：本 Skill 覆盖任何可以通过公开 `zynq_mcp` 原子能力完成的 Zynq 工程
（PS / PL / Platform 三域）。公开能力缺失或返回不确定状态时必须停止并报告
产品缺口，不得临时写脚本绕过（见「公开边界（硬门禁）」）。

## 使用方式（用户输入）

| 输入 | 说明 | 责任方 |
|------|------|--------|
| 需求文档 `<REQUIREMENT_DOC>` | 功能目标、观测方式、PASS/FAIL 判定、上位机分工 | 用户 |
| 板卡物理事实 | 板卡型号、外设型号、接口、引脚分配、电平、时钟（含「未确认」标注） | 用户（现实层） |
| 目标档位偏好（可选） | 采样率/帧率/吞吐等产品级取舍的偏好 | 用户（拍板） |

分工边界：**现实层（物理事实）归用户**，本 Skill 不得臆造物理事实；**工程层
（架构/协议/参数/代码/约束/构建/验证/判定）归智能体**；**产品级取舍由智能体
提案、用户拍板**。

## 占位符约定

全篇使用占位符代替任何具体项目值。占位符一律不猜值、不填值：

| 占位符 | 含义 | 来源 |
|--------|------|------|
| `<REQUIREMENT_DOC>` / `<REQUIREMENT_*>` | 需求文档及其字段（功能/外设/观测/判定/上位机） | 用户需求 |
| `<TARGET_MARKER>` | 需求给定的判定 marker（细分为 `<PASS_MARKER>` / `<FAIL_MARKER>`） | 用户需求 |
| `<外设>` / `<PERIPHERAL>` | 需求声明的目标外设（本 Skill 不预设任何外设） | 用户需求 |
| `<BOARD_ID>` / `<PART>` | 板卡标识与器件型号 | 板卡配置包 |
| `<PROJECT_PATH>` / `<SESSION_ID>` | 工作目录与会话标识 | S1 `create_session` 产出 |
| `<XSA_PATH>` / `<BITSTREAM_PATH>` / `<ELF_PATH>` | 三域产物路径 | S5 公开操作产出 |
| `<UART_PORT>` / `<BAUDRATE>` | UART 观测配置 | 物理事实 + 需求文档 |

## S0–S8 总览

| 阶段 | 名称 | 主旨 |
|------|------|------|
| S0 | 需求解析 | 把用户需求转成结构化需求（功能/外设/观测/判定/上位机分工），缺失则回问 |
| S1 | 物理事实清单 | 从板卡配置包 + 用户输入产出物理事实表（型号/接口/引脚/电平/时钟，未确认即标注） |
| S2 | 带宽/资源预算 | 按需求指标换算带宽预算、对照板卡 PL 资源上限做占用预估 |
| S3 | 架构选型 | 全部工程决策：拓扑、时钟域、数据通路、地址规划、中断 vs 轮询 |
| S4 | 方案提案 | 给用户的取舍提案（档位/判据线/推荐项），用户拍板 |
| S5 | 分域实现 | Platform BD/XSA/Manifest → PL 构建/bitstream/Manifest → PS 软件/ELF/Manifest |
| S6 | 一致性验证 | `verify_consistency` 跨域校验（revision/板卡/地址/产物 SHA256） |
| S7 | 部署观测 | JTAG 8 步部署 + UART 捕获（marker 来自需求文档） |
| S8 | 判定/恢复 | `evaluate_observation` 机读判定 + 证据归档 + 诊断恢复 |

**严格串行。** 每个阶段成功后才进入下一个。阶段之间通过 MCP **自动发布的
Manifest/Artifact** 交接产物，不依赖智能体记忆。

## 阶段对应文档

| 阶段 | 文档 | 工具类别 |
|------|------|----------|
| S0 | [phases/0_requirement.md](phases/0_requirement.md) | control query |
| S1 | [phases/1_physical_facts.md](phases/1_physical_facts.md) | control query + 工作区读 |
| S2 | [phases/2_budget.md](phases/2_budget.md) | domain query |
| S3 | [phases/3_architecture.md](phases/3_architecture.md) | domain query |
| S4 | [phases/4_proposal.md](phases/4_proposal.md) | 无工具（纯提案） |
| S5 | [phases/5_domain_implementation.md](phases/5_domain_implementation.md) | platform / pl / ps command 原子 |
| S6 | [phases/6_consistency.md](phases/6_consistency.md) | `verify_consistency`（纯 query） |
| S7 | [phases/7_deployment_observation.md](phases/7_deployment_observation.md) | ps JTAG + UART command |
| S8 | [phases/8_verdict_recovery.md](phases/8_verdict_recovery.md) | `evaluate_observation` + 诊断/恢复 |

通用机制（Operation 纪律、Manifest 链、原子序列模板、构建链、UART 捕获、
观测判定、恢复阶梯、清理）：见 [appendix_mechanics.md](appendix_mechanics.md)。

## 领域知识边界

本框架**不内置任何具体外设知识包**（BD 拓扑、软件结构、PASS/FAIL 语义都不
预设）。每个真实项目的领域知识来源是：

1. 需求文档（要什么、怎么观测、什么算成功）；
2. 板卡物理事实（现实层，用户提供）；
3. S3 架构决策（智能体工程判断）。

框架提供纪律（阶段、门禁、恢复、证据），知识包提供判断——知识包由每个实例
按需挂载，永不写死在框架里。

## 公开边界（硬门禁）

本 Skill 的正式执行面只有统一 `zynq_mcp` Server 的公开 tools。智能体可以在
`<PROJECT_PATH>` 内创建需求输入文件（如程序源码、约束文件）并读取公开产物，
但不得：

- 导入任何 MCP 内部 Python 包或实例化内部 bridge/controller；
- 自行启动、停止或调用 EDA 工具进程（Vivado、XSCT、XSDB、Tcl shell）或旧 MCP；
- 用 shell 编译/链接应用，或绕过 `ps_compile`；
- 手工生成、发布或修改 Platform / PL / PS Manifest；
- 直接读取、编辑或删除 Execution Ledger、runtime 状态、锁文件；
- 按进程名杀进程，或在 MCP 之外重试不确定结果。

公开能力缺失或返回不确定状态时必须停止并报告产品缺口，不能临时写脚本绕过。

## 长任务与真实状态规则（Operation 纪律）

所有 command tool 都先返回 `operation_id`。随后只使用 `wait_operation` 或
`get_operation_status`，并保存每次公开响应形成状态时间线。至少读取：

`status`, `status_source`, `backend`, `observed_state`, `vendor_status`,
`current_step`, `observation_quality`, `last_progress_at`, `artifact_state`,
`deadline_at`, `recommended_action`。`progress_pct` 是可选字段；缺失不影响判断。

```
command → operation_id
  → wait_operation(operation_id, bounded_timeout)
  → RUNNING + recommended_action=WAIT       → 继续有界等待
  → RUNNING + recommended_action=DIAGNOSE   → diagnose_execution，再按返回建议处理
  → RECOVERY_REQUIRED / recommended_action=RECOVER
                                                → 先 diagnose_execution；仅在公开诊断确认
                                                   无活动受控进程/资源后调用 recover_execution
  → SUCCEEDED + artifact_state=PUBLISHED（Manifest 产物型操作） → 下一步
  → FAILED/TIMED_OUT/INTERRUPTED/OUTCOME_UNKNOWN → 停止正常流程，进入 S8
```

`wait_operation` 的等待超时不等于 Operation 超时：若返回 `wait_timed_out=true`
且 Operation 仍为 `RUNNING`，不得把它当作失败或重新提交同一命令。所有判断以
Ledger 返回的真实 backend observation 和 `recommended_action` 为准。

## 证据纪律（证据 = 机读）

- 终态、产物、判定全部来自**可机器验证的证据**：Ledger 真实状态、Manifest/
  Artifact 的 SHA256、UART 捕获文本；
- 磁盘上存在孤立的产物文件不能代替终态和 Manifest；
- fail-closed：无法确认真实状态时返回明确错误并停止，不推断成功、不推断
  运行中、不推断已释放。

## 会话恢复（对话丢失恢复）

当你接手一个已有工作目录时，**不要盲目重跑所有阶段**。先调用
`get_execution_state` 与 `diagnose_execution`，只读列出
`<PROJECT_PATH>/manifests/` 下三个子目录（platform / pl / ps）的自动发布
Manifest，随后用 `verify_consistency` 判断状态：

| 结果 | 含义 | 动作 |
|------|------|------|
| `all_passed = true` | 三域产物一致 | 进入 S7 部署 |
| `failed` 非空 | 某个 Manifest 数据不一致 | 找到不匹配的域，从那里重跑 |
| `skipped` 非空 | 公开证据不完整 | 停止；从缺失 Manifest 对应的域重新执行公开流程 |
| Manifest 部分缺失 | 对应域未完成 | 从缺失域开始；禁止根据散落产物手工补 Manifest |
| 所有 Manifest 都不存在 | 全新项目 | 从 S0 开始 |

**原则**：公开 Ledger 状态 + 自动发布的 Manifest 是证据。读取 Manifest 是允许
的；读取或修改 runtime/Ledger 文件不允许。

## 工具前缀约定

| 前缀 | 域 | 用途 |
|------|----|------|
| `create_*` / `close_*` / `get_*` / `recover_*` | Control | Session 管理 |
| `platform_*` | Platform | BD 设计 + XSA 导出（原子序列） |
| `pl_*` | PL | FPGA 综合/布局/布线/bitstream |
| `ps_*` | PS | ARM 软件 + JTAG 部署 + UART |
| `verify_*` / `evaluate_*` | Verification | 跨域校验 + 判定 |

## Session ID 传递规则

| 工具前缀 | 是否需要 session_id | 说明 |
|---------|:--:|------|
| `create_session` / `close_session` | 否 | Control 层自动注入 |
| `get_*` / `recover_*` / `wait_operation` | 否 | Control 层自动注入 |
| `platform_*` | 否 | Transport 自动注入 |
| `pl_*` | 否 | Transport 自动注入 |
| `ps_*` | **是** | PS domain schema 要求显式传入 |
| `verify_*` / `evaluate_*` | 否 | Query tool |

**规则**：所有 `ps_*` 前缀的 domain tool 调用时都必须显式传入 `session_id` 参数。
其他 tool 不需要。不加 `session_id` 会返回 `INVALID_ARGUMENT / SESSION_ID_REQUIRED`。

## 工作目录约定

所有产物写入 session 创建时指定的 `<PROJECT_PATH>` 目录。**禁止**写入
`mcps/`、`boards/`、源码目录等仓库目录。

## 失败与恢复总原则

任何阶段失败时：**先分类，再诊断，再恢复，不要盲目重试**。错误分类与诊断
cascade 见 [phases/8_verdict_recovery.md](phases/8_verdict_recovery.md) 与
[appendix_mechanics.md](appendix_mechanics.md)「恢复阶梯」。服从公开
`recommended_action`：WAIT 只等，DIAGNOSE 只诊断，RECOVER 先确认无活动受控
进程/资源，STOP 则停止并报告。

## 已知限制

| 限制 | 说明 |
|------|------|
| 执行面 = 公开 MCP 能力 | 公开工具无法表达的工程需求 → 停止并报告产品缺口 |
| 部署方式 = JTAG-only（当前开发配置，架构 P7） | BOOT.BIN / QSPI / SD 启动不在当前开发配置内 |
| UART 波特率 | 以需求文档与板卡物理事实为准；`ps_diagnose_uart_clock` 校验真实波特率 |
