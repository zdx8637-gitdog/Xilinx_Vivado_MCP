# B12-A1 黑盒输入冻结基线（Agent3 阶段黑盒）

> 日期：2026-08-25（Get-Date 实测）｜性质：B12-A1 黑盒验收的输入冻结记录，运行期间任何输入漂移即判定运行无效。
> 触发：B12-A1 白盒真板 PASS（`docs/development/tests/B12_a1_whitebox_rerun_report.md`，commit `0aca2ef`）→ 用户授权：白盒 PASS 后计时 1 小时无回复则自动接黑盒。

## 1. 冻结输入

| 输入 | 版本锚点 | SHA256 |
|---|---|---|
| MCP 生产代码 | git commit `0aca2efccebc3a4f476a3b02902e9b134e017e20`（mcps 自 `837c5a5` 起零改动） | git 权威 |
| 工具数 | 104（9 control + 95 domain） | 机械统计 |
| 需求文档 | `docs/development/tests/B12_a1_requirement_draft.md` | `0de2946c530f9464cd0a7e3051b1b0221ad1f7b01c5cd4d64e9160c488c31675` |
| Skill SKILL.md | — | `1506042952e184fe7d3528e5a8260032ed2e77e76ef5f74284d8ee22e7fb682f` |
| phases/0_requirement | — | `8fbce8559ea99d3edc939b0630fb84300b63f63e602b498c517d907814c81890` |
| phases/1_physical_facts | — | `d6e9b989a01d73c5d1710e3f8d03a1409f913f6b465704ebe0303f7f918221a0` |
| phases/2_budget | — | `9e38b24d321ef88be9d908f398b49acfc2792951bff44babd0798678f1659143` |
| phases/3_architecture | — | `557c85bcb8bdc0bdd7e2c880e4b031740132794ff9b9e2f6bdcd2457cb738f16` |
| phases/4_proposal | — | `f3411e796eae0b86fe04e692a6f699f86ab3939dfeb3e8f93ca37831a6b754da` |
| phases/5_domain_implementation | — | `45c0ad3f10ded72e1c2e9e7939276a6deeed7a586b80e290833131b1cda577f9` |
| phases/6_consistency | — | `d8a8c1f5dc6beb94dda34f5dd8b39e485951766a4533d5eada64ddbef4d1c082` |
| phases/7_deployment_observation（含 hw_server 自启行） | — | `d6a037a5e216b5a9646a301070e2eab275f0b231c2e77f37ad0814a01433fbd5` |
| phases/8_verdict_recovery | — | `5086aa99cf1557bc4b73f6e88f0f09a08f1b85e5bea4b7f935cf099d446710cd` |
| appendix_mechanics | — | `1573e5ee5e761c54ea265b7af9f41b1bfa533e50cd16090e35a2cb8cff3c0fa9` |
| 板卡 README | `boards/ALINX_AX7020_v1.0/README.md` | `8cf4cc70ffa6d07dd06b08f63fbf291375a430e5742e5de63446e298edb33710` |
| 板卡 board_profile | `boards/ALINX_AX7020_v1.0/board_profile_ALINX_AX7020_v1.0.json` | `a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18` |
| 回归基线 | 1393 passed / 1 skipped / 41 deselected / 0 failed（N3 后实测） | B12-N3 记录 |

## 2. 硬门禁（沿用 B09/B11，逐条）

1. 全部 EDA、构建、Manifest、部署与观测操作经公开 `zynq_mcp` tools；
2. 不 import `mcps.zynq_mcp.*` 内部模块；不直接启动 Vivado/XSCT/Tcl；不手工 `make`；
3. 长任务全走 operation 机制，Execution Ledger 为状态真相；
4. 不得读取 `D:\fpgaproject` 下任何文件（唯一例外：以子进程启动 `python -m mcps.zynq_mcp.server`，cwd 项目根；`ZYNQ_BOARD_PROFILE_DIRS` 指向板卡包为公开事实）；
5. 命令执行仅限：启动 MCP 服务器 + 运行自身驱动脚本（仅标准库 + mcp SDK）；
6. 工程文件只写在隔离区。

## 3. 隔离与供给

- 隔离区：`D:\_b12_a1_external\agent3_20260825\`（项目外）——含 REQUIREMENT.md 副本、skill 快照、board/ 事实副本；
- **黑盒禁入**：厂商例程/教程（docs/ad7606boardinformation、docs/boardinformation 教程 PDF、`D:\BaiduNetdiskDownload` 厂商工程）、本仓库其余一切、白盒报告与实现细节。

## 4. 运行有效性判定

运行结束后审核方机械核对：输入哈希与本节一致、MCP commit 未变、硬门禁逐条有证据、UART 判据满足（≥4 轮 OK + `DMA_LOOP_PASS` 一次 + PASS 后持续输出）、Consistency 12/12、收尾按 Skill 7d（保持运行态）。任何漂移/违规 → 运行无效。
