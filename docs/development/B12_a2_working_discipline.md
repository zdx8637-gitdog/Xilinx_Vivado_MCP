# B12-A2 会话纪律速查（上下文压缩后必读）

> 目的：本轮会话曾因上下文压缩丢失关键纪律（零轮询、停滞判定、串行执行等），造成误判与打扰。
> 本文件是持久记忆：任何上下文压缩、会话恢复、goal 续轮之后，**先读本文件再行动**。
> CLAUDE.md 概览区有指针；本文件不属于冻结规则节，可随工作进展更新。

## A. 与子代理协作纪律（最高优先级，已两次犯错，不得再犯）

1. **零轮询**：不主动查询子代理状态（list_agents / git status / 进程检查都不做），只依赖**完成通知**。goal 轮次要求"make progress" ≠ "查子代理"——无独立工作可做时就安静等。
2. **静默 ≠ 停滞**：子代理思考、读代码不产生文件改动与进程，属正常状态。停滞阈值必须高；"几轮无文件改动"不是停滞证据。已发生的两次误判：A2 首轮 stall 误判、修复轮 7 轮误判后误打断。
3. **确需判断停滞时**：先向用户汇报观察与依据，经用户同意才处理；不得自行中断/重定向子代理。
4. **串行执行**：修复 / EDA / 硬件相关的子代理同一时刻只跑一个。**修复轮先行，白盒等修复落地 + 审查 + 提交之后才启动**（并行派发曾被用户纠正）。
5. **禁用 goal 工具**（用户 2026-08-25 定）：不再创建 / 恢复 / 编辑 goal；任务推进由**子代理完成通知事件驱动**，无通知就不动；收到通知后按 §F 动作链执行。当前 goal 已暂停，勿再 resume。

## B. B12-A2 测试方案（用户 2026-08-25 定，不可自行更改）

- **方案乙**：PL 环形缓冲（BRAM）**连续采集永不停机**；PS 收到 UART 指令 `UPLOAD` 后上传**最新固定 1s 全 8 通道**原始采样值（数据量固定、非参数）；传完打印 DONE 断开发送，采集继续。
- 采样率 ≥1 kHz（建议 2000 Hz）；上行经 PS UART1（COM4，115200）。
- **证据（solid 证实原则）**：保存数据文件（CSV）+ **8 通道「ADC 原始值 vs 时间」波形图（原始计数值，不换算电压）** + measurement.json + `A2_PASS`。外部独立重算工具：`tools/scripts/b12_a2_external_verify.py`（方差识别通道 + 插值过零初值 + 四参数正弦拟合测频；1s 数据可满足 ≤1% 精度；输出 measurement.json + 8 通道 PNG）。
- 固件自报状态行**不是**验收证据，只作流程信号。

## C. 盲测保密（B12-A2）

- 通道号与频率是**用户持有的答案**；一切供给智能体的材料（需求文档 / brick plan / 任务提示）**不得出现**；"1s 可见 10 个波形"这类表述会泄密，禁止。
- 白盒报告可含白盒自己的测定结论；黑盒供给白名单 = 需求文档 + 板卡包公开事实 + 泛化 Skill + 公开 MCP；黑盒禁入白盒材料与仓库其余内容。

## D. 缺陷分类口径（用户定，勿再混淆）

- Ledger / MCP 生产代码只管赛灵思**开发流程**；**D-D（等待不超时）属测试协议问题** → 由需求 v2 的确定性协议根治，**不改 MCP**。
- **修复轮范围**：D1 准入死锁、D-B 参数契约（TypeError→OUTCOME_UNKNOWN）、D-E 未决操作恢复（P6 gate 永久阻断）、D-A add_ip 双通道配置静默失效、D-C 编译 stderr 被吞；**P2 只记录不修**：操作 deadline 无 watchdog。
- **测试纪律写需求文档，不写 Skill**（Skill 只管开发流程，测试最多一句带过）。测试方案按项目走，需求文档承载。

## E. 仓库纪律与工具事实

- **CLAUDE.md 冻结规则节**哈希 `66666d2037afa7c178657c8c25c58a8addbfdabbf36731b1a1f6be232eb3e3ea`；算法 = `# AI Agent 驱动 Zynq-7020 项目规则` 标记起 → EOF 去掉尾换行。只能改标记之前的概览部分，改后必须机械复核哈希不变。
- pytest **从仓库根**运行（勿 cd mcps）；基线 1435 collected / 1393 passed / 1 skipped / 41 deselected / 0 failed；修复不得净减；修复轮禁跑 host_live / device_live（避免与硬件轮冲突）。
- GitHub 网络间歇抖动：push 以 git 输出的更新行（`old..new main -> master`）为成功凭据；ls-remote 复核失败时注明待复核，不反复轰炸。
- pip 镜像：阿里云 `https://mirrors.aliyun.com/pypi/simple/`（直连超时、清华 403）。matplotlib 3.11.1 + numpy 2.4.3 已装。
- 硬件：COM4 = 板载 CP210x（PS UART1）；COM7 = CH340 波形发生器（勿选）；AD7606C 在 J11，量程跳线 ±10V。
- 需求文档：`docs/development/tests/B12_a2_requirement_draft.md`（v2）。

## F. 当前进行时状态（随进展更新本段）

- **用户授权（2026-08-25，最新）**：修复 → 白盒 v2 → 黑盒**按序自动推进**。修复完成后审查，无问题即提交推送并准备环境开启白盒 v2；白盒完成后审查，无大问题即准备环境开启 **A2 黑盒**；有问题**保守处理**（影响黑盒的修掉再开黑盒）；**仅遇重大问题才停止等待用户指示**。
- **修复轮子代理 5264308c**：✅ **修复轮 #1/#2/#3 全部完成并经主代理审查（2026-08-25）**。#1 = D1/D-B/D-E/D-A/D-C（报告 B12_a2_flow_fix_report.md）；#2 = D1 残留 generation/参数契约泛化/ps_compile 可见性/pl_reset_run/manifest 版本化/add_ip 归因（B12_a2_flow_fix2_report.md）；#3 = **deadline watchdog（D1/D-D 共同根治本；局限：synth/place/route 长跑分支未包装）**+ system_top 保留名防覆盖 + create_session resume_hint + Skill phase5 纪律（XDC 注释独占行/多驱动检查）（B12_a2_flow_fix3_report.md）。当前回归基线：**1467 collected / 1425 passed / 1 skipped / 41 deselected / 0 failed**（主代理逐次复核）。遗留 P2：长跑工具 deadline 包装、UART 捕获加固（需求 v2.1 承接）。
- **白盒 v2 子代理 40bcfd5c**：✅ **完成（最终对账定案，2026-08-26）**。project_h 全量贯通：FSCAL=1999Hz、一致性 12/12、A2_PASS、**完整 2000 帧零丢字节（57600 波特率，sum16 通过）**、8 通道波形图（1s 恰 10 周期）。**最终盲测值（数据推导 + 外部独立复核一致）**：通道 = 丝印 CH6 ✓（与用户答案一致）；频率 = **9.965 Hz**（插值过零/网格 LS/稳定 GN 三法一致；用户答案 10Hz，误差 0.35% ✓ ≤1%）；Vpp = 2.68V。⚠️ 教训：外部脚本原 GN 会发散到 10.19Hz 劣质最优——已修（网格初值+有界 GN，commit 064a1fa）。证据在 workspaces/b12_a2_agent1c_20260825/evidence/。收尾：目标 RUNNING、hw_server/rdi_xsdb 按 N3 语义保留、无 Vivado 残留。
- **新 P2（黑盒前修复轮 #4 范围）**：D2=`pl_generate_bitstream` 输出=impl_1 运行目录时 file copy 自复制失败（BITSTREAM_NOT_FOUND，白盒已绕）；`pl_reset_run` 转发 `-force` 到 reset_runs 报 Unknown option；MCP UART 捕获偶发丢字节（每捕获 20–100 字符，<0.1%，按用户口径=测试流程、需求 v2.1 有界重试对抗，框架加固留 P2）。
- **黑盒（431fa5c5，旧）**：🛑 **2026-08-29 已停止并弃用**（其上下文带历史失败迭代，不符「全新无记忆」黑盒纪律；隔离区 agent3_20260825 保留归档）。此前状态：⏸️ 暂停（2026-08-28 白天，用户成本考虑，晚上再跑）。已中断；v14（WR=1）排除。**★ 主代理仿真验证（2026-08-28，.tmp_sim/，公开 MCP 仿真四工具跑通）**：逐字提取黑盒 v14 采集逻辑 + 数据手册级 AD7606C 行为模型自检仿真 → **12 帧中 2–12 帧 8 通道全部完美捕获（1111…8888 与模型一致），第 1 帧为启动瞬态 x；接口时序全部合规（conv_high=2540ns、busy_high=650ns、busy_fall→cs_fall=29ns、RD 100/100ns、采样于 RD 落+40ns）** → **黑盒 v14 RTL 逻辑正确、时序合规——真板读不到数据不是它的逻辑问题**，而是模块/芯片级（CM2368 行为差异/DB 通路/模块引脚电平）或环境效应（白盒 6µs 大余量吸收）。波形图 `.tmp_sim/sim_waveform.png`、通道对照图 `.tmp_sim/sim_channels.png`。**晚上选项更新**：①黑盒改用"大余量时序"（BUSY 落后等 ≥1µs 再读、RD/采样放宽——本板实测有效配方，需用户授权给配方）；②查模块芯片丝印；③万用表量模块 OS/DB 引脚电平。另：修复轮 #5（Skill 补丁：接口时序仿真强制步骤）已落地。
- 外部对账工具已提交推送（commit 50c7ca7）；需求 v2 已提交推送（commit c18b89c）；记忆文档 fea63fb、授权记录 5af9c2b、黑盒基线草案 4732cd6/c5294c5 均已推送。**需求已演进至 v2.2**（上传纪律 + **快照冻结解耦硬性要求**——混帧截断/11Hz 误读的根因，commit b974997）。
- **协调侧实验 B（用户 2026-08-28 白天批准并启动）**：白盒工作区新目录 project_i（不动 project_h），单变量实验——把固定 6µs 等待换成 BUSY 高→低**电平等待**（3 级同步、不用边沿），其余逐字节不变（CONV 40ns 脉冲 / RD 50-10ns / +40ns 采样 / 8 通道 / WR=1 / RESET=0 / FR_D 悬空 / 100MHz / 固件 / 57600）。目标：确认 BUSY 链路能否驱动已证实的采集链。已派发 40bcfd5c（先接口时序仿真后上板）；完成通知后主代理审查（帧推进 2000Hz / 波形 / 频率 ≤1%）。成功→黑盒问题指向别处（按 big_seen 分流）；失败（WPTR 冻结）→BUSY 链路升为头号嫌疑。黑盒代理保持暂停，等 B 结果再定。
- **实验 B 主代理亲自执行（用户 2026-08-28 定：不派子代理、非测试）**：① project_h→project_i 全量复制（2433 文件），RTL 仅 adc_ringbuf_top.v 改 25+/6−（git diff 证明，其余 5 文件 SHA 一致）；② 接口时序仿真 `.tmp_sim/busy/`（逐字提取补丁后 FSM + AD7606C 模型，100MHz）**PASS**：CONV 40ns / BUSY 650ns / t_D_BSY=39ns≥25ns / RD 50-10ns / 帧 1–5 全 8 通道=理想值；③ 构建走 Vivado batch 直驱（`.tmp_agent_tools/build_i.tcl`，绕 MCP 阶段机——新 session 只能从 PLATFORM_DESIGN 起步，重走平台流程不必要）；④ 部署用 MCP ps_* 批次（`.tmp_agent_tools/batch_deploy2.json`，12 步全 SUCCEEDED）；⑤ 采集用 pyserial 直连（`.tmp_agent_tools/u1_capture.py`）；⑥ 分析 `.tmp_agent_tools/a2_analyze2.py`（int16 有符号）→ project_i/evidence/。
- **★ 实验 B 结果（2026-08-28 晚，全链成功）**：① **FSCAL rate=1999 cycles=50000 dwptr=2000** → BUSY 电平等待下帧率精确 2000Hz，**本板 BUSY 链路确认工作**（断线/卡死则 WPTR 冻结 rate=0）；② 上传 64202 字节=完整 2000 帧零丢，sum16=0x4c50 通过；③ 有符号解析：活跃=**CH6**（std=3108，余 7 通道 <27 LSB 近 DC，与白盒一致）、**频率网格 LS=9.9650Hz**（白盒 9.96496 完全一致；FFT 峰 10.000Hz；误差 vs 10Hz=0.35%≤1%）、幅度 1.3435V、Vpp 2.6868V（白盒 1.34/2.68 一致）。④ 结论：**固定 6µs→BUSY 电平等待的单变量替换不影响采集**——BUSY 触发模式在本板被证明可用；黑盒 v14 失败与 BUSY 链路/触发方式无关 → 嫌疑收窄至黑盒数据通路（12-bank XPM/12×AXI BRAM 控制器/其固件上传，从未独立验证）或 CONV 保持高/RESET 脉冲/FR_D/9RD/50MHz 细节。⑤ 会话已关闭（干净状态留黑盒）；证据 project_i/evidence/（b12_a2_expB_data_signed.csv、b12_a2_expB_waveforms_8ch.png、b12_a2_expB_measurement.json）、原始捕获 .tmp_agent_tools/b12_a2_expB_uart.txt。⑥ 下一步（待用户指示）：回黑盒——先 big_seen 一读定案（=1→数据通路；=0→采集侧单变量改 CONV/RESET/FR_D/9RD）。
- **★ 黑盒失败步骤定位（主代理亲自执行，2026-08-29 早）**：黑盒读回始终"每个地址都返回 word0"的**根因 = `rtl/adc_top.v` 第 136 行读地址切片错误**：`addr_a = bram_addr_a_w[g][10:0]`。AXI BRAM 控制器（SINGLE_PORT_BRAM=1）在其 13 位 `bram_addr_a` 总线上**把字地址放在 [12:2]、[1:0]=00（字节道）**；取 [10:0] 保住了两个 0 字节道位、丢掉了字地址高 2 位 → XPM 读地址 = 字地址×4（且 ≥512 回绕）。于是每次 AXI 读都命中 4× 目标字：v14 系 = 帧 w 的 word0={CH2,CH1}（→"V1/V2 交替 + 静态窗口 + CH6 永不可见"）；v15j = S_WR0 常量 0x11112222（→"处处 0x11112222"）。证据链（硬件级）：v15k 地址标记版 mrd 全 12 bank → 读字 w 恒得位置 4w 的内容（A|4w）；v15n = v15j 常量 + 单行修复 → mrd 精确轮转 1111/3333/5555/7777、word2047、跨 512 边界全对（首扫 BANK10/11 为 0 仅是环形尚未写到——0x44000000/0x46000000=bank10/11，非缺陷）。写通路、控制器、BD、固件全部证明完好。变体 v15k/v15n/v15p 在 `D:\_b12_a2_external\agent3_20260825\`，脚本 `.tmp_agent_tools/build_bb_v15*.tcl`、`sweep_v15*.tcl`、`inspect_netlist*.tcl`（不入库）。
- **★ 黑盒修复后真实采集闭环（v15p，2026-08-29 早）**：v15p = 黑盒 v15b（真实采集 {s1,s0}…）+ 单行修复 [12:2]。MCP 全 10 步部署 SUCCEEDED → 黑盒固件 UPLOAD：A2_PASS、2000 帧、SUM=0x38931E97（低 16 位与本地重算一致）。分析：**活跃=CH6（std 3116，±4400 LSB）**，余 7 通道 <1 LSB（ch5 有 13.5 LSB 轻微串扰）；外部独立工具对账：**通道=丝印 CH6、频率 9.965Hz（10Hz 误差 0.35%）、Vpp 8826 LSB = 2.6935V（白盒 2.6868V）**——黑盒设计与白盒盲测结论完全一致。证据 `.tmp_agent_tools/v15p_evidence/`（measurement.json + 8 通道波形 PNG）、`v15p_ring.csv`、`bb_uart.txt`。板上现为 v15p。黑盒主项目尚未合入修复（等用户指示）。
- **★ 黑盒按 B 模式重开（2026-08-29，用户拍板「从公开契约重新实现」）**：全新无记忆子代理 f146433c（隔离区 `D:\_b12_a2_external\agent3_final_20260829\`，全新空目录；仅公开契约白名单 = 需求 v2.2 + 板卡包 + AD7606C_WARE 例程 + 泛化 Skill（含新合并的自验证纪律）+ 公开 MCP）。前置已做：黑盒基线 §7.5 修订（Skill 五文件新 SHA + 新隔离区记录）；主代理 MCP 会话已关、环境干净（hw_server 保留自启）。零轮询：等完成通知后按证据清单 + 外部工具复核审查。
- **★ 黑盒完成并验收（2026-08-29 下午）**：全新代理从零实现全流程（478 次 MCP 调用/63 工具；自建 RTL+BD+固件；自发落实新 Skill：POST 判定块带 BUILD/PL/PS、L1 TPG 8000/8000 MISMATCH=0 + REG_READBACK、L2 EVENT_COUNTERS（CONV=2000/BUSYF=2000/RD=16000/FRSTD=2000/RERR=0）、FSCAL 实测 2000.455Hz）→ UPLOAD 2000 帧 SUM=A919 → A2_PASS 恰一次 + DONE；evaluate_observation PASS。**盲测结论：CH6、9.9675Hz、Vpp 8820 LSB=2.6915V**；主代理独立复核（自建提取 + 外部工具）：CH6、9.9765Hz、2.6917V——三方一致（用户 10Hz 误差 ≤1%）。已知口径内 2 帧 UART 捕获损坏（F0264/F6DE，框架 P2）。目标 RUNNING、无 EDA 残留。证据 `agent3_final_20260829/project8_dep/evidence/`。**已提交推送：commit 806c065（2ddf028..806c065 main→master，ls-remote 复核一致；经 127.0.0.1:7890 代理）**。
- **杂散产物注意**：仓库根出现未跟踪 `bitstream/`（system_top.bit 4045667B，白盒 MCP 操作默认路径写出的副本）——**不入库**，待用户确认后清理或忽略。

## F1. 事件动作链（收到子代理完成通知后，按此执行——不依赖 goal）

1. **修复轮完成通知** → 按 §G 口径审查 diff + 回归统计（passed ≥1393 且 failed=0，有变化须一一映射说明）→ 通过则提交推送 → 准备环境（无 EDA 残留、COM4 在位）→ 放行白盒 v2（send_message 给 40bcfd5c，放行消息已定稿：S0–S8 全流程，方案乙，证据 = 数据文件 + 8 通道原始值波形图 PNG + measurement.json + A2_PASS）。
2. **白盒 v2 完成通知** → 按 8 项证据清单审查 + `tools/scripts/b12_a2_external_verify.py` 独立重算复核 → 无大问题 → 冻结黑盒基线（§7 SHA 复核、改 FROZEN）→ 准备环境 → 派发 A2 黑盒（照 A1 模式：隔离区 `D:\_b12_a2_external\agent3_20260825\`、全新无记忆、仅公开契约）；有问题保守处理（影响黑盒的修掉再开黑盒）。
3. **黑盒完成通知** → 证据审查 → 终版对账汇报给用户（通道/频率/Vpp 对账表 + solid 证据）。
4. **任一步遇重大问题** → 停止，向用户汇报并等待指示。

## G. 修复轮审查预期口径（HEAD 基线已建，2026-08-25）

- **D-E**：P6 gate（execution_gate.py L51-55）已有 `resolved_by_recovery` 逃生口；堵点在 recovery 的 P1（活 worker 拒绝）排在解析之前。预期修复 = 活 worker 拒绝仅限**非终结态操作**；终结未解析（OUTCOME_UNKNOWN/INTERRUPTED/TIMED_OUT）允许只解析账本、不动进程（保住 B11 ⑥.1 安全不变式）。
- **D-A**：platform_add_ip（platform_atoms.py L273-311）set_property 后**无回读校验**，白盒实测 get_property 返回空串（配置未写进去）。预期 = 属性真实生效，或 set 后强制回读、失败即报错——禁止静默 SUCCEEDED。
- **D-B**：ps_add_sources 等 schema 未声明参数 → SDK TypeError → OUTCOME_UNKNOWN。预期 = 正确接受或稳定 INVALID_ARGUMENT（顶层 ErrorCode + reason_code），绝不 TypeError。
- **D-C**：MAKE_FALLBACK 只回传单行（domain_runner.py）。预期 = 完整 make/编译器输出（超长截断需注明总长）。
- **D1**：准入门 P2/P3/P5 进程身份校验链。预期 = 陈旧活 worker 正确接管，或稳定可恢复错误，不得死锁。
