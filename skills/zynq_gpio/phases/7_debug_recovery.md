# Phase 7 — Debug & Recovery

> 触发条件: 任何 Phase 失败且无法自动恢复时
> 原则: 先诊断，再恢复。不要盲目重试。

## 总原则

你是一个有诊断能力的 AI。遇到错误时：

1. **先分类**——对照下面 8 种错误类型，确认属于哪一种
2. **收集证据**——用 `get_operation_status` 查 op 详情，用 `get_execution_state` 查 server 状态，用 `diagnose_execution` 看 server 自我诊断
3. **选择恢复策略**——下面每种类型给出了诊断步骤
4. **不要使用逃生通道**——所有 EDA/build/Manifest/JTAG/UART/恢复动作通过公开 MCP tools 完成；不得导入内部模块、启动工具进程、编辑 Ledger 或按名称杀进程
5. **服从 `recommended_action`**——WAIT 只等，DIAGNOSE 只诊断，RECOVER 先确认无活动受控进程/资源，STOP 则停止并报告

## 错误分类与诊断

### ENV_ERROR — 环境/工具不可用

**症状**: `ADAPTER_NOT_READY`, `VIVADO_NOT_FOUND`, `ENV_ERROR`

**诊断**: 调用 `get_execution_state` 和 `diagnose_execution`，记录 backend、
process health、PID ownership、current_step 与 reason_code。hw_server 可达性只通过
公开 JTAG tool 返回判断。

**恢复**: 按公开诊断向用户报告缺失环境。智能体不得自行修改 PATH、启动 EDA
或 hw_server；环境修复后由用户重新发起新 session。

### TOOL_ERROR — 工具执行失败

**症状**: `TOOL_ERROR`, `VIVADO_TCL_ERROR`

**诊断**: 查看 operation 的 `error.message`、`vendor_status`、`current_step` 和 `status_source`。

**恢复**: 根据错误内容修正参数后重试。如果连续 3 次相同错误 → 报告并停止。

### PLATFORM_ERROR — BD 设计错误

**症状**: `BD_VALIDATION_FAILED`（BD 验证失败），地址冲突

**诊断**:
- BD 验证失败：检查 Tcl 输出中的 `ERROR` / `CRITICAL WARNING`
- 地址冲突：用 `verify_consistency` 检查 address_map

**恢复**: 当前流程走 `platform_generate {}` 快捷路径，BD 是固定配置。如果快捷路径失败 → 检查 board profile 和 XSA 导出产物。

### PL_BUILD_ERROR — 综合/布局/布线失败

**症状**: `SYNTHESIS_FAILED`, `IMPLEMENTATION_FAILED`, `TIMING_NOT_MET`

**诊断**:
- `get_execution_state` → 确认 worker 和 Vivado 进程状态
- `get_operation_status` → 查看详细错误消息

**恢复**:
| 子类 | 动作 |
|------|------|
| 综合失败 | 检查 RTL 源码和 BD wrapper 是否正确添加到工程。确认 `pl_generate_system_top` 先执行了 |
| 布局/布线失败 | 尝试换 directive = `Explore` |
| 时序失败 (WNS < 0) | 报告 WNS/TNS 值。如果 WNS 接近 0 (≤ 0.3ns)，可尝试 `Explore` directive。否则降低 FCLK 频率或检查约束 |
| 长任务无进展 | 以 `observed_state`, `last_progress_at`, `observation_quality`, `deadline_at` 判断；按 `recommended_action` WAIT/DIAGNOSE/RECOVER，不自行判死或杀进程 |

### PS_BUILD_ERROR — 编译/链接失败

**症状**: `BUILD_FAILED`, `APP_CREATE_FAILED`, `BSP_CREATE_FAILED`

**诊断**:
- 检查错误消息中的编译器输出
- 确认 XSA 文件正常（Phase 1 产出）
- 确认源码路径正确

**恢复**:
- `BUILD_FAILED`: 检查 main.c 中是否有未定义的符号（需要 BSP 先创建）
- `IMPORT_HW_FAILED`: XSA 可能缺少 HDF。确认 platform_generate 成功完成

### JTAG_ERROR — JTAG 通信失败

**症状**: `TARGET_UNRESPONSIVE`, `DAP not responding`, `DOWNLOAD_FAILED`

**诊断**:
| 步骤 | MCP Tool | 查什么 |
|------|----------|--------|
| 1 | `ps_list_targets` | JTAG 链上设备列表，ARM DAP 是否存在 |
| 2 | `ps_get_device_info` | DAP 设备信息（idcode） |
| 3 | `ps_get_target_status` | 目标当前状态（halted/running/reset） |
| 4 | `ps_diagnose_dap` | DAP 诊断报告 + 建议操作 |

**恢复**:
| 故障 | 动作 |
|------|------|
| DAP 无响应 | `ps_recover_target("auto")` — 自动 cascade: halt→processor_reset→core_reset→system_reset→ps7_init→verify |
| 重连失败 | `ps_reconnect_target` — 断开重连 |
| 残留调试状态 | `ps_clear_debug_session` — 清除断点和调试器残留 |
| Bitstream 未烧录 | 回到 Phase 5，执行公开 `pl_program_fpga` |

### 特殊诊断：UART 有输出但 GPIO 不通

**这是 Zynq-7020 开发中最容易误判的场景。** UART 走 PS 地址空间（`0xE0001000`），只需 `ps7_init` 就能工作。GPIO 走 PL 地址空间（`0x41200000`），必须额外执行 `loadhw $xsa` 注册 PL 的 AXI 内存映射。

症状：UART 正常输出、ARM 程序在跑、但 GPIO 读写无效（LED 不闪烁、`Xil_In32(GPIO_BASE)` 返回 0 或崩溃）。

诊断：
1. `ps_halt_target` → 确认 CPU 可以 halt
2. `ps_reg_read("pc")` → PC 在 `main` 附近（不是 `0xffffff28` 复位向量）
3. `ps_mem_read(0x41200000)` → 读到什么？
   - 返回有效值 = GPIO 通路正常
   - 返回 0 且无异常 = `loadhw` 未执行 → 回到 Phase 5 确认 `ps_load_hardware` 被调用
   - `MEM_READ_FAILED` = AXI 路由未建立 → 同上

### UART_ERROR — UART 无输出或乱码

**症状**: UART capture 返回 `timeout` 或 `partial`

**⚠️ 先确认波特率**：PS UART 实际传输速率约为 **115,944 bps，不是标准 115200**。
- Zynq-7020 IO_PLL = 1000 MHz
- SLCR UART_CLK_CTRL DIVISOR0 = 10 → UART_REF = 1000/11 ≈ 90.9 MHz
- UART1 BAUDGEN CD = 49, BAUDDIV BDIV = 16
- 实际 Baud = 90.9M / (49×16) = 115,944 bps（0.64% 偏差）
- PC 以 115200 读取误差在 ±2% 容忍范围内，但如果晶振偏移或线缆质量差，可能超阈

**标准诊断 cascade**（使用 JTAG 直接读寄存器）:

| 步骤 | MCP Tool | 参数 | 检查 |
|------|----------|------|------|
| 1 | `ps_halt_target` | `{}` | 能否 halt CPU |
| 2 | `ps_reg_read` | `{"register": "pc"}` | PC 值——是否飞到了 abort handler？ |
| 3 | `ps_reg_read` | `{"register": "cpsr"}` | CPU 模式 |
| 4 | `ps_diagnose_uart_clock` | `{"expected_baud": 115200}` | **一键诊断**——读 SLCR UART_CLK_CTRL + UART1 BAUDGEN/BAUDDIV 寄存器，计算真实波特率 |

`ps_diagnose_uart_clock` 返回的 `computed_baud` 是 ARM 端**真正的传输速率**。如果 `baud_match == false`：
- 记录 `computed_baud` 值（如 115944）
- 用这个值重新启动 capture：`ps_start_uart_capture(port="COM4", baudrate=<computed_baud>)`
- 然后 `ps_run_target` → `ps_wait_uart_capture`

**如果仍然无输出**（波特率正确时）:
- 确认 capture 在 `ps_run_target` **之前**打开（先开窗户再放跑）
- 确认 `ps_download_elf` 成功、ELF 正确
- 确认 `main.c` 中 UART 初始化通过（检查 PC 是否在 main 附近而非 0xffffff28 等复位向量 地址）

### ARTIFACT_STALE — 产物校验失败

**症状**: `verify_consistency` 返回 `failed` 非空

**诊断**: 逐个检查 failed 的规则——哪个 manifest 的 revision/SHA256 不匹配。

**恢复**: 不匹配的 Phase 必须重跑。例如 platform_revision 变了→重新跑 Phase 2 和 Phase 3。

## 通用诊断工具

| Tool | 用途 | 何时用 |
|------|------|--------|
| `get_execution_state` | 查看 lane/stage/backend/resource 状态 | 任何错误后第一步 |
| `get_operation_status` | 查看真实 backend observation、步骤、deadline、产物与建议 | 某个 tool 调用失败或长时间运行时 |
| `diagnose_execution` | Server 自我诊断报告 | lane = RECOVERY_REQUIRED 时 |
| `recover_execution` | 尝试将 lane 从 RECOVERY_REQUIRED 恢复到 IDLE | 残留状态阻塞新操作时 |

## 不可恢复的情况

如果以下全部尝试后仍然无法继续：
1. 公开诊断建议恢复且 `recover_execution` 失败
2. `ps_recover_target("auto")` 失败
3. 重新 `create_session`（新 project_path）

→ **停止并向用户报告**。列出已尝试的恢复步骤和每个步骤的结果。
