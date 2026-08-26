# B12-A2 黑盒验收基线（FROZEN — 2026-08-26）

> 日期：2026-08-26 ｜ 状态：**FROZEN**（白盒 v2 通过 + 保守修复完成；本文件为黑盒唯一供给基线，冻结后不得修改）
> 模式：照 B12-A1 黑盒先例（隔离区 + 全新无记忆智能体 + 仅公开契约）。

## 1. 黑盒供给白名单（唯一可读材料）

| 材料 | 路径 |
|---|---|
| 需求文档（v2，定稿） | `docs/development/tests/B12_a2_requirement_draft.md` |
| 板卡包公开事实面 | `boards/ALINX_AX7020_v1.0/`（README/board_profile/adc/ 事实卡+数据手册+引脚 JSON） |
| 泛化 Skill | `skills/zynq_dev/`（SKILL.md + phases 0–8） |
| 公开 MCP | 105 工具（`python -m mcps.zynq_mcp.server`，stdio） |

## 2. 黑盒禁入（硬性）

- 白盒报告与工作区（`docs/development/tests/B12_a2_whitebox*`、`workspaces/b12_a2_agent1c_*`）；
- 厂商例程/教程、本仓库其余一切、三个 legacy 目录；
- 盲测答案（通道号/频率）——**只存在于用户手中**，智能体必须从真板采集数据自行测定。

## 3. 执行面

- 隔离工作区：`D:\_b12_a2_external\agent3_20260825\`（新目录，黑盒智能体唯一可写区）；
- 全新无记忆智能体；仅凭 §1 材料 + 公开 MCP 独立完成 S0–S8 全流程；
- 硬件环境：板卡 COM4（CP210x，115200）、J11 AD7606C（±10V 量程）、正弦已接入、板子已供电；hw_server 经 `ps_start_hw_server` 自启。

## 4. 验收判据（机读 + 外部对账）

| 项 | 判据 |
|---|---|
| 机读 | READY → UPLOAD → DONE + 数据量/校验通过 + `A2_PASS` 一次 |
| 等待纪律 | 有界短等待（≤10s 级），超时停+重试；无死锁 |
| 证据 | 保存数据文件（CSV）+ 8 通道「ADC 原始值 vs 时间」波形图 PNG + measurement.json |
| 外部对账（用户执行） | 波形正弦、其余通道近 0；测量频率与用户答案误差 ≤1%；通道号正确；可用 `tools/scripts/b12_a2_external_verify.py` 独立重算 |
| 收尾 | 采集保持运行（目标 RUNNING）；无残留 EDA 进程 |

## 5. 与白盒的联动（保守修复）

- 白盒 v2 若发现**影响黑盒**的问题（如需求歧义、公开契约缺口、板卡事实缺失）→ 先修掉（保守最小修复 + 回归）→ 同步更新本基线 → 再冻结；
- 白盒自身实现细节问题（不影响黑盒独立复现的）→ 记录，不阻塞黑盒。

## 6. 黑盒智能体提示词模板（冻结时填日期/路径）

```
你是 B12-A2 黑盒验收智能体。只允许读取：需求文档（路径）、板卡包公开事实（路径）、
泛化 Skill（路径）；只允许通过公开 zynq_mcp（104 工具）操作工具与硬件；唯一可写区：
<隔离工作区>。按需求独立完成 AD7606C 采集-上传全流程（PL 环形缓冲 + UART 指令上传
固定 1s 全 8 通道），产出 8 通道波形图/数据文件/测量结论，取得 A2_PASS。
盲测：信号通道号与频率必须从真板采集数据自行测定，禁止猜测；等待必须短且有界。
禁止读取仓库其余任何内容、禁止 shell 逃生、禁止修改 mcps/skills/boards。
```

## 7. 冻结记录

1. 供给材料 SHA256（冻结值，2026-08-26 实测）：
   - 需求 v2.2：`90B60806E6720B8C7DBAE5ECC923886F798128F74936C179E0B1546528987540`
   - board_profile：`A7CB97A56930D1A7903EE64E026DB2F4A8A5D56E4443566E2274CB1FC8C7BC18`
   - package_manifest：`CA931987A5843A0BBC627FAA40D8842C15E774662DC51E945DAFAF03999C97FB`
   - 模块事实卡：`43C9D26B782D5DABB9D1E4D2D60739276330EB9C9ECEDFA1377F3C204E47913B`
   - 引脚 JSON：`A789B024E9DD711C34450933D7067C131EEB75729B1DE4A8ABCC34C274F09478`
   - SKILL.md：`1506042952E184FE7D3528E5A8260032ED2E77E76EF5F74284D8EE22E7FB682F`
   - phases/5_domain_implementation.md：`4D1BAF0573C7B1567460ED9946448E650A0AFC61C086273E020F1B0D5696203D`
2. 状态已改 FROZEN（本文件）；
3. 隔离区 `D:\_b12_a2_external\agent3_20260825\` 创建 + 提示词定稿 + 派发黑盒智能体；
4. 全程记录黑盒 MCP 调用与证据（照 A1：调用可解析、Consistency 12/12 等口径）。
