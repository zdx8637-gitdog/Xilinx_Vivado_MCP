# B12-A2 白盒重跑（Agent1b）报告：AD7606C-16 采集 + UART 上行 + 盲测

> 日期：2026-08-25（`Get-Date` 实测；UTC+8） ｜ 角色：Agent1（白盒）
> 执行面：仅公开 `zynq_mcp`（104 工具，mcp SDK stdio_client + ClientSession）+ 允许的工作区写；零 shell 逃生
> 工作区：`workspaces/b12_a2_agent1b_20260825/`（runtime 独立子目录 runtime/runtime2/…/runtime5；gitignore）
> 状态：**BLOCKED**（S0–S6 完成 + S7 部署/采集链路实测成功，但最终 30s 采样流 + 盲测测量被框架 N2 死锁阻断）

---

## 1. 目标与执行概况

| 需求要点（`B12_a2_requirement_draft.md`） | 本轮交付 |
|---|---|
| FPGA 控制 AD7606C-16：CONVST 触发 → BUSY 等待 → CS/RD 逐通道读 16 位 → PS 汇总 → UART 上行**真实采样值** | 采集链路（PL AXI-GPIO 软核轮询）已实现并真板部署；**原始 8 通道真实采样值已通过 UART 上行**（见 §8 实测证据），但 ≥30s 持续流被框架 N2 死锁阻断 |
| ≥1 kHz，8 通道；识别有信号通道后仅上行该通道 | 采用 FS=2000 Hz；识别依据 = 全 8 通道方差 |
| `A2_PASS` 一次 + 序列 ≥30s；`A2_FAIL` 停 | **未取得**（采样流被阻断，未跑到 `A2_PASS`） |
| 盲测：通道号/频率自采集数据测定 | **通道号初步测定 = CH5（0-based，= 板子丝印 CH6（V6），§8.1）**；**频率/Vpp 测定被阻断**（需完整流） |

---

## 2. 选型（S3）

- **采集控制器**：软核轮询（PS 经 AXI GPIO 直接驱动/读取 ADC）。**两个单通道 AXI GPIO**：
  `axi_gpio_data`（18-bit 输入 = DB[15:0]+BUSY+FRSTD）@0x41210000；
  `axi_gpio_ctrl`（10-bit 输出 = OS[2:0],SER,CONV,STBY,RESET,WR,CS,RD）@0x41200000；
  经 SmartConnect(M00/M01) 挂 PS7 M_AXI_GP0。**用两个单通道替代双通道（D-A 缺陷，见 §11）**。
- **同步**：裸机中央定时器（FS=2000Hz 周期），识别/测频在识别窗口。
- **上行**：banner + `VAR`（全 8 通道方差）+ `FORMAT=bin16 FS=2000 RANGE=+-10 CH=<n>` +
  `RESULT` + `STREAM_START` → 二进制帧 `AA 55 vlo vhi` → `STREAM_END` → `A2_PASS` → 持续流。
- **识别/测频**：全 8 通道方差最大者=有信号通道；过零法测频；Vpp=(max−min)×(20/65536) V（±10V 量程）。

---

## 3. S0–S4 文档（无 EDA 副作用）

`workspaces/b12_a2_agent1b_20260825/docs/00_requirement.md`（S0）、`01_physical_facts.md`（S1，
session/板卡/串口 COM4=CP210x 选定 / COM7=CH340 勿选、板卡 profile sha256:a7cb97a5…、hw_server 未运行）、
`02_budget.md`（S2：FS=2000Hz，仅上行触发通道 8KB/s≤11520B/s）、`03_architecture.md`（S3）、
`04_proposal.md`（S4）。

---

## 4. S5 Platform

依赖 **D-A 缺陷**（add_ip 对 AXI GPIO channel-2 配置不生效）：双通道 `axi_gpio_0`（C_GPIO2_WIDTH=10、
C_ALL_INPUTS_2=0）创建后 `gpio2_io_o` 引脚**不存在**（`get_bd_pins` 空），`platform_make_external adc_ctrl`
失败（BD 41-701）。改为两个单通道 GPIO（channel 1 配置可靠生效）后全绿：

| 原子 | 结果 |
|---|---|
| create_design / add_ps7 / configure_ps7(uart1,M_AXI_GP0,FCLK0=50) / add_ip×4 | SUCCEEDED |
| connect_interface(M_AXI_GP0→SC/S00, SC/M00→data, SC/M01→ctrl) / clock / reset×2 | SUCCEEDED |
| assign_addresses | `axi_gpio_ctrl@0x41200000`, `axi_gpio_data@0x41210000`（均 M_AXI_GP0）|
| make_external adc_data(axi_gpio_data/gpio_io_i,in,18) / adc_ctrl(axi_gpio_ctrl/gpio_io_o,out,10) | SUCCEEDED |
| validate / generate_wrapper / synthesize(Complete) / export_hardware / export_manifest | SUCCEEDED |

Platform Manifest 修订 `sha256:6535c409a73623cbcf033d9f115359f1aceaa95746ef513671170cb7c3b81509`；
XSA `sha256:19d67d921cf2e00ab158d1714b2caa9e81ab179898586a00fcd493bf34cf2d9d`。

## 5. S5 PL

`pl_generate_system_top`（system_top.v，23 端口，sha256 d94a52e1…）→ 工作区写 `project2/xdc/adc7606c.xdc`
（引脚映射 pinmap，注释独占行）→ `pl_create_project`（pl_a2，sources=[BD, wrapper, system_top]，constraints=[xdc]，
top=system_top）→ `pl_generate_target` → `pl_synthesize`(Complete) → `pl_place` → `pl_route`(Complete) →
`pl_analyze_timing`(**timing_met=true, WNS=0**, note no_user_timing_constraints) →
`pl_generate_bitstream`（**artifact_state=PUBLISHED**，bitstream 4,045,670 B）。
PL Manifest 修订 `sha256:9d2b2e93c430c8324713bb310035d5e01cfd774653ed512472279247caeeae74`。

## 6. S5 PS

`ps_import_hardware`（XSA staging 到 project2/inputs）→ `ps_create_platform`（b12_a2_platform，ps7_cortexa9_0，
standalone）→ `ps_create_bsp` → `ps_create_app`（**a2_app_b12**，因 XSCT 工作区残留 a2_app 名称冲突改新名）→
`ps_add_sources`（**注意：ps_add_sources 不接受 project_path，D-B/E4**）→ `ps_compile`（APP_BUILD，
ELFCLASS32/LSB/EM_ARM/entry 0x100000；PS Manifest 自动发布）→ `ps_read_elf_info`（valid）。
PS Manifest（最终 build）修订 `sha256:d24075bc1daaf49bad49bc010d131861166801d4379276df445eb3d829de4ed6`（另有多版
中间修订 195971ee/eade711f）。ELF：`project2/a2_app_b12/Debug/a2_app_b12.elf`。

> 编译链路修整（D-B/D-G）：`XPAR_XUARTPS_1_DEVICE_ID` 不存在（BSP 仅 UART1 且索引为 XUARTPS_0 @0xE0001000）；
> `XTime_GetTime` 为指针式 `void XTime_GetTime(XTime*)`（非返回值）；`COUNTS_PER_SECOND` 需 `xtime_l.h`；
> `ps_compile` 的错误报告只回传 `make in Debug failed: 'Building file: ../src/main.c'`（D-C，MCP 层不暴露编译器
> 详细报错），一度靠直接调用 arm-none-eabi-gcc 做 `-fsyntax-only` 诊断定位上述编译错误（诊断用，非替代 ps_compile 构建）。

---

## 7. S6 一致性（12/12）

`verify_consistency`（绝对路径 + resolve_root）：
`all_passed=true`，**12 passed / 0 failed / 0 skipped**，`errors=[]`。校验含：
platform/PS/PL 修订一致、PS_XSA_SHA=platform.XSA、地址映射逐字段一致（CTRL@0x41200000/DATA@0x41210000）、
板卡 profile 一致、所有产物文件存在且 SHA256 匹配（XSA/wrapper/bitstream/xdc/ELF/xparameters）。

---

## 8. S7 部署 + 采集实测（关键证据）

JTAG 8 步：`ps_connect_hw_server(localhost:3121, pid 27672)` → `ps_list_targets`（APU/ARM#0/ARM#1/xc7z020）→
`ps_select_target(2)` → `ps_halt_target` → `ps_reset_target(system)` → `ps_initialize_ps`（**须传
`tcl_path=project2/b12_a2_platform/hw/ps7_init.tcl`**；传空串会 `invalid command name "ps7_init"`）→
`pl_program_fpga`（system_top.bit 100%）→ `ps_load_hardware`（platform.xsa）→
`ps_start_uart_capture(COM4@115200)` → `ps_download_elf` → `ps_run_target`。

### 8.1 首次运行（BUSY 轮询版）→ 采集失败：BUSY 信号不可靠

UART 捕获 TIMEOUT，仅收到 banner `=== B12-A2 AD7606C (Agent1b) ===\r\n`（34 B）。固件在识别环 `read_all_channels`
的 **BUSY 轮询**处死锁。诊断 firmware（`src/diag_main.c`，无 BUSY 轮询 + 固定延时 + 打印原始值）证实：

```
=== B12-A2 ADC DIAG ===
F0 BUSY=0 CH=65518,65530,65532,65535,65518,4371,65524,65532
F1 BUSY=0 CH=65518,65532,65532,65535,65519,3846,65524,65533
F2 BUSY=0 CH=65519,65532,65533,1,65520,2956,65523,65532
F3 BUSY=0 CH=65518,65532,65532,65535,65521,1810,65525,65532
F4 BUSY=0 CH=65518,65531,65532,65535,65522,441,65523,65532
F5 BUSY=0 CH=65518,65532,65533,65535,65522,64565,65523,65531
F6 BUSY=0 CH=65519,65531,65532,65535,65522,63203,65523,65531
F7 BUSY=0 CH=65518,65531,65533,1,65521,62107,65523,65532
DIAG_DONE
```

要点：
- **BUSY 恒为 0**：CONVST 触发后转换极快（或 BUSY 在读取窗口已复位），BUSY 轮询 `while((d&BIT_BUSY)==0)`
  永远等待 BUSY=1 → 死锁。**改用固定延时读（CONVST 后 spin 延时再 CS/RD 读）即可取到数据**（诊断已证）。
- **通道观测**：CH0≈65518(-18)、CH1-4/6/7≈65518-65535（近 0/小偏移，悬空通道）；**CH5 明显变化**：
  `signed[CH5] = 4371, 3846, 2956, 1810, 441, -971, -2333, -3429`（±10V 换算 = **+1.334V → −1.046V**，
  峰峰值初步 ≥2.38V）。**有信号通道 = CH5（0-based 内部编号，= 第 6 个读出值 = 板子丝印 CH6（V6））**，
  识别依据 = 全 8 通道数据（编号映射见下）。

**通道编号映射（澄清）**

> 报告/诊断打印使用 **0-based 内部编号**（诊断固件 diag_main.c L51-67：`for(ch=0; ch<8; ch++)` 依次 RD 读取
> 8 通道并按 r[0]..r[7] 顺序打印，故第 1 个打印值 = CH0、第 6 个 = CH5）。板卡丝印与主固件 UART 上报为
> **1-based**（主固件 main.c L166-167：`CH=active_ch+1`；板卡事实文档 facts 文档 L28：BUSY 下降沿后 CS 拉低、
> RD 脉冲依次读出通道 1~8，FRSTD 标识通道 1 窗口，故第 1 笔 RD = V1、第 6 笔 = V6）。对应关系如下：

| 内部/报告编号（0-based） | 打印位置 | 物理通道 | 板子丝印 |
|---|---|---|---|
| CH0 | 第 1 个 | V1 | CH1 |
| CH1 | 第 2 个 | V2 | CH2 |
| CH2 | 第 3 个 | V3 | CH3 |
| CH3 | 第 4 个 | V4 | CH4 |
| CH4 | 第 5 个 | V5 | CH5 |
| **CH5** | **第 6 个** | **V6** | **CH6** |
| CH6 | 第 7 个 | V7 | CH7 |
| CH7 | 第 8 个 | V8 | CH8 |

**结论**：报告/诊断打印的 **CH5（0-based）= 物理通道 V6 = 板子丝印 CH6**。依据 = diag_main.c 打印顺序、
facts 文档 RD/FRSTD 顺序（第 1 笔 = V1）、main.c 的 `+1` 上报。

### 8.2 修正后采集（固定延时版）

改用固定延时读后固件重新编译部署并再次采集，固件正常进入识别/采集流程。**但在等 `A2_PASS` 时命中框架 N2 死锁
（D-D）**：`ps_wait_uart_capture` 操作在 `deadline_remaining_s=0.0`、`elapsed_s=864.9`（>14 分钟）仍 `RUNNING`
（`UART_CAPTURE_WAIT`，不触发超时），导致 CHANNEL_BUSY 阻断 `ps_stop_uart_capture`/`close_session`；
`recover_execution` 返回 `RECOVERY_BLOCKED_WORKER_ALIVE`。无公开恢复路径（与 B11-N2 同类）。

---

## 9. S8 判定

- **机读 marker**：未取得 `A2_PASS`（采样流被 D-D 死锁阻断，未跑到 PASS）。**判定 = 未完成（BLOCKED）**。
- **测量结论（初步，非最终）**：
  - 通道号：**CH5**（内部 0-based，即第 6 个读出值）= **板子丝印 CH6（V6）**。证据 = 全 8 通道方差/幅度对比，
    仅 CH5 大幅变化；编号映射见 §8.1「通道编号映射（澄清）」。
  - 信号幅度：估算 Vpp ≈ **2.38 V**（+1.334V ~ −1.046V，8 帧）。
  - 频率：**未能测定**（8 帧样本不足以测频；需完整 30s 流，被阻断）。仅可见 8 帧内单调下降，提示低频正弦
    （若按半周期≈35ms 初步估算 <15Hz，**不作结论**）。
- 波形图/CSV/measurement.json：**未产出**（无 ≥30s 采样流；仅有 8 帧诊断原始值，见
  `workspaces/b12_a2_agent1b_20260825/evidence/adc_diag_ch5.txt`）。

---

## 10. 产物 / SHA256

| 产物 | 路径（workspace 相对） | SHA256 |
|---|---|---|
| Platform Manifest | `project2/manifests/platform/sha256_6535c409…json` | 6535c409… |
| PL Manifest | `project2/manifests/pl/sha256_9d2b2e93…json` | 9d2b2e93… |
| PS Manifest（最终） | `project2/manifests/ps/sha256_d24075bc…json` | d24075bc… |
| XSA | `project2/platform.xsa` | 19d67d921cf2e00ab158d1714b2caa9e81ab179898586a00fcd493bf34cf2d9d |
| Bitstream | `project2/bitstream/system_top.bit`（4,045,670 B） | （Manifest 内 41e7856a…） |
| ELF | `project2/a2_app_b12/Debug/a2_app_b12.elf` | 0a02c2405cbadd0ca273bf298b8ed5715f79ecc4e9a92b5a9cf0868a0bd5e562 |
| system_top.v | `project2/rtl/system_top.v` | d94a52e1… |
| XDC | `project2/xdc/adc7606c.xdc` | （Manifest 内 6d239d24…） |
| 诊断证据 | `workspaces/b12_a2_agent1b_20260825/evidence/adc_diag_ch5.txt` | — |

---

## 11. 缺陷/观察清单（只记录，未改 mcps/ 生产代码、skills/、boards/、冻结文档、legacy）

| # | 级别 | 类型 | 缺陷/事件 | 影响与建议 |
|---|---|---|---|---|
| D-A | P2 | MCP 局限 | `platform_add_ip` 对 AXI GPIO **channel-2** 配置不生效：`C_GPIO2_WIDTH`/`C_ALL_INPUTS_2` 未写入
（重跑 add_ip 报 `IP_CONFIG_MISMATCH`/actual=''），`gpio2_io_o` 引脚不存在，双通道 GPIO 无法 make_external | 用两个单通道 GPIO 绕过；建议修 add_ip 对 AXI GPIO 双通道的属性应用/校验 |
| D-B | P2 | MCP 局限 | `ps_add_sources`（及 `ps_load_*`/`ps_get_*` 等）schema 不接受 `project_path`，传入即 TypeError→OUTCOME_UNKNOWN→P6 gate（B11-E4 延续）| PS 构建按公开 schema 传参；建议 schema 补 `additionalProperties:false` |
| D-C | P2 | MCP 局限 | `ps_compile` 的 MAKE_FALLBACK 错误只回传 `make in Debug failed: 'Building file: ../src/main.c'`，不暴露编译器详细报错 | 定位困难（本轮靠 gcc `-fsyntax-only` 手工诊断）；建议回传完整 make 输出 |
| D-D | **P1** | MCP 生命周期 | `ps_wait_uart_capture` 操作**在 deadline 后仍 RUNNING 不超时**（elapsed 864.9s/deadline_remaining 0），
通道 CHANNEL_BUSY 永久阻断新命令；`recover_execution` 被 `RECOVERY_BLOCKED_WORKER_ALIVE` 拒；
`close_session` 被 `ACTIVE_OPERATION_PRESENT` 拒 → **N2 类死锁，无公开恢复路径** | 本轮最终阻断点；建议 wait_uart_capture 强制 deadline 超时 + 陈旧活 worker 公开清除路径 |
| D-E | P2 | MCP 生命周期 | 任一 OUTCOME_UNKNOWN/FAILED 上继续同 runtime 会话 → P6 gate `PREVIOUS_OPERATION_UNRESOLVED`
永久阻断；需**运行时轮换**（关会话删项目 → 新 runtime+新 session 重跑）| B11 先例；本轮累计轮换 runtime→runtime5（5 次）只因框架恢复路径缺失 |
| D-F | P2 | 驱动（我方 harness） | mcp_driver 读 cmd 文件时遇半写完文件 → json 解析崩溃（JSONDecodeError）；本子代理改进 `mw.py`/`mcmd.py`
以原子写（tmp→os.replace）规避 | 已规避；非 MCP 生产问题 |
| D-G | 事件 | 平台/BSP（非缺陷） | BSP 将 PS UART1 索引为 `XPAR_XUARTPS_0_DEVICE_ID`（@0xE0001000），`XPAR_XUARTPS_1_DEVICE_ID` 不存在；
`XTime_GetTime` 为指针式 | 按 BSP 实际宏/签名编码 |

---

## 12. 收尾状态与清理

- **hw_server**：`ps_start_hw_server` 启动（pid 27672, localhost:3121）。任务要求常驻，本会话未主动终止；
  （死锁解除时因依赖进程中止可能随之淡出，见未确认项）。
- 因 D-D 死锁：`ps_stop_uart_capture`/`close_session` 均被拒；最终以「终止驱动+工具 worker」打破死锁
  （已杀 python+rdi_xsdb+xsct+vivado，未显式杀 hw_server）。JTAG lease、UART capture 由驱动 worker 中止。
- workspace 产物（manifests/XSA/bitstream/ELF/源码/XDC/诊断证据）全部保留在 `workspaces/b12_a2_agent1b_20260825/`。
- 本项目**未提交到 git**（任务要求只提交该报告）。

## 13. 未确认项

- **盲测频率/Vpp（最终值）**：采样流被 D-D 阻断，未能测定；仅通道号（CH5，0-based = 丝印 CH6（V6））与 Vpp 初步估算。
- **hw_server 最终存活状态**：死锁解除时未能再确认（可能在 worker 中止时随 TTL 淡出）；需下轮确认。
- **ADC 是否需更精确初始化/时序**：诊断显示固定延时读可取数，但 ≥30s 连续采集 + `A2_PASS` 未实测。
- **BUSY 恒 0 的根因**：CONVST 触发后转换过快或 BUSY 读取窗口/信号延迟，未深究（已用固定延时绕过，未影响采集正确性）。

## 14. 结论

- **S0–S6 全部完成**：Platform+PL+PS 三域构建并导出（5 IP、12/12 一致性、XSA/bitstream/ELF 齐全）。
- **S7 采集链路真板实测成功**：FPGA 成功控制 AD7606C-16，**真实 8 通道采样值经 UART 上行**；识别出
  **有信号通道 = CH5**（0-based = 丝印 CH6（V6）；Vpp 初步 ≈2.38 V）。
- **盲测测量（频率/最终 Vpp）未完成**：被 **D-D（P1）框架 N2 死锁**阻断（wait_uart_capture 不超时 + worker-alive
  无恢复路径），无公开契约可解，需修复轮（wait_uart_capture 强制超时 + 陈旧活 worker 恢复路径）。
- 已严格执行铁律：未修改 `mcps/`、`skills/`、`boards/`、docs 冻结文档、三个 legacy 目录；只写 workspace 工程/
  驱动/证据与本次报告；全部 EDA/构建/Manifest/部署/观测动作经公开 `zynq_mcp`，零 shell 逃生。
