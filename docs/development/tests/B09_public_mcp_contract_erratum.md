# B09 GPIO 黑盒验收公开 MCP 契约勘误

> 版本：v0.3
> 日期：2026-08-13
> 状态：**CLOSED**（用户审核通过并授权关闭）
> 类型：Docs-only；不修改 Skill、生产代码、测试代码或既有 B09 证据

冻结契约与实施路线：

- [B09_execution_observation_contract.md](../mcp/B09_execution_observation_contract.md) v1.0 COMPLETE / FROZEN
- [B09_execution_observation_implementation_plan.md](../mcp/B09_execution_observation_implementation_plan.md) v0.1 PLANNING

## 1. 勘误结论

B09 R3 已经证明 AX7020 GPIO 纵向切片在真实硬件上能够完成：Platform XSA、PL Bitstream、PS ELF、Manifest 一致性、JTAG 部署、UART 观测和 GPIO readback 均通过。

但 B09 R3 没有证明最初约定的产品契约：**全新无记忆的黑盒智能体仅凭 Skill 和公开 `zynq_mcp` 工具即可完成完整 Zynq 项目，且全部长任务均受统一 Execution Ledger、Preflight Gate、超时、恢复和进程所有权管理。**

因此原 B09 结果分层定性如下：

| 验收维度 | 结论 |
|---|---|
| 真实硬件 GPIO 功能 | PASS |
| XSA / Bitstream / ELF 产物链 | PASS |
| JTAG / UART / GPIO readback | PASS |
| Manifest 一致性结果 | PASS |
| Skill + 公开 MCP 纯黑盒契约 | NOT VERIFIED |
| 全流程 Execution Ledger 覆盖 | NOT VERIFIED |
| B10 GPIO v1 冻结门禁 | BLOCKED |

原报告保持不变，继续作为硬件功能证据：

- `workspaces/gpio_b09_r3_20260812/REPORT_B09_R3_Agent2.md`

本勘误不否定其硬件结果，只撤回“仅凭公开契约完成全部流程”的产品验收结论。

## 2. 已确认的三条逃生通道

### 2.1 PL 构建绕过公开 MCP

`skills/zynq_gpio/phases/2_pl_build.md` 明确要求在 MCP 会话外直接导入并使用内部 `VivadoTclBridge`，同时明确不使用公开的 `pl_synthesize`、`pl_place`、`pl_route`、`pl_analyze_timing` 和 `pl_generate_bitstream` 工具。

影响：

- PL 长任务不属于统一 MCP 的公开调用链；
- Execution Ledger 无法完整表示真实 Vivado 任务；
- Preflight Gate、PID 所有权、心跳、期限和恢复契约没有覆盖该构建；
- 黑盒智能体必须知道内部 Python 模块及 Tcl 构建细节。

### 2.2 PL Manifest 由黑盒智能体手工发布

Skill 要求 standalone 构建完成后直接调用内部 `publish_pl_build_manifest()`。该发布没有由公开 MCP Operation 的成功事务自动完成。

影响：

- Bitstream 成功与 Manifest 发布不是一个原子产品结果；
- Manifest 缺失时 Skill 仍允许把构建视为成功；
- 智能体需要了解内部 Manifest publisher 和 snapshot 结构。

### 2.3 PS ELF 通过手工 `make` 补全

Skill 在 `ps_compile` 后要求进入应用 `Debug` 目录手工运行 `make`，因为当前公开工具没有可靠完成最终 ELF 链接。

影响：

- `ps_compile` 的成功状态不等价于 ELF 构建完成；
- Ledger 可能先于真实编译/链接进入终态；
- 手工链接的进度、超时、退出码和产物没有完整进入统一 Operation 证据。

## 3. GPIO v1 正式公开契约

关闭本勘误后，黑盒智能体可以：

- 读取 `skills/zynq_gpio/`、锁定 Board Package 和运行时公开 MCP schema；
- 在自己的干净工作目录创建需求输入、Verilog、XDC、C 源码和验收脚本；
- 使用公开 `zynq_mcp` 工具查询、构建、部署、观测和恢复；
- 读取公开 ToolResponse、Execution Ledger 查询结果和自动发布的 Manifest。

黑盒智能体不得：

- 导入 `mcps.zynq_mcp.*` 内部模块；
- 直接实例化 `VivadoTclBridge`、`XsctBridge` 或其他内部 Adapter；
- 直接启动 Vivado、Vitis、XSCT、Tcl 或旧 MCP Server；
- 手工调用 `publish_pl_build_manifest()` 或其他内部 Manifest publisher；
- 手工运行 `make`、`gcc`、链接器或等价构建命令来补全 MCP 的缺失步骤；
- 直接修改 Execution Ledger 或伪造 Operation/Manifest 终态。

允许保留维护者调试入口，但必须满足：

- 不出现在正式 GPIO Skill 主流程；
- 不计入 B09/B10 正式验收；
- 明确标记为 maintenance/debug-only；
- 不得把其产物伪装成公开 MCP 闭环结果。

## 4. 关闭勘误所需产品行为

### 4.1 PL 构建

- Skill 只调用公开 PL 工具；
- MCP 内部可以使用独立 Vivado 子进程，但该进程必须由 SingleWorkerController 和 Execution Ledger 所有；
- 每个长任务必须呈现 `ACCEPTED -> RUNNING -> terminal`；
- 超时、崩溃、PID 身份不符或心跳丢失必须进入机器可判定的失败/恢复状态；
- 不允许自动重跑结果未知的命令。

### 4.2 PL Manifest

- Bitstream Operation 只有在 Artifact 校验及 PL Manifest 原子发布成功后才能进入 `SUCCEEDED`；
- Manifest 发布失败必须返回精确错误并保留可恢复证据；
- Agent 不负责调用内部 publisher。

### 4.3 PS 编译

- `ps_compile` 必须覆盖 BSP、应用编译、最终链接、ELF 存在性/架构校验和 PS Manifest 发布；
- `SUCCEEDED` 必须意味着可供 `ps_download_elf` 使用的 ELF 已存在；
- `make` 可以作为 MCP 内部实现，但不得成为 Agent 的公开步骤。

## 5. 重新验收门禁

修复顺序：

1. Agent1 完成白盒修复和生产入口测试；
2. 使用真实 Vivado/Vitis/XSCT 运行 host-live；
3. Agent1 在真实板卡完成一次全公开 MCP 白盒重放；
4. 修订 Skill，删除三条手动逃生通道；
5. 启动**全新无记忆 Agent2 会话**重新执行 B09；
6. 新 Agent2 通过后，才允许进入 B10。

新 B09 的硬门禁：

- 所有 EDA、构建、Manifest、烧录、UART 操作均来自公开 `call_tool`；
- R2 轨迹只允许 Codex 技能加载器执行一次只读 `Get-Content ...\\SKILL.md`；加载后所有 command execution 必须为 0，任何额外 shell 或脚本均判失败；
- 所有长任务在 Ledger 中可查询，并有真实 PID/身份/心跳/期限；
- PL/PS Manifest 自动发布，`verify_consistency` 全部通过；
- 最终 UART 包含 `GPIO_E2E_PASS`，不存在 `GPIO_E2E_FAIL`，GPIO WROTE/READ 全匹配；
- 流程结束后无本轮遗留 Vivado/XSCT/MCP 子进程；
- Agent 脚本机械扫描以下模式为 0：
  - `VivadoTclBridge`
  - `publish_pl_build_manifest`
  - 直接 `make`
  - 直接 `vivado` / `xsct` / Tcl 启动
  - `mcps.zynq_mcp` 内部模块导入

## 6. 当前边界

本文档最初只完成文档定性。后续已按冻结契约完成 O1–O7，技术关闭条件已经满足：

- O1–O5 已完成统一 Ledger、真实工具/进程观测、Manifest 终态门禁和 JTAG/UART 资源观测；
- O6 已删除 Skill 逃生通道并完成 Agent1 全公开 MCP 真实硬件重放；
- O7 R1/R2 分别因 vendor launcher 与 bitstream 发布门禁失败，均作为历史整改证据保留；
- 第一轮失败包含公开 Vivado 后端 `BACKEND_START_FAILED`、Agent2 零-shell边界违规以及 `ps_disconnect_hw_server` schema/运行时不一致；
- O7 R3 已由另一全新无记忆 Agent2 在新的项目外隔离环境执行并判 `PASS`；
- 在用户确认关闭本勘误并授权 O8 前，不自动进入 B10。

R2 前置整改已完成：Vivado 启动保留 stderr/退出码并在首条用户 Tcl 前允许一次安全重启；全部 `ps_*` schema 显式公开 session 语义；同 workspace XSA 可幂等导入。专项、host-live 与完整非硬件回归均已通过，新的项目外隔离环境已经创建；当前等待隔离后端预检和新 Agent2 正式重验，尚不改变 O7/B10 状态。

隔离后端预检随后定位并关闭了 Xilinx Windows launcher 的窄环境缺陷：vendor 子进程现在会补齐缺失的 `PROCESSOR_ARCHITECTURE`/Windows 核心变量。第三套全新预检的 `platform_generate` 已 `SUCCEEDED / PUBLISHED`，Manifest/wrapper/XSA 和完整公共清理均有证据；当前仅等待新 Agent2 正式重验，O7/B10 状态仍不提前变更。

O7 R2 随后成功通过 Platform、PL synthesis/place/route/timing，但在 `pl_generate_bitstream` 的目标复制/Manifest 发布门禁失败。MCP 已增加输出父目录创建、copy 明确标记和请求路径存在性验证；R2 证据见 [B09_O7_R2_failure_report.md](../mcp/B09_O7_R2_failure_report.md)。

O7 R3 随后完成全链路公开 MCP 重验：Platform、PL、PS Manifest 均发布，Consistency 12/12，真实硬件 UART 8/8 读写匹配并出现 `GPIO_E2E_PASS`；轨迹只有一次只读 Skill 加载，之后 command execution 为 0；UART/JTAG/session 清理完整。证据见 [B09_O7_R3_pass_report.md](../mcp/B09_O7_R3_pass_report.md)。

当前状态：O1–O6 **COMPLETE / FROZEN**；O7 **R3 PASS / AWAITING USER REVIEW**；本勘误技术关闭条件已满足，等待用户确认关闭并授权 O8/B10 冻结。

## 7. 关闭记录（2026-08-13）

用户已审核 O7 R3 黑盒验收证据并授权关闭本勘误。B09 从 `HARDWARE_FUNCTIONAL_PASS / PUBLIC_MCP_CONTRACT_NOT_VERIFIED` 变更为 **`COMPLETE`**：公开 MCP 纯黑盒契约已被全新无记忆 Agent2 在项目外隔离环境验证通过（R3），并在本仓库 `D:\_b09_verify_20260813\` 完成一次独立复测，结论一致（PASS，8/8 readback，`GPIO_E2E_PASS`，三 Manifest 自动发布，Consistency 12/12，边界审计 0 违规）。

关闭后状态：

- B09 公开 MCP 契约勘误：**CLOSED**；
- B09：**COMPLETE**；
- B10：不再被本勘误阻塞，转为等待用户确认 GPIO v1 稳定基线并选择下一纵向切片（Interrupt / DMA loopback / ILA debug / Boot），即 O8 内容。

本勘误关闭不自动冻结 B10，也不自动启动 O8。
