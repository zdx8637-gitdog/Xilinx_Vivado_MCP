# AI Agent 驱动 Zynq-7020 开发框架（MCP + Skill）

> 用 AI Agent（主代理）驱动 Vivado/XSim/Vitis 做 Zynq-7020（ALINX AX7020）FPGA 开发：**一个 MCP Server + 一个泛化 Skill + 一个板卡配置包**，Agent 通过 MCP 原子工具完成平台/PL/PS 全流程（建 BD、综合、实现、bitstream、裸机编译、JTAG 部署），按「三域四层 + Brick」增量构建并经白盒/黑盒验收。
>
> **外部使用者只需要：`mcps/`（MCP Server）+ `skills/`（泛化 Skill）+ `boards/`（板卡配置包）+ 本 README。** 其余目录为项目开发历史与验证资产（见下）。

## 迭代到什么程度（2026-09-03）

- **框架**：B00–B09 ✅ COMPLETE/FROZEN（统一 `zynq_mcp`、执行账本、单通道生命周期、公开 MCP 黑盒验收）；B11 ✅ COMPLETE（泛化框架黑盒验证：零外设字样 Skill + 6-LED 考题全链路 PASS）；**当前 MCP 共 109 工具**（11 control + 98 domain：platform/PL/PS 域；B13 修复轮#1–#10 已合入 master，见 [B13_P4_MILESTONE.md](docs/development/tests/B13_P4_MILESTONE.md)）。Execution Observation：O1–O6 FROZEN，O7 R3 PASS。
- **功能切片（真板）**：B12 ✅ A1（DMA 环回白盒+黑盒）/ A2（AD7606C-16 8 通道实采，盲测通道/频率/幅度三方法一致）——**B13 ✅ P4 完成（2026-09-05）**：TCP 扫描上传模拟全链黑白盒双过（UART 指令 + TCP 25M 点整图 2.087MB/s、TPG 全速门禁、ADC 三档 2k/100k/1M ±0.5%、L2 事件计数 1:1、STOP/覆盖/溢出/重连、KAT 双向量、verify 12/12）。
- **验证方法论 v2**：[validation_methodology.md](docs/development/validation_methodology.md)（L0 契约/L1 功能/L2 状态/L3 公共面 + 行为偏离审计 + 黑盒终审——黑白盒任务书设计的长期纪律）。
- **框架已知问题与加强方案**：[B13_exposed_framework_issues.md](docs/development/mcp/B13_exposed_framework_issues.md)（9 条 MCP/Skill 级问题全部经修复轮#1–#10 关闭，状态见文档 §五/§六）。
- **协作纪律（长期）**：[subagent_communication_rules.md](docs/development/subagent_communication_rules.md)。

## 给外部使用者：最小上手

| 目录 | 说明 |
|------|------|
| `mcps/zynq_mcp/` | **唯一 MCP Server**（105 工具；`control/` 执行账本与单通道生命周期 + `adapters/` Vivado/XSCT/JTAG/UART + `domains/` platform/PL/PS）。启动：`python -m mcps.zynq_mcp.server`（stdio JSON-RPC；客户端可用 `mcp` SDK 的 `ClientSession` 接入） |
| `mcps/common/` | 公共契约：板卡配置包/锁/Revision/Artifact/错误模型（MCP 依赖） |
| `skills/zynq_dev/` | 泛化开发 Skill（零项目外设字样：SKILL.md + phases 0–8 + 机制附录） |
| `boards/ALINX_AX7020_v1.0/` | **板卡配置包**（板卡唯一数据源：README/xdc/profile/ps7 preset/manifest） |
| `vendor/drivers/`、`tools/scripts/` | 可选：USB-UART 驱动与扫描/安装脚本 |

- 测试：**从仓库根**运行 `python -m pytest mcps`（勿 cd mcps）；环境 Windows + Python 3.12 + `mcp==1.28.1`；Vivado/Vitis 2023.1。
- 平台要求：AI Agent 会话需能调用 MCP 工具（本仓库的 Server 为外部启动的 stdio 服务；无原生挂载时可用 `mcp` SDK 写 stdio 客户端调用，见框架问题文档）。

### 非核心目录（外部使用者可忽略）

| 目录 | 说明 |
|------|------|
| `docs/` | 冻结架构 + 全部 Brick 开发记录/测试规划/技能归档（项目过程资产，体量大） |
| `hello_fpga/`、`g9_hw_test/`、`embedded_projects/`、`validation_projects/` | 开发期参考/验证项目（Golden + 故障注入 + 黑盒材料） |
| `workspaces/`、`.zynq_runtime*/`、`.o6_runtime*/`、`.tmp_*` | 运行态/证据目录（不入库） |
| `PC_end_tcptest/` | B13 上位机测试程序（项目专属） |
| `Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/` | legacy 旧 MCP/平台（独立 Git 仓库，已被 `mcps/zynq_mcp` 取代） |

## Brick 历史进度

| Brick | 状态 | 说明 |
|---|---|---|
| B00–B03 | ✅ COMPLETE/FROZEN | 项目整理、执行观察 O1–O6、板卡配置包、环境基线 |
| B04 | ✅ COMPLETE/FROZEN | 统一 `zynq_mcp`：执行账本 + 单通道生命周期 + Vivado/XSCT Adapter |
| B05–B09 | ✅ COMPLETE/FROZEN | GPIO 纵向切片全链路（白盒/阶段黑盒/公开 MCP 黑盒 PASS） |
| B10/B11 | ✅ COMPLETE | O8 冻结包；泛化框架黑盒验证（去 GPIO 化 Skill + 109 工具 + 6-LED 考题，全六阶段闭环） |
| B12 | ✅ A1/A2 COMPLETE | 数据采集链路：DMA 环回白盒+黑盒；AD7606C-16 8 通道真板实采（盲测一致） |
| **B13** | **✅ P4 完成（2026-09-05）** | TCP 扫描上传模拟全链：P0 协议互测 → P1 TPG 全速门禁 → P2 ADC 三档实采 → P3 上位机联调 → **P4 黑白盒双过（2.087MB/s 整图零错、三档 ±0.5%、verify 12/12）+ 框架升级修复轮#1–#10 合入 master** |

## 核心组件（当前）

| 目录 | 说明 |
|------|------|
| `mcps/zynq_mcp/` | **唯一 MCP**（105 工具，详见上）；`docs/development/mcp/B10_freeze_manifest.md` 为发布清单 |
| `skills/zynq_dev/` | 泛化 Skill（B11 冻结）；旧 GPIO Skill 归档于 `docs/development/skill/archive/` |
| `boards/ALINX_AX7020_v1.0/` | Board Configuration Package（板卡唯一数据源） |
| `Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/` | legacy/已出范围（独立 Git 历史，保留在磁盘） |

## 开发规划与入口文档

- 顶层架构（冻结）：[docs/architecture_ai_zynq7020.md](docs/architecture_ai_zynq7020.md) v2.3.1
- Brick 计划与进度：[docs/brick_development_plan.md](docs/brick_development_plan.md)
- 冻结契约（当前）：`docs/development/tests/B13_requirement_draft.md`
- 框架问题复盘：[docs/development/mcp/B13_exposed_framework_issues.md](docs/development/mcp/B13_exposed_framework_issues.md)
- 会话纪律速查：`docs/development/B12_a2_working_discipline.md`
