# Zynq-7020 智能体开发 Brick 进度规划

> 版本：v0.18
> 日期：2026-08-13
> 状态：B09 公开 MCP 纯黑盒验收通过，契约勘误已关闭；Execution Observation O1–O6 FROZEN；O7 R3 PASS；O8 NOT STARTED
> 架构依据：`docs/architecture_ai_zynq7020.md` v2.3.1
> 修订：v0.18 — 用户审核 O7 R3 黑盒证据后授权关闭 B09 公开 MCP 契约勘误，B09 标记 COMPLETE；B10 转入等待用户确认基线与下一切片决策（O8），不再被 B09 勘误阻塞

## 1. 本文档的作用

本文档只回答三个问题：当前做哪个 Brick、该 Brick 交付什么、满足什么条件才能进入下一个 Brick。

顶层架构仍以 `architecture_ai_zynq7020.md` 为准。本规划不重复解释三域四层、P1-P8 或完整 API 清单，也不代替 Skill、MCP、测试的具体开发记录。

## 2. 文档组织方式

不建立三份不断膨胀的“总开发文档”。Brick 0 经审核后建立以下三个持续演进的目录：

```text
docs/
├── architecture_ai_zynq7020.md       # 冻结的顶层架构
├── brick_development_plan.md          # 唯一的 Brick 状态与顺序索引
└── development/
    ├── skill/                         # Skill 的设计、实现和问题处置记录
    ├── mcp/                           # PL / Platform / PS MCP 的迭代记录
    └── tests/                         # 测试项目、故障注入和验收证据
```

现有 `docs/development/G0-G12` 是历史材料，Brick 0 只能建立索引和归类方案，不应直接覆盖或改写。

新增文档按 Brick 追加，例如：

```text
development/skill/B01_standard_zynq_flow.md
development/mcp/B03_common_contract.md
development/mcp/B04_pl_mcp_adapter.md
development/tests/B08_gpio_whitebox_report.md
development/tests/B09_gpio_agent2_blackbox_report.md
```

规则：

1. 一个 Brick 可以在多个目录各新增一份与其职责相关的记录。
2. 不把后续经验回填成一份无法追溯的巨型文档；修正应追加变更记录并链接原文。
3. 每份记录至少包含输入、变更、输出、验证证据、遗留问题和对应 Brick 编号。
4. `brick_development_plan.md` 只更新状态、门禁结论和文档链接，不收纳实现细节。

## 3. 执行原则

1. **严格串行过门禁**：当前 Brick 未验收，下一 Brick 只能做调研，不能进入实现。
2. **先证明再扩展**：Phase B 只覆盖 PL 基线、PS UART 和 AXI GPIO 三个测试层级。
3. **复用现有 PL 能力**：`Xilinx_Vivado_MCP` 是 Vivado 进程层的基础，不重写 Vivado/XSim 进程层。
4. **统一 MCP 入口、内部模块化**：最终只向智能体暴露一个 zynq_mcp Server；Platform、PL、PS 作为内部责任域（domains）继续存在，但不是独立 MCP Server。Skill 内部可按标准流程拆分参考文件。
5. **MCP 只提供能力**：zyng_mcp 的 Platform/PL/PS domains 不承担需求分解和工程决策。
6. **测试不是最后补做**：每个能力都必须由测试需求导出，并在同一 Brick 留下证据。
7. **单执行通道**：全系统只有一条执行通道。同一时间只允许一个活动 Operation、至多一个受监管的 EDA 工具子进程。所有领域命令严格串行，不允许跨域并行。
8. **错误经验双落地**：新发现的故障既要追加到 Skill 的 debug/recovery 指南，也要增加相应回归或故障注入测试。
9. **禁止静默搬迁**：文件移动、删除、仓库合并和 Git 历史处理必须出现在 Brick 0 方案中，经审核后执行。

## 4. Brick 总览

| Brick | 目标 | 主要区域 | 当前状态 |
|---|---|---|---|
| B00 | 盘点并规范现有项目 | 全项目 | ✅ **完成** |
| B01 | 固化标准流程与最小验收需求 | Skill + Tests | ✅ **完成** |
| B02 | 建立 MCP 公共契约与三服务框架（过渡性开发资产，非最终部署形态） | MCP + Tests | ✅ **完成** |
| B03 | 固化板卡配置包与环境基线 | MCP + Tests | ✅ **COMPLETE / FROZEN** |
| B04 | 统一 zynq_mcp 基础入口、执行账本、单通道生命周期、Vivado/PL Adapter 与 PL 能力 | MCP + Tests | ✅ **核心 COMPLETE / FROZEN；后续 PL 能力已集成** |
| B05 | 在统一 zynq_mcp 内实现 Platform/AXI Domain | MCP + Tests | ✅ **COMPLETE / FROZEN** |
| B06 | 在统一 zynq_mcp 内实现 PS/ARM Domain | MCP + Tests | ✅ **COMPLETE / INTEGRATED** |
| B07 | 统一 Skill 调用唯一 zynq_mcp，完成 GPIO Workflow | Skill + MCP | ✅ **COMPLETE / CONTRACT ERRATUM CLOSED** |
| B08 | Agent1 完成 GPIO 白盒验收与故障注入 | Tests + Skill | ✅ **WHITE-BOX HARDWARE PASS (R6)** |
| B09 | Agent2 在干净环境完成黑盒复现 | Tests | ✅ **COMPLETE（公开 MCP 纯黑盒 PASS）** |
| B10 | 冻结 GPIO 纵向切片 v1，确定下一切片 | 全项目 | ✅ **O8 冻结包 COMPLETE（2026-08-14）；下一切片方向已由 B11 承接** |
| B11 | 泛化框架黑盒验证：Skill/MCP 去 GPIO 化 + 6-LED 考题黑盒重验 | Skill + MCP + Tests | ✅ **COMPLETE（2026-08-16）：全六阶段闭环，Agent2 终验黑盒 PASS** |

## 5. 逐 Brick 交付与门禁

### B00：现有项目盘点与规范化

目标：先弄清已经有什么、哪些已验证、哪些可复用、哪些只是生成物或历史试验，再建立清晰且可回退的项目秩序。

Agent1 首先只提交整理方案，不立即移动或删除文件。方案必须包含：

- 顶层所有目录、独立 Git 仓库、MCP、Skill、测试项目、板卡资料和开发文档的清单。
- 将内容标为：当前核心、可复用、历史参考、测试证据、工具生成物、外部厂商资料、待确认。
- 当前入口与依赖关系：`.mcp.json`、Vivado/Vitis 路径、绝对路径、板卡串口/JTAG 配置、项目间引用。
- 现有验证结论及其证据位置，特别是 Vivado MCP、AXI GPIO、PS/ARM、JTAG 和 UART。
- 建议的目标目录树及逐项 `旧路径 -> 新路径` 映射。
- 立即可整理项与延后重构项的明确区分。
- Git 历史保留、回滚、备份和每批次验证方法。
- 拟删除或忽略的生成物清单；没有审核批准不得删除。
- 整理后唯一入口说明和文档索引方案。

边界：

- B00 可以整理日志、缓存、临时工程、重复文档、入口和索引。
- B00 不得提前把 `Xilinx_Vivado_MCP` 物理拆成 PL MCP 与 Platform MCP，也不得借整理改变已冻结的责任边界。
- `D:\BaiduNetdiskDownload\AX7020_2023.1` 是外部厂商资料源，应记录来源和校验值，不应未经选择整包复制进项目。

执行分两段：

1. B00-A：Agent1 提交整理方案；由审核方和用户确认。
2. B00-B：按批准方案分批整理；每批后运行相应旧基线，失败即停止并回退该批。

完成门禁：

- 项目入口、活动代码、历史资料和生成物可以被明确区分。
- 无文件无故丢失，无独立仓库历史被破坏。
- 现有 Vivado MCP 和已验证硬件链路的基线结果不倒退。
- `docs/development/skill`、`mcp`、`tests` 三个迭代目录及索引建立完成。
- Agent1 提交 B00 完成报告，审核通过后方可进入 B01。

### B01：标准 Zynq 流程与最小验收需求

目标：把“需求如何变成可运行的 Zynq 工程”写成统一 Skill 的流程骨架，并用测试需求约束 MCP，而不是先无限扩展 API。

交付：

- 一份端到端流程骨架：需求分析 → PS/Platform/PL 分工 → 工程生成 → 构建 → 部署 → 观测 → 判定 → 恢复。
- GPIO 最小项目需求：ARM 通过 AXI GPIO 控制 4 个 PL LED，UART 输出机器可判定的 PASS/FAIL 证据。
- T00：现有 PL MCP 回归；T01：PS UART Hello；T02：AXI GPIO 全链路。
- 明确成功判据、失败判据、超时、需要保存的 Artifact 和故障注入项。
- 从上述流程反推最小 MCP 能力表，暂不追求架构文档中的完整 API 数量。

完成门禁：每个计划实现的 MCP API 都能对应到至少一个流程步骤或验收项；没有“先做了再找用途”的 API。

### B02：MCP 公共契约与三服务框架

目标：先统一行为语义，再开发具体工具。B02 交付了三个独立 MCP skeleton（`zynq_platform`/`zynq_pl`/`zynq_ps`），
后续架构演进为统一 zynq_mcp（见 B04），三域作为内部 domains 继续存在。

> **历史标注（v0.2 修订）**：B02 的三个独立 MCP skeleton 是过渡性开发资产。
> 最终产品形态只有一个 zynq_mcp Server。B02 的契约、Artifact、Lock 和错误模型保留为共享基础。

交付：

- 统一 `ToolResponse`、错误码、日志字段和严格成功/失败语义。
- `context = {session_id, board_id, project_path, lease_holder}`。
- query / set / command 分类；command 返回 `operation_id`，不承诺幂等。
- 工程锁和 JTAG 锁的公共实现接口。
- Artifact 最小 Schema、revision 算法、SHA256 校验和 stale 拒绝规则。
- PL、Platform、PS 三个 MCP 的可启动空框架和能力发现接口（过渡性）。

完成门禁：契约测试通过；三个 MCP 对同类错误给出一致且机器可判定的结果。

### B03：板卡配置包与环境基线

目标：让板卡参数只有一个受校验的数据源，消除个人电脑绝对路径和手工隐含配置。

交付：

- AX7020 `board_profile`、PS7 preset、XDC 及来源 SHA256。
- Vivado/Vitis/XSCT、器件型号、JTAG cable、UART 的环境探测与诊断记录。
- DDR physical/configured、QSPI physical/linear window、PL/PS LED 和时钟参数校验。

完成门禁：新会话能只凭板卡配置包完成环境预检；配置漂移会被明确拒绝。

### B04：统一 zynq_mcp 基础入口 + 执行账本 + 单通道生命周期 + Vivado/PL Adapter

目标：建立统一 zynq_mcp Server（唯一对智能体暴露的 MCP 入口），实现 Execution Ledger、
单执行通道、Preflight Gate、Recovery 入口和受监管的 Vivado 子进程管理。

最终产品：Platform/PL/PS 继续作为三个责任域（domains）存在，但只是 zynq_mcp 的内部模块，
共享一个 ZynqContext、一个 Execution Ledger、一个执行通道、一个活动 Operation、一个 Preflight Gate。

子步骤：

- **R1**（FROZEN）：统一 skeleton、双锁 Instance Guard、原子 Execution Ledger、Preflight Gate（P1-P10）、
  Recovery、Session 管理、9 个控制 API、单实例模型、B03 缓存勘误、Package Lock 崩溃安全性。
- **R2**（FROZEN）：将现有 Vivado Bridge 迁移为统一 zynq_mcp 内部 PL Adapter。
  接入 SingleWorkerController、Ledger、Process Guard、Preflight/Recovery。
  全局单 Worker、无 pool、无 per-session Worker、无自动 rebuild。
- **R3**（PENDING FREEZE CONFIRMATION）：接入最小 PL 领域 API（`pl_generate_system_top` 等）。
  - **R3.0**（FROZEN）：Domain Runner (Command/Set/Query)、Operation Runner、dedup、单执行通道。
  - **R3.1-A**（FROZEN）：E003 Stage Gate + E004 Stage Advance + E005 board_profile_sha256。
  - **R3.1-B**（FROZEN）：system_top 组件（Manifest binder + Verilog parser + generator）。
  - **R3.1-C**（FROZEN）：MCP 公开注册 `pl_generate_system_top`；Public MCP SDK + Contract tests 与 Agent3 smoke 已通过。
  - **R3.2–R3.5**：原计划未逐子步骤形成独立冻结记录；对应 PL 能力后来在 B07 集成阶段进入统一 MCP。该历史文档缺口不得被解释为 B09 公开 MCP 闭环已经通过。

完成门禁（R3.0 历史基线，已由后续 R3.1 子步骤升级）：

> **R3.0 历史基线**（2026-08-06）：zync_mcp/tests 228 collected；mcps 全量 669 passed / 1 skipped；
> list_tools=9；PL 组件已交付但未公开注册。

当前 R3.1-C 完成门禁（等待用户冻结确认）：

- list_tools=10（已注册 pl_generate_system_top）
- test_r3_1c_public.py：25 passed
- zynq_mcp/tests：253 collected，253 passed
- mcps 全量：694 passed，1 skipped（B02 POSIX-only），0 failed，695 collected
- Agent3 R3.1-C preconditioned public MCP smoke（待 Agent3 调用）
- R3.1-C Controlled Freeze Package Revision 3 已提交

### B05：在统一 zynq_mcp 内实现 Platform/AXI Domain

目标：在统一 zynq_mcp 的 `domains/platform/` 内实现 GPIO 纵向切片需要的 BD/PS7/AXI 能力。

最小范围：创建 BD、加入和配置 PS7、加入 AXI GPIO、连接接口/时钟/复位、分配地址、校验设计、生成 wrapper、导出 Platform XSA 与 Manifest。

边界：Platform Domain 管通信拓扑和 BD；不拥有最终 Bitstream，不操作 JTAG，不决定 GPIO 地址、时钟或复位方案。

完成门禁：在无手工 GUI/Tcl 补丁的情况下生成可复现且地址一致的 Platform XSA；Artifact 校验通过。

> **B05 完成记录（2026-08-08）**：
> - 交付：1 个公开 MCP tool（`platform_generate`），`list_tools`=11
> - 生产代码：`domains/platform/platform_domain.py`、`adapters/vivado/` 复用
> - 契约：`artifact_schema.py` 新增 `resolve_root`（兼容扩展，非 breaking）
> - 遗留平台复用：14 项对比文档 `LEGACY_COMPARISON.md`（10 reused, 4 diverged）
> - Component tests：16 passed | Host-live：7 passed（337.45s, Vivado 真实执行）
> - Black-box（Agent3 独立全新上下文）：3/3 scenarios, 42/42 assertions PASS
> - 全量回归：905 passed, 1 skipped, 0 failed
> - Runtime 隔离：runner 使用独立 `ZYNQ_RUNTIME_ROOT` temp dir，无跨 run 状态泄漏
> - 关键 SHA256 冻结：
>   - `artifact_schema.py`: `sha256:381dac32c76b65febcd2aecffb4e2ccede0d32a4f7c7b4ca4a84f48b7cda4418`
>   - `platform_domain.py`: `sha256:102264f09cb171724b0f273bdabb17b6744cab18c66057966231432d923c1ed2`
> - 未自行冻结；Agent3 由 Manager 放行，非 Agent1 调用

### B06：在统一 zynq_mcp 内实现 PS/ARM Domain

目标：在统一 zynq_mcp 的 `domains/ps/` 内实现 PS 软件和真板控制闭环。

最小范围：导入 XSA、创建 platform/BSP/app、加入源码、编译、连接与选择目标、reset、initialize、download、run、halt、wait、UART 读取、基础恢复与 DAP 诊断。

烧录边界：Bitstream 编程归 PL Domain；ELF 下载、ARM 调试和 UART 观测归 PS Domain。

完成门禁：T01 在真板稳定输出机器可判定的 UART PASS；重复运行和常见 JTAG 故障恢复有证据。

> **B06 库阶段记录（2026-08-08，历史）**：策略为「库代码先开发 → B05 FREEZE 后集成」，零冲突并行。
> - 库阶段交付（4 Agent 并行，全部 sonnet）：
>   - Agent A：`adapters/xsct/` — XsdbBridge + XsctBridge + 27 Tcl templates（61 tests）
>   - Agent B：`adapters/uart/` — SerialAdapter（22 tests, 含 2 device_live）
>   - Agent C：`domains/ps/` — jtag_target + target_control + memory_access + target_recovery（21 APIs, 97 tests）
>   - Agent D：`domains/ps/` — debug_session（7 APIs, 26 tests）
> - 库阶段合计：~200 tests, 26 新文件, 0 共享文件修改
> - 集成阶段待做：`capabilities.py`/`dispatcher.py`/`domain_runner.py` 注册 + MCP SDK contract tests
> - BSP/Build 管线（需 B05 Platform XSA）也属于集成阶段

> **B06 集成状态更新（2026-08-12）**：PS/ARM/JTAG/UART 能力已经进入统一 `zynq_mcp`，并在 B08/B09 真实板卡流程中完成 ELF、JTAG 和 UART 验证。当前机械前缀统计为 48 个 `ps_*` 工具；能力元数据计数仍需在 B10 前统一。

### B07：统一 Skill 调用唯一 zynq_mcp，完成 GPIO Workflow

目标：把 B01 的流程骨架和 B04-B06 的工具能力连接成一个对智能体可用的 Skill。
交付 Skill 文档 + E2E 全链路验证。

> **B07 完成记录（2026-08-10）**：
> - Skill 文档：`skills/zynq_gpio/`（SKILL.md + 8 phase 文件，716 行）
> - 覆盖：需求模板 → Phase 0-7 全部 7 阶段，每阶段含 Skill 决策、MCP 工具调用序列、产物验证、失败恢复
> - E2E 验证：全链路 7 Phase 全部 PASS（P1 平台 5min → P2 综合 1min → P3 编译 1min → P4 12/12 → P5 部署 → P6 PASS）
> - UART 输出：`=== AX7020 ARM Test G11 ===` + LED 交替 `1010`/`0101`，verdict=PASS
> - 关键修复：新增 `ps_load_hardware`（loadhw）、`ps_ensure_arm_accessible`（DAP 恢复）、`pl_program_fpga`（XSDB fpga -f）
> - VivadoTclBridge：直接 `vivado.exe -mode tcl` 取代旧 MCP 两层 stdio
> - 工具总数：**101** | 全量回归：**1178 passed**
> - 未自行冻结；后续已进入 B08/B09

> **B07 契约勘误（2026-08-12，已于 2026-08-13 关闭）**：Skill 主流程曾包含 standalone `VivadoTclBridge`、手工发布 PL Manifest 和手工 `make` 三条逃生通道。O6 已删除这些通道并完成 Agent1 全公开 MCP 白盒重放；O7 R3 由全新 Agent2 黑盒复验通过；用户审核后关闭勘误。详见 [B09_public_mcp_contract_erratum.md](development/tests/B09_public_mcp_contract_erratum.md)。

### B08：Agent1 白盒验收

目标：由实现者证明每层为什么成功或失败，并固化回归证据。

交付：

- T00、T01、T02 的完整日志、Manifest、XSA、Bitstream、ELF 和 Run Manifest。
- GPIO LED 行为与 UART PASS 的双证据。
- 旧 ELF、错误 board profile、地址冲突、JTAG 中断等故障注入结果。
- 每个实际故障对应的 Skill debug 条目和自动回归测试。

完成门禁：所有必测项通过；失败用例被正确拒绝或恢复；没有未记录的手工步骤。

> **B08 完成记录（2026-08-11）**：Agent1 R6 白盒硬件流程 PASS，Platform、PL、PS、Consistency、JTAG、UART 与 Observation 均有真实证据。该结果保留为白盒功能证据；其中使用的手动逃生通道不满足重新打开的公开 MCP 契约。证据：`workspaces/gpio_b08_r6_20260811/B08_R6_TEST_REPORT.md`。

### B09：Agent2 黑盒复现

目标：证明成功来自 Skill 与 MCP，而不是 Agent1 的上下文和个人经验。

Agent2 只获得：需求、统一 Skill、已注册的 zynq_mcp、板卡配置说明和干净工作目录。不得获得 Agent1 对话、黄金工程操作步骤、隐藏 Tcl 或 `run_tcl` 权限。

完成门禁：Agent2 从零生成并验证 T02；能够识别故意提供的 stale ELF 和错误板卡 revision；结果可由证据自动判定。

> **B09 R3 结果（2026-08-12）**：真实硬件 `GPIO_E2E_PASS`、8/8 WROTE/READ、Consistency 12/12、JTAG/UART 均通过。但 Agent2 按 Skill 直接使用内部 `VivadoTclBridge`、内部 Manifest publisher 和手工 `make`，所以只能判定为 `HARDWARE_FUNCTIONAL_PASS`，不能判定为 `PUBLIC_MCP_CONTRACT_PASS`。原报告保持为证据：`workspaces/gpio_b09_r3_20260812/REPORT_B09_R3_Agent2.md`。

> **O7 R1 结果（2026-08-13）**：全新 Agent2 已通过项目外 Skill/MCP 隔离环境执行，终态 `FAIL / NOT FROZEN`。公开 `platform_generate` 以 `BACKEND_START_FAILED` 终止，未进入 PL/PS/JTAG/UART；同时 Agent2 使用 shell 读取 Skill，违反本轮硬门禁。详见 `docs/development/mcp/B09_O7_R1_failure_report.md`。

> **O7 R2 结果（2026-08-13）**：R1 launcher 缺陷修复并通过隔离预检后，另一全新 Agent2 成功完成 Platform、PL synthesis/place/route/timing，但 `pl_generate_bitstream` 因公开目标父目录不存在且 copy success 未机械验证，以 `ARTIFACT_STALE / MANIFEST_PUBLISH_FAILED` 终止。详见 `docs/development/mcp/B09_O7_R2_failure_report.md`。

> **O7 R3 结果（2026-08-13）**：在新的 `D:\_o7_external\agent2_20260813_r3` runtime/workspace 中，另一全新 Agent2 仅用 Skill + 公开 `zynq_mcp` 完成 P1–P6。Platform、PL、PS 均发布 Manifest，Consistency 12/12；真实硬件 UART 8/8 读写相等并出现 `GPIO_E2E_PASS`；轨迹仅有一次只读 Skill 加载，此后 command execution 为 0；JTAG/UART/session 清理通过。O7 判定 `PASS / AWAITING USER REVIEW`，详见 `docs/development/mcp/B09_O7_R3_pass_report.md`。

> **B09 勘误关闭（2026-08-13）**：用户审核 O7 R3 黑盒证据后授权关闭公开 MCP 契约勘误。另在本仓库 `D:\_b09_verify_20260813\` 隔离环境完成一次独立黑盒复测，结论一致（PASS，8/8 readback，三 Manifest 自动发布，Consistency 12/12，边界审计 0 违规）。B09 标记为 **COMPLETE**。详见 `docs/development/tests/B09_public_mcp_contract_erratum.md`。

重新完成 B09 的附加硬门禁：

- 黑盒智能体的 EDA、构建、Manifest、部署与观测操作全部通过公开 `zynq_mcp` tools；
- 不导入 `mcps.zynq_mcp.*` 内部模块，不直接启动 Vivado/XSCT/Tcl，不手工执行 `make`，不直接调用内部 Manifest publisher；
- 全部长任务均由 Execution Ledger 提供真实状态、PID/身份、心跳、期限和恢复证据；
- 修复后必须使用**全新无记忆 Agent2 会话**重新验收。

### B10：冻结最小纵向切片 v1

目标：把通过黑盒的最小能力冻结成后续扩展的稳定基线。

交付：版本标签/发布清单、能力矩阵、已知限制、回归入口和下一切片决策。

后续候选顺序：Interrupt → DMA loopback → ILA debug → Boot/非 JTAG 部署。每个候选都重新按 Skill、MCP、Tests 三个目录逐 Brick 追加记录，不回到大而全的文档模式。

完成门禁：用户确认 GPIO v1 可作为稳定基线，并明确选择下一纵向切片。

> **当前门禁**：O7 技术验收已通过，但 B10 不自动冻结。B09 契约勘误已关闭；B10 等待用户确认 GPIO v1 稳定基线并选择下一纵向切片（Interrupt / DMA loopback / ILA debug / Boot），随后归档版本、发布清单、能力矩阵与已知限制（O8）。

**B10 完成记录（2026-08-14）**：用户已确认 GPIO v1 为稳定基线，O8 冻结包已交付。发布清单见 [B10_freeze_manifest.md](development/mcp/B10_freeze_manifest.md)：tag `o7r3-baseline-20260813` → commit `4e0d1482477e9afc3a000837298c0f63dcf60c34`；本轮回归 1331 passed / 1 skipped / 37 deselected（0 failed）、1369 collected；12 项冻结资产 SHA256 已归档（`platform_domain.py`、`.mcp.json` 与 O6/O1–O6 冻结记录一致）；已知限制 6 项已记录。下一切片方向已提出（数据采集切片：PL AD 采集 → DMA → DDR3 → PS 读 DDR3 → UART 上行 → 上位机成像/分析），**正式规划待确认**，本完成记录不视为下一切片已选定。

### B11：泛化框架黑盒验证（Skill/MCP 去 GPIO 化 + 6-LED 考题）

目标：把 GPIO 配方 Skill 泛化为**零 GPIO 字样**的通用工程框架（S0–S8 九阶段），MCP 彻底去 GPIO 化（B05 冻结资产 `platform_generate` 按勘误处置），并以 **6-LED 项目需求（PL 4 + PS 2 一起控制）** 作为黑盒考题，证明 Skill + MCP 是面向任意 Zynq 工程开发的通用框架——具体项目只是一份递给智能体的需求文档。

配套文档：

- 规划（六阶段计划与门禁）：[B11_plan.md](development/mcp/B11_plan.md)
- B05 冻结资产处置勘误草案：[B11_platform_generate_erratum_draft.md](development/mcp/B11_platform_generate_erratum_draft.md)
- 6-LED 黑盒考题需求草案：[B11_blackbox_requirement_draft.md](../tests/B11_blackbox_requirement_draft.md)
- 泛化 Skill 设计基础：[B11_generalized_skill_design.md](../skill/B11_generalized_skill_design.md)
- 数据采集提案已改述为「验证实例候选（非当前立项对象）」：[B11_data_acquisition_proposal.md](development/mcp/B11_data_acquisition_proposal.md)

阶段：① 泛化 Skill 重写（进行中）→ ② MCP 去 GPIO 化 → ③ Agent1 白盒自测 → ④ Agent3 阶段黑盒 → ⑤ 用户硬件确认 → ⑥ Agent2 终验黑盒。

完成门禁：

- 新 Skill 机械扫描 GPIO / LED 等考题外设字样 0 命中；旧 GPIO Skill 按方案 A 归档（`git mv` + SHA256 记录）；
- MCP 工具 101→100，capability 常量修正（关闭 B10 已知限制①）；
- 全量回归不净减（基线：1369 collected / 1331 passed / 1 skipped / 37 deselected）；
- 黑盒终验沿用 B09 硬门禁：零 shell、全公开 MCP、Execution Ledger 全覆盖、Consistency 通过、6-LED 硬件现象由用户确认。

> **立项记录（2026-08-14）**：用户批准 B11 规划并授权进入阶段①。方向要点：GPIO 项目仅作为需求考题，Skill 与 MCP 是面向任意 Zynq 工程的通用框架（用户分工：板卡物理事实归用户，Zynq 工程层归智能体）；数据采集（AD→DMA→DDR3→UART→上位机成像）降级为未来验证实例候选；旧 GPIO Skill 归档方式选定方案 A（`docs/development/skill/archive/zynq_gpio_v1/`）；阶段机推进权默认按规划推荐 (a)=`platform_export_manifest` 承担推进（用户未反对）。

> **阶段①②完成记录（2026-08-14）**：
> 阶段①——新泛化 Skill `skills/zynq_dev/`（11 文件 / 633 行，S0–S8 九阶段），机械扫描 gpio / LED / 0x41200000 / breath / blink **0 命中**；旧 GPIO Skill 按方案 A 归档至 `docs/development/skill/archive/zynq_gpio_v1/`（10 文件 SHA256 记录，SKILL.md 与 B10 冻结值一致）；契约测试 10→11 重映射（+1 零字样门禁回归测试）。
> 阶段②——`platform_generate` 移除（勘误完成记录见 [B11_platform_generate_erratum.md](development/mcp/B11_platform_generate_erratum.md)）；阶段机推进权转 `platform_export_manifest`（决策 a）；`evaluate_observation` marker 必填；工具 101→100、能力常量机械派生（关闭 B10 已知限制①）；回归 **1376 collected / 1337 passed / 1 skipped / 38 deselected / 0 failed**（无净减，`.mcp.json` 哈希不变）。

> **阶段③ 记录（2026-08-14，含整改轮）**：
> 首轮白盒自测 BLOCKED（报告 [B11_phase3_whitebox_report.md](../tests/B11_phase3_whitebox_report.md)）：暴露 P1×4（D1 无地址分配、D2 无端口外部化、D3 无合成、D4 空闲心跳死锁）+ P2×6。
> 整改轮（报告 [B11_remediation_round_report.md](development/mcp/B11_remediation_round_report.md)）：D0–D9 全部 FIXED——心跳回归"索要进程"模型（瞬时失败重试、P5 不再双重计票、ALIVE+STALE revive）；新增 3 原子 `platform_assign_addresses`/`platform_make_external`/`platform_synthesize`（工具 100→103）；回归 **1411 collected / 1371 passed / 1 skipped / 39 deselected / 0 failed**。
> 重跑 PASS（报告 [B11_phase3_rerun_report.md](../tests/B11_phase3_rerun_report.md)）：真板 6-LED 全链路闭环——Platform 20/20 原子、XSA 含 HDF（350KB）、PL timing met、PS ELF、Consistency 12/12 ×2、故障注入双跑（`WROTE:0x2A READ:0x2B`→`LED_E2E_FAIL` 机读 FAIL；16 轮全对→`LED_E2E_PASS` 机读 PASS）、D4 修复实测（130s 空闲后正常准入）。
> 新债：**D10（P1）** `ps_set_compiler_options` 的 defines 不传 `ps_compile`（故障注入改以源码变体等价交付）；**D11（P2）** verify_consistency Manifest 路径须绝对。E1/E2 为系统内存压力瞬时崩溃（非缺陷），E3 部署后再构建需先释放后端——均已按 S8 恢复阶梯处理并记录。

> **阶段③.2 + 终版 + ⑤ 记录（2026-08-14/15）**：
> ③.2 小整改（报告 [B11_phase3_2_fix_report.md](development/mcp/B11_phase3_2_fix_report.md)）：D10 FIXED（真机实测 XSCT 正确 API 为 `app config -add define-compiler-symbols`，defines 真实生效）；D11 FIXED（相对路径+resolve_root 解析、无 resolve_root 显式 INVALID_ARGUMENT）；**PS 输出引脚不亮根因** R1 输出使能位被清/R2 写掩码寄存器/R3 读回写镜像（应读 DATA_RO 真实状态）——Skill §5.1 增加零字样驱动要点；需求更新为"PASS 后持续 1s 交替循环 + 读回必须真实状态寄存器"；回归 1417 collected / 1376 passed / 1 skipped / 40 deselected。
> 阶段③终版（报告 [B11_phase3_final_report.md](../tests/B11_phase3_final_report.md)）：真板 PASS——Platform 15 原子/PL/PS 全绿、FAULT 构建（D10 defines 注入）真板 `LED_E2E_FAIL` 机读 FAIL、正确构建 `LED_E2E_PASS` 后继续捕获 28 行（14 轮）交替、读回走 DATA_RO、Consistency 12/12 ×2。新债 N2（P2，OUTCOME_UNKNOWN+IDLE 通道死锁，已用新 runtime 实例恢复）。
> **阶段⑤ 用户硬件确认（2026-08-15）**：用户实板观察发现收尾清理把 ARM halt、灯冻结在模式 B（N3 事件）→ 公开 MCP 探测证实 halted → `ps_run_target` 恢复 → **用户确认 6 灯恢复 1s 交替（含 PS 2 灯物理点亮）**。Skill `phases/7` 新增「7d 收尾清理」决策点：**目标最终状态由需求确认并留证据（默认保持运行，halt/reset 必须有依据），不设固定动作**。

> **阶段④ 记录（2026-08-15）**：Agent3 阶段黑盒 **PASS**。输入冻结基线见 [B11_phase4_blackbox_basis.md](../tests/B11_phase4_blackbox_basis.md)（Skill/需求/MCP 哈希零漂移）；隔离区 `D:\_b11_p4_external\agent3_20260815\`（项目外，不可读仓库）。全新上下文智能体仅凭「Skill 快照 + 需求文档 + 板卡事实 + 公开 zynq_mcp（103 工具）」独立完成 6-LED 全流程：252 次真实 MCP 调用、223 个终态（199 SUCCEEDED / 24 FAILED 均为早期恢复轮次，按 S8 分类恢复后全绿）、三 Manifest、Consistency 12/12、16 轮读回全对 + `LED_E2E_PASS` 一次 + **PASS 后 31 轮持续交替**（30s 二次捕获）、PS 读回 DATA_RO（与生成 BSP 头文件交叉核对）、收尾保持 RUNNING、无残留进程。硬门禁逐条自查通过。环境观察：黑盒运行在仓库根产生 `vivado_pl/` 生成目录（Vivado 默认行为），已加入 .gitignore。

> **阶段⑥ 记录（2026-08-15/16，含整改轮）**：
> 首轮 Agent2 终验 **BLOCKED**（产品缺口，非智能体失败）：S5 实现期自误（make_external 臆造引脚名 → 悬空端口）+ 服务器端崩溃恢复残留（close 失败 → recover 不清 backend/owner 字段 → `UNOWNED_WORKER_PRESENT` 永久阻断），Agent2 穷尽公开恢复路径后按 Skill 停止、未越权（证据 `D:\_b11_p4_external\agent2_20260815\`）。
> ⑥.1 整改轮（报告 [B11_phase6_1_fix_report.md](development/mcp/B11_phase6_1_fix_report.md)）：recovery 补清全部 owner/instance 残留（含 IDLE 死锁态愈合），门禁本身零改动（活 worker 仍拒）；Skill 新增「引脚/接口名必须真实查询、不得臆造」决策规则；真实进程级复现 Agent2 失败链 + 9 新测试；回归 **1426 collected / 1385 passed / 1 skipped / 40 deselected / 0 failed**。
> **Agent2 重验 PASS（2026-08-16，冻结基线 v2）**：全新无记忆智能体独立完成全流程——313 个 operation（280 SUCCEEDED / 33 FAILED 均有据恢复）、三 Manifest、Consistency 12/12、UART 25 行读回全对 + `LED_E2E_PASS` 一次 + **PASS 后继续 9 行交替**、收尾恢复运行态后新捕获仍交替（`uart_resume_after_cleanup.txt`）、PS 读回 DATA_RO、无残留进程；硬门禁 6 条逐条自查通过；隔离区 `D:\_b11_p4_external\agent2b_20260815\`，33,829 条 MCP 调用全量可解析。
> **B11 完成结论**：泛化框架（零外设字样 Skill S0–S8 + 103 工具 MCP）经「Agent1 白盒 → Agent3 阶段黑盒 → 用户硬件确认 → Agent2 终验黑盒」全链路验证，两个不同全新上下文智能体均仅凭 Skill+需求+板卡事实独立完成真实硬件项目。遗留债：N1（ps_mem_read 解析 gap）、N2（OUTCOME_UNKNOWN+IDLE 通道死锁）均为 P2 已记录；`.mcp.json` 空注册形态待后续决策。

## 6. 当前工作

- B00–B03：✅ COMPLETE / FROZEN。
- B04：✅ 统一 MCP、Execution Ledger、单执行通道和核心生命周期已冻结；PL 能力后续已集成。
- B05：✅ COMPLETE / FROZEN。
- B06：✅ PS/ARM/JTAG/UART 已集成并由真实硬件流程验证。
- B07：✅ GPIO Skill 功能与公开 MCP 契约重验通过；契约勘误已关闭。
- B08：✅ Agent1 R6 白盒硬件 PASS；作为功能证据保留。
- B09：✅ COMPLETE；O7 R3 全新 Agent2 公开 MCP 纯黑盒 PASS，契约勘误已关闭；R1/R2 失败作为历史整改证据保留。
- B10：✅ O8 冻结包 COMPLETE（2026-08-14）；用户已确认 GPIO v1 稳定基线；发布清单见 [B10_freeze_manifest.md](development/mcp/B10_freeze_manifest.md)；下一切片方向已由 B11 承接（方向重定：泛化框架黑盒验证）。
- B11：✅ **COMPLETE（2026-08-16）**：全六阶段闭环——泛化 Skill `skills/zynq_dev/`（零字样）、MCP 103 工具、阶段③真板 PASS、⑤用户确认 6 灯 1s 交替、④ Agent3 黑盒 PASS、⑥ Agent2 终验黑盒 PASS（首轮 BLOCKED→⑥.1 修复→重验 PASS，冻结基线见 [B11_phase4_blackbox_basis.md](../tests/B11_phase4_blackbox_basis.md)）；规划见 [B11_plan.md](development/mcp/B11_plan.md)。
- Execution Observation Contract：✅ [v1.0 COMPLETE / FROZEN](development/mcp/B09_execution_observation_contract.md)。
- 总体完善方案：[O1–O6 COMPLETE / FROZEN；O7 R3 PASS；O8 冻结包已交付（2026-08-14，见 B10 发布清单）](development/mcp/B09_execution_observation_implementation_plan.md)。
- O1冻结证据：[B09_O1_completion_report.md](development/mcp/B09_O1_completion_report.md)。
- O2实施证据：[B09_O2_implementation_report.md](development/mcp/B09_O2_implementation_report.md)。
- O3实施证据：[B09_O3_implementation_report.md](development/mcp/B09_O3_implementation_report.md)。
- O4实施证据：[B09_O4_implementation_report.md](development/mcp/B09_O4_implementation_report.md)。
- O5实施证据：[B09_O5_implementation_report.md](development/mcp/B09_O5_implementation_report.md)。
- O6冻结证据：[B09_O6_completion_report.md](development/mcp/B09_O6_completion_report.md)。

当前机械基线（2026-08-12）：

- `ALL_TOOLS=101`，其中 control=9、domain=92；机械前缀统计 Platform=15、PL=27、PS=48、Verification=2；
- `python -m pytest mcps --collect-only -q`：1322 collected；
- O1最终非硬件回归：1225 passed / 1 skipped / 38 deselected；另行真实入口验证12 passed；其余26项host/device-live未在O1重复执行；
- O2专项+reconcile：37 passed（含1项真实Vivado→XSCT→XSDB串行切换）；O2冻结非硬件回归1259 passed / 1 skipped / 34 deselected；
- O3/O4专项：27 collected；O3真实Vivado与O4真实XSCT各1项host-live PASS；最终非硬件回归1286 passed / 1 skipped / 35 deselected；
- O5专项：10 passed（8 component/contract + 1 host-live + 1 device-live）；SDK-only设备链捕获真实COM4 `GPIO_E2E_PASS`；最终非硬件回归1294 passed / 1 skipped / 37 deselected；
- O6：Skill逃生扫描0；公开MCP r7真实重放PASS（71 calls / 65 timeline / consistency 12/12 / GPIO 8/8）；最终非硬件回归1322 passed / 1 skipped / 37 deselected；
- Capability 常量与实际领域计数存在 1 项漂移，B10 前必须统一。

> **架构修订（v0.3）**：新增三 Agent 分阶段测试工作流架构。Agent1（白盒实现+阶段黑盒项目构建）、
> Agent3（阶段黑盒验收，全新上下文）、Agent2（B09 最终黑盒复现，全新上下文）。
> 每个阶段完成后必须通过 Agent3 阶段黑盒门禁。Agent2 只在 B08 完成后调用。
> 详细架构见 [brick_test_workflow_architecture.md](development/tests/brick_test_workflow_architecture.md)。

## 7. 三 Agent 分阶段测试工作流概要

| # | 阶段 | 开发交付 | Agent1 白盒门禁 | Agent3 阶段黑盒 | 用户硬件验收 | 输入来源 | 进入条件 |
|---|------|---------|----------------|----------------|------------|---------|---------|
| 1 | **B04 R3.1-C** | PL public API (1 tool) | Contract + SDK tests (25) | R3.1-C public MCP smoke（preconditioned；不得称 B09） | 否 | Precondition fixture | R3.1-C freeze confirmed |
| 2 | **B04 R3.2** | Build Pipeline (5 APIs) | worker/stage/evidence tests | PL host-live black-box（需 Vivado Worker） | 否 | B04 R3.1-B + precondition | Agent1 白盒 + Agent3 PASS |
| 3 | **B04 R3.3** | Bitstream + PL Build Manifest (1 API) | manifest schema/revision tests | PL host-live black-box | 否 | R3.2 output | Agent1 白盒 + Agent3 PASS |
| 4 | **B04 R3.4** | JTAG/Hardware (5 APIs) | JTAG lease/recovery tests | JTAG hardware-live black-box（需 hw_server + 板卡） | 是 | R3.3 .bit + Manifest | Agent1 白盒 + Agent3 HW-live + user confirms |
| 5 | **B04 R3.5** | Integration Gate | 全量回归 + list_tools=21 | R3.5_HOST_INTEGRATION（默认）：Agent3 MCP SDK；验证累计生命周期、list_tools=21、控制 API 兼容、B01 签名、stage rejection、artifact 链；不含硬件操作。R3.5_HARDWARE_REPLAY（可选附加）：需用户授权；Agent3 报告软件结果，硬件现象由用户确认 | 否（HOST_INTEGRATION）；是（HARDWARE_REPLAY 需用户确认） | R3.2–R3.4 Agent3-accepted artifacts | HOST_INTEGRATION PASS + HARDWARE_REPLAY (NOT_RUN 或 PASS with user confirm) |
| 6 | **B05** | Platform/AXI/GPIO Domain | BD/XSA/Manifest tests | Platform public workflow（需 Vivado IPI） | 通常否 | Board Profile + B01 spec | Agent1 白盒 + Agent3 PASS |
| 7 | **B06** | PS/ARM/JTAG/UART Domain | build/deploy/recovery tests | PS public workflow（需 hw_server + UART） | 是 | B05 Platform XSA + Manifest | Agent1 白盒 + Agent3 HW-live + user confirms UART |
| 8 | **B07** | 统一 Skill GPIO Workflow | Skill + MCP contract tests | Skill phase black-box | 可需 | B05+B06 artifacts + Skill docs | 功能 PASS；公开 MCP 契约勘误需关闭 |
| 9 | **B08** | Agent1 GPIO 白盒验收 + 故障注入 | T00/T01/T02 + F001–F006 | 不替代 Agent3 | 是 | All prior artifacts | R6 硬件 PASS；修复逃生通道后需公开 MCP 白盒重放 |
| 10 | **B09** | Agent2 独立黑盒复现 | 不适用 | Agent2 final black-box | 是 | Clean workspace only | O7 R3 全新 Agent2 只用 Skill + 公开 MCP，PASS；待用户审核 |
| 11 | **B10** | GPIO v1 冻结 | 全部证据归档 | B09 公开 MCP 契约通过 | 是 | All B09 evidence | 等待用户授权 O8 并确认 GPIO v1 基线 |

完整的矩阵（含 Agent 角色说明、证据等级、跨域交接门禁、Precondition Provisioning、黑盒测试项目结构和执行规则）见 [brick_test_workflow_architecture.md](development/tests/brick_test_workflow_architecture.md)。

### 7.1 R3.1-C 预置前置状态说明

R3.1-C 的 `PL_GENERATE` 阶段无法通过当前已实现的公开 API 到达（Platform API 在 B05 才实现）。因此 R3.1-C 的 Agent3 阶段黑盒使用 **受控前置状态（preconditioned state）**：

- 前置状态由 Manager Reviewer 或验收 harness 创建，不是 Agent3 的工作。
- 详细规则见 [brick_test_workflow_architecture.md §4 Precondition Provisioning](development/tests/brick_test_workflow_architecture.md)。

## 8. 跨域交接门禁（概要）

### 8.1 Platform → PL
- Platform Manifest、XSA、wrapper、board profile、platform revision 一致。
- PL 不得使用隐藏或过期输入。

### 8.2 PL → PS
- Bitstream、PL Build Manifest、Platform XSA、address map、artifact revision 一致。
- PS 必须拒绝 stale ELF、错误 XSA 和地址不一致。

### 8.3 PS → GPIO Run
- ELF、Run Manifest、board profile、JTAG target、UART capture 绑定一致。
- UART 必须产生机器可判定 PASS/FAIL。

完整门禁规则见 [brick_test_workflow_architecture.md](development/tests/brick_test_workflow_architecture.md)
- [B03_to_B04_handoff.md](development/B03_to_B04_handoff.md) — B03→B04 交接

冻结资产：
- [B02_common_contract_plan.md](development/mcp/B02_common_contract_plan.md) (冻结)
- [B02_contract_test_plan.md](development/tests/B02_contract_test_plan.md) (冻结)
- [B02_completion_report.md](development/mcp/B02_completion_report.md)
