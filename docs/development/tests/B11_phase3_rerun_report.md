# B11 阶段③ Agent1 白盒自测重跑报告（PASS — 6-LED 硬件全流程闭环）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 22:55 +08:00）
> 状态：**PASS** — 仅凭「新泛化 Skill（`skills/zynq_dev/`，零 GPIO 字样）+ 6-LED 需求文档
> （`docs/development/tests/B11_blackbox_requirement_draft.md`）+ 公开 `zynq_mcp`（103 工具）」
> 在真板完成 S0–S8 全流程：Platform 15 步原子序列 → PL 构建 → PS 软件 → S6 一致性 → S7 JTAG
> 部署 + UART 观测 → S8 机读判定；故障注入双跑（FAULT→FAIL、正确→PASS）均真板执行。
> 角色：Agent1（白盒）| 执行面：仅公开 `zynq_mcp`（103 工具，mcp SDK `stdio_client +
> ClientSession` 驱动）+ 允许的工作区写 | 全程零 shell 逃生（未直接运行 vivado/xsct/make，
> 未 import `mcps.zynq_mcp` 内部模块做操作）
> 工作区：`workspaces/b11_p3_agent1_rerun_20260814/`（runtime 独立于 `.zynq_runtime`；gitignore）
> 配套证据：`workspaces/b11_p3_agent1_rerun_20260814/mcp_calls.jsonl`（299 行完整机读轨迹）、
> `project/00..04_*.md`（S0–S4 文档）、`project_r3/`（最终产物：三 Manifest/XSA/bitstream/ELF）、
> `evidence/uart_fault.txt`、`evidence/uart_ok.txt`

## 1. 目标与执行概况

目标：验证 D1–D9 整改后，「泛化 Skill + 6-LED 考题 + 公开 103 工具」可在真板完成全流程
（上一轮白盒在 Platform 域即被 D1–D3 阻塞，见 `B11_phase3_whitebox_report.md`）。

执行结果：**S0–S4 完成；S5 Platform 15 步原子序列 20/20 SUCCEEDED（含 D1 assign、
D2 make_external、D3 synthesize、D5/D7/D8/D9 实证）；S5-PL 8 步全 SUCCEEDED（bitstream +
PL Manifest 自动发布）；S5-PS 编译成功（PS Manifest 自动发布 ×2：FAULT 与正确）；S6
`verify_consistency` 两次（FAULT 构建、正确构建）均 **all_passed=true, 12/12**；S7 两次真板
部署（8 步 JTAG + UART 捕获，先开窗再放跑）；S8 `evaluate_observation` 显式 marker 机读判定
**FAULT→FAIL、正确→PASS**。**

会话与操作统计（真实 Ledger 终态，mcp_calls.jsonl 解析）：

| 会话 | project 目录 | command 操作终态 | 说明 |
|---|---|---|---|
| session-02277465fcf0… | `project` | 24 SUCCEEDED / 2 FAILED | Platform 15 步 + PL 至 place；pl_place opt_design 崩溃（环境，见 §9）→ 按 S8③ 换新会话 |
| session-a793354c2023… | `project_r2` | 28 SUCCEEDED / 1 FAILED | Platform+PL 至 bitstream；write_bitstream `bad allocation`（环境，见 §9）→ 换新会话 |
| session-c3932d6b3fcb… | `project_r3` | **82 SUCCEEDED / 2 FAILED** | 全链闭环；2 FAILED 为我的后端切换用法错误（见 §9），纠正后全绿 |
| **合计** | — | **134 SUCCEEDED / 5 FAILED** | 终态分布；25+ 操作要求满足（R3 单会话即 82 个成功终态） |

总耗时：21:50:29 → 22:55（约 **1h05m**，含两轮环境性故障恢复重跑）。

## 2. S3 选型理由（AXI GPIO 主路线）

- **采用 AXI GPIO**（`xilinx.com:ip:axi_gpio:2.0`，4-bit，C_ALL_OUTPUTS=1）：与整改轮 D1
  host_live 真机链 1:1 同构（PS7 + axi_gpio + proc_sys_reset + smartconnect 四 IP），地址
  `0x41200000` 与 B09/B05 一致可对照；读回经 AXI GPIO DATA 寄存器直接满足需求 §3.2。
- **EMIO 路线**：D0 已补 `gpio: {emio_enable,width,io}` 键（能力确认可达），但无真机链背书，
  本轮不引入变量（详见 `project/03_architecture.md`）。
- 约束文件：仅取 `board.xdc` 的 led_pins 段（J16/K16/M15/M14 + LVCMOS33）；设计时钟全部来自
  PS7 FCLK_CLK0（50MHz），不接入 PL 振荡器。

## 3. S5-Platform 15 步原子序列（R3 会话，operation_id 全部实测）

| # | 原子 | operation_id | 终态 | 关键证据 |
|---|---|---|---|---|
| 1 | platform_create_design | op-bc61e51a819b… | SUCCEEDED | Vivado 2023.1 后端 PID 23016 启动 |
| 2 | platform_add_ps7 | op-b42e7fc30741… | SUCCEEDED | ps7_preset.tcl 应用 |
| 3 | platform_configure_ps7 | op-6bbf8dba2ed2… | SUCCEEDED | updated=[m_axi_gp0, fclk0_mhz, uart1_enable, uart1_io] |
| 4 | platform_add_ip (axi_gpio_led) | op-e79add0c2c92… | SUCCEEDED | C_GPIO_WIDTH=4,C_ALL_OUTPUTS=1 |
| 5 | platform_add_ip (rst_ps7_50M) | op-8ac9c097e6ee… | SUCCEEDED | proc_sys_reset:5.0 |
| 6 | platform_add_ip (smartconnect_0) | op-5f9d6e03ee7a… | SUCCEEDED | smartconnect:1.0, NUM_SI=1 |
| 7 | platform_connect_interface | op-0fb006984f8f… | SUCCEEDED | M_AXI_GP0→S00_AXI |
| 8 | platform_connect_interface | op-446f851592c3… | SUCCEEDED | M00_AXI→S_AXI |
| 9 | platform_connect_clock | op-8735cd77446f… | SUCCEEDED | FCLK_CLK0→4 目标 |
| 10 | platform_connect_clock | op-904b1575c45e… | SUCCEEDED | FCLK_RESET0_N→ext_reset_in |
| 11 | platform_connect_reset | op-7f33ba55a63d… | SUCCEEDED | peripheral_aresetn→s_axi_aresetn |
| 12 | platform_connect_reset | op-a7003545cabf… | SUCCEEDED | interconnect_aresetn→aresetn |
| 13 | **platform_assign_addresses（D1）** | op-0316bd44f167… | SUCCEEDED | **address_map 非空**：`axi_gpio_led @ 0x41200000 / 0x00010000 / master M_AXI_GP0` |
| 14 | platform_set_address（D5） | op-6917c31213d0… | SUCCEEDED | 短名 `axi_gpio_led/S_AXI` 解析，无 SEGMENT_NOT_FOUND |
| 15 | **platform_make_external（D2）** | op-c65bd6907def… | SUCCEEDED | 端口 `led_pins` out width 4；wrapper 含 `output [3:0]led_pins`（实测） |
| 16 | platform_validate（D7 -force） | op-0a6bf7eb2b2e… | SUCCEEDED | validation=passed |
| 17 | platform_generate_wrapper | op-0285a4ae05ee… | SUCCEEDED | `hdl/platform_bd_wrapper.v`（sha256:87f701f9…，含 led_pins 端口） |
| 18 | **platform_synthesize（D3）** | op-9ea46bc08fa4… | SUCCEEDED | `synth_design Complete!`，WNS=14.839，jobs=1，272.7s |
| 19 | platform_export_hardware | op-db170db2d57e… | SUCCEEDED | XSA=350,194 B（>1500B），**zip 12 条目含 hwdef.xml/platform_bd.hwh/ps7_init.c/h/tcl**（HDF 实证） |
| 20 | platform_export_manifest | op-3e87e2b1833f… | SUCCEEDED | **completion_evidence: PLATFORM_DESIGN→PL_GENERATE**；ip_list=4 IP（D8）；address_map 非空；clock_tree 完整路径 `processing_system7_0/FCLK_CLK0…`（D9） |

Platform Manifest（自动发布，status=locked）：文件 SHA256 `ba0bb5d5c45d…`，
`platform_revision = sha256:62834e13879c37bbc108c3709ab54d2a8c2cc691e5552c10f03c054101f0ea8f`；
`xsa_sha256 = sha256:9dab9aa3901268ab99db168946bc808807e995a4f51407e6a76b02ee43a7ff07`。

## 4. S5-PL 构建链（R3 会话）

| 顺序 | 工具 | 终态 | 证据 |
|---|---|---|---|
| 1 | pl_generate_system_top | SUCCEEDED | `rtl/system_top.v`（sha256:9db9f546…）例化 wrapper 并引出 `led_pins[3:0]`，22 端口；stage→PL_BUILD |
| 2 | （工作区写）`project_r3/xdc/led_pins.xdc` | — | board.xdc led_pins 段 |
| 3 | pl_create_project | SUCCEEDED | project `pl_led6`（BD+wrapper+system_top；top=system_top） |
| 4 | pl_generate_target | SUCCEEDED | BD 产物 up-to-date |
| 5 | pl_synthesize | SUCCEEDED | `synth_design Complete!`（60s） |
| 6 | pl_place | SUCCEEDED | place_design 完成（opt_design 本次正常） |
| 7 | pl_route | SUCCEEDED | `route_design Complete!`；stage→PL_TIMING |
| 8 | pl_analyze_timing | SUCCEEDED | **timing_met=true**（WNS=0；无用户时序约束）；stage→PL_BITSTREAM |
| 9 | pl_generate_bitstream | SUCCEEDED | **artifact_state=PUBLISHED**；bitstream `project_r3/bitstream/led6_top.bit`（4,045,670 B，sha256:44086862…）；**PL Manifest 自动发布**（文件 SHA256 `1af290c0d0cba…`，revision sha256:79b15db4…）；stage→PS_BUILD |

## 5. S5-PS 软件链（R3 会话）

| 顺序 | 工具 | 终态 | 证据 |
|---|---|---|---|
| 1 | ps_import_hardware | SUCCEEDED | XSA staging（same-file 幂等） |
| 2 | ps_create_platform | SUCCEEDED | `ax7020_platform`（standalone / ps7_cortexa9_0） |
| 3 | ps_create_bsp | SUCCEEDED | platform generate |
| 4 | ps_create_app | SUCCEEDED | `led6_app` |
| 5 | （工作区写）`project_r3/src/main.c`（正确版）与 `main_fault.c`（故障版） | — | 6-LED 逻辑：A=0x2A/B=0x15 交替、位序 [PL3 PL2 PL1 PL0 PS1 PS0]、active-low 直写、每轮 `WROTE:0x%X READ:0x%X`、8 轮（16 次模式写）、LED_E2E_PASS/FAIL |
| 6 | ps_add_sources | SUCCEEDED ×2 | main.c 拷入 `led6_app/src`（正确版/故障版各一次） |
| 7 | ps_compile（FAULT 版） | SUCCEEDED | **artifact_state=PUBLISHED**；PS Manifest revision sha256:9f793d34…（文件 SHA256 35ea1ba1…）；ELF sha256:e01f5445… |
| 8 | ps_compile（正确版） | SUCCEEDED | **artifact_state=PUBLISHED**；PS Manifest revision sha256:977c7888…（文件 SHA256 b7504a26…）；ELF sha256:a009c216… |
| 9 | ps_read_elf_info | SUCCEEDED | ELFCLASS32 / LSB / machine 40 (EM_ARM) / entry 0x100000 |

## 6. S6 一致性验证（两次，均 all_passed）

| 构建 | 三 Manifest（platform/pl/ps revision） | board_profile_sha256 | 结果 |
|---|---|---|---|
| FAULT | 62834e13… / 79b15db4… / 9f793d34… | sha256:a7cb97a5… | **all_passed=true；12 checks passed / 0 failed / 0 skipped** |
| 正确 | 62834e13… / 79b15db4… / 977c7888… | 同上 | **all_passed=true；12 checks passed / 0 failed / 0 skipped** |

7 条规则逐项实证：PL/PS `built_from_platform_revision == platform_revision` ✓；PS
`platform_xsa_sha256 == platform.xsa_sha256`（9dab9aa3…）✓；地址映射逐字段一致
（`XPAR_AXI_GPIO_LED_BASEADDR=0x41200000`）✓；PS/PL board_profile ✓；全部产物文件存在且
SHA256 匹配（XSA/wrapper/bitstream/xdc/ELF/xparameters.h）✓。

> 用法备注（D11）：`verify_consistency` 的三条 Manifest 路径必须传**绝对路径**（实现
> `_load_manifest` 直接用 `os.path.isfile(path)`，`resolve_root` 仅解析 Manifest 内部产物路径）。
> 按 Skill 模板的相对路径调用会全部 skipped（实测第一次调用即如此），本报告最终以绝对路径通过。

## 7. S7 部署 + S8 判定（真板，两次独立部署）

部署序列（每次 8 步 + UART）：`ps_connect_hw_server(127.0.0.1:3121)` → `ps_list_targets`
（APU/ARM#0/ARM#1/xc7z020）→ `ps_select_target(2)` → `ps_halt_target` → `ps_reset_target
(scope=system)` → `ps_initialize_ps`（`ax7020_platform/hw/ps7_init.tcl`）→ `pl_program_fpga`
（bitstream，100% 下载）→ `ps_load_hardware`（platform.xsa）→ **`ps_start_uart_capture`
（COM4@115200，先开窗）** → `ps_download_elf` → `ps_run_target` → `ps_wait_uart_capture` →
`ps_stop_uart_capture`。串口实证：`ps_list_serial_ports` → **COM4 = Silicon Labs CP210x
（VID 0x10C4 / PID 0xEA60）**，与 board profile usb_bridge 一致。

### 7a. 故障注入运行（FAULT 版 ELF，门禁先跑）

UART 全文（`evidence/uart_fault.txt`，60 字符，无 `\x00`）：
```
=== AX7020 LED B11 ===
WROTE:0x2A READ:0x2B
LED_E2E_FAIL
```
- `ps_wait_uart_capture` markers=[LED_E2E_FAIL] → **MATCHED**（capture_id uart-ef810fa98262；
  第 1 轮即触发，~1s）。
- `evaluate_observation`（显式 pass=`LED_E2E_PASS` / fail=`LED_E2E_FAIL`）→ **verdict=FAIL**
  （fail_marker_found=true, pass=false）。

### 7b. 正确运行（正确版 ELF，门禁后跑）

UART 全文（`evidence/uart_ok.txt`，390 字符，无 `\x00`；16 行全部写入==读回）：
```
=== AX7020 LED B11 ===
WROTE:0x2A READ:0x2A     (8× A)
WROTE:0x15 READ:0x15     (8× B)
LED_E2E_PASS
```
- 逐轮读回表（16 轮，全部一致）：

| 轮 | 模式 | WROTE | READ | 判定 | 轮 | 模式 | WROTE | READ | 判定 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | A | 0x2A | 0x2A | ✓ | 9 | A | 0x2A | 0x2A | ✓ |
| 2 | B | 0x15 | 0x15 | ✓ | 10 | B | 0x15 | 0x15 | ✓ |
| 3 | A | 0x2A | 0x2A | ✓ | 11 | A | 0x2A | 0x2A | ✓ |
| 4 | B | 0x15 | 0x15 | ✓ | 12 | B | 0x15 | 0x15 | ✓ |
| 5 | A | 0x2A | 0x2A | ✓ | 13 | A | 0x2A | 0x2A | ✓ |
| 6 | B | 0x15 | 0x15 | ✓ | 14 | B | 0x15 | 0x15 | ✓ |
| 7 | A | 0x2A | 0x2A | ✓ | 15 | A | 0x2A | 0x2A | ✓ |
| 8 | B | 0x15 | 0x15 | ✓ | 16 | B | 0x15 | 0x15 | ✓ |

- `ps_wait_uart_capture` markers=[LED_E2E_PASS] → **MATCHED**（capture_id uart-4f6a11f8c889；
  程序 ~11s 完成，60s 观测窗内 ✓——delay 经实测标定 100M 迭代≈1s/轮，见 §9 标定记录）。
- `evaluate_observation`（显式 marker）→ **verdict=PASS**（pass_marker_found=true, fail=false）。

## 8. D4 心跳修复回归观察（顺带验证，两次实测）

| 场景 | 实测 |
|---|---|
| 空闲 131s 后 `get_execution_state` | 正常返回（worker ABSENT——编译终态已清理 XSCT，属预期） |
| **存活 XSDB worker（PID 33356）+ 空闲 130s（心跳陈旧）后提交 `ps_disconnect_hw_server`** | **正常准入并 SUCCEEDED（无 WORKER_UNRESPONSIVE）** — D4 修复行为实测确认：进程存活+身份一致时，陈旧心跳不再阻断准入 |

## 9. 新缺陷 / 事件清单（只记录，不修生产代码）

| # | 级别 | 类型 | 缺陷/事件 | 证据 | 影响与建议 |
|---|---|---|---|---|---|
| **D10** | P1（门禁路径） | MCP 缺陷 | **`ps_set_compiler_options` 的 `defines` 从不传给 `ps_compile`**：`ps_bsp.py` 将 defines 存入模块级 `_WS_DEFINES`，但 `compile_app` 只调用 `templates.app_build(name)`（无 `-defines`），`_WS_DEFINES` 全程只写不读（grep 仅 3 处：声明/写入/删除）；`app_build_defines` 模板存在但从未被调用 | 设 defines=FAULT_INJECT 后编译，`led6_app/Debug/makefile` 等构建产物 grep `FAULT_INJECT` **0 命中**；上板输出全部 READ==WROTE（宏未生效） | 需求文档 §4 门禁的宏注入路径不可用。**本轮以「自写源码故障变体」（`main_fault.c` 无条件 `read6^=0x1`，允许的工作区写）交付同一故障语义并在真板完成 FAIL 双跑**；建议修复：`compile_app` 读取 `_WS_DEFINES[ws]` 并经 `app_build_defines` 传 `-defines`（并补组件测试） |
| D11 | P2 | Skill 文档 | `verify_consistency` 的 Manifest 路径语义与 Skill 模板不一致：模板用项目相对路径，实现要求绝对路径（相对路径 → 全部规则 skipped，`errors` 非空） | 第一次调用（相对路径）12 条 skipped + 3 条 NOT FOUND；绝对路径后 12/12 通过 | 建议在 appendix_mechanics §2.2 注明三条 Manifest 路径需绝对（或实现支持相对路径） |
| E1 | 事件 | 环境（非缺陷） | R1 `pl_place`：opt_design 批处理进程崩溃 `TclStackFree: incorrect freePtr`（runme.log 实证；当时系统仅 3.1GB 空闲、commit 37.2/40.8GB，用户游戏 nightreign 1.7GB 在运行；O7 R1–R3 均无此崩溃记录 → 瞬时环境问题） | impl_1 runme.log；vivado.log `__O3_STATUS=opt_design ERROR` | 按 S8 恢复：新会话重跑（R3 内存 6–9GB 时同链 opt_design/write_bitstream 全部通过） |
| E2 | 事件 | 环境（非缺陷） | R2 `pl_generate_bitstream`：write_bitstream `ERROR: [Designutils 20-1700] bad allocation`（运行日志实证；同样内存压力背景） | impl_1 runme.log | 同上；R3 内存好转后同一 bitstream 一步成功 |
| E3 | 事件 | 用法 | R3 故障构建时 `ps_add_sources`/`ps_compile` 报 `BACKEND_SWITCH_REQUIRES_IDLE`：JTAG lease 未释放时不可切换 XSDB→XSCT 后端（`tool_process_controller.ensure_backend` 要求 lane IDLE 且 `ps_disconnect_hw_server` 后后端清理） | 两次 FAILED 的 reason_code | 规范动作：部署后再构建前先 `ps_disconnect_hw_server`（Skill 未明示「构建↔部署来回」的后端释放要求，可作文档补充点） |

## 10. 产物清单与 SHA256（真实磁盘值，R3 最终）

| 产物 | 路径（workspace 相对） | 大小 | SHA256 |
|---|---|---|---|
| Platform Manifest | `project_r3/manifests/platform/sha256_62834e13….json` | 1381 B | `ba0bb5d5c45dd6450b0b6b851f8ef07b73a842252a312ece06216e48421ef11c` |
| PL Manifest | `project_r3/manifests/pl/sha256_79b15db4….json` | 1626 B | `1af290c0d0cba1b48ab42bf509c50120983c38f0f4c8604ba27da1a0a8b243a2` |
| PS Manifest（正确） | `project_r3/manifests/ps/sha256_977c7888….json` | 1550 B | `b7504a2679d45efc87cc2a52b7260bd6081df0b46f655f39fa023cf0ef71aa36` |
| PS Manifest（FAULT） | `project_r3/manifests/ps/sha256_9f793d34….json` | 1550 B | `35ea1ba1c5606a2947bc656dc3a0324c7bfa93dbe26efd9a8ed1af4ee957b934` |
| XSA（含 HDF） | `project_r3/platform.xsa` | 350,194 B | `9dab9aa3901268ab99db168946bc808807e995a4f51407e6a76b02ee43a7ff07` |
| Bitstream | `project_r3/bitstream/led6_top.bit` | 4,045,670 B | `440868624575e41bd00ba93add92e1f185686e284f123805d7e19e0df7ae9700` |
| ELF（正确） | `project_r3/led6_app/Debug/led6_app.elf` | 188,812 B | `a009c216cfc8989fc70e2355afd3887124ac5c09c00c18069c09348ee9cc3224` |
| ELF（FAULT） | 同上（构建间覆盖，以 manifest 记录为准） | — | `e01f54455ff62f5f709bd467d2ea2ffc5ebda6b98f8c6b58a1d0a68e6d2e5d84` |
| BD wrapper | `project_r3/hdl/platform_bd_wrapper.v` | 2,728 B | `87f701f955d807b830e097812b75bed8d89d8720ceee761bbcc3675e4d9b7c2a` |
| system_top.v | `project_r3/rtl/system_top.v` | 2,190 B | `9db9f54696ffae0f31e1e2c4b800575f03781caad1176ed31e703ea39be55472` |
| UART 证据 | `evidence/uart_fault.txt` / `evidence/uart_ok.txt` | 60 / 390 B | —（全文见 §7） |

**XSA 含 HDF 验证**：350,194 B（>1500B）；zip 解包 12 条目 = `hwdef.xml, platform_bd.bda,
platform_bd.hwh, platform_bd_smartconnect_0_0.hwh, ps7_init.c/h/html/tcl, ps7_init_gpl.c/h,
xsa.json, xsa.xml`。对比上轮 1.5KB `pre_synth` 空壳（D3 修复实证）。
**address_map 实测**：`axi_gpio_led @ 0x41200000 / 0x00010000 / master processing_system7_0/M_AXI_GP0`
（assign_addresses 与 Manifest、PS xparameters 三处一致）。
**wrapper 端口实测**：`output [3:0]led_pins`（+DDR_*/FIXED_IO_*），system_top 直通。

## 11. 清理证据（PID 前后核对）

| 进程 | 本阶段前 | 本阶段中 | 本阶段后 |
|---|---|---|---|
| vivado.exe（MCP 启动，R3 PID 23092） | 无 | 运行（Platform/PL 域） | **已终止**（close_session；Get-Process 无结果） |
| rdi_xsct.exe（R3 PID 20268 等） | 无 | 运行（PS 构建域；每次 ps_compile 终态自动清理） | **已终止** |
| rdi_xsdb.exe（R3 PID 4748→33356→572） | 无 | 运行（JTAG 域；每次 ps_disconnect 自动清理） | **已终止** |
| hw_server.exe（PID 19880，2026-08-09 启动） | 存在（早于本阶段） | 仅连接复用（127.0.0.1:3121） | **仍在运行（不在清理范围，与 B09 记录一致）** |
| python（驱动 23792 + server 20932） | 无 | 运行 | **已退出**（quit 指令，exit 0） |

## 12. 未确认项

- **LED 物理亮灭现象**：UART 读回全对 + marker 判定 PASS 已证明电气/地址/回读链路正确，但 LED
  实际点亮模式需用户/录像按需求 §3.1 确认 → 留待阶段⑤。
- UART 波特率偏差：`ps_diagnose_uart_clock` 未调用（115200 捕获正常、无乱码，未触发诊断条件）；
  如需可补测。

## 13. 结论

- **本阶段判定：PASS**。D1–D9 修复在真板全流程实证通过（Platform 15 步含 assign/make_external/
  synthesize、XSA 含 HDF、三 Manifest 一致性 12/12、JTAG 8 步部署、UART 16 轮读回全对、
  `LED_E2E_PASS` 机读 PASS）；故障注入双跑真板执行（`LED_E2E_FAIL` → verdict FAIL → 正确构建 →
  verdict PASS）。
- 新发现 1 个 P1 门禁路径缺陷（**D10**：defines 未传入 ps_compile）与 1 个 P2 文档缺陷（D11），
  按铁律只记录不修改生产代码；D10 门禁以「自写源码故障变体」等价交付并如实标注。
- 环境性事件 E1/E2（内存压力致 opt_design/write_bitstream 崩溃）与用法事件 E3 均如实记录；
  恢复路径全部在公开契约内（S8 恢复阶梯：新会话 + 新 PROJECT_PATH）。
- 已按铁律：未修改 `mcps/`、`skills/`、`boards/`、docs 冻结文档、三个 legacy 目录；只写了
  workspace 工程/驱动/证据与本文档；全部 EDA/构建/Manifest/部署/观测动作经公开 MCP，零 shell
  逃生。
