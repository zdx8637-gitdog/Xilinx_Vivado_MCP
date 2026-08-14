# B11 数据采集切片立项提案（DRAFT）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 16:26 +08:00）
> 状态：**DRAFT — 待用户审核。本文档是立项提案，不代表切片已立项、不冻结任何资产、不修改任何生产代码 / 测试 / skills / boards。**
> 性质：为「下一 Brick（数据采集切片）」提供切片分解、带宽预算、能力缺口、流契约草案与用户物理事实清单。配套设计见 `docs/development/skill/B11_generalized_skill_design.md`（两份文档交叉引用）。
> 基线：B10 冻结包（`docs/development/mcp/B10_freeze_manifest.md`，tag `o7r3-baseline-20260813`）；唯一 MCP `mcps/zynq_mcp/`（101 工具：9 control + 92 domain，机械统计 `Tool(name=` = 101）；板卡 `boards/ALINX_AX7020_v1.0/`。

## 1. 目标重述与边界

用户一句话需求（记录于 B10 清单 §6）：

> PL AD 采集（pin 配置由用户提供）→ DMA → DDR3 → PS 读 DDR3 → UART 上行到上位机，上位机做实时成像/分析。

本框架（Zynq 侧）职责边界：

| 项 | 归属 |
|---|---|
| PL 采集逻辑（ADC 控制器、跨时钟、FIFO、AXI-Stream 打包） | 本框架（智能体实现） |
| AXI DMA → DDR3 通路（HP 口、地址映射） | 本框架（Platform 域 + 工程决策） |
| PS 读 DDR3（DMA 驱动、cache 一致性）与 UART 上行 | 本框架（PS 域 + 工程决策） |
| **流协议契约（帧格式、速率、完整性判定）** | **两智能体共同冻结契约**（本文档 §6 为草案，待上位机智能体确认） |
| 上位机接收、解析、实时成像/分析 | **另一个智能体**（本框架只交付「可机器验证的流契约」与实现侧证据） |
| ADC 型号、引脚分配、量程、板级电平方案 | **用户**（现实层物理事实，§7 清单） |

边界声明：本提案只覆盖 Zynq 侧（B11-A/B/C 三个子切片）；上位机侧验证以「契约桩」（按 §6 契约解析的脚本/桩程序）在本框架内自证机读完整性，不替代上位机智能体。

## 2. 切片分解建议：B11-A → B11-B → B11-C

### B11-A：DMA → DDR3 环回（无真实 ADC）

- **目标**：在无真实 ADC 的情况下，验证「PL 图案发生器 → AXI DMA（S2MM, HP0）→ DDR3 → PS 读回比对」整条数据通路，并固化地址映射与 cache 一致性语义。
- **门禁判据（无损环回）**：PS 应用对 DMA 缓冲区的 N 字节（如 1 MiB，递增/PRBS 图案）逐字节比对与图案发生器输出**完全一致**（0 差异），并经 UART 输出机器可判定 marker（如 `DMA_LOOPBACK_PASS`，含校验和）。机读判定 = 比对一致 + 校验和一致。
- **需要的用户物理事实**：无（纯内部链路；DDR 缓冲地址段（如 0x00100000 起）与大小由工程层决定）。
- **复用能力**：platform 原子（`platform_configure_ps7` s_axi_hp0/irq_f2p/fclk、`platform_add_ip` 实例化 axi_dma、`platform_connect_*`、`platform_set_address`、`platform_validate` 等 14 原子）、PL 构建链（`pl_create_project` 可在创建时纳入图案发生器 RTL）、PL 仿真链（图案发生器 testbench）、PS 构建链（`ps_create_bsp/app`、`ps_add_sources`、`ps_compile`）、JTAG 部署 8 步、`verify_consistency`、`evaluate_observation`（文本 marker 判定）。
- **新增/扩展能力**：见 §5 缺口 —— N1（中断连接原子，若走中断驱动）、N2（自定义 RTL 顶层/源管理）、N4（DDR 缓冲校验，建议 PS 内 CRC + marker 复用路径）、N5（自动地址分配，低优先级）、N6（BSP 驱动使能/校验）；Skill 扩展 S2（自定义顶层集成）、S3（DMA/中断拓扑决策）、S4（XAxiDma + cache 一致性规范）。

### B11-B：UART 二进制流协议

- **目标**：在 B11-A 通路上验证「PS 读 DDR3 → UART 二进制帧上行」的端到端协议：帧头 + 序号 + 载荷 + CRC、定长捕获、M MB 无损上行。
- **门禁判据（机读完整性 100%）**：上位机侧契约桩按 §6 协议解析 M MB 数据，机读判定 = **帧序号连续（无丢帧/乱序）且 CRC 全部通过** → PASS；任一断号或 CRC 错 → FAIL。定长捕获可复现。
- **需要的用户物理事实**：无（波特率可行性受桥片物理限制，§4 已按板卡资料确认）。
- **复用能力**：PS 构建链、JTAG 部署、B11-A 的 DMA 缓冲（充当定长数据源）。
- **新增/扩展能力**：N3（UART 二进制/定长捕获模式，含 921600 实证）、S5（二进制流协议实现与自检框架）、S7（高波特率部署变体）。

### B11-C：真实 ADC 接入

- **目标**：在 A/B 可信后接入真实 ADC：ADC 控制器 RTL、跨时钟域（ADC 采样时钟 → PL 逻辑时钟）、FIFO、时序约束，完成「引脚 → 采样值 → DDR3 → UART」全链路。
- **门禁判据（已知输入 → 正确采样值）**：对 ADC 施加已知输入（信号源已知电压或静态电平），PS 读回采样值在容差内与预期一致（如满量程 50% 输入 → 读回 ≈ 2048 @12-bit / 32768 @16-bit），UART 输出机器可判定 PASS。
- **需要的用户物理事实**：§7 全清单（型号、接口、分辨率、采样率、通道数、量程、引脚分配、采样时钟来源）。
- **复用能力**：B11-A 的 DMA 通路、B11-B 的流协议、PL 仿真链（ADC 控制器 testbench）。
- **新增/扩展能力**：N2（自定义 RTL 顶层集成——ADC 控制器 + 跨时钟 FIFO + AXI-Stream 打包）、N1（采集完成中断）、N7（数值容差断言判定）；Skill 扩展 S6（跨时钟/FIFO/时序约束知识）、S1（物理事实清单流程）。

### 为什么先 A 后 B 再 C

1. **依赖链**：B11-B 需要「PS 读 DDR3 的定长数据源」（B11-A 的环回缓冲正好充当）；B11-C 需要 B11-B 的流协议把采样值送达上位机。A → B → C 是数据路径的自然顺序。
2. **故障隔离面最小**：A 无外部依赖，先隔离「DMA/HP/地址/cache」这类工程语义问题；B 在可信通路上只引入「协议/捕获」一个变量，门禁是纯机读（序号+CRC），与硬件波形解耦；C 最后才引入物理 ADC，此时只有「引脚 → 采样值」是新变量。若先做 C，ADC 抖动/时钟/电平问题会污染 DMA 与 UART 调试，无法判定故障层。
3. **每步门禁都可机读验收**：A（比对一致）、B（序号+CRC 100%）、C（已知输入 → 正确值），符合本框架「机读 PASS/FAIL」纪律；A/B 无硬件依赖，可在无 ADC 时先行冻结链路契约。

## 3. 前置：板卡物理事实（已从板卡资料确认）

| 项 | 值 | 出处 |
|---|---|---|
| USB-UART 桥片 | **CP2102-GM**（Silicon Labs CP210x 系列），VID/PID = 0x10C4 / 0xEA60 | `boards/ALINX_AX7020_v1.0/README.md` §UART；`board_profile_ALINX_AX7020_v1.0.json` `usb_bridge` |
| UART 控制器 / 引脚 | UART1，MIO 48/49；默认 115200 | 同上 |
| UART 桥片最大波特率 | **1 Mbps**（Silicon Labs CP2102 数据手册：300 bps – 1,000,000 bps；CP2102N 才支持 3 Mbaud）。⚠ 此值来自芯片数据手册规格，板卡资料未直接标注最大波特率，**待实物/用户确认** | CP2102-GM 数据手册（[manualzz](https://www.manualzz.com/doc/13435928/silicon-labs-cp2102--gm-usb-to-uart-bridge-datasheet) / [elcodis](https://elcodis.com/parts/562534/CP2102-GM_dt88465.html#datasheet)）；CP2102 不支持 3 Mbaud（[Silicon Labs 社区](https://community.silabs.com/s/question/0D5Vm00000JMZfXKAX/does-the-cp2102-allow-for-3-meg-baud-while-using-it-in-vcp-mode?language=en_US)） |
| PS UART1 波特率发生器 | UART_REF ≈ 90.9 MHz（IO_PLL 1000 MHz / (DIVISOR0+1=11)）；Baud = UART_REF / (CD × BDIV)。921600：CD=99,BDIV=1 → 918.3 kbps（−0.37%）或 CD=49,BDIV=2 → 927.6 kbps（+0.66%），均 < ±2% 容差 | `skills/zynq_gpio/phases/5_deployment.md` L66、`phases/7_debug_recovery.md` L114–118（实测 115944 @115200 档） |
| DDR3 | 配置 512 MB，32-bit，533.333 MHz（DDR-1066）→ 理论带宽 ~4.27 GB/s | `board_profile_*.json` `ddr_*`；README §Memory |
| PL 资源 | 53,200 LUT / 106,400 FF / 140 BRAM36 / 220 DSP48E1 | README §PL Resources；board_profile `pl_resources` |
| PL 扩展口 | 2× 40-pin headers（J11/J13，2.54mm，3.3V） | `docs/architecture_ai_zynq7020.md` 附录 B（L2030） |
| Ethernet（未来判据线） | GEM0 → RTL8211E，MIO 16–27，RGMII（物理链路存在；PS GEM 软件栈未实现） | README §Peripherals |

## 4. UART 带宽预算表

### 4.1 基础换算（计算式自证）

- **8N1 每字节 10 bit** = 1 start + 8 data + 1 stop → 原始字节率 `B_raw = 波特率 / 10`。
  （若改为 8N2 双停止位则为 11 bit/字节 → `B_raw = 波特率 / 11`，本表按标准 8N1 计算。）
- **帧开销模型**（§6 协议草案）：帧 = 帧头 2 B + 序号 4 B + 长度 2 B + 载荷 P B + CRC 2 B → 开销 H = 10 B。
- **16-bit 单通道采样率**（带帧，256 样本/帧，P=512 B）：`S_sps = (波特率/10) × 512/522 ÷ 2`。
- **灰度 8-bit 成像帧率**：`fps = (波特率/10) × N/(N+10) ÷ N`，N = 像素数（160×120 = 19,200；320×240 = 76,800）。
- **无帧开销上限**（理论参考）：`S_sps ≤ (波特率/10) ÷ 2`。

### 4.2 预算表

| 波特率 (bps) | 桥片可行性（CP2102-GM ≤ 1 Mbps） | 原始字节率 B_raw (B/s) | 16-bit 单通道采样率：无帧开销 | 16-bit 采样率：带帧（256 样本/帧） | 灰度 160×120 (fps) | 灰度 320×240 (fps) |
|---|---|---|---|---|---|---|
| **921,600** | ✅ 规格内 | 92,160 | 46,080 S/s（46.1 kS/s） | **45,197 S/s（45.2 kS/s）** | **4.80** | **1.20** |
| 2,000,000 | ❌ 超桥片上限（PS 端可行：CD=45,BDIV=1 → 2.02 Mbps，+1.0%） | 200,000 | 100,000 S/s | 98,084 S/s（98.1 kS/s） | 10.41 | 2.60 |
| 3,000,000 | ❌ 超桥片上限（PS 端可行：CD=30,BDIV=1 → 3.03 Mbps，+1.0%） | 300,000 | 150,000 S/s | 147,126 S/s（147.1 kS/s） | 15.62 | 3.90 |

示例计算（921,600 档）：
- `B_raw = 921600/10 = 92,160 B/s`
- 16-bit 带帧：`92160 × 512/522 ÷ 2 = 45,197 S/s`
- 160×120：`92160 × 19200/19210 ÷ 19200 = 4.7975 → 4.80 fps`
- 320×240：`92160 × 76800/76810 ÷ 76800 = 1.1998 → 1.20 fps`

### 4.3 可行性结论（每档）

- **921,600**：**当前板卡可行**（CP2102-GM 规格内，PS 波特率发生器误差 < ±1%）。16-bit 单通道上限 ≈ **45 kS/s**；灰度档位 ≈ 160×120 @ **4.8 fps**、320×240 @ **1.2 fps**。
- **2M / 3M**：**当前板卡物理不可行**——瓶颈是 CP2102-GM 的 1 Mbps 上限（PS 端 UART 发生器本身可到 2.02/3.03 Mbps）。表格按波特率档列出仅供（a）桥片升级（如 CP2102N 支持 3 Mbaud）或（b）换 Ethernet 时的判据参考。**本框架 B11-B 建议以 921,600 为实测档位**，2M/3M 不列为门禁。

### 4.4 PS 端简单压缩的收益与代价

| 方案 | 增益 | 代价 |
|---|---|---|
| delta 编码（16-bit 样本 → 8-bit 相邻差，周期性绝对锚帧） | ~2× 载荷 | PS CPU（667 MHz A9 在 ≤ 92 kB/s 下开销可忽略）、断帧错误传播（需锚帧/重同步）、上位机解码 |
| 游程编码 RLE（灰度） | 典型 2–4×（结构化图像），最差 1×（噪声） | 转义/长度前缀处理、延迟、上位机解码复杂度 |
| 组合（行内 delta + RLE） | 2–4× | 复杂度叠加；有损不可接受时只能无损变体 |

**代价共性**：CPU 开销在 UART 速率下不构成瓶颈；真正代价是**错误传播**（有损帧会污染后续 delta 链）与**上位机解码契约复杂度**（压缩格式必须写进 §6 契约）。压缩只能**推迟**判据线，不能改变桥片 1 Mbps 物理上限。

### 4.5 物理判据线：何时必须换 Ethernet

以 921,600 档、帧开销 10 B/帧为基准，有效载荷上限 ≈ **92.1 kB/s**：

- **16-bit 单通道采集需求 > ~45 kS/s** → UART 物理不可行，必须 Ethernet；
- **灰度成像需求 > ~4.8 fps @160×120 或 > ~1.2 fps @320×240** → UART 物理不可行，必须 Ethernet；
- 压缩（2–4×）可将判据线放宽约 2–4 倍（如 ~90–180 kS/s），但以契约复杂度与错误传播为代价。

Ethernet 判据的物理基础：板载 **GEM0 → RTL8211E（RGMII，MIO 16–27）** 链路已存在（README §Peripherals），但 PS GEM 软件栈未实现（101 工具无 GEM 能力），且 P7 为 JTAG-only 开发配置——**Ethernet 上行属后续切片，不在 B11 范围**。DDR3（~4.27 GB/s 理论）与 HP 口（64-bit @FCLK）带宽远高于 UART，**瓶颈恒在上行链路**，DMA→DDR3→PS 段无需担忧带宽。

## 5. 能力缺口清单（对照现有 101 工具逐项核对）

统计口径：`mcps/zynq_mcp/control/capabilities.py` 机械统计 `Tool(name=` = 101 = 9 control + 92 domain（platform 15 / pl 27 / ps 48 / verification 2）。每条标注「可复用现有工具 / 需新增 MCP 能力 / 需扩展 Skill」+ 一句话原因 + 影响子切片。

### 5.1 可复用现有工具（11 项）

| # | 工具/组 | 一句话说明 | 影响子切片 |
|---|---|---|---|
| R1 | control 9 工具（create_session / get_execution_state / wait_operation / get_operation_status / diagnose_execution / recover_execution 等） | 生命周期与长任务纪律全切片通用 | A/B/C |
| R2 | `platform_configure_ps7` | 已支持 s_axi_hp0/hp1、irq_f2p、fclk0/1，可配置 DMA 通路所需 PS7 接口（注：schema 明示 hp0/hp1，hp2/hp3 仅靠 additionalProperties 透传，未明示，需验证） | A/C |
| R3 | `platform_add_ip`（vlnv 通用实例化） | 可实例化 axi_dma / axi_smartconnect / axi_intc / axi_fifo_mm_s 等 Catalog IP；架构 §4.3.2 规划的 add_axi_dma 快捷 API 未实现，但通用原子可覆盖（差异=无专属校验，属 Skill 组合问题） | A/C |
| R4 | `platform_connect_interface/clock/reset`、`platform_set_address`、`platform_validate`、`platform_generate_wrapper`、`platform_export_hardware/manifest` | BD 拓扑连线、时钟复位、逐段手工地址、验证与产物导出 | A/C |
| R5 | `pl_create_project`（sources+constraints+top 创建时可用） | 可在创建时纳入自定义 RTL 源与 ADC XDC | A/C |
| R6 | `pl_generate_target`、`pl_synthesize/place/route/analyze_timing/generate_bitstream` | PL 构建链 | A/B/C |
| R7 | `pl_compile_sim/elaborate_sim/run_simulation/parse_sim_log` | 图案发生器 / ADC 控制器 / 跨时钟 FIFO 的仿真验证 | A/C |
| R8 | `ps_import_hardware`、`ps_create_platform/bsp/app`、`ps_add_sources`、`ps_compile`、`ps_get_build_status`、`ps_read_elf_info` | PS 软件构建链 | A/B/C |
| R9 | JTAG 部署 8 步（ps_connect_hw_server … ps_load_hardware、ps_download_elf、ps_run_target、ps_ensure_arm_accessible、ps_recover_target 等） | 部署序列与恢复通用 | A/B/C |
| R10 | `verify_consistency` | Manifest 链一致性（revision/board/SHA256）通用；DDR 缓冲区扩展见 N4 | A/B/C |
| R11 | `evaluate_observation`（文本 marker 判定） | B11-A 的「PS 内比对 + CRC marker」与 B11-C 的「采样值 marker」可复用；B11-B 二进制完整性不适用（见 N3/N7） | A/C |

### 5.2 需新增 MCP 能力（7 项）

| # | 缺口 | 一句话原因（为什么现有工具不够） | 影响子切片 |
|---|---|---|---|
| N1 | **中断连接原子**（platform_connect_interrupt(source, ps_irq_line) / query_interrupt_map） | 14 个 platform 原子中无中断连线/查询；`platform_configure_ps7` 的 irq_f2p 只能整体使能 16 线，无法把具体 PL 中断源路由到指定 IRQ_F2P 线（架构 §4.3.3 第六类规划的 API 未实现） | A（中断驱动 DMA）、C（采集完成中断） |
| N2 | **自定义 RTL 源增删/顶层切换**（pl_add_sources / pl_set_top） | `pl_create_project` 只能在创建时指定 sources/top；迭代式 RTL 开发（ADC 控制器反复改端口）无工具可加/换源；`pl_generate_system_top` 固定只实例化 BD wrapper，无法表达「wrapper + 自定义逻辑」顶层 | A（图案发生器）、C（ADC 控制器） |
| N3 | **UART 二进制/定长捕获模式** | 现有 `ps_start/wait/stop_uart_capture` 面向文本 marker 且剥离 `\x00`（B08/B09 的 xil_printf null 字节处理），直接破坏二进制载荷；`ps_read_uart` 按时长读且文本化。附注：921600 档从未实证（B08/B09 均为 115200），需 device_live 验证 + `ps_diagnose_uart_clock` 适配 | B（机读完整性 100% 门禁无法用现有文本捕获验证） |
| N4 | **DDR 缓冲区校验能力**（可选路径） | `verify_consistency` 只校验 Manifest 链 + 产物文件 SHA256，不校验「DDR 缓冲区内数据」；`ps_mem_read` 按 word 读、MB 级慢。建议 B11-A 走 PS 应用内 CRC + 文本 marker（复用 R11），此缺口为可选路径 | A |
| N5 | **自动地址分配/冲突检查原子**（assign_addresses / check_address_conflicts） | 14 个原子只有逐段 `platform_set_address`；DMA 通路寄存器段+数据段增多时手工易错（架构 §4.3.3 第七类规划未实现）。低优先级：B11-A 地址段有限可先用 set_address | A/C |
| N6 | **BSP 驱动使能/校验**（如 xaxidma 是否进入 BSP） | `ps_create_bsp` 无显式驱动配置/查询工具；`ps_set_compiler_options` 仅支持 -D 宏（B10 已知限制）。若 BSP 按 XSA 自动集成则只是 Skill 层校验问题 | A（XAxiDma）、C |
| N7 | **数值容差断言判定**（可选） | `evaluate_observation` 只做文本 marker，无「采样值在容差内」的数值判定语义；建议 B11-C 走 PS 内比较 + marker（复用），数值断言列为可选扩展 | C |

### 5.3 需扩展 Skill（7 项）

| # | 缺口 | 一句话原因 | 影响子切片 |
|---|---|---|---|
| S1 | 物理事实清单流程（S1 阶段） | GPIO Skill 无此环节（板卡固定、无外部外设）；数据采集必须显式收集用户物理事实并标注「未确认」 | C（B11-A/B 无外部事实） |
| S2 | 带宽/资源预算与档位推导（S2 阶段） | GPIO 无带宽问题；需把 §4 计算式固化为可复用的决策步骤 | B/C |
| S3 | DMA/中断的 Platform 拓扑决策框架 | 需新增 HP 口、S2MM、地址段规划、中断线分配的标准决策路径（对齐架构 §4.3.4「Skill 决定为什么这样连」） | A |
| S4 | PS 侧 DMA 驱动 + cache 一致性规范 | XAxiDma 使用模式、Xil_DCacheFlushRange/InvalidateRange 的时序与位置是工程层知识，需写入 PS 包 | A |
| S5 | 二进制流协议实现与机读自检框架 | 帧构造/解析、CRC16、序号连续性判定、速率协商的实现规范；与上位机契约落地 | B |
| S6 | 跨时钟/FIFO/时序约束知识 | ADC 采样时钟域 → PL 逻辑时钟域 → AXI-Stream 的 FIFO 设计、input delay 约束写法（PL 域扩展） | C |
| S7 | 自定义 RTL 顶层集成流程 | 「wrapper + 自定义逻辑」system_top 的写法与经 `pl_create_project` 进入工程的公开路径（配合 N2） | A/C |

**缺口统计**：可复用 11 项 / 需新增 MCP 7 项 / 需扩展 Skill 7 项，合计 25 项。

## 6. 与上位机智能体的流协议契约草案（两智能体共同冻结契约）

> 本契约是**两个智能体（Zynq 侧本框架 + 上位机侧）的共同冻结契约**：Zynq 侧按此格式上行，上位机侧按此格式解析。B11-B 门禁（机读完整性 100%）即按本契约的判定规则执行。**草案版本 v0.1，待上位机智能体确认后冻结**；冻结前任何字段可协商。

### 6.1 帧格式（little-endian，字节序明确）

```
┌──────────┬──────────┬──────────┬──────────────┬──────────┐
│ 帧头 2 B  │ 序号 4 B  │ 长度 2 B  │   载荷 P B    │ CRC16 2 B │
│ 0xAA 0x55 │ u32 seq  │ u16 len  │ 采样/像素数据  │ CCITT-FALSE│
└──────────┴──────────┴──────────┴──────────────┴──────────┘
```

- 帧头固定 `0xAA 0x55`（不含于 CRC 计算域之外亦可，两端一致即可；建议 CRC 覆盖「序号 + 长度 + 载荷」）。
- 序号：u32，从 0 起严格递增（回绕策略：超过 0xFFFFFFFF 后回绕 0 并发送专用同步帧，B11-B 捕获量级不会触发，仅写入契约）。
- 长度：u16，载荷字节数（16-bit 采样时 = 2×样本数；8-bit 像素时 = 像素数）。len = 0 为心跳帧（载荷为空，CRC 只覆盖序号+长度）。
- CRC16：CCITT-FALSE（poly 0x1021，初值 0xFFFF），字节序 little-endian 存放。
- 建议默认帧参数（B11-B 实测档）：波特率 921600，16-bit 采样每帧 256 样本（P=512 B），或灰度每帧一行（P=N）。

### 6.2 速率协商与握手

- Zynq 侧上电/复位后先发 `SYN`（专用帧：帧头 + 版本号 + 建议波特率 + 建议帧参数字段），等待上位机 `ACK` 或 `NAK(参数)`。
- 上位机 `ACK` 后进入稳态流；上位机可发起 `REQ(新参数)` 重协商（不影响已接收数据的完整性判定）。
- 推荐实现：握手使用同一帧格式 + 专用 type 字段（帧头后第 1 个字节做 type：0x01=SYN，0x02=ACK，0x03=NAK，0x04=REQ，0x05=HEARTBEAT，0x00=DATA）。此字段占用 1 B，可并入「序号/长度」之前的固定头（帧开销 H 相应调整为 11 B；§4 预算按 H=10 B 计算，为保守下限，实际开销以冻结版契约为准）。
- 超时：上位机对 SYN 的响应超时（建议 2 s）后 Zynq 侧重发（最多 3 次），仍无响应则上报流状态（不静默）。

### 6.3 完整性判定（机读 PASS/FAIL）

| 判定 | 规则 | 结果 |
|---|---|---|
| PASS | 捕获期内：序号连续（无缺号/乱序）**且** CRC16 全部通过 | 机读 `PASS` |
| FAIL | 任一帧 CRC 错，或序号断号（含握手失败、超时） | 机读 `FAIL`，并报告首个错误帧序号与类型 |
| 例外 | 心跳帧（len=0）不参与序号连续性计数；重协商期间的 REQ/ACK 帧不计数 | — |

上位机判定逻辑必须是确定性、可复现的（同一字节流 → 同一判定），这是「可机器验证的流契约」的硬要求。

### 6.4 冻结流程建议

1. 本草案 v0.1 交上位机智能体评审（字段、字节序、CRC 参数、回绕/心跳语义）；
2. 双方确认后以契约文档形式冻结（版本号入 SYN 帧）；
3. B11-B 门禁按冻结版契约执行，任何契约修改走双方共同变更记录。

## 7. 需要用户提供的物理事实清单

以下为**硬件物理事实（现实层）**，不是工程决策；由用户提供，智能体只消费并用于工程方案。任何一项缺失即阻塞对应子切片（B11-C 依赖全部；B11-A/B 无外部事实）。

| # | 物理事实 | 为什么影响工程方案（每项一句话） |
|---|---|---|
| 1 | **ADC 型号 + 数据接口类型**（SPI / 并行 / 串行 LVDS 等） | 决定 PL 控制器 RTL 形态（SPI master + CS/SCLK 时序 vs 并行总线 + data_valid 握手）、是否可用 Catalog IP（如 XADC）、引脚数量与占用 |
| 2 | **分辨率**（bit/样本） | 决定采样字宽、FIFO 与 DDR 打包宽度、每样本字节数，直接进入 §4 带宽预算公式 |
| 3 | **最大采样率**（S/s） | 决定采样时钟域设计、FIFO 深度、DMA 突发是否跟得上、UART 上行档位（对照 §4.5 判据线决定是否必须 Ethernet） |
| 4 | **通道数** | 决定通道复用/多 FIFO/时间交错打包、通道寻址字段与上位机数据布局（契约 §6 载荷语义） |
| 5 | **参考电压/量程** | 决定转换语义与上位机标定（已知输入 → 正确采样值的判据）；同时决定**电平兼容性**——AX7020 PL 扩展口为 3.3V（J11/J13），若 ADC 逻辑电平非 3.3V，电平转换属**板级物理事实**，需用户确认方案 |
| 6 | **引脚分配**（pin 配置，用户已承诺提供） | 直接生成 XDC 约束（PACKAGE_PIN / IOSTANDARD / 驱动强度），决定 PL 工程约束与布线可行性；含 ADC 时钟/数据/CS/SCLK 逐脚映射 |
| 7 | **采样时钟来源**（ADC 自带 / PL 提供）（建议项） | 决定跨时钟域设计（异步 FIFO vs 同步）、时序约束（input delay）写法 |
| 8 | **供电与参考电压来源、上电时序**（建议项） | 决定 PL 侧是否需要使能控制、采样建立时间、是否需要用户确认上电顺序 |

## 8. 立项前提与 DRAFT 声明

- 本文档为 **DRAFT**，待用户审核；不写 FROZEN/COMPLETE，不声称已立项；切片仅在用户确认本提案与 `B11_generalized_skill_design.md` 后按现有 Brick 流程（Skill / MCP / Tests 三目录逐子切片记录）立项。
- B11-A/B/C 门禁、缺口清单、流契约草案均为提案值，立项后以正式规划为准。
- 未修改任何代码、测试、skill、boards、冻结架构文档；`mcps/`、`skills/`、`boards/`、`docs/architecture_ai_zynq7020.md`、`docs/brick_development_plan.md` 冻结资产未动。
- 引用的事实（工具数、板卡参数、带宽换算）均来自仓库内文件或本文 §4 的自证计算式；桥片最大波特率（1 Mbps）来自芯片数据手册规格，板卡资料未直接标注，已标注「待实物/用户确认」。
