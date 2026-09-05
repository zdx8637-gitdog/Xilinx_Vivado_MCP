# B13-P4b 暴露型白盒重测基线（输入冻结 + 规则）

- 日期：2026-09-05（中国标准时间）
- 定位：P4 已黑白盒双过、修复轮#1–#10 已合入 master（`18e8445`）。本轮白盒在**当前最修复态框架**上重测同一产品链，核心原则由用户定：**暴露工具问题——所有问题逐条上报、不隐藏；是否修复由主代理/用户裁定；规避旧白盒的绕行策略**。
- 结构（用户拍板）：**主线自然走 + Phase R 独立复验**——"必走清单"不进主线（防止白盒路线变形、防止踩点式打卡与漏报）；已知问题复验由主代理亲驾执行。

## 一、工作区与执行者

- **新白盒工作区**（全新空目录）：`D:\_b13_external\agent1_p4b_20260905\`（evidence/ + WB_PROMPT.md + mcp_client/mcp_call 已就绪）
- 执行者：用户新会话派发；任务书 `WB_PROMPT.md`
- **Phase R（主代理亲驾，不在白盒任务内）**：逐条复验黑盒 LESSONS_LEARNED + 修复轮#1–#10 修过的问题——输出"已修复 ✓ / 仍存在 ✗ / 行为已变"机读状态表。

## 二、输入白名单（SHA 冻结）

| 输入 | 路径 | SHA256 |
|---|---|---|
| B13 契约 v0.5 | `docs\development\tests\B13_requirement_draft.md` | `422B4E7CCE33578C67A992C63223A8CEFF68F724A689AF76FC656AF2A14CF5B7` |
| 板卡包（7 件关键文件） | `boards\ALINX_AX7020_v1.0\` | 与 B13_P4_blackbox_basis.md §二一致（README `8CF4CC70…` 等） |
| Skill（11 文件） | `skills\zynq_dev\` | SKILL.md `E1A157D6…41CD01`；appendix_mechanics `913E0ED0…FE2D15`；phases/0 `8E38FD41…3EA01`、1 `D6E9B989…221A0`、2 `9E38B24D…59143`、3 `557C85BC…38F16`、4 `F3411E79…54DA`、5 `BDE7A8DC…40A26`、6 `D8A8C1F5…D1C082`、7 `2A085324…935936`、8 `88C7053E…0FB10` |
| 公开 MCP | `python -m mcps.zynq_mcp.server`（cwd `D:\fpgaproject`，`PYTHONIOENCODING=utf-8`） | 109 工具 |

## 三、防绕行规则（任务书 §二已写，基线重申）

1. 强制公共面：仅 MCP 公开工具 + Skill；xsdb/tcl/batch 直驱仅作诊断回退且逐条登记。
2. 偏离逐条上报：模板 = `Tool/现象/复现/影响/证据/建议分类`；**只暴露不裁决**。
3. 框架只读：MCP 代码与 Skill 不得修改。
4. 完工后主代理跑 `tools/audit/bypass_audit.py` 机械审计（工具矩阵 + 未调用清单逐条标注"未涉及/已绕开"）——绕行没有文字游戏空间。

## 四、验收面（产品轨照 v0.5 全判据）

L1 TPG 1M 整图（5000 行/25M 点/零错/≥2MB/s）；三档 2k/100k/1M ±0.5%；L2 事件
计数 1:1（RD=8×、RERR=CERR=0）；P3 ADC-CH6 真采 25M 点；STOP/覆盖/溢出/重连；
KAT 双向量（0x29B1/0x54ACFE90）；verify_consistency 12/12。机读证据进 `evidence\`。

## 五、环境事实

- 板卡：黑盒终版固件在跑（白盒自建会覆盖，预期）；UART=COM4；J11 AD7606C ±10V；
  GEM0↔PC（.10/.20）；hw_server tcp::3121 运行中；MCP 账本已清（lane IDLE、无
  残留会话、worker ABSENT）。
- 历史参照只读：旧白盒/黑盒工作区可读背景，workaround 不得照搬（照搬=绕行须登记）。
