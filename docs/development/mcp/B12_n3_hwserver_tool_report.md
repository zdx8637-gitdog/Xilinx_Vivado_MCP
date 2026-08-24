# B12-N3 整改报告：新增 `ps_start_hw_server`（hw_server 本地自启）

日期：2026-08-24
范围：公开 zynq_mcp 新增 `ps_start_hw_server` 工具，框架自足启动 hw_server，用户零人工。

## 1. 现象 / 根因

- **现象**：环境重启后旧 hw_server 进程消失，公开工具只有 `ps_connect_hw_server`
  （连接**已有**实例，`domains/ps/jtag_target.py::connect_hw_server`），无启动能力；
  B12-A1 白盒重跑 S7 前无法自足恢复 JTAG 服务器。
- **根因**：PS 域公开契约缺失「本地自启 hw_server」这一侧——连接工具只做
  `connect tcp:<url>`，把「实例必须已在监听」当成了外部前提，框架不自足。

## 2. 实现位置（文件:行）

| 位置 | 内容 |
|------|------|
| `mcps/zynq_mcp/domains/ps/hw_server_start.py`（新） | `start_hw_server` 生产入口：有界 TCP 探测 → exe 解析 → detached 派生 → 有界就绪等待；`parse_host_port` / `tcp_port_open` / `resolve_exe` / `spawn_hw_server` 辅助函数 |
| `mcps/zynq_mcp/domains/ps/__init__.py:56-60` | `_REASON_TO_CODE` 新增 `HW_SERVER_NOT_FOUND`(ENV_ERROR) / `HW_SERVER_START_FAILED`(TOOL_ERROR) / `HW_SERVER_START_TIMEOUT`(TOOL_ERROR) |
| `mcps/zynq_mcp/control/capabilities.py:47` | `DOMAIN_TOOLS` 注册 `ps_start_hw_server`（schema `{url?, exe_path?}`） |
| `mcps/zynq_mcp/control/capabilities.py:423` | `ps.implemented` 48→49 |
| `mcps/zynq_mcp/dispatcher.py:46` | import `hw_server_start` |
| `mcps/zynq_mcp/dispatcher.py:107` | `_PS_TOOL_NAMES` 加入 `ps_start_hw_server` |
| `mcps/zynq_mcp/dispatcher.py:944` | `_PS_TOOL_MAP["ps_start_hw_server"] = (hw_server_start, "start_hw_server")` |
| `mcps/zynq_mcp/control/domain_runner.py:51` | `_PS_LOCAL_DIRECT_TOOLS` 加入 `ps_start_hw_server`（本地执行、`bridge=None`、不占 EDA worker） |
| `skills/zynq_dev/phases/7_deployment_observation.md:20` | 7a 预检表加一行 hw_server 自启 |
| `skills/zynq_dev/phases/7_deployment_observation.md:91` | 「涉及的工具类别」补 `ps_start_hw_server` |

## 3. schema

```json
{"type": "object",
 "properties": {"url": {"type": "string"}, "exe_path": {"type": "string"}}}
```

- `url` 默认 `localhost:3121`，可选；`exe_path` 可选覆盖。
- `session_id` 由 `_inject_ps_session_schema` 机械注入（transport 字段，非 required）。

## 4. 行为契约

1. TCP 探测 `url` 端口（有界 ~2s）→ 已监听返回 `{already_running: true, url}`，**不触碰现有进程**；
2. 未监听 → 解析 `hw_server.exe`（`exe_path` 覆盖 > 默认
   `D:\Xilinx\Vitis\2023.1\bin\unwrapped\win64.o\hw_server.exe`）；找不到 →
   `ENV_ERROR / HW_SERVER_NOT_FOUND`；
3. 派生 `asyncio.create_subprocess_exec`，Windows 带
   `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`（0x208，脱离 MCP 常驻），记录 pid；
4. 就绪等待真实轮询端口（0.5s 间隔，30s 总超时）→
   `{started: true, pid, exe, port, url}`；超时 → `TOOL_ERROR / HW_SERVER_START_TIMEOUT`；
   进程提前退出 → 读 stderr 摘要 → `TOOL_ERROR / HW_SERVER_START_FAILED`；
5. 幂等；**只启动不停止**（常驻基础设施）。

## 5. 测试清单（新文件 `mcps/zynq_mcp/tests/test_ps_start_hw_server.py`）

组件（mock 端口探测 / exe 解析 / spawn，非 host_live，全绿）：

- `test_already_running_reuse` — 已监听复用，spawn 不被调用
- `test_exe_missing_fail_closed` — exe 缺失 → `ENV_ERROR/HW_SERVER_NOT_FOUND`
- `test_spawn_failure` — spawn 失败 → `TOOL_ERROR/HW_SERVER_START_FAILED`
- `test_readiness_timeout` — 就绪超时 → `TOOL_ERROR/HW_SERVER_START_TIMEOUT`
- `test_early_exit_reports_stderr` — 提前退出 → stderr 摘要 + exit_code
- `test_invalid_url` — 非法 url → `INVALID_ARGUMENT/INVALID_URL`
- `test_wiring_registered_everywhere` — capabilities/dispatcher/`_PS_LOCAL_DIRECT_TOOLS`/schema 全接线

host_live（真实启动）：

- `test_host_live_real_spawn_and_cleanup` — 真实 `hw_server.exe` 派生 → 端口 LISTENING →
  字段正确（started/pid/exe/port/url）→ 只清理本测试启动的 PID；启动前已有 hw_server
  则走 `already_running` 分支并跳过终止。

## 6. 测试结果

- 组件（非 host_live）：`7 passed, 1 deselected`。
- host_live：`1 passed, 6 deselected`（真实派生 ~5.8s，端口实测 LISTENING，清理后
  `hw_server` 进程消失、端口关闭）。
- 计数断言更新 6 处 `==103 → ==104`：
  `test_r3_runner.py:809`、`test_r3_1c_public.py:251`、`test_r2_adapter.py:756`、
  `test_r1_mcp_sdk.py:119`、`test_pl_bridge.py:956`、`test_o6_skill_contract.py:237`。
- `ps.implemented` 48→49；`DOMAIN_APIS_IMPLEMENTED`/`total_tools` 机械派生自动 95/104。

## 7. 回归（项目根目录，非硬件）

```
1393 passed, 1 skipped, 41 deselected in 201.09s (0 failed)
```

对照基线（1427 collected / 1386 passed / 1 skipped / 40 deselected / 0 failed）：
+7 passed（本文件 7 个组件测试）、+1 deselected（本文件 1 个 host_live），无下降、无失败。
`ps_connect_hw_server` 语义零改动，其既有测试全绿（含
`test_b06_ps_public.py` 等 206 passed / 5 deselected 定向回归）。

## 8. Skill 改动点

- 7a 预检表新增一行：`hw_server（未运行时）→ ps_start_hw_server（本地自启，幂等，
  只启不停）→ 自启失败按 ENV_ERROR 诊断（HW_SERVER_NOT_FOUND / HW_SERVER_START_TIMEOUT）
  → 标记 BLOCKED: HW_SERVER`。
- 「涉及的工具类别」补 `ps_start_hw_server`。
- 零字样门禁自查：新增文本仅含 hw_server / ps_start_hw_server / ENV_ERROR /
  HW_SERVER_NOT_FOUND / HW_SERVER_START_TIMEOUT，**未新增** `gpio` / `0x41200000` /
  `LED` / `breath|blink` 字样。

## 9. host_live 证据

- 命令：`python -m pytest mcps/zynq_mcp/tests/test_ps_start_hw_server.py -m host_live -q`
  → `1 passed, 6 deselected in 5.79s`。
- 测试后核实：`hw_server` 进程不存在、TCP 3121 端口关闭（只清理本测试启动进程）。

## 10. `.mcp.json` 不变

SHA256 = `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`（未变）。

## 11. 禁区声明

未触碰 `boards/`、架构文档、`README`、`CLAUDE.md`、`legacy/`、`validation_projects/`。
