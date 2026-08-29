# zynq_dev Skill 测试章节修订稿 v5（定稿·已合并）

> 状态：**定稿，已合并至 `skills/zynq_dev/`（2026-08-29）**。本文档为修订评审
> 记录，供追溯；实际生效内容以 Skill 文件为准。
> v5 收口（定稿轮 4 个小修点，已随合并落地）：① 顶部「测量与判定链」泛化——
> 按适用性选取，Drain/Data Accounting 只属于涉及在途数据的计数比对；
> ② Observation Point 强制范围泛化（数据/控制/状态/时序/域界/IP 集成边界）；
> ③ POST 明确「最小实现」口径；④ 命名原则正式写入 SKILL.md。
> 合并文件：`SKILL.md`、`phases/5_domain_implementation.md`、
> `phases/7_deployment_observation.md`、`phases/8_verdict_recovery.md`、
> `appendix_mechanics.md`。

---

## 0. 框架总览（正交三维）

```
验证层级（WHAT）
  L1 Internal Verification          设计自己拥有的逻辑/连接，隔离外部世界
  L2 Integration Verification       与外部依赖/器件/IP/域之间的集成（可 N/A）
  L3 System Functional Verification 最终需求行为（marker 判定，现有机制）

验证方法（HOW，按故障模型从原语库选）
  Test Stimulus / Checker / Counter / Observation Point / POST
  Known-Answer Test / Readback / Loopback / Timing Measurement

测试强度（HOW HARD，按工作包络选 Profile）
  Functional / Boundary-Corner / Throughput / Stress / Soak

测量与判定链
  Quiesce → Drain → Snapshot → Data Accounting
  → End-to-End Verification → Segment-by-Segment Fault Isolation
  → Evidence-Based Fault Isolation（限定修改范围）
```

三个维度正交：一个项目 = 选若干（层级, 方法, Profile）组合；测试设施按故障模型
安装，不为模板而存在。

---

## 1. 术语与行业依据（命名基准）

| 本框架术语 | 行业对应 | 一句话定位 |
|------------|----------|-----------|
| **可测性设计（DFT 思想）** | Design for Testability / Built-In Self-Test | 测试能力是设计交付物的一部分 |
| **Test Stimulus（测试激励）** | stimulus / pattern generation | 隔离外部世界后驱动被测对象的确定性激励 |
| **TPG（Test Pattern Generator）** | PRBS / pattern generator | Test Stimulus 的数据通路实例（入口旁路图案源） |
| **Checker（校验器）** | PRBS checker / KAT | 汇端/响应端对期望行为的判定 |
| **Event Counter（事件计数器）** | RMON 计数器 / Performance Counter | 只计数协议事件，不判数据内容 |
| **Observation Point（观测点，OP）** | Instrumentation Point / Observability | 设计关键边界的轻量可观测点（计数/状态/错误/时间），逐段隔离 |
| **Known-Answer Test（KAT，已知答案测试）** | KAT（密码学/算法验证标准做法） | 算法模块用已知输入-期望输出对验证 |
| **POST（自检命令）** | power-on self-test | PS 侧 SELFTEST：机读判定块 |
| **End-to-End Verification（端到端验证）** | end-to-end test | 验证层级伞形词；**Data Integrity Verification 是其数据通路子型** |
| **Segment-by-Segment Fault Isolation** | fault isolation | 相邻 OP 差值定位故障段 |
| **Evidence-Based Fault Isolation** | root-cause analysis 原则 | 证据限定修改范围（S8） |
| **Data Accounting（数据量守恒）** | transaction accounting / conservation check | 跨变换段的计数按单位+守恒关系比较 |

**命名原则（写进 Skill 写作纪律）**：优先业界术语；只有不存在清晰行业术语时才
定义项目内术语；禁止为已有方法自造缩写。

> **强度声明**：上表是「思想来源」，不是「标准符合性声明」。本框架实现的是
> FPGA/SoC 运行期可观测性 + 自检，**不**等价于 ASIC 制造测试意义的 DFT，
> **不**实现 IEEE 1687 仪器网络，**不**包含扫描链/MBIST 等硅级设施。

---

## 2. 修订 1：SKILL.md（两处概览行 + 新增「可测性纪律」小节）

### 2.1 S5 概览行

原文：

> | S5 | 分域实现 | Platform BD/XSA/Manifest → PL 构建/bitstream/Manifest → PS 软件/ELF/Manifest |

改为：

> | S5 | 分域实现 | Platform BD/XSA/Manifest → PL 构建/bitstream/Manifest → PS 软件/ELF/Manifest；**PL/PS 实现须按「测试设施与故障模型对应」内嵌自验证设施（Test Stimulus / Checker / Counter / Observation Point / POST，见 phases/5.4）** |

### 2.2 S7 概览行

原文：

> | S7 | 部署观测 | JTAG 8 步部署 + UART 捕获（marker 来自需求文档） |

改为：

> | S7 | 部署观测 | JTAG 8 步部署 + **自检阶梯 L1→L2（见 phases/7e）** + UART 捕获（marker 来自需求文档） |

### 2.3 新增小节（放在「证据纪律」之后）

```markdown
## 可测性纪律（自验证硬规则）

测试能力是交付设计的一部分（借鉴行业 DFT/BIST 思想）。测试设施按故障模型
对应安装，不为模板而存在。通用设施五类：

1. **POST**——所有工程强制：PS 侧自检命令，输出带构建标识的机读判定块；
2. **Test Stimulus**——凡需隔离外部世界验证的部件强制（数据通路实例 = TPG）；
3. **Checker**——凡 Test Stimulus 存在处必有对应判定（数据通路实例 =
   Pattern/Sequence Checker；算法模块实例 = Known-Answer Test）；
4. **Counter / Observation Point**——凡关键边界（外部握手、数据通路、域界）
   按需放置；计数类用 Event Counter，综合类用 Observation Point；
5. **验证层级门禁**——L1（Internal）→ L2（Integration，可 N/A）→ L3
   （System），见 phases/7e。

强制规则、门禁与配方见 [phases/5_domain_implementation.md](phases/5_domain_implementation.md)
「5.4」、[phases/7_deployment_observation.md](phases/7_deployment_observation.md)
「7e」、[phases/8_verdict_recovery.md](phases/8_verdict_recovery.md)
「故障归因约束」、[appendix_mechanics.md](appendix_mechanics.md)
「10. 自验证配方」。思想来源见 appendix_mechanics.md「11. 行业依据」。
```

---

## 3. 修订 2：phases/5_domain_implementation.md — 新增「5.4 可测性设计（自验证强制规则）」

放在现有「5.3 PS（软件链）」之后、「智能体自主决策范围」之前。全文如下：

```markdown
## 5.4 可测性设计（自验证强制规则）

> 测试能力是交付设计的一部分（借鉴行业 DFT/BIST 思想）：以下设施必须随设计
> 一并交付，控制/读取面全部经 PS 可访问寄存器或 POST 自检命令暴露；禁止以
> 「调试临时代码」方式事后移除。

### 5.4.0 适用性原则（测试设施与故障模型对应）

仪器按故障模型安装，**不为满足模板而存在**：

| 设施 | 强制范围 | 豁免 |
|------|----------|------|
| POST | 所有工程 | 无 |
| Test Stimulus + Checker | 需隔离外部世界验证的部件（数据通路、算法模块、控制 FSM 等） | 纯组合无状态部件 |
| Event Counter | 存在外部握手/事务的接口 | 纯 GPIO 输出、复位脚、无握手配置接口等无事件可数的接口 |
| Observation Point | 关键边界（数据通路边界、域界、IP 边界、软件缓冲前后） | 纯控制/状态通路（可用寄存器回读替代） |

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
```

---

## 4. 修订 3：phases/7_deployment_observation.md — 新增「7e 部署后自检阶梯」

放在「7d. 收尾清理」之前。全文如下：

```markdown
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

**测量与判定链（所有涉及计数比对的层级通用）**：
`Quiesce → Drain → Snapshot → Data Accounting → End-to-End Verification
→ Segment-by-Segment Fault Isolation`（配方见附录 10.3）。

原则：**「上游正确」与「下游正确」是两个独立命题**，各自必须有自己的证据；
L3 现象异常时按 L1→L2 证据定位，禁止用 L3 现象反推 L1/L2 结论。

- L1/L2（适用者）通过是进入 L3 的前置条件；失败时按「失败恢复入口」+ S8
  分类诊断，并用相邻 Observation Point 差值定位故障段。
- L1 不必等到上板才做：PL 侧模拟（附录 4 仿真链）先行；上板 L1 是最终门禁。
- L1/L2 判定块文本随 7c 捕获一并归档至 `<PROJECT_PATH>/evidence/`。
```

---

## 5. 修订 4：appendix_mechanics.md — 新增两节

### 5.1 新增「10. 自验证配方」

放在「9. Session / JTAG 清理」之后：

```markdown
## 10. 自验证配方（Test Stimulus / Event Counter / Pattern Checker / POST）

### 10.1 TPG 图案配方（数据通路 Test Stimulus）

| 模式 | 内容 | 能定位的错误 |
|------|------|-------------|
| 常数轮转 | N 个互异常量按位置轮转（`模式值 = <常量表>[位置 mod N]`） | 位置错位、整段重复/缺失 |
| 地址/序号标记 | `模式值 = 自身位置标识` | 地址错位、跨段串扰、读错位置 |
| 全 0 / 全 1 | 固定常值 | stuck-at、固定位错误 |
| walking-1 / walking-0 | 单比特行走（`0001→0010→0100→1000→…` 及反相） | 位道错接、位交换、宽度不匹配 |
| PRBS | LFSR + 种子，收端同种子重生成比对 | 随机位错误、间歇错误 |

选择指引：先常数轮转定位「段级」，再地址/序号标记定位「位置级」；位级用
全 0/全 1 与 walking 系列；随机性错误才上 PRBS。**禁止只用全 0 / 全 1**
（对位交换与位置错位无鉴别力）。常量互异且与真实数据可区分。

### 10.2 Event Counter 测量窗口

```
寄存器组： <事件>_CNT + <事件>_SNAP（影子锁存）+ OVERFLOW_STICKY
流程：     CLEAR → ARM → RUN → SNAPSHOT → READ
中止：     STOP / DISARM（可选）
```

- READ 只读影子寄存器；OVERFLOW_STICKY 置位即本轮 FAIL（或加宽位宽重跑）；
- 跨时钟域计数一律以 SNAPSHOT 为准；
- **端到端计数比对**（含排空）不属于本窗口，见 10.3。

预期关系模板（由 PS 侧 POST 按配置计算，RTL 不写死）：

| 关系类型 | 示例形态（占位符） | 判定 |
|----------|-------------------|------|
| 1:1 型 | `<发出事件>_CNT == <收到事件>_CNT` | 相等 |
| 周期型 | 每 `<周期>` 恰 `<K>` 次 | 整除余数校验 |
| 沿型 | 每次 `<发出事件>` 恰 1 个 `<事件沿>` | 逐事件核对 |

### 10.3 Pattern Checker + Observation Point 配方

```
测试模式流量： 逻辑记录 { SEQ, PATTERN, CHECK }
              物理编码不得改变被测通路位宽/握手/流控
最小扰动推荐： TPG = f(seq)（只传 PATTERN）；收端 expected = f(local_seq)
可选：         sideband 通道携带元数据（不改 payload）
正常模式流量： <REAL_DATA> 原样通过
```

```
Pattern Checker 结果：
  恒有效： { total, mismatch_count, first_bad_index }
  条件字段： { last_seq, seq_gap_count, first_bad_seq }
            仅当 SEQ 显式传输 / PATTERN 可无歧义反推 SEQ /
            checker 带明确 resynchronization 时有效
```

```
Observation Point：
  基础字段： { count, unit, overflow_sticky }
             + 按需 { state, status, error_flags, timestamp,
                      latency, period, occupancy }
  测试字段： { mismatch_count, first_bad_index }
             + 条件字段 { last_seq, seq_gap_count, first_bad_seq }
```

```
Data Accounting（守恒比对）： 相邻 OP 按 unit + 通路契约的守恒关系比较
                              （如 1 SAMPLE = 2 BYTE）；仅 1:1 段要求 count 相等

排空后比对（Drain-before-check）： STOP_SOURCE → QUIESCE/DRAIN → SNAPSHOT
  相邻 OP 计数差必须满足其一，否则不得判 FAIL：
    1. 通路已 quiescent / drained（守恒关系下差为 0）；
    2. 差值由可观测 occupancy / in-flight 完全解释。

逐段隔离： 故障段 = 守恒关系下相邻 OP 差值非零（或 first_bad_index 首次
           出现的 OP 对）
```

- Pattern Checker 判定：`mismatch_count == 0`；校验算法任选（checksum/CRC）；
- `<SEQ>` 位宽满足通路最大在途数据量（防回绕歧义）；允许有界回绕并在判定
  时校验。

### 10.4 POST 判定块格式（机读，KV 文本，支持 N/A）

```
SELFTEST BEGIN RUN=<N> BUILD=<SYSTEM_BUILD_ID> PL=<BITSTREAM_ID> PS=<ELF_ID>
SELFTEST L1 RUN=<N> STEP=<步骤名> RESULT=PASS|FAIL|N/A <数字字段>
SELFTEST L2 RUN=<N> STEP=<步骤名> RESULT=PASS|FAIL|N/A <数字字段>
SELFTEST DONE RUN=<N> L1=PASS|FAIL|N/A L2=PASS|FAIL|N/A
```

- `<RUN>` 每次自检递增；`<SYSTEM_BUILD_ID>` 由 Platform（XSA）+ bitstream +
  ELF 的 artifact manifest 共同生成；`<BITSTREAM_ID>`/`<ELF_ID>` 为该次构建
  产物标识（Manifest revision / SHA256 短码）；
- `N/A` 仅用于「工程不存在该层级适用对象」（如无外部握手接口的 L2），
  不得以 N/A 掩盖适用但未执行的测试；
- `<数字字段>` = 该步判定数字（`MISMATCH=0 TOTAL=<N>`、`EVENT=<名>
  EXPECT=<M> GOT=<N>`、`OP0_COUNT=<N> OP1_COUNT=<N> UNIT=<单位>`）；
- 判定以 `SELFTEST DONE` 行为准；单步 FAIL 必须体现在汇总行；
- 保持 KV 文本格式（不引入 JSON），便于 UART 逐行解析。

### 10.5 自检证据归档

POST 判定块文本随 7c 捕获一并保存；`evaluate_observation` 的 PASS 判据
（需求 marker）与 L1/L2 判定块互为独立证据，缺一不可。
```

### 5.2 新增「11. 行业依据」

```markdown
## 11. 行业依据（测试理念出处）

本框架测试纪律是以下思想的**借鉴与工程化合并**，不是任何标准的符合性实现：

| 理念 | 行业出处 |
|------|----------|
| 测试能力是设计交付物 | DFT / BIST 思想：[DFT & BIST 课程](https://smtnet.com/training/index.cfm?fuseaction=view_event&event_id=461&company_id=50816) |
| 内部确定性测试源 + 汇端校验 | PRBS 图案生成/检查：[Xilinx 7 系列 GT 收发器手册](https://manualzz.com/doc/o/kus2n/xilinx-7-series-user-manual-83-h0_0011_07fe#14)；[LiteX Memory Testing and BIST](https://deepwiki.com/enjoy-digital/litex/7.2-uart-and-serial-communication) |
| 事件计数监视（不看数据内容） | [AMBA AXI Performance Monitor IP](https://semiiphub.com/ip/datasheet/amba-axi-performance-monitor-7051)；[RMON 计数器](https://manual.yamaha.com/network/switches/swx2310p/td/en/Rev.2.02.31/oam_oam_rmon.html) |
| 内嵌仪器标准化（借鉴思想） | [IEEE 1687-2014（IJTAG）](https://standards.ieee.org/ieee/1687/10896/)；[系统级 DFT 指南](https://www.jtag.com/system-dft-guidelines-boundary-scan-at-system-level/) |
| 片上自检工程范例 | [OpenTitan DV 方法论](https://opensecura.googlesource.com/3p/lowrisc/opentitan/+show/9cae6d97d933f648fc7545dec65c7a25dc1f1a03/doc/ug/dv_methodology/index.md) |
| 测试分层与上电自检 | [嵌入式测试指南](https://theembeddedkit.io/wp-content/uploads/2024/11/Embedded-testing-essential-guide-by-The-Embedded-Kit.pdf)；[FPGA 板级 bring-up 实例](https://github.com/heisaman/PLFM_RADAR/blob/main/docs/bring-up.html) |
```

---

## 6. 修订 5：phases/8_verdict_recovery.md — 「Evidence-Based Fault Isolation」（正式纳入）

在 S8 诊断部分增加：

```markdown
## 故障归因约束（Evidence-Based Fault Isolation）

已由下层独立证据 PASS 的域，在没有新的反证前**不得作为首要修改对象**；
修改范围以证据定位到的故障段（相邻 Observation Point 差值 / Event Counter
比对结果）为边界。

原因：测试工具产生的是证据；证据不仅用于 PASS/FAIL 判定，也用于**约束修改
范围**。最终现象异常时最常见的失效模式是「到处改」——把原本正确的下层实现
也改坏。证据的作用就是限定「允许修改哪一层代码」。
```

---

## 7. 定稿轮待办（合并时已全部关闭）

- ✅ 既有 `phases/5_domain_implementation.md` 接口时序仿真步骤的示例词已替换为
  占位符式表述（`<触发信号>/<忙信号>/<片选>/<读脉冲>`；"外部器件/存储器"）。
- ✅ 「厂商已验证 IP 边界豁免口径」（5.4.0）已与各域实现章节现有接口纪律核对，
  无冲突表述（现有纪律为多驱动/未约束端口检查与接口时序仿真，均不涉及 IP
  内部仪器要求）。
- ✅ 「命名原则」已正式写入 SKILL.md（「可测性纪律」节内「术语与命名原则」）。
- ✅ 定稿轮 4 个小修点（测量链泛化 / OP 范围泛化 / 最小 POST / 命名原则）已落地。
