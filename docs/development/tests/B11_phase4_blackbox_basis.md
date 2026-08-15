# B11 阶段④ 黑盒输入冻结基线（Agent3 阶段黑盒）

> 日期：2026-08-15（`Get-Date` 实测）
> 性质：黑盒验收的输入冻结记录——Agent3 运行期间以下输入一律以此哈希为准，任何漂移即判定运行无效。

## 1. 冻结输入

| 输入 | 版本锚点 | SHA256 |
|---|---|---|
| MCP 生产代码 | git commit `5479a3dc81bb51a62e988fc9174b4b6c93ebee1c` | git 权威 |
| 工具数 | 103（9 control + 94 domain） | 机械统计 |
| 6-LED 需求文档 | `docs/development/tests/B11_blackbox_requirement_draft.md` | `39e65e01e09cc4dfb39b8506b3a649ecd4459dea6141294f5f82f91d076a25a2` |
| Skill `skills/zynq_dev/SKILL.md` | — | `1506042952e184fe7d3528e5a8260032ed2e77e76ef5f74284d8ee22e7fb682f` |
| `phases/0_requirement.md` | — | `8fbce8559ea99d3edc939b0630fb84300b63f63e602b498c517d907814c81890` |
| `phases/1_physical_facts.md` | — | `d6e9b989a01d73c5d1710e3f8d03a1409f913f6b465704ebe0303f7f918221a0` |
| `phases/2_budget.md` | — | `9e38b24d321ef88be9d908f398b49acfc2792951bff44babd0798678f1659143` |
| `phases/3_architecture.md` | — | `557c85bcb8bdc0bdd7e2c880e4b031740132794ff9b9e2f6bdcd2457cb738f16` |
| `phases/4_proposal.md` | — | `f3411e796eae0b86fe04e692a6f699f86ab3939dfeb3e8f93ca37831a6b754da` |
| `phases/5_domain_implementation.md` | — | `45c0ad3f10ded72e1c2e9e7939276a6deeed7a586b80e290833131b1cda577f9` |
| `phases/6_consistency.md` | — | `d8a8c1f5dc6beb94dda34f5dd8b39e485951766a4533d5eada64ddbef4d1c082` |
| `phases/7_deployment_observation.md` | — | `5c9e9284bcb33cace2052d823b560dc0daefa85104defc73ce1b7a17700fb83b` |
| `phases/8_verdict_recovery.md` | — | `5086aa99cf1557bc4b73f6e88f0f09a08f1b85e5bea4b7f935cf099d446710cd` |
| `appendix_mechanics.md` | — | `e8ef4adcb4db1daf1a4151bc0033de72d69b7c371403c4f052938a79d0f15613` |
| 板卡物理事实（board package README + board_profile JSON） | `boards/ALINX_AX7020_v1.0/`（已锁定 package_manifest） | 板卡包锁定机制 |
| 回归基线（本轮起点） | 1417 collected / 1376 passed / 1 skipped / 40 deselected / 0 failed | B11 阶段③.2 记录 |

## 2. 硬门禁（沿用 B09/O7 R3，逐条）

1. 黑盒智能体的 EDA、构建、Manifest、部署与观测操作全部通过公开 `zynq_mcp` tools；
2. 不导入 `mcps.zynq_mcp.*` 内部模块，不直接启动 Vivado/XSCT/Tcl，不手工执行 `make`，不直接调用内部 Manifest publisher；
3. 全部长任务均由 Execution Ledger 提供真实状态、PID/身份、心跳、期限和恢复证据；
4. Consistency 通过（三 Manifest + board profile 一致）；
5. UART 含 `LED_E2E_PASS`（且无 `LED_E2E_FAIL`），PASS 后持续输出 WROTE/READ（需求 §3.1 持续循环）；
6. 读回来自引脚真实状态寄存器（需求 §3.2）；
7. 收尾按 Skill 7d：目标最终状态由需求确认（本需求=持续可见→保持运行），halt/reset 无依据不得执行；
8. 结束后无本轮遗留 Vivado/XSCT/XSDB 子进程。

## 3. 隔离规则（黑盒边界）

- 隔离工作区：`D:\_b11_p4_external\agent3_20260815\`（项目外），内含 Skill 快照、需求文档副本、板卡事实副本；
- 黑盒智能体**不得读取** `D:\fpgaproject` 下的任何文件（唯一例外：以子进程方式启动 `python -m mcps.zynq_mcp.server`，cwd 为项目根；`ZYNQ_BOARD_PROFILE_DIRS` 指向板卡包为公开事实）；
- 允许的命令执行：启动 MCP 服务器 + 运行仅含标准库与 `mcp` SDK 的客户端驱动脚本；其余 shell 执行必须为 0。

## 4. 运行有效性判定

Agent3 运行结束后由审核方机械核对：输入哈希是否与本节一致、MCP commit 是否未变、门禁逐条是否有证据。任何输入漂移 → 运行无效，需重跑。

## 5. v2 更新（阶段⑥.1 修复后，2026-08-15 —— Agent2 终验重跑用）

阶段⑥ Agent2 首轮终验 BLOCKED（服务端 P1：崩溃恢复残留 `UNOWNED_WORKER_PRESENT` 死锁，证据 `D:\_b11_p4_external\agent2_20260815\evidence\`）；⑥.1 整改轮修复（报告 [B11_phase6_1_fix_report.md](../mcp/B11_phase6_1_fix_report.md)）。以下为 Agent2 重跑的冻结基线 v2：

| 输入 | 版本锚点 | SHA256 |
|---|---|---|
| MCP 生产代码 | git commit `8b965301fb4ddf4b9c7676e58e5b25102f6e62cb` | git 权威 |
| 工具数 | 103（不变） | 机械统计 |
| 6-LED 需求文档 | 不变 | `39e65e01e09cc4dfb39b8506b3a649ecd4459dea6141294f5f82f91d076a25a2` |
| Skill `appendix_mechanics.md`（新增"引脚名必须真实查询"决策规则） | — | `1573e5ee5e761c54ea265b7af9f41b1bfa533e50cd16090e35a2cb8cff3c0fa9` |
| Skill 其余 10 文件 | 与 v1 一致（§1 表） | 同 §1 |
| 板卡物理事实 | 不变 | 板卡包锁定 |
| 回归基线 | 1426 collected / 1385 passed / 1 skipped / 40 deselected / 0 failed | ⑥.1 实测 |

硬门禁与隔离规则同 §2/§3；隔离区更换为 `D:\_b11_p4_external\agent2b_20260815\`。
