# Vivado MCP Architecture v1.0 — FROZEN

> 日期: 2026-08-01
> 状态: **FROZEN** — Architecture v1.0
> 前置: G0–G5 已完成
> 
> 本文件定义了 Vivado MCP 的三层架构和长期演进路线。
> 后续 G6–G9 的实施方案基于此架构，不做原则性修改。

---

## 0. Architecture Principles (IMMUTABLE)

以下四个原则是 v1.0 的基石。后续 G6–G9 的所有实现必须遵守。

### Principle 1: Process Layer — 只负责管理外部程序

| 职责 | 反例 (禁止) |
|------|-------------|
| 生命周期 (start/shutdown/restart) | ❌ `get_timing()` |
| stdin/stdout 通信 | ❌ `run_build()` |
| 超时 + 清理 | ❌ `report_utilization()` |
| 环境变量 (settings64.bat) | ❌ 任何 FPGA 业务逻辑 |
| 进程状态 (is_running, pid) | |

Process 是**可执行文件的 thin wrapper**，对 FPGA 一无所知。

### Principle 2: Tool Layer — 一个 Tool = 一个工程动作

| 职责 | 反例 (禁止) |
|------|-------------|
| 调用一个或多个 Process | ❌ `develop_design()` (这是 Workflow) |
| 输入校验 + 输出格式化 (JSON) | ❌ Tool 直接访问可执行文件 (绕过 Process) |
| 单一原子操作 | |

Tool 不编排流程。Tool 做一件事，做好。

### Principle 3: Workflow Layer — 负责 AI 工程流程

| 职责 | 反例 (禁止) |
|------|-------------|
| 编排多个 Tool | ❌ Claude Code 手动调用 5 个 Tool 做一件事 |
| 错误恢复 + 重试 | ❌ Workflow 直接访问 Process |
| 返回结构化结果 | |

Claude Code **永远只应该看到 Workflow 层**。

### Principle 4: All Workflows Must Be Recoverable

Workflow 失败时必须返回**可恢复状态**，而不是抛出异常后退出：

```json
// ✅ 正确 — AI 可以继续
{
  "stage": "timing",
  "status": "failed",
  "wns_ns": -0.5,
  "next_action": "optimize_rtl",
  "resume_from": "build"  // 修复后从这里继续，不重跑 Simulation
}

// ❌ 错误 — 信息丢失，必须从头来
{
  "error": "Timing failed"
}
```

每个 Workflow 天然支持：**暂停 → 诊断 → 修复 → 从断点继续**。

---

## 1. Standard FPGA Development Workflow

### 1.1 完整流程图

```
┌─────────────────────────────────────────────────────────────────────┐
│                     FPGA 开发全流程                                   │
├─────────┬─────────┬─────────┬─────────┬─────────┬──────────────────┤
│ DESIGN  │ VERIFY  │ BUILD   │ ANALYZE │ DEPLOY  │ DEBUG            │
├─────────┼─────────┼─────────┼─────────┼─────────┼──────────────────┤
│ RTL     │ Lint    │ Synth   │ Timing  │ BitGen  │ ILA              │
│ Design  │         │         │         │         │                  │
│         │ Sim     │ Opt     │ Util    │ Program │ UART debug       │
│ XDC     │         │         │         │         │                  │
│         │ Formal  │ Place   │ Power   │ Verify  │ Signal Tap       │
│ IP      │         │         │         │         │                  │
│ Config  │ Cov     │ Route   │ Clock   │         │ Logic Analyzer   │
└─────────┴─────────┴─────────┴─────────┴─────────┴──────────────────┘
                                    │
                            ┌───────┴───────┐
                            │   ITERATE     │
                            │ RTL → Build   │
                            │ → Analyze     │
                            │ → Fix         │
                            └───────────────┘
```

### 1.2 各阶段详情

| # | 阶段 | 目的 | 输入 | 输出 | 必须 | 工具 |
|---|------|------|------|------|:--:|------|
| D1 | **RTL Design** | 编写硬件描述 | 需求规格 | .v/.sv/.vhd 文件 | ✅ | 编辑器, Claude Code |
| D2 | **Constraint Design** | 时序/管脚约束 | 板级原理图 | .xdc 文件 | ✅ | 编辑器, Claude Code |
| D3 | **Lint** | 静态规则检查 | RTL | 警告/错误列表 | 🔶 | Vivado Lint, Verilator, SpyGlass |
| D4 | **Functional Simulation** | 验证逻辑正确性 | RTL + Testbench | 波形 + 断言报告 | ✅ | xsim, ModelSim, Questa |
| B1 | **Synthesis** | RTL→门级网表 | RTL + XDC | Netlist + DCP | ✅ | synth_design |
| B2 | **Optimization** | 逻辑优化 | Synth DCP | Opt DCP | ✅ | opt_design |
| B3 | **Placement** | 单元布局 | Opt DCP | Placed DCP | ✅ | place_design |
| B4 | **Physical Optimization** | 物理优化 | Placed DCP | PhysOpt DCP | 🔶 | phys_opt_design |
| B5 | **Routing** | 布线 | PhysOpt DCP | Routed DCP | ✅ | route_design |
| A1 | **Timing Analysis** | 时序收敛检查 | Routed DCP | 时序报告 | ✅ | report_timing_summary |
| A2 | **Utilization Analysis** | 资源占用 | Routed DCP | 利用率报告 | ✅ | report_utilization |
| A3 | **Power Analysis** | 功耗估算 | Routed DCP | 功耗报告 | 🔶 | report_power |
| A4 | **Clock Analysis** | 时钟树检查 | Routed DCP | 时钟报告 | 🔶 | report_clocks |
| A5 | **DRC** | 设计规则检查 | Routed DCP | DRC 报告 | ✅ | report_drc |
| P1 | **Bitstream Generation** | 生成配置数据 | Routed DCP | .bit 文件 | ✅ | write_bitstream |
| P2 | **Hardware Programming** | 下载到 FPGA | .bit + JTAG | 配置完成的 FPGA | 🔶 | program_hw_devices |
| P3 | **Hardware Verification** | 上板验证功能 | FPGA + 外设 | 目视/仪器确认 | ✅ | 示波器, 逻辑分析仪, UART |
| P4 | **ILA Debug** | 片内逻辑分析 | Routed DCP + ILA IP | 波形数据 | 🔶 | ILA core + HW Manager |
| P5 | **UART Debug** | 运行时日志 | FPGA + UART pin | 文本日志 | 🔶 | 串口终端 |
| R1 | **Regression Testing** | 自动化回归 | 测试套件 | Pass/Fail 报告 | 🔶 | CI + 脚本 |

> 🔶 = 推荐但非强制；✅ = 每个项目必须

---

## 2. MCP Responsibility Mapping

### 2.1 决策矩阵

| 阶段 | MCP Tool? | 理由 |
|------|:--:|------|
| **RTL Design** | ❌ No | Claude Code 直接写文件。MCP 提供的是 Vivado 操作接口，不是编辑器。 |
| **Constraint Design** | ❌ No | 同上。XDC 文件编辑属于代码生成层。 |
| **Lint** | 🔶 Optional | 有价值但工具链复杂。`run_tcl` 可临时覆盖。Phase C+ 考虑。 |
| **Functional Simulation** | ✅ **Yes** | 这是之前缺失的关键环节。AI 在 RTL→Build 之间需要验证逻辑。独立进程（xvlog/xelab/xsim），不能复用 VivadoProcess。 |
| **Synthesis** | ✅ Yes | 已有隐含支持（`run_tcl`）。应升级为结构化 Tool。Phase B。 |
| **Optimization** | ✅ Yes | 同上，与 Synthesis 一起。 |
| **Placement** | ✅ Yes | 同上。 |
| **Physical Optimization** | ✅ Yes | 同上。 |
| **Routing** | ✅ Yes | 同上。 |
| **Timing Analysis** | ✅ **Done** | `report_timing_summary`。 |
| **Utilization Analysis** | ✅ **Done** | `report_utilization`。 |
| **Power Analysis** | 🔶 Phase C | 低优先级。`run_tcl` 可覆盖。 |
| **Clock Analysis** | ✅ **Done** | `get_clocks`。 |
| **DRC** | 🔶 Phase C | `run_tcl` 可覆盖。 |
| **Bitstream Generation** | 🔶 Phase B | 与 Build 工具一起。 |
| **Hardware Programming** | ✅ Yes | G5.3 已验证通过 `run_tcl`。应升级为结构化 Tool：`program_device`。 |
| **Hardware Verification** | ❌ No | 物理世界。AI 可辅助解读结果，但不能替代人眼/仪器。 |
| **ILA Debug** | 🔶 Phase D | 需要 ILA IP + HW Manager。独立的 `hw_server` 进程。长期有价值。 |
| **UART Debug** | 🔶 Phase D | G5.3 已验证软 UART。作为 MCP Tool 可让 AI 读取 FPGA 运行时日志。但硬件连接（USB-UART）不在 MCP 控制范围内。 |
| **Regression Testing** | 🔶 Phase E | CI 集成，非单次交互。 |

### 2.2 不应成为 MCP Tool 的阶段

| 阶段 | 原因 |
|------|------|
| RTL Design | 这是 Claude Code 的原生能力（写代码），不需要 MCP 中介 |
| Constraint Design | 同上 |
| Hardware Verification | 物理世界的观测（目视 LED、示波器），MCP 无法替代 |
| 第三方 EDA 工具 (Vitis, PetaLinux) | 独立工具链，各有自己的 CLI/API |

---

## 3. Process Architecture

### 3.1 进程分类

当前架构只有一个 `VivadoProcess`：

```
VivadoProcess → subprocess.Popen → vivado -mode tcl
```

但完整 FPGA 工作流涉及**三类独立进程**：

```
┌─────────────────────────────────────────────────────────────┐
│                    PROCESS ARCHITECTURE                      │
├───────────────┬─────────────────┬───────────────────────────┤
│ 类别           │ 进程            │ 通信方式                   │
├───────────────┼─────────────────┼───────────────────────────┤
│ Vivado Tcl    │ vivado -mode tcl│ stdin/stdout + marker     │
│ (VivadoProcess)│                 │                           │
├───────────────┼─────────────────┼───────────────────────────┤
│ Simulation    │ xvlog           │ 命令行参数 + exit code     │
│ (XSimProcess) │ xelab           │ + 日志文件                 │
│               │ xsim            │                           │
├───────────────┼─────────────────┼───────────────────────────┤
│ Hardware      │ hw_server       │ Tcl via Vivado HW Manager │
│ (HwProcess)   │ (hardware manager)│ (复用 VivadoProcess 部分) │
└───────────────┴─────────────────┴───────────────────────────┘
```

### 3.2 为什么需要独立的 XSimProcess

| 维度 | VivadoProcess | XSimProcess |
|------|:---:|:---:|
| 命令 | `vivado -mode tcl` | `xvlog`, `xelab`, `xsim` |
| 通信 | 交互式 Tcl | one-shot 命令行 |
| 输出 | stdout + marker | 日志文件 + VCD/WDB |
| 生命周期 | 长连接 (start→shutdown) | 三步骤 (编译→细化→仿真) |
| 并发 | 不能并发 | 可多个仿真并发跑 |
| 超时 | 命令级 | 仿真有 runtime 限制 |

**关键差异**：`VivadoProcess` 是长连接交互式 Tcl shell，`xvlog/xelab/xsim` 是短生命周期的命令行工具。仿真不需要 Vivado Tcl shell（仿真不加载 Vivado 工程），每个阶段输出的是文件而非 Tcl 返回值。

### 3.3 进程架构建议

```
mcp/vivado-mcp-win/
├── vivado_process.py    ← VivadoProcess (已有)
├── xsim_process.py      ← XSimProcess (新增)
│   ├── compile(sources)     → xvlog
│   ├── elaborate(top)       → xelab
│   ├── simulate(timeout)    → xsim
│   └── parse_vcd(path)      → assertion report
└── hw_process.py        ← HwProcess (Phase D)
    ├── program(bitfile)     → hw_server + Tcl
    └── monitor_uart(com)    → 串口读取 (Phase D)
```

---

## 4. MCP Tool Phased Roadmap

### 4.1 阶段总览

```
Phase A    Phase B     Phase C     Phase D     Phase E
(G4.3 ✅)  (G5.2)      (G6)        (G7)        (G8)
─────────  ─────────   ─────────   ─────────   ─────────
查询       Build       Simulation  Hardware    Regression
分析       流程        验证         Debug       CI
```

### 4.2 Phase A — Core Analysis (G4.3) ✅ 已完成

12 个只读查询 + Admin Tool

| Tool | 类别 |
|------|------|
| `get_vivado_info` | 环境 |
| `get_capabilities` | 环境 |
| `open_checkpoint` | 工程 |
| `close_design` | 工程 |
| `report_timing_summary` | 分析 |
| `report_utilization` | 分析 |
| `get_cells` | 查询 |
| `get_nets` | 查询 |
| `get_clocks` | 查询 |
| `get_ports` | 查询 |
| `get_property` | 通用 |
| `run_tcl` (Admin) | 管理 |

### 4.3 Phase B — Build Flow (G5.2)

结构化构建工具。当前通过 `run_tcl` 可完成，但缺乏参数校验和进度反馈。

| Tool | 输入 | 输出 |
|------|------|------|
| `synth_design` | top, part, flatten_strategy | 状态 + 日志路径 |
| `opt_design` | directive | 状态 |
| `place_design` | directive | 状态 |
| `phys_opt_design` | directive | 状态 |
| `route_design` | directive | 状态 |
| `write_bitstream` | output_path | bitstream 路径 |
| `open_project` | project_path | part, design_name |
| `create_project` | name, part, sources, constraints | — |

**设计原则**：每个命令对应一个 Tool。LLM 可以编排顺序（synth→opt→place→route→bitgen），进度可控，错误隔离。

### 4.4 Phase C — Simulation (G6)

| Tool | 输入 | 输出 | 依赖 |
|------|------|------|------|
| `compile_sim` | sources[], top | 状态 | XSimProcess |
| `elaborate_sim` | top | 状态 | XSimProcess |
| `run_simulation` | top, timeout, vcd_path | assertions[], pass/fail | XSimProcess |
| `parse_sim_log` | log_path | warnings[], errors[] | 文件读取 |
| `get_sim_assertions` | vcd_path | assertion_results[] | VCD 解析 |

**关键决策**：`compile_sim` + `elaborate_sim` + `run_simulation` 是三步骤独立的 Tool，不是合一的。因为：
- 编译失败时可以在编译阶段修复，不需要重新细化
- 细化失败独立排查
- 仿真可以用不同参数重跑

### 4.5 Phase D — Hardware Debug (G7)

| Tool | 输入 | 输出 | 依赖 |
|------|------|------|------|
| `program_device` | bitstream_path | 状态 | HwProcess (Vivado HW Mgr) |
| `get_device_status` | — | DONE status, device info | HwProcess |
| `connect_hw_server` | url | 状态 | HwProcess |
| `disconnect_hw_server` | — | 状态 | HwProcess |
| `read_uart` | port, baudrate, duration | lines[] | 串口库 |

ILA 相关（Phase D+）：
| `setup_ila_trigger` | trigger_condition | — | HwProcess |
| `capture_ila_waveform` | timeout | vcd_path | HwProcess |

### 4.6 Phase E — Regression & CI (G8)

| Tool | 输入 | 输出 |
|------|------|------|
| `run_test_suite` | suite_path | pass/fail + logs |
| `compare_timing` | baseline_json, current_json | diff_report |
| `compare_utilization` | baseline_json, current_json | diff_report |

---

## 5. Three-Layer Architecture (Revised)

### 5.0 核心洞察

当前 MCP 架构是两层：

```
Claude Code → MCP Tool → VivadoProcess/Vivado
```

这种扁平结构的问题是：**Claude Code 必须知道什么时候调用哪个 Tool**。对于简单查询（"WNS 是多少？"）这没问题。但对于开发流程（"帮我改 UART 波特率"），Claude Code 需要手动编排 5+ 个 Tool，每次决策都在 LLM 的 prompt 上下文里争夺注意力。

**解决方案：引入 Workflow Layer**

```
┌─────────────────────────────────────────────────────────────────┐
│                     THREE-LAYER ARCHITECTURE                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Claude Code                                                     │
│    │                                                             │
│    ▼                                                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LAYER 3: WORKFLOW                                        │   │
│  │  ─────────────────                                        │   │
│  │  develop_design()     → RTL → Sim → Build → Report        │   │
│  │  verify_design()      → Sim → Timing → Utilization        │   │
│  │  release_build()      → Build → Bitgen → Program          │   │
│  │  diagnose_failure()   → Parse error → Suggest fix         │   │
│  │                                                           │   │
│  │  Claude only needs ONE tool call per task.                │   │
│  │  The Workflow handles orchestration internally.           │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LAYER 2: TOOL                                            │   │
│  │  ─────────────                                            │   │
│  │  run_simulation()    run_build()    program_device()      │   │
│  │  report_timing()     get_cells()    read_uart()           │   │
│  │                                                           │   │
│  │  Each Tool does ONE thing. Tools are composable.          │   │
│  │  Workflows are built from Tools.                          │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  LAYER 1: PROCESS                                         │   │
│  │  ───────────────                                          │   │
│  │  VivadoProcess    XSimProcess    HardwareProcess          │   │
│  │                                                           │   │
│  │  Each Process wraps ONE executable.                       │   │
│  │  Processes have NO knowledge of FPGA workflows.           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                             │                                    │
│                             ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Vivado / xsim / hw_server                                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 5.1 各层职责

| 层 | 对上层隐藏什么 | 对上层暴露什么 | 谁调用 |
|---|---------------|---------------|--------|
| **Layer 1 (Process)** | 子进程管理、stdin/stdout、超时、清理、版本锁 | `send_tcl(cmd)` / `compile(sources)` / `program(bit)` | Layer 2 (Tool) |
| **Layer 2 (Tool)** | Tcl 命令语法、文件路径拼接、JSON 格式化 | 结构化输入/输出 Schema | Layer 3 (Workflow) 或 Claude Code |
| **Layer 3 (Workflow)** | 多 Tool 编排、错误恢复、重试、中间状态 | 单一意图式调用 | Claude Code |

### 5.2 为什么 Workflow 必须是一层，而不是写进 Prompt

写进 Prompt 的问题：

```
"请先调用 run_simulation，如果 PASS 就调用 synth_design，然后调用 place_design..."
```

- Prompt 膨胀：每个任务需要 200+ tokens 的指令
- 不可复用：换一个项目，Prompt 要重写
- 不可测试：Prompt 指令的正确性无法用单元测试验证
- 脆弱：Vivado 版本升级，所有 Prompt 都要改

Workflow Layer 的优势：

- Claude Code 调用 `develop_design({target: "修改 UART 波特率", auto_fix: true})`
- Workflow 内部：Sim → Build → Analyze → 如果 Timing FAIL → 自动调整约束 → 重试
- Claude Code 只看到最终结果
- Workflow 逻辑可以用 Python 测试

### 5.3 Workflow 设计原则

1. **一个 Workflow = 一个工程师意图**
   - `develop_design` = "我要开发这个设计"（Sim+Build+Report）
   - `verify_design` = "这个设计对吗"（Sim+Timing+Util）
   - 不是 `run_simulation_then_synth_then_place_then_route`

2. **Workflow 返回结构化结果，不是中间 Tool 输出**
   ```python
   # 不是:
   {"step1": "sim PASS", "step2": "synth OK", "step3": "WNS=16ns"}
   
   # 而是:
   {"status": "pass", "wns_ns": 16.6, "lut_used": 64, 
    "sim_assertions": 6, "issues": []}
   ```

3. **Workflow 内部可以调用其他 Workflow**
   - `release_build` 内部调用 `verify_design` → `program_device`

4. **Workflow 有明确的 exit gate**
   - 不是无限循环，而是 "最多重试 3 次，然后报告阻塞点"

### 5.4 Workflow 示例

```python
# Layer 3 — Claude Code 调用
develop_design(
    project="hello_fpga",
    changes="修改 BREATH_TIMER_MAX 为 500000，使呼吸周期变为 4 秒",
    auto_fix=True      # 如果 Timing FAIL，自动调整
)

# Workflow 内部编排:
#   1. generate_rtl(changes)        ← 未来: AI-generate
#   2. compile_sim(...)              ← Layer 2 Tool  
#   3. run_simulation(...)           ← Layer 2 Tool
#   4. if SIM_FAIL: return error
#   5. run_build(...)                ← Layer 2 Tool
#   6. report_timing(...)            ← Layer 2 Tool
#   7. if TIMING_FAIL and auto_fix: adjust_constraints() → back to 5
#   8. report_utilization(...)       ← Layer 2 Tool
#   9. return structured_result
```

### 5.5 修订后的路线图

```
G5.3  Workflow Architecture Design    (本文档更新)
      ↓
G6    Layer 1: XSimProcess + Layer 2: Simulation Tools
      ↓
G7    Layer 3: AI Development Workflow (develop_design, verify_design)
      ↓     (Simulation → Build → Report 自动编排)
      ↓
G8    Layer 1: HardwareProcess + Layer 2: program_device, read_uart
      ↓
G9    Layer 3: release_build workflow (Build → Bitgen → Program → Verify)
```

**关键变化**：
- Workflow 不是 Phase E 的"以后再说"，而是 G7 就引入（紧跟 Simulation 之后）
- Simulation 和 Build 在 Workflow 层**统一**——`develop_design` 内部编排 Sim→Build→Report
- Hardware 仍然后置（G8），但以 HardwareProcess + Hardware Tool + Hardware Workflow 三层形式引入

---

## 6. Simulation Architecture

### 5.1 为什么仿真应该是 first-class MCP capability

没有仿真的 FPGA 开发就像没有单元测试的软件开发：

- **当前状态**：RTL → Build → 烧录 → 看 LED。反馈周期 >5 分钟。
- **有了仿真**：RTL → Testbench → xsim（几秒）→ 波形/断言。反馈周期 <30 秒。
- **对 AI 的意义**：AI 在改 RTL 后可以立即跑仿真验证逻辑，而不需要等 Vivado 综合+烧录。

### 5.2 推荐架构

```
┌──────────────────────────────────────────────────────────────┐
│                     MCP Simulation Layer                      │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  server.py                                                    │
│    └── VivadoTools.run_simulation(tb, sources, timeout)        │
│          │                                                     │
│          ├── XSimProcess.compile(sources)                      │
│          │     └── subprocess.run([xvlog, -sv, ...])           │
│          │     → 返回: warnings[], errors[]                    │
│          │                                                     │
│          ├── XSimProcess.elaborate(top)                        │
│          │     └── subprocess.run([xelab, -L, ..., top])       │
│          │     → 返回: status                                  │
│          │                                                     │
│          └── XSimProcess.simulate(top, timeout, vcd_path)      │
│                └── subprocess.run([xsim, top, -R],             │
│                     timeout=timeout)                           │
│                → 返回: exit_code, assertion_lines[], log_text  │
│                                                               │
│  可选: assertion parser                                        │
│    └── 解析 xsim 输出中的 $display/$error/$fatal 行             │
│    └── 解析 VCD 中的关键信号值                                  │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 VCD / WDB 处理

| 格式 | 工具 | 策略 |
|------|------|------|
| **VCD** (Value Change Dump) | xsim 默认 | 纯文本，可用 Python 解析关键信号的值变化 |
| **WDB** (Vivado Waveform DB) | xsim `-view` | Vivado GUI 专用，MCP 不直接处理 |
| **SAIF** (Switching Activity) | xsim | 功耗分析用，Phase C+ |

**MCP 策略**：
- 仿真时生成 VCD（`$dumpfile` + `$dumpvars` 在 testbench 中）
- 提供 `parse_sim_log` 工具解析日志中的 `$display`/`$error`/PASS/FAIL
- 不试图替代波形查看器（那是 GUI 工具的强项）
- AI 可以通过日志中的断言和关键信号值来判断仿真结果

### 5.4 断言报告

Testbench 中的 `$display` / `$error` / `$fatal` 是 MCP 可解析的最重要信息：

```verilog
if (duty_out >= DUTY_MIN)
    $display("  PASS: duty at valid level");
else
    $display("  FAIL: duty = 0x%08h", duty_out);
```

`run_simulation` 返回时自动提取包含 PASS/FAIL 的行，格式化为结构化断言报告。

### 5.5 Testbench 标准

为支持 MCP 自动仿真，testbench 应遵循约定：

1. **断言用 `$display`**：`$display("  PASS: ...")` 或 `$display("  FAIL: ...")`
2. **VCD 可选但推荐**：`$dumpfile` + `$dumpvars`
3. **超时安全**：testbench 必须有 `$finish` 调用
4. **确定性**：每次运行结果相同（无随机种子依赖，除非显式使用 `$random`）

---

## 6. Hardware Integration

### 6.1 JTAG Programming

**应成为核心 MCP API**。

理由：
- 烧录是 AI→FPGA 闭环的最后一步
- G5.3 已验证可行（通过 `run_tcl` + Vivado HW Manager）
- 结构化 Tool 比 `run_tcl` 更可靠（参数校验、错误处理）

建议 Tool：

```
program_device:
  输入: bitstream_path
  流程: open_hw_manager → connect_hw_server → program → verify DONE
  返回: {status: "programmed"|"failed", done_pin: "HIGH"|"LOW"}
```

### 6.2 UART Monitor

**应成为 MCP Tool，但有限制**。

理由：
- AI 可以通过 UART 读取 FPGA 运行时输出
- G5.3 已验证可行（PowerShell 串口读取）
- 但硬件连接（USB-UART 适配器、COM 口）不在 MCP 控制范围内

建议 Tool：

```
read_uart:
  输入: port, baudrate, duration_seconds
  返回: lines[]  ← 文本日志
  限制: 需要物理 USB-UART 连接，MCP 无法自动检测 COM 口
```

**COM 口发现**：可以用 `serial.tools.list_ports` 列出可用端口，帮助 AI 自动选择。

### 6.3 ILA (Integrated Logic Analyzer)

**Phase D，有价值但复杂**。

理由：
- ILA 需要在设计阶段插入 ILA IP core
- 需要 `hw_server` 和 Vivado Hardware Manager
- 触发条件设置、波形捕获都需要与 Vivado 交互
- 是高级调试功能，不是日常开发必需品

建议：Phase D 再引入，当前 `run_tcl` 可临时覆盖。

---

## 7. Long-Term AI Development Loop

### 7.1 理想闭环

```
┌──────────────────────────────────────────────────────────┐
│              AI FPGA Engineer — 理想工作流                  │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  [1] 需求                                                  │
│    │  "LED 呼吸频率改为 3 秒"                               │
│    ▼                                                       │
│  [2] AI 修改 RTL                                           │
│    │  修改 DUTY_STEP / BREATH_TIMER_MAX                    │
│    ▼                                                       │
│  [3] AI 运行仿真  ←── 验证逻辑正确                          │
│    │  PASS → 继续                                          │
│    │  FAIL → 回 [2]                                        │
│    ▼                                                       │
│  [4] AI 运行 Build  ←── 验证时序/资源                       │
│    │  Timing PASS → 继续                                   │
│    │  Timing FAIL → 回 [2] 或修改约束                      │
│    ▼                                                       │
│  [5] AI 下载到 FPGA                                        │
│    │  编程成功 → 继续                                       │
│    │  编程失败 → 诊断 JTAG                                  │
│    ▼                                                       │
│  [6] 物理验证 / UART 监控                                   │
│    │  符合预期 → 完成                                       │
│    │  不符合 → 回 [2]                                      │
│    ▼                                                       │
│  [7] 提交 + 更新 Baseline                                   │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

### 7.2 自动化可行性评估

| 阶段 | 全自动 | 半自动 | 需人工 | 说明 |
|------|:--:|:--:|:--:|------|
| RTL 修改 | ✅ | | | LLM 擅长的代码编辑 |
| 仿真验证 | ✅ | | | 结构化断言，AI 能判断 PASS/FAIL |
| Build | ✅ | | | 已通过 MCP 自动化 |
| 时序分析 | ✅ | | | 结构化 JSON，AI 能解读 |
| 资源分析 | ✅ | | | 同上 |
| JTAG 烧录 | ✅ | | | 已通过 MCP 验证 |
| 物理验证 | | 🔶 | | 部分可自动（UART 日志），目视需要人 |
| ILA 调试 | | 🔶 | | AI 可解析波形，设置触发需经验 |
| Constraint 优化 | | 🔶 | | 时序闭包需要工程经验 |
| Architecture 设计 | | | ✅ | 顶层架构决策需要人 |

### 7.3 关键洞察

> **仿真是 AI 自主性的关键分水岭。**
>
> 没有仿真：AI 改 RTL → Build → 烧录 → 看结果。每次迭代 5-10 分钟。
> 有了仿真：AI 改 RTL → xsim（几秒）。发现错误立即修正。迭代密度提高 100 倍。
>
> 这意味着 AI 可以自主探索 RTL 修改、验证假设、修复 bug——
> 不需要每次等 Build+烧录。仿真让 AI 在"实验室环境"里快速迭代，
> Build+烧录变成最终验证步骤，而非每次调试的必经之路。

---

## 9. 最终路线图（修订版）

```
                    LAYER 1              LAYER 2              LAYER 3
                    (Process)            (Tool)               (Workflow)
                    ─────────            ──────               ─────────
G4.3 ✅             VivadoProcess        12 Tools             —
G5.3 ✅             (已有)               (已有)                —

G6 (当前)           XSimProcess          run_simulation       —
  Simulation        独立进程抽象          compile_sim          
  Infrastructure    XSimProcess.run()     elaborate_sim        
                    泛化执行接口          parse_sim_log         
                    非写死 xvlog/xelab                         
                                                              
G7                  —                    —                    develop_design
  Workflow Layer                                            verify_design
                                                             (编排 Sim→Build→Report)
                                                              
G8                  HardwareProcess      program_device       release_build
  Hardware           独立进程抽象          read_uart            (编排 Build→Bitgen→Program)
  Infrastructure                                           
                                          
G9                  —                    ILA tools            diagnose_failure
  Hardware           (复用 HardwareProcess) regression           (编排 Error→Fix→Retry)
  Closed Loop
```

### G6 验收标准：Simulation Infrastructure

目标不是"把仿真跑通"，而是**建立一个与 VivadoProcess 平级的、可长期扩展的 Simulation Domain**。

| # | 验收项 | 说明 |
|---|--------|------|
| 1 | **XSimProcess** | 独立进程抽象，不依附于 VivadoProcess |
| 2 | **泛化执行接口** | `XSimProcess.run(command)` — 不写死 xvlog/xelab/xsim 顺序，Tool 决定调用哪个 |
| 3 | **xvlog 支持** | 编译 RTL + Testbench |
| 4 | **xelab 支持** | 细化顶层模块 |
| 5 | **xsim 支持** | 运行仿真，支持超时 kill |
| 6 | **Assertions** | 从 `$display` 输出中提取 PASS/FAIL |
| 7 | **VCD/WDB** | 生成波形文件，提供路径供外部查看 |
| 8 | **Timeout** | 仿真超时自动 kill，防止死循环 |
| 9 | **JSON Result** | 结构化返回：status, assertions, errors, vcd_path |
| 10 | **不包含 Workflow** | 没有 `develop_design`，那属于 G7 |

**G6 不交付的东西**：
- ❌ Workflow 编排
- ❌ 自动 RTL 修复
- ❌ 多 testbench 并行
- ❌ 覆盖率统计

### 关键决策

1. **G6 只做 Simulation（Layer 1 + Layer 2），不做 Workflow**
   - 先把仿真能力跑通，Tool 层稳定后再上 Workflow
   - XSimProcess 独立于 VivadoProcess，生命周期完全不同

2. **G7 引入 Workflow Layer**
   - `develop_design` 把 Simulation + Build + Analysis 串成一条自动化流水线
   - Claude Code 不需要知道 xelab/synth_design/place_design
   - Workflow 逻辑可测试、可版本升级

3. **G8 才做 Hardware**
   - HardwareProcess + program_device + read_uart
   - 之后 Hardware 的 Workflow 自然融入现有框架

4. **Layer 之间严格单向依赖**
   - Workflow → Tool → Process → Executable
   - 不允许 Tool 直接访问 Executable（绕过 Process）
   - 不允许 Workflow 直接访问 Process（绕过 Tool）

---

## 附录：进程抽象对比

| | VivadoProcess | XSimProcess | HwProcess |
|---|:---:|:---:|:---:|
| 底层可执行文件 | `vivado -mode tcl` | `xvlog`, `xelab`, `xsim` | `hw_server` + Vivado Tcl |
| 通信模式 | 交互式 (长连接) | One-shot (短连接) | 混合 |
| stdout 处理 | Completion marker | 日志解析 | Tcl + 日志 |
| 超时模型 | 命令级 | 全流程级 | 操作级 |
| 并发安全 | ❌ (单实例) | ✅ (多实例) | ⚠️ (hw_server 单实例) |
| 启动时间 | ~2 min (冷启动) | <1 sec | ~5 sec |
| Phase | ✅ 已实现 | Phase C | Phase D |
