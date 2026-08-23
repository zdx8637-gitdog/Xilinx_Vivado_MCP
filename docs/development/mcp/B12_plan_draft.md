# B12 立项规划草案：AN9238 高速 ADC → DMA → TCP 完整数据链路（DRAFT）

> 日期：2026-08-24（`Get-Date` 实测 2026-08-24 07:12 +08:00）
> 状态：**DRAFT — 待用户审核，不代表已立项、不冻结任何资产、不修改任何生产代码 / 测试 / skills / boards / 冻结文档。** 本文档为立项规划草案；阶段计划、门禁、能力缺口、物理事实清单均为草案值，立项后以正式规划为准，任何拆片均需用户审核后逐个推进。
> 方向定调（用户）：**AN9238 高速 ADC（已有配件）→ DMA → TCP 完整数据链路**，验证完整 PL/PS 集成编程；DeepSeek Harness 宿主迁移为未来独立事项（不在本 Brick 内）。
> 基线：B11 COMPLETE（`docs/brick_development_plan.md` §4/§6）；唯一 MCP `mcps/zynq_mcp/` **103 工具（9 control + 94 domain：platform 17 / pl 27 / ps 48 / verification 2）**，机械统计 `Tool(name=` = 103（本会话实测 `capabilities.py`）；泛化 Skill `skills/zynq_dev/`（SKILL.md + phases/0–8 + appendix_mechanics.md）；板卡 `boards/ALINX_AX7020_v1.0/`。
> 配套既有文档：`docs/development/mcp/B11_data_acquisition_proposal.md`（既有候选提案——本文档**取代其定位**为「未来数据采集实例」立项输入，并与之对齐/升级：其 B11-A/B/C 切片、流契约草案、缺口清单内容保留为参考，但按「UART 调试先行 + TCP 数据通道 + 1 MSPS 目标」重排）。

## 1. 背景与目标

### 1.1 定位：泛化框架的第二个验证实例

B11 已完成「泛化框架黑盒验证」：用 6-LED 项目当考题、Skill 完全去外设化，证明 Skill + MCP 是面向任意 Zynq 工程开发的通用框架。**B12 是这套框架的第二个验证实例**——把验证面从「静态 GPIO 电平」推进到「高速源同步并行总线采集 + DMA + 网络数据通路」的完整 PL/PS 集成编程。

| 维度 | B11（第一个实例） | B12（第二个实例） |
|---|---|---|
| 数据方向 | PS 写 → 外设（LED 输出） | 外设读 → PL 采集 → DMA → DDR → TCP 上送（输入数据链） |
| 时序特征 | 无时序约束（纯电平） | 高速源同步并行总线（单沿锁存 / IOB / 跨时钟） |
| 数据通路 | 无（寄存器直写） | 采集 → 降采样 → FIFO → DMA S2MM → DDR3 → lwIP → 上位机 |
| 观测通道 | UART 文本 marker | UART（调试通道）+ TCP（数据通道）分层 |
| 领域知识 | 无外设知识包 | 需「域插件」：高速 ADC 纪律 / 时钟域 / DMA / TCP 帧协议 |

### 1.2 数据链路目标（完整 PL/PS 集成）

```
AN9238(单通道, offset binary, 12bit)
   │ 源同步时钟（板载晶振，时钟输入 FPGA）
   ▼
PL 捕获（IOB 锁存 + 每拍必消费 FIFO）──┐
   │                                   │ 降采样（自由运行 ADC 的速率控制）
   ▼                                   ▼
跨时钟 FIFO（单点）                1 kHz（B12-A 调试档）/ 1 MSPS（B12-C 目标档）
   ▼
AXI-Stream 打包 ──► DMA S2MM ──► DDR3 ──► PS 读 ──► UART（调试通道，B12-A）
                                              └──────► lwIP TCP 服务器 GEM0 千兆（数据通道，B12-B/C）
```

### 1.3 分层原则（必须记录，含义不可改动）

- **UART = 调试通道，TCP = 数据通道**。UART 先证明「ADC 链路活」（机读还原频率/幅度），TCP 后承接「1 MSPS 实时波形流」。两者职责不混用：UART 只承担低速率可机读判定的调试上行，TCP 承担最终目标带宽的数据上行。
- **UART 调试先行**：先低采样率（如 1 kHz，PL 降采样实现——AD9238 无 CONVST/BUSY，是流水线自由运行型）经 UART 上行，证明「引脚 → 采样值 → PS → 上行」全链活；再上 TCP。
- **器件时序以 datasheet 为准**：AN9238 datasheet 作为物理事实输入给智能体；Skill 只放通用纪律（单沿锁存 / IOB / 每拍必消费 FIFO / 跨域单点），不放任何具体器件时序参数。
- **本次用单通道**：AN9238 为 2 通道（65 MSPS 12bit），本 Brick 只打通 ch0 单通道数据链，ch1 留作后续（复用同一套 DMA/TCP 通路）。
- **TCP 帧契约与上位机共同冻结**：可复用 B11 流协议草案（帧头 + 序号 + CRC）；上位机智能体就绪时间未定，冻结前 Zynq 侧用契约桩自证机读完整性。

## 2. 拆片与门禁（每片独立可验收）

> 拆片原则：每片引入**最小新变量**，门禁**全部机读可判定**（PASS/FAIL marker 风格沿用 B11），失败可定位到单层（ADC 链路 / DMA 通路 / 网络协议）。A → B → C 是数据路径的自然顺序；B12-A 先隔离「源同步采集 + 降采样 + UART 上行」，B12-B 在可信通路上引入「DMA + TCP 字节流」，B12-C 最后合入「1 MSPS 实时波形流」。

### B12-A：UART 调试链路（AN9238 单通道 → PL 捕获 → 降采样 → PS 读 → UART）

- **目标**：证明「引脚 → 采样值 → PS 读 → UART 上行」全链活。AN9238 单通道（ch0）→ PL 捕获（IOB 锁存 + 每拍必消费 FIFO）→ 降采样（1 kHz 档）→ PS 读（AXI-Lite 寄存器或小 FIFO）→ UART 逐样本/打包上行。
- **门禁**：正弦输入 → UART 数据机读还原频率/幅度正确（PASS/FAIL marker 风格沿用 B11：上行数据含可机读的逐样本值或统计量 + `ADC_UART_PASS`/`ADC_UART_FAIL` marker，`evaluate_observation` 文本判定）。机读判据 = 还原频率在容差内 + 幅度在容差内（offset binary 满量程 50% 输入 → 读回 ≈ 2048 @12-bit）。
- **复用能力**：platform 原子序列、PL 构建/仿真链、PS 构建链、JTAG 部署、UART 文本捕获（`ps_start/wait/stop_uart_capture` + `evaluate_observation`）、`verify_consistency`。
- **新增/扩展**：见 §3 缺口 N1（自定义 IP 仓库）、N3（AXI-Stream 覆盖验证）、S1–S3（高速源同步 / 时钟域 / 降采样纪律）。

### B12-B：DMA + TCP 通道（DMA S2MM → DDR3 + lwIP TCP 服务器 → 字节流回环）

- **目标**：在 B12-A 可信通路上引入「DMA S2MM → DDR3」与「lwIP TCP 服务器（GEM0 千兆）→ 上位机字节流回环」。数据源先用 PL 图案发生器（或 B12-A 的降采样采集）充当定长字节流，隔离 DMA/HP/地址/cache 与网络协议两层变量。
- **门禁**：M MB 无损 + 帧序号 + CRC 全过。上位机契约桩按 §6 协议解析 M MB 数据，机读判定 = 帧序号连续（无丢帧/乱序）且 CRC 全部通过 → `TCP_LOOPBACK_PASS`；任一断号/CRC 错 → `TCP_LOOPBACK_FAIL`。**TCP 帧契约与上位机共同冻结**（本片冻结前用契约桩自证）。
- **复用能力**：platform 原子（HP0 使能、axi_dma 实例化、地址分配）、PL 构建链、PS 构建链、JTAG 部署、`verify_consistency`。
- **新增/扩展**：见 §3 缺口 N1（GEM0/RGMII 使能键）、N2（lwIP 库选择）、N4（DMA 中断路由）、S4–S6（DMA 驱动 + cache 一致性 / TCP 帧协议 / lwIP 应用）。

### B12-C：高性能整合（1 MSPS 实时波形流 TCP 上送）

- **目标**：合入 A/B 两片，完成「AN9238 单通道 65 MSPS 采集 → PL 降采样 1 MSPS（÷65）→ FIFO → DMA S2MM → DDR3 → lwIP TCP → 上位机 1 MSPS 实时波形流（1 µs/样本）」的最终目标档位。
- **门禁**：正弦 → 上位机还原频率/幅度正确（机读判定沿用 B12-A 的判据，上行通道换为 TCP）。1 MSPS × 16-bit = 2 MB/s = 16 Mbps，GEM0 千兆（理论 125 MB/s）裕量充足，瓶颈不在链路而在「实时流契约 + 上位机解析」。
- **复用能力**：B12-A 的采集/降采样链路、B12-B 的 DMA/TCP 通路与帧协议、PL 仿真链（采集 testbench）。
- **新增/扩展**：见 §3 缺口（与 B12-B 共享 DMA 中断 / lwIP / 帧协议；无额外专用缺口，重点是档位换算与实时性调优）。

### 拆片依赖与顺序

1. **B12-A 无网络依赖**，先隔离「源同步采集 + 降采样 + UART」——若先做 DMA/TCP，ADC 抖动/时钟/电平问题会污染 DMA 与网络调试，无法判定故障层。
2. **B12-B 依赖 B12-A 的「可信定长数据源」**，但可用图案发生器先行（DMA/TCP 与 ADC 解耦），使 A/B 可并行推进。
3. **B12-C 依赖 A+B**，合入时只有「档位换算（65 MSPS → 1 MSPS）+ 实时流吞吐」是新变量。

## 3. 能力缺口清单（对照 103 工具逐项核对）

统计口径：`mcps/zynq_mcp/control/capabilities.py` 机械统计 `Tool(name=` = **103** = 9 control + 94 domain（platform 17 / pl 27 / ps 48 / verification 2）。每条标注「可复用现有工具 / 需新增 MCP / 需扩展 Skill」+ 一句话原因 + 影响切片。

### 3.1 可复用现有工具（10 项）

| # | 工具/组 | 一句话说明 | 影响切片 |
|---|---|---|---|
| R1 | control 9 工具（create_session / get_operation_status / wait_operation / get_execution_state / diagnose_execution / recover_execution 等） | 生命周期 + 长任务 + 崩溃恢复纪律全切片通用 | A/B/C |
| R2 | platform 原子序列（create_design / add_ps7 / configure_ps7 / add_ip / connect_interface·clock·reset / set_address / assign_addresses / make_external / validate / generate_wrapper / synthesize / export_hardware / export_manifest） | BD 拓扑、时钟复位、地址分配、外部化、验证、XSA/Manifest 导出——DMA/HP/互联拓扑全靠这套原子（B11 ③.1 新增的 assign_addresses/make_external/synthesize 已关闭原提案 N5/部分 N2 缺口） | A/B/C |
| R3 | `pl_create_project` + `pl_generate_target` | 可在创建时纳入自定义采集 RTL 源 + ADC 约束，并生成 BD OOC 输出产物（wrapper 综合前必需） | A/B/C |
| R4 | PL 构建链（pl_synthesize / place / route / generate_bitstream / analyze_timing / analyze_utilization） | 采集/降采样/FIFO RTL 的综合实现与时序收口（源同步总线时序经 `pl_analyze_timing` 验证） | A/B/C |
| R5 | PL 仿真链（pl_compile_sim / elaborate_sim / run_simulation / parse_sim_log） | 采集控制器 / 降采样计数器 / 跨时钟 FIFO 的 testbench 仿真验证 | A/C |
| R6 | PS 构建链（ps_import_hardware / create_platform / create_bsp / create_app / add_sources / compile / get_build_status / read_elf_info） | PS 裸机应用构建（含 DMA 驱动 + lwIP 应用源码经 `ps_add_sources` 进入） | A/B/C |
| R7 | JTAG 部署（ps_connect_hw_server / initialize_ps / load_hardware / download_elf / run_target / ensure_arm_accessible / recover_target 等） | 部署序列与恢复通用（JTAG-only 开发配置，架构 P7） | A/B/C |
| R8 | UART 文本捕获（ps_start / wait / stop_uart_capture / read_uart / write_uart / diagnose_uart_clock / list_serial_ports） | B12-A 调试通道的捕获与波特率诊断（`ps_diagnose_uart_clock` 校验真实波特率） | A |
| R9 | `verify_consistency` | 三 Manifest + 板卡 profile + 产物 SHA256 一致性（采集工程同样走 Manifest 链） | A/B/C |
| R10 | `evaluate_observation`（文本 marker 判定，pass/fail marker 必填） | B12-A/B/C 的 PASS/FAIL marker 机读判定复用；数值容差判定由 PS 内比较 + marker 落地（不新增数值断言原子） | A/B/C |

### 3.2 需新增 MCP 能力（6 项，其中 1 项可选）

| # | 缺口 | 一句话原因（为什么现有工具不够） | 影响切片 |
|---|---|---|---|
| N1 | **`platform_configure_ps7` 缺 GEM0/RGMII 使能键**（`PCW_EN_ENET0` / `PCW_ENET0_PERIPHERAL_ENABLE` / `PCW_ENET0_ENET0_IO`（MIO 16..27）/ `PCW_ENET0_GRP_MDIO_ENABLE`+`MDIO_IO`（MIO 52..53）/ `PCW_ENET0_PERIPHERAL_FREQMHZ`（1000 Mbps）/ `PCW_ENET_RESET_*` / MIO 16–27 IOTYPE=HSTL 1.8V） | 当前 schema 明示键仅 m_axi_gp0/gp1、s_axi_hp0/hp1、s_axi_acp、irq_f2p、fclk0/1、uart1、gpio、ddr（GEM 键只能靠 `additionalProperties: True` 透传，未明示、未校验）；TCP 数据通道必须先使能 GEM0 + RGMII 1.8V 电平 + 125 MHz ref 时钟 | B（DMA+TCP）、C |
| N2 | **`ps_create_bsp` 缺 lwIP 库选择** | 当前 schema 仅 platform_name + project_path，无 lwip141/lwip211 等 BSP 库选择与版本校验；lwIP 应用依赖 BSP 正确集成 lwIP 库（裸机 netif/TCP 栈） | B（DMA+TCP）、C |
| N3 | **AXI-Stream 连接/外部化原子覆盖性验证** | 采集 IP 的 `M_AXIS`（tdata/tkeep/tlast/tready/tvalid）→ `axis_register_slice` → `axi_dma/S_AXIS_S2MM` 是纯 AXI-Stream 链；现有 `platform_connect_interface` 描述面向内存映射 AXI，`platform_make_external` 的 interface 分支未验证 AXI-Stream 接口外部化。需 host_live 验证覆盖性，不足则补原子 | A/B/C |
| N4 | **DMA 中断路由原子**（s2mm_introut → xlconcat → IRQ_F2P[0..15]） | `platform_configure_ps7` 的 `irq_f2p` 只能整体使能 16 线，无法把具体 PL 中断源（如 `axi_dma/s2mm_introut`）路由到指定 IRQ_F2P 线；现有 connect 原子仅覆盖 interface/clock/reset，无单比特中断网连接（需 xlconcat + connect_bd_net） | B（DMA 完成中断）、C |
| N5 | **`pl_create_project` 增自定义 IP 仓库路径**（`ip_repo_paths` + `update_ip_catalog`） | 官方参考工程用 `set_property ip_repo_paths` + `update_ip_catalog` 注册 `alinx.com:user:ad9238_sample:1.0` 自定义采集 IP；`pl_create_project` 当前仅 name/part/sources/constraints/project_dir/top/force，无 IP 仓库路径参数 | A/B/C |
| N6 | （可选）**`pl_add_sources` / `pl_set_top`（迭代式 RTL 源增删 / 顶层切换）** | 采集 RTL 迭代开发（反复改端口/换顶层）时 `pl_create_project` 只能在创建时指定源，无增量增删工具；低优先级（B12-A 可一次性建全源） | A/C |

### 3.3 需扩展 Skill（6 项，全部按「域插件」设计，零外设字样门禁）

> **域插件机制**（沿用 B11 的「知识包按需挂载，永不写死在框架里」，SKILL.md §领域知识边界）：B12 新增的领域知识**不写进** SKILL.md / phases/0–8 / appendix_mechanics.md 的通用文本，而是作为独立「域插件」文档按实例挂载。门禁（机械扫描 0 命中）：通用 Skill 文本中 **gpio / 0x41200000 / LED / breath|blink 等外设字样**（B11 门禁延续）**以及本 Brick 新增的 ad9238 / AN9238 / lwIP / GEM0 / TCP / ENET 等领域字样**均不得出现；具体器件时序、引脚、协议参数只存在于域插件 + 需求文档 + 板卡物理事实。

| # | 缺口（域插件） | 一句话原因 | 影响切片 |
|---|---|---|---|
| S1 | **高速源同步并行总线纪律**（datasheet 为准 / 单沿锁存 / IOB 锁存 / 每拍必消费 FIFO） | 12-bit 并行源同步总线在 65 MSPS 下必须 IOB 锁存 + 连续消费（自由运行 ADC 无背压），这是 PL 工程层纪律，器件具体时序参数以 datasheet 为准、不写进 Skill | A/C |
| S2 | **时钟域纪律**（时钟转发/PLL、跨域 FIFO 单点、异步 FIFO 参数） | ADC 时钟域 → PL 逻辑时钟域 → AXI-Stream 的跨域必须单点（一处异步 FIFO），input delay / IOB 约束写法是 PL 域扩展知识 | A/C |
| S3 | **降采样与自由运行 ADC 速率控制**（无 CONVST/BUSY，计数器降采样，档位换算 65 MSPS→1 kHz / 1 MSPS） | 自由运行流水线 ADC 无启动/忙握手，速率由 PL 降采样计数器控制；档位换算（÷65000 / ÷65）是实例级决策 | A/C |
| S4 | **PS 侧 DMA 驱动 + cache 一致性规范**（XAxiDma S2MM、BD 链、Xil_DCacheInvalidateRange 时序位置） | DMA S2MM 收包 + DDR 缓冲 + cache 失效是 PS 工程层知识，需写入 PS 域插件 | B/C |
| S5 | **TCP 帧协议纪律**（帧头 + 序号 + CRC，与上位机共同冻结，复用 B11 流协议草案） | 帧构造/解析、CRC、序号连续性判定的实现规范；与上位机契约落地 | B/C |
| S6 | **PS 侧 lwIP 应用知识**（BSP 库选择、netif 初始化、TCP server 模板、GEM 中断） | lwIP 裸机 netif/TCP server/PHY 初始化的应用结构是 PS 域插件知识，配合 N2 库选择 | B/C |

**缺口统计**：可复用 **10** 项 / 需新增 MCP **6** 项（N6 可选）/ 需扩展 Skill（域插件）**6** 项，合计 **22** 项。

## 4. 物理事实清单

> 纪律：物理事实（现实层）归用户，智能体只消费。每项标注**出处**与**待确认**状态；缺项即阻塞对应切片（B12-A 依赖 §4.1/§4.2 采集相关项；B12-B 依赖 §4.2 网口拓扑/§4.3 IP 方案）。

### 4.1 用户提供项（物理事实，已定调，原样记录）

| # | 物理事实 | 出处 | 状态 |
|---|---|---|---|
| U1 | 硬件模块：**ALINX AN9238**（2 通道 65 MSPS 12bit AD9238，板载晶振、时钟输入 FPGA，offset binary 编码，SMA 单端 ±5V）；**本次用单通道** | 用户定调 | 已确认（方向） |
| U2 | 信号发生器（正弦） | 用户提供 | 已确认 |
| U3 | AN9238 datasheet（将作为物理事实输入给智能体；**器件时序参数以 datasheet 为准**） | 用户将提供 | 待提供 |
| U4 | 引脚连接：官方 XDC 已找到（见 §4.2 F1），标注「出处=官方教程 XDC，**待用户实板确认**」 | 用户 + 官方教程 | 待实板确认 |

### 4.2 已找到项（白盒规划参考，黑盒智能体不会获得；出处与关键参数必须准确）

| # | 物理事实 | 值 / 关键参数 | 出处 | 状态 |
|---|---|---|---|---|
| F1 | **J11 引脚映射（ADC 数据/时钟）** | ch0 数据 J20/H20/L16/L17/M17/M18/D19/D20/E18/E19/G17/G18（data[0..11]）+ 时钟 H17；ch1 数据 F16/F20/F19/G20/G19/H18/J18/L20/L19/M20/M19/K18 + 时钟 F17；IOSTANDARD **LVCMOS33**；**IOB true**（数据脚） | `D:\BaiduNetdiskDownload\AX7020_2023.1\course_s1_fpga\18_ad9238_hdmi\auto_create_project\src\constraints\ad9238_hdmi.xdc`（与 `...\course_s2_vitis\30_ad9238_lwip\...\src\constraints\ad9238.xdc` 引脚一致，仅端口名不同：ad9238_* vs adc_*） | **待实板确认**（出处=官方教程 XDC） |
| F2 | **时钟方向歧义（关键）** | HDMI 参考（18_ad9238_hdmi）把时钟脚 H17/F17 作**输入**（`ad9238_sample.v` 的 `input adc_clk`，与「时钟输入 FPGA」一致）；lwIP 参考（30_ad9238_lwip）把 `adc_ch0_clk/adc_ch1_clk` 作 **FPGA 输出**（pl_config.tcl `create_bd_port -dir O`，由 FCLK_CLK2 驱动）。两官方参考方向相反 | 上述两个官方工程 | **待确认（以 datasheet/实板为准）**——影响「源同步捕获 vs FPGA 供时钟」两种 PL 捕获拓扑 |
| F3 | **GEM0/RGMII 使能键（白盒）** | `PCW_EN_ENET0 {1}`、`PCW_ENET0_PERIPHERAL_ENABLE {1}`、`PCW_ENET0_ENET0_IO {MIO 16 .. 27}`、`PCW_ENET0_GRP_MDIO_ENABLE {1}`、`PCW_ENET0_GRP_MDIO_IO {MIO 52 .. 53}`、`PCW_ENET0_PERIPHERAL_FREQMHZ {1000 Mbps}`、`PCW_ACT_ENET0_PERIPHERAL_FREQMHZ {125.000000}`、`PCW_ENET_RESET_ENABLE {1}`（Active Low / Share reset pin）、MIO 16–27 IOTYPE=**HSTL 1.8V**（RGMII 1.8V 电平） | `...\30_ad9238_lwip\Vivado\auto_create_project\ps_config.tcl` | 白盒参考（黑盒不获） |
| F4 | 板卡网口物理链路 | GEM0 → RTL8211E，MIO 16–27，RGMII | `boards/ALINX_AX7020_v1.0/README.md` §Peripherals | 已确认（板卡事实） |
| F5 | 官方 ADC→DMA→网络参考工程结构 | 自定义 IP `ad9238_sample`（vlnv `alinx.com:user:ad9238_sample:1.0`，AXI-Lite 控制 start/length + M_AXIS 16bit 输出 + `xpm_fifo_async` 1024 深）；2× `axi_dma`（S2MM-only，**Scatter-Gather**，64-bit M_AXI_S2MM，burst 128）→ `axi_interconnect`（4SI/1MI）→ `S_AXI_HP0`；`axis_register_slice`（TDATA_NUM_BYTES=2）；中断 `s2mm_introut` → `xlconcat` → `IRQ_F2P`；地址 `axi_dma_0`=0x40400000、`ad9238_sample_0`=0x43C20000 | `...\30_ad9238_lwip\Vivado\auto_create_project\pl_config.tcl` + `create_project.tcl` | 白盒参考（黑盒不获） |
| F6 | 官方 Vitis 侧参数 | `MAX_DMA_LEN 0x800000`、`ADC_SAMPLE_NUM 1024*512`、`BD_COUNT 4`、`ADC_BITS 12`、`ADC_BYTE 2`；DMA 缓冲 `__attribute__((aligned(64)))`；`Xil_DCacheInvalidateRange` 在收包后、`XAxiDma` S2MM 中断驱动（`XScuGic` + `XPAR_FABRIC_AXI_DMA_0_S2MM_INTROUT_INTR`） | `...\30_ad9238_lwip\Vitis\auto_create_vitis\src\ad_lwip\adc_dma.h` + `main.c` + `lwip_app.c` | 白盒参考（黑盒不获） |
| F7 | **官方参考为 UDP 非 TCP（重要）** | 官方 `lwip_app.c` 用 **UDP 8080**（`start_udp(8080)`、`udp_sendto`），非 TCP；用户定调为 **TCP**。B12-B 的 TCP server 需在官方 UDP 模板上改 TCP（lwIP raw API `tcp_new/tcp_bind/tcp_listen/tcp_accept/tcp_write`），此为实例级工程决策 | `...\30_ad9238_lwip\Vitis\auto_create_vitis\src\ad_lwip\lwip_app.c` | 白盒参考（黑盒不获） |
| F8 | PC 端示波器 | 官方附 `示波器.exe`（PC 端波形显示，解析官方 UDP 帧 `TargetHeader` 5B + 大端字节序样本） | `...\30_ad9238_lwip\示波器.exe` | 白盒参考（可选上位机桩，帧契约不与之绑定） |

### 4.3 尚未提供项（阻塞对应切片）

| # | 缺失事实 | 阻塞切片 | 说明 |
|---|---|---|---|
| M1 | **板子网口拓扑**（交换机 / 直连） | B12-B/C | 决定 MAC/IP/网关/自协商（千兆需交换机或千兆直连；百兆交换机会把 GEM0 降到 100 Mbps） |
| M2 | **IP 方案**（静态 IP / DHCP / 网段） | B12-B/C | lwIP netif 初始化与 TCP server 绑定地址 |
| M3 | **上位机智能体就绪时间** | B12-B/C | TCP 帧契约需与上位机共同冻结；就绪前用契约桩自证机读完整性 |

## 5. 三 Agent 工作流沿用 + 黑盒输入冻结纪律沿用

- **三 Agent 工作流**（沿用 B11，逐片 A/B/C 各走一遍）：Agent1（长期白盒实现 + 自测）→ Agent3（全新上下文阶段黑盒，隔离目录复现）→ 用户硬件确认 → Agent2（全新无记忆终验黑盒）。
- **黑盒输入冻结纪律**（沿用 B11 阶段④，`docs/development/tests/B11_phase4_blackbox_basis.md`）：B12-A 黑盒运行时冻结「需求文档 + Skill（含域插件）+ MCP 生产代码 commit + 板卡物理事实」的 SHA256，任何漂移即判定运行无效。**B12-A 黑盒时冻结需求 + Skill + MCP 哈希**（域插件纳入 Skill 冻结集，与 B11 的「Skill 10 文件哈希」同风格）。
- **硬门禁**（沿用 B09/O7 R3）：黑盒智能体零 shell、全公开 MCP、Execution Ledger 全覆盖、Consistency 通过、UART/TCP 机读判定、收尾无残留进程。
- **隔离规则**：黑盒智能体不得读取 `D:\fpgaproject` 下任何文件（唯一例外：以子进程方式启动 `python -m mcps.zynq_mcp.server`）；厂商资料（§4.2 F 系列）**只作白盒规划参考，黑盒智能体不会获得**。

## 6. 未来事项记录（DSH 宿主迁移，不在本 Brick 内）

- **DSH（DeepSeek Harness）宿主迁移（插件化）为独立 Brick**，不在本 Brick 内；本规划**不改结构、不预埋宿主专属假设**。
- 本 Brick 只遵守两条纪律：
  1. **Skill 不写宿主专属假设**（通用 Skill 文本不得出现任何 DSH / Harness / 宿主调用形态的假设；域插件同理只写领域知识不写宿主假设）；
  2. **`skills/zynq_dev` 保持唯一技能源**（不新建并列 Skill 目录，域插件作为该目录下的挂载资源，遵循「永不写死在框架」机制）。

## 7. DRAFT 声明与硬约束

- 本文档为 **DRAFT**，待用户审核；**不写 FROZEN/COMPLETE，不代表立项**。
- 拆片 A/B/C、门禁、缺口清单、物理事实清单、TCP 帧契约草案均为规划草案；B12-B 的 TCP server 实现形态（官方 UDP 模板改 TCP）、时钟方向（源同步 vs FPGA 供时钟）、1 MSPS 档位换算等在实现轮次出最小设计并经审核后定案。
- **硬约束遵守声明**（本会话实测）：
  - 纯文档：未修改 `mcps/`、`skills/`、`boards/`、任何冻结文档、brick 状态、README、CLAUDE.md、legacy 目录；仅新增本文档。
  - 未运行 pytest、未启动 EDA、未碰硬件。
  - 读文件用 `read` 工具；非 UTF-8 厂商文件（adc_dma.h/main.c）用 `[System.IO.File]::ReadAllText(path, GB2312)` 读取，未用 `Get-Content`（避免截断）。
  - 厂商资料路径与关键参数按上文 §4.2 逐条标注出处；引脚映射、GEM 使能键、DMA 拓扑参数均来自 read/grep 机械读取（本会话实测），未臆造。
