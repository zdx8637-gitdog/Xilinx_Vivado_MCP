# S5 — 分域实现（Domain Implementation）

> 输入: 已批准方案 | 输出: Platform XSA/Manifest → PL bitstream/Manifest → PS ELF/Manifest（全部 MCP 自动发布）

## 职责

按批准方案依次实现三个域。**全部 EDA/构建行为必须经过公开 MCP command 原子**
（执行纪律见 [appendix_mechanics.md](../appendix_mechanics.md)）。每个 command
调用后记录 `operation_id`，用 `wait_operation`/`get_operation_status` 保存真实
Ledger 观测时间线；终态不是 `SUCCEEDED`（Manifest 产物型还需
`artifact_state == "PUBLISHED"`）时立即停止串行链。

## 5.1 Platform（BD 原子序列）

按 [appendix_mechanics.md](../appendix_mechanics.md)「platform 原子序列模板」执行：
创建工程 → PS7 → 配置 → 实例化 IP → 连线 → 时钟/复位 → 地址 → 校验 → wrapper
→ XSA → Manifest。IP 选型、配置参数、连线关系、地址段**全部来自 S3 决策与需求
文档**（占位符），本 Skill 不预设任何具体外设。

产物：`<XSA_PATH>` + wrapper + Platform Manifest（含 `platform_revision` 与
`address_map`，S6 要用）。

## 5.2 PL（构建链）

按附录「PL 构建链」执行：`pl_generate_system_top` → 需求约束文件（写入
`<PROJECT_PATH>` 下，允许的工作区操作）→ `pl_create_project`（sources 含 BD +
wrapper + top；constraints；top 由方案决定）→ `pl_generate_target` →
`pl_synthesize` → `pl_place` → `pl_route` → `pl_analyze_timing`（timing 通过）
→ `pl_generate_bitstream`（产出 `<BITSTREAM_PATH>`）→ PL Manifest 自动发布。

> **约束/综合实现检查纪律（硬性，违反即视为实现不合格）：**
>
> 1. **XDC 注释必须独占行**：行内 `#` 会被 Vivado 误解析为 option 值，触发
>    `Common 17-161 Invalid option value '#' for 'objects'` 并使该端口约束失效
>    （进而 impl 报 `UCIO-1` 未约束端口、write_bitstream 失败——已两次踩坑）。
>    所有 XDC 注释必须以独占一行（`# ...`）书写，**禁止** 在
>    `set_property ... # 注释` 后追加行内注释。
> 2. **综合/实现后必须检查多驱动与未约束端口警告**：`pl_synthesize` /
>    `pl_place` / `pl_route` 成功后，必须核对对 Log 中的
>    `[Synth 8-XXXX] multiple drivers` 类多驱动警告与 `[DRC UCIO-1]` /
>    `[Common 17-XXXX]` 未约束端口警告。RTL 多驱动曾**静默成活板 bug**
>    （不报错、时序通过但行为错误）。任一此类警告都必须先定位到具体
>    端口/信号并确认无数据冲突，才允许继续下一阶段；无法确认时视为失败。

> **对外接口时序仿真验证（强制步骤；修订：仿真在独立会话执行，
> 不得插在 `pl_analyze_timing` 与 `pl_generate_bitstream` 之间）：**
>
> 若设计含对外设接口的时序要求（如外部器件/存储器的控制/数据时序），必须：
> 1. 先编写**数据手册级行为模型**与**自检 testbench**（含接口时序断言——如
>    `<触发信号>/<忙信号>/<片选>/<读脉冲>` 的建立/保持、采样窗口、通道数据
>    对照等）。
> 2. 经公开 MCP `pl_compile_sim → pl_elaborate_sim → pl_run_simulation →
>    pl_parse_sim_log` 完成仿真，**PASS 后才允许上板**。
> 3. **执行位置与顺序（F7 修订，真板实证）**：
>    - 仿真后端与 PL 构建会话**互斥**（`ADAPTER_NOT_READY: Direct EDA
>      backend already active`）——仿真必须**另开独立会话**执行；
>    - 位流生成门禁要求 `pl_generate_bitstream` 的**直接前序**是
>      SUCCEEDED 的 `pl_analyze_timing`（相邻性）——仿真**不得插入**
>      analyze_timing 与 bitstream 之间，否则位流报
>      `STAGE_PREREQUISITE_UNMET` 且该阶段 analyze_timing 不可重跑（死锁）；
>    - 因此时序链保持 `analyze_timing → bitstream` **紧邻**；仿真在独立
>      会话中于综合前（RTL/引擎级）或位流后（接口级复核）完成并留机读证据。
>
> 原因：`pl_analyze_timing`（STA）只验证 FPGA **内部**时序，**不验证对外设接口
> 时序**。接口时序错误（如控制信号建立/保持、通道数据错位）在时序报告中
> 不可见，只能靠接口级仿真暴露；`pl_parse_sim_log` 的 PASS/FAIL 为机读证据。
> 仿真失败/Fail 时必须定位并修复后重跑，不得跳过直接上板。

> **综合告警门禁 + 仿真多驱动/X 检查（强制步骤，位于 `pl_generate_bitstream`
> 之前；缺失即不得上板）：**
>
> 1. **synth CRITICAL WARNING = 0 门禁**：综合日志出现任何 CRITICAL WARNING
>    （多驱动信号、无驱动输出等）一律视为阻塞。它们是综合期专属缺陷，RTL
>    仿真（last-write-wins 语义）会掩盖，真板必现。
> 2. **仿真多驱动/X 传播检查**：xvlog/xelab 全程审视 warning；关键握手/控制
>    信号在仿真中加 X 断言（采样到 X 即 FAIL）。
>
> 原因（真板实证）：引擎计数器双 always 驱动在 XSim 下 SIM_PASS、真板挂死，
> 综合 CRITICAL 告警才暴露。上板前两道门禁缺一不可。

## 5.3 PS（软件链）

按附录「PS 软件链」执行：`ps_import_hardware`（XSA staging 规避同文件冲突）→
`ps_create_platform` → `ps_create_bsp` → `ps_create_app` → **自写程序源码**
（`<PROGRAM_SOURCE>`，按需求文档的判定规范编写）→ `ps_add_sources` →
`ps_compile`（唯一正式编译入口）→ `ps_get_build_status`（取 `<ELF_PATH>`）→
`ps_read_elf_info` 校验 → PS Manifest 自动发布。

**写前查询纪律（自写程序源码前的强制动作）**：`ps_create_bsp` 完成后，写任何
驱动/库相关代码前，先读本工程 BSP 真值源（BSP 目录 `include/` 生成头 +
`libsrc/` 对应驱动的头/实现源码），按附录「15. 写前查询」索引查官方文档，
并对照附录「14. 工程层正确姿势库」逐条自查——禁止凭记忆写 API。

## 5.4 可测性设计（自验证强制规则）

> 测试能力是交付设计的一部分（借鉴行业 DFT/BIST 思想）：以下设施必须随设计
> 一并交付，控制/读取面全部经 PS 可访问寄存器或 POST 自检命令暴露；禁止以
> 「调试临时代码」方式事后移除。

### 5.4.0 适用性原则（测试设施与故障模型对应）

仪器按故障模型安装，**不为满足模板而存在**：

| 设施 | 强制范围 | 豁免 |
|------|----------|------|
| POST | 所有工程（最小实现即可，见 5.4.4） | 无 |
| Test Stimulus + Checker | 需隔离外部世界验证的部件（数据通路、算法模块、控制 FSM 等） | 纯组合无状态部件 |
| Event Counter | 存在外部握手/事务的接口 | 纯电平直驱输出、复位脚、无握手配置接口等无事件可数的接口 |
| Observation Point | 需要逐段定位或运行期可观测性的关键设计边界（数据、控制、状态、时序、域界、IP 集成边界） | 已有等价可观测机制且能提供独立证据的边界 |

**厂商已验证 IP 的边界豁免口径**：厂商 IP 的**内部逻辑**豁免仪器义务（不向
IP 内部插观测点）；但其**边界不豁免**——参数配置、时钟/复位连线、地址段、
以及本设计连到其端口上的**信号位约定**，一律按「IP 手册端口位约定」编码契约
校验（Observation Point 放在本设计拥有的 RTL 边界上，IP 两侧各一个）。原因：
厂商验证的是 IP 独立（OOC）环境，不是本设计的集成用法；集成错误（接线、
位约定、配置）恰恰发生在边界。

### 5.4.1 测试原语库（HOW，按故障模型选用）

| 被测对象类型 | 适用方法（行业术语） | 说明 |
|--------------|----------------------|------|
| 数据通路 / 缓冲 | **TPG + Pattern/Sequence Checker**；缓冲边界/回绕用 Pattern Test + Boundary/Wraparound Test | 图案按附录 10.1 |
| 算法模块 | **Known-Answer Test（KAT）** | 已知输入-期望输出对 |
| 控制 FSM | 确定性 Test Stimulus + 状态/事件 Observation | 状态转移序列观测 |
| 时序发生器（周期/脉宽类输出） | Edge / Period / Pulse-width Measurement | 沿/周期/脉宽计数 |
| AXI / 事务接口 | Transaction / Event Counter | 事务计数与预期比对 |
| 寄存器接口 | Register Readback / Walking Pattern | 写回读 + 行走位 |
| 中断逻辑 | Event Injection + Interrupt Counter | 注入事件、数中断 |
| 存储类（适用时） | Memory Test / March Test | 按存储类型选算法 |

**TPG / Pattern Checker 是测试原语之一，不是框架骨架**：数据型项目用它们，
非数据型项目用上表其余原语。顶层概念始终是 Test Stimulus / Checker /
Counter / Observation Point / POST。

### 5.4.2 Event Counter —— 外部接口的事件计数（测量窗口语义）

- 计数对象 = **可观测的协议事件**：控制器发出的触发/事务数、收到的应答/
  就绪/完成事件数、关键信号上升/下降沿数。具体事件由需求文档与器件协议
  定义（占位符），本 Skill 不预设。
- 计数器只负责**客观计数**（RTL 零业务知识）；预期关系（发出数==收到数、
  每周期恰一次等）由 PS 侧 POST 按配置计算比对（RMON / Performance
  Counter 式「计数监视」）。
- **测量窗口（强制）**：`CLEAR → ARM → RUN → SNAPSHOT → READ`；需要中止时
  `STOP / DISARM`。SNAPSHOT 锁存进影子寄存器，READ 只读影子寄存器；
  跨时钟域或位宽超过 PS 原子读宽时以 SNAPSHOT 为准。提供 `OVERFLOW_STICKY`，
  置位即本轮 FAIL（或加宽位宽重跑）。
- **注意**：`STOP_SOURCE → QUIESCE/DRAIN → SNAPSHOT → CHECK` 属于**端到端
  计数比对**（Observation Point 守恒检查，见 5.4.3），不属于 Event Counter
  窗口本身——事件计数不需要排空系统。

### 5.4.3 Pattern Checker + Observation Point —— 校验与逐段定位

**测试记录是逻辑记录**：测试模式数据流为逻辑记录 `{SEQ, PATTERN, CHECK}`；
**物理编码不得改变被测通路的位宽、握手与流控**。推荐最小扰动：
`TPG = f(seq)`（图案是序号函数），物理上只传 PATTERN；收端按
`expected = f(local_seq)` 本地重构期望值。禁止为测试扩大总线或改写生产
数据格式；确需携带元数据时使用 sideband。正常模式数据原样通过。

**Pattern Checker 结果字段**：

| 字段 | 含义 | 有效性 |
|------|------|--------|
| `total` | 接收总数 | 恒有效 |
| `mismatch_count` | 与期望不符的个数 | 恒有效 |
| `first_bad_index` | 首个不符的本地序号 | 恒有效 |
| `seq_gap_count` / `last_seq` / `first_bad_seq` | 缺口/序号类 | **条件字段**：仅当 SEQ 被显式传输、或 PATTERN 能无歧义反推 SEQ、或 checker 带明确的 resynchronization 算法时有效 |

原因：图案-单传方案下，收端可靠得到「错不错、错在哪」，但**不能严格得知
丢了几个**——丢 1 个与整体滑移不可区分；缺口计数只有序号显式可恢复时才
数学闭合。Observation Point 的测试字段同样遵守此条件。

**Observation Point（边界观测点）**：放在设计关键边界（数据入口/出口、缓冲
两侧、厂商 IP 两侧、PL/PS 域界、软件缓冲前后、上行链路前后；占位符
`[OP0..OPn]`，由 S3 架构决定）。字段分两类：

| 字段组 | 字段 | 生效模式 |
|--------|------|----------|
| 基础字段（所有模式） | `count`、`unit`（计数单位）、`overflow_sticky`；按需 `state` / `status` / `error_flags` / `timestamp` / `latency` / `period` / `occupancy` | 正常 + 测试 |
| 测试字段（仅测试模式） | `mismatch_count`、`first_bad_index`；条件字段 `last_seq` / `seq_gap_count` / `first_bad_seq` | 仅测试 |

正常模式数据不携带序列号，测试字段在正常模式无定义（读为 0）。

**Data Accounting（数据量守恒，逐段比对前提）**：相邻 Observation Point 的
计数**不要求原始值相等**——合法通路本身会改变事务基数（打包、位宽转换、
DMA burst、帧组装、抽取）。规则：

1. 每个 OP 声明自己的计数单位：`UNIT=SAMPLE|BYTE|BEAT|FRAME|TRANSACTION`；
2. 跨变换段按**通路契约的守恒关系**比较（如 `1 SAMPLE = 2 BYTE`；
   `4000 SAMPLE == 1000 BEAT`，由契约换算）；
3. 只有一一映射的段才要求 `count` 相等。

**排空后比对（Drain-before-check，端到端计数比对）**：计数比较前必须
`STOP_SOURCE → QUIESCE/DRAIN → SNAPSHOT`，且相邻 OP 计数差满足其一，
否则不得据计数差判 FAIL：

1. 通路已 quiescent / drained（守恒关系下差为 0）；
2. 差值由可观测 occupancy / in-flight 数量完全解释。

**逐段故障隔离（Segment-by-Segment Fault Isolation）**：故障段 = 相邻
Observation Point 的差值（守恒关系下非零，或 `first_bad_index` 首次出现的
OP 对）。据此定位到具体段，禁止跨段反推。

### 5.4.4 POST —— PS 侧自检命令

- 固件提供 `<SELFTEST_TRIGGER>` 触发命令，执行 L1/L2 自检，输出带构建标识
  的机读判定块（格式见附录 10.4）。
- **构建标识必须绑定 PL + PS（Build Traceability）**：判定块同时携带
  `<SYSTEM_BUILD_ID>`、`<BITSTREAM_ID>`、`<ELF_ID>`；`<SYSTEM_BUILD_ID>` 由
  Platform（XSA）+ bitstream + ELF 的 artifact manifest 共同生成（与 S6
  Manifest 校验思想一致）。原因：新 ELF + 旧 bitstream（或反之）的混搭下，
  SELFTEST PASS 不能归因到「这一套」产物。
- **POST 是最小接口，不强制复杂自检设施**：POST 是统一自检入口与结果接口；
  最简工程的自检 = 寄存器回读等最小检查 + 判定块（`L2=N/A`）即可，不得为
  满足「POST 强制」而过度搭建自检固件。设施规模与故障模型对应（5.4.0）。
- 判定块是 S7 的机读证据，与最终 marker 同等效力。

### 5.4.5 交付门禁

- **验证层级门禁**：所有**适用**层级通过后（L1 PASS、L2 PASS 或 N/A），才
  允许进入 L3 全功能观测；失败时按 S8 分类诊断并用相邻 Observation Point
  定位故障段，不得直接跳到 L3。
- **工作包络硬规则（以 Test Profile 表述）**：Profile 的选择必须覆盖需求
  规定的**工作包络**——存在吞吐率、周期、burst、FIFO、DMA 或缓冲特性的
  通路，L1 不得仅以 Functional 通过，必须至少加 **Throughput**（满带宽接口
  做 line-rate test）；存在背压/溢出机制的必须加 **Stress**；存在回绕结构的
  必须加 **Soak**。**禁止以"低速发送少量测试点成功"作为此类通路自检通过的
  证据。**
- L1/L2 的判定块随 S7 捕获证据一并归档。
- **修复必须配回归（强制）**：任何缺陷修复必须附带回归用例并进入机读门禁
  脚本（修复无回归 = 不得上板）。防止同一缺陷被后续编辑回退而不自知
  （真板实证：断连语义缺陷的修复曾被回退两次，全靠外部亲测才抓回）。

## 智能体自主决策范围

- 各域实现全部细节：IP 配置、RTL、驱动、参数、代码、约束（工程层全归智能体）。

## 用户必须提供的物理事实

- 无（S1–S4 已锁定；涉及板级改动回 S1）。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| 任一域构建失败 | 按 S8 错误分类定位域；修复输入后从对应域重跑公开序列 |
| Manifest 终态门禁失败 | 保留证据并停止；不得手工补 Manifest |
| `TIMED_OUT` / `OUTCOME_UNKNOWN` | `diagnose_execution`；仅按 `recommended_action` 恢复 |

## 涉及的工具类别

- platform command 原子：`platform_create_design`、`platform_add_ps7`、
  `platform_configure_ps7`、`platform_add_ip`、`platform_connect_interface`、
  `platform_connect_clock`、`platform_connect_reset`、`platform_set_address`、
  `platform_validate`、`platform_generate_wrapper`、`platform_export_hardware`、
  `platform_export_manifest`；
- pl command：`pl_generate_system_top`、`pl_create_project`、`pl_generate_target`、
  `pl_synthesize`、`pl_place`、`pl_route`、`pl_analyze_timing`、`pl_generate_bitstream`；
- ps command：`ps_import_hardware`、`ps_create_platform`、`ps_create_bsp`、
  `ps_create_app`、`ps_add_sources`、`ps_compile`、`ps_get_build_status`、
  `ps_read_elf_info`。
