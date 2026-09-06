# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

AI Agent（Claude Code）驱动的 Zynq-7020（ALINX AX7020，xc7z020clg400-2）FPGA 开发框架。Claude 通过 MCP Server 操作 Vivado/XSim/Vitis 等 EDA 工具，按「三域四层 + Brick」计划增量构建。

- 冻结顶层架构：[docs/architecture_ai_zynq7020.md](docs/architecture_ai_zynq7020.md) v2.3.1
- Brick 状态索引：[docs/brick_development_plan.md](docs/brick_development_plan.md)（B00–B09 COMPLETE；B09 公开 MCP 纯黑盒验收 PASS（O7 R3，2026-08-13），契约勘误已关闭；B10/O8 冻结包已交付（2026-08-14，用户确认 GPIO v1 稳定基线）；B11 ✅ **COMPLETE（2026-08-16）**：泛化框架黑盒验证——Skill/MCP 去 GPIO 化 + 6-LED 考题，全六阶段闭环（输入冻结见 [B11_phase4_blackbox_basis.md](docs/development/tests/B11_phase4_blackbox_basis.md)）；B12 ✅ **完成（2026-08-29）**：数据采集链路（AD7606C-16）——A1 DMA 环回白盒+黑盒 PASS、A2 白盒真板实测成功（盲测通道已识别）+ 黑盒（B 模式）从公开契约独立复现全流程并真实验收 PASS（CH6、9.9765Hz、Vpp 8820 LSB，三方对账一致）；**B13 ✅ P4 完成（2026-09-05）**：白盒 PASS（F1–F8 发现）+ 黑盒 PASS（5 缺陷修复、契约 v0.5 全判据机读通过、2.087MB/s、三档 ±0.5%、L2 1:1、verify 12/12）→ 框架升级修复轮#1–#10（M1 环合法化/M2 平台原子/M3 确定性/M4 元数据/M5 幂等/Skill 四补/黑白盒反馈/摘要覆盖/ADDRESSING 注入）已合入 master；P4b 暴露式白盒复测完成（6/8 gate，F-25/F-19 待裁定）；Skill 增附录 §14 工程层正确姿势库 + §15 写前查询纪律（2026-09-06，机械门禁 14/14）；修复轮 #11 立项（白盒报告1 产品缺陷：F-25/F-19，首要，**已实施八门禁全 PASS**）见 [B13_fix_round_11_plan.md](docs/development/tests/B13_fix_round_11_plan.md)；修复轮 #12 立项（报告1 框架发现 8 项 + 效率吸收：ps_bsp_grep + 响应附注，**已全部实施并入 master（baece1c/0923257），工具 109→111，回归 1549 passed/0 failed**）见 [B13_fix_round_12_plan.md](docs/development/tests/B13_fix_round_12_plan.md)；验证方法论 v2（L0–L3 + 行为偏离审计，[validation_methodology.md](docs/development/validation_methodology.md)）+ 偏离审计器 tools/audit/bypass_audit.py；里程碑 [B13_P4_MILESTONE.md](docs/development/tests/B13_P4_MILESTONE.md)；MCP 109 工具（11 control + 98 domain）。B10 发布清单：[docs/development/mcp/B10_freeze_manifest.md](docs/development/mcp/B10_freeze_manifest.md)；B11 规划：[docs/development/mcp/B11_plan.md](docs/development/mcp/B11_plan.md)）
- Execution Observation：O1–O6 FROZEN，O7 R3 PASS，O8 冻结包已交付（2026-08-14）
- 根目录是新的 core Git 仓库（分支 main → 远端 master，925 个文件）：基线 commit `4e0d148`，tag `o7r3-baseline-20260813` 锁定 O7 R3 基线；远端 origin = https://github.com/zdx8637-gitdog/Xilinx_Vivado_MCP（旧内容已按授权覆盖替换，原旧远程 HEAD `59f2abb` 已记录）。`Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、`zynq_platforms/` 三个旧仓库为 legacy/已出范围（保留在磁盘、各自独立且已停更的 Git 历史，不被新仓库跟踪）。**B13 升级分支 framework-iteration 已 fast-forward 合入 master（2026-09-05，HEAD `2f72394`）。**
- 会话纪律速查（上下文压缩后必读）：[docs/development/B12_a2_working_discipline.md](docs/development/B12_a2_working_discipline.md)——零轮询/串行执行/盲测保密/缺陷口径/当前状态
- **验证方法论 v2（长期纪律，白盒/黑盒任务书设计必读）**：[docs/development/validation_methodology.md](docs/development/validation_methodology.md)——L0 测契约/L1 测功能/L2 测状态（L2-A 规范 + L2-B 扰动）/L3 伪黑盒/行为偏离审计（六型 + A–F 分类）/专家帮助率/黑盒切片 + LESSONS 回流（**回流泛化审查：项目特化不进 Skill，留项目文档**）；偏离审计器 `tools/audit/bypass_audit.py`
- **子代理通讯机制（长期纪律，跨所有 Brick 必读）**：[docs/development/subagent_communication_rules.md](docs/development/subagent_communication_rules.md)——send_message 排队语义 / 方向级指令「中断+合并投递」/ 发出后必须跟进确认（不能发了就不管）/ ask_user_question 人类转述 / 队列不可删除与跳过规则
- **项目外证据工作区（只读归档）**：`D:\_b13_external\agent1_20260829\`（P0–P2 白盒参照）、`agent1_p4_20260904\`（P4 白盒，evidence/FINDINGS.md）、`agent2_p4_20260904\`（P4 黑盒，FINAL_REPORT/LESSONS_LEARNED/acceptance_summary）；测试智能体在这些项目外工作区运行，**不加载本文件**——其规范由各自 WB/AGENT2_PROMPT + 冻结基线文档承载
- 下面「AI Agent 驱动 Zynq-7020 项目规则」是冻结的工作纪律，任何实现/汇报必须遵守。

## ⛔ 泛化红线（每次上下文压缩恢复后，动手改 Skill/MCP 前第一自查）

`skills/zynq_dev/` 与 `mcps/` 是**面向任意 Zynq 工程的通用框架资产**。上下文丢失后极易被当前 Brick 的项目实例带偏而特化——因此：

1. **项目特化禁入 Skill/MCP**：外设料号（如某 ADC 型号）、板卡型号、Brick 名、项目上位机口径、版本串约定、项目工具名——一律只进项目文档（`docs/development/tests/<brick>_*`）或项目 PROMPT，写入 Skill/MCP 视为违规。
2. **回流先过泛化滤网**：从当前项目学到的经验要进 Skill 前，先把项目名词抽象成通用模式（对照 appendix_mechanics §13：左侧是通用表述，右侧是抽象前禁止形态）；拿不准就放项目文档，不放进框架。
3. **机器强制**：`test_o6_skill_contract.py::test_skill_mechanical_gate_zero_current_project_terms` 会扫描 Skill 全文件的项目特化词——加词即门禁失败。白名单例外必须显式论证（如「上位机」= 通用需求分工字段）。
4. 修复轮改动 Skill 时，提交前跑 `python -m pytest mcps/zynq_mcp/tests/test_o6_skill_contract.py -q`。

## 🚫 推理反犹豫纪律（防 wait 循环与上下文污染）

**严禁在推理过程中重复使用犹豫词（wait / hmm / hold on / 再想想 等）循环兜圈**
——每次犹豫循环都在烧上下文，是污染的头号来源。信息不足时立即执行三动作：

1. **列「缺失的关键证据」清单**：缺什么、去哪拿（文件路径 / 工具 / 命令）；
2. **给一种最高概率假设**：只给一种，不并列多假设自我辩论；
3. **多执行测试作证**：用测试/实验证明或证伪该假设；证伪则更新假设再测，
   禁止无测试的多轮自我否定。

停不下来的判断标准：同一问题连续两轮没有新增证据且没有新测试 → 必须按上面
三动作落地为清单+假设+测试，而不是继续想。

## 🤖 子代理等待纪律（goal 轮与子代理并存时必读）

**一旦派发子代理（subagent / subagent_fork），后续 goal 自动续跑轮次只做一件
事：等待子代理答复。** 禁止在 goal 轮里轮询子代理进度——list_agents、
进度文件、git status "推测是否开工" 全部禁止：goal 轮节奏远快于子代理的
真实进展，轮询必然误判"卡死/无产出"并错误打断正在工作的子代理（修复轮 #12
的实证教训：子代理读码规划被误判停滞而中断，其实它完成了全量定位快照）。

- 子代理完成或被中断时，运行时会主动通知——届时再处理其答复；
- 需要追加指令/决策时用 `send_message`（排队语义：等其当前回合结束后处理）；
- 子代理报告了真实阻塞（明确说出"卡住/无法继续"）才允许介入；
- 若确有硬超时必须打断，先经 `send_message` 追问一次，无响应才
  `interrupt_agent`——不得用"没看到文件写入"作为打断依据。

## 常用命令

```bash
# 主测试套件（必须从项目根目录运行，勿 cd 进 mcps/）
python -m pytest mcps

# 非硬件回归（跳过需 EDA 工具或硬件的测试）：1499 passed / 1 skipped / 43 deselected / 0 failed（约 227 秒；1 skipped 为 B02 POSIX-only；43 deselected = 39 host_live + 4 device_live）
python -m pytest mcps -m "not host_live and not device_live"

# 单个测试
python -m pytest mcps/zynq_mcp/tests/test_r1_gate.py -k <test_name>

# 机械门禁用的收集统计（当前 1543 collected）
python -m pytest mcps --collect-only -q

# 列出所有 pytest marker
python -m pytest mcps --markers

# 按 marker 运行（host_live 共 39 个 = 需 Vivado/XSim；device_live 共 4 个 = 需 USB-UART）
python -m pytest mcps -m "host_live"
```

> ⚠️ **不要 `cd mcps && python -m pytest`**：约 20 个 MCP SDK contract 测试会派生 `python -m mcps.zynq_mcp.server` 子进程，CWD 为 `mcps/` 时子进程无法 import `mcps`（ModuleNotFoundError）；从项目根目录运行则全部通过。

环境：Windows + Python 3.12.9，依赖 `mcp==1.28.1`、`pytest`、`pytest-asyncio`。测试 marker：`host_live`（需真实 EDA 工具）、`device_live`（需真实 USB-UART）。`mcps/conftest.py` 注入 `ZYNQ_BOARD_PROFILE_DIRS` 指向 `mcps/common/tests/fixtures`；`mcps/zynq_mcp/tests/conftest.py` 注入 `ZYNQ_RUNTIME_ROOT` 指向临时目录。

## 架构：三域四层

1. **Workflow 层** — Claude Code + Skills 编排，无领域知识（Skills: `fpga-develop`、`fpga-verify`，位于 `Xilinx_Vivado_MCP/skills/`）
2. **Domain Skill 层** — PS / Platform / PL 领域知识（裸机、AXI、RTL、时序…）
3. **MCP 层** — 原子硬件操作 API（query / set / command）
4. **EDA Process 层** — Vivado / XSCT / XSim 进程管理，不含 FPGA 业务逻辑

部署演进：B02 曾用三个独立 MCP（`pl_mcp` / `platform_mcp` / `ps_mcp`），现已合并为**唯一** `mcps/zynq_mcp/` Server（B09 公开 MCP 黑盒验收 PASS），Platform/PL/PS 变为内部 domains。根目录 `.mcp.json` 当前为空注册 `{"mcpServers": {}}`（SHA256=d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02，与 O1–O6 冻结记录一致）；最终「仅 `zynq` 一个入口」的注册形态待后续决策（B10/O8 发布清单已知限制 ②）。

## 关键目录

| 目录 | 说明 |
|------|------|
| `Xilinx_Vivado_MCP/` | legacy/已出范围（独立 Git 仓库，已停更，不被新 core 仓库跟踪）。`server.py` 入口；`config.py` 集中版本/路径/环境；`vivado_process.py`/`xsim_process.py` 进程层；`vivado_tools.py`/`tcl_templates.py` 工具封装；`skills/` 含 `fpga-develop`/`fpga-verify`。注意 `mcps/zynq_mcp/adapters/vivado_adapter.py` 的 `build_default_params()` 仍保留兼容引用，但公开注册的工具已不依赖旧 server（见 `mcps/zynq_mcp/domains/pl/pl_bridge_tools.py` 顶部注释） |
| `Xilinx_Vitis_MCP/` | legacy/已出范围（独立 Git 仓库，Vitis MCP 骨架，已停更，不被新 core 仓库跟踪） |
| `zynq_platforms/` | legacy/已出范围（独立 Git 仓库，已停更，不被新 core 仓库跟踪）。含 `ax7020_base/` block design、构建输出、Vitis workspace、Tcl 脚本 |
| `mcps/common/` | 公共契约：`board_package.py`（板卡配置包）、`board_profile.py`、`project_lock.py`、`revision.py`、`artifact_schema.py`、`env_probe.py`、`error_codes.py`、`tool_response.py`、`api_category.py`、`control_api.py`、`jtag_lock.py`、`context.py` |
| `mcps/zynq_mcp/` | 唯一 MCP（共 109 工具：11 control + 98 domain；B13 修复轮#1–#10 已合入 master——#6 新增 `platform_package_user_ip`/`platform_set_bd_object_property`、#1 `workflow_rollback`/`workflow_resume_from`、#8 摘要覆盖/仓库注册/ps_mem_read 修复、#10 ADDRESSING 注入，详见 [B13_P4_MILESTONE.md](docs/development/tests/B13_P4_MILESTONE.md)）：`control/`（execution_ledger、single_worker、execution_gate、instance_guard、process_guard、session、recovery、operation_service、operation_registry、capabilities、context、timeout_config、workspace、workflow）、`adapters/`（vivado/xsct/jtag/uart）、`domains/`（pl/platform/ps）。B10 已知限制①（计数漂移）已由 B11 阶段②关闭：`DOMAIN_APIS_IMPLEMENTED` 机械派生 |
| `mcps/{pl_mcp,platform_mcp,ps_mcp}/` | B02 过渡遗留（最终被 zynq_mcp 取代） |
| `boards/ALINX_AX7020_v1.0/` | Board Configuration Package — 板卡唯一数据源（README.md、board.xdc、board_profile JSON、package_manifest、ps7_preset.tcl、SOURCES.md） |
| `skills/zynq_dev/` | 泛化框架 Skill（B11 阶段①，零项目外设字样：SKILL.md + phases/0–8 + appendix_mechanics——§12 AXI 握手缺陷模式库、**§13 已知问题与处理建议（真板实证通用件，项目特化禁入）**）；旧 GPIO Skill 已归档 `docs/development/skill/archive/zynq_gpio_v1/`（方案 A，SHA256 记录） |
| `docs/` | 冻结架构 + `development/`（G0–G12 历史材料；按 Brick 记录的 `mcp/`、`tests/`、`skill/`；**长期纪律：validation_methodology.md 验证方法论 v2、subagent_communication_rules.md；B13-P4：B13_requirement_draft v0.5 契约 + P4 白盒/黑盒基线 + B13_P4_MILESTONE**）+ `boardinformation/`（ALINX 官方 6 本 PDF 教程）+ `reference/`（golden_baseline、deployment） |
| `tools/scripts/` | PowerShell 工具（USB/驱动/UART 扫描、CH340/CP210x 驱动安装） |
| `tools/audit/` | 库存扫描脚本（`b00_tool_inventory.py`、`b01_consistency_scan.py`、`scan_absolute_paths.py`）+ **`bypass_audit.py` 行为偏离审计器（白盒报告必附，用法见 validation_methodology §二）** |
| `vendor/drivers/` | 厂商 USB-UART 驱动（CP210x、FTDI） |
| `hello_fpga/` | 纯 PL Breath LED 完整参考项目（rtl/sim/constraints/vivado_project/scripts） |
| `g9_hw_test/` | PL 硬件闭环验证 |
| `embedded_projects/` | PS bare-metal ARM 参考（`ps_led_test/`） |
| `validation_projects/` | Golden（breath_led）+ 11 故障注入设计 + Agent2 黑盒验收（AGENT1_PROMPT.md、AGENT2_PROMPT.md） |
| `.zynq_runtime/` | zynq_mcp 运行期状态目录（Operation Ledger、Artifact、Session 持久化；证据与运行态，不入库） |
| `.o6_runtime*/` | O6 阶段证据与运行态目录（不入库） |
| `workspaces/` | 证据与运行态目录（不入库） |
| `.claude/` | 项目级 Claude Code 配置（`settings.json` 启用全部 MCP Server、`settings.local.json` 本地权限） |

## 环境

- Vivado 2023.1 安装于 `D:\Xilinx\Vivado\2023.1`（可用 `VIVADO_ROOT` / `VIVADO_EXEC` 环境变量覆盖）
- MCP Server 注册见根目录 `.mcp.json`（当前为空注册 `{"mcpServers": {}}`，SHA256=d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02；最终「仅 `zynq` 一个入口」的注册形态待后续决策，B10/O8 发布清单已知限制 ②）
- `.claude/settings.json` 设 `enableAllProjectMcpServers: true` 使项目 MCP Server 自动启动
- 测试运行时 `ZYNQ_BOARD_PROFILE_DIRS` 和 `ZYNQ_RUNTIME_ROOT` 由 `conftest.py` 注入，生产环境不设
- **测试必须从项目根目录运行**（`python -m pytest mcps`）：若 `cd mcps && python -m pytest`，约 20 个 MCP SDK contract 测试会因派生 `python -m mcps.zynq_mcp.server` 子进程在 CWD `mcps/` 下无法 import `mcps` 而失败

---

# AI Agent 驱动 Zynq-7020 项目规则

## 角色与边界

- Agent1 是长期上下文的白盒实现 Agent，负责规划、实现、自测、文档和交接。
- Agent2 是全新上下文的黑盒验收 Agent，只按公开契约验证，不依赖 Agent1 的实现说明。
- 当前提示词、Brick 规划、冻结文档和交接文档共同构成工作范围；发生冲突时先报告，不得自行改变架构原则。
- 未经明确授权，不得自行冻结 Brick、进入下一 Brick/子步骤、调用 Agent2 或修改冻结资产。
- 完成当前范围后停止并等待审核。

## 冻结与变更纪律

- 修改前必须阅读当前 Brick 规划、测试规划、上一步交接、审核意见及相关生产代码和测试。
- 默认保留 B00–B03 及后续已冻结结论。发现冻结内容确有缺陷时，标记为 Erratum，说明影响和最小修改范围，不得顺手重构。
- 工作区可能包含用户修改和多个子仓库。不得清理、移动、覆盖或提交无关文件。
- 不得通过删除、合并、重命名有效测试来换取通过。
- 必须记录修改前后的测试文件、测试函数、collected、passed、skipped、xfail 数量。
- 完整回归数量下降默认视为阻塞；确需删除测试时，必须先给出旧测试到替代测试的一一映射及等价性证明。

## 实现原则

- 不要只针对上一条审核意见做表面补丁。修复前检查同一状态机、生命周期及相邻错误路径是否存在相同问题。
- 优先复用已经验证的公共契约、锁、Revision、Artifact 和错误模型，避免复制出第二套语义。
- 生产逻辑必须 fail-closed：无法确认真实状态时返回明确错误，不得推断成功、运行中、已释放或已恢复。
- 进程、Operation、锁、Artifact 和 Session 的状态必须来自真实持久化证据，不得仅依赖 PID、缓存或调用成功。
- 错误响应必须包含稳定的顶层 ErrorCode；需要细分时使用 `error.details.reason_code`。
- 所有等待、外部进程和资源获取必须有有界超时及明确恢复建议。

## 禁止伪测试

以下行为一律禁止：

- 空测试、`pass` 占位、`except Exception: pass`。
- 捕获异常后不验证异常类型、ErrorCode 和 reason_code。
- 使用 `assert result in ("success", "error")` 等无法证明行为的断言。
- 只证明函数可调用，却声称行为已验证。
- 使用不存在的 operation_id 冒充真实 Operation 正向测试。
- 两个 `subprocess.run` 顺序执行却声称并发。
- 用手工构造最终结果绕过生产入口。
- 直接调用错误构造器冒充生产路径验证。
- 仅有 mock 验证却声称真实 MCP、真实进程或真实硬件通过。
- 使用固定 `sleep` 碰运气代替 READY、Barrier、Event 或其他确定性同步。
- 通过 retry、skip、xfail、放宽断言或减少测试隐藏竞态。

## 声明与证据必须对应

- 声称"跨进程"：至少两个真实 OS 进程参与。
- 声称"并发"：必须证明两个操作存在时间重叠，并使用确定性同步。
- 声称"真实 MCP"：必须启动真实 server，并通过 MCP SDK `ClientSession` 调用。
- 声称"真实 Operation"：必须创建持久化 operation_id，并验证其状态和重启恢复。
- 声称"崩溃恢复"：资源持有进程必须被异常终止，不能先主动释放资源。
- 声称"PID 已清理"：记录真实 PID，验证终止前 alive、终止后 not alive。
- 声称"原子"：必须包含竞争、故障注入和最终状态验证。
- 声称"所有 API"：逐项列出真实调用，数量必须与 capability 表一致。
- 声称"全量回归"：必须报告 pytest 的 collected/passed/skipped/xfail 原始统计。

## 证据等级

每项能力必须标记为以下一种，不得越级描述：

- `IMPLEMENTED_AND_TESTED`：生产入口已实现，且有对应有效测试。
- `IMPLEMENTED_NOT_TESTED`：生产实现存在，但没有有效测试。
- `TEST_HELPER_ONLY`：只存在于测试辅助代码。
- `MOCK_ONLY`：仅完成 mock 验证。
- `STATIC_REVIEW_ONLY`：只完成静态检查。
- `DEFERRED`：明确延后到指定阶段。
- `NOT_IMPLEMENTED`：尚未实现。

无法确认时写"未确认"，不得推断为 PASS。

## Adapter 真实工具 Gate（产品级要求）

每个 Adapter（进程管理）模块交付时，必须通过真实工具 host_live 测试——不是 skip，不是 mock，是真实进程真实命令：

- `adapters/xsct/`：≥1 个 host_live test 真实启动 XSDB/XSCT 并验证 eval() 输出正确
- `adapters/uart/`：≥1 个 host_live test 真实枚举串口（list_ports 非空即 PASS）
- `adapters/vivado/`：保持现有 Vivado host_live 标准

**未通过真实工具 gate 的 Adapter，不能向 Domain 层声称 IMPLEMENTED_AND_TESTED。** Domain 模块只能用 Mock 做单元测试，但不能声称 Adapter 已验证。所有基于 FakeBridge 的测试证据等级上限为 `MOCK_ONLY`，真实工具通过后才能升级为 `IMPLEMENTED_AND_TESTED`。

Doma in 函数的集成验证（MCP SDK contract tests）也需要 host_live 标记验证真实路径，但 Domain 测试可以 skip 真实工具（因为 Mock 已验证逻辑正确性）。Adapter 没有这个豁免——它就是把真实工具的输出翻译正确，不测真实工具就没有存在的意义。

## 提交前机械门禁

提交汇报前必须机械执行并核对：

1. 测试文件和测试函数数量。
2. `pytest --collect-only` 的 collected 数量。
3. skip、xfail、空 pass、TODO、NotImplementedError 扫描。
4. broad exception 吞噬扫描。
5. 修改、删除、重命名文件清单。
6. 修改前后测试覆盖映射。
7. 冻结资产 SHA256 校验。
8. `.mcp.json` 变更检查。
9. 生产入口与测试调用入口对应检查。
10. 规划、测试文档、代码和汇报中的数量一致性检查。

所有数字必须来自命令输出，不得估算。表格各行相加不等于 Total 时，停止提交并先修正。

## 自我反证审核

汇报前主动尝试推翻自己的结论：

- 声称并发时，检查是否其实顺序执行。
- 声称崩溃时，检查是否提前释放了资源。
- 声称所有 API 时，检查是否遗漏、只测错误路径或只验证可调用。
- 声称无回归时，检查测试是否减少或被替换。
- 声称 fail-closed 时，检查异常是否被吞掉或转换成成功状态。
- 声称资源已释放时，检查 Handle、PID、锁和后台 Task 是否仍存在。
- 声称状态真实时，检查是否可能出现 unknown 却报告 running/success。
- 声称跨会话恢复时，检查状态是否真的来自 Ledger/Artifact，而非内存缓存。

发现反例时必须修复或报告阻塞，不得继续声称完成。

## 风险分级与停止规则

- `P0`：可能破坏数据、破坏串行执行、误判任务状态、错误恢复或造成不可逆操作。必须阻塞当前步骤。
- `P1`：常见异常或恢复路径行为错误。当前步骤原则上必须处理。
- `P2`：极低概率二次异常、代码卫生或不影响当前验收目标的技术债。记录后可延后，不得无限阻塞 Brick。

只要存在以下任一情况，不得写 `FINAL`、`COMPLETE` 或 `FROZEN`：

- P0/P1 未关闭。
- 测试数量无解释下降。
- 存在空 pass、未授权 skip/xfail。
- 声明与生产入口或测试证据不一致。
- 机械统计或文档数字不一致。
- 完整回归不稳定。
- 冻结资产发生未授权变化。
- 存在未解释的失败、竞态或残留进程。

P2 必须进入技术债清单，但不得为了追求理论完美反复扩大当前步骤。

## 汇报最低内容

- 当前状态及是否进入下一步骤。
- 修改文件清单。
- 本轮契约和实际行为对照。
- 生产入口证据与对应测试名称。
- 测试机械统计和完整回归结果。
- 新增、保留、替换、删除测试的数量及映射。
- 冻结资产不变证明。
- 已实现、未测试、延后和未实现能力的明确区分。
- 遗留 P2 技术债。
- 明确声明没有自行冻结或越级进入下一步骤。

报告应简洁、可机械复核。不要用篇幅代替证据。
