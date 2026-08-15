# S0 — 需求解析（Requirement Parsing）

> 输入: 用户一句话需求 + 方向 | 输出: 结构化需求（写入 `<PROJECT_PATH>/<REQUIREMENT_DOC>`）

## 职责

把用户的需求转换成结构化需求文档。本阶段**不产生任何 EDA 副作用**，只做读
上下文与书写需求输入文件（书写属于允许的工作区操作）。

## 结构化需求字段

| 字段 | 占位符 | 说明 |
|------|--------|------|
| 功能目标 | `<REQUIREMENT_FUNCTIONAL>` | 系统做什么（要什么，不写怎么实现） |
| 外设对象 | `<REQUIREMENT_PERIPHERAL>` | 需求文档声明的目标外设（本 Skill 不预设任何外设） |
| 观测方式 | `<REQUIREMENT_OBSERVABLE>` | 可观察输出（如 UART 文本、视觉现象、仿真波形） |
| 板载效果存续性 | `<REQUIREMENT_OBSERVATION_PERSISTENCE>` | 观测结束后板载效果的存续要求（持续可见 / 无要求 / 要求停止）——S7 收尾据此确认目标最终状态（可选，缺省=保持运行） |
| 判定条件 | `<REQUIREMENT_PASS>` / `<REQUIREMENT_FAIL>` | PASS/FAIL 的机读定义（即 `<PASS_MARKER>` / `<FAIL_MARKER>`） |
| 上位机分工 | `<REQUIREMENT_UPPER_COMPUTER>` | 数据流的另一端由谁消费/解析 |
| 时钟/接口/地址约束 | `<REQUIREMENT_CLOCK>` / `<REQUIREMENT_INTERFACE>` / `<REQUIREMENT_ADDRESS>` | 需求给出的约束（可选） |

**占位符规则**：`<REQUIREMENT_*>` 全部来自需求文档，本 Skill 不填值、不猜值。

## 智能体自主决策范围

- 把需求映射到 S0–S8 框架与候选实现路线（不拍板，只在 S4 提案）；
- 识别缺失信息并列出必需项。

## 用户必须提供的物理事实（现实层）

- 需求本身：方向、目标、上位机分工；
- 若涉及外设型号/接口/引脚，属 S1 物理事实，本阶段只登记缺失项。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| 需求不完整 | 列出缺失项，返回 S0 等用户补齐；禁止臆造需求 |
| 需求与外设对象冲突 | 与用户澄清后更新需求文档 |

## 涉及的工具类别

- control query：`get_capabilities`（只读能力声明）、`get_execution_state`（只读上下文）。
- 无任何 command / set 副作用。
