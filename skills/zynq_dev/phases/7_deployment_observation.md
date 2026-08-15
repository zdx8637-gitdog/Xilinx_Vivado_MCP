# S7 — 部署观测（Deployment & Observation）

> 输入: Bitstream + ELF + UART 观测配置 | 输出: JTAG 部署完成 + 捕获数据
> 前提: S6 一致性校验通过

## 职责

JTAG 部署（8 步标准序列，见 [appendix_mechanics.md](../appendix_mechanics.md)）
+ UART 捕获。**UART capture 必须在 CPU 执行前打开**——先开窗户再放跑。
**8 步部署必须在同一个 MCP session 内完成**（JTAG lease、target 选择与
Controller 持有的调试状态都绑定到该 session）。

## 执行序列

### 7a. 前提检查

| 检查项 | 工具类别 | 不可用时 |
|--------|----------|---------|
| hw_server | `ps_connect_hw_server` | 标记 `BLOCKED: HW_SERVER` |
| JTAG 链 | `ps_list_targets` → ARM Cortex-A9 DAP 存在 | 标记 `BLOCKED: NO_ARM_DAP` |
| UART | `ps_list_serial_ports`（按 `port` 字段判断，勿用字符串包含判断） | 标记 `BLOCKED: NO_UART` |

### 7b. 部署（8 步，缺一不可）

`select → halt → rst -system → ps7_init → fpga -f → loadhw → dow → con`

| 步骤 | 工具类别 | 目的 |
|------|----------|------|
| 1 | `ps_select_target` | 选择 ARM 核 |
| 2 | `ps_halt_target` | CPU 暂停（幂等） |
| 3 | `ps_reset_target`（scope=system） | 复位系统（非处理器） |
| 4 | `ps_initialize_ps` | 初始化系统时钟 + DDR + PS-PL 接口（ps7_init.tcl 来自工程产物） |
| 5 | `pl_program_fpga` | 烧录 `<BITSTREAM_PATH>`（**必须在 ps7_init 之后**） |
| 6 | `ps_load_hardware` | 注册 PL 内存映射（loadhw `<XSA_PATH>`） |
| 7 | `ps_download_elf` | 下载 `<ELF_PATH>` 到内存 |
| 8 | `ps_run_target` | 启动 CPU 执行 |

**硬性顺序知识**：`fpga -f` 必须在 `ps7_init` 之后执行——`rst -system` 会把
PS-PL 接口置于阻塞状态，先烧录 FPGA 会导致后续对 PL 地址空间的访问进入 abort
handler。`loadhw` 必须在访问 PL 外设前执行——缺它，ARM 程序对 PL 地址空间的
读写会访问无效地址。

### 7c. UART 捕获

start（CPU 执行前）→ wait（markers 全部来自需求文档，`<PASS_MARKER>` /
`<FAIL_MARKER>`）→ stop（取完整文本）。marker 纪律与 `\x00` 清理见
[appendix_mechanics.md](../appendix_mechanics.md)「UART 捕获」。

### 7d. 收尾清理（硬性规则：保持目标运行态）

观测与判定完成后的收尾顺序：`ps_stop_uart_capture` → `ps_disconnect_hw_server`
→ `close_session`。**收尾阶段严禁调用 `ps_halt_target` / `ps_reset_target`**：
目标必须保持运行态离开——需求若要求持续效果（循环演示类），板载现象在断开
JTAG 后必须继续可见，供用户随时观察。经验教训：曾因收尾时停住目标，导致
板载效果冻结在最后写入状态，用户误判为功能失败；恢复方式为重新
`ps_run_target` 恢复执行（固件仍在内存中，无需重烧）。判定 PASS 不等于可以
停住目标——"效果持续可观察"是需求的观测语义，由 S8 判定表确认。

## 智能体自主决策范围

- 部署序列执行与观测配置（波特率、捕获窗口、marker/帧判定规则——marker 值
  必须来自需求文档，不臆造）。

## 用户必须提供的物理事实

- 无（UART 端口/波特率来自 S1 物理事实与需求文档）。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| JTAG 链只有 DAP 而无 ARM 核 | `ps_ensure_arm_accessible`（rst -system 恢复） |
| DAP 不响应 | 保存公开状态；按 `recommended_action` 调 `ps_recover_target` 或 `recover_execution` |
| download 失败 | 检查 ELF 编译、`ps_initialize_ps` 与 `ps_load_hardware` 是否完成 |
| UART 无输出 | 波特率偏差诊断（`ps_diagnose_uart_clock`）+ 确认 capture 先于 run + 确认程序未 crash |

## 涉及的工具类别

- ps JTAG command：`ps_connect_hw_server`、`ps_list_targets`、`ps_select_target`、
  `ps_halt_target`、`ps_reset_target`、`ps_initialize_ps`、`ps_load_hardware`、
  `ps_download_elf`、`ps_run_target`、`ps_ensure_arm_accessible`、
  `ps_list_serial_ports`、`ps_diagnose_uart_clock`；
- pl command：`pl_program_fpga`；
- UART capture command：`ps_start_uart_capture`、`ps_wait_uart_capture`、
  `ps_stop_uart_capture`。
