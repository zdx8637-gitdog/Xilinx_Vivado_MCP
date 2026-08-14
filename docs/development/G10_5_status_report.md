# G10.5 / G11 — ARM 调试状态汇报

> 日期: 2026-08-03
> 状态: **阻塞 — 等待 Vitis GUI XSCT 命令序列**

---

## 一、已验证通过的硬件通路（7 项检查中的 6 项）

参考诊断树检查点：

| # | 检查项 | 结果 | 验证方法 |
|:--:|--------|:--:|------|
| C1 | FPGA Bitstream 配置 | ✅ | XSCT `targets` 确认 xc7z020, `fpga -f` 成功 |
| C2 | PS 初始化 | ✅ | `ps7_init` + `ps7_post_config`, LVL_SHFTR_EN=0x0F |
| C3 | DDR 读写 | ✅ | `mwr 0x00100000 0xDEADBEEF` → `mrd` 回读一致 |
| C4 | **AXI GPIO** 可访问 | ✅ | XSCT `mwr 0x41200000 0xA` → `mrd` 回读 0x0000000A |
| C5 | **UART1** 寄存器可读 | ✅ | CR=0x114 (TX+RX enabled), SR=0x0A (TX FIFO empty), BAUDGEN/BDIV 可读 |
| C7 | **LED 物理映射** | ✅ | XSCT 循环 mwr 0x0→0xF→0x5→0xA, LED 按预期变化, active-low 确认 (0=ON, 1=OFF) |

LED 循环测试：
- 4 种模式 (全亮/全灭/LED0+2亮/LED1+3亮) 每 3 秒切换
- 目视确认与预期完全一致
- 证明: AXI GPIO IP → PL pin → LED 硬件链路完好

## 二、关键发现的 root cause

### 发现 #1: `loadhw` 是必需的缺失步骤

**现象**: 没有 `loadhw` 时，所有 `mwr`/`mrd` 到 PL AXI 地址 (0x41200000) 返回：
```
Memory write error at 0x41200000. Blocked address 0x41200000.
PL AXI slave ports access is not allowed. This address has not been added to the memory map.
```

**根因**: XSCT 调试器需要加载 XSA 来注册 PL 内存映射表。PS7 的 AXI 地址过滤寄存器 (AFI) 由 `ps7_init` 配置，但 DAP 需要通过 `loadhw` 获取这些地址映射信息才允许访问。

**影响**: 所有之前的 `download_test.tcl` / `download_v2.tcl` 都缺少此步骤。ARM 程序中的 `Xil_Out32(LED_BASE, ...)` 访问 AXI GPIO 同样因地址映射未注册而失败。

**当前状态**: `download_final.tcl` 已修复，加入 `loadhw` 步骤。

### 发现 #2: JTAG targets 编号因 reset 状态变化

- 干净复位后: `1=APU [2=Cortex-A9#0, 3=Cortex-A9#1], 4=xc7z020`
- 与之前脚本中 `targets -set 1` 选 APU vs xc7z020 的假设不同
- 已在 `download_final.tcl` 中修复

### 发现 #3: UART 波特率配置

- Bootrom 默认: CD=124 (BAUDGEN=0x7C), BDIV=6
  - 实际速率 = 100MHz / (124 × 6) ≈ 134,408 bps
- 正确 115200 配比: CD=108 (BAUDGEN=0x6C), BDIV=8
  - 实际速率 = 100MHz / (108 × 8) ≈ 115,740 bps (0.47% 误差)
- BD MIO 48/49 映射已核对，与硬件文档一致

### 发现 #4: 已有的硬件通路不是 ARM 自己跑的

> **关键区别 — 我们从未证明 ARM CPU 正确执行了我们下载的代码**

| 操作 | 发起者 | 走的总线 | 验证状态 |
|------|--------|---------|:--:|
| `XSCT mwr 0x41200000` | JTAG DAP | DAP → AHB → AXI → GPIO | ✅ LED 会变 |
| ARM `Xil_Out32(0x41200000, val)` | ARM Cortex-A9 | CPU → MMU → Cache → AXI → GPIO | ❌ 从未验证 |
| `XSCT mwr 0xE0001030` | JTAG DAP | DAP → APB → UART1 → TXD | ✅ COM4 收到过字节 |
| ARM `Xil_Out32(0xE0001030, c)` | ARM Cortex-A9 | CPU → APB → UART1 → TXD | ❌ 从未验证 |

**所有已验证通路的发起者都是 JTAG 调试器的 DAP，ARM 核心本身没有被证明正确执行过。**

---

## 三、ARM 调试的失败尝试

### 尝试过的 ARM 程序变体

| 版本 | 策略 | 结果 |
|------|------|:--:|
| v1 | 跑马灯 + `usleep()` | ❌ `usleep` 裸机不工作, LED 无变化 |
| v2 | 跑马灯 + spin-wait | ❌ LED 不变 (缺少 loadhw) |
| v3 | 交替 LED + spin-wait | ❌ 同上 |
| v4 | 简洁 LED + spin-wait | ❌ 同上 |
| v5 | **纯 UART** (不碰 PL/GPIO), spin-wait | ❌ COM4 零字节 |
| v6 | 纯 UART, 不重配, 用 bootrom 配置 | ❌ COM4 零字节 |

### 尝试过的 CPU 处理方式

| 方式 | 结果 |
|------|:--:|
| `stop` → `dow` → `con` | ❌ |
| `rst -processor` → `stop` → `dow` → `con` | ❌ |
| `rst -system` → `ps7_init` → `loadhw` → `stop` → `dow` → `con` | ❌ (DDR 重置导致后续混乱) |
| 直写 UART TX FIFO (经 XSCT) | ✅ 曾经收到 7 字节 → ⚠️ 后续不稳定 (DAP 状态恶化) |

---

## 四、当前核心僵局

> **ARM 程序未被执行的根本原因，不是缺少某一个命令，而是我们对 XSCT 的 JTAG Debug 状态机理解不完整。**

我们知道：
- `connect` → `fpga` → `ps7_init` → `loadhw` → `dow` → `rwr pc` → `con` 是必需的步骤
- `rst -processor` 有时有帮助但会导致 CPU 重定向到复位向量

我们不知道：
- `dow` 前后 Vitis GUI 对 CPU 状态做了哪些清理
- 是否需要显式地:
  - 清除硬件断点 (`bpremove`)
  - 设置异常向量表 (VBAR)
  - 初始化 SP (rwr sp)
  - 初始化 Translation Table Base (TTBR0)
  - Flush/Invalidate Cache
  - 设置 CPSR 模式位
- `con` 之前是否需要特殊的 CPU 初始化序列

**每个失败的 ARM 程序都无法区分是:**
- CPU 没在跑
- CPU 跑了但立即 Crash (undefined instruction, prefetch abort, data abort)
- CPU 跑了但没有执行我们的代码 (Cache 残留/MMU 残留)
- CPU 跑了、代码也执行了、但外设配置不对

---

## 五、建议下一步行动

1. **你在 Vitis GUI 中操作**
   - 用 ALINX 官方的 ps_axi_gpio 或 ps_uart 例程
   - 执行一次 **Launch on Hardware** (System Debugger)
   - 在 Vitis 中打开 **Window → Show View → XSCT Console**
   - 完整记录 Console 中输出的所有命令

2. **我把这些命令固化为 XsctProcess**
   - 照抄官方命令序列，不做任何修改
   - 先验证 ARM 程序能稳定跑起来
   - 再逐步替换为我们自己的 ELF

3. **验证闭环**
   - ARM ELF (纯 UART) → COM4 收到 "HELLO"
   - ARM ELF (UART + LED) → COM4 收到日志 + LED 变化
   - 连续下载 10 次验证无需断电

---

## 六、附带文件清单

| 文件 | 用途 |
|------|------|
| `docs/development/G11_vitis_mcp.md` | G11/G10.5 技术文档 |
| `embedded_projects/ps_led_test/src/main.c` | ARM 源文件 |
| `zynq_platforms/ax7020_base/vitis_workspace/` | Vitis workspace (含编译好的 ELF) |
| `zynq_platforms/ax7020_base/download_final.tcl` | 修复后下载脚本 (含 loadhw) |
| `zynq_platforms/ax7020_base/led_loop.tcl` | LED 循环验证脚本 |
| `zynq_platforms/ax7020_base/bringup_v3.tcl` | 7 项诊断检查脚本 |
| `zynq_platforms/ax7020_base/uart_test2.tcl` | UART 寄存器诊断 + TX 测试 |
