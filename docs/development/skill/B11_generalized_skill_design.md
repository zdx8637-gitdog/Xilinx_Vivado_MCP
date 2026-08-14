# B11 泛化 Skill 决策框架设计（DRAFT）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 16:26 +08:00）
> 状态：**DRAFT — 待用户审核。本文档是设计提案，不代表切片已立项、不冻结任何资产、不修改任何生产代码 / 测试 / skills / boards。**
> 性质：为「下一 Brick（数据采集切片）」立项决策提供 Skill 层面的泛化设计。配套提案见 `docs/development/mcp/B11_data_acquisition_proposal.md`（两份文档交叉引用）。
> 对照基线：`skills/zynq_gpio/`（B07 冻结，SHA256 见 `docs/development/mcp/B10_freeze_manifest.md` §3）；顶层架构 `docs/architecture_ai_zynq7020.md` v2.3.1（P1–P8）。

## 1. 背景与目的

当前唯一 Skill `skills/zynq_gpio/` 是 **GPIO 纵向切片的配方**（固定外设、固定步骤、固定产物路径）。B10 冻结后，下一切片是数据采集（PL AD 采集 → DMA → DDR3 → PS 读 DDR3 → UART 上行），它包含自定义 RTL、AXI DMA、HP 口、中断、二进制流协议等 GPIO Skill 明确不支持的能力（`SKILL.md` §技能声明）。若继续按「每个切片写一份配方 Skill」扩展，Skill 将退化为项目仓库，违背其「泛化工具」定位。

本文档目的：
1. 诊断现有 GPIO Skill 中哪些是**可泛化的工程骨架**、哪些是**必须按域扩展的领域知识**（引用 phase 文件作证据）；
2. 提出一个**与具体外设无关的通用阶段框架**（需求解析 → 物理事实清单 → 带宽/资源预算 → 架构选型 → 方案提案 → 分域实现 → 一致性验证 → 部署观测 → 判定/恢复）；
3. 明确**分工边界**：现实层（板卡物理事实）归用户，工程层（架构/协议/参数/代码）归智能体，产品级取舍由智能体提案、用户拍板；
4. 定义 MCP 工具在该框架各阶段的角色，并把能力缺口与 `B11_data_acquisition_proposal.md` §5 的清单交叉引用。

**范围**：本文档只做设计（DRAFT），不实现、不修改 `skills/zynq_gpio/`。落地为可执行 Skill 需用户审核并批准切片立项后进行。

## 2. 现状诊断：GPIO Skill 是「配方」，不是「框架」

### 2.1 「配方」的证据（逐文件引用）

| 固化点 | 证据（文件 / 行） | 配方化表现 |
|---|---|---|
| 技能边界 = 固定外设 | `skills/zynq_gpio/SKILL.md` L7–8：「我**只**能做通过 AXI GPIO 控制 PL LED 的项目。我不支持：中断、DMA、自定义 RTL、DDR 共享、FreeRTOS/Linux、QSPI/SD 启动、ILA 调试」 | 能力声明绑定到单一外设，而非绑定到「流程」 |
| 需求模板 = 固定字段值 | `SKILL.md` L14–30：`pl_logic=无`、`dma=无`、`interrupts=无`、`addresses=AXI GPIO @ 0x41200000`、`clocks=FCLK0=50MHz` | 需求解析被写成 GPIO 示例值，换外设即失效 |
| 板卡维度固定 | `phases/0_board_profile.md` L6–8：「board_id 固定为 `ALINX_AX7020_v1.0`（当前唯一支持的板卡）；不需要 AI 选择板卡」 | 板卡选择环节不存在 |
| BD 拓扑固化为一键配方 | `phases/1_platform_design.md` L5–9：「使用快捷路径 `platform_generate {}`——一键生成 PS7 + SmartConnect + 4-bit AXI GPIO Block Design；GPIO 地址固定为 `0x41200000`；不需要 AI 选择 IP 或配置参数」 | 架构选型被完全旁路；`platform_generate {}` 是无参数快捷路径 |
| PL 工程路径/顶层/XDC 固化 | `phases/2_pl_build.md` L15–22：「top 固定为 `system_top`；PL 工程固定为 `<project_path>/vivado/gpio_pl`；XDC 放在 `<project_path>/xdc/gpio_led.xdc`；bitstream 固定为 `<project_path>/bitstream/gpio_led.bit`」；L34–42 XDC 内容固定 4 个 LED 引脚（J16/K16/M15/M14） | 产物命名与约束内容写死 |
| PS 软件固化为主程序模板 | `phases/3_ps_software.md` L17–76「GPIO 测试规范（C 代码必须满足）」：固定基地址 `0x41200000`、固定 UART 115200 8N1、固定循环/readback/PASS-FAIL 逻辑 | 软件结构绑定 GPIO 语义 |
| 观测语义绑定文本 marker | `phases/6_observation.md` L7–8：「判定规则固定，不需要 AI 判断」；L35–42 判定条件绑定 `GPIO_E2E_PASS`/`GPIO_E2E_FAIL` | PASS/FAIL 判定是文本 marker，非通用观测模型 |
| UART 参数固定 | `phases/5_deployment.md` L49：`ps_start_uart_capture` baudrate=115200 固定；`SKILL.md` L188「UART 仅 115200，波特率固定，变更需修改 C 源码」 | 链路配置写死 |

### 2.2 可泛化的资产（不随外设变化）

以下内容在 GPIO 流程中已被验证，且**与具体外设无关**，应提升为通用框架的骨架：

1. **阶段划分骨架**（Phase 0–7 的顺序与产物交接模式）：板卡验证 → 平台设计 → PL 构建 → PS 软件 → 一致性 → 部署 → 观测 → 恢复。任何 Zynq 纵向切片都符合「跨域产物串联 + Manifest 交接」的形状。
2. **长任务纪律**（`SKILL.md` §长任务与真实状态规则）：所有 command → `operation_id` → `wait_operation`/`get_operation_status`，以 Ledger 真实状态（`status_source/observed_state/vendor_status/current_step/recommended_action`）为准，外层超时 > 内层 + 30s，`wait_timed_out` 不视为失败。这是全框架通用的执行纪律。
3. **Manifest 一致性语义**（`phases/4_consistency.md` §7 条规则）：跨域 revision 一致、board_profile_sha256 一致、artifact 存在且 SHA256 匹配。规则内容与「GPIO」无关，是产物链通用契约。
4. **JTAG 部署 8 步序列**（`phases/5_deployment.md` L11、L72–93）：`select → halt → rst -system → ps7_init → fpga -f → loadhw → dow → con`，含「fpga 必须在 ps7_init 之后」「loadhw 注册 PL 内存映射」等硬性顺序知识。与具体设计无关。
5. **恢复分类与诊断 cascade**（`phases/7_debug_recovery.md`）：8 类错误（ENV/TOOL/PLATFORM/PL_BUILD/PS_BUILD/JTAG/UART/ARTIFACT_STALE）与「先诊断再恢复、服从 recommended_action、无逃生通道」原则，是通用诊断框架。
6. **公开边界硬门禁**（`SKILL.md` §公开边界）：不导入 MCP 内部模块、不自行启动 EDA、不手工发布 Manifest、不直接操作 Ledger/锁——这是框架级纪律，任何切片必须继承。

### 2.3 必须按域扩展的领域知识（框架不内置，按需挂载）

| 域 | 领域知识 | 说明 |
|---|---|---|
| Platform（通信拓扑） | 每类外设的 BD 拓扑与地址规划 | GPIO：PS7+AXI GPIO+SmartConnect，1 段地址；DMA：PS7(HP0/IRQ_F2P)+AXI DMA(S2MM)+SmartConnect+AXI INTC，寄存器段+数据通路；未来视频/HDMI：另类拓扑 |
| PL（FPGA 逻辑） | 每类设计的 RTL 形态、仿真策略、时序约束 | GPIO：无自定义 RTL；B11：图案发生器、ADC 控制器、跨时钟 FIFO、AXI-Stream 打包；未来：视频时序生成等 |
| PS（ARM 软件） | 每类外设的软件结构 | GPIO：寄存器 poke + readback；B11：XAxiDma 驱动 + cache 一致性（flush/invalidate）+ 流协议打包；未来：GEM/网络栈 |
| 观测 | 每类设计的 PASS/FAIL 语义 | GPIO：文本 marker；B11-B：二进制帧的序号连续性 + CRC；B11-C：数值容差断言 |

**结论**：GPIO Skill = 通用骨架 + GPIO 领域知识包。泛化改造 = 把骨架抽出来 + 为数据采集切片编写新的领域知识包，而不是复制一份 GPIO 配方改名。

## 3. 泛化框架：通用阶段定义

框架由 9 个阶段组成。每个阶段写明：输入、输出、智能体可自主决策的范围、用户必须提供的物理事实、失败恢复入口。框架不包含任何「GPIO/DMA/ADC 具体怎么做」——那是领域知识包的职责（§5）。

| 阶段 | 输入 | 输出 | 智能体自主决策范围 | 用户必须提供 | 失败恢复入口 |
|---|---|---|---|---|---|
| S0 需求解析 | 用户一句话需求 + 方向 | 结构化需求（功能/外设/观测/判定/上位机角色） | 把需求映射到切片框架与候选方案；识别缺失信息 | 需求本身（方向、目标、上位机分工） | 需求不完整 → 列出缺失项返回 S0 |
| S1 物理事实清单 | 需求 + 板卡配置包 | 物理事实表（型号/接口/引脚/电平/时钟，含「未确认」标注） | 从 board profile 提取板级事实并做交叉校验（如 UART 桥片型号与 VID/PID） | **ADC 型号、数据接口类型、分辨率、最大采样率、通道数、参考电压/量程、引脚分配**（现实层，框架不臆造） | 事实缺失 → 阻塞并列出必需项；查不到写「未确认」 |
| S2 带宽/资源预算 | 物理事实 + 需求指标 | 带宽预算表；PL 资源占用预估（对照 53,200 LUT / 106,400 FF / 140 BRAM36 / 220 DSP48E1）；DDR/HP 余量 | 全部换算与档位推导（如 UART 上行速率 → 最大采样率/帧率，见配套提案 §4） | 目标档位偏好（可选） | 预算超限 → 回 S3 降档，或提出物理路径变更（如换 Ethernet） |
| S3 架构选型 | 预算 + 板卡 | 拓扑（HP 口、DMA 模式、时钟域、FIFO 深度、中断 vs 轮询）、流协议草案、地址规划 | **全部工程决策**（对应架构 §4.3.4：Skill 决定「为什么这样连」） | 无（除非涉及板级改动，则回 S1） | 选型自检失败 → 回 S2 调整预算假设 |
| S4 方案提案 | 架构选型 | 给用户的取舍提案（采样率档位、压缩 vs 无损、中断 vs 轮询的实时性影响） | 提案（含利弊、判据线、推荐项） | **拍板产品级取舍**（只拍「档位/取舍」，不拍工程细节） | 用户否决 → 按反馈回 S2/S3 |
| S5 分域实现 | 方案（已批准） | Platform BD/XSA/Manifest → PL RTL/bitstream/Manifest → PS ELF/Manifest（全部自动发布） | 各域实现全部细节（RTL、驱动、参数、代码） | 无 | 沿用 Phase 7 错误分类；任一门禁失败回对应域重跑 |
| S6 一致性验证 | 三 Manifest + 产物 | `verify_consistency` 报告（all_passed/errors） | 执行校验、定位不匹配域 | 无 | 任一规则失败 → 回不匹配的域 |
| S7 部署观测 | Bitstream + ELF + 观测配置 | JTAG 部署完成 + 捕获数据 | 部署序列执行与观测配置（波特率、捕获窗口、marker/帧判定） | 无 | 部署错误分类（JTAG/UART/波特率偏差） |
| S8 判定/恢复 | 捕获数据 | 机读 PASS/FAIL + 证据归档 | 判定、报告、恢复决策 | 最终审核（是否冻结、是否进入下一轮） | 判定失败 → 按 Phase 7 诊断 cascade 或报告阻塞 |

**关键属性**：
- 阶段产物全部经 MCP 自动发布的 Manifest/Artifact 交接（S6 校验），不依赖智能体记忆；
- 每个阶段都有明确的失败恢复入口，不回溯重跑（对齐 P6「所有 Workflow 可恢复」）；
- S0–S4 是「设计面」（无 EDA 副作用），S5–S8 是「执行面」（全部走公开 MCP command 纪律）。

## 4. 分工边界（必须写入框架的契约）

| 层 | 归属 | 内容 | 框架内的位置 |
|---|---|---|---|
| 现实层 | **用户** | ADC 型号、数据接口类型、分辨率、最大采样率、通道数、参考电压/量程、**引脚分配**、板级电平方案（如非 3.3V 逻辑需电平转换）、板卡物理事实 | S1 输入（物理事实清单）；S4 拍板物理可行性 |
| 工程层 | **智能体** | 架构、协议、参数、代码、约束、构建、验证、判定 | S2–S8 全自主，用 MCP 工具验证（query/set/command） |
| 产品级取舍 | **智能体提案 → 用户拍板** | 成像档位、采样率 vs 帧率、压缩 vs 无损、中断 vs 轮询（若影响实时指标） | S4 方案提案 |
| 上位机（另一智能体） | 外部 | 流契约的另一端（接收、解析、成像/分析） | 本框架只交付**可机器验证的流契约**（见配套提案 §6），契约为两智能体共同冻结 |

原则：
- 智能体不得臆造物理事实（查不到写「未确认」）；用户不得被要求做工程决策（只拍「档位/取舍」）。
- 产品级取舍必须由智能体**提案**（含判据线与推荐项），用户**拍板**；智能体不得替用户定档，也不得把工程细节丢给用户。

## 5. 领域知识包的形态（框架的扩展点）

框架落地为 Skill 后，每个纵向切片 = 通用骨架 + 一个领域知识包。知识包按三域 + 观测四块组织，与现有 GPIO Skill 的 phase 结构对齐：

| 领域知识包 | 内容 | 数据采集切片（B11）需要哪些 | GPIO（现状，冻结不动） |
|---|---|---|---|
| Platform 包 | BD 拓扑模式、地址规划、时钟/复位/中断分配 | DMA+HP0 拓扑、S2MM、地址段规划、中断线分配 | 一键 `platform_generate {}` 配方 |
| PL 包 | RTL 形态、仿真策略、时序约束 | 图案发生器、ADC 控制器、跨时钟 FIFO、AXI-Stream 打包、input delay 约束 | 无自定义 RTL |
| PS 包 | 软件结构、驱动用法、cache 语义 | XAxiDma 用法、Xil_DCacheFlushRange/InvalidateRange、二进制流打包、CRC | 寄存器 poke + readback |
| 观测包 | PASS/FAIL 语义、判定工具组合 | 二进制帧完整性（序号+CRC）、数值容差断言 | 文本 marker `GPIO_E2E_*` |

知识包之间不得交叉写死路径/引脚/地址（那是配方化的根源）；通用骨架提供纪律（阶段、门禁、恢复），知识包提供判断（怎么连、怎么写、怎么算成功）。

## 6. MCP 工具在框架中的角色

### 6.1 原子分类与阶段映射

| 框架阶段 | 用到的原子类型 | 代表工具 | 角色 |
|---|---|---|---|
| S0/S1 | query（control） | `get_capabilities`、`get_execution_state` | 只读上下文，不产生副作用 |
| S2/S3 | query（domain） | `platform_list_ips`、`pl_query_*`、`ps_read_elf_info`、`ps_get_bsp_status` | 预算/选型的证据来源 |
| S4 | 无工具 | — | 纯提案（智能体输出，用户拍板） |
| S5 Platform | command/set | `platform_create_design`、`platform_add_ps7`、`platform_configure_ps7`、`platform_add_ip`、`platform_connect_*`、`platform_set_address`、`platform_validate`、`platform_generate_wrapper`、`platform_export_hardware/manifest` | BD 拓扑原子（set/command，不推进 stage 的原子） |
| S5 PL | command/query | `pl_create_project`、`pl_generate_target`、`pl_synthesize/place/route/analyze_timing/generate_bitstream`、`pl_compile_sim/elaborate_sim/run_simulation/parse_sim_log` | 构建链 + 仿真链 |
| S5 PS | command/query | `ps_import_hardware`、`ps_create_platform/bsp/app`、`ps_add_sources`、`ps_compile` | 软件构建链 |
| S6 | query | `verify_consistency` | 跨域一致性（纯只读） |
| S7 | command | JTAG 8 步部署、`ps_start/wait/stop_uart_capture` | 部署 + 捕获 |
| S8 | query | `evaluate_observation`、`get_operation_status`、`diagnose_execution` | 判定 + 恢复 |

纪律不变：command 一律 `operation_id → wait_operation`；`ps_*` 一律显式传 `session_id`；阶段之间只用自动发布的 Manifest 交接。

### 6.2 能力缺口（与配套提案交叉引用）

框架在 S3/S5/S7/S8 阶段会命中当前 101 工具的缺口，完整清单见 `docs/development/mcp/B11_data_acquisition_proposal.md` §5（N1–N7 需新增 MCP、S1–S7 需扩展 Skill、R1–R11 可复用）。本框架视角的要点：

- **S3 架构选型**：无中断连线/查询原子（N1）时，「中断 vs 轮询」无法走标准 BD 原子路径；无自动地址分配原子（N5）时地址规划靠手工 `platform_set_address`。
- **S5 PL**：自定义 RTL 迭代缺少 `pl_add_sources`/`pl_set_top`（N2），当前只能靠 `pl_create_project` 创建时一次性纳入 sources/top。
- **S5 PS**：BSP 驱动（XAxiDma）无显式使能/校验工具（N6），依赖 BSP 按 XSA 自动集成；cache 一致性是代码层知识（Skill 扩展 S4）。
- **S7 观测**：现有 UART capture 面向文本并剥离 `\x00`，二进制流需要新捕获模式（N3）；高波特率 921600 从未实证（N3 附注）。
- **S8 判定**：二进制完整性判定不在 `evaluate_observation` 语义内（N4/N7）。

## 7. 与现有 GPIO Skill 的关系与迁移路径

- GPIO Skill（`skills/zynq_gpio/`，B07 冻结）**保持不变**：它是框架的第一个实例化配方，同时充当框架骨架的验证基准（O7 R3 黑盒 PASS）。
- 若用户批准本框架与 B11 立项：新建领域知识包（如 `skills/zynq_gpio/` 之外的 `skills/zynq_data_acquisition/` 或按用户决定的结构），GPIO 配方作为对照样例回填，**不修改冻结资产**。
- 本框架的「配方 vs 框架」诊断结论同时约束未来视频/HDMI 切片：每次只新增领域知识包，不再新增独立大配方。

## 8. DRAFT 声明

- 本文档为 **DRAFT**，待用户审核；不写 FROZEN/COMPLETE，不声称已立项。
- 未修改任何代码、测试、skill、boards、冻结架构文档；`skills/zynq_gpio/` 冻结资产 SHA256 未动（对照 `B10_freeze_manifest.md` §3）。
- 引用的事实（工具数、板卡参数、phase 文件内容）均来自仓库内文件；带宽数字的自证计算式见配套提案 §4。
