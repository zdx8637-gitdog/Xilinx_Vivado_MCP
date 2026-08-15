# FPGA AI Agent Project — AX7020 Zynq-7020 开发框架

> 项目根: `D:\fpgaproject`
> 架构: `docs/architecture_ai_zynq7020.md` v2.3.1 (FROZEN TOP-LEVEL)
> 当前：B00–B06 基础能力已完成；B07/B08/B09 硬件功能链路 PASS；Execution Observation O1–O6 已冻结；O7 R3 全新 Agent2 公开 MCP 黑盒验收 PASS，B09 契约勘误已由用户审核关闭；B10/O8 冻结包已交付（用户确认 GPIO v1 稳定基线），下一切片规划待确认

## B03 板卡配置包与环境基线

| 子步骤 | 状态 |
|--------|------|
| 0: 权威资产盘点 | ✅ |
| 1: Board Configuration Package 与 Schema | ✅ |
| 2: 环境探测与诊断 | ✅ |
| 3: 漂移与错误配置测试 | ✅ |
| 4: B03 总门禁与冻结 | ✅ |
| Agent2 黑盒验收 | ✅ (19/19 PASS) |
| **B03 总体** | **✅ COMPLETE / FROZEN** |

## B04 统一 zynq_mcp 基础入口 + 执行账本 + 单通道生命周期

| 子步骤 | 状态 |
|--------|------|
| R0: 架构审计 | ✅ FROZEN |
| R1: Skeleton + Session + Ledger + Preflight + Instance Guard | ✅ COMPLETE / FROZEN (89 tests) |
| R2: Vivado Bridge → PL Adapter + SingleWorker + Heartbeat | ✅ COMPLETE / FROZEN (35 tests, 566 collected / 565 passed / 1 skipped) |
| R3.0: Domain Execution Lifecycle (Command/Set/Query Runner + P9) | ✅ COMPLETE / FROZEN (36 tests, 0 warnings) |
| R3.1-C: `pl_generate_system_top` 公开注册 | ✅ COMPLETE / FROZEN |
| 后续 PL / PS / Verification 能力 | ✅ 已进入统一 `zynq_mcp` 集成；当前总计 101 tools |
| **B04 总体** | **核心生命周期 COMPLETE / FROZEN** |

| 文档 | 内容 |
|------|------|
| [B04_pl_mcp_adapter_plan.md](docs/development/mcp/B04_pl_mcp_adapter_plan.md) | B04 实施规划 |
| [B04_pl_mcp_adapter_test_plan.md](docs/development/tests/B04_pl_mcp_adapter_test_plan.md) | B04 测试规划 |
| [B04_R2_completion_report.md](docs/development/mcp/B04_R2_completion_report.md) | B04 R2 完成报告 |
| [B04_R3_implementation_plan.md](docs/development/mcp/B04_R3_implementation_plan.md) | B04 R3 PL 领域 API 实施规划 |
| [B04_R3_test_plan.md](docs/development/tests/B04_R3_test_plan.md) | B04 R3 测试规划 |

## GPIO 纵向切片当前状态

| Brick | 状态 | 说明 |
|---|---|---|
| B07 | ✅ SKILL CONTRACT REMEDIATED / O6 FROZEN | 逃生通道已删除；Agent1 只用 Skill + 公开 MCP 完成真实重放 |
| B08 | ✅ WHITE-BOX HARDWARE PASS | Agent1 R6 已完成真实硬件全链路 |
| B09 | ✅ COMPLETE（PUBLIC MCP BLACK-BOX PASS） | 全新 Agent2 仅用 Skill + 公开 MCP 完成 P1–P6；Consistency 12/12、UART 8/8、`GPIO_E2E_PASS`，边界与清理审计通过；契约勘误已关闭 |
| B10 | ✅ COMPLETE（O8 冻结包已交付） | 用户已确认 GPIO v1 稳定基线（2026-08-14）；发布清单见 [B10_freeze_manifest.md](docs/development/mcp/B10_freeze_manifest.md)；下一切片方向已由 B11 承接 |
| B11 | ⏳ 阶段①②③⑤完成（阶段④待启动） | 泛化框架黑盒验证：泛化 Skill `skills/zynq_dev/`、MCP 去 GPIO 化+整改（103 工具）；阶段③真板 PASS（[终版报告](docs/development/tests/B11_phase3_final_report.md)）+ ⑤用户确认 6 灯 1s 交替（含 PS 2 灯）；规划见 [B11_plan.md](docs/development/mcp/B11_plan.md) |

当前O1冻结基线（2026-08-12）：`ALL_TOOLS=101`；`mcps` 1264 collected；O1专项 24 passed；最终非硬件回归 1225 passed / 1 skipped / 38 deselected。另行真实入口验证 12 passed；其余26项host/device-live未在O1重复执行。

公开 MCP 契约勘误及整改门禁：

- [B09_public_mcp_contract_erratum.md](docs/development/tests/B09_public_mcp_contract_erratum.md)
- [B09_execution_observation_contract.md](docs/development/mcp/B09_execution_observation_contract.md) — v1.0 COMPLETE / FROZEN
- [B09_execution_observation_implementation_plan.md](docs/development/mcp/B09_execution_observation_implementation_plan.md) — O1–O6 COMPLETE/FROZEN；O7 R3 PASS；勘误已关闭
- [B09_O7_R1_failure_report.md](docs/development/mcp/B09_O7_R1_failure_report.md) — O7 第一轮黑盒失败终态、证据和重验门禁
- [B09_O7_R2_failure_report.md](docs/development/mcp/B09_O7_R2_failure_report.md) — O7 第二轮推进至 bitstream Manifest 门禁后的失败证据与整改
- [B09_O7_R3_pass_report.md](docs/development/mcp/B09_O7_R3_pass_report.md) — O7 第三轮全公开 MCP 黑盒通过证据、边界审计与清理结果
- [B09_O1_completion_report.md](docs/development/mcp/B09_O1_completion_report.md) — Ledger v2兼容扩展实施与分层回归证据
- [B09_O2_implementation_report.md](docs/development/mcp/B09_O2_implementation_report.md) — O2冻结证据：统一EDA后端所有权、真实PID/PROCESS观测和回归
- [B09_O3_implementation_report.md](docs/development/mcp/B09_O3_implementation_report.md) — O3冻结证据：Platform/PL真实Vivado STATUS观测与Manifest终态门禁
- [B09_O4_implementation_report.md](docs/development/mcp/B09_O4_implementation_report.md) — O4冻结证据：XSCT真实PID/步骤观测、ARM ELF与PS Manifest终态门禁
- [B09_O5_implementation_report.md](docs/development/mcp/B09_O5_implementation_report.md) — O5冻结证据：Controller-owned XSDB、JTAG lease、UART capture与真实RESOURCE观测
- [B09_O6_completion_report.md](docs/development/mcp/B09_O6_completion_report.md) — O6冻结证据：Skill公共边界、Agent1全公开MCP真实GPIO重放与清理
- 原 B09 硬件证据保留在 `workspaces/gpio_b09_r3_20260812/REPORT_B09_R3_Agent2.md`

## 开发规划文档

| 文档 | 内容 |
|------|------|
| [B03_completion_report.md](docs/development/mcp/B03_completion_report.md) | B03 完成报告 |
| [B03_to_B04_handoff.md](docs/development/B03_to_B04_handoff.md) | B03→B04 交接 |
| [B03_asset_inventory.md](docs/development/mcp/B03_asset_inventory.md) | B03 子步骤0 资产盘点 |

## 入口文档

| 文档 | 内容 |
|------|------|
| [architecture_ai_zynq7020.md](docs/architecture_ai_zynq7020.md) | 冻结的顶层架构: 三域四层 + P1-P8 |
| [brick_development_plan.md](docs/brick_development_plan.md) | Brick 开发规划与进度 |
| [B00_project_cleanup_plan.md](docs/development/B00_project_cleanup_plan.md) | B00 项目整理方案 (v0.3) |
| [B00_completion_report.md](docs/development/B00_completion_report.md) | B00 执行报告 |

## 核心组件

| 目录 | 说明 |
|------|------|
| `Xilinx_Vivado_MCP/` | Vivado MCP Server (27 tools + 2 Skills), 独立 Git 仓库 |
| `Xilinx_Vitis_MCP/` | Vitis MCP Server 骨架, 独立 Git 仓库 |
| `zynq_platforms/` | AX7020 平台工程, 独立 Git 仓库 |
| `docs/` | 架构文档、开发记录、厂商教程 |
| `tools/scripts/` | 工具脚本 (UART 扫描、驱动安装) |
| `vendor/drivers/` | 厂商驱动 (CP210x、FTDI) |

## 参考设计

| 目录 | 说明 |
|------|------|
| `hello_fpga/` | 纯 PL Breath LED 完整项目 |
| `g9_hw_test/` | PL 硬件闭环验证 |
| `embedded_projects/` | PS bare-metal ARM 参考代码 |
| `validation_projects/` | Golden + 11 故障注入 + Agent2 黑盒 |

## 外部资料

| 路径 | 说明 |
|------|------|
| `docs/boardinformation/` | ALINX 官方 6 本 PDF 教程 (~99MB) |
| `vendor/drivers/` | CP210x/FTDI USB 驱动 |
