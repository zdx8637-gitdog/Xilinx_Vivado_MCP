# G11 — ARM JTAG Development Workflow

> 日期: 2026-08-03
> 状态: ✅ G11.0 完成 — ARM 执行 + UART 验证通过 + 连续下载无需断电
> 下一步: G11.1 — XsctProcess

---

## 一、目标

构建 AI Agent 可操作的 Zynq-7020 ARM 开发通道：
```
Claude Code → MCP → XSCT → JTAG → ARM Cortex-A9 → UART/LED
```

关键原则：**所有步骤可观测、可恢复、可复现。Power Cycle 是最后手段。**

---

## 二、规范构建流水线

### 2.1 Vivado 端：BD → bitstream → XSA

**脚本**: `zynq_platforms/ax7020_base/block_design/build_g11.tcl`

**规范要求**（来自 ALINX 官方 537 参数 PS7 配置）:
1. 使用完整的 PS7 配置（不依赖 Vivado 默认值）—— DDR 时序、外设时钟、MIO 驱动强度全部显式指定
2. PS7 + AXI Interconnect + AXI GPIO 全部在 BD 中创建
3. 综合前生成 HDL wrapper，约束写在 XDC 文件中（不在 Tcl 中动态设 pin）
4. 使用 `launch_runs` + `wait_on_run` 处理 IP OOC 综合

**产出**:
| 文件 | 路径 |
|------|------|
| Bitstream | `g11_build/ps_led.bit` |
| XSA | `xsa/ax7020_base.xsa` |
| ps7_init.tcl | `g11_build/.../ax7020_base_processing_system7_0_0/ps7_init.tcl` |

### 2.2 Vitis 端：XSA → Platform → BSP → ELF

**脚本**: `zynq_platforms/ax7020_base/build_g11_vitis.tcl`

**规范要求**（来自 ALINX 官方 `build_vitis.tcl`）:
1. 使用 `sysproj build`（不是 `app build`）—— 与官方一致
2. 从 Vivado 导出的 XSA 创建 Platform → 自动 generate BSP → 编译 ELF
3. XSA 和 ELF 必须来自**同一次构建**——防止 BSP 地址不匹配

**BSP 一致性验证**:
```
xparameters.h:  XPAR_AXI_GPIO_LED_BASEADDR  = 0x41200000
BD hw_handoff:  C_BASEADDR                  = 0x41200000
xparameters.h:  XPAR_PS7_UART_1_UART_CLK_FREQ_HZ = 100000000
```

**ARM 启动链**（反汇编确认）:
```
0x100000  _vector_table  → b _boot
0x10012c  _boot          → CPU ID 检查 → 各模式 SP → CACR → MMU → VFP
0x10039c                 → b _start
0x10071c  _start         → __cpu_init → 清 BSS → 设 SP → __libc_init_array
0x100770                 → bl main
0x10059c  main
```

### 2.3 VFP 使能确认

反汇编已确认 BSP 正确使能 VFP（与之前的 UND 假象无关）:
```
10035c: mrc p15,0,r1,c1,c0,{2}     @ 读 CACR
100360: orr r1, r1, #0xF00000      @ 允许 CP10/CP11 (VFP/NEON)
100364: mcr p15,0,r1,c1,c0,{2}     @ 写回 CACR
100368: vmrs r1, fpexc
10036c: orr r1, r1, #0x40000000    @ 使能 VFP
100370: vmsr fpexc, r1
```

---

## 三、关键修复汇总

### Fix #1: `loadhw` — 必需的 PL 内存映射注册

**问题**: `mwr`/`mrd` 到 PL AXI 地址 (0x41200000) 报错：
> `PL AXI slave ports access is not allowed. This address has not been added to the memory map`

**根因**: XSCT 调试器需要通过 `loadhw <XSA>` 注册 PL 内存映射表，否则拒绝所有 PL AXI 访问。

**修复**: 在 `ps7_init` 之后、任何 `mwr`/`mrd`/`dow` 之前调用：
```tcl
loadhw D:/fpgaproject/zynq_platforms/ax7020_base/xsa/ax7020_base.xsa
```

### Fix #2: 软件断点禁止用于诊断

**问题**: `bpadd main` 产生 UND 异常假象（PC=main + UND mode + SP 垃圾）

**根因**: `bpadd` 将第一条指令替换为 `BKPT`。ARM 未使能调试时，BKPT 触发 UND 异常而非调试事件。

**规则**: 诊断脚本**禁止使用软件断点**。用连续读 PC 判断 CPU 是否在执行。

### Fix #3: UART 波特率

**问题**: 115200 乱码，COM4 收到乱码/零字节

**根因计算**:
```
XPAR_PS7_UART_1_UART_CLK_FREQ_HZ = 100,000,000   ← xparameters.h 声称值
UART_CLK_CTRL (0xF8000154)       = 0x00000A02   ← SLCR 实际值
  DIVISOR0 = 10 → UART_REF = IO_PLL / 11 ≈ 90.9 MHz  ← 实际值！

光盘 baud = 100MHz / (108 × 8) = 115,740 bps  ← 算错
正确 baud = 90.9MHz / (49 × 16) = 115,956 bps  ← 实测吻合
```

**规则**: 不信任 `xparameters.h` 的时钟值；用 `mrd 0xF8000154` 读 SLCR 寄存器确定实际 UART 参考时钟。

---

## 四、恢复状态机（无需断电的核心）

### 4.1 为什么需要恢复

XST 脚本退出时 CPU 保持运行状态；下次重连时 CPU 可能处于任意状态（Running / Halted / Suspended / 异常死循环）。不能假设干净状态。

### 4.2 递进恢复流程

**脚本**: `zynq_platforms/ax7020_base/recover_target.tcl`

```
[0] Clean slate           — disconnect 清旧会话
[1] Connect               — 枚举 targets（打印 properties 含 state）
[2] Program FPGA           — target 4 (xc7z020)
[3] Recover + halt         — 递进:
    ├─ 3a. stop 直接 halt
    ├─ 3b. rst -processor → stop
    ├─ 3c. rst -cores     → stop
    └─ 3d. rst -system    → stop
    全部失败 → 提示 power cycle（最后一步）
[4] PS init                — ps7_init + ps7_post_config
[5] Load HW                — loadhw 注册 PL 内存映射
[6] Download ELF           — dow 重试最多 3 次
[7] Run                    — rwr pc + con
[8] Clean exit             — leave CPU running (or halt if needed)
```

### 4.3 halt 检测规则

```tcl
proc try_halt {check_state} {
    set ok [catch {stop} result]
    # "Already stopped" = SUCCESS — CPU 已经 halted
    if {[string match {*Already stopped*} $result]} { return 1 }
    # state 检查: Halted / Stopped / Suspended 都是 halted
    if {$check_state && [is_halted]} { return 1 }
    return 0
}
```

**教训**: `stop` 对已 halt 的 CPU 返回 "Already stopped"——这是成功，不是失败。state 命令可能返回 Running / Halted / Stopped / Suspended 四种状态。

### 4.4 硬件限制

| 项目 | 事实 |
|------|------|
| JTAG 线缆 | FT2232H, 仅 TCK/TDI/TDO/TMS 4 线 |
| SRST 引脚 | **无** — 板载 JTAG 不连接 PS_SRST_B |
| `rst -srst` | XSCT 语法支持但物理无效 — 无信号线驱动 |
| `rst -processor` | ✅ 有效 — 通过 DAP 访问 CPU debug 寄存器 |
| `rst -system` | ✅ 有效 — 触发 PS 内部系统复位 |

---

## 五、ARM 程序规范

### 5.1 最小 ARM 程序（当前验证用）

**文件**: `embedded_projects/ps_led_test/src/main.c`

**原则**:
- 直接写外设寄存器（`Xil_Out32`），不依赖 BSP 初始化（`xil_printf` 需要 BSP 的 `init_platform`）
- active-low LED (0=ON, 1=OFF)
- UART 波特率从 SLCR 寄存器确定实际值
- spin-wait 延时（不依赖 `usleep` + 定时器中断）

### 5.2 诊断原则

1. **不用软件断点诊断** — BKPT → UND 假象
2. **连续读 PC 判断执行** — PC 持续变化 = 在执行
3. **异常 handler 定位** — PC 停在 handler 地址 = 确认异常路径
4. **DSFR/IFSR 辅助** — 非零说明发生了 Data/Prefetch Abort

---

## 六、正常开发循环（无需断电）

```
修改 main.c
    ↓
cp + rm old ELF + rebuild_app.tcl      (30秒)
    ↓
recover_target.tcl                      (60秒)
    ↓
read_uart COM4 @ 115200                (即时)
    ↓
目视 LED 交替                           (即时)
    ↓
修改 main.c                             ← 循环
```

**全程无需碰开发板电源。** 第一次成功后，后续循环只是重新 `recover_target → dow → con`。

---

## 七、文件清单

| 文件 | 用途 |
|------|------|
| `block_design/build_g11.tcl` | Vivado: BD → bitstream → XSA (ALINX 537 param) |
| `build_g11_vitis.tcl` | Vitis: XSA → platform → sysproj build → ELF |
| `recover_target.tcl` | XSCT: 递进恢复 + 下载 + 运行 |
| `diag_halt.tcl` | 纯观测诊断: CPU state, PC, CPSR, DFSR/IFSR |
| `embedded_projects/ps_led_test/src/main.c` | ARM 验证程序 |
| `constraints/led_pins.xdc` | LED 引脚约束 |
| `g11_build/ps_led.bit` | 最终 bitstream |
| `vitis_workspace_g11/ps_led_test/Debug/ps_led_test.elf` | 最终 ARM ELF |
| `xsa/ax7020_base.xsa` | 硬件平台导出 |

---

## 八、AI Agent 操作接口（G11.1 规划）

**Layer 1** — `XsctProcess`: xsct 命令行 thin wrapper（参考 `XSimProcess`）

**Layer 2** — Vitis MCP Tools:
| Tool | 功能 |
|------|------|
| `launch_on_hardware` | recover + dow + run |
| `build_app` | 重编译 ARM ELF |
| `read_arm_uart` | COM4 @ 115200 |
| `debug_arm` | halt → 读 PC/CPSR/SP/LR |

**Layer 3** — `fpga-verify` Skill: Build → Download → UART → LED 全验证

---

## 九、已知待办

| 项目 | 状态 |
|------|:--:|
| PL UART (COM5 / F17) 加入 bitstream | ⬜ G10_PL_uart_addon.md 待实施 |
| XsctProcess (Layer 1) | ⬜ |
| Vitis MCP Tools (Layer 2) | ⬜ |
| fpga-verify Skill 整合 ARM | ⬜ |
| BOOT.BIN 打包 | G12 |
