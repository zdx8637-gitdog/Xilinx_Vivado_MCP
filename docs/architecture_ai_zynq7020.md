# Zynq-7020 AI Agent Development Framework — Architecture v2.3.1

> 日期: 2026-08-03
> 状态: **FROZEN TOP-LEVEL** — 三域四层 + P1-P8 已冻结
> MCP API 与 Artifact Schema 在纵向切片中允许兼容演进
> 替代: v2.3 (装配收口), v2.2 (API 完整性), v1.0 (FROZEN)

---

## 0. 版本演进

v2.0 → v2.1: Workflow 与 Domain Skill 分离、Platform 去 Vivado 化、子技能扩展预留。

v2.1 → v2.2: PS/PL MCP API 从 ~10 个扩展到完整清单、Domain Skill I/O 契约、Artifact Contract 初版。

v2.2 → v2.3（本次）：基于完整架构审计，修复阻塞性问题：

| 新增项 | 说明 |
|--------|------|
| **System Assembly & Ownership** | 谁拥有 Vivado 工程、BD wrapper、RTL、bitstream？各 MCP 的写边界在哪？ |
| **Artifact Contract v2** | 不可变 manifest、schema_version、文件校验和、board profile hash、revision 计算规则 |
| **共享资源所有权模型** | Vivado 工程/JTAG hw_server 的 lease、互斥锁、session 生命周期 |
| **API 分类与幂等重定义** | query / set / command 三类，幂等仅承诺 query 和 set |
| **Board Configuration Package** | 唯一板卡数据源，含物理容量 vs 配置容量、厂商参数校验和 |

| 修正项 | 说明 |
|--------|------|
| 板卡基线数据 | DDR 512MB→1GB, QSPI 128Mbit→256Mbit, LED 2→4 (对齐厂商资料) |
| UART baud 归属 | 三层模型: Platform 设 PS7 初始值 / PS BSP 设运行期 / 观测端使用相同参数 |
| P7: JTAG-only | 从"不可变原则"改为"当前开发配置" |
| 三域定义 | 从"物理硬件区域"改为"责任域 bounded context" |
| 原子 API 调用规则 | 区分代码复用（内部）与跨 API 网络调用（禁止） |
| API 签名一致性 | context 参数在所有 API 签名中体现 |

---

## 1. 核心架构

### 1.1 四层模型

```
┌──────────────────────────────────────────────────────────────┐
│                        AI Agent                               │
│                      (Claude Code)                             │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              WORKFLOW LAYER                               │ │
│  │              (编排 + 无领域知识)                           │ │
│  │                                                          │ │
│  │  adc_workflow   ethernet_workflow   vision_workflow ...  │ │
│  │                                                          │ │
│  │  职责: Phase 分解 → Domain Skill 调度 → 跨域结果验证      │ │
│  │  不知道: Verilog 语法 / AXI 协议 / C 编译选项             │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                             │ 调用                            │
│  ┌──────────────────────────┼──────────────────────────────┐ │
│  │              DOMAIN SKILL LAYER                           │ │
│  │              (领域知识 + 标准化流程)                       │ │
│  │                                                          │ │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐     │ │
│  │  │  PS Skill     │ │Platform Skill│ │  PL Skill     │     │ │
│  │  │              │ │              │ │              │     │ │
│  │  │ ARM 软件开发  │ │PS-PL 通信设计│ │FPGA 逻辑开发  │     │ │
│  │  │              │ │              │ │              │     │ │
│  │  │ 子技能:      │ │ 子技能:      │ │ 子技能:      │     │ │
│  │  │ · baremetal  │ │ · gpio       │ │ · rtl        │     │ │
│  │  │ · dma_driver │ │ · dma        │ │ · sim        │     │ │
│  │  │ · interrupt  │ │ · interrupt  │ │ · timing     │     │ │
│  │  │ · boot       │ │ · clock_reset│ │ · ila_debug  │     │ │
│  │  │ · ...        │ │ · ddr        │ │ · ...        │     │ │
│  │  │              │ │ · axis       │ │              │     │ │
│  │  │              │ │ · ...        │ │              │     │ │
│  │  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘     │ │
│  └─────────┼────────────────┼────────────────┼─────────────┘ │
│            │ 调用            │ 调用            │ 调用          │
│  ┌─────────┼────────────────┼────────────────┼─────────────┐ │
│  │         ↓                ↓                ↓              │ │
│  │                   MCP LAYER                               │ │
│  │                   (原子 API)                               │ │
│  │                                                          │ │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐     │ │
│  │  │  PS MCP       │ │Platform MCP  │ │  PL MCP       │     │ │
│  │  │              │ │              │ │              │     │ │
│  │  │ 每个 API     │ │ 每个 API     │ │ 每个 API     │     │ │
│  │  │ = 一个完整   │ │ = 一个完整   │ │ = 一个完整   │     │ │
│  │  │ 硬件操作     │ │ 硬件操作     │ │ 硬件操作     │     │ │
│  │  │              │ │              │ │              │     │ │
│  │  │ 内部封装     │ │ 内部封装     │ │ 内部封装     │     │ │
│  │  │ EDA 细节     │ │ EDA 细节     │ │ EDA 细节     │     │ │
│  │  └──────────────┘ └──────────────┘ └──────────────┘     │ │
│  │                                                          │ │
│  └──────────────────────────────────────────────────────────┘ │
│                             │                                  │
│  ┌──────────────────────────┼──────────────────────────────┐ │
│  │              EDA PROCESS LAYER                            │ │
│  │              (Vivado / XSCT / XSim 进程管理)             │ │
│  │                                                          │ │
│  │  职责: 启动/停止/通信/超时/清理                            │ │
│  │  不知道: 任何 FPGA 业务逻辑                                │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 各层职责边界

```
Workflow:   "做什么项目、分几个阶段、各阶段调用哪个 Domain Skill"
            不知道: 任何具体技术知识

Domain:     "这个领域怎么做、有哪些标准模式、如何判断结果"
            不知道: EDA 工具的 Tcl 语法、进程管理

MCP:        "怎么操作硬件 / 生成文件"
            不知道: 工程流程、决策逻辑

EDA:        "怎么让外部程序干活"
            不知道: 上面三层在做什么
```

### 1.3 Zynq-7020 硬件三域（不变）

```
                         XC7Z020CLG400-2
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ┌───────────────────────────┐  ┌────────────────────────────┐  │
│  │  Domain A: ARM PS          │  │  Domain C: FPGA PL          │  │
│  │                            │  │                              │  │
│  │  ARM Cortex-A9 ×2 @667MHz │  │  Artix-7 Fabric             │  │
│  │  DDR / UART / SPI / I2C   │  │  LUT / FF / BRAM / DSP      │  │
│  │  GPIO / GEM / SDIO / USB  │  │                              │  │
│  │  SCU / GIC / L2 Cache     │  │                              │  │
│  └─────────────┬──────────────┘  └─────────────┬────────────────┘  │
│                │                                │                   │
│                └──────────┬─────────────────────┘                   │
│                           │                                         │
│  ┌────────────────────────┴────────────────────────────────────┐   │
│  │  Domain B: Interconnect (PS ↔ PL 通信层)                     │   │
│  │                                                              │   │
│  │  AXI GP0/1 (32b)  PS→PL 控制    │  IRQ_F2P[15:0] 中断       │   │
│  │  AXI HP0-3 (64b)  PL→DDR 高速   │  FCLK[3:0]     时钟       │   │
│  │  AXI ACP  (64b)   PL↔Cache      │  RESETN[3:0]   复位       │   │
│  │  DMA / Stream / EMIO / XADC     │  地址映射      DDR 共享    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                           │                                         │
│                           ↓                                         │
│                   DDR3 (512MB shared)                               │
└─────────────────────────────────────────────────────────────────┘
```

三个域是**责任域 (bounded context)**，不是三个互斥的物理硬件区域。

> 例如 AXI DMA、AXI SmartConnect、AXI INTC 这些 IP 核**物理上消耗 PL 资源**（LUT/FF），但它们的**设计责任属于 Platform 域**——因为它们的存在是为了 PS-PL 通信结构，而不是实现 PL 的功能逻辑。

三个域的职责边界由**设计责任**决定，不由 EDA 工具决定：

| | Domain A: PS | Domain B: Interconnect | Domain C: PL |
|---|---|---|---|
| **职责** | ARM 软件 | PS ↔ PL 通信结构 | FPGA 逻辑 |
| **操作对象** | Cortex-A9、DDR、外设 | AXI 通路、中断、时钟 | LUT/FF/BRAM/DSP |
| **产出物** | ELF、C 代码 | 通信拓扑、地址映射 | Bitstream、RTL |
| **当前用到的 EDA 工具** | XSCT, XSDB | Vivado IPI | Vivado, XSim |

> EDA 工具列在底部作为"当前如何实现"的参考，不是架构定义的一部分。换 EDA 工具版本时，MCP 是承担版本差异的主要层——架构目标是 Workflow 和 Domain Skill 无需修改，但这是接口兼容性目标，不是绝对保证。

---

## 2. Workflow 层

### 2.1 定义

Workflow 不是第四个域。它是**跨域的工程编排层**。

```
Workflow:  我知道一个 ADC 采集系统需要:
            Phase 1 → Platform Skill (建立 DMA 通路)
            Phase 2 → PL Skill (开发采集逻辑)
            Phase 3 → PS Skill (开发读取程序)
            Phase 4 → 端到端 JTAG 验证

           但我不知道:
           - Platform Skill 内部怎么做 DMA 配置
           - PL Skill 怎么写 Verilog
           - PS Skill 怎么编译 ELF

           我只知道: 谁做、什么顺序、什么算成功
```

### 2.2 Workflow 与 Domain Skill 的关系

```
adc_workflow:
  │
  ├─ Phase 1: "建立 ADC 数据通路"
  │   └─→ 调用 Platform Skill (委托, 不干预内部细节)
  │       返回: DMA 通路就绪, 地址映射表
  │
  ├─ Phase 2: "开发 FPGA 采集逻辑"
  │   └─→ 调用 PL Skill (委托)
  │       返回: bitstream, 仿真 PASS
  │
  ├─ Phase 3: "开发 ARM 读取程序"
  │   └─→ 调用 PS Skill (委托)
  │       返回: ELF
  │
  └─ Phase 4: "端到端验证"
      ├─ pl_program_device(bitstream)   ← 直接调用 MCP (无需经过 Skill)
      ├─ ps_download_elf(elf)           ← 直接调用 MCP
      └─ ps_read_uart() → "ADC DMA OK ✅"
```

### 2.3 Workflow 直接调用 MCP 的限制

Workflow 可以调用 Domain Skill（委托完整子流程），也可以直接调用 MCP API——但**仅限以下三类操作**：

| 允许直调 | API 示例 | 原因 |
|----------|---------|------|
| **部署产物** | `pl.program(bitstream)`, `ps.download(elf)` | 不涉及设计决策，仅执行已生成的产物 |
| **状态控制** | `ps.run()`, `ps.halt()`, `ps.reset()` | 运行期目标控制，不修改设计 |
| **结果观测** | `ps.read_uart()`, `ps.mem_read()`, `pl.get_device_status()` | 只读验证，无副作用 |

**任何包含领域设计决策的操作，必须委托对应 Domain Skill**：

```
❌ Workflow 直接调用:          ✅ 委托 Domain Skill:
   platform.add_ip(...)          Platform Skill (完整通路设计)
   platform.connect_interface()  Platform Skill
   platform.assign_addresses()   Platform Skill
   ps.create_app()               PS Skill (完整软件开发)
   ps.create_bsp()               PS Skill
   pl.synthesize()               PL Skill (完整 FPGA 构建)
```

**道理**：如果 Workflow 可以直接编排细粒度 MCP API，Domain Skill 就失去了存在意义——Workflow 变成了 Skill。这条规则保证 Workflow 始终是"调度者"，而不是"执行者"。

### 2.4 Workflow 示例

```
workflows/
├── adc_dma/              ← ADC + DMA 采集系统
├── ethernet_stream/      ← 以太网流式传输
├── gpio_control/         ← GPIO 寄存器控制 (最简单的跨域入门)
├── vision_pipeline/      ← 图像采集 + 处理
└── template/             ← 新建 Workflow 的模板
```

每个 Workflow 是一个独立目录，包含：

```
adc_dma/
├── WORKFLOW.md           ← Phase 定义 + 调用关系
├── platform_config/      ← 平台配置 (调用 Platform Skill 的输入)
├── expected_outputs/     ← 预期结果 (UART 输出、寄存器值)
└── README.md             ← 这个 Workflow 做什么
```

---

## 3. Domain Skill 层

### 3.1 设计原则

```
Domain Skill:
  知道自己领域的一切工程知识
  不实现底层操作 (那是 MCP 的事)
  不编排跨域流程 (那是 Workflow 的事)

子技能:
  一个 Domain Skill 不是一个大文件
  是多个独立子技能的集合
  每个子技能 = 一种标准工程模式
```

### 3.2 Domain Skill 统一输入输出契约

Workflow 与 Domain Skill 之间不能仅靠概念性关系。每个 Domain Skill 必须遵循统一的输入输出格式：

**输入**：

```json
{
  "requirement": "建立 ADC → DMA → DDR 数据通路",
  "project_context": {
    "project_dir": "projects/examples/adc_dma_system",
    "part": "xc7z020clg400-2",
    "platform_name": "ax7020_base"
  },
  "existing_artifacts": {
    "hardware_description": "adc_platform.xsa",
    "manifest": "adc_platform.json"
  },
  "constraints": {
    "dma_channels": 1,
    "data_width": 32,
    "fclk_mhz": 100
  },
  "acceptance_criteria": {
    "validation": "platform.validate() must pass",
    "address_map": "no conflicts"
  }
}
```

**输出**：

```json
{
  "status": "success" | "failed" | "partial",
  "artifacts": {
    "hardware_description": "adc_platform.xsa",
    "manifest": "adc_platform.json"
  },
  "design_state": {
    "address_map": { "dma0": "0x40400000", "gpio0": "0x41200000" },
    "interrupt_map": { "dma0_s2mm": 0 },
    "clock_tree": { "FCLK_CLK0": ["dma0", "gpio0"] }
  },
  "validation": {
    "passed": true,
    "checks": { "connectivity": "pass", "address": "pass", "clock": "pass" },
    "warnings": []
  },
  "decisions": [
    "使用 HP0 而非 ACP (数据量大, 无需 cache-coherent)",
    "使用 IRQ_F2P[0] 而非轮询 (实时性要求)"
  ],
  "resume_from": null
}
```

**关键字段说明**：

| 字段 | 谁设 | 含义 |
|------|------|------|
| `decisions` | Domain Skill | **必须输出**。AI 做了哪些设计选择、为什么。这是 Skill 的核心价值——不是 "改了什么"，而是 "为什么这样改" |
| `resume_from` | Domain Skill | 失败时返回可恢复的断点。`null` 表示从头开始。非 `null` 时 Workflow 可以跳过已完成阶段 |
| `design_state` | Domain Skill | 供下游 Phase 读取。PS Skill 从这里取 `address_map` 生成 C 代码；PL Skill 从这里取 `clock_tree` 做约束 |

### 3.3 PS Skill — ARM 软件域

```
ps_skill/
├── SKILL.md                  ← 总入口: ARM 开发知识体系
│
├── baremetal/                ← Bare-metal 开发
│   ├── SKILL.md              ← 裸机程序标准流程
│   ├── uart_hello.md         ← 最小 UART 打印程序
│   └── linker_guide.md       ← Linker Script 配置
│
├── dma_driver/               ← DMA 驱动
│   ├── SKILL.md              ← XAxiDma 标准使用模式
│   ├── simple_transfer.md    ← 单次传输
│   ├── cyclic_transfer.md    ← 循环传输
│   └── interrupt_mode.md     ← 中断驱动模式
│
├── interrupt/                ← 中断处理
│   ├── SKILL.md              ← GIC 配置 + 中断服务函数
│   ├── pl_to_ps.md           ← PL→PS 中断处理
│   └── nested_irq.md         ← 嵌套中断
│
├── peripheral/               ← 外设驱动
│   ├── gpio.md
│   ├── uart.md
│   ├── spi.md
│   └── i2c.md
│
└── references/
    ├── ax7020_memory_map.md  ← 地址映射参考
    ├── bsp_config.md         ← BSP 配置项
    └── xparameters_guide.md  ← xparameters.h 解读
```

### 3.4 Platform Skill — PS↔PL 通信结构设计

```
platform_skill/
├── SKILL.md                  ← 总入口: PS-PL 通信设计方法论
│
├── gpio_control/             ← 寄存器控制通路 (AXI GPIO)
│   ├── SKILL.md              ← "PS 通过 AXI GPIO 控制 PL 寄存器"
│   └── address_decode.md     ← 地址译码设计
│
├── dma_stream/               ← DMA 数据通路 (AXI DMA)
│   ├── SKILL.md              ← "PL 通过 DMA 写 DDR, PS 读取"
│   ├── s2mm.md               ← Stream→Memory Map (PL→DDR)
│   ├── mm2s.md               ← Memory Map→Stream (DDR→PL)
│   └── bd_ring.md            ← Buffer Descriptor Ring 配置
│
├── interrupt_routing/        ← 中断路由
│   ├── SKILL.md              ← "PL IP → AXI INTC → IRQ_F2P → GIC → ARM"
│   └── priority_guide.md     ← 中断优先级设计
│
├── clock_reset/              ← 时钟与复位
│   ├── SKILL.md              ← "PS FCLK → PL 时钟域规划"
│   └── domain_crossing.md    ← 跨时钟域设计
│
├── ddr_sharing/              ← DDR 共享策略
│   ├── SKILL.md              ← "PS 和 PL 如何共享 512MB DDR"
│   └── cache_coherency.md    ← ACP 缓存一致性模式
│
├── axis_stream/              ← AXI Stream 流式接口
│   ├── SKILL.md              ← "PL 内部 Stream 互联"
│   └── fifo_buffer.md        ← Stream FIFO 缓冲设计
│
└── references/
    ├── ps7_config_full.md    ← PS7 全部配置项
    └── axi_topology.md       ← AXI 互联拓扑参考
```

**关于 "Platform" 的命名与描述**：

Platform Skill 的职责是 "PS 和 PL 之间的通信结构设计"，它的操作对象是 AXI 通路、中断、时钟、复位、地址映射、DDR 共享。目前我们用 Vivado IP Integrator (Block Design) 来实现它——但架构层面不绑定这个工具。以后可以用纯 RTL 实现 AXI Interconnect，Platform Skill 的职责不变。

类似地，"XSA 文件"是 Vivado 特有的硬件描述格式。架构层面不把它作为核心概念——它是 Platform MCP 当前实现中，"导出通信拓扑信息给 PS MCP 和 PL MCP"的一种手段。换成其他格式（JSON/YAML/自定义描述），不影响上层。

### 3.5 PL Skill — FPGA 逻辑域

```
pl_skill/
├── SKILL.md                  ← 总入口: FPGA 开发知识体系
│
├── rtl_design/               ← RTL 设计
│   ├── SKILL.md              ← Verilog/SystemVerilog 编码规范
│   ├── fsm_pattern.md        ← 状态机模板
│   ├── pipeline_pattern.md   ← 流水线模板
│   └── cdc_pattern.md        ← 跨时钟域模板
│
├── simulation/               ← 仿真验证
│   ├── SKILL.md              ← Testbench 编写规范
│   ├── assertion_style.md    ← 断言风格
│   └── waveform_analysis.md  ← 波形分析
│
├── build/                    ← 综合与实现
│   ├── SKILL.md              ← 综合/布局/布线流程
│   ├── timing_closure.md     ← 时序收敛策略
│   └── resource_opt.md       ← 资源优化策略
│
├── debug/                    ← 硬件调试
│   ├── SKILL.md              ← ILA 在线调试
│   └── jtag_verify.md        ← JTAG 硬件验证
│
└── references/
    ├── xc7z020_resource.md   ← 芯片资源参考
    └── constraint_guide.md   ← XDC 约束指南
```

---

## 4. MCP 层

### 4.1 MCP = 原子 API，不是裸露 Tool

每个 MCP API 保证执行成功时满足一个明确的后置条件。API 内部封装 EDA 进程调用、Tcl 生成、错误恢复——但对外暴露的是结构化接口，不是 EDA 命令序列。

```
MCP 暴露的是 原子 API — 一个 API = 一个完整的硬件操作。

内部封装:
  - EDA 进程调用
  - Tcl 脚本生成
  - 错误恢复
  - 结果解析

对外:
  输入 = (context, 高层参数)   // context = {session_id, board_id, project_path}
  输出 = 结构化结果 (JSON)      // 含 operation_id, 变更摘要, 错误信息
```

### 4.2 PS MCP — ARM 软件域

> **范围**: 仅限 JTAG 开发路径。BOOT.BIN / QSPI Flash 烧写属于生产部署，由开发者人工接管 (P7)。

PS MCP 操作 ARM Cortex-A9 的软件生命周期：从硬件导入、BSP 创建、编译链接、JTAG 下载调试、到运行时观测。**它不包含任何硬件连接设计决定**——时钟频率、地址映射、中断号全部来自 Platform MCP 导出的 manifest。

#### 硬件连接与目标管理

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.connect_hw_server()` | 连接 JTAG hw_server | XSCT connect |
| `ps.disconnect_hw_server()` | 断开 JTAG 连接 | XSCT disconnect |
| `ps.list_targets()` | 列出 JTAG 链上所有目标 | XSCT targets |
| `ps.select_target(id)` | 选择 ARM 目标 (DAP) | XSCT target -set |
| `ps.get_target_status()` | 查询目标状态 (running/halted/reset) | XSCT targets -target-properties |
| `ps.get_device_info()` | 查询 ARM DAP 状态 (IDCODE, CTI/CTM 寄存器, 目标状态)。注意: FPGA DONE pin 属于 PL MCP (`pl.get_device_status()`) | XSDB device properties |

#### 硬件平台与 BSP 生命周期

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.import_hardware(xsa_path, manifest_path?)` | 从 XSA 导入硬件描述创建 xparameters.h。可选 manifest 用于校验和与 revision 验证。注意：当前 XSCT/Vitis 原生 BSP 创建以 XSA 为输入格式，manifest 是 Agent 友好的补充摘要，不是 XSA 的替代 | XSCT import_hw |
| `ps.create_platform(name, hardware, domain_config)` | 创建软件平台 + domain (standalone / freertos / linux) | XSCT platform |
| `ps.create_bsp(platform, bsp_config)` | 生成 Board Support Package | XSCT bsp |
| `ps.update_hardware(xsa_path)` | 硬件平台变更后更新 BSP + xparameters.h。以 XSA 为主输入，manifest 用于 revision 校验 | XSCT update_hw |
| `ps.get_bsp_status()` | 查询 BSP 配置状态、外设驱动列表 | XSCT bsp -status |

> **UART baud 的三层模型**：
> 1. **Platform 层 (PS7 preset)**：Zynq PS7 配置中有 `PCW_UART1_BAUD_RATE=115200`，设置 UART 控制器的**硬件初始值**。由 Platform MCP 的 `configure_ps7()` 写定，导出到 manifest。这是硬件的默认波特率。
> 2. **PS 软件层 (BSP/驱动)**：PS MCP 从 manifest 读取 initial baud 配置 BSP，但 ARM 端 UART 驱动初始化时可以覆盖为不同值。驱动最终决定运行期波特率。
> 3. **观测端 (ps.read_uart)**：串口读取工具必须使用与**运行时驱动实际配置相同的波特率**。Manifest 里同时记录 initial baud 和 observed baud，避免不匹配。
>
> **一个参数，三个消费者，必须一致**。不一致时 Workflow 报错。

#### 应用工程与编译

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.create_app(name, platform, template?)` | 创建 ARM 应用工程 (bare-metal / freertos) | XSCT app create |
| `ps.add_sources(files)` | 添加 C/H 源文件 | XSCT app config |
| `ps.set_compiler_options(opts)` | 设置编译/链接选项 | XSCT app config |
| `ps.compile()` | 编译 → 链接 → ELF | XSCT build |
| `ps.get_build_status()` | 查询编译状态 + 警告/错误 | XSCT build -status |
| `ps.read_elf_info(elf_path)` | 读取 ELF 段信息 (入口地址, text/data/bss 布局) | objdump / readelf |

#### JTAG 下载与目标控制

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.download(elf_path)` | JTAG 下载 ELF 到 DDR | XSDB dow |
| `ps.reset(scope)` | 复位。scope = `processor` / `system` | XSDB rst / rst -processor |
| `ps.initialize(manifest)` | 执行 PS 初始化序列 (PS7 init Tcl) | XSDB ps7_init |
| `ps.run(core?)` | 启动处理器执行 | XSDB con |
| `ps.halt(core?)` | 暂停处理器 | XSDB stop |
| `ps.step(core?)` | 单步执行 | XSDB stp |
| `ps.wait_for_state(state, timeout)` | 等待 halted/running 状态 | XSDB poll state |

#### 调试

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.debug_start(elf, target)` | 开始调试会话，返回 debug_session_id | XSDB debug session |
| `ps.breakpoint_add(addr_or_symbol)` | 设置断点 | XSDB bpadd |
| `ps.breakpoint_remove(id)` | 移除断点 | XSDB bpremove |
| `ps.read_register(name)` | 读取 CPU 寄存器 (r0-r15, sp, lr, pc, cpsr) | XSDB rrd |
| `ps.write_register(name, value)` | 写 CPU 寄存器 | XSDB rwr |
| `ps.stack_trace()` | 获取调用栈 | XSDB backtrace |
| `ps.debug_close(debug_session_id)` | 关闭调试会话 | XSDB disconnect |

#### 运行时观测

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `ps.read_uart(port, baud, duration_ms?)` | 读取 PS UART 输出 | 串口读取 |
| `ps.write_uart(port, data)` | 写入 PS UART | 串口写入 |
| `ps.reg_read(address)` | 读内存映射外设寄存器 | XSDB mrd |
| `ps.reg_write(address, value)` | 写内存映射外设寄存器 | XSDB mwr |
| `ps.mem_read(address, length)` | 读 DDR 内存块 | XSDB mrd |
| `ps.mem_write(address, data)` | 写 DDR 内存块 | XSDB mwr |

#### 目标恢复

这是 JTAG 开发最频繁出问题的场景——DAP 错误、目标失联、残留调试会话。

| API | 做什么 |
|-----|--------|
| `ps.recover_target(strategy)` | 自动恢复目标连接。strategy = `auto` (halt→processor_reset→core_reset→init→verify) 或自定义 cascade |
| `ps.reconnect_target()` | 重新连接到已打开的 JTAG 目标 |
| `ps.clear_debug_session()` | 清除残留调试器状态 |
| `ps.diagnose_dap()` | 诊断 DAP 状态并报告可能的原因 |

`recover_target("auto")` 内部 cascade：
```
1. halt target
2. processor reset
3. core reset
4. system reset
5. PS7 init (从 manifest 读取 init sequence)
6. verify state → halted
```
每一步失败时返回当前阶段和状态，供 Workflow 决定下一步。

#### API 统计

| 类别 | 数量 |
|------|------|
| 硬件连接与目标管理 | 6 |
| 硬件平台与 BSP | 5 |
| 应用工程与编译 | 6 |
| JTAG 下载与目标控制 | 7 |
| 调试 | 7 |
| 运行时观测 | 6 |
| 目标恢复 | 4 |
| **合计** | **~41** |

### 4.3 Platform MCP — 连接层

#### 4.3.1 定位

Platform MCP 负责把 AI Agent 提出的 "PS 和 PL 应该怎样连接" 转换成 Vivado IP Integrator (IPI) 可以执行的硬件连接配置。

```
AI Agent: "我要一条 DMA 通道从 PL 到 DDR"
    ↓
Platform Skill: 决定拓扑 (S2MM 模式, HP0, FCLK0=100MHz, IRQ_F2P[0])
    ↓
Platform MCP:  结构化 API 调用
    ↓
Vivado IPI Tcl: create_bd_cell / connect_bd_intf_net / assign_bd_address / ...
    ↓
Block Design:   IP 实例 + 连线 + 地址映射
```

**Platform MCP 负责**：创建连接结构、配置连接结构、检查连接结构、查询连接结构、导出连接结果。

**Platform MCP 不负责**：写 ARM C 程序、写 FPGA Verilog、编译 ELF、综合/布局布线 PL、JTAG 烧录。

#### 4.3.2 API 设计原则：通用为主，快捷为辅

Base API（通用，覆盖所有 IP 类型）永远存在。快捷 API 是可选的便利层，内部必须复用 Base API，不得绕过。

```
✅ 正确:
   platform.add_dma_channel(dir=S2MM, width=32)
       └─→ 内部调用 platform.add_ip("axi_dma") + platform.set_ip_properties(...)

❌ 错误:
   每加一种 IP 都新增一个 MCP Tool
   platform.add_axi_dma, platform.add_axi_gpio, platform.add_axi_timer,
   platform.add_axis_fifo, platform.add_clock_wizard, platform.add_...  ← 失控
```

#### 4.3.3 完整 API 清单（八类）

> 总计数: ~46 个 API（含快捷 API）。所有 API 第一个参数为 `context` 对象。

**第一类：Design 生命周期管理**

| API | 做什么 | 幂等性 |
|-----|--------|--------|
| `platform.create_design(name, part)` | 创建 IPI 子系统 (.bd)，关联 FPGA 型号 | 已存在→返回已有对象 |
| `platform.open_design(name)` | 打开已有设计 | 不存在→报明确错误 |
| `platform.save_design()` | 保存当前设计 | 多次调用安全 |
| `platform.close_design()` | 关闭设计 | 已关闭→无操作 |
| `platform.get_status()` | 返回当前设计名、是否修改、IP 数量 | 纯查询 |

**第二类：PS7 硬件接口配置**

| API | 做什么 |
|-----|--------|
| `platform.add_ps7(preset_or_config)` | 实例化并配置 Zynq PS7。接受板卡 preset 名或完整配置 dict |
| `platform.configure_ps7(config)` | 修改 PS7 配置。可部分更新，只传要改的字段 |

PS7 配置 dict:

```json
{
  "m_axi_gp0": true,
  "m_axi_gp1": false,
  "s_axi_hp0": true,
  "s_axi_hp1": false,
  "s_axi_acp": false,
  "fclk0_mhz": 100,
  "fclk1_mhz": 0,
  "irq_f2p": true,
  "uart1": {"enable": true, "io": "MIO 48..49"},
  "ddr": "MT41K256M16RE-125"
}
```

> PS7 配置同时设置**硬件引脚连接**（`uart1.enable`, `io = "MIO 48..49"`）和**控制器初始参数**（`baud = 115200`——这是 PCW_UART1_BAUD_RATE，PS7 IP 配置的一部分）。UART baud 是一个参数、三个层面：Platform 设 PS7 硬件初始值、PS BSP/驱动读取并应用（可覆盖）、观测端使用相同参数。详见 PS MCP 章节的三层模型。

**第三类：IP 管理**

| API | 做什么 | 幂等性 |
|-----|--------|--------|
| `platform.add_ip(vlnv, instance_name, properties?)` | 从 IP Catalog 实例化任意 IP | 同名已存在→比对配置，一致返回 OK，不一致报差异 |
| `platform.set_ip_properties(instance_name, properties)` | 修改已有 IP 的属性 | 只改传入字段 |
| `platform.remove_ip(instance_name)` | 删除 IP | 不存在→无操作 |
| `platform.list_ips(filter?)` | 列出当前设计中所有 IP 及属性 | 纯查询 |

快捷 API（内部必须调用 `add_ip`）：

| 快捷 API | 等价于 |
|----------|--------|
| `platform.add_axi_dma(name, config)` | `add_ip("xilinx.com:ip:axi_dma:7.1", name)` + 配置 |
| `platform.add_axi_gpio(name, config)` | `add_ip("xilinx.com:ip:axi_gpio:2.0", name)` + 配置 |
| `platform.add_axi_interrupt_controller(name, config)` | `add_ip("xilinx.com:ip:axi_intc:4.1", name)` + 配置 |
| `platform.add_processor_reset(name)` | `add_ip("xilinx.com:ip:proc_sys_reset:5.0", name)` + 配置 |
| `platform.add_smartconnect(name, config?)` | `add_ip("xilinx.com:ip:smartconnect:1.0", name)` + 配置 |

**第四类：自定义 RTL 模块接入**

这是 Platform MCP 的关键缺口——不仅需要连接 Catalog IP，还必须能把 PL Skill 开发的自定义 Verilog 模块接进系统。

| API | 做什么 |
|-----|--------|
| `platform.add_module_reference(name, source_path, hdl_type?)` | 将自定义 RTL 模块注册为可被连接的设计对象。hdl_type = `verilog` / `vhdl` / `systemverilog` |
| `platform.refresh_module(name)` | RTL 源码变更后刷新模块接口（端口列表、参数） |
| `platform.list_module_interfaces(name)` | 查询模块暴露的 AXI 接口和信号端口 |
| `platform.create_interface_port(name, mode, protocol)` | 为当前设计创建 AXI 接口端口 (外部引脚) |
| `platform.create_signal_port(name, direction, width?)` | 为当前设计创建单信号端口 |

**第五类：接口和信号连接**

| API | 做什么 |
|-----|--------|
| `platform.connect_interface(source, destination)` | 连接 AXI 总线接口 |
| `platform.connect_signal(source, destination)` | 连接单线信号 |
| `platform.disconnect(target)` | 断开接口或信号连接 |
| `platform.make_external(port, name?)` | 将 IP 端口设为外部引脚 |
| `platform.query_connections(filter?)` | 查询当前所有连接，返回结构化列表 |

**第六类：时钟、复位、中断**

| API | 做什么 |
|-----|--------|
| `platform.connect_clock(source, targets[])` | PS FCLK → 一组 IP 时钟输入。自动检查频率匹配 |
| `platform.connect_reset(source, targets[])` | 自动处理复位极性并连接各 IP |
| `platform.connect_interrupt(source, ps_irq_line)` | PL IP 中断 → IRQ_F2P 指定线号。自动检查冲突 |
| `platform.query_clock_tree()` | 当前时钟拓扑 |
| `platform.query_reset_tree()` | 当前复位拓扑 |
| `platform.query_interrupt_map()` | 当前中断映射 |

**第七类：地址空间管理**

| API | 做什么 |
|-----|--------|
| `platform.assign_addresses(master?)` | 自动分配地址。可指定 master（如 PS GP0） |
| `platform.set_address(segment, base_address, size?)` | 显式指定某个 slave segment 的基地址和大小。segment 格式: `ip_name/port_name` |
| `platform.get_address_space(master)` | 查询某个 master 的完整地址空间 |
| `platform.exclude_range(start, size)` | 排除地址段 |
| `platform.check_address_conflicts()` | 检测地址重叠 |
| `platform.query_address_map()` | 结构化返回完整地址映射表 |

> 地址配置不仅是基地址。完整的地址配置包括：属于哪个 Master、Slave Segment、Range、Offset。`set_address(segment, base, size)` 的三参数设计覆盖了这些维度。`get_address_space(master)` 可以一次获取某个 Master 的全部地址布局，供 PS Skill 生成 xparameters.h 等效内容。

**第八类：验证、拓扑查询、产物导出**

```
验证:
  platform.validate()              ← 全量验证
  platform.check_unconnected()     ← 仅检查未连接端口

拓扑查询:
  platform.query_topology()        ← 一次性返回全量结构化 JSON (见 4.3.4 示例)
  platform.list_ips(filter?)       ← IP 清单及属性
  platform.query_connections()     ← 全部连线

产物导出:
  platform.generate_wrapper()      ← 生成 HDL 顶层壳
  platform.generate_outputs()      ← 生成 IP 输出产物
  platform.export_hardware(path)   ← 导出硬件描述 (当前实现: XSA)
  platform.export_manifest(path)   ← 导出结构化 JSON 契约
```

**结构化拓扑查询**（`platform.query_topology()` 返回示例）：

```json
{
  "design_name": "ax7020_adc",
  "part": "xc7z020clg400-2",
  "ps": {
    "instance": "processing_system7_0",
    "gp_ports": {"M_AXI_GP0": true, "M_AXI_GP1": false},
    "hp_ports": {"S_AXI_HP0": true},
    "clocks": {"FCLK_CLK0": 100000000},
    "interrupts": {"IRQ_F2P": {"lines": 16, "used": [0]}}
  },
  "ips": [
    {
      "name": "axi_dma_0",
      "vlnv": "xilinx.com:ip:axi_dma:7.1",
      "properties": {"c_include_s2mm": true, "c_s2mm_data_width": 32},
      "addresses": {"S_AXI_LITE": "0x40400000"}
    },
    {
      "name": "axi_gpio_0",
      "vlnv": "xilinx.com:ip:axi_gpio:2.0",
      "properties": {"c_gpio_width": 4},
      "addresses": {"S_AXI": "0x41200000"}
    }
  ],
  "connections": [
    {"type": "axi",  "from": "processing_system7_0/M_AXI_GP0", "to": "axi_dma_0/S_AXI_LITE"},
    {"type": "axi",  "from": "axi_dma_0/M_AXI_S2MM",         "to": "processing_system7_0/S_AXI_HP0"},
    {"type": "clk",  "from": "processing_system7_0/FCLK_CLK0", "to": "axi_dma_0/s_axi_lite_aclk"},
    {"type": "irq",  "from": "axi_dma_0/s2mm_introut",       "to": "processing_system7_0/IRQ_F2P[0]"}
  ],
  "unconnected_ports": ["axi_dma_0/mm2s_introut"],
  "validated": true,
  "warnings": []
}
```

> `platform.export_manifest()` 是建议你们自己生成的 JSON 契约文件。它和 XSA 的关系是：XSA 是 Vivado 原生格式（供 Vitis/XSCT 消费），Manifest 是 AI Agent 友好的结构化摘要（供 Skill 和 Workflow 读取，避免反复查询 MCP）。两者不互相替代。

#### 4.3.4 Skill 与 MCP 分工：以 DMA 为例

```
Platform Skill (设计决策)           Platform MCP (执行)
─────────────────────────          ────────────────────
"需要 S2MM 模式 DMA"              platform.add_axi_dma(name="dma0", {
"数据宽度 32-bit"                    mode: "s2mm",
"走 HP0 进 DDR"                     data_width: 32,
"用 GP0 配寄存器"                   })
"中断接 IRQ_F2P[0]"              
"FCLK0 给 100MHz"                 platform.connect_interface(
                                     "ps/M_AXI_GP0",
                                     "smartconnect_0/S00_AXI")
                                   
                                   platform.connect_interface(
                                     "smartconnect_0/M00_AXI",
                                     "dma0/S_AXI_LITE")
                                   
                                   platform.connect_interface(
                                     "dma0/M_AXI_S2MM",
                                     "ps/S_AXI_HP0")

                                   platform.connect_clock(
                                     "ps/FCLK_CLK0",
                                     ["dma0/s_axi_lite_aclk",
                                      "dma0/m_axi_s2mm_aclk"])

                                   platform.connect_interrupt(
                                     "dma0/s2mm_introut",
                                     ps_irq_line=0)

                                   platform.assign_addresses()
                                   platform.validate()
                                   platform.export_hardware("adc_platform.xsa")
```

Skill 决定**为什么这样连**。MCP 只负责**执行连接**。底层适配器把 MCP 调用转成 Tcl——这个转换对上层完全不可见。

### 4.4 PL MCP — FPGA 逻辑域

PL MCP 负责纯 FPGA 逻辑开发的全部操作：工程管理、仿真、综合、实现、时序分析、bitstream 生成、JTAG 烧录、ILA 调试。**它不包含任何硬件连接设计决定**——PL 用什么时钟、DMA 接口怎样接、地址如何映射，全部来自 Platform MCP 导出的 manifest。

#### 工程与源文件管理

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `pl.create_project(name, part, sources, constraints)` | 创建 Vivado 工程 | Vivado create_project |
| `pl.open_project(path)` | 打开已有工程 | Vivado open_project |
| `pl.close_project()` | 关闭工程 | Vivado close_project |
| `pl.add_sources(files, type?)` | 添加 RTL/仿真/约束源文件 | Vivado add_files |
| `pl.remove_sources(files)` | 移除源文件 | Vivado remove_files |
| `pl.set_top(module)` | 设置顶层模块 | Vivado set_property top |
| `pl.update_compile_order()` | 重新扫描依赖并更新编译顺序 | Vivado update_compile_order |
| `pl.get_project_status()` | 查询工程状态 (top, 文件数, 编译顺序, 状态) | Vivado report |

#### 仿真

`pl.simulate()` 是完整仿真流程的高层 API，内部走 xvlog → xelab → xsim 三步。

| API | 做什么 |
|-----|--------|
| `pl.simulate(sim_sources, tb_top, options?)` | 完整仿真流程 → 断言报告 + 波形路径 |

**输入**：
```json
{
  "design_sources": ["rtl/adc_controller.v", "rtl/dma_streamer.v"],
  "sim_sources": ["sim/tb_adc_top.sv"],
  "tb_top": "tb_adc_top",
  "generics": {"DATA_WIDTH": 32, "SIM_MODE": true},
  "runtime_us": 100000,
  "timeout_s": 120
}
```

**输出**：
```json
{
  "compile_status": "pass",
  "elaborate_status": "pass",
  "assertions": {
    "passed": 12, "failed": 0,
    "details": [
      {"name": "dma_tvalid_check", "status": "pass"},
      {"name": "adc_data_valid", "status": "pass"}
    ]
  },
  "log_path": "sim/xsim.log",
  "waveform_path": "sim/dump.vcd",
  "warnings": []
}
```

#### 综合与实现

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `pl.synthesize(directive?)` | RTL 综合 | Vivado synth_design |
| `pl.place(directive?)` | 布局 | Vivado place_design |
| `pl.route(directive?)` | 布线 | Vivado route_design |
| `pl.place_and_route(directive?)` | 布局布线联合 | Vivado place + route |
| `pl.run_drc()` | 设计规则检查 | Vivado report_drc |
| `pl.get_build_status()` | 查询构建各阶段状态 | Vivado report |

#### 时序与资源分析

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `pl.analyze_timing(clock?)` | 时序分析 → WNS/TNS/WHS/THS + 关键路径 | Vivado report_timing_summary |
| `pl.analyze_utilization()` | 资源利用率 (LUT/FF/BRAM/DSP/BUFG/IO) | Vivado report_utilization |
| `pl.get_wns(clock?)` | 仅查询 WNS（最差负时序裕量） | Vivado property SLACK |
| `pl.get_tns(clock?)` | 仅查询 TNS（总负时序裕量） | Vivado property SLACK |
| `pl.get_power()` | 功耗估算 | Vivado report_power |
| `pl.query_clocks()` | 查询当前设计中所有时钟 | Vivado get_clocks |
| `pl.query_ports(direction?)` | 查询 IO 端口 | Vivado get_ports |
| `pl.query_cells(filter?)` | 查询逻辑单元 | Vivado get_cells |
| `pl.query_nets(filter?)` | 查询信号网络 | Vivado get_nets |
| `pl.query_timing_paths(clock, n_paths?)` | 查询指定时钟域的时序路径 | Vivado report_timing |

#### Bitstream 与 JTAG 硬件

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `pl.generate_bitstream(path)` | 生成 .bit 文件 | Vivado write_bitstream |
| `pl.connect_hw_server()` | 连接 JTAG hw_server | Vivado open_hw |
| `pl.open_hw_target()` | 打开硬件目标 | Vivado open_hw_target |
| `pl.list_devices()` | 列出 JTAG 链上所有设备 | Vivado get_hw_devices |
| `pl.select_device(id)` | 选择 FPGA 设备 | Vivado current_hw_device |
| `pl.program(bitstream_path)` | JTAG 烧录 FPGA | Vivado program_hw_devices |
| `pl.get_device_status()` | 查询 FPGA 设备状态 (DONE, INIT, IDCODE) | Vivado get_property |
| `pl.close_hw_target()` | 关闭硬件目标 | Vivado close_hw_target |

#### ILA 调试

PL Skill 目录明确包含 `debug/ila_debug.md`，PL MCP 必须有对应的 ILA 能力。

| API | 做什么 |
|-----|--------|
| `pl.ila.list_signal_sets()` | 列出可用于 ILA 的信号集 |
| `pl.ila.insert(signals, trigger_width?, sample_depth?)` | 插入 ILA core 并重新生成 bitstream |
| `pl.ila.configure_trigger(signals, conditions)` | 配置触发条件 |
| `pl.ila.arm()` | 启动 ILA 捕获等待触发 |
| `pl.ila.capture()` | 读取捕获数据 |
| `pl.ila.export_waveform(path, format?)` | 导出 ILA 波形 (csv / vcd) |
| `pl.ila.status()` | 查询 ILA 状态 (idle / armed / triggered / full) |

#### 验证

| API | 做什么 | 内部封装 |
|-----|--------|---------|
| `pl.verify()` | 设计后综合验证 | Vivado validate + DRC + 时钟检查 |

#### API 统计

| 类别 | 数量 |
|------|------|
| 工程与源文件管理 | 8 |
| 仿真 | 1 (高层封装) |
| 综合与实现 | 6 |
| 时序与资源分析 | 10 |
| Bitstream 与 JTAG | 8 |
| ILA 调试 | 7 |
| 验证 | 1 |
| **合计** | **~41** |

### 4.5 MCP 调用规则

```
P2.1: MCP API = 具有单一后置条件的执行能力
      一个 API 保证在执行成功时满足一个明确的后置条件。
      API 内部可以调用多个 EDA 命令，但对外是一个操作。
      例如 pl.synthesize() 的后置条件: "RTL 已综合，网表就绪"。

P2.2: MCP 不编排
      MCP API 不编排多个域的操作顺序。编排在 Workflow/Domain Skill 层。

P2.3: MCP 不跨域
      PS MCP 不操作 PL 硬件，PL MCP 不操作 PS 硬件，
      Platform MCP 不操作纯 PS 软件编译或纯 PL 综合实现。

P2.4: MCP API 之间不互相调用 (跨网络)
      PS MCP API → Platform MCP API:  禁止
      PL MCP API  → PS MCP API:      禁止
      
      同一 MCP 内部的代码复用 (同一个进程内的函数调用) 不属于此规则:
      platform.add_axi_dma() → 内部调用 platform.add_ip() + platform.set_ip_properties()
      这是同一 MCP Server 内的代码组织，不影响架构边界。

P2.5: MCP 不做架构选择，但必须执行参数验证和安全保护
      ┌──────────────────────────────────────────────────────────┐
      │  ❌ 不是 MCP 的事 (架构选择):                             │
      │    · 应该用 HP0 还是 ACP？                                │
      │    · DMA 应该中断驱动还是轮询？                           │
      │    · FCLK 应该设置成 50MHz 还是 100MHz？                  │
      │    · 是否需要 AXI Interrupt Controller？                  │
      │                                                          │
      │  ✅ 是 MCP 的事 (执行保护):                                │
      │    · 检查给定的时钟频率是否在硬件支持范围内               │
      │    · 拒绝非法地址范围 (如 0xFFFF0000-0xFFFFFFFF 已被占用) │
      │    · 检查中断线是否已分配给其他 IP                        │
      │    · 验证 AXI 接口协议版本兼容性                          │
      │    · 拒绝会破坏已有连接的 disconnect                      │
      └──────────────────────────────────────────────────────────┘
```

### 4.6 MCP 安全规则（所有 MCP 通用）

```
API 分类:
  所有 MCP API 分为三类，规则不同:
  
  query:    只读，无副作用。
            示例: ps.get_target_status(), pl.query_clocks(), platform.query_topology()
            承诺: 总是幂等，可随时调用。
  
  set:      有副作用但可重复。
            示例: platform.add_ip(), pl.set_top(), ps.set_compiler_options()
            承诺: 幂等——同一参数重复调用不得产生新副作用。
            已存在 → 返回 "已存在且一致"，或报 "已存在但配置不同"。
  
  command:  有副作用且不可重复。
            示例: ps.step(), ps.run(), pl.ila.capture(), IL
            A arm, UART 读取(有状态)
            承诺: 不承诺幂等。每次调用有意产生新状态。
            必须在响应中返回 operation_id 或当前状态以便追踪。
            建议支持 idempotency_key (客户提供 key，已执行的 command 返回缓存结果)。

P3.1: 幂等性 (仅 query 和 set)
      同一参数重复调用同一 query 或 set API，不得产生额外副作用。
      例如重复 "添加 IP" 时：
        ✅ 返回 "已存在且配置一致" 或报 "已存在但配置不同，差异为 ..."
        ❌ 创建一个同名的第二个 IP

P3.2: 事务安全
      操作失败时不得留下半完成状态。
      例如连接 AXI 时如果第二步失败：
        ✅ 回滚已做的第一步，或者保留第一步并返回明确的可恢复状态
        ❌ 留下一根孤立的线，下次查询时莫名其妙

P3.3: 可查询
      每个 set 和 command 操作都有对应的 query 操作。
      写操作的返回值必须包含改了什么（而不只是 "OK"）。

P3.4: 上下文绑定
      所有 API 的第一个隐式或显式参数为 context:
      context = { session_id, board_id, project_path, lease_holder }
      
      MCP 生命周期:
        context = mcp.create_session(board_id, project_path)  → 返回 session_id
        ... 所有 API 调用携带 context ...
        mcp.close_session(session_id)  → 释放所有 lease 和上下文
      
      禁止对"当前活动工程"的全局假设。
```

### 4.7 API 粒度说明

**本架构采用原子工具模式。**

```
Platform MCP 提供 "add_ip"、"connect_interface"、"assign_addresses" 等原子操作。
Platform Skill 将这些原子操作组合成 GPIO、DMA、中断等标准连接流程。
```

**快捷 API（`platform.add_axi_dma()`、`platform.add_axi_gpio()` 等）是可选的便利层**：
- 它们与原子 API 在同一 MCP Server 的同一进程内，是同一代码库内的函数调用
- 不违反 P2.4（P2.4 禁止跨网络跨 MCP 调用）
- 可以减少常见场景的调用次数，但不是 MCP 的核心抽象

三个 MCP 的粒度：

```
PS MCP:       API ≈ 单个目标操作 (如 ps.download, ps.halt, ps.compile)
Platform MCP: API ≈ 单个设计操作 (如 platform.add_ip, platform.connect_interface)
PL MCP:       API ≈ 单个构建/查询操作 (如 pl.synthesize, pl.analyze_timing)
```

---

## 5. System Assembly & Ownership — 谁拥有什么

这是 v2.2 最大的结构缺口：三个 MCP 各自描述了 API，但没有定义它们怎样共享 Vivado 工程、JTAG 链和最终 bitstream 的组装责任。

### 5.1 所有权矩阵

```
┌────────────────────────────────────────────────────────────────┐
│                        所有权矩阵                                │
├──────────────────┬──────────┬──────────┬──────────┬───────────┤
│  对象             │ Platform │ PL MCP   │ PS MCP   │ Workflow  │
│                  │ MCP      │          │          │           │
├──────────────────┼──────────┼──────────┼──────────┼───────────┤
│ Block Design     │   W 所有者│   —      │   —      │   R       │
│ PS7 配置         │   W      │   —      │   —      │   R       │
│ AXI IP (DMA/GPIO)│   W      │   —      │   —      │   R       │
│ 地址映射         │   W      │   R      │   R      │   R       │
│ 时钟/复位/中断   │   W      │   R      │   R      │   R       │
│ BD Wrapper HDL   │   W      │   R      │   —      │   R       │
│ 自定义 RTL       │   —      │   W 所有者│   —      │   R       │
│ 约束 (XDC)       │   W (IO) │   W (时序)│   —      │   R       │
│ Vivado 工程      │   W (BD) │   W (synth)│  —      │   R       │
│ Vivado 综合/布局 │   —      │   W      │   —      │   R       │
│ Bitstream        │   —      │   W 所有者│   —      │   R       │
│ Vivado HW Server │   —      │   W (prog)│   —      │   R       │
│ JTAG DAP (ARM)   │   —      │   —      │   W 所有者│   R       │
│ JTAG TAP (FPGA)  │   —      │   W      │   —      │   R       │
│ Platform XSA     │   W 所有者│   R      │   R      │   R       │
│ System XSA       │   —      │   W 所有者│   R      │   R       │
│ BSP / xparams    │   —      │   —      │   W 所有者│   R       │
│ ELF              │   —      │   —      │   W 所有者│   R       │
│ Run Manifest     │   —      │   —      │   —      │   W 所有者│
├──────────────────┴──────────┴──────────┴──────────┴───────────┤
│  W = 写所有者 (Write Owner)   R = 只读消费者 (Read Consumer)    │
│  W 所有者 = 该阶段的唯一写入者，负责生成 + 版本 + 校验和        │
└────────────────────────────────────────────────────────────────┘
```

### 5.2 最终 bitstream 如何产生

这是 v2.2 的最大遗漏：ADC 示例里 PL 生成 adc_top.v + bitstream，Platform 建立 DMA 通路，但**谁把 adc_top 接入 BD wrapper 并最终生成含 PL 逻辑的完整 bitstream？**

答案是 **PL MCP 负责最终组装和构建**：

```
Phase 1: Platform MCP
  ┌─────────────────────────────────────────────────────┐
  │ 创建 BD → 配置 PS7 → 添加 DMA/GPIO/INTC → 连线    │
  │ → 分配地址 → 导出:                                    │
  │   · BD Wrapper HDL (platform.v / platform.vhd)       │
  │   · Platform Manifest JSON (地址/中断/时钟/接口)      │
  │   · XSA (native hardware description)                │
  │   → 所有产物带有 platform_revision = "sha256:xxx"    │
  └─────────────────────────────────────────────────────┘
                           │
                           ↓  PL MCP 读取 BD wrapper + Manifest
Phase 2: PL Skill → PL MCP
  ┌─────────────────────────────────────────────────────┐
  │ 1. 从 Manifest 读取 PL 需要暴露的接口:               │
  │    · s2mm_stream (AXIS slave, 32-bit) ← BD wrapper   │
  │    · 中断输出线                                       │
  │    · 时钟输入                                         │
  │                                                      │
  │ 2. 开发 adc_top.v:                                   │
  │    · ADC SPI 接口 → 并行数据                         │
  │    · 并行数据 → AXI Stream (tdata/tvalid/tready/tlast)│
  │    · Stream 端口命名与 BD wrapper 接口对齐            │
  │                                                      │
  │ 3. PL MCP 创建 system_top.v (最终顶层):              │
  │    // 端口名称相同不能自动连接，必须显式连接            │
  │    // BD wrapper: s2mm_stream 端口                    │
  │    // adc_top:    s2mm_stream 端口                    │
  │    system_top                                      │
  │    ├── ax7020_adc_wrapper (from Platform MCP)        │
  │    └── adc_top              (from PL Skill)          │
  │        .s2mm_stream(s2mm) ←→ wrapper.s2mm_stream     │
  │        .clk               ←  wrapper.FCLK_CLK0       │
  │        .irq               →   wrapper.irq_f2p[0]     │
  │                                                      │
  │ 4. PL MCP 负责构建:                                  │
  │    pl.add_sources([bd_wrapper, adc_top, system_top]) │
  │    pl.set_top("system_top")                          │
  │    pl.synthesize() → pl.place_and_route()             │
  │    pl.analyze_timing() → WNS +0.12 ✅                │
  │    pl.generate_bitstream("adc_full.bit")              │
  │                                                      │
  │ 5. PL MCP 写入 Artifact:                             │
  │    bitstream: "adc_full.bit"                         │
  │    built_from_platform_revision: "sha256:xxx"        │
  │    rtl_revision: "sha256:yyy"                        │
  │    timing_met: true                                  │
  └─────────────────────────────────────────────────────┘
```

> **关键**：
> - PL MCP 负责创建 `system_top`，显式连接 BD wrapper 和自定义 RTL 的端口
> - PL MCP 综合的是 `system_top`，不是 `adc_top`
> - Platform MCP 只拥有 BD + wrapper，PL MCP 是最终 bitstream 的唯一所有者

### 5.3 两种 XSA 的区别

这是一个容易混淆的点，在此明确：

| | Platform XSA | System XSA |
|---|---|---|
| **何时导出** | Platform MCP 导出 BD 后 | PL MCP 生成最终 bitstream 后 |
| **包含内容** | PS7 配置 + 地址映射 + 中断映射 + BD 中的 IP 的硬件描述 | 最终硬件描述 (含 PL 逻辑实例化) + 可选 embedded bitstream。时序/布局细节留在 PL Build Manifest, 不重复进 XSA |
| **用途** | PS BSP / xparameters.h 生成 | 未来 BOOT.BIN 流程、完整设计归档 |
| **写所有者** | Platform MCP | PL MCP |
| **文件名** | `ax7020_adc_platform.xsa` | `ax7020_adc_system.xsa` |

PS MCP 的 `ps.import_hardware()` 使用 **Platform XSA**（BSP 只需要地址映射和 PS7 配置，不需要完整的 implemented design）。System XSA 是未来 boot/deployment 阶段的产物，当前 JTAG-only 开发模式下不生成。

### 5.4 共享资源所有权模型与锁仲裁

三个 MCP 共享两类稀缺资源：(1) Vivado 工程状态 (2) JTAG hw_server 连接。

```
共享锁 (Resource Lock) 实现原则:
  · 锁不是第四个 MCP，是一个三个 MCP 共用的基础库 (shared utility)
  · 实现方式: 操作系统文件锁 或 本地 Resource Coordinator 进程
  · 锁键规范:
    - Vivado 工程锁键:  project_path 的规范化绝对路径
    - JTAG 锁键:        hw_server URL + cable serial number
  · 每个锁包含:  { lease_id, owner_session_id, scope, acquired_at, ttl_s, heartbeat_at }
  · TTL: 默认 300s。持有者每 60s 发送 heartbeat，超时自动回收
  · MCP 崩溃/exit 时 OS 清理文件锁，不会残留死锁

Vivado 工程 lease:
  · 同一 Vivado 进程同一时刻只有一个 MCP 持有写 lease
  · Platform MCP: 获得 BD 写 lease → 修改 BD → 释放 lease
  · PL MCP:      获得工程写 lease → 添加 RTL + 综合/实现 → 释放 lease
  · 读操作 (查询拓扑/查询时序) 也需要获取读 lease (共享锁)
    → 防止读到另一个 MCP 正在修改的半完成状态
  · Platform MCP 和 PL MCP 永远不会同时持有同一工程写 lease

JTAG hw_server 互斥:
  · JTAG 链同时只能有一个 master
  · PL MCP:  获得 JTAG 锁 → program bitstream → 释放
  · PS MCP:  获得 JTAG 锁 → download ELF → debug → 释放
  · Workflow 负责串行化: Phase 4 先 PL program → 释放 → 再 PS download

Session 生命周期:
  · 每个 MCP 通过 create_session(board_id, project_path) 获取上下文
  · context = { session_id, board_id, project_path, lease_holder }
  · MCP 退出时必须 close_session(session_id)，释放所有 lease
  · Workflow 在所有 Phase 结束后清理所有 session
```

### 5.5 系统装配顺序

Platform MCP 先锁定的设计要素（地址/中断/时钟/接口），PL MCP 和 PS MCP 基于锁定的 manifest 并行或顺序工作：

```
Platform MCP                    PL MCP                   PS MCP
────────────                    ──────                   ──────
锁定 BD 设计
导出 manifest (v1)
+ BD wrapper HDL
+ XSA                             │                        │
                                  ↓                        ↓
                           读取 manifest             读取 manifest
                           对齐 RTL 接口             生成 BSP + xparams
                           综合/实现                  编译 ELF
                           生成 bitstream             
                           写入 artifact_revision=v1  写入 artifact_revision=v1
                                  │                        │
                                  └────────┬───────────────┘
                                           ↓
                                    Workflow 验证:
                                    ✅ platform_revision 一致
                                    → 下载 bitstream + ELF
```

---

## 6. Artifact Contract v2 — 不可变契约

### 6.1 设计原则

v2.2 的 Contract 让三个 MCP 和 Workflow 共同修改一个 JSON——这是并发写入的安全漏洞。v2.3 修正为**各 MCP 生成独立不可变 manifest，Workflow 只汇总**。

```
规则:
  1. 每个 MCP 写入自己的 manifest 文件 (单写者)
  2. 任何 MCP 不修改其他 MCP 的 manifest
  3. Workflow 只读取各 manifest，汇总为 run manifest
  4. 所有 manifest 包含 schema_version 和文件校验和
```

### 6.2 Manifest 类型与命名

所有 manifest 存储在 `manifests/<type>/<revision>.json`，按 revision 命名而非固定文件名。这保证"不可修改旧文件"和 immutable 原则一致。

| Manifest | 写所有者 | 路径模式 | 内容 |
|----------|---------|---------|------|
| Board Profile | 手动固化 | `manifests/board_profile.json` | board_id, vivado_part, ddr, qspi, ps7_preset_sha256, xdc_sha256, vendor_source_sha256 |
| Platform Manifest | Platform MCP | `manifests/platform/<revision>.json` | 地址映射、中断映射、时钟树、接口清单、BD wrapper 路径 + SHA256、XSA 路径 + SHA256、platform_revision |
| PL Build Manifest | PL MCP | `manifests/pl/<revision>.json` | bitstream 路径 + SHA256、built_from 各 revision、时序结果、ILA probes |
| PS Build Manifest | PS MCP | `manifests/ps/<revision>.json` | ELF 路径 + SHA256、built_from 各 revision、XSA SHA256 校验链、BSP 配置 |
| Run Manifest | Workflow | `manifests/run/<run_id>.json` | deployment_plan + execution_evidence 分离，汇总所有 revision |

### 6.3 Revision 算法

```
revision = SHA256(规范化 JSON 输入摘要)

输入摘要包含 (按固定字段顺序):
  {
    "board_profile_sha256": "sha256:...",
    "sources": [
      {"path": "relative/path/to/file.v", "sha256": "sha256:..."},
      {"path": "relative/path/to/file.xdc", "sha256": "sha256:..."}
    ],
    "tcl_config_sha256": "sha256:...",
    "tool_versions": {"vivado": "2023.1", "build": "3457360"},
    "ip_versions": {"axi_dma": "7.1", ...}
  }

计算: JSON.stringify(摘要, sorted_keys) → UTF-8 bytes → SHA256
路径: 所有 paths 相对于 project root，使用正斜杠
```

### 6.4 Platform Manifest 示例

```json
{
  "schema_version": "2.3.1",
  "manifest_type": "platform",
  "project_id": "adc_dma_system",
  "board_id": "ALINX_AX7020_v1.0",
  "board_profile_sha256": "sha256:abc123...",

  "platform_revision": "sha256:platform_sources_and_config",
  "revision_inputs": {
    "bd_tcl_sha256": "sha256:...",
    "ps7_config_sha256": "sha256:...",
    "ip_versions": {
      "axi_dma": "7.1",
      "smartconnect": "1.0",
      "axi_intc": "4.1"
    },
    "vivado_version": "2023.1",
    "vivado_build": "3457360"
  },
  "generated_at": "2026-08-03T15:30:00+08:00",

  "artifacts": {
    "bd_wrapper": {
      "path": "platform/hdl/ax7020_adc_wrapper.v",
      "sha256": "sha256:..."
    },
    "xsa": {
      "path": "platform/ax7020_adc.xsa",
      "sha256": "sha256:..."
    }
  },

  "design_state": {
    "address_map": {
      "axi_dma_0": {"base": "0x40400000", "range": "64K"},
      "axi_gpio_0": {"base": "0x41200000", "range": "64K"}
    },
    "interrupt_map": {
      "dma0_s2mm": {"line": 0, "controller": "axi_intc_0"}
    },
    "clock_tree": {
      "FCLK_CLK0": {"freq_hz": 100000000, "targets": ["axi_dma_0", "axi_gpio_0"]}
    },
    "pl_interfaces": [
      {"name": "s2mm_stream", "type": "axis", "direction": "slave", "data_width": 32}
    ]
  },

  "validation": {"passed": true, "warnings": []},
  "status": "locked"
}
```

### 6.5 PL Build Manifest 示例

```json
{
  "schema_version": "2.3.1",
  "manifest_type": "pl_build",
  "project_id": "adc_dma_system",
  "board_id": "ALINX_AX7020_v1.0",

  "pl_revision": "sha256:rtl_sources_and_constraints",
  "built_from": {
    "platform_revision": "sha256:platform_sources_and_config",
    "platform_manifest_sha256": "sha256:def456...",
    "board_profile_sha256": "sha256:abc123..."
  },
  "revision_inputs": {
    "rtl_sources_sha256": "sha256:...",
    "constraints_sha256": "sha256:...",
    "vivado_version": "2023.1",
    "vivado_build": "3457360"
  },
  "generated_at": "2026-08-03T16:00:00+08:00",

  "artifacts": {
    "bitstream": {
      "path": "pl/adc_full.bit",
      "sha256": "sha256:..."
    },
    "build_log": {"path": "pl/build.log", "sha256": "sha256:..."},
    "ltx": {"path": "pl/adc_full.ltx", "sha256": "sha256:..."}
  },

  "timing": {
    "met": true,
    "wns_ns": 0.12,
    "tns_ns": 0.0,
    "clock": "FCLK_CLK0"
  },
  "utilization": {"lut_pct": 15, "ff_pct": 8, "bram": 2, "dsp": 3},

  "status": "locked"
}
```

### 6.6 PS Build Manifest 示例

```json
{
  "schema_version": "2.3.1",
  "manifest_type": "ps_build",
  "project_id": "adc_dma_system",
  "board_id": "ALINX_AX7020_v1.0",

  "ps_revision": "sha256:ps_sources_and_config",
  "built_from": {
    "platform_revision": "sha256:platform_sources_and_config",
    "platform_manifest_sha256": "sha256:...",
    "platform_xsa_sha256": "sha256:...",
    "board_profile_sha256": "sha256:abc123..."
  },
  "revision_inputs": {
    "c_sources_sha256": "sha256:...",
    "bsp_config_sha256": "sha256:...",
    "linker_script_sha256": "sha256:...",
    "vitis_version": "2023.1",
    "xsct_version": "2023.1"
  },
  "generated_at": "2026-08-03T16:05:00+08:00",

  "artifacts": {
    "elf": {
      "path": "ps/adc_app.elf",
      "sha256": "sha256:..."
    },
    "build_log": {"path": "ps/build.log", "sha256": "sha256:..."}
  },

  "bsp_config": {
    "stdin": "uart1",
    "stdout": "uart1",
    "os": "standalone",
    "optimization": "-O2"
  },

  "status": "locked"
}
```

### 6.7 Run Manifest（Workflow 验证）

Run Manifest 区分 `deployment_plan` (事前计划) 和 `execution_evidence` (事后记录)。没有实际下载结果、目标 ID、UART 日志校验和时，状态只能是 `ready`，不能是 `verified`。

```json
{
  "schema_version": "2.3.1",
  "manifest_type": "run",
  "run_id": "run_20260803_161000",
  "project_id": "adc_dma_system",
  "board_id": "ALINX_AX7020_v1.0",
  "board_serial": "TBD",
  "generated_at": "2026-08-03T16:10:00+08:00",

  "components": {
    "platform": {"revision": "sha256:platform...", "manifest_path": "manifests/platform/sha256-platform.json"},
    "pl_build": {"revision": "sha256:pl...", "bitstream_sha256": "sha256:...",
                 "built_from_platform": "sha256:platform..."},
    "ps_build": {"revision": "sha256:ps...", "elf_sha256": "sha256:...",
                 "built_from_platform": "sha256:platform...",
                 "platform_xsa_sha256": "sha256:..."}
  },

  "consistency": {
    "platform_revisions_match": true,
    "board_profile_match": true,
    "all_artifacts_exist": true,
    "all_checksums_valid": true,
    "errors": [],
    "warnings": []
  },

  "deployment_plan": {
    "target": {"hw_server": "localhost:3121", "cable_serial": "TBD"},
    "steps": [
      {"seq": 1, "action": "pl.program", "artifact": "pl/adc_full.bit", "verify": "DONE pin high"},
      {"seq": 2, "action": "ps.download", "artifact": "ps/adc_app.elf"},
      {"seq": 3, "action": "ps.run"},
      {"seq": 4, "action": "ps.read_uart", "port": "COM5", "baud": 115200, "expected": "DMA_OK", "timeout_s": 10}
    ]
  },

  "execution_evidence": null,

  "status": "ready"
}
```

**事后补充 execution_evidence 后**:

```json
  "execution_evidence": {
    "executed_at": "2026-08-03T16:15:00+08:00",
    "hw_server": "localhost:3121",
    "cable_serial": "D1234567",
    "steps": [
      {"seq": 1, "action": "pl.program", "result": "success", "done_pin": true, "duration_ms": 3500},
      {"seq": 2, "action": "ps.download", "result": "success", "entry_point": "0x00100000", "duration_ms": 800},
      {"seq": 3, "action": "ps.run", "result": "success"},
      {"seq": 4, "action": "ps.read_uart", "result": "success",
       "output": "ADC CH0: 2048  CH1: 3102  DMA_OK ✅",
       "output_sha256": "sha256:..."}
    ]
  },

  "status": "verified"
```

### 6.8 拼装规则

```
拼装验证由 Workflow 执行，但规则编码在 Run Manifest schema 中：

1. board_profile_sha256 在所有 manifest 中必须一致
2. pl_build.built_from.platform_revision == platform.platform_revision
3. ps_build.built_from.platform_revision == platform.platform_revision
4. ps_build.built_from.board_profile_sha256 == board_profile_sha256
5. 任何 mismatch → Run Manifest consistency.errors 非空
6. errors 非空 → Workflow 禁止进入 Phase 4 (下载/部署)
7. 所有 artifact 文件必须存在且 SHA256 匹配

所有 manifest 写入后即锁定 (status: "locked")。任何修改必须新建 manifest
（revision 改变）。不允许修改已锁定的 manifest。
```

---

## 7. 跨域协作：Workflow 如何工作

### 7.1 完整示例：ADC DMA 采集系统

```
adc_workflow 编排:

┌─ Phase 1: Platform Skill ──────────────────────────────────┐
│  "建立 ADC → DMA → DDR 数据通路"                            │
│                                                             │
│  Platform Skill 知道:                                       │
│    1. PS7 使能 S_AXI_HP0 (PL→DDR 高速端口)                 │
│    2. PS7 使能 IRQ_F2P (PL 中断→ARM)                       │
│    3. PS7 使能 FCLK0 = 100MHz                              │
│    4. 添加 AXI DMA (S2MM 模式, 32-bit)                     │
│    5. 连接 DMA M_AXI_S2MM → PS S_AXI_HP0                   │
│    6. 连接 DMA 中断 → AXI INTC → IRQ_F2P                   │
│    7. 分配地址 → 验证                                       │
│                                                             │
│  Platform Skill 调用:                                       │
│    platform.add_ps7("ax7020")                                │
│    platform.configure_ps7({                                  │
│        s_axi_hp0: true, m_axi_gp0: true,                     │
│        fclk0_mhz: 100, irq_f2p: true                         │
│    })                                                        │
│    platform.add_axi_dma("dma0", {mode: "s2mm", width: 32})   │
│    platform.add_smartconnect("sc0")                          │
│    platform.add_axi_interrupt_controller("intc0")            │
│    platform.add_processor_reset("rst0")                      │
│    platform.connect_interface("ps/M_AXI_GP0", "sc0/S00_AXI") │
│    platform.connect_interface("sc0/M00_AXI", "dma0/S_AXI_LITE")│
│    platform.connect_interface("dma0/M_AXI_S2MM", "ps/S_AXI_HP0")│
│    platform.connect_clock("ps/FCLK_CLK0",                    │
│        ["dma0/s_axi_lite_aclk", "dma0/m_axi_s2mm_aclk"])     │
│    platform.connect_reset("ps/FCLK_RESET0_N",                │
│        ["rst0/ext_reset_in"])                                │
│    platform.connect_interrupt("dma0/s2mm_introut", irq_line=0)│
│    platform.assign_addresses()                               │
│    platform.validate()                                       │
│    platform.export_hardware("adc_platform.xsa")               │
│    platform.export_manifest("adc_platform.json")              │
│                                                             │
│  输出: address_map, irq_map, hardware_description, manifest  │
└────────────────────────────────────────────────────────────┘
                           │
┌─ Phase 2: PL Skill ────────────────────────────────────────┐
│  "开发 ADC 采集 + DMA Stream 发送逻辑"                      │
│                                                             │
│  PL Skill 知道:                                             │
│    1. ADC 接口 RTL (SPI → 并行数据)                        │
│    2. AXI Stream 打包 (数据 → tdata/tvalid/tready/tlast)  │
│    3. 顶层集成 (ADC + Stream + DMA 接口)                    │
│    4. Testbench (模拟 ADC 数据)                             │
│    5. 仿真验证 → 综合 → 布局布线 → 时序分析                  │
│                                                             │
│  PL Skill 调用:                                             │
│    pl.simulate(sources, tb_adc_top) → PASS                 │
│    pl.set_top("system_top")                                 │
│    pl.synthesize() → PASS                                   │
│    pl.place_and_route() → PASS                              │
│    pl.analyze_timing() → WNS +0.12ns ✅                    │
│    pl.generate_bitstream("adc_full.bit")                    │
│                                                             │
│  输出: adc_full.bit                                         │
└────────────────────────────────────────────────────────────┘
                           │
┌─ Phase 3: PS Skill ────────────────────────────────────────┐
│  "开发 ARM DMA 读取程序"                                    │
│                                                             │
│  PS Skill 知道:                                             │
│    1. 从 hardware_description 获取 DMA 基地址              │
│    2. 使用 XAxiDma API: Init → StartTransfer → WaitIRQ    │
│    3. 中断服务函数注册                                      │
│    4. 编译 → ELF                                            │
│                                                             │
│  PS Skill 调用:                                             │
│    ps.create_app("adc_reader", platform)                     │
│    ps.add_sources(["main.c", "dma_handler.c"])               │
│    ps.compile() → adc_app.elf                                │
│                                                             │
│  输出: adc_app.elf                                          │
└────────────────────────────────────────────────────────────┘
                           │
┌─ Phase 4: 端到端 JTAG 验证 ────────────────────────────────┐
│  (Workflow 直接调用 MCP, 不经过 Domain Skill)               │
│                                                             │
│  pl.program("adc_full.bit")                                │
│  ps.download("adc_app.elf")                                │
│  ps.run()                                                   │
│  result = ps.read_uart(COM5, 115200)                        │
│                                                             │
│  → "ADC CH0: 2048  CH1: 3102  DMA_OK ✅"                  │
│                                                             │
│  数据通路: ADC → PL → DMA → HP0 → DDR → ARM   ✅           │
└────────────────────────────────────────────────────────────┘
```

### 7.2 调用链总结

```
adc_workflow
  ├─→ Platform Skill ──→ platform MCP APIs ──→ Vivado IPI
  ├─→ PL Skill       ──→ pl MCP APIs       ──→ Vivado / XSim
  ├─→ PS Skill       ──→ ps MCP APIs       ──→ XSCT / XSDB
  └─→ pl.program() + ps.download() + ps.read_uart()  (直调 MCP)
```

Workflow 不关心 MCP 内部用什么 EDA 命令。换工具版本时（如 Vivado 2023.1→2025.2），目标是 Workflow 和 Domain Skill 无需修改——但 MCP API 的输入输出契约可能因 EDA 语义变化而需要兼容性适配，这不是架构层面的失败。

---

## 8. 项目目录结构

```
Xilinx_AI_Agent/
│
├── docs/
│   ├── architecture_ai_zynq7020.md    ← 本文件
│   └── development/                   ← G0-G12 历史开发记录
│
├── workflows/                         ← 跨域编排 (Workflow 层)
│   ├── adc_dma/
│   │   ├── WORKFLOW.md
│   │   ├── platform_config/
│   │   └── expected_outputs/
│   ├── ethernet_stream/
│   ├── gpio_control/
│   └── template/                      ← 新建 Workflow 模板
│
├── skills/                            ← 领域知识 (Domain Skill 层)
│   │
│   ├── ps_skill/
│   │   ├── SKILL.md
│   │   ├── baremetal/
│   │   ├── dma_driver/
│   │   ├── interrupt/
│   │   ├── peripheral/
│   │   └── references/
│   │
│   ├── platform_skill/
│   │   ├── SKILL.md
│   │   ├── gpio_control/
│   │   ├── dma_stream/
│   │   ├── interrupt_routing/
│   │   ├── clock_reset/
│   │   ├── ddr_sharing/
│   │   ├── axis_stream/
│   │   └── references/
│   │
│   └── pl_skill/
│       ├── SKILL.md
│       ├── rtl_design/
│       ├── simulation/
│       ├── build/
│       ├── debug/
│       └── references/
│
├── mcps/                              ← 原子 API (MCP 层)
│   │
│   ├── ps_mcp/
│   │   ├── server.py                  ← MCP Server (stdio)
│   │   ├── api/                       ← 对外 API 实现
│   │   │   ├── target_api.py          ← connect_hw_server, list_targets, select, get_status
│   │   │   ├── platform_api.py        ← import_hardware, create_platform, create_bsp
│   │   │   ├── app_api.py             ← create_app, add_sources, compile
│   │   │   ├── jtag_api.py            ← download, reset, initialize, run, halt, step
│   │   │   ├── debug_api.py           ← debug_start, breakpoint, register, stack_trace
│   │   │   ├── uart_api.py            ← read_uart, write_uart
│   │   │   ├── mem_api.py             ← reg_read/write, mem_read/write
│   │   │   └── recovery_api.py        ← recover_target, reconnect, diagnose_dap
│   │   ├── process/                   ← EDA 进程管理 (内部)
│   │   │   ├── xsct.py
│   │   │   └── xsdb.py
│   │   └── tcl/                       ← Tcl 模板 (内部)
│   │
│   ├── platform_mcp/
│   │   ├── server.py
│   │   ├── api/
│   │   │   ├── design_api.py          ← create/open/save/close/get_status
│   │   │   ├── ps7_api.py             ← add_ps7, configure_ps7
│   │   │   ├── ip_api.py              ← add_ip, set_ip_properties, remove_ip, list_ips + shortcuts
│   │   │   ├── module_api.py          ← add_module_reference, refresh, list_interfaces
│   │   │   ├── connection_api.py      ← connect_interface, connect_signal, disconnect, make_external
│   │   │   ├── clock_reset_api.py     ← connect_clock, connect_reset
│   │   │   ├── interrupt_api.py       ← connect_interrupt, query_interrupt_map
│   │   │   ├── address_api.py         ← assign_addresses, set_address, check_conflicts
│   │   │   ├── query_api.py           ← query_topology, query_connections, query_clock_tree
│   │   │   └── export_api.py          ← generate_wrapper, export_hardware, export_manifest
│   │   ├── process/
│   │   │   └── vivado.py
│   │   └── tcl/
│   │
│   └── pl_mcp/
│       ├── server.py
│       ├── api/
│       │   ├── project_api.py         ← create/open/close_project, add_sources, set_top
│       │   ├── sim_api.py             ← simulate (xvlog → xelab → xsim)
│       │   ├── synth_api.py           ← synthesize
│       │   ├── impl_api.py            ← place, route, place_and_route
│       │   ├── timing_api.py          ← analyze_timing, get_wns/tns, query_timing_paths
│       │   ├── utilization_api.py     ← analyze_utilization, get_power
│       │   ├── bitstream_api.py       ← generate_bitstream
│       │   ├── hw_api.py              ← connect_hw_server, open_target, program, get_device_status
│       │   ├── ila_api.py             ← insert, configure_trigger, arm, capture, export_waveform
│       │   ├── query_api.py           ← query_clocks/ports/cells/nets
│       │   └── verify_api.py          ← run_drc, verify, get_build_status
│       ├── process/
│       │   ├── vivado.py
│       │   └── xsim.py
│       └── tcl/
│
├── projects/
│   │
│   ├── platforms/                     ← 硬件平台定义
│   │   └── ax7020_base/
│   │
│   ├── pl_projects/                   ← 纯 PL 工程
│   │   ├── hello_fpga/
│   │   ├── g9_hw_test/
│   │   └── validation/
│   │
│   ├── ps_projects/                   ← 纯 PS 工程
│   │   └── ps_led_test/
│   │
│   └── examples/                      ← 完整 SoC 参考设计
│       ├── adc_dma_system/
│       │   ├── platform/
│       │   ├── pl/
│       │   ├── ps/
│       │   └── verify/
│       ├── ethernet_stream/
│       └── gpio_control/
│
└── README.md
```

> `projects/examples/` 替代了 v2.0 的 `projects/system_projects/`。例子会越来越多，`examples/` 语义更准确，也暗示这是参考实现而非唯一的标准结构。

---

## 9. 当前项目迁移路线

### 9.1 现有资产 vs 目标位置

```
现有                                   →  目标位置
────────────────────────────────────────────────────────────
Xilinx_Vivado_MCP/                    →  mcps/pl_mcp/ (拆分)
  ├── vivado_tools.py                 →  pl_mcp/api/synth_api + impl_api + ...
  ├── sim_tools.py                    →  pl_mcp/api/sim_api.py
  ├── hw_tools.py                     →  pl_mcp/api/hw_api.py
  ├── vivado_process.py               →  pl_mcp/process/vivado.py
  ├── xsim_process.py                 →  pl_mcp/process/xsim.py
  ├── tcl_templates.py                →  pl_mcp/tcl/
  ├── skills/fpga-verify              →  skills/pl_skill/build/
  └── skills/fpga-develop             →  skills/pl_skill/rtl_design/

  (Block Design Tcl)                  →  mcps/platform_mcp/ (提取)
  (PS7 config Tcl)                    →  mcps/platform_mcp/tcl/

Xilinx_Vitis_MCP/ (骨架)              →  mcps/ps_mcp/ (建设)

zynq_platforms/ax7020_base/           →  projects/platforms/ax7020_base/
embedded_projects/ps_led_test/        →  projects/ps_projects/ps_led_test/
hello_fpga/                           →  projects/pl_projects/hello_fpga/
g9_hw_test/                           →  projects/pl_projects/g9_hw_test/
validation_projects/                  →  projects/pl_projects/validation/

docs/development/G0-G12               →  docs/development/ (保留, 历史)
docs/architecture_review.md           →  冻结, 不再修改
```

### 9.2 迁移策略

```
原则: 先做最小纵向切片验证架构, 再做横向批量开发。
      先验证一条完整链路能跑通, 再拆分目录和扩展 API。

Phase A (当前): 架构确立
  ✅ architecture_ai_zynq7020.md v2.3.1
  ⬜ Review 确认 → 冻结顶层架构 (三域四层 + P1-P8)

Phase B (2-3 周): 最小 GPIO 纵向切片 (先不动现有代码)
  目标: 用最小实现验证架构能否跑通
  包含: 最小 MCP + 最小 Domain Skill + Workflow + Artifact Contract

  ⬜ Step 1: Board Profile 固化
     创建 projects/platforms/ax7020_base/board_profile.json
     提取厂商 ps_config.tcl → ps7_preset.tcl + 填入 SHA256
     提取厂商 XDC 约束 → board.xdc + 填入 SHA256

  ⬜ Step 2: 最小 Platform MCP (~10 API)
     create_design, add_ps7, configure_ps7,
     add_axi_gpio, connect_interface, connect_clock,
     connect_reset, assign_addresses, validate,
     export_hardware, export_manifest

  ⬜ Step 3: 最小 Platform Skill
     知识: 决定 GPIO 地址 (如 0x41200000)、时钟 (FCLK0=50MHz)、复位链
     调用: Platform MCP 的上述 API
     输出: Platform Manifest + XSA

  ⬜ Step 4: 最小 PL MCP (~12 API)
     create_project, add_sources (含 bd_wrapper + system_top), set_top,
     synthesize, place_and_route, analyze_timing, generate_bitstream,
     connect_hw_server, open_hw_target, select_device, program, get_device_status

  ⬜ Step 5: 最小 PL Skill
     知识: system_top 顶层集成、XDC 约束、时序目标
     调用: PL MCP 的上述 API
     输出: Bitstream + PL Build Manifest

  ⬜ Step 6: 最小 PS MCP (~20 API, 覆盖完整 JTAG 开发闭环)
     硬件连接: connect_hw_server, disconnect_hw_server, list_targets, select_target, get_target_status
     BSP 周期: import_hardware, create_platform, create_bsp, get_bsp_status
     应用工程: create_app, add_sources, compile, get_build_status
     JTAG 控制: download, reset, initialize, run, halt, wait_for_state
     运行时:   read_uart, reg_read, reg_write, mem_read
     目标恢复: recover_target, reconnect_target, diagnose_dap
     注意: loadhw + ps7_init 是 AXI 地址可访问的前提, 由 initialize() 封装

  ⬜ Step 7: 最小 PS Skill
     知识: Bare-metal GPIO 程序、BSP 配置、中断(本阶段可跳过)
     调用: PS MCP 的上述 API
     输出: ELF + PS Build Manifest

  ⬜ Step 8: gpio_control Workflow
     Phase 1: Platform Skill (GPIO 通路) → XSA + Manifest
     Phase 2: PL Skill (system_top + 约束 + 构建) → Bitstream + Manifest
     Phase 3: PS Skill (BSP + GPIO C 程序) → ELF + Manifest
     Phase 4: 部署 → pl.program(bitstream) → ps.initialize + ps.download(elf)
              → ps.run() → ps.read_uart() → "GPIO LED OK ✅"

  ⬜ Step 9: Agent2 黑盒验收
     Agent1 完成实现和白盒验证后:
     · 全新 Agent2 只收到 需求描述 + Domain Skill 文档 + MCP API 文档 + 板卡说明
     · Agent2 在干净目录重新生成 XSA、BIT、ELF、Manifest
     · 故意提供旧 ELF / 错误 board revision → 确认 Workflow 拒绝部署
     · 验收: Agent2 可独立复现完整的 GPIO LED 闪烁链路
     这才是真正证明 Skill 可移植、MCP 可使用

  ⬜ Step 10: Artifact Contract 最小实现
     schema_version、revision 算法、board_profile_sha256 校验链
     stale artifact 拒绝逻辑
     Run Manifest: deployment_plan + execution_evidence
     注意: Contract 必须在 Phase B 实现, Phase D 只做扩展

  注意: 此阶段不拆分 Xilinx_Vivado_MCP, 不新建 MCP 目录

Phase C (3-4 周): 物理拆分 (GPIO 切片 + Agent2 验证通过后)
  ⬜ Xilinx_Vivado_MCP → mcps/pl_mcp + mcps/platform_mcp
  ⬜ Xilinx_Vitis_MCP   → mcps/ps_mcp (从 Phase B 最小 PS MCP 扩展)
  ⬜ 三个 MCP 各自独立注册到 Claude Code
  ⬜ GPIO 切片在新拆分下重新验证 (含 Agent2 复查)

Phase D (5-8 周): 扩展 Skill + 新 Workflow
  ⬜ skills/ 层充实子技能 (DMA / Interrupt / Boot 等)
  ⬜ adc_dma Workflow (第二个纵向切片)
  ⬜ Artifact Contract 扩展 (run evidence 自动校验、多 revision 追踪)
```

### 9.3 G 阶段重新规划

```
Phase 0: 最小切片验证 (验证架构, 不拆分目录)
─────────────────────────────────────────
G13: GPIO 纵向切片 (含最小三层)
     MCP:  最简 Platform (~10) + PL (~12) + PS (~20) API
     Skill: 最小 Platform + PL + PS Skill (GPIO 场景)
     Workflow: gpio_control (四 Phase)
     Contract: 最小 Schema + Revision 算法 + stale 拒绝
     Agent2: 黑盒复现验收
     验收: stale artifact 拒绝, board_revision 不匹配拒绝, JTAG 恢复,
           Agent2 可独立复现

Phase 1: PL Domain (现有基础 G0-G9)
────────────────────────────────────
G14: 物理拆分 — Xilinx_Vivado_MCP → mcps/pl_mcp + mcps/platform_mcp
G15: PL Skill 扩展 — 充实 rtl_design / simulation / timing / debug 子技能

Phase 2: Platform Domain
────────────────────────────────────
G16: Platform MCP 提取 — 从 Xilinx_Vivado_MCP 提取 Interconnect 相关功能
G17: Platform Skill 建设 — gpio_control → dma_stream → interrupt_routing

Phase 3: PS Domain
────────────────────────────────────
G18: PS MCP 建设 — Xilinx_Vitis_MCP 从骨架到完整 PS MCP
G19: PS Skill 建设 — baremetal → dma_driver → interrupt

Phase 4: Workflow 扩展
────────────────────────────────────
G20: adc_dma Workflow    — DMA 端到端 JTAG 自动化
G21: 多 Workflow 并行开发 — ethernet_stream, vision_pipeline 等
```

---

## 10. 不可变架构原则

```
P1: 以硬件架构为第一层抽象
    Zynq-7020 = PS + Interconnect + PL 三个责任域 (bounded context)。
    不以 EDA 工具 (Vivado/Vitis) 划分系统边界。
    换 EDA 工具版本时，目标是 Workflow 和 Domain Skill 无需修改——
    但这是接口兼容性目标，不是绝对保证。MCP 是承担版本差异的唯一层。

P2: 四层模型, 每层职责唯一
    Workflow  → 编排 (什么项目, 什么阶段, 谁来做)
    Domain    → 知识 (这个领域怎么做, 什么算成功)
    MCP       → 执行 (操作硬件, 生成文件, 返回结果)
    EDA       → 通信 (管理外部进程, 不知道 FPGA 是什么)

P3: MCP = 原子 API, 不编排, 不跨域, 不互相调用
    MCP 暴露具有单一后置条件的 API。Platform MCP 提供 add_ip、connect_interface
    等原子操作，Platform Skill 将它们组合成 DMA、GPIO、中断等标准连接流程。
    编排在 Workflow/Domain Skill 层, 不在 MCP 层。
    同一 MCP 内部的代码复用 (函数调用) 不同于跨 MCP 网络调用。

P4: Workflow 层 = 唯一的跨域编排者
    Workflow 不是第四个"域", 它是独立于三个域之上的编排层。
    Workflow 不包含领域知识。它委托 Domain Skill 做专业判断。
    Workflow 可以直接调用 MCP API，但仅限于: 部署产物 / 状态控制 / 结果观测。
    任何包含设计决策的操作必须委托 Domain Skill。

P5: Domain Skill = 可扩展的层次结构
    每个 Domain Skill 不是一个大文件, 是多个独立子技能的集合。
    增加新子技能 (如 PCIe, DDR, Linux) 不修改顶层架构。

P6: 所有 Workflow 可恢复
    失败时返回可恢复状态 (phase + resume_from),
    而不是抛出异常后从头开始。

P7: 当前开发配置为 JTAG-only
    JTAG = 当前 MVP 开发模式。AI Agent 输出三件套 (hardware_description + bitstream + ELF)。
    BOOT.BIN / QSPI Flash / SD Boot 属于后续阶段，通过 Workflow 扩展而非架构变更来支持。

P8: 共享资源通过锁协调 (不引入第四 MCP)
    Vivado 工程和 JTAG hw_server 在三个 MCP 间通过共享锁库协调。
    锁键使用 project_path / hw_server cable serial，包含 TTL + heartbeat + 超时回收。
    这不是第四个 MCP，是三个 MCP 共用的基础库。
```

---

## 11. 附录

### A. 术语对照

| v1.0 | v2.1 (稳定) | v2.3.1 (本次) | 说明 |
|------|------------|--------------|------|
| VivadoMCP | PL MCP | PL MCP | 不变: 三域自 v2.0 起稳定 |
| ZynqMCP | Platform MCP | Platform MCP | 不变 |
| VitisMCP | PS MCP | PS MCP | 不变 |
| (不存在) | System Skill | Workflow 层 | 修正: Workflow 不是第四个域 |
| Block Design | Interconnect Layer | Interconnect Layer | 不变 |
| XSA | hardware_description | Platform XSA + System XSA | **新增区分**: BSP 用 platform_xsa, 发布用 system_xsa |
| system_projects | examples | examples | 不变 |
| (不存在) | 初版 | Artifact Contract v2 | **完善**: 不可变 manifest + revision 算法 + run evidence |
| 全部幂等 | query/set/command | query/set/command + idempotency_key | 不变 |
| 无锁模型 | lease 概念 | 共享锁库 (P8) | **新增**: TTL/heartbeat/OS文件锁/读lease |
| 无 system_top | 无 | system_top 显式连接 | **新增**: PL MCP 负责顶层集成 |
| 批量开发优先 | 批量开发优先 | GPIO 切片优先 | **修正**: 先验证一条链路再横向扩展 |

### B. AX7020 Board Configuration Package

> 这是架构的唯一板卡数据源。所有 MCP 和 Skill 从这里读取参数，不硬编码、不从绝对路径加载。
>
> 厂商资料来源: D:\BaiduNetdiskDownload\AX7020_2023.1\
> 权威文件: course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl (537 参数 PS7 preset)
> 状态: ⚠ board_profile.json 尚未物理固化, 以下为架构规格

```
board_id:                "ALINX_AX7020_v1.0"
chip:                    XC7Z020-2CLG400I
vivado_part:             "xc7z020clg400-2"

# DDR3 (物理 1GB, 配置 512MB)
ddr_physical:            1 GB (2× 4Gbit = 8Gbit total)
ddr_chip_vendor_preset:  "MT41J256M16 RE-125"  # 来自厂商 Tcl CONFIG.PCW_UIPARAM_DDR_PARTNO
ddr_configured:          512 MB                 # PCW_DDR_RAM_HIGHADDR = 0x1FFFFFFF
ddr_frequency:            533.333 MHz           # PCW_UIPARAM_DDR_FREQ_MHZ
ddr_bus_width:            32 Bit

# QSPI Flash
qspi_physical:           256 Mbit (W25Q256, per README_CN.md) = 32 MB
qspi_linear_window:      16 MB (0xFC000000–0xFCFFFFFF)
                         Zynq-7000 x4 QSPI 线性地址模式最大映射窗口为 16MB。
                         大于 16MB 的部分需要通过 I/O 模式访问。
qspi_data_mode:          x4                     # PCW_SINGLE_QSPI_DATA_MODE

# PS 外设
uart:                    UART1 → CP2104 (MIO 48-49)
                         PS7 baud initial = 115200 (PCW_UART1_BAUD_RATE)
ethernet:                GEM0 → RTL8211E (MIO 16-27, RGMII)
sd:                      SDIO0 (MIO 40-45)
ps_clock:                33.333 MHz (PS_CLK, on-board oscillator)

# PL (独立晶振)
pl_oscillator:           50 MHz (on-board, independent of PS_CLK)

# PL 资源
pl_resources:            53,200 LUT / 106,400 FF / 140 BRAM36 / 220 DSP48E1

# PL 物理 IO (来自 README_CN.md)
pl_leds:                 4 (PL-side, active-low via transistor)
ps_leds:                 2 (PS-side, via MIO)
pl_buttons:              4 (PL-side, active-high, pull-down)
ps_buttons:              2 (PS-side, via MIO)
pl_user_io:              2× 40-pin headers (J11, J13, 2.54mm pitch, 3.3V)

# 厂商参考文件 (待固化为 board_profile.json + board.xdc + ps7_preset.tcl)
vendor_ps7_preset_tcl:   "AX7020_2023.1/course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl"
vendor_ps7_preset_sha256: "<固化时填入>"   # ⚠ TBD
vendor_xdc_sha256:        "<固化时填入>"   # ⚠ TBD
vendor_source_dir:        "D:/BaiduNetdiskDownload/AX7020_2023.1/"

⚠ 下一步: 创建 projects/platforms/ax7020_base/board_profile.json + ps7_preset.tcl + board.xdc，
  填入 SHA256，删除所有绝对路径依赖 (当前 build_g11.tcl 包含此依赖)。
  完成后本附录应从 Markdown 变为指向 board_profile.json 的引用。
```

