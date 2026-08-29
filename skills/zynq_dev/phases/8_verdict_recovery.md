# S8 — 判定 / 恢复（Verdict & Recovery）

> 输入: UART 捕获文本 | 输出: 机读 PASS/FAIL 判定 + 证据归档
> 触发条件: 任何阶段失败且无法自动恢复时，本阶段也是恢复入口

## 判定职责

机读判定，语义固定（直接调用工具，不需要 AI 主观判断）：

| 步骤 | 工具类别 | 参数 |
|------|----------|------|
| 1 | `evaluate_observation` | `{"uart_text": "<捕获文本>", "pass_marker": "<PASS_MARKER>", "fail_marker": "<FAIL_MARKER>"}` |

- **marker 显式传需求文档给定的值**（`<PASS_MARKER>` / `<FAIL_MARKER>`），
  不得使用默认值或臆造 marker；
- `verdict` 取值：`PASS`（含 PASS marker）/ `FAIL`（含 FAIL marker 且无 PASS）/
  `TIMEOUT`（文本为空）/ `INCOMPLETE`（有内容但无任何 marker）；**PASS marker
  优先于 FAIL marker**。

## 证据归档

- verdict + 完整 UART 文本 → 保存为 `<PROJECT_PATH>/evidence/uart_result.json`；
- 证据链：S0–S8 所有产物的路径 + SHA256 + 判定结果（Manifest 是自动发布的证据）。

## 故障归因约束（Evidence-Based Fault Isolation）

已由下层独立证据 PASS 的域，在没有新的反证前**不得作为首要修改对象**；
修改范围以证据定位到的故障段（相邻 Observation Point 差值 / Event Counter
比对结果）为边界。

原因：测试工具产生的是证据；证据不仅用于 PASS/FAIL 判定，也用于**约束修改
范围**。最终现象异常时最常见的失效模式是「到处改」——把原本正确的下层实现
也改坏。证据的作用就是限定「允许修改哪一层代码」。

## 智能体自主决策范围

- 判定执行、报告、恢复决策。

## 用户必须提供的决策

- 最终审核（是否冻结、是否进入下一轮）。

## 失败恢复入口（错误分类与诊断 cascade）

**先分类，再诊断，再恢复，不要盲目重试。**

| 类别 | 症状示例 | 诊断 | 恢复 |
|------|----------|------|------|
| ENV | 环境/工具不可用 | `get_execution_state` + `diagnose_execution` | 按公开诊断报告缺失环境；智能体不得自行改 PATH/启动 EDA |
| TOOL | 工具执行失败 | 查 `error.message` / `vendor_status` | 修正参数重试；连续 3 次相同错误 → 报告停止 |
| PLATFORM | BD 验证失败/地址冲突 | 查 Tcl 输出 ERROR/CRITICAL WARNING；`verify_consistency` | 修正 BD 配置从对应原子重跑 |
| PL_BUILD | 综合/布局/布线/时序失败 | `get_execution_state` + `get_operation_status` | 按错误类别修正输入重跑；时序失败记录 WNS/TNS |
| PS_BUILD | 编译/链接失败 | 查编译器输出、XSA、源码路径 | 修正源码后 `ps_compile` 重跑 |
| JTAG | 目标无响应/下载失败 | `ps_list_targets` / `ps_diagnose_dap` / `ps_get_target_status` | `ps_recover_target` / `ps_reconnect_target` / `ps_clear_debug_session` |
| UART | 无输出/乱码 | `ps_diagnose_uart_clock`（真实波特率）+ halt 后读 pc | 用 computed_baud 重启 capture；确认顺序 |
| ARTIFACT_STALE | `verify_consistency` failed 非空 | 逐规则定位不匹配 Manifest | 不匹配的域重跑（例如 revision 变了 → 重跑下游域） |

**服从 `recommended_action`**：WAIT 只等，DIAGNOSE 只诊断，RECOVER 先确认无
活动受控进程/资源，STOP 则停止并报告。

## 不可恢复的情况

以下全部尝试后仍无法继续：① 公开诊断建议恢复且 `recover_execution` 失败；
② `ps_recover_target` 失败；③ 重新 `create_session`（新 `<PROJECT_PATH>`）。
→ **停止并向用户报告**，列出已尝试的恢复步骤与结果。

## 涉及的工具类别

- verification query：`evaluate_observation`、`verify_consistency`；
- control query：`get_operation_status`、`get_execution_state`、`diagnose_execution`、
  `recover_execution`；
- ps 诊断 command：`ps_diagnose_uart_clock`、`ps_recover_target`、
  `ps_reconnect_target`、`ps_clear_debug_session`、`ps_halt_target`、`ps_reg_read`。
