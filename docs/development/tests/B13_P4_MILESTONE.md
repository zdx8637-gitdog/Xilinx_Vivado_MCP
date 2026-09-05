# B13-P4 里程碑（P4_MILESTONE）

- 日期：2026-09-05（中国标准时间）
- 结论：**B13-P4 白盒 PASS + 黑盒 PASS；框架升级（修复轮#1–#10 + 验证方法论 v2 + 偏离审计器）合入 master（`687501c`，随后 `fe2d28b` 前后一致）。**

## 一、产品链验收（契约 v0.5，真板机读）

| 判据 | 白盒 | 黑盒（终审） |
|---|---|---|
| L1 TPG 1M 整图 | 2.289 MB/s 零错 | 2,086,666 B/s 零错、25M 点精确自停 |
| 三档速率 ±0.5% | 999,598/99,981/1,999 | 2000/100000/1000040（+0.004%）|
| L2 事件计数 1:1 | CONV=BUSYF=FRSTD=25M、RD=8×、RERR=CERR=0 | 同口径 PASS（250ms 窗口 250001/250000/2000000/250000）|
| P3 ADC-CH6 1M 真采 | PASS | 5000 行/25M 点/0 丢错/2.087MB/s |
| STOP/覆盖/溢出/重连/KAT 双向量 | PASS | PASS（STOP 3,235,925 点后复采干净；覆盖双 START；water 4486→overflow 3772；重连 1000+4000 行 0 缺 0 重；0x29B1/0x54ACFE90）|
| verify_consistency | 12/12 | 12/12 |
| 交付物 | bit 1da587bc 系（P2） | **bit 38f32cfd / ELF c73d32a4**（manifest ca4794df/97effb85/f1459f36；板上终态 PL=38f32cfd PS=R2）|

## 二、框架升级（修复轮#1–#10，全部合入 master）

| 轮 | 内容 | 触发源 |
|---|---|---|
| #1 | M1 环合法化：workflow_rollback/resume_from + ROLLBACK_TARGETS + workflow_history | P2 真板复盘 |
| #2 | M5 重试语义：dedup_lookup（仅 SUCCEEDED 阻断） | 同上 |
| #3 | M3 XSA 确定性：zip 层归一 | 同上 |
| #4 | M4 元数据同步：磁盘 xsa 哈希为真相 | 同上 |
| #5 | M5b define 幂等（XSCT already contains） | 同上 |
| #6 | M2 平台原子：package_user_ip 两段式 + set_bd_object_property + make_external 派生名实采 + Skill S1–S4 | 升级计划 |
| #7 | 白盒 F1/F3/F6 根治（响应层级/XSA 成员时间戳/平台级回退目标）+ F7b Skill | 白盒反馈 |
| #8 | 黑盒四项 P1：ps_mem_read 无 0x 前缀解析 + fail-closed、loadhw ADDRESSING 自诊断、PL 摘要含 IP 产品、pl_create_project 注册 IP 仓库 | 黑盒反馈 |
| #9 | 摘要覆盖闭合：.cproject + ip_repo component.xml/xgui（含 Python glob 点文件陷阱） | 黑盒反馈 |
| #10 | ADDRESSING 注入：export 自动合成 hwh ADDRESSING 段（真板 hsi 验证 loadhw rc=0 + DAP 直读 PL 寄存器） | 升级计划 |

MCP 保持 **109 工具**（11 control + 98 domain）。回归基线：**1499 passed / 1 skipped / 43 deselected / 0 failed**（collected 1543）。

## 三、验证方法论升级（本里程碑最大无形资产）

- [validation_methodology.md](../validation_methodology.md) v2：**L0 测契约，L1 测功能，L2 测状态（L2-A 规范 + L2-B 扰动），L3 测公共面，偏离审计测专家补偿，黑盒测未知世界**；行为偏离六型 + A–F 分类；专家帮助率指标；回流泛化审查（项目特化不进 Skill）。
- `tools/audit/bypass_audit.py` 偏离审计器（工具矩阵/未调用/失败/重试/替代/外部脚本，实测白盒 51/109、黑盒 62/109 工具）。
- Skill 附录 §13 已知问题与处理建议（7 条通用件，状态标记 ✅/⚠️/📌）。

## 四、黑白盒教训（一句话版）

白盒（专家补偿）绕开的路径 = 黑盒（新手按说明书）撞上的墙——两者差异量化了"文档面 vs 实践面漂移"。黑盒 3× token 是独立验证的合理代价；修复轮#7–#10 已把漂移点逐个焊死，并把"新手路径探针/状态转移覆盖/绕行审计"写进下一代测试体系。

## 五、遗留（全部登记，不阻断）

- 框架侧：无未闭 P1。可选增强 = XSA 注入已落地（#10）；F7a 仿真后端互斥（进程层改造）、F8 PS include-path 在 Skill §13.7/项目文档登记。
- 项目侧（见黑盒 HANDOFF.md §5）：receiver 单行 rate 口径、版本串自动化、register_slice 受控复位——下轮 BD 变更顺手做。

## 六、证据索引

- 白盒：`D:\_b13_external\agent1_p4_20260904\evidence\`（FINDINGS.md 8 项 + 全链证据）
- 黑盒：`D:\_b13_external\agent2_p4_20260904\`（FINAL_REPORT.md / LESSONS_LEARNED.md / acceptance_summary.json / evidence\）
- 框架修复轮：#1–#10 均在 git log（framework-iteration → master `687501c`）
- 板卡终态：黑盒终版固件运行中（PING→PONG 主代理亲测复核）
