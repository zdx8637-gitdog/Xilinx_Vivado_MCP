# S7 — 部署观测（Deployment & Observation）

> 输入: Bitstream + ELF + UART 观测配置 | 输出: JTAG 部署完成 + 捕获数据
> 前提: S6 一致性校验通过

## 职责

JTAG 部署（8 步标准序列，见 [appendix_mechanics.md](../appendix_mechanics.md)）
+ UART 捕获。**UART capture 必须在 CPU 执行前打开**——先开窗户再放跑。
**8 步部署必须在同一个 MCP session 内完成**（JTAG lease、target 选择与
Controller 持有的调试状态都绑定到该 session）。

## 执行序列

### 7a. 前提检查

| 检查项 | 工具类别 | 不可用时 |
|--------|----------|---------|
| hw_server | `ps_connect_hw_server` | 标记 `BLOCKED: HW_SERVER` |
| hw_server（未运行时） | `ps_start_hw_server`（本地自启，幂等，只启不停） | 自启失败按 ENV_ERROR 诊断（`HW_SERVER_NOT_FOUND` / `HW_SERVER_START_TIMEOUT`）→ 标记 `BLOCKED: HW_SERVER` |
| JTAG 链 | `ps_list_targets` → ARM Cortex-A9 DAP 存在 | 标记 `BLOCKED: NO_ARM_DAP` |
| UART | `ps_list_serial_ports`（按 `port` 字段判断，勿用字符串包含判断） | 标记 `BLOCKED: NO_UART` |

> **双端字节级 KAT 对拍（联调前强制）**：协议/帧格式类接口在联调前，双方
> 实现必须用**同一组字节级 KAT 向量**对拍（同一输入字节串 → 双方各自计算/
> 校验，比对一致才算通过），不得只各测各的。文本契约的字段级歧义（覆盖
> 范围、字节序、置零参与等）只有字节级对拍能暴露——真板实证：校验和覆盖
> 口径歧义导致联调全帧失败。KAT 向量随证据归档。

### 7b. 部署（8 步，缺一不可）

`select → halt → rst -system → ps7_init → fpga -f → loadhw → dow → con`

| 步骤 | 工具类别 | 目的 |
|------|----------|------|
| 1 | `ps_select_target` | 选择 ARM 核 |
| 2 | `ps_halt_target` | CPU 暂停（幂等） |
| 3 | `ps_reset_target`（scope=system） | 复位系统（非处理器） |
| 4 | `ps_initialize_ps` | 初始化系统时钟 + DDR + PS-PL 接口（ps7_init.tcl 来自工程产物） |
| 5 | `pl_program_fpga` | 烧录 `<BITSTREAM_PATH>`（**必须在 ps7_init 之后**） |
| 6 | `ps_load_hardware` | 注册 PL 内存映射（loadhw `<XSA_PATH>`） |
| 7 | `ps_download_elf` | 下载 `<ELF_PATH>` 到内存 |
| 8 | `ps_run_target` | 启动 CPU 执行 |

**硬性顺序知识**：`fpga -f` 必须在 `ps7_init` 之后执行——`rst -system` 会把
PS-PL 接口置于阻塞状态，先烧录 FPGA 会导致后续对 PL 地址空间的访问进入 abort
handler。`loadhw` 必须在访问 PL 外设前执行——缺它，ARM 程序对 PL 地址空间的
读写会访问无效地址。

> **ps_load_hardware 已知限制（B13-F8 修复轮#8，真 Vivado 实测）**：
> Vivado 2023.1 的 `write_hw_platform`/`write_hwdef` **不输出 ADDRESSING 段**
> （BD 内存中 segments 存在亦然），hsi 据此合成的调试映射**非确定**——dow
> 可能间歇报 `Blocked address ... Reserved address range`（同一 XSA 两次结果
> 不同）。处置：`ps_load_hardware` 返回 `addressing_section=MISSING` 属预期
> 而非故障；若 dow 出现该错误 → **跳过 ps_load_hardware**（DAP 默认身份映射
> 对 ELF 运行足够）；需读 PL 寄存器时手动 xsdb `loadhw`。调试读内存用
> `ps_mem_read`（返回 `0x%08X` 规范化字；空结果 fail-closed 报
> MEM_READ_NO_DATA，提示先 loadhw/与 xsdb 对账）。

### 7c. UART 捕获

start（CPU 执行前）→ wait（markers 全部来自需求文档，`<PASS_MARKER>` /
`<FAIL_MARKER>`）→ stop（取完整文本）。marker 纪律与 `\x00` 清理见
[appendix_mechanics.md](../appendix_mechanics.md)「UART 捕获」。

### 7e. 部署后自检阶梯（L1 → L2 → L3，门禁式）

部署完成、CPU 运行后，先走自检阶梯，再进入 7c 的 marker 判定。

**验证层级（WHAT）**：

| 级 | 名称 | 验证命题 | 手段（按 5.4.1 原语库选） | 证据 |
|----|------|----------|------------------------------|------|
| L1 | Internal Verification（内部实现验证） | 在尽可能隔离外部世界的条件下，本设计自己控制的逻辑与内部连接是否正确 | Test Stimulus + Checker / KAT / Readback / 状态事件观测 / 时序测量（数据通路实例 = TPG + Pattern Checker） | POST 判定块 |
| L2 | Integration Verification（集成/接口验证） | 本设计与外部依赖（器件/PHY/其他 FPGA/MCU/DDR/PS↔PL 域界）之间的集成是否正确 | Event Counter 测量窗口比对（预期由配置计算；数据内容不参与判定） | POST 判定块（含计数表） |
| L3 | System Functional Verification（系统功能验证） | 最终需求行为 | 需求 marker 判定（7c 现有机制不变） | 捕获文本 + evaluate_observation |

**L2 允许 N/A**：工程不存在外部握手/事务接口时 `L2=N/A`，不得为满足门禁
硬造 L2 测试。判定支持 `PASS / FAIL / N/A`（见附录 10.4）。

**测试强度（HOW HARD，Test Profile）**——按 DUT 特性与工作包络从下列 profile
选取，不要求每类项目全做：

| Profile | 行业名 | 内容 | 典型适用 |
|---------|--------|------|----------|
| Functional | Functional Test | 低速确定性激励，验证连接/逻辑/位约定 | 所有项目 |
| Boundary | Boundary / Corner-case Test | 边界值/峰值条件 | 计数边界、极值配置 |
| Throughput | Throughput Test（满带宽 = line-rate test） | 目标数据率连续运行完成校验 | 有吞吐特性的通路 |
| Stress | Stress Test | ≥峰值速率 + burst / backpressure / overflow | 有流控/背压/溢出机制者 |
| Soak | Soak / Endurance Test | 持续至缓冲、序号、地址多次回绕 | 有回绕结构、长时可靠性要求者 |

> 注：Throughput/Soak 能**暴露** CDC 实现缺陷与速率相关偶发错误，但**不能
> 证明不存在亚稳态**；CDC 正确性的判定依据是 CDC 架构审查 + 同步器结构 +
> STA/CDC 分析，不归入本阶梯。

**工作包络硬规则**：Profile 选择必须覆盖需求规定的工作包络（映射关系见
5.4.5）；**禁止以"低速发送少量测试点成功"作为含吞吐特性通路的自检证据**。

**测量与证据链（按适用性选取，不是所有项目都需要全部环节）**：
`Observe/Snapshot → Check/Accounting → End-to-End Verification →
Fault Isolation → Evidence-Based Modification Scope`；
**涉及在途数据的计数比对时**使用：
`Quiesce → Drain → Snapshot → Data Accounting`（配方见附录 10.3）。

原则：**「上游正确」与「下游正确」是两个独立命题**，各自必须有自己的证据；
L3 现象异常时按 L1→L2 证据定位，禁止用 L3 现象反推 L1/L2 结论。

- L1/L2（适用者）通过是进入 L3 的前置条件；失败时按「失败恢复入口」+ S8
  分类诊断，并用相邻 Observation Point 差值定位故障段。
- L1 不必等到上板才做：PL 侧模拟（附录 4 仿真链）先行；上板 L1 是最终门禁。
- L1/L2 判定块文本随 7c 捕获一并归档至 `<PROJECT_PATH>/evidence/`。

### 7d. 收尾清理（决策点：目标最终状态必须确认，不设固定动作）

观测与判定完成后的收尾顺序：`ps_stop_uart_capture` → `ps_disconnect_hw_server`
→ `close_session`。**目标最终状态（保持运行 / 暂停 / 复位）不是固定动作，
而是本阶段必须向需求确认的交付项**：

1. **先确认需求语义**：需求对"观测结束后板载效果"的要求是什么——
   持续可见（循环演示类）/ 无要求 / 明确要求停止。S0 需求解析时就应把
   "板载效果存续性"列为观测语义之一（`<REQUIREMENT_OBSERVATION_PERSISTENCE>`）。
2. **默认保持运行态**：需求未明确要求停止时，默认让目标保持运行——
   判定 PASS 后板载现象继续可见，用户可随时观察（断电/复位才会消失）。
   这是 fail-safe 朝可观测性的默认。
3. **任何 halt / reset 必须有依据**：若需求明确要求停止（或存在安全/资源
   原因必须停止），执行并记录动作、依据与最终状态；无依据不得 halt。
4. **记录最终目标状态**：在交付证据中写明收尾后的目标状态（running/halted）
   与理由——"最终状态是什么、为什么"是判定链的一部分，不是顺便动作。

经验教训：曾因收尾时无依据地停住目标，导致板载效果冻结在最后写入状态，
用户误判为功能失败；恢复方式为重新恢复执行（固件仍在内存中，无需重烧）。
教训不是"永远保持运行"，而是**"最终状态必须由需求确认并留证据"**。

## 智能体自主决策范围

- 部署序列执行与观测配置（波特率、捕获窗口、marker/帧判定规则——marker 值
  必须来自需求文档，不臆造）。

## 用户必须提供的物理事实

- 无（UART 端口/波特率来自 S1 物理事实与需求文档）。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| JTAG 链只有 DAP 而无 ARM 核 | `ps_ensure_arm_accessible`（rst -system 恢复） |
| DAP 不响应 | 保存公开状态；按 `recommended_action` 调 `ps_recover_target` 或 `recover_execution` |
| download 失败 | 检查 ELF 编译、`ps_initialize_ps` 与 `ps_load_hardware` 是否完成 |
| UART 无输出 | 波特率偏差诊断（`ps_diagnose_uart_clock`）+ 确认 capture 先于 run + 确认程序未 crash |

## 涉及的工具类别

- ps JTAG command：`ps_connect_hw_server`、`ps_start_hw_server`、`ps_list_targets`、`ps_select_target`、
  `ps_halt_target`、`ps_reset_target`、`ps_initialize_ps`、`ps_load_hardware`、
  `ps_download_elf`、`ps_run_target`、`ps_ensure_arm_accessible`、
  `ps_list_serial_ports`、`ps_diagnose_uart_clock`；
- pl command：`pl_program_fpga`；
- UART capture command：`ps_start_uart_capture`、`ps_wait_uart_capture`、
  `ps_stop_uart_capture`。
