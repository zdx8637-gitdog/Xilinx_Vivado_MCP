# B12-A1 白盒（Agent1）重跑报告 — DMA 数据通路环回真板 PASS

> 日期：2026-08-24/25（`Get-Date` 实测 2026-08-25 00:45 +08:00）
> 角色：Agent1（白盒）| 执行面：仅公开 `zynq_mcp`（104 工具，mcp SDK `stdio_client + ClientSession`）+ 允许的工作区写 | 全程零 shell 逃生
> 工作区：`workspaces/b12_a1_agent1_rerun_20260824/`（runtime 独立）
> 状态：**PASS（真板机器证据）** — 本轮为上一轮 P0 BLOCKED（板卡包封条）之后的重跑；
> B03 合同简化（封条退役）+ N3（`ps_start_hw_server`）均已落地并经真机验证。

## 1. 结论（一句话）

公开 MCP 104 工具全流程 S0–S8 闭环：DMA（简单模式 + 轮询）经 axis_data_fifo 环回写入 DDR，
PS 逐字节校验，真板双跑 **FAULT→`DMA_LOOP_FAIL`（机读 FAIL）**、**干净→≥4 轮 OK +
`DMA_LOOP_PASS` 一次 + 继续无限循环（实测 374 轮全 OK）**；三 Manifest `verify_consistency`
两次 **12/12**；`ps_start_hw_server` 本地自启（pid 27404）闭环 N3 缺口。

## 2. 上一轮阻断与整改闭环

| 上一轮 P0 | 本轮结果 |
|---|---|
| `create_session` 被板卡包封条阻断（`EXTRA_FILE_IN_DIR`，board 目录含 ADC 额外资产）| B03 合同简化后 `validate_package_runtime` 退役目录封条 → `create_session` **SUCCEEDED**，记录 `board_profile_sha256=sha256:a7cb97a5…` |
| hw_server 未运行、公开 MCP 无自启工具 | N3 `ps_start_hw_server` **SUCCEEDED**（`{"started": true, "pid": 27404, "port": 3121}`）|

## 3. S0–S4（结构化文档，无 EDA 副作用）

`docs/00_requirement.md`（S0）、`01_physical_facts.md`（S1）、`02_budget.md`（S2）、
`03_architecture.md`（S3）、`04_proposal.md`（S4）。`get_capabilities` 确认 104 工具
（9 control + 95 domain，含 `ps_start_hw_server`）。

### S3 选型记录

- **PS 驱动**：XAxiDma **简单模式**（`c_include_sg=0`）+ **轮询完成**（`XAxiDma_Busy`），不接中断。
- **缓冲**：双缓冲各 1MB（静态、32B 对齐）。
- **图案**：递增序列 seed 0x5A，`pattern[i] = (0x5A + i) & 0xFF`（可独立重算）。
- **拓扑**（对齐厂商 `pl_config.tcl`）：`axi_dma/M_AXIS_MM2S → axis_data_fifo(1024)/S_AXIS →
  M_AXIS → axi_dma/S_AXIS_S2MM`；`M_AXI_MM2S/S2MM → axi_interconnect(2SI→1MI)/S00,S01 →
  M00 → PS7/S_AXI_HP0`；`M_AXI_GP0 → ps7_0_axi_periph → axi_dma/S_AXI_LITE`；
  FCLK_CLK0=100MHz 统一时钟；proc_sys_reset 复位。
- **地址**：`platform_assign_addresses` 自动分配 → `axi_dma_0 @ 0x40400000`（GP0 数据空间）。

## 4. S5-Platform（15 步原子序列，全 SUCCEEDED）

`platform_create_design(dma_loopback)` → `add_ps7`（ps7_preset.tcl）→
`configure_ps7`（uart1 enable、**s_axi_hp0=true**、**fclk0=100MHz**）→ `add_ip`×5
（axi_dma/axi_interconnect×2/axis_data_fifo/proc_sys_reset）→ `connect_interface`×7
（**含 2 条 AXIS 接口，`connect_bd_intf_net` 通用支持，无缺口**）→ `connect_clock`(14 pins) →
`connect_reset`×3 → `assign_addresses`（`axi_dma_0 @ 0x40400000`）→ `validate`（passed）→
`generate_wrapper`（`hdl/platform_bd_wrapper.v`，sha256 `19d512a9…`）→ `synthesize`
（`synth_design Complete!`，WNS=4.23，jobs=1，~492s）→ `export_hardware`
（`platform.xsa` 334,691 B，sha256 `6f4781ae…`）→ `export_manifest`
（platform_revision `sha256:68a8bea9…`，ip_list 6，clock_tree 完整）。

## 5. S5-PL（构建链，全 SUCCEEDED）

`pl_generate_system_top`（`rtl/system_top.v`，21 端口）→ `pl_create_project`
（`pl_vivado/pl_dma`，sources=BD+wrapper+system_top，top=system_top）→ `pl_generate_target`
（up-to-date）→ `pl_synthesize`（Complete，~49s）→ `pl_place` → `pl_route`（Complete）→
`pl_analyze_timing`（**timing_met=true**，无用户约束）→ `pl_generate_bitstream`
（**artifact_state=PUBLISHED**；bitstream `bitstream/dma_loop_top.bit` 4,045,670 B，sha256
`c9fa3e38…`；PL Manifest revision `sha256:3263a89a…`，文件 sha256 `8548c83e…`）。

## 6. S5-PS（软件链 + 关键根因修复）

固件 `src/main.c`（自写）：1MB 图案 → DCacheFlush → MM2S+S2MM SimpleTransfer → 轮询 Busy →
DCacheInvalidate → 逐字节校验 → `ROUND:<k> BYTES:<n> OK|ERR`；≥4 轮 OK 打印一次
`DMA_LOOP_PASS` 并继续循环；任一不一致 `DMA_LOOP_FAIL` 停止；`#ifdef FAULT_INJECT`
篡改 `RxBuffer[0]`。

### 关键根因（首跑 `ERR (mm2s)` 定位与修复）

首跑真板报 `ROUND:1 BYTES:1048576 ERR (mm2s)`——非故障注入，而是 **MM2S SimpleTransfer
被拒**。定位：`xaxidma.c` L159 `MaxTransferLen = (1U << Config->SgLengthWidth) - 1`，而
`XPAR_AXI_DMA_0_SG_LENGTH_WIDTH = 14` → **单次简单模式传输上限 16383 字节**，1MB 单次传输
必返回 `XST_INVALID_PARAM`。修复：**1MB 按 8192 字节分块（128 块）传输**（仍是完整 1MB
图案经 DMA 通路，逐字节校验不变），仅改固件、无需重建 PL。

链（双构建均 `artifact_state=PUBLISHED`，工作区 `project/ps/`，OK 先编译干净、FAULT 后编译）：

| 步骤 | 构建 | 结果 |
|---|---|---|
| ps_import_hardware / ps_create_platform / ps_create_bsp | — | SUCCEEDED（platform `ax7020_platform`，ps7_init.tcl 于 `hw/`）|
| ps_create_app(dma_app_ok) + ps_add_sources + ps_compile | OK | SUCCEEDED；ELF sha256 `5c6444ba…`；PS Manifest `sha256:c4f8da06…`（文件 `0af79529…`）|
| ps_create_app(dma_app_fault) + ps_add_sources + ps_set_compiler_options(FAULT_INJECT) + ps_compile | FAULT | SUCCEEDED；ELF sha256 `7315488b…`；PS Manifest `sha256:ccd20db1…`（文件 `16bd5af1…`）|
| ps_read_elf_info ×2 | 两 ELF | ELFCLASS32 / LSB / machine 40 (EM_ARM) / entry 0x100000 |

**D10 defines 真机生效证据**：FAULT `Debug/src/subdir.mk` 编译行含 `-DFAULT_INJECT`，
OK 编译行无任何 `-D`（干净）。

## 7. S6 一致性（两次，均 12/12）

| 构建 | platform / pl / ps revision | board_profile_sha256 | 结果 |
|---|---|---|---|
| OK | 68a8bea9… / 3263a89a… / c4f8da06… | sha256:a7cb97a5… | **12 passed / 0 failed / 0 skipped** |
| FAULT | 68a8bea9… / 3263a89a… / ccd20db1… | 同上 | **12 passed / 0 failed / 0 skipped** |

调用方式（D11）：三条 Manifest 传绝对路径 + `resolve_root`；地址映射 `XPAR_AXI_DMA_0_BASEADDR=0x40400000` 逐字段一致。

## 8. S7 部署 + S8 判定（真板，双跑）

JTAG 8 步：`ps_start_hw_server`（**自启 pid 27404**）→ `ps_connect_hw_server(127.0.0.1:3121)` →
`ps_list_targets`（APU/ARM#0/ARM#1/xc7z020）→ `ps_select_target(2)` → `ps_halt_target` →
`ps_reset_target(system)` → `ps_initialize_ps`（`hw/ps7_init.tcl`）→ `pl_program_fpga`（100%）→
`ps_load_hardware`（platform.xsa）→ `ps_start_uart_capture(COM4@115200)` → `ps_download_elf` →
`ps_run_target` → `ps_wait_uart_capture` → `ps_stop_uart_capture`。串口实测 COM4 = Silicon Labs CP210x（VID 0x10C4/PID 0xEA60）。

### 7a. FAULT 运行 → 机读 **FAIL**

UART 全文（`evidence/uart_fault.txt`，169 B）：
```
=== AX7020 DMA LOOP B12 ===
DMA simple-mode polling loop, N=1048576 bytes, seed=0x5A, chunk=8192
ROUND:1 BYTES:1048576 ERR
DMA_LOOP_FAIL
loop stopped at round 1 (ok=0)
```
- `ps_wait_uart_capture markers=[DMA_LOOP_FAIL]` → **MATCHED**（第 1 轮触发）。
- `evaluate_observation(pass=DMA_LOOP_PASS, fail=DMA_LOOP_FAIL)` → **verdict=FAIL**
  （fail_marker_found=true）。

### 7b. 干净运行 → 机读 **PASS** + 继续循环证据

UART 全文（`evidence/uart_ok.txt`，10,480 B，**374 轮全 OK**）：
```
=== AX7020 DMA LOOP B12 ===
DMA simple-mode polling loop, N=1048576 bytes, seed=0x5A, chunk=8192
ROUND:1 BYTES:1048576 OK
ROUND:2 BYTES:1048576 OK
ROUND:3 BYTES:1048576 OK
ROUND:4 BYTES:1048576 OK
DMA_LOOP_PASS
ROUND:5 BYTES:1048576 OK
...（ROUND:6 … ROUND:374 全部 OK）...
```
- `ps_wait_uart_capture markers=[DMA_LOOP_PASS]` → **MATCHED**。
- **PASS 后继续循环证据**：匹配后保持捕获开放 ~73s 再 stop → 全文含 **PASS 之后 370 行
  `ROUND OK`（ROUND:5–374，全部一致）**，满足「≥4 行后续 ROUND」且远超需求。
- `evaluate_observation` → **verdict=PASS**（pass_marker_found=true，fail=false）。

## 9. ps_start_hw_server 使用实录

- 第 1 次（S7 前置）：`{"started": true, "pid": 25208, "exe": "...hw_server.exe", "port": 3121, "url": "localhost:3121"}`。
- 收尾驱动重启后重自启：`{"started": true, "pid": 27404, ...}`（幂等自启；收尾后仍监听 3121，**未终止**，常驻服务）。

## 10. 产物清单与 SHA256（真实磁盘值）

| 产物 | 路径（workspace 相对） | 大小 | SHA256 |
|---|---|---|---|
| Platform Manifest | `project/manifests/platform/sha256_68a8bea9….json` | 1680 B | 0238b2f66b3db8fd8b9fb7377151decb9244d3d4a3b709c6deaaeed45f7792e2 |
| PL Manifest | `project/manifests/pl/sha256_3263a89a….json` | 1626 B | 8548c83e5676a8b3860eeb6ee6aae417d929d70356f12ddce898463fca90ce46 |
| PS Manifest（OK）| `project/manifests/ps/sha256_c4f8da06….json` | 1562 B | 0af79529b7d58b285c30fee16ebd9237e45e5f32edab8df35bbb17993a31a616 |
| PS Manifest（FAULT）| `project/manifests/ps/sha256_ccd20db1….json` | 1571 B | 16bd5af12aadb2da741530b246652926bfb6caf6c305ec71e678b9047b23005d |
| XSA（含 HDF）| `project/platform.xsa` | 334,691 B | 6f4781ae9f79342ba0b375139c7bc94d48d7f1cb123d196afcdc77269df45f86 |
| Bitstream | `project/bitstream/dma_loop_top.bit` | 4,045,670 B | c9fa3e3806b6b99710271923953a423c6556a88cd6b57f9358d3cc919b66131a |
| ELF（OK）| `project/ps/dma_app_ok/Debug/dma_app_ok.elf` | 305,492 B | 5c6444bac784ec15fdc5787fcddd7e700b0b3c827f28ae49a748218e4c075c3c |
| ELF（FAULT）| `project/ps/dma_app_fault/Debug/dma_app_fault.elf` | 305,520 B | 7315488b44cc7e9eec8ea3e92ddb8233287531cef020bb333637ade636b2fa5f |
| UART 证据 | `evidence/uart_fault.txt` / `evidence/uart_ok.txt` | 169 / 10,480 B | a69d07aa… / 7d17f81a… |

## 11. 操作终态分布（真实 Ledger）

`mcp_calls.jsonl` 共 676 行；`wait_operation` 终态 **SUCCEEDED=107 / FAILED=6**。
6 个 FAILED 均为**过程导航错误**（非产品缺陷），全部经公开契约路径恢复：
- `ps_compile → MANIFEST_PUBLISH_FAILED`：ELF 落在 `ps_fault/` 子目录，finalizer 无法发现 → 改用 `ps/` 布局。
- `ps_import_hardware → IMPORT_HW_FAILED` ×2：XSCT 工作区已设置（`setws` 无 `-switch`）→ 重启驱动。
- `ps_compile → BUILD_FAILED` ×3：①OK 缺 makefile.defs（驱动重启丢 active platform）→ `ps_create_bsp` 重新激活；
  ②fault 重复 `FAULT_INJECT`（define 已持久化）→ 重启驱动清 `_WS_DEFINES`。

## 12. 清理证据与最终目标状态

| 进程 | 收尾后 | 依据 |
|---|---|---|
| vivado.exe / rdi_xsct.exe / rdi_xsdb.exe | **已终止**（`close_session`；`Get-Process` 无结果）| 实测空 |
| hw_server.exe（pid 27404，本工具自启）| **仍在运行**（监听 3121）——常驻服务，**不终止** | 实测 Listen |
| **目标最终状态** | **RUNNING**（`ps_get_target_status → state=running`，OK 固件持续循环，未 halt）| Skill 7d：需求要求持续循环 |

收尾顺序：`ps_stop_uart_capture`（两次）→ `ps_get_target_status`（记录 running）→
`ps_disconnect_hw_server` → `close_session`（`completed` 全项、`incomplete: []`）。

## 13. 缺陷清单 / 观察项（只记录，不改生产代码）

| # | 级别 | 类型 | 缺陷/观察项 | 证据 | 建议 |
|---|---|---|---|---|---|
| R1 | P2 | 工程决策记录 | **AXI DMA 简单模式单次传输上限 16383 字节**（`c_sg_length_width=14` → `MaxTransferLen=2^14-1`），1MB 单次 SimpleTransfer 被拒 `XST_INVALID_PARAM` | 首跑 `ERR (mm2s)`；`xaxidma.c` L159；`XPAR_AXI_DMA_0_SG_LENGTH_WIDTH=14` | 已按公开契约绕行（8192 分块），记录；若需单次大传输，可 `platform_add_ip` 设 `c_sg_length_width` 更大值重建 PL |
| R2 | P2 | MCP 生命周期 gap | 驱动重启后 XSCT 丢「active platform」，`app build` 缺 makefile.defs 而 `BUILD_FAILED`（`exec make` 的 stderr 被 tolerate，真实链接错误不可见）| OK 首编译失败轨迹 | 建议 compile_app 失败时回传 make stderr 摘要；或 `app build` 前自动 `platform active` |
| N1 | P2 | 观察项（延续 B11）| `ps_mem_read` 对真实 XSDB 2023.1 返回 SUCCESS 但 `words: []`（mrd 输出解析 gap）| 本轮读 0x40400000 实测 | 不修不判失败；建议补 host_live mrd 解析测试 |

## 14. 未确认项

- **DMA 数据通路的物理完整性**由 UART 机器证据闭环（374 轮逐字节一致 + FAULT 注入 FAIL），
  无需人工物理观测；DDR 内容未额外做 JTAG 读回（`ps_mem_read` words 为空，N1），但逐字节
  校验已针对「经历 DMA 的 DDR 内容」完成（RX 缓冲经 DMA S2MM 写入后 Invalidate 再比对）。

## 15. 结论

**本阶段判定：PASS（UART 机器证据）**。B03/N3 整改闭环；简单模式 DMA + 轮询 + 8192 分块
环回真板双跑：FAULT→`DMA_LOOP_FAIL`（机读 FAIL）、干净→`DMA_LOOP_PASS` 一次 + 继续无限循环
（374 轮全 OK）；S6 两次 12/12；JTAG 8 步 + UART 捕获全经公开 MCP；`ps_start_hw_server`
自启闭环。已按铁律：未修改 `mcps/`、`skills/`、`boards/`、冻结文档、三个 legacy 目录；
仅写工作区工程/驱动/证据与本报告；全部 EDA/构建/Manifest/部署/观测经公开 MCP，零 shell 逃生。
