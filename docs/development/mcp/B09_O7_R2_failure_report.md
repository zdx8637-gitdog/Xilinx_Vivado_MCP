# B09 O7 第二轮黑盒失败报告

> 日期：2026-08-13
> 状态：FAIL / NOT FROZEN
> 角色：全新监督子代理启动的项目外无记忆 Agent2
> 隔离根：`D:\_o7_external\agent2_20260813_r2`

## 1. 终态

O7 R2 必须判定为 **FAIL**。R1 的 Vivado 启动故障已在第三套隔离预检中关闭，正式 Agent2 也成功通过 Platform、综合、布局、布线和 timing 门禁，但在 P2 `pl_generate_bitstream` 的 PL Manifest 发布门禁失败，因此没有继续 P3–P6。

## 2. 已通过的真实公开 MCP 阶段

- `platform_generate` operation `op-e6b2fdbf66414924aab26664ac960cb0`：`SUCCEEDED / PUBLISHED`；
- `pl_generate_system_top`、XDC 输入创建、`pl_create_project`、`pl_generate_target`：成功；
- `pl_synthesize` operation `op-e3b20f8e127e4a868d23b442f6794bd6`：`SUCCEEDED`；
- `pl_place` operation `op-6eab10ae8eea40ce93212a92d595fda6`：`SUCCEEDED`；
- `pl_route` operation `op-cc5387f1af6c44dd91a9794837856529`：`SUCCEEDED`；
- `pl_analyze_timing` operation `op-1038a9e677b44c1b83046a594fc197d6`：`SUCCEEDED`，`timing_met=true`。

## 3. P2 失败

| 项目 | 证据 |
|---|---|
| Tool | `pl_generate_bitstream` |
| Operation | `op-5c27bf8ab3254858b623f901d94f4ea4` |
| 终态 | `FAILED` |
| Artifact state | `FAILED` |
| Code | `ARTIFACT_STALE` |
| Reason | `MANIFEST_PUBLISH_FAILED` |
| Step | `PL_MANIFEST_PUBLISH` |

Vivado 的 canonical run 目录中已经生成 `system_top.bit`，但公开请求路径 `workspace\bitstream\gpio_led.bit` 的父目录不存在。Tcl `file copy` 的交互错误文本未被旧解析规则识别，PL 工具误报 success；随后强制 Manifest 发布因目标 bitstream 不存在而 fail-closed。

## 4. 解决方案

1. `pl_generate_bitstream` 在进入 vendor run 前由 MCP 创建公开输出父目录；
2. copy Tcl 使用 `catch` 输出明确成功/失败标记；
3. 工具返回 success 前必须同时看到 `BIT_DONE` 且请求路径确实存在；
4. 缺失目标文件返回 `ARTIFACT_STALE / BITSTREAM_NOT_FOUND`，不得拖到 Manifest 阶段才暴露；
5. 外部 Skill cleanup 只在公开资源状态显示 JTAG 已连接时调用 disconnect，避免对不存在的 lease 产生伪 cleanup failure。

## 5. 边界与清理

机械轨迹只有一次只读 Skill `Get-Content`；加载后无其他 command execution，EDA/build/Manifest 全部来自公开 MCP。Agent2 在 P2 首个强制失败后停止推进。`close_session` 成功并执行 `direct_backend_shutdown`，本轮 Vivado 已退出；运行前既有 `hw_server` PID 19880 未被触碰。

`ps_disconnect_hw_server` 在 JTAG 从未连接时被 Agent2 调用并返回 `JTAG_LEASE_MISSING`。这是 Skill cleanup 条件执行问题，不是本轮 P2 主失败原因。

## 6. 原始证据

- `D:\_o7_external\agent2_20260813_r2\evidence\o7_r2_trace.jsonl`
- `D:\_o7_external\agent2_20260813_r2\evidence\o7_r2_stderr.txt`
- `D:\_o7_external\agent2_20260813_r2\evidence\o7_r2_last.txt`
- `D:\_o7_external\agent2_20260813_r2\workspace\O7_AGENT2_R2_BLACKBOX_REPORT.md`

R2 环境和证据只读保留。下一轮必须使用新的 runtime、workspace、插件版本和无记忆 Agent2，不得从 R2 session 恢复。
