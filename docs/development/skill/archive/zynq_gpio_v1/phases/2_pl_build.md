# Phase 2 — PL Build（公开 MCP 串行链）

> 输入: Platform Manifest + BD wrapper + Platform BD | 输出: system_top.v + bitstream + 自动发布的 PL Build Manifest

## 硬门禁

Phase 2 的全部 EDA 行为必须经过统一 `zynq_mcp` 的公开 `call_tool`。智能体不得
导入内部模块、启动独立 Vivado/Tcl 进程、复制整个 Vivado 工程后直接执行 Tcl，
也不得手工发布 PL Manifest。

每个下列 tool 都是 command：调用后记录 `operation_id`，使用
`wait_operation`/`get_operation_status` 保存真实 Ledger 观测时间线，终态不是
`SUCCEEDED` 时立即停止串行链。

## Skill 决策

- top 固定为 `system_top`；
- PL 工程固定为 `<project_path>/vivado/gpio_pl`；
- sources 固定包含 Platform BD、Phase 1 wrapper 和生成的 `system_top.v`；
- XDC 放在 `<project_path>/xdc/gpio_led.xdc`，确保自动 Manifest 能发现；
- bitstream 固定为 `<project_path>/bitstream/gpio_led.bit`；
- synthesis/place/route/timing/bitstream 严格按顺序执行，不自动重试。

## 2a. 生成 system_top

| MCP Tool | 参数 | 成功条件 |
|----------|------|----------|
| `pl_generate_system_top` | `{"wrapper_path": "<Phase 1 wrapper_rel>"}` | Operation `SUCCEEDED`; `rtl/system_top.v` 存在；stage=`PL_BUILD` |

`wrapper_path` 必须与 Platform Manifest 交叉引用一致。不得选择另一个 wrapper。

## 2b. 创建约束输入

在 `<project_path>/xdc/gpio_led.xdc` 写入：

```tcl
set_property PACKAGE_PIN J16 [get_ports {gpio_led[3]}]
set_property PACKAGE_PIN K16 [get_ports {gpio_led[2]}]
set_property PACKAGE_PIN M15 [get_ports {gpio_led[1]}]
set_property PACKAGE_PIN M14 [get_ports {gpio_led[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {gpio_led[*]}]
```

创建需求输入文件属于允许的工作区操作；执行 Tcl 不允许。

## 2c. 创建公开 PL 工程

Platform BD 的确定性路径为：

`<project_path>/vivado/platform/platform_project.srcs/sources_1/bd/platform_bd/platform_bd.bd`

调用：

```json
pl_create_project({
  "name": "gpio_pl",
  "part": "xc7z020clg400-2",
  "sources": [
    "<project_path>/vivado/platform/platform_project.srcs/sources_1/bd/platform_bd/platform_bd.bd",
    "<project_path>/hdl/platform_bd_wrapper.v",
    "<project_path>/rtl/system_top.v"
  ],
  "constraints": ["<project_path>/xdc/gpio_led.xdc"],
  "project_dir": "<project_path>/vivado/gpio_pl",
  "top": "system_top",
  "force": true
})
```

路径缺失或 BD 候选不是唯一预期文件时 fail-closed；不得通过复制历史工程绕过。

## 2d. 生成 BD output products

| MCP Tool | 参数 | 成功条件 |
|----------|------|----------|
| `pl_generate_target` | `{"target_type": "synthesis"}` | Operation `SUCCEEDED` |

这一步通过受控 Vivado backend 生成 BD OOC netlist/constraints，是综合前置。

## 2e. 综合、布局、布线、时序、bitstream

| 顺序 | MCP Tool | 参数 | 成功后的 stage / 证据 |
|------|----------|------|------------------------|
| 1 | `pl_synthesize` | `{"top": "system_top"}` | `PL_IMPLEMENT`; `vendor_status` 为完成态 |
| 2 | `pl_place` | `{}` | `PL_IMPLEMENT`; placement 完成 |
| 3 | `pl_route` | `{}` | `PL_TIMING`; route 完成并打开实现结果 |
| 4 | `pl_analyze_timing` | `{}` | `PL_BITSTREAM`; `completion_evidence.timing_met == true` |
| 5 | `pl_generate_bitstream` | `{"path": "<project_path>/bitstream/gpio_led.bit", "force": true}` | `PS_BUILD`; bitstream 有效；PL Manifest 已自动发布 |

`progress_pct` 可为空；必须有 `status_source`, `observed_state`, `vendor_status`,
`current_step`, `observation_quality`, `recommended_action`。等待期间只按公开建议
WAIT/DIAGNOSE/RECOVER，不得直接探测或操作 Vivado 进程。

## 2f. PL Manifest 终态门禁

`pl_generate_bitstream` 只有在以下条件全部满足后才能 `SUCCEEDED`：

- bitstream 存在且 SHA256 已验证；
- timing 前置证据为通过；
- XDC 被发现并交叉引用；
- `manifests/pl/sha256_*.json` 由 MCP 自动发布且合法。

若 Operation 返回 `MANIFEST_PUBLISH_FAILED`、`ARTIFACT_FINALIZATION_FAILED` 或
`artifact_state != PUBLISHED`，Phase 2 失败。禁止把“bit 文件存在”当作成功，也禁止
手工补写 Manifest。

## 失败恢复

| 症状 | 动作 |
|------|------|
| admission `CHANNEL_BUSY` | 查看返回的活动 Operation 和 `recommended_action`；等待原任务，不重复提交 |
| `STAGE_PREREQUISITE_UNMET` | 用 `get_execution_state` 核对上一成功阶段，回到缺失的公开步骤 |
| `SYNTHESIS_FAILED` | 检查公开错误、system_top/BD/XDC 输入路径；修正输入后新建干净 session/workspace |
| `TIMING_NOT_MET` | 记录 WNS/TNS，停止；本 GPIO 基线不以手工 directive 绕过时序失败 |
| `TIMED_OUT` / `OUTCOME_UNKNOWN` | `diagnose_execution`；只有公开诊断建议时调用 `recover_execution`，不得杀进程或自行重跑 |
| Manifest 终态门禁失败 | 保留证据并停止；不能绕过产品 finalizer |

## Phase 2 完成条件

同时满足：所有公开 Operation `SUCCEEDED`、stage=`PS_BUILD`、bitstream SHA 有效、
PL Manifest 自动存在且 `artifact_state=PUBLISHED`。否则不得进入 Phase 3。
