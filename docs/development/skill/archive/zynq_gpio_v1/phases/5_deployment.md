# Phase 5 — Deployment (JTAG)

> 输入: Bitstream + ELF + UART port
> 前提: Phase 4 一致性校验通过
> 硬件要求: hw_server 运行在 localhost:3121，板卡上电，JTAG 线连接，COM4 UART 可用

## Skill 决策

- JTAG 操作严格串行：先烧录 PL bitstream，再部署 PS ELF
- UART capture **必须在 CPU 执行前打开**——确保不丢数据
- 部署序列: `select → halt → rst -system → ps7_init → fpga -f → loadhw → dow → con`（8 步，缺一不可）
- **⚠️ 8 步部署必须在同一个 MCP session 内完成**。JTAG lease、target 选择和
  Controller-owned XSDB 状态都绑定到该 session；不能拆成多个 server 生命周期。

## 前提检查

| 检查项 | 方法 | 不可用时 |
|--------|------|---------|
| hw_server | `ps_connect_hw_server` 是否成功 | 标记 `BLOCKED: HW_SERVER` |
| JTAG 链 | `ps_list_targets` → ARM Cortex-A9 DAP 存在 | 标记 `BLOCKED: NO_ARM_DAP` |
| UART | `ps_list_serial_ports` → 检查返回数据中的 `port` 字段 | 标记 `BLOCKED: NO_UART` |

**`ps_list_serial_ports` 返回格式**：
```json
{"status": "success", "data": {"ports": [{"port": "COM4", "description": "...", "hwid": "...", "vid": "0x10C4", "pid": "0xEA60"}], "count": 3}}
```
**判断 COM4 存在**：`any(p["port"] == "COM4" for p in result["data"]["ports"])`，**不要**用 `"COM4" in ports`（ports 是 dict 列表，不是字符串列表）。

## 执行序列

### 5a. 验证 JTAG 链

编程前先检查 JTAG 状态，确认 ARM 核可见：

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `ps_connect_hw_server` | `{}` | `status == "SUCCEEDED"` |
| 2 | `ps_list_targets` | `{}` | JTAG 链包含 4 个目标（APU + 2×ARM + xc7z020） |

如果 JTAG 链只有 2 个目标（DAP + xc7z020），没有 ARM 核：
→ 调用 `ps_ensure_arm_accessible` 恢复（`rst -system`）

### 5b. 开始 UART Capture（在 CPU 执行前——强制顺序）

**顺序不可变更**：capture 必须先于 JTAG 部署中的 `ps_run_target` 打开。

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 4 | `ps_start_uart_capture` | `{"session_id": "...", "port": "COM4", "baudrate": 115200}` | `operation_id` 非空 |
| 4b | `wait_operation` | `{"operation_id": "..."}` | `status == "SUCCEEDED"` |

**⚠️ UART tools 都是 COMMAND tools**（`ps_*` 前缀），调用后返回 `operation_id`，必须 `wait_operation` 才能拿到结果。

**`capture_id` 取值路径**（3 层嵌套）：
```python
start_r = await call("ps_start_uart_capture", {"session_id": sid, "port": "COM4", "baudrate": 115200})
op_id = start_r["data"]["operation_id"]
wait_r = await wait_operation(op_id)
capture_id = wait_r["data"]["result"]["data"]["capture_id"]   # ← 3 层嵌套
```

如果取错层级（如 `wait["data"]["capture_id"]`），拿到空字符串传入 `ps_wait_uart_capture`，server 返回纯文本 `Input validation error` 而非 JSON，导致 `JSONDecodeError`。

**capture 是持久后台 reader**——从 `start` 那一刻起持续读串口，所有到达的数据实时累积到内部 buffer。

**波特率说明**：PS UART 实际传输速率约 **115,944 bps**（Zynq-7020 IO_PLL=1000MHz → UART_REF≈90.9MHz → CD=49,BDIV=16 → 0.64% 偏差）。PC 端以 115200 读取在容忍范围内（±2%），如乱码则用 `ps_diagnose_uart_clock` 计算真实波特率。

### 5c. ARM 部署（标准 Zynq-7020 流程）

公开 MCP 固定实现以下标准序列：

```
select → halt → rst -system → ps7_init → fpga -f → loadhw → dow → con
```

**关键补充（loadhw）**：`ps7_init` 初始化 DDR 控制器和系统时钟，但**不**注册 PL 的 AXI 内存映射。`loadhw $xsa_path` 告诉 ARM "哪些地址是 PL 外设、如何路由"。缺少此步骤，`Xil_Out32(0x41200000, ...)` 会访问无效地址导致 CPU crash。

| 步骤 | MCP Tool | 参数 | 目的 |
|------|----------|------|------|
| 5 | `ps_select_target` | `{"session_id": ..., "target_id": 2}` | ARM Cortex-A9 MPCore #0 |
| 6 | `ps_halt_target` | `{"session_id": ...}` | CPU 暂停（幂等——已 halt 仍成功） |
| 7 | `ps_reset_target` | `{"session_id": ..., "scope": "system"}` | rst -system，复位系统（非处理器） |
| 8 | `ps_initialize_ps` | `{"session_id": ..., "tcl_path": "<ws>/ps/.../ps7_init.tcl"}` | 初始化系统时钟+PLL+MIO+DDR+PS-PL接口 |
| 9 | `pl_program_fpga` | `{"bitstream_path": "<bitstream_path>"}` | 烧录 FPGA（**必须在 ps7_init 之后**） |
| 10 | `ps_load_hardware` | `{"session_id": ..., "xsa_path": "<xsa_path>"}` | 注册 PL AXI 内存映射 |
| 11 | `ps_download_elf` | `{"session_id": ..., "elf_path": "<elf_path>"}` | 下载 ARM 程序到 DDR |
| 12 | `ps_run_target` | `{"session_id": ...}` | 启动 CPU 执行 |

**⚠️ FPGA 编程顺序（B08 R4 验证）**：`fpga -f` 必须在 `ps7_init` 之后执行。
`rst -system` 将 PS-PL AXI 接口置于阻塞状态，如果在此之前烧录 FPGA，
`Xil_Out32(0x41200000, ...)` 访问 PL 地址空间时 CPU 会进入 abort handler
（PC 跳到 0xffffff28）。`ps7_init` 重新初始化系统时钟和 PS-PL 接口后，
`fpga -f` 才能安全配置 PL 并与 ARM 通信。

### 5d. 等待 UART 输出

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 13 | `ps_wait_uart_capture` | `{"session_id": "...", "capture_id": "...", "markers": ["WROTE:0x", "GPIO_E2E_PASS"], "timeout_s": 90}` | `operation_id` 非空 |
| 13b | `wait_operation` | `{"operation_id": "...", "timeout_s": 140}` | `status == "SUCCEEDED"` |

**⏱ 超时预算**：外层 `wait_operation` 的超时必须**显著大于**内层 op 的 `timeout_s`
（因为每轮 `wait_operation` 内部轮询有 0.5-1s 间隔开销）。规则：外层 ≥ 内层 + 30s。
本例：内层 90s → 外层 140s。

**⚠️ 同样需要 `wait_operation`。** 所有 UART tools 都是 command tool，返回 `operation_id` 后才能获取实际结果。

**marker 选择**：`ps_wait_uart_capture` 要求列表中**全部** marker 出现才返回
`matched`。因此正式等待列表是 `WROTE:0x` + `GPIO_E2E_PASS`；不能把互斥的
`GPIO_E2E_FAIL` 加入同一必需列表。停止 capture 后仍必须检查完整文本中不存在
`GPIO_E2E_FAIL`。

**timeout 设为 90s**：8 轮 × ~1s = ~8s，90s 有余量覆盖 delay 偏差。

**注意**：`session_id` 必须作为参数传入所有 PS domain tools（`ps_*` 前缀），但 control tools（`create_session`、`get_execution_state`、`wait_operation`）和 verification tools（`verify_consistency`）不需要。

### 5e. 收集输出

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 14 | `ps_stop_uart_capture` | `{"session_id": "...", "capture_id": "..."}` | `operation_id` 非空 |
| 14b | `wait_operation` | `{"operation_id": "..."}` | `status == "SUCCEEDED"` |

**⚠️ 同样需要 `wait_operation`。** `text` 和 `char_count` 在 `wait_operation` 返回的 `data.result.data` 中。

**保存返回的 `text` 字段**——Phase 6 需要。

### 5f. 清理 UART 输出（必须）

**xil_printf null 字节问题**：bare-metal `xil_printf` 在 Zynq-7020 PS UART 上使用 32-bit 写（`Xil_Out32`）操作 8-bit UART TX FIFO，导致每字符间插入 3 个 `\x00` 字节。`evaluate_observation` 的 marker 匹配会被这些 null 字节破坏。

**必须在 `evaluate_observation` 调用前清理**：
```python
uart_text = stop_result["data"]["text"]
uart_text = uart_text.replace("\x00", "")  # 清理 xil_printf 的 32-bit 写填充
```

如果 UART 文本不作此清理，marker `GPIO_E2E_PASS` 会变成 `G\x00\x00\x00P\x00\x00\x00...`，导致 Phase 6 误判 `INCOMPLETE`。

## 产物

| 产物 | 用途 |
|------|------|
| UART 完整输出文本 | Phase 6 判定 PASS/FAIL |

## 失败恢复

| 症状 | 动作 |
|------|------|
| JTAG 链只有 DAP + xc7z020 | 调用 `ps_ensure_arm_accessible` |
| DAP 不响应 | 先保存 Operation/JTAG resource 公开状态；按 `recommended_action` 调用 `ps_recover_target("auto")` 或 `recover_execution` |
| download 失败 | 检查 ELF 是否正确编译、DDR 是否已初始化（ps_initialize_ps 成功？loadhw 已调用？） |
| UART 无输出 | 检查波特率偏差（`ps_diagnose_uart_clock`）+ 确认 capture 在 run 之前打开 + 确认程序未 crash（halt 后读 pc 寄存器） |
