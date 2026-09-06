# 验证方法论 v2：L0–L3 + 行为偏离审计 + 黑盒终审（B13-P4 黑白盒复盘沉淀）

- 日期：2026-09-05（v2：吸收三方评审意见——行为偏离审计、L2-A/B 拆分、专家帮助率、能力簇覆盖、结构化绕行理由、L0 契约层）
- 一句话凝练：**L0 测契约，L1 测功能，L2 测状态，L3 测公共面，偏离审计测专家补偿，黑盒测未知世界。**
- 适用：此后所有 Brick 的白盒/黑盒任务书设计与验收审核。

---

## 零、核心认知（实证基础）

1. **专家补偿效应**：白盒（连续体）的先验知识会把 MCP 接口缺陷"吸收"掉——工具坏了，测试却绿了。绿的是"专家系统可用性"，红的是"产品可用性"。
2. **功能覆盖 ≠ 状态转换覆盖**：四个 P1 全是"A→B 生命周期转移"的裂缝。FPGA+Vivado+Vitis+JTAG+PS+PL 是高度有状态系统，缺陷藏在转移上。
3. **名义能力 vs 实际可用能力漂移**：工具描述宣称"可用"，实际"某些状态/迭代下不可用"。白盒绕开时不觉异常，文档面与专家实践面就此漂移。
4. **本项目最大的测试风险已不是"FPGA 功能有没有 bug"，而是"Agent 是否在用自己的知识替 MCP 擦屁股"**。黑白盒差异量化了漂移量——黑盒按说明书走，每处漂移就是一场战斗。

## 一、测试层级（L0 → 黑盒）

### L0 契约一致性（新增，先于一切）

回答："MCP 声称自己能做什么，和它实际能做什么是否一致？"逐项核对：工具存在、参数 schema 正确、**描述与真实行为一致**、前置条件明确、状态限制正确、错误码稳定、返回值语义正确。既有机械门禁（工具计数/schema 校验/Skill 契约测试）是 L0 的静态部分；L0 的动态部分 = **每个能力簇抽 1–2 个代表性工具做一次真实行为对拍**（如 `ps_mem_read` 读已知 DDR 与 xsdb 对账）。本轮四条 P1 若先过 L0 动态对拍，本可在 L1 之前暴露。

### L1 功能正确性

单个工具本身：参数校验、原子语义、fail-closed、回读校验。现有 1500+ 回归 + host_live 属此层。L1 通过只证明"工具对"，不证明"串起来对"。

### L2 生命周期正确性（拆 A/B 两半）

**L2-A 规范生命周期**（必测基线，全走公开 MCP API）：

```
Create → Build → Modify → Rebuild → Export → Reload → Program → Debug → Recover
(平台)  (PL/PS)  (改RTL/IP/BD/源)  (原地重建)  (XSA)  (导入PS)  (JTAG)  (读/查)  (异常恢复)
```

强制转移清单：打包 IP → 实例化 → **改 IP 内容 → 原地重建**（不打散工程）；XSA 重导出字节一致 + 重复导入幂等；部署**必须走 `ps_load_hardware`** 并验证 dow 映射；内存观测**先 `ps_mem_read` 与 xsdb 对账**；FAILED 后同签名重试；中断/超时后 recover；BD 改 → validate → 回退 → 再前进。

**L2-B 扰动生命周期**（每 Brick 有界清单，防"生命周期本身变成新标准答案"）：

```
Modify→Modify        Build→Build           Export→Reload(未改即重导)
Reload→Reload        Build→改IP→不清理→Build   Program→重新Program
失败→Retry           失败→Recover          Recover→Retry
Debug→断JTAG→重连→Debug    STOP→立即再START→STOP
```

### L3 公共面 / 伪黑盒

执行约束：只用 MCP 公开工具 + Skill 书面指引；禁止直接 Vivado Tcl / xsdb 脚本 / 历史配方替代（诊断回退须声明入审计）；**按能力簇覆盖，不按工具机械覆盖**（见 §三）。

**L3 边界**：伪黑盒不能替代真黑盒——先验在认知层，换工具只能提前暴露 ~80%，终审仍是真黑盒。

### 黑盒终审

全新无记忆、仅公开契约 + 板卡包 + Skill + MCP 自描述；**按门禁切片早跑**；LESSONS_LEARNED（现象→根因→对策）必须回流 Skill 或工具描述。
- **回流泛化审查（强制）**：经验回流 Skill/MCP 前先过"泛化滤网"——只收**任何同类项目都适用**的通用件（工具行为、平台限制、通用设计模式）；**项目特化件**（某项目的上位机口径、版本串约定、具体 BD 细节）留在该项目文档/工作区，禁止进泛化框架。"上位机/对端分工"作为需求字段是通用概念（保留），"修这个项目的上位机"是特化工作（归项目）。

## 二、行为偏离审计（Deviation Audit，替代单一"绕行审计"）

顶层概念 = **偏离**：Agent 的实际行为与"公共契约指引的路径"之间的任何偏差。六型 + 裁决优先级（一次事件只归一类，按序取第一个命中）：

| 型 | 定义 | 优先级 |
|---|---|---|
| External Path | 用 Tcl/XSDB/Vivado 等 MCP 外路径完成本有 MCP 工具的能力 | 1 |
| Substitution | 工具 A 失败/不符预期 → 换工具 B 完成 | 2 |
| Bypass | 已知工具存在却从未调用（绕开） | 3 |
| Workaround | 工具"成功"但结果不符 → 凭经验改参数/流程后成功 | 4 |
| Retry | 同工具同签名反复重试（≥3 次突发）才成功 | 5 |
| Silent Fix | Agent 自行修正 MCP 返回结果后继续 | 6 |

**结构化记录模板**（每条偏离一条记录，白盒报告必填）：

```
Tool: ps_mem_read
Expected: 读取 DDR
Actual: 返回错误/空
Agent action: xsdb mrd
Reason: MCP 工具失败
Classification: B (MCP缺陷)
```

**分类六档与处置线**：

| 档 | 含义 | 处置 |
|---|---|---|
| A | 合法跳过（契约/Skill 明确排除） | 记录即可 |
| B | MCP 缺陷 | 修复轮（P1 级） |
| C | Skill 缺陷（该写没写/写错） | Skill 修订 |
| D | Agent 误判 | 复盘，不修框架 |
| E | 工具能力重叠（换工具是等价的） | 考虑合并/标注 |
| F | 测试环境问题 | 环境治理 |

**分工**：审计器（`tools/audit/bypass_audit.py`）机械取证——工具矩阵、从未调用、失败清单、重试突发、替代候选（A 失败→B 成功）、外部脚本线索；**Workaround / Silent Fix / 最终 A–F 归类由 Agent 自述 + 主代理复核**（这两类在认知层，机器抓不到）。

## 三、能力簇覆盖（替代"每工具必用"）

109 工具按簇覆盖：**每簇 ≥1 正常路径 + 1 跨状态路径 + 1 失败恢复路径**，不逐工具机械覆盖（防止为覆盖而覆盖）。

| 簇 | 代表工具范围 |
|---|---|
| 会话与工作流 | create/close_session、workflow_rollback/resume_from、recover、diagnose、get_* |
| 平台-BD 构造 | platform_create_design…validate、package_user_ip、set_bd_object_property、make_external、export_* |
| PL 构建链 | pl_create_project…generate_bitstream、analyze_*、query_* |
| 仿真 | pl_compile/elaborate/run/parse_sim |
| PS 构建 | ps_import_hardware…ps_compile、set_compiler_options、get_build_status |
| 部署与 JTAG | ps_start/connect_hw_server、list/select/reset/halt/run、load_hardware、download_elf、program_fpga |
| 调试与内存 | ps_reg_read/write、mem_read/write、breakpoint_*、debug_*、stack_trace、diagnose_dap |
| UART | ps_start/wait/stop_uart_capture、read/write_uart、list_serial_ports |
| 观测与一致性 | verify_consistency、evaluate_observation |

## 四、专家帮助率（Expert Compensation Rate）

口径：事件 = 每次工具调用 + 每条自述偏离。分层统计：

```
MCP 原生完成  = 首次调用即成功、无后续偏离、无重试突发的调用
经验 workaround = Workaround + Silent Fix（自述层）
外部工具替代  = External Path + Substitution（机械+自述）
重试依赖     = Retry 突发（机械）
人工/特殊脚本 = 主代理介入 + 非标准脚本
```

目标值**越低越好**，且每条偏离必须有 A–F 分类。本指标直接回答："这系统多少能力是 MCP 自己提供的，多少是 Agent 的聪明补上的。"

## 五、任务书模板 v2

白盒任务书必含：① 目标（产品判据+机制清单）；② L0 动态对拍项（每簇抽 1–2 代表工具）；③ **L2-A 强制转移清单** + **L2-B 本 Brick 扰动清单（有界）**；④ L3 规则（能力簇覆盖 + 诊断回退白名单）；⑤ 偏离审计义务（auditor 输出 + 结构化记录 + A–F 归类 + **发现即落盘**——每个问题当场写入 FINDINGS 文件，不攒到汇报，上下文损坏时不丢发现）；⑥ 专家帮助率统计义务；⑦ 输入白名单 SHA + 隔离纪律；⑧ 完成标准 + 停止汇报纪律。

黑盒任务书追加：切片定义 + LESSONS_LEARNED 回流义务。
