# B12-A2 White-box Report (Agent1) — AD7606C-16 acquisition: Platform + PL complete, PS build P1 BLOCKED

> Date: 2026-08-25 | Role: Agent1 (white-box) | Exec surface: only public `zynq_mcp` (mcp SDK ClientSession)
> Status: **P1 BLOCKED** — FPGA control-train built to **bitstream with timing met**, firmware + analysis script complete,
> but the **PS bare-metal build is blocked by a framework deadlock** (N2-style) that has no public remedy.

Workspace: `workspaces/b12_a2_agent1_20260825/` (runtime2 was used for the 2nd build; evidence ledger `mcp_calls.jsonl`).

## 1. Target & scope
FPGA control of AD7606C-16 (凌智 V1.3 on AX7020 J11, ±10V, internal 2.5V) — CONVST-trigger, BUSY wait,
CS/RD 16-bit parallel read of 8 channels at ≥1kHz; real sample values over PS UART1 @115200, line/binary
format in a banner; ≥30s continuous; `A2_PASS`/`A2_FAIL`. Blind-test discipline: channel / frequency / amplitude
are NOT assumed; they are measured from captured data (channel by largest variance over all 8, freq by
zero-crossing + FFT, Vpp from min/max with ±10V LSB). No blind parameter is asserted anywhere.

## 2. 选型 (S3)
- **采集控制器**：软核轮询 + AXI GPIO（PL 放双通道 AXI GPIO：ch1 全输入 18bit = DB[15:0]+BUSY+FRSTD；
  ch2 全输出 10bit = OS[2:0],SER,CONV,STBY,RESET,WR,CS,RD）。零自定义 RTL，复用 B11 AXI-GPIO 链路。
- **PS↔PL 交接**：AXI-Lite 寄存器（`xgpio` 驱动，ch1 读 / ch2 写），@ 0x41200000。
- **采样率**：1000 Hz（软件用 XTime/`COUNTS_PER_SECOND` 精确门控 CONVST）。
- **识别**：8 通道方差对比取最大；**频率**：零交叉（主）+ FFT（复核）；**Vpp**：min/max ±10V 换算。
- **上行**：banner 声明 `FORMAT=bin16 FS=1000 RANGE=+-10 CH=<n>`；二进制帧 `AA 55 vlo vhi`；`A2_PASS`/`A2_FAIL`。

## 3. 每阶段证据

### S0–S4（无 EDA 副作用）
`docs/00_requirement.md`（S0）、`01_physical_facts.md`（S1）、`02_budget.md`（S2）、`03_architecture.md`（S3）、`04_proposal.md`（S4）。

### S5 Platform（全部 SUCCEEDED）
双通道 AXI GPIO（`C_IS_DUAL=1`, `C_GPIO_WIDTH=18`, `C_GPIO2_WIDTH=10`, `C_ALL_INPUTS=1`, `C_ALL_INPUTS_2=0`）
+ PS7(uart1) + proc_sys_reset + smartconnect；连线/时钟/复位全通过；`adc_data[17:0]`(in) + `adc_ctrl[9:0]`(out) 外部化；
**validate PASS**；**synthesize WNS=15.447**；XSA + Platform Manifest 发布。
- Platform Manifest: `project2/manifests/platform/sha256_b358c1fae6948e888cca9c8c1b653e79c6fdea099e558224ff243e3f6bbff42c.json`
- XSA: `project2/platform.xsa` (SHA256 `16c7952226fe492e6ee56dee0bcda0c39253f440e9bd4f83d87d5ec99a40c583`)

### S5 PL（全部 SUCCEEDED，timing_met=true）
`system_top`（对外 `adc_data`/`adc_ctrl`）→ `pl_create_project`(sources=BD+wrapper+system_top, constraints=xdc, top=system_top)
→ `pl_generate_target` → `pl_synthesize` → `pl_place` → `pl_route` → `pl_analyze_timing`（**timing_met=true, WNS=0**）
→ `pl_generate_bitstream`（**SUCCEEDED**）。PL Manifest 发布。
- Bitstream: `project2/bitstream/a2_top.bit` (4,045,670 B, SHA256 `38a4c12b1c21a66dfa6672115972e82a49df766359b00a27c289a57cc46c496a`)
- PL Manifest: `project2/manifests/pl/sha256_dbe9dab70219f20372c2cdeb06e65367ac841fd11ab6b11beb912130688b7316.json`

### S5 PS（BLOCKED）
- `ps_import_hardware` → SUCCEEDED.
- `ps_create_platform` → **`ACCEPTED`/`NOT_STARTED` stuck in `ADMISSION`** (>15 min), no `.spr`/workspace created on disk.
- Subsequent `ps_create_bsp`/`ps_create_app`/`ps_add_sources`/`ps_compile` all → `LOCK_BUSY`/`CHANNEL_BUSY`.
- `recover_execution` → `RECOVERY_BLOCKED_WORKER_ALIVE`（`rdi_xsct.exe` pid 5620 存活）.
- `close_session` → `ACTIVE_OPERATION_PRESENT`（拒绝）. `wait_operation` → 永远 ACCEPTED；admission deadline 过后仍 ACCEPTED.

## 4. BLOCKER 根因（框架缺陷，非设计失败）
这是与 B11 `N2` 同类的框架死锁：一个 command 被 admit 进 `ADMISSION` 后，worker（`rdi_xsct`）存活但 heartbeat STALE，
op 既不启动也不自动超时；`recover_execution` 因 `RECOVERY_BLOCKED_WORKER_ALIVE` 被拒（无公开工具能停掉该 worker）；
`close_session` 因 `ACTIVE_OPERATION_PRESENT` 被拒。B11 的处置是重启 harness/driver 并换新 runtime 子目录——非本 agent 所能。

## 5. 缺陷清单（只记录，未改生产代码）
| # | 级别 | 类型 | 证据 | 影响 |
|---|------|------|------|------|
| D1 | P1 | MCP gap | `ps_create_platform` op `op-9ba78ff116354e65b04c863278ce50e6` ACCEPTED/NOT_STARTED, `deadline_remaining_s=0`, `elapsed_s≈727`; `rdi_xsct.exe` pid 5620 alive; recover/close both blocked | 阻塞 PS 构建与 S6/S7；无公开处置 |
| D2 | P2 | MCP gap | PL 阶段无公开回退：`pl_analyze_timing` 推进到 `PL_BITSTREAM` 后 `pl_create_project`/`pl_synthesize` 被 `STAGE_PREREQUISITE_UNMET` 硬性门禁；`recover_execution` 不重置 stage（context.py `ROLLBACK_TARGETS[PL_BITSTREAM]=[PL_BUILD]` 从未公开为工具） | bitstream 失败后只能整会话重来 |
| D3 | P2 | 已修正（实测） | Vivado XDC 不把行内 `#` 当注释：`set_property ... # DB0` → `Common 17-161 Invalid option value '#' for 'objects'`（adc7606c.xdc:8）→ 端口未约束 → impl DRC `UCIO-1`（adc_data/adc_ctrl）→ write_bitstream 失败。修复：注释移到独立行 | 已修复，bitstream 已生成 |

## 6. 测量结论（未完成，因 PS 部署受阻）
无 `A2_PASS` 实测输出——PS 应用未编译、未部署、无 UART 捕获。所需脚本已就绪：
`analyze_waveform.py`（纯标准库：解析 UART→`uart_samples.csv`、`waveform.png`(zlib 自绘 PNG)、`measurement.json`）；
固件 `project2/src/main.c`（含 variance 通道识别、零交叉测频、`AA 55` 二进制流、`A2_PASS`/`A2_FAIL`）。
通道号/频率/峰峰值**必须**在 PS 部署并采集后由 `analyze_waveform.py` 从 UART 数据测定——当下无法给出（盲测纪律：不臆造）。

## 7. 收尾状态
- 目标最终状态：**未知**（进程未部署，原始目标状态无关）。
- 会话：`session-3017b7842cca467094bb67b55bf1c3e2` 仍因 `ACTIVE_OPERATION_PRESENT` 无法正常 close（框架死锁遗留）。
- 清理证据：4 次 `close_session` 尝试（2 次本会话 project 阶段成功、1 次 N2 前关闭成功、1 次因 ACTIVATION 拒绝）记录在 `mcp_calls.jsonl`。

## 8. 未确认项
- 盲测通道号 / 频率 / 峰峰值：**全部未测定**（因部署受阻；算法与脚本已备好）。
- PS 构建死锁的框架级根因：确认是 worker 存活且 stale、op 卡在 ADMISSION，需 harness 级处置。

## 9. 结论
- 已把 AD7606C-16 的 FPGA 控制链构建到 **bitstream（timing met，WNS=0）**，并写出 firmware + 纯标准库分析脚本；
  但 PS 构建被一个无公开处置的框架死锁（N2 类）**硬阻塞**，导致 S6/S7 与测量无法执行。
- 已按铁律：未修改 `mcps/`、`skills/`、`boards/`、docs 冻结文档、三个 legacy 目录；所有 EDA/构建/Manifest/部署动作经公开 MCP；分析脚本纯标准库。缺陷只记录不修改。
- 处置建议：需要 harness/driver 级恢复（清掉存活 stale 的 `rdi_xsct` worker 与卡住的 op），随后在同一 `project2` 继续 PS 构建 → S6 → S7 → 测量。
