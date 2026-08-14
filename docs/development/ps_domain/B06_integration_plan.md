# B06 Integration Phase — Execution Plan

> 2026-08-08 | 前置条件: B05 FROZEN | B06 库阶段完成

## 集成范围

库阶段已交付 ~28 unique PS APIs（排除内部 helper）。分两批注册：

### 第一批：JTAG + UART + Recovery（不依赖 BSP/Build，不依赖 XSA）

| 组 | MCP tool 名称 | 对应 domain 函数 | 输入参数 |
|----|-------------|-----------------|---------|
| JTAG 连接 | `ps_connect_hw_server` | `jtag_target.connect_hw_server` | `url?` |
| | `ps_disconnect_hw_server` | `jtag_target.disconnect_hw_server` | 无 |
| | `ps_list_targets` | `jtag_target.list_targets` | 无 |
| | `ps_select_target` | `jtag_target.select_target` | `target_id` |
| | `ps_get_target_status` | `jtag_target.get_target_status` | 无 |
| | `ps_get_device_info` | `jtag_target.get_device_info` | 无 |
| 目标控制 | `ps_reset_target` | `target_control.reset_target` | `scope?` |
| | `ps_initialize_ps` | `target_control.initialize_ps` | 无 |
| | `ps_run_target` | `target_control.run_target` | `core?` |
| | `ps_halt_target` | `target_control.halt_target` | `core?` |
| | `ps_step_target` | `target_control.step_target` | `core?` |
| | `ps_wait_for_state` | `target_control.wait_for_state` | `state`, `timeout_s?` |
| 内存/寄存器 | `ps_reg_read` | `memory_access.reg_read` | `register` |
| | `ps_reg_write` | `memory_access.reg_write` | `register`, `value` |
| | `ps_mem_read` | `memory_access.mem_read` | `address`, `length?` |
| | `ps_mem_write` | `memory_access.mem_write` | `address`, `data` |
| 恢复 | `ps_recover_target` | `target_recovery.recover_target` | `strategy?` |
| | `ps_reconnect_target` | `target_recovery.reconnect_target` | 无 |
| | `ps_clear_debug_session` | `target_recovery.clear_debug_session` | 无 |
| | `ps_diagnose_dap` | `target_recovery.diagnose_dap` | 无 |
| UART | `ps_read_uart` | 新 wrapper | `port`, `baudrate?`, `duration_ms?` |
| | `ps_list_serial_ports` | 新 wrapper | 无 |

共 22 tools。

### 第二批（后续，需 B05 XSA + BSP）

| 组 | MCP tool 名称 |
|----|-------------|
| BSP/Build | `ps_import_hardware`, `ps_create_platform`, `ps_create_bsp`, `ps_create_app`, `ps_add_sources`, `ps_compile`, `ps_get_build_status`, `ps_read_elf_info` |
| Debug | `ps_debug_start`, `ps_debug_close`, `ps_breakpoint_add`, `ps_breakpoint_remove`, `ps_read_register`, `ps_write_register`, `ps_stack_trace` |
| 下载 | `ps_download_elf`（需编译产出的 ELF） |

第二批本次不做。

## 架构设计：Bridge 生命周期

当前 `CommandRunner.run_command()` 支持 `executor="local"` 路径（不走 Vivado Worker）。PS tools 用 local 路径，但需要 `XsdbBridge` 实例。

**方案**：`CommandRunner` 持有可选的 `XsdbBridge`。首次 PS tool 调用时 lazy-init bridge，`close_session` 时停止。

```
CommandRunner.run_command("ps_list_targets", ...)
  → DomainExecutionMutex.try_acquire()
  → ledger_transaction(admit)
  → _execute("local", local_fn)
      → if bridge not started: await bridge.start()
      → result = await local_fn(bridge, **arguments)
      → mark_succeeded / mark_failed
  → mutex.release()
```

不需要改 `SingleWorkerController`。PS tools 不经过 Worker（Worker 是 Vivado 专用的）。

## 改动清单

### 改动 1: `domain_runner.py`

- `CommandRunner.__init__` 新增可选参数 `xsdb_bridge=None`
- 新增方法 `_ensure_xsdb_bridge()` — lazy start
- 新增方法 `_stop_xsdb_bridge()` — 供 close_session 调用
- `_execute` 的 local 分支：如果是 ps_* tool，在调用 local_fn 前 ensure bridge
- 新增 `_PS_SUCCESS_STAGE` 映射（PS tools 的 stage 不变更——PS 域不推进 workflow stage）

### 改动 2: `capabilities.py`

- `DOMAIN_TOOLS` 列表中新增 22 个 PS Tool 定义
- `build_capabilities()` 中 `ps.implemented` 从 0 改为 22
- `DOMAIN_APIS_IMPLEMENTED` 从 2 改为 24

### 改动 3: `dispatcher.py`

- `_DOMAIN_TOOLS` 集合追加 22 个 PS tool names
- 在 `dispatch()` 中新增 PS tool 路由逻辑：
  ```python
  if tool_name.startswith("ps_"):
      return await self._dispatch_ps_tool(tool_name, arguments, session_id, board_id, project_path)
  ```
- 新增 `_dispatch_ps_tool()` 方法

### 改动 4: `server.py`

- 向 `CommandRunner` 传入 XsdbBridge（如果可用）
- 在 finally block 中停止 bridge

### 改动 5: 测试文件

- 新增 `mcps/zynq_mcp/tests/test_b06_ps_public.py` — MCP SDK contract tests
- 测试范围：query 类 tools（list_targets, get_device_info 等）通过 MCP SDK 调用
- 测试需要 XSDB on PATH，否则 skip

## 不做的

- BSP/Build 管线（第二批）
- Debug 工具（第二批）
- 修改 frozen 的 `SingleWorkerController`
- 修改 frozen 的 `ExecutionLedger` schema
