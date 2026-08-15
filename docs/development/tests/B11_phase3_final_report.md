# B11 阶段③白盒自测第三次运行（重做）最终报告 — 6-LED 硬件全流程 PASS + 持续 1s 交替 + 物理状态读回

> 日期：2026-08-15（`Get-Date` 实测 2026-08-15 ~10:50 +08:00）
> 状态：**PASS（UART 机器证据）** — 按**更新后的需求文档**（`B11_blackbox_requirement_draft.md`
> §3.1 持续循环 / §3.2 DATA_RO 真实状态读回 / §3.3 PASS 后不退出）与**更新后的 Skill**
> （`skills/zynq_dev/` §5.1 PS 输出引脚驱动要点）重新实现固件并在真板完成双跑闭环：
> FAULT 构建 → `LED_E2E_FAIL`（机读 FAIL）；正确构建 → `LED_E2E_PASS` + **PASS 后继续无限
> 1s 交替 ≥28 行 WROTE/READ 证据**。6 灯（含 PS 2 灯）物理亮灭留待用户阶段⑤确认。
> 角色：Agent1（白盒）| 执行面：仅公开 `zynq_mcp`（103 工具，mcp SDK `stdio_client +
> ClientSession`）+ 允许的工作区写 | 全程零 shell 逃生
> 工作区：`workspaces/b11_p3_agent1_final_20260814/`（runtime 独立；gitignore）
> 配套证据：`mcp_calls_phase1.jsonl`（243 行，S0–S6 前半 + 事故记录）、`mcp_calls.jsonl`
> （103 行，最终 PS/部署/判定阶段）、`docs/00..04_*.md`（S0–S4）、`evidence/uart_fault.txt`、
> `evidence/uart_ok.txt`、`REPORT.md`

## 1. 目标与执行概况

| 需求要点（更新版） | 本轮交付 |
|---|---|
| §3.1 满 8 轮全对打印一次 `LED_E2E_PASS`，**随后继续无限交替**（约 1s/模式）直到复位/断电 | **PASS 后捕获到 28 行 WROTE/READ（14 轮 A/B 交替 ≈ 28s）**——循环未停 ✓ |
| §3.2 读回必须来自引脚真实状态寄存器（PS 端 **DATA_RO 0x060**，禁读写镜像） | 固件读 `0xE000A060`（DATA_RO）；UART 读回值与驱动电平逐位一致（若 OUTEN 未使能，引脚上拉为高 → DATA_RO=1 → 必 FAIL，实测 PASS ⇒ 引脚真实被驱动）✓ |
| §3.2 active-low 语义、位序 `[PL3 PL2 PL1 PL0 PS1 PS0]`、`WROTE:0x%X READ:0x%X` | 固件直写 active-low；0x2A/0x15 交替；格式固定 ✓ |
| §3.3 任一不一致 `LED_E2E_FAIL` 并停止 | FAULT 注入双跑真板：第 1 轮 `READ:0x2B` → `LED_E2E_FAIL` → 停止 ✓ |
| §5 自检机读判定 | `evaluate_observation` 显式 marker：FAULT→**FAIL**、正确→**PASS** ✓ |
| D10 defines 新机制（`ps_set_compiler_options` + `app config define-compiler-symbols`） | **真机验证生效**：FAULT app makefile 含 `-DFAULT_INJECT`，且真板第 1 轮即 FAIL（上一轮宏未达编译器的缺陷已闭环）✓ |
| D11 一致性路径（绝对路径 / resolve_root） | `verify_consistency` 绝对路径 + `resolve_root` → 两次 **12/12** ✓ |

选型（S3）：**AXI GPIO 主路线**（`axi_gpio_led` 4-bit @ 0x41200000，与 B09/整改轮/前两轮一致可对照）；
PS 侧 MIO0/MIO13 输出（DIRM+OUTEN 显式配置、写 DATA 0x040、读 DATA_RO 0x060）。

## 2. S0–S4 文档化（无 EDA 副作用）

`docs/00_requirement.md`（S0 结构化需求，§3.1–§3.3 更新版要点）、`01_physical_facts.md`（S1，
board profile / board.xdc / ps7_preset / BSP 驱动寄存器事实 / hw_server PID 19880 / COM4=CP210x）、
`02_budget.md`（S2）、`03_architecture.md`（S3 AXI GPIO 选型 + 拓扑 + 地址 + R1/R2/R3 修复要点）、
`04_proposal.md`（S4）。`get_capabilities` 确认 103 工具（9 control + 94 domain）。

## 3. S5-Platform（15 步原子序列，全 SUCCEEDED）

| # | 原子 | 终态 | 关键证据 |
|---|---|---|---|
| 1–12 | create_design / add_ps7 / configure_ps7 / add_ip(axi_gpio_led, rst_ps7_50M, smartconnect_0) / connect_interface ×2 / connect_clock ×2 / connect_reset ×2 | 全部 SUCCEEDED | 4 IP：PS7+axi_gpio+proc_sys_reset+smartconnect；FCLK_CLK0=50MHz |
| 13 | **platform_assign_addresses**（D1） | SUCCEEDED | `address_map` 非空：`axi_gpio_led @ 0x41200000 / 0x00010000 / master M_AXI_GP0` |
| 14 | platform_set_address（短名 `axi_gpio_led/S_AXI`，D5） | SUCCEEDED | 无 SEGMENT_NOT_FOUND |
| 15 | **platform_make_external**（D2） | SUCCEEDED | `led_pins` out width 4；wrapper 含 `output [3:0]led_pins`（实测） |
| 16 | platform_validate（-force，D7） | SUCCEEDED | validation passed |
| 17 | platform_generate_wrapper | SUCCEEDED | `hdl/platform_bd_wrapper.v`（sha256 2602ebdd…） |
| 18 | **platform_synthesize**（D3） | SUCCEEDED | `synth_design Complete!`，WNS=14.839，jobs=1，262.7s |
| 19 | platform_export_hardware | SUCCEEDED | XSA 350,194 B，zip 12 条目含 hwh/hwdef/ps7_init（**HDF 实证**） |
| 20 | platform_export_manifest | SUCCEEDED | platform_revision `sha256:395c79b1…`，ip_list 4，address_map 非空，clock_tree 完整路径（D8/D9） |

Platform Manifest：文件 SHA256 `2d2ceae48d9f0233c37ddb74ac93cd63aab920fad78ddb02c1e13f2dfa7ffc3b`。

## 4. S5-PL（构建链，全 SUCCEEDED）

`pl_generate_system_top`（system_top.v 22 端口，sha256 9db9f546… 与前轮一致=确定性）→
工作区写 `xdc/led_pins.xdc`（board.xdc led_pins 段）→ `pl_create_project`（pl_vivado/pl_led6，
sources=BD+wrapper+system_top，top=system_top）→ `pl_generate_target` → `pl_synthesize` →
`pl_place`（place_design 完成）→ `pl_route`（route_design Complete!）→ `pl_analyze_timing`
（**timing_met=true**，WNS=0）→ `pl_generate_bitstream`（**artifact_state=PUBLISHED**；
bitstream 4,045,670 B，sha256 12992210…；PL Manifest 自动发布，文件 SHA256 `8aa45b29…`，
revision `sha256:982e83eb…`）。

## 5. S5-PS（软件链，按更新需求实现）

固件 `project/src/main.c`（自写，参考实现 `main_r3p2_fixed.c` 的 R1/R2/R3 修复语义，独立成文）：

- **PS 端（§5.1 顺序：方向→使能→数据→读回）**：`DIRM(0x204) |= MIO_MASK`（1=输出方向）；
  `OUTEN(0x208) |= MIO_MASK`（**1=输出使能**，R1 修复——上一轮 `& ~MASK` 清位致高阻不亮）；
  写 `DATA(0x040)`（R2 修复，不用 0x000 DATA_LSW 掩码写）；读 `DATA_RO(0x060)`（R3 修复，
  引脚真实状态，禁读写镜像）。MIO_MASK = bit0(MIO0/PS0) | bit13(MIO13/PS1)。
- PL 端：AXI GPIO TRI=0（全输出），写 DATA（0x41200000）。
- 模式 A=0x2A（点亮 PL2/PL0/PS0）/ B=0x15（点亮 PL3/PL1/PS1），`for(;;)` 无限交替，
  每模式 `delay_round()` ≈1s；每轮 `WROTE:0x%X READ:0x%X`（大写 hex、全 6 位）。
- 满 8 轮（A→B 计 1 轮）全对 → 打印一次 `LED_E2E_PASS`，**继续无限循环**；任一不一致 →
  `LED_E2E_FAIL` 并停机。
- `#ifdef FAULT_INJECT`：`read6 ^= 0x1`（故障注入，经 D10 修复后的 defines 机制注入）。

链（双构建均 `artifact_state=PUBLISHED`）：

| 步骤 | 构建 | 结果 |
|---|---|---|
| ps_import_hardware / ps_create_platform / ps_create_bsp | — | SUCCEEDED（XSA staging 幂等） |
| ps_create_app（led6_app_fault / led6_app_ok）+ ps_add_sources | 两 app | SUCCEEDED |
| ps_set_compiler_options(defines=**FAULT_INJECT**) + ps_compile | **FAULT** | SUCCEEDED + PS Manifest 发布（`sha256:12951e04…`） |
| ps_compile（无 defines，**干净构建**） | **OK** | SUCCEEDED + PS Manifest 发布（`sha256:4c846d5b…`） |
| ps_read_elf_info ×2 | 两 ELF | ELFCLASS32 / LSB / machine 40 (EM_ARM) / entry 0x100000 |

**D10 机制真机验证**（上一轮 P1 缺陷已闭环）：FAULT app `Debug/src/subdir.mk` 编译行含
`-DFAULT_INJECT`（`app config -add define-compiler-symbols` 真实写入构建配置并参与编译）；
OK app 编译行**无任何 defines**。真板行为佐证：FAULT ELF 第 1 轮 `READ:0x2B` → FAIL。

## 6. S6 一致性（两次，均 12/12）

| 构建 | 三 Manifest（platform/pl/ps） | board_profile_sha256 | 结果 |
|---|---|---|---|
| FAULT | 395c79b1… / 982e83eb… / 12951e04… | sha256:a7cb97a5… | **all_passed=true；12 passed / 0 failed / 0 skipped** |
| OK | 395c79b1… / 982e83eb… / 4c846d5b… | 同上 | **all_passed=true；12 passed / 0 failed / 0 skipped** |

调用方式（D11）：三条 Manifest 传**绝对路径** + `resolve_root=<project>`；无 skipped。

## 7. S7 部署 + S8 判定（真板，两次独立部署）

部署序列（每次 8 步 + UART）：`ps_connect_hw_server(127.0.0.1:3121)` → `ps_list_targets`
（APU/ARM#0/ARM#1/xc7z020）→ `ps_select_target(2)` → `ps_halt_target` → `ps_reset_target(system)`
→ `ps_initialize_ps`（ps7_init.tcl）→ `pl_program_fpga`（bitstream 100%）→ `ps_load_hardware`
（platform.xsa）→ **`ps_start_uart_capture`（COM4@115200，先开窗）** → `ps_download_elf` →
`ps_run_target` → `ps_wait_uart_capture` → `ps_stop_uart_capture`。串口实证：
`ps_list_serial_ports` → COM4 = Silicon Labs CP210x（VID 0x10C4 / PID 0xEA60），与 board
profile usb_bridge 一致。

### 7a. 故障注入运行（FAULT ELF，门禁先跑）→ verdict **FAIL**

UART 全文（`evidence/uart_fault.txt`，60 B，无 `\x00`）：
```
=== AX7020 LED B11 ===
WROTE:0x2A READ:0x2B
LED_E2E_FAIL
```
- `ps_wait_uart_capture` markers=[LED_E2E_FAIL] → **MATCHED**（第 1 轮即触发）。
- `evaluate_observation`（pass=LED_E2E_PASS / fail=LED_E2E_FAIL）→ **verdict=FAIL**
  （fail_marker_found=true，pass=false）。
- **D10 闭环实证**：defines=FAULT_INJECT 经新机制真实进入编译并上板生效（上一轮宏未达编译器）。

### 7b. 正确运行（OK ELF，门禁后跑）→ verdict **PASS** + 继续循环证据

UART 全文（`evidence/uart_ok.txt`，1006 B，无 `\x00`；**44 行 WROTE/READ = 16 轮前 + 28 轮后**）：
```
=== AX7020 LED B11 ===
WROTE:0x2A READ:0x2A     (16 行：8× A + 8× B，8 轮全部写入==读回)
WROTE:0x15 READ:0x15
...（16 行全对）...
LED_E2E_PASS
WROTE:0x2A READ:0x2A     (28 行：PASS 之后继续 A/B 交替 14 轮 ≈ 28s)
WROTE:0x15 READ:0x15
...（PASS 后 28 行，全部写入==读回）...
```
- 16 轮逐位核对：全部 `WROTE==READ`（0x2A/0x15），0 不一致。
- `ps_wait_uart_capture` markers=[LED_E2E_PASS] → **MATCHED**。
- **PASS 后继续循环证据**：匹配后保持捕获开放 25s 再 halt/stop → 全文含 PASS 之后的
  **28 行 `WROTE/READ`（14 个 A/B 交替 ≈ 28s）**，全部一致 → 需求 §3.1「打印一次 PASS 后
  继续无限交替」机器实证（要求 ≥4 轮，实际 14 轮）。
- `evaluate_observation` → **verdict=PASS**（pass_marker_found=true，fail=false）。

## 8. DATA_RO 读回事实陈述（PS 引脚真实性）

1. **固件代码保证**：唯一读回路径为 `Xil_In32(0xE000A000 + 0x060)`（`MIO_DATA_RO=0x060`），
   即 PS GPIO **DATA_RO（只读输入状态寄存器，反映引脚物理电平）**；写入路径为
   `0xE000A000 + 0x040`（DATA，RW）与 `0x204/0x208`（DIRM/OUTEN）。**从未读 0x000 写镜像**。
2. **寄存器事实来源**：板卡实际 BSP 驱动 `gpiops_v3_11`（`xgpiops_hw.h` L50–55：DATA=0x040 RW、
   DATA_RO=0x060 RO；`xgpiops.c` `XGpioPs_ReadPin` L248–250 读 DATA_RO）——与 Zynq-7000 TRM
   UG585 一致（来源详见 `B11_phase3_2_fix_report.md` §3.1-B）。
3. **机器可判佐证（UART 数据本身）**：若 OUTEN 被清（R1 病态），MIO0/MIO13 高阻、板卡上拉
   使能 → DATA_RO 位 13/0 = 1 → 模式 A（PS 位=00）读回必然 ≠0x2A → `LED_E2E_FAIL`。实测
   正确构建 44 轮 `READ==WROTE`（含 PS 位 0/1 逐位一致）⇒ OUTEN=1、DATA 正确驱动、DATA_RO
   反映引脚真实电平 ⇒ **PS LED 在点亮模式下被真实驱动为低（active-low 亮）**。
4. `ps_mem_read` 对 `0xE000A204/0xE000A208/0xE000A040/0xE000A060/0xE000A000` 仍返回
   `words: []`（SUCCESS 无可解析字）——**观察项 N1 延续**（mrd 输出解析 gap，不修不判失败；
   根因结论以驱动源码为准）。

## 9. 新缺陷 / 观察项清单（只记录，不改生产代码）

| # | 级别 | 类型 | 缺陷/事件 | 证据 | 影响与建议 |
|---|---|---|---|---|---|
| N2 | P2 | MCP 生命周期 gap | **OUTCOME_UNKNOWN 前序 op 死锁**：某 op 终态 OUTCOME_UNKNOWN（本次为 `ps_add_sources` 收到未声明的 `project_path` kwarg → 域函数 TypeError）后通道进入 RECOVERY_REQUIRED；`recover_execution` 在 XSCT worker 存活时拒（RECOVERY_BLOCKED_WORKER_ALIVE）；`close_session`+`create_session` 将 lane 复位为 IDLE 但**不清理前序 op**，P6 门禁（`PREVIOUS_OPERATION_UNRESOLVED`）永久阻断新命令；`recovery_mutator` 对 IDLE lane 直接短路（`ALREADY_IDLE`）不标记 resolved_by_recovery | `mcp_calls_phase1.jsonl`（diagnose/recover/close/create 轨迹）；`runtime/execution_ledger.json` 保留死锁现场 | 无公开工具可解。本轮恢复动作：重启自有 MCP 驱动（harness）并换用新 runtime 子目录（`runtime3`），旧 runtime 保留为证据；证据 ledger 轮转为 `mcp_calls_phase1.jsonl`。建议：recovery_mutator 在 IDLE+未解析前序 op 时也标记 resolved_by_recovery（或 close/create 时清前序）；ps_* schema 补 `additionalProperties:false` 使未知参数在 SDK 层即被拒（避免域函数 TypeError） |
| N1 | P2 | 观察项（延续） | `ps_mem_read` 对真实 XSDB 2023.1 返回 SUCCESS 但 `words: []`（mrd 输出格式与 `_parse_mrd_words` 期望不匹配；DDR 对照同样为空） | 本轮 5 个地址实测（0xE000A204/208/040/060/000） | 与 phase3.2 结论一致；不修（不影响本阶段判定）；建议补 host_live mrd 解析测试或改 ps_reg_read 路径 |
| E4 | 事件 | 用法（非缺陷） | `ps_add_sources`/`ps_compile` 的 XSCT 当前工作区由最近一次带 `project_path` 的 ps_* 调用决定（本会话曾因指向 project_ok 导致 ELF 越界校验 `ELF_VERIFY_FAILED`；ps_compile 终态 finalizer 强制 ELF 在 session project_path 内） | 两次 `ELF_VERIFY_FAILED` op 记录 | 规范动作：PS 构建始终在同一 session project_path 内进行；跨目录切换需先 ps_import_hardware(project_path=目标) |
| E5 | 事件 | 环境（非缺陷） | 恢复期间驱动重启曾重放 mcp_in 旧 cmd 文件（processed 集为空），约 30s 后被我中止；已归档 `mcp_archive_20260815_104034/` 并换新 runtime 重启；S6 12/12 证明产物无损伤 | 归档目录；两次 verify_consistency 全过 | 驱动重启前清空/归档 mcp_in（harness 操作习惯） |

## 10. 产物清单与 SHA256（真实磁盘值）

| 产物 | 路径（workspace 相对） | 大小 | SHA256 |
|---|---|---|---|
| Platform Manifest | `project/manifests/platform/sha256_395c79b1….json` | 1381 B | 2d2ceae48d9f0233c37ddb74ac93cd63aab920fad78ddb02c1e13f2dfa7ffc3b |
| PL Manifest | `project/manifests/pl/sha256_982e83eb….json` | 1626 B | 8aa45b29f129a1793eed3db331d38185539787a1c34be4a5643837a1489249a2 |
| PS Manifest（FAULT） | `project/manifests/ps/sha256_12951e04….json` | 1550 B | 6d70ae544c9e5969f9b0c6e27b8593d959392b75ea86bf694a02b0dc89782966 |
| PS Manifest（OK） | `project/manifests/ps/sha256_4c846d5b….json` | 1550 B | 7b91543845fd8c9317f52181b15dc9abd864b087cc78a0d0181eb7760f0dc785 |
| XSA（含 HDF） | `project/platform.xsa` | 350,194 B | 3c4e8ea769678760132ad35f679a46852f1a597ebc236f2eedfa1de0d4843612 |
| Bitstream | `project/bitstream/led6_top.bit` | 4,045,670 B | 12992210ae44d83722d52cff9666c5b8e7899150f3a16b92c1249e9a4c61f595 |
| ELF（FAULT） | `project/led6_app_fault/Debug/led6_app_fault.elf` | 188,992 B | bda766fde82bfef5c557cd58bd89566f7caadefc1be9baa36a9a7b13f506c4a7 |
| ELF（OK） | `project/led6_app_ok/Debug/led6_app_ok.elf` | 188,968 B | 9e95b24bc4bd857698fb97052e7e819770d614278ad56b9204505130005db35a |
| BD wrapper | `project/hdl/platform_bd_wrapper.v` | 2,728 B | 2602ebdd6d8cbb85b147fc33ebc3b72b01f7de30264ac957023cd39d9493540f |
| system_top | `project/rtl/system_top.v` | 2,190 B | 9db9f54696ffae0f31e1e2c4b800575f03781caad1176ed31e703ea39be55472 |
| 约束 | `project/xdc/led_pins.xdc` | 496 B | 353d8edc5a8253dfd0aaa125945ebcc970d6220c2dca2a46c6b31e8727bf4c8a |
| UART 证据 | `evidence/uart_fault.txt` / `evidence/uart_ok.txt` | 60 / 1006 B | e27cbae8… / b659a429…（全文见 §7） |

## 11. 操作终态分布（真实 Ledger）

- 最终阶段（`mcp_calls.jsonl`，clean）：**46 SUCCEEDED / 2 FAILED**（wait_operation 终态）。
  2 FAILED 为过程导航错误：`ps_compile` 首跑 BUILD_FAILED（工作区未指向含 BSP 的 project 目录）、
  `ps_create_app` APP_CREATE_FAILED（app 名已存在）——均按公开路径纠正后全绿；非产品缺陷。
- 前半阶段（`mcp_calls_phase1.jsonl`，含事故重放污染）：Platform 15 步 / PL 9 步全部
  SUCCEEDED（含事故前成功轨迹），事故段（N2 死锁 + 驱动重放）如实保留。
- 25+ 操作要求满足（最终阶段 46 个成功终态）。

## 12. 清理证据（PID 前后核对）

| 进程 | 本阶段前 | 本阶段中 | 本阶段后 |
|---|---|---|---|
| vivado.exe（MCP 启动） | 无 | 运行（Platform/PL 域） | **已终止**（close_session；Get-Process 无结果） |
| rdi_xsct.exe | 无 | 运行（PS 构建域） | **已终止**（close_session force shutdown；无结果） |
| rdi_xsdb.exe | 无 | 运行（JTAG 域；每次 disconnect 清理） | **已终止** |
| hw_server.exe（PID 19880，2026-08-09 启动） | 存在（早于本阶段） | 仅连接复用（127.0.0.1:3121） | **仍在运行（不在清理范围，与 B09/前轮记录一致）** |
| python（驱动+server） | 无 | 运行 | 驱动仍在后台（本会话 harness；证据 ledger 持续写入） |

## 13. D10 机制使用情况

- 使用方式：`ps_set_compiler_options {"opts":{"defines":"FAULT_INJECT"}}` → `ps_compile`
  （FAULT app）。机制为 D10 修复后的 `app config -add define-compiler-symbols FAULT_INJECT`
  （每符号一条）→ `app build`。
- **生效实证**：① FAULT app `Debug/src/subdir.mk` 编译行含 `-DFAULT_INJECT`；② 真板 FAULT
  ELF 第 1 轮 `READ:0x2B` → `LED_E2E_FAIL`（宏分支真实编译进固件并改变行为）。
- OK 构建未设 defines（`_WS_DEFINES` 无该 ws 键），编译行无任何 `-D`——干净构建。

## 14. 未确认项

- **LED 物理亮灭现象（尤其 PS 2 灯 MIO0/MIO13）**：UART 机器证据（DATA_RO 读回 + 44 轮
  逐位一致 + PASS 后继续循环）已证明电气/地址/回读链路正确且 PS 引脚被真实驱动，但**实际
  亮灭模式需用户按需求 §3.1 实板确认**——留待阶段⑤（本次需求 §6 明确：6 灯含 PS 2 灯都要亮）。

## 15. 结论

- **本阶段判定：PASS（UART 机器证据）**。按更新后需求文档与更新后 Skill 重新实现固件，
  真板双跑闭环：FAULT→`LED_E2E_FAIL`（机读 FAIL）、正确→`LED_E2E_PASS` + **PASS 后继续
  无限 1s 交替 28 行证据**；DATA_RO 真实状态读回逐位一致；D10 defines 新机制真机生效；
  S6 两次 12/12；JTAG 8 步部署 + UART 捕获全部经公开 MCP。
- 新记录观察项 N2（OUTCOME_UNKNOWN 死锁，P2，只记录）与 N1 延续；恢复路径全部在公开
  契约内（close_session + 新 session + harness 新 runtime 实例）。
- 已按铁律：未修改 `mcps/`、`skills/`、`boards/`、docs 冻结文档、三个 legacy 目录；只写了
  workspace 工程/驱动/证据、本文档与 `REPORT.md`；全部 EDA/构建/Manifest/部署/观测动作经
  公开 MCP，零 shell 逃生。
