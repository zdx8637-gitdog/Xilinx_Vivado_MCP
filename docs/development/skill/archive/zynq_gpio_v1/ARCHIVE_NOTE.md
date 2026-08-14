# ARCHIVE NOTE — zynq_gpio Skill v1（方案 A 归档）

> 归档日期：2026-08-14 17:46:30 +08:00（`Get-Date` 实测）
> 归档理由：**B11 阶段①「泛化 Skill 重写」**——旧 GPIO 配方 Skill 被通用框架
> Skill（`skills/zynq_dev/`，零考题外设字样）取代。归档方式为用户批准的
> **方案 A**（`docs/development/mcp/B11_plan.md` §2 阶段①「旧 GPIO Skill 归档
> 方案」提案 A：移至 `docs/development/skill/archive/`，保留完整历史证据 +
> SHA256 记录；与 `Xilinx_Vivado_MCP/skills/` 的 legacy 先例一致）。
>
> **声明：本归档按用户批准的方案 A 执行，非静默搬迁。**

## 路径变更

| 原路径 | 新路径 |
|--------|--------|
| `skills/zynq_gpio/` | `docs/development/skill/archive/zynq_gpio_v1/` |

移动方式：`git mv skills/zynq_gpio docs/development/skill/archive/zynq_gpio_v1`
（保留完整 Git 历史证据；文件内容零改动，见下方 SHA256 与 B10 冻结清单一致）。

## 归档文件 SHA256 清单（`Get-FileHash -Algorithm SHA256` 实测，2026-08-14）

| 文件 | SHA256 |
|------|--------|
| `SKILL.md` | `9645d0cb817bd98106b3df95e70501dfe98d1d913ded8047a3b5b5af95c900df` |
| `phases/0_board_profile.md` | `0f842963ba123616a326f9b73ec86ecf9b21c74d2232e2bf00935171ccac6af4` |
| `phases/1_platform_design.md` | `f35c7eaed9a7d743c553df3fcb6e1a86bc8f337ce506b2d73f1890379d23ab0e` |
| `phases/2_pl_build.md` | `555c8d0e72e773a076a7d33f249dfbb1edf5410ee3c09cb609e4e3df535a8a38` |
| `phases/3_ps_software.md` | `96a271ef7e32cb107c8d130b977468091e71aebb087df538009dccf1c903940f` |
| `phases/4_consistency.md` | `7f9f3cb0ce63530784138b4cd4517446b8d6daf5887ab7422348dcfe64f3280e` |
| `phases/5_deployment.md` | `ce483b6a9bb06cd43e9451cfe8f978c48fbf1761dcf06669e450c2a60a554cea` |
| `phases/6_observation.md` | `e3603cfcbe559a0c126256bdf8a3a10cf93acdc7971f0ed78d3ed5f4ca91d23b` |
| `phases/7_debug_recovery.md` | `b6df7fafd725df60e1506ee0f4b1ec458286eb0f6779e46ff1e6afae5de046f4` |
| `phases/appendix_uart_baremetal.md` | `c55af3f7b780cad2747b856b89c79295b14be9fe62015587e5ab80475828f335` |

> 共 **10 个文件**。其中 `SKILL.md` 的 SHA256（`9645d0cb…`）与
> `docs/development/mcp/B10_freeze_manifest.md` §3、`B09_O6_completion_report.md`
> 记录的冻结值完全一致，证明归档未改动任何内容。

## 后续

- 活跃 Skill 为 `skills/zynq_dev/`（B11 阶段①交付，面向任意 Zynq 工程的通用
  框架，机械门禁：GPIO / 0x41200000 / LED / breath / blink 零命中）。
- 本目录为**只读归档**：保留作对照样例与历史证据，新开发不得引用本目录路径。
