# SynthPilot 调研与对比分析报告

> 日期：2026-08-04
> 来源：[SynthPilot 官网](https://www.synthpilot.dev/) · [GitHub 教程仓库](https://github.com/LNC0831/SynthPilot_Tutorial)
> 对比对象：本项目 `Xilinx_Vivado_MCP` (27 tools + 2 Skills)

---

## 1. 项目定位

| 维度 | SynthPilot | 本项目 (VivadoMCP) |
|------|-----------|--------------------|
| 性质 | 商业产品（含永久免费层） | 开源项目 |
| 目标用户 | 通用 FPGA 工程师 | Zynq-7020 AI 智能体自治开发 |
| 核心理念 | 用 AI 替代 Vivado GUI 手动操作 | 让 Claude Code 成为自治 FPGA 工程师 |
| 成熟度 | v1.3.0+，900+ 注册用户 | 早期（B00/B01 ✅，B02 待开始） |
| 分发方式 | `uv tool install synthpilot` | `git clone` + venv + pip |

---

## 2. 架构对比

### SynthPilot

```
AI 工具 (Claude/Cursor/Codex)  ←→  SynthPilot MCP Server (本地 TCP)  ←→  Vivado (Tcl)
```

- 单体 MCP Server，通过本地 TCP 通信
- 支持多个 AI 客户端（Claude Code, Cursor, OpenAI Codex 等）
- `synthpilot setup` 自动注册到 AI 编辑器
- `synthpilot doctor --fix` 自诊断与自愈

### 本项目

```
Claude Code  ←→  stdio (MCP JSON-RPC)
                    │
                server.py
                    │
          ┌─────────┼─────────┐
          │         │         │
    VivadoTools  SimTools  HwTools
          │         │         │
    VivadoProcess XSimProcess  Serial
          │         │         │
       Vivado      xvlog/    COM ports
      -mode tcl  xelab/xsim
```

- **显式三层架构**：Process → Tool → Skill，职责分明
- 仿真工具**独立于 Vivado 长进程**，冷启动立即可用
- 规划中的**三 MCP 架构**（PL + Platform + PS），适配 Zynq 异构特性

---

## 3. 工具数量与覆盖度

| 类别 | SynthPilot Free | SynthPilot Pro/MAX | 本项目 |
|------|:-:|:-:|:-:|
| 版本/信息查询 | ✅ | ✅ | ✅ 3 tools |
| 设计查询 (cells/nets/clocks/ports) | ✅ | ✅ | ✅ 5 tools |
| 项目管理 | ✅ | ✅ | ✅ 2 tools |
| 综合/实现/比特流 | ✅ | ✅ | ✅ 4 tools |
| 时序与利用率分析 | ✅ | ✅ | ✅ 2 tools |
| 设计验证 (DRC) | ✅ | ✅ | ✅ 1 tool |
| 仿真 (xvlog/xelab/xsim) | ❌ | ✅ | ✅ 4 tools |
| 硬件编程 (JTAG) | ✅ | ✅ | ✅ 3 tools |
| UART 读取 | ❌ | ✅ | ✅ 2 tools |
| IP 配置 (Clocking/FIFO/BRAM) | ❌ | ✅ | ❌ |
| Block Design (Zynq PS/AXI) | ❌ | ✅ | ❌（规划在 B05） |
| ILA/VIO 调试 | ❌ | ✅ | ❌ |
| Linter | ❌ | ✅ | ❌ |
| 自定义 Tcl | ❌ | ✅ (MAX) | ✅ 1 tool |
| **总计** | **~40** | **475~500+** | **27** |

---

## 4. Skill / Workflow 对比

| 维度 | SynthPilot (oh-my-fpga) | 本项目 |
|------|--------------------------|--------|
| 数量 | 13 个专家工作流 | 2 个 Skill |
| 类型 | 针对**具体问题**：收敛时序、CDC 审计、Zynq SoC 启动等 | 针对**通用流程**：验证、迭代开发 |
| 分发 | Claude Code plugin / MCP prompts | 项目中 `skills/` 目录 |
| 未来规划 | — | B07 统一 Zynq Skill（需求分解 → Platform → PL → PS → 部署观测） |

---

## 5. 技术实现细节

| 维度 | SynthPilot | 本项目 |
|------|-----------|--------|
| 语言 | Python 3.10+ | Python 3.x |
| 安装 | `uv tool install` + 自动注册 AI 编辑器 | 手动 venv + pip + `.mcp.json` |
| Vivado 通信 | 本地 TCP + 原生 Tcl | subprocess batch mode + 自定义 marker 协议 |
| Vivado 版本 | 2018.1 ~ 最新 | 2023.1（锁定） |
| 器件支持 | 全系列 (7-series ~ Versal) | 仅 XC7Z020CLG400-2 |
| 跨平台 | Windows + Linux | Windows only |
| 结构化响应 | 未公开 | 类型化 dataclass + ToolResponse JSON |
| 自诊断 | ✅ `doctor --fix` | ⚠️ 仅 version_guard |
| 仿真引擎 | 内置于 Pro+ | 独立 XSimProcess，冷启动立即可用 |

---

## 6. 本项目独特优势

1. **开源**：完全可审查、可修改、可扩展
2. **更清晰的架构分层**：Process → Tool → Skill 三层，每层职责明确
3. **结构化类型响应**：所有工具返回 Pydantic 风格的 dataclass（`TimingSummary`、`UtilizationSummary`、`CellInfo` 等），便于 AI 消费和自动判定
4. **设计验证工具** (`validate_design`)：后置条件检查（clocks_defined / ports_assigned / timing_valid / part_known），捕获假 PASS
5. **仿真独立运行**：XSimProcess 不依赖 Vivado 长进程，冷启动期间仿真仍可用
6. **硬件闭环验证**：UART 读取 + JTAG 编程，实现从 RTL 到真板的完整闭环
7. **统一 MCP + 内部三域架构**：Platform / PL / PS 作为代码域分离，部署为一个统一 zynq_mcp Server（规划中），兼顾职责清晰和单一入口
8. **双 Agent 验证方法**：B08 白盒验收 + B09 黑盒复现，证明成功来自 Skill 而非个人经验
9. **测试驱动开发**：MCP 能力必须由测试需求导出，禁止"先做了再找用途"

---

## 7. 从 SynthPilot 可借鉴的方向

| 方向 | 优先级 | 说明 |
|------|:------:|------|
| 安装自动化 | 🔴 高 | `setup` 命令 + 自动注册 AI 编辑器，替代手动 `.mcp.json` |
| 自诊断能力 | 🟡 中 | `doctor --fix` 模式，检测 Vivado 路径、license、JTAG 驱动 |
| IP 配置工具 | 🔴 高 | Clocking Wizard、FIFO、BRAM — 本项目 B05 的目标 |
| Block Design 工具 | 🔴 高 | Zynq PS7、AXI Interconnect — 本项目 B05 的目标 |
| 专家工作流 | 🟡 中 | 在通用 Skill 之上增加针对时序收敛、CDC 审计的命名工作流 |
| 多 AI 客户端 | 🟢 低 | 支持 Cursor、Codex 等，扩大用户群 |
| `uv tool` 分发 | 🟢 低 | 比 git clone + venv 更标准化 |

---

## 8. 生命周期与串行执行对比

### 8.1 对比前提

以下对比仅基于 SynthPilot 公开资料（官网、GitHub 教程仓库、公开文档）。
SynthPilot 内部为闭源商业产品，标注"公开资料无法确认"不意味着它肯定缺少某项能力，
只表示无法从公开证据中确认。

### 8.2 详细对比

| 维度 | SynthPilot | 本项目 v0.3 (规划中) |
|------|-----------|----------------------|
| **命令串行化** | 公开资料无法确认是否有全局命令队列 | 全局单通道 + preflight gate 强制执行 |
| **get_run_status** | 公开资料确认存在 `get_run_status` 用于查询 Vivado 任务 | `get_operation_status(id)` poll + `wait_operation(id, timeout_s)` bounded wait |
| **多 Vivado 实例** | 公开资料确认 Free 版支持 1 设备；公开资料无法确认是否支持多 Vivado 进程 | 1 个全局 Worker 进程树；禁止启动第二个 |
| **挂起仿真恢复** | 公开资料无法确认 | `diagnose()` + `recover()` 显式契约 |
| **串口 busy 检测** | 公开资料无法确认 | Preflight gate P9 资源安全检查 |
| **Operation ID** | 公开资料确认存在 `operation_id` 用于长任务追踪 | UUID per command；持久化在 Execution Ledger |
| **持久化执行账本** | 公开资料无法确认 | 原子 JSON ledger；跨 MCP 重启恢复 |
| **MCP 重启恢复** | 公开资料确认 `doctor --fix` 自诊断；公开资料无法确认任务状态恢复能力 | 完整状态恢复：ledger 读取 → 进程验证 → 心跳检测 → 状态判定 |
| **前置进程检测** | 公开资料无法确认 | 10 点 preflight（每点独立机器可判定） |
| **流程阶段保护** | 公开资料无法确认 | Zynq stage sequence 强制；上一步 OUTCOME_UNKNOWN → 阻塞下一步 |
| **分层超时模型** | 公开资料无法确认是否有分层超时 | 4 层：wait_timeout / operation_deadline / heartbeat_timeout / cleanup_timeout |
| **精确进程所有权** | 公开资料无法确认 | PID + process_start_time + executable_path + server_instance_id + worker_generation |
| **跨进程实例保护** | 公开资料无法确认 | 双层锁：进程内 asyncio.Lock + OS 跨进程 instance.lock |
| **重复请求去重** | 公开资料无法确认 | 相同 tool + args + session + stage → DUPLICATE_REQUEST |
| **MCP 架构** | 单体 MCP Server（通过本地 TCP） | 统一 MCP Server + 内部三域（Platform/PL/PS）（规划中） |
| **失败模式** | 公开资料无法确认是否区分 crash/timeout/outcome_unknown | 明确区分：FAILED / TIMED_OUT / INTERRUPTED / UNRESPONSIVE / OUTCOME_UNKNOWN / RECOVERY_REQUIRED |
| **自动重试策略** | 公开资料无法确认 | 命令类操作决不自动重试；查询类操作有限恢复方案（需显式记录） |

### 8.3 关键差异总结

本项目的生命周期管理在以下方面具有可审计、可验证的架构优势：

1. **持久化状态**：Execution Ledger 使 MCP 重启后能恢复"上一个任务是什么"，而不依赖内存对象或 Agent 记忆。
2. **分层超时**：不把所有超时混为一类——等待超时、任务期限、心跳超时、清理超时各自独立。
3. **进程所有权**：不依赖 PID 单一字段，结合启动时间、路径、generation 和 instance_id 防止 PID 复用误判。
4. **Fail-closed**：状态不明确时禁止继续流程；不自动杀进程；不自动重试命令类操作。
5. **显式恢复**：diagnose/recover 作为独立工具，可审计、可机器判定。

SynthPilot 作为成熟的商业产品，在工具广度（500+ tools）、安装体验（`uv tool install` + 自动注册）、
自诊断（`doctor --fix`）等方面显著领先。上述对比不构成产品优劣判断，仅记录在本项目架构中
可验证的能力特性。SynthPilot 的闭源特性意味着其内部实现无法通过公开资料确认或否认。

---

## 9. 结论

**SynthPilot** 是一个成熟的商业 FPGA MCP 产品，用 500+ 工具覆盖 Vivado 几乎所有 GUI 操作。其核心价值在于**广度**、**开箱即用**和**安装体验**。对于需要快速将 AI 引入现有 FPGA 工作流的工程师，它是目前最完整的选择。截至目前约有 900+ 注册用户。

**本项目** 是一个更聚焦、更有架构深度的开源方案。它不追求工具数量，而是追求：

- **正确的架构**：统一 MCP 入口 + 内部 Platform/PL/PS 三域 + Adapter 层
- **正确的执行模型**：单执行通道 + 持久化执行账本 + 分层超时 + 前置检测
- **正确的验证**：从仿真到真板闭环，双 Agent 黑盒复现
- **正确的流程**：Skill 驱动自治开发 + Zynq 阶段顺序保护
- **正确的测试**：每个能力都由验收需求驱动

本项目的长期差异化竞争力在于 **Zynq SoC 的 PS/PL 协同自动化**——这一能力 SynthPilot 尚未深入覆盖，
而本项目从架构层面就为它做好了准备。v0.3 架构修订（统一 MCP + 单执行通道 + Execution Ledger）
进一步强化了生命周期管理的可审计性和安全性。

---

## 附录：SynthPilot 定价

| 计划 | 价格 | 工具数 | 设备数 |
|------|------|:------:|:------:|
| Free | ¥0（永久） | 40 | 1 |
| Trial | ¥1（7 天） | 475+ | — |
| Pro | ¥29/月 或 ¥256/年 | 475+ | 2 |
| MAX | ¥49/月 或 ¥399/年 | 500+ | 3 |
