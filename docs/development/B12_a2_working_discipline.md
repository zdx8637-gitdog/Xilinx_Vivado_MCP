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
- **黑盒（431fa5c5）**：🔄 **已恢复重跑（2026-08-27，公开事实勘误后）**。勘误三处（用户授权，commit 595e719）：①引脚 JSON FR_D 方向 out→in（FRSTDATA 是 ADC 输出）；②事实卡补录 BUSY 下降沿边沿触发规范时序（t_D_BSY≥25ns、t_CONV 0.5–0.65µs、CONVST≥22ns 保持高式、RESET 高有效 10ms、凌智 MODE0 参照模式）；③参照例程模式说明（随②写入）。黑盒基线 §7 SHA 已同步更新。**黑盒自行完成 4 代修复（v5 BUSY 边沿方向、v6 规范模式、v7 通道重复、v8 FRSTDATA 门控移除），2000 帧校验通过 + A2_PASS，但其 v8 仍读不到信号并称"8 输入无信号"。主代理 A/B 复测（2026-08-27 重新部署白盒设计）：CH6 正弦仍在（std≈3095）→ 信号源正常、黑盒 v8 仍有隐藏缺陷（其奇/偶通道值过于均匀，疑通道指针未步进）。已发中性提示（不泄露答案）：奇偶均匀疑点 + FRSTDATA 步进校验法。
- 外部对账工具已提交推送（commit 50c7ca7）；需求 v2 已提交推送（commit c18b89c）；记忆文档 fea63fb、授权记录 5af9c2b、黑盒基线草案 4732cd6/c5294c5 均已推送。**需求已演进至 v2.2**（上传纪律 + **快照冻结解耦硬性要求**——混帧截断/11Hz 误读的根因，commit b974997）。
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
