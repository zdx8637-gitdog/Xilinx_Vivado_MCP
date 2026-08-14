# G11 — ARM JTAG 调试诊断方法论

> 日期: 2026-08-03
> 状态: 离线分析完成，待板旁执行 diag_halt.tcl
> 关联: [G11_vitis_mcp.md](G11_vitis_mcp.md), [G10_5_status_report.md](G10_5_status_report.md)

---

## 一、离线分析结论（不需要硬件，已确认）

### 1. BSP VFP 使能序列是正确的

反汇编 `vitis_workspace_g11/ps_led_test/Debug/ps_led_test.elf`：

```
10035c: mrc p15,0,r1,c1,c0,{2}   @ 读 CACR
100360: orr r1, r1, #0xF00000    @ 允许 CP10/CP11 (VFP/NEON)
100364: mcr p15,0,r1,c1,c0,{2}   @ 写回 CACR
100368: vmrs r1, fpexc
10036c: orr r1, r1, #0x40000000  @ 使能 VFP
100370: vmsr fpexc, r1
```

**结论**: VFP 使能逻辑正确。若 CPU 从 `_boot` 正常启动，VFP 会在跳 main 前被启用，main 中的浮点/VFP 指令不会触发 UND。**"VFP 未启用导致 UND" 假设被推翻。**

### 2. 软件断点 (bpadd) 产生 UND 假象

之前 `bp_test_v2.tcl` 观察到:
- PC = 0x10059c (main)
- CPSR mode = 0x1b (UND)
- SP = 0xffffffe8 (垃圾)

**重新解释**: `bpadd main` 将 main 第一条指令替换为 `BKPT`。ARM 在调试未正确使能时，BKPT 触发 **UND 异常**而非调试事件。所以:
- PC 停在 main = 断点命中
- CPSR=UND = BKPT 被当 UND 处理（调试器未接管）
- SP 垃圾 = UND handler 的 push 用了未初始化的 UND SP

**结论**: 诊断脚本**禁止使用软件断点**。这会制造"CPU 崩溃"的假象。

### 3. 完整启动链

```
0x100000 _vector_table
   b _boot
0x10012c _boot          @ CPU ID 检查、各模式 SP、CACR、MMU、VFP
   ...
0x10039c b _start
0x10071c _start         @ __cpu_init → 清 BSS → 设 SP → __libc_init_array → bl main
0x10059c main
```

`__cpu_init` (0x100f58) 清除异常状态寄存器:
```
mcr p15,0,r0,c5,c0,{0}  @ 清 DFSR
mcr p15,0,r0,c5,c0,{1}  @ 清 IFSR
mcr p15,0,r0,c6,c0,{0}  @ 清 DFAR
mcr p15,0,r0,c6,c0,{2}  @ 清 IFAR
```

**含义**: 正常启动后 DFSR/IFSR 应为 0。诊断时若读到非零，说明启动路径上发生了异常。

### 4. 异常 handler 都是死循环

```
Xil_UndefinedExceptionHandler: b .   (死循环)
Xil_DataAbortHandler:          b .
Xil_PrefetchAbortHandler:      b .
```

**含义**: 若 CPU 真进入异常，会卡死在 handler 死循环，不会回到 main。诊断时 PC 若停在 handler 地址附近，即确认异常路径。

---

## 二、diag_halt.tcl 使用方法

文件: `zynq_platforms/ax7020_base/diag_halt.tcl`

```bash
xsct D:/fpgaproject/zynq_platforms/ax7020_base/diag_halt.tcl
```

设计原则（来自调试评审）:
1. **纯观测** — 每步打印原始输出，不吞错误，不自行解析
2. **不用软件断点** — 避免 BKPT UND 假象
3. **连续读 PC** (200ms 间隔) — 判断 CPU 是否真在运行
4. **逐步递进 reset**: `-processor` → `-cores` → `-system`
5. **退出前保证 halted + disconnect** — 下次连接干净状态

执行阶段:
| Phase | 观测 |
|-------|------|
| 1 | connect, targets 全列表, target-properties (含 state) |
| 2 | 基线: state, PC×3 (200ms间隔), CPSR, SP, LR |
| 3 | 尝试 stop, 再读 state/PC, halt 判定 |
| 4 | reset 递进: -processor → -cores → -system, 每步观测 |
| 5 | DFSR/IFSR 读取 (mcr p15 c5/c6) |
| 6 | 干净退出: stop + state + PC + disconnect |

---

## 三、结果解读指南

拿到 diag_halt.tcl 输出后，按以下规则分析:

### CPU 状态判定
| state 输出 | 含义 | 下一步 |
|-----------|------|--------|
| Running + PC 持续变化 | CPU 在执行 | 需 reset 后才能 dow |
| Halted/Stopped | CPU 已停 | 直接 dow |
| state 命令失败 | JTAG/DAP 通信异常 | 检查 cable/targets |

### halt timeout 判定
| 现象 | 可能原因 |
|------|---------|
| stop timeout + state 显示 Running | CPU 关中断或卡 WFE，debug halt 无法介入 |
| stop timeout + state 无法读取 | JTAG 链路/DAP 异常 |
| stop 成功但 PC 停在 handler 地址 | CPU 已进异常死循环 |

### reset 递进后
| 结果 | 结论 |
|------|------|
| rst -processor 后能 halt | CPU 状态可恢复，无需重启 |
| rst -system 后能 halt | PS 级恢复成功，DDR 需重新 ps7_init |
| 全部失败 | 才考虑断电（此时有充分证据） |

### DFSR/IFSR 解读
| 寄存器非零 | 含义 |
|-----------|------|
| DFSR 非零 | 发生过 Data Abort（数据访问异常） |
| IFSR 非零 | 发生过 Prefetch Abort（取指异常） |

---

## 四、关键教训（固化到后续所有下载脚本）

1. **退出前必须 halt**:
   ```tcl
   catch {targets -set 2; stop}
   after 300
   catch {state}
   disconnect
   ```

2. **不用软件断点诊断** — 用连续读 PC 判断运行状态

3. **halt 失败时递进 reset**，不要直接跳"断电"

4. **每步可观测** — 不吞错误，打印原始输出

---

## 五、Recover Target State Machine

文件: `zynq_platforms/ax7020_base/recover_target.tcl`

### 设计原则（来自调试评审）

> Power Cycle 是**最后一步**，不是第一步。

官方 Launch on Hardware 流程（参考）:
```
connect → FPGA Program → Reset APU → Init PS → Download ELF → Run
```

### 状态机

```
download()
  ├─ 能 halt ? ─ YES ─ 正常下载
  └─ NO
      ├─ rst -processor ─ 能 halt ? ─ YES ─ 下载
      ├─ rst -cores ───── 能 halt ? ─ YES ─ 下载
      ├─ rst -system ───── 能 halt ? ─ YES ─ 下载
      └─ NO ─ 提示: Please power cycle board
```

### 为什么"第一次 OK 第二次 DAP Error"

| 可能原因 | 说明 |
|---------|------|
| 调试会话未正确关闭 | XSCT 持有旧状态，重连未真正重新枚举 |
| hw_server 残留异常 target | 旧进程占用 3121 端口 |
| ARM 程序改了调试/SLCR/时钟寄存器 | 导致 DAP 后续无法访问 |
| reset 顺序与官方不一致 | 缺少某个关键步骤 |

**这些都比"Zynq 必须断电"更符合现象。**

### 完整流程

```
[0] Clean slate        — 先 disconnect 清旧会话
[1] Connect            — 枚举 targets
[2] Program FPGA       — 尝试 target 4, fallback 1
[3] Recover + halt     — 递进: halt → rst -processor → rst -cores → rst -system
[4] PS init            — 总是 ps7_init + ps7_post_config
[5] Load HW            — loadhw 注册 PL 内存映射
[6] Download ELF       — 重试最多 3 次
[7] Run                — rwr pc + con
[8] Clean exit         — stop + disconnect
```
