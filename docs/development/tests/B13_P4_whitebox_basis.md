# B13-P4 白盒输入冻结与验收基准（含框架升级机制清单）

- 日期：2026-09-04（中国标准时间）
- 定位：B13-P4 = 需求冻结 + 白盒/黑盒。本文冻结**白盒输入**与**验收基准**；白盒在新会话、新隔离工作区执行，旧白盒证据只读参照。
- 分支：白盒工作在 `framework-iteration`（HEAD cf7a58d，109 工具）上执行；`master` 保持已验证稳定状态，合并须白盒+黑盒通过后由用户决定。

---

## 一、白盒目标（用户裁定：方案 3 合并）

1. **产品链**：高速 ADC→DMA→TCP 扫描上传模拟全链真板验收（B13 需求 v0.5 契约）。
2. **框架升级机制**（专门验收项）：本次升级的新功能/架构（M1–M5 + S1–S4）在产品白盒过程中逐项验证取证。

## 二、冻结输入（路径 + 只读约束）

| 输入 | 路径 | 状态 |
|------|------|------|
| B13 需求契约 v0.5（§3 UART 下行 / §3.1 ACK/NAK / §4 TCP 上行 / §4.2 CRC 口径与 KAT） | `D:\fpgaproject\docs\development\tests\B13_requirement_draft.md` | 冻结，改必先报 Erratum |
| 板卡配置包 | `D:\fpgaproject\boards\ALINX_AX7020_v1.0\` | 冻结 |
| 泛化 Skill（含 S1–S4 四补） | `D:\fpgaproject\skills\zynq_dev\` | 冻结 |
| MCP（109 工具：11 control + 98 domain） | `D:\fpgaproject\mcps\zynq_mcp\`（framework-iteration） | 本轮被测对象，版本 cf7a58d |
| 上位机测试程序（51/51 通过） | `D:\fpgaproject\PC_end_tcptest\` | 冻结，白盒只读调用 |
| 升级机制清单（M1–M5 + S1–S4 全文） | `D:\fpgaproject\docs\development\mcp\B13_exposed_framework_issues.md` | 冻结 |

## 三、参照与工作区

- **旧白盒参照（只读，永不修改）**：`D:\_b13_external\agent1_20260829\`（P0/P1/P2 全过；板载 P2 最终固件 bit 1da587bc / ELF d6f94091；含 mcp_client.py/mcp_call.py 工具与全部证据）。
- **新白盒工作区**：`D:\_b13_external\agent1_p4_20260904\`（evidence/ 证据目录 + mcp_client.py/mcp_call.py 已就绪）。
- 白盒提示词：`D:\_b13_external\agent1_p4_20260904\WB_PROMPT.md`。

## 四、产品链验收基准（照 v0.5，与 P0–P2 口径一致）

- 链路：AD7606C-16（CH6 唯一上行）→ 采集引擎 → xpm_fifo_async → axis_register_slice → AXI DMA(SG) → DDR3 50MB 覆盖式 → PS lwIP TCP Server（192.168.1.10:5001）→ PC 上位机。
- 帧格式：5000 帧/图 × 5000 点 × 16-bit（帧 = 行，SEQ 大端）；CRC32 IEEE 大端覆盖 12B 头+零 CRC 域+payload（KAT READY 帧 CRC=`54acfe90`）。
- UART 下行：MAGIC `A5 5A` + CRC16-CCITT-FALSE 小端（KAT "123456789"→`0x29B1`）；ACK/NAK 语义照 §3.1；NAK reason 0x01–0x07；帧后 ASCII 调试文本非帧内容（主机须 MAGIC 扫描）。
- 三档实测：2k / 100k / 1M SPS，L2 事件计数 1:1（CONV/BUSYF/FRSTD/RD/RERR/CERR）、TPG 档位映射、REG_READBACK、RST 回归、溢出丢最旧+计数。
- 门禁脚本判据按档位区分（P2 审核注记 ① 遗留：2k/100k 档不得套 ≥2MB/s 阈值）。

## 五、框架升级机制验收清单（专门验收项，逐项取证）

- **M1 环合法化**：PS_BUILD 后合法 `workflow_rollback(PL_BUILD)`（lane=IDLE 前提），下游 artifact revision 失效；`workflow_history` 有回退记录；全程无外科改 ledger。白盒须真实走一次「构建→缺陷→回退→修复→重建」。
- **M2 平台原子**：`platform_package_user_ip` 打包自研 RTL（真 Vivado 子进程路径）→ `platform_add_ip` 实例化 → `platform_set_bd_object_property`（clk 端口 CONFIG.FREQ_HZ / 时钟 pin CONFIG.ASSOCIATED_BUSIF）→ `platform_make_external` 派生名实采（S_AXI→S_AXI_0 语义）。BD 内自研引擎应以用户 IP 方式进设计（替代 P1 的「PL 域工程 + .bd 旁支」）。
- **M3 确定性**：同输入重导出 XSA 字节一致（SHA256 相同）；manifest revision 只依赖内容。
- **M4 元数据同步**：session 恢复后 manifest 与磁盘产物一致；`verify_consistency` 干净通过。
- **M5 幂等/重试**：同参数重复 set（define/属性）幂等成功；一次 FAILED 后同签名重试合法放行（不再被 dedup 永久锁死）。
- **S1–S4 过程性**：修复必配回归；synth CRITICAL WARNING=0 门禁；AXI 握手缺陷模式库自查；双端字节级 KAT 对拍（下位机与上位机同组向量）。

## 六、白盒规范（长期纪律）

- 禁 ask_user_question：决策点/方向级问题 → 停止工作、汇报现状与证据，等主代理/用户裁决。
- 证据机器可验，禁止自证；关键结论主代理亲自复核。
- 硬件操作严格串行；长命令后台执行。
- 冻结资产与旧白盒工作区只读。
- 完成标准：§四产品链门禁全过 + §五机制清单全 ✓ + 证据齐全（evidence/），随后停止汇报，不自行进入黑盒。
