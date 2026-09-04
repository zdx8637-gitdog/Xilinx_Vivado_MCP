# B13-P4 黑盒基线（输入冻结 + 验收基准）

- 日期：2026-09-04（中国标准时间）
- 定位：P4 白盒已通过（产品链全过 + 框架机制 M1–M5/S1–S4 取证，修复轮#7 后 M2/M3 重新判定成立）。本文件冻结**黑盒输入**与**验收基准**，供全新无记忆黑盒会话执行。
- 框架版本：`framework-iteration` @ **789fe26**（109 工具：11 control + 98 domain）；`master` 保持稳定版未动，合并须本黑盒通过后由用户决定。

---

## 一、黑盒工作区（全新空目录，仅白名单可读）

- **工作区**：`D:\_b13_external\agent2_p4_20260904\`（evidence/ 证据目录 + mcp_client.py/mcp_call.py 工具 + AGENT2_PROMPT.md 任务书）
- **隔离纪律**：黑盒从公开契约独立重新实现；**禁止读取**：白盒工作区（`agent1_20260829`、`agent1_p4_20260904`）、本仓库 `docs/development/` 除契约外的一切、MCP 源码、上位机实现细节、历史证据。

## 二、输入白名单（路径 + SHA256 冻结）

| 输入 | 路径 | SHA256 |
|------|------|--------|
| B13 需求契约 v0.5（唯一考题） | `D:\fpgaproject\docs\development\tests\B13_requirement_draft.md` | `422B4E7C…4CF5B7` |
| 板卡包 README | `boards/ALINX_AX7020_v1.0\README.md` | `8CF4CC70…DB33710` |
| 板卡包 XDC | `boards/ALINX_AX7020_v1.0\board.xdc` | `055A3AAA…ACAECE2` |
| 板卡包 profile | `boards/ALINX_AX7020_v1.0\board_profile_ALINX_AX7020_v1.0.json` | `A7CB97A5…C8C7BC18` |
| 板卡包 manifest | `boards/ALINX_AX7020_v1.0\package_manifest.json` | `CA931987…399C97FB` |
| PS7 预设 | `boards/ALINX_AX7020_v1.0\ps7_preset.tcl` | `14222186…737B3299` |
| ADC 引脚事实 JSON | `boards/ALINX_AX7020_v1.0\adc_ad7606c_pinmap.json` | `A8FD6F8F…9F50C44B` |
| 泛化 Skill（11 文件，全部冻结） | `skills/zynq_dev\` | SKILL.md `38F8689C…4755A2`、appendix_mechanics `B9CD3035…0B9B255`、phases/0–8 共 9 件（SHA 见 §六） |
| 厂商 FPGA 例程（授权公开） | `docs\ad7606boardinformation\AD7606C-16模块资料\AD7606C-16模块资料\参考例程\FPGA\AD7606C_WARE.zip` | 按件自取 |
| 官方 Vitis 例程（AXI DMA SG） | `D:\BaiduNetdiskDownload\AX7020_2023.1\course_s2_vitis\`（契约点名 31/20/19） | 目录 |
| 上位机（冻结验收对端，可运行） | `D:\fpgaproject\PC_end_tcptest\` | 51/51 已验 |
| 公开 MCP（工具即契约，经 get_capabilities 自描述） | `python -m mcps.zynq_mcp.server`（cwd `D:\fpgaproject`，`PYTHONIOENCODING=utf-8`） | 109 工具 |

## 三、验收基准（照 v0.5，机读证据）

- 全链独立实现：AD7606C-16（CH6 唯一上行）→ 采集引擎 → FIFO → AXI DMA(SG) → DDR3 50MB 覆盖式 → PS lwIP TCP Server（192.168.1.10:5001）→ PC。
- UART 下行 §3 + ACK/NAK §3.1；CRC16-CCITT-FALSE 小端 KAT `0x29B1`；TCP 上行 §4 帧格式；CRC32 IEEE 大端覆盖口径 KAT（READY 帧 `54acfe90`）。
- 门禁：L1 TPG 1M 整图（5000 行、零错、≥2MB/s、25M 点自停）；三档 2k/100k/1M 实测 ±0.5%；L2 事件计数 1:1（RD=8×、RERR=CERR=0）；溢出丢最旧+计数；覆盖式；双端字节级 KAT 对拍；§7.1 机读接收器独立验收（不依赖 GUI）。
- 判据按档位区分（2k/100k 不套 ≥2MB/s 阈值，仅 1M 套）。

## 四、环境事实

- 板卡：ALINX AX7020；UART = **COM4**（板载 CP210x，115200）；**COM7 = CH340 波形发生器，勿选**；AD7606C 在 J11（±10V）；板载现状 = P4 白盒终版固件（running），黑盒自建固件将覆盖（属预期）。
- 网络：PS GEM0 ↔ PC 直连（PS 192.168.1.10 / PC 192.168.1.20）。
- hw_server 经 `ps_start_hw_server` 自启；部署先 `ps_halt_target` 再 `ps_initialize_ps`（DAP 卡死教训）。

## 五、黑盒规范（长期纪律）

- 禁 ask_user_question：决策点/方向级问题 → 停止工作、汇报现状与证据，等主代理/用户裁决。
- 证据机器可验，禁止自证；主代理亲自复核。
- 硬件操作严格串行；长命令后台执行。
- 冻结资产与白名单外的一切只读/禁读。
- 完成标准：§三门禁全过 + 证据齐全（evidence/）→ 停止 → 汇报；不自行进入合并或后续阶段。

## 六、Skill 全文件 SHA（黑盒基线冻结）

| 文件 | SHA256 |
|------|--------|
| skills/zynq_dev/SKILL.md | `38F8689C8EA5CDD88041AC86FA2AB5FE4BC818F5C901E1E9D0250A84F04755A2` |
| skills/zynq_dev/appendix_mechanics.md | `B9CD3035990DB74B9A4C93C96A12D44C3938343808C2C4F45CD5BCC0E0B9B255` |
| skills/zynq_dev/phases/0_requirement.md | `8FBCE8559EA99D3EDC939B0630FB84300B63F63E602B498C517D907814C81890` |
| skills/zynq_dev/phases/1_physical_facts.md | `D6E9B989A01D73C5D1710E3F8D03A1409F913F6B465704EBE0303F7F918221A0` |
| skills/zynq_dev/phases/2_budget.md | `9E38B24D321EF88BE9D908F398B49ACFC2792951BFF44BABD0798678F1659143` |
| skills/zynq_dev/phases/3_architecture.md | `557C85BCB8BDC0BDD7E2C880E4B031740132794FF9B9E2F6BDCD2457CB738F16` |
| skills/zynq_dev/phases/4_proposal.md | `F3411E796EAE0B86FE04E692A6F699F86AB3939DFEB3E8F93CA37831A6B754DA` |
| skills/zynq_dev/phases/5_domain_implementation.md | `A4FD59A04D4EA358C1EE125DE1889AB99A543CAFB7E9C7F132DC43A5C23EBA57` |
| skills/zynq_dev/phases/6_consistency.md | `D8A8C1F5DC6BEB94DDA34F5DD8B39E485951766A4533D5EADA64DDBEF4D1C082` |
| skills/zynq_dev/phases/7_deployment_observation.md | `3BBB0C2A1BC968B30A5D64C8250BEEE43671EC6929761DCBF8D0883DAD2AFFFB` |
| skills/zynq_dev/phases/8_verdict_recovery.md | `88C7053ED4754B26E7B90ED234472B7E994786411ED5B28B2129B4FE6AB0FB10` |
