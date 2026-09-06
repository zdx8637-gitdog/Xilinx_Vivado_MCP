# B13-P4b 代码规范与 Zynq 库使用规范问题报告（工程层回顾）

日期：2026-09-06（第三代接续收尾）
定位：本报告只登记**工程层**问题——我方固件/RTL/PC 机具的编码写法与
Zynq/Vitis 库（XUartPs、XAxiDma、XEmacPs、lwIP、Xil_* 等）使用姿势问题。
每条都是"**这么写了 → 测试暴露了 → 回去改了**"的真实路径，与流程/工具
（MCP/Skill/框架）无关。流程工具类问题见 `evidence/FINDINGS.md`（F 系列）。

---

## 1. 总览表

| # | 层 | 错误写法 | 测试暴露 | 回改 | 引用 |
|---|---|---|---|---|---|
| 1 | 固件 | `XUartPs_SendBuffer` 直接发长帧 | SELFTEST ACK 帧从未上总线 | 先排空 TX FIFO 再发、校验计数值 | F-14 |
| 2 | 固件 | lwIP NO_SYS 只调 `xemacif_input` | 客户端连接超时，收不到任何包 | 主循环补轮询 ISR 体 `emacps_recv_handler` | F-15 |
| 3 | 固件 | `TCP_SND_BUF` 配 256KB | 连上后 0 字节发出 | 降回 60KB（`tcpwnd_size_t` 是 u16） | F-16 |
| 4 | 固件 | `tcp_write(栈缓冲, apiflags=0)` | READY 帧头/CRC 是垃圾 | 栈/堆缓冲一律 `TCP_WRITE_FLAG_COPY` | F-16b |
| 5 | 固件 | 逐位 CRC32 表放普通 DDR | 23 行/s（0.22MB/s） | slice-by-4 + 表搬 OCM + 32 位读 + 关软件 TCP 校验和 | F-17 |
| 6 | 固件 | 使能 MMU+D-cache | ping/ARP 全死，残留态毒化后续下载 | 回退 D-cache OFF + OCM 表方案 | F-18 |
| 7 | 固件 | 采集路径不武装 PL 事件计数器 | STATUS 实测速率恒 0 | `capture_begin` 补 CLR→ARM | F-22 |
| 8 | 固件 | END 帧一次性 `tcp_write` 不查返回值 | 拥塞下 END 永久丢失 | 排空后发送 + sndbuf 检查 + 重试 | F-23 |
| 9 | 固件 | 假设重下 ELF 会复位 PL 数据通路 | 重下后每采丢失 ~927 样点 | 全量部署（重灌位流）起跑 | F-24 |
| 10 | 固件 | 排空停滞时无人调 `tcp_output` | 窗口重开后 0 字节排出 | 发送循环 break 后主动踢 `tcp_output` | F-25c |
| 11 | 固件 | 100ms RXEN 翻转工作区照搬 | 停读恢复后第一个 ACK 后再无 ACK 入账 | 移除（ACK-only RX 不适用该勘误） | F-25d |
| 12 | 固件 | lscript.ld 默认栈 8KB | selftest 后栈溢出崩溃 | `_STACK_SIZE=0x10000`、`_HEAP_SIZE=0x8000` | D-04 |
| 13 | RTL | 多位信号用位拼接移位同步 | gear1(01)→00，100k 档变 2k | 整宽两级同步 `s<=in; s1<=s0` | F-21 |
| 14 | RTL | case 的 default 恰好等于某档值 | 1M 档"碰巧正常"掩盖缺陷 | 三档全测；default 不得伪装成合法档 | F-21 |
| 15 | RTL | 计数器无显式 arm 语义 | conv 恒 0 → 下游速率判 0 | 固件每次采集 CLR/ARM 成对 | F-22 |
| 16 | PC | 吞吐阈值写死 2.0 标称 | 合格板被 0.105% 余量误杀 | 阈值 = 标称×(1−容差) = 1.99 | F-19 |
| 17 | PC | 溢出判据 `rows==6000` | 与丢最旧语义矛盾必败 | 账目守恒式 `crc_ok+坏CRC+ovf==总行数` | F-20 |
| 18 | PC | 拥塞模拟压 SO_RCVBUF=4096 | 零窗口 persist 楔死排空 | 去掉小窗口，仅停读 35s | F-25 |
| 19 | PC | 常量单位与固件不对齐（点/行 vs 字节/行） | 误判引擎 30M 目标"异常" | 两端常量逐一对表（PTS=5000 点=10000B/行） | 本会话 |
| 20 | PC | 同一 COM4 开两次（监听线程 + uart_cmd） | PermissionError 直接崩 | 串口单进程单句柄、短开短关 | D-01 |

---

## 2. 固件（bare-metal C / Vitis standalone）

### 2.1 XUartPs：非阻塞发送会静默丢帧（F-14）
- **错误写法**：`XUartPs_SendBuffer(&uart_ps, buf, len)` 一次发 ~600B（SELFTEST
  应答块），不检查 FIFO 状态、不校验发送计数。
- **测试暴露**：SELFTEST 的 ACK 帧从未出现在总线上（CRC/帧头全无）。
- **根因**：`XUartPs_SendBuffer` 是**非阻塞**接口——TX FIFO 满时整帧被丢，
  调用方拿到的"成功"不代表上总线。
- **回改/规范**：
  1. 发送前 `while (!XUartPs_IsTransmitEmpty(&uart_ps) && guard < 1000000u);`
     排空上一帧；
  2. 发送后校验实际计数（`XUartPs_GetNumSent` 或 SendBuffer 返回的字节数）；
  3. 长帧（> FIFO 深度）必须分段或等 FIFO 腾空；控制帧永远走这个收口。
- **防再犯**：所有 UART 出帧只经一个 `uart_send()` 收口；禁止裸调 SendBuffer。

### 2.2 lwIP NO_SYS：RX 必须轮询 ISR 体，TX 必须主动踢（F-15 / F-25c）
- **错误写法 A**：D-02 去掉了 GEM 中断注册（NO_SYS），主循环只调
  `xemacif_input(&netif)`。
- **测试暴露**：客户端 connect 超时——ARP/任何包都收不到。
- **根因**：`xemacif_input` 只从软件队列 `recv_q` 取包；把 RX BD 环灌进
  `recv_q` 的是 `emacps_recv_handler`（中断服务函数体）。没中断就得在主循环
  里轮询 FRAMERX 状态位并**手动调用这个 ISR 体**。
- **错误写法 B**：发送循环 `while(...){ if(tcp_sndbuf<need) break; send_row(); }`
  一旦 break，此后没有任何代码再调 `tcp_output`。
- **测试暴露**：停读 35s 恢复后，窗口已重开（sndwnd=65070）但 0 字节排出。
- **回改/规范**：
  1. 主循环每轮：`if (ISR & FRAMERX) { 清位; emacps_recv_handler(state); }`
     `xemacif_input(&netif);`
  2. 发送循环 break 后：`if (rows_sent < avail) tcp_output(pcb);`（踢一脚）；
  3. `tcp_tmr()` 保持 250ms 节奏；NO_SYS 应用就是 lwIP 的"操作系统"。
- **防再犯**：NO_SYS 轮询三件套（收 ISR 体 + input + 踢 output + tmr）写进
  主循环模板，缺一不可。

### 2.3 TCP_SND_BUF 受 u16 窗口类型钳制（F-16）
- **错误写法**：`TCP_SND_BUF=262144`（256KB）。
- **测试暴露**：客户端连接成功、READY 可见，但 START 后 0 字节数据。
- **根因**：`LWIP_WND_SCALE=0` 时 `tcpwnd_size_t` 是 **u16**，256KB 截断为 0
  → `snd_buf=0` → 任何 `tcp_write` 失败（tcp.c:1893 路径）。
- **回改/规范**：60KB（u16 内最大值附近，留头）；改任何 `TCP_*_BUF/WND`
  宏前先确认 `tcpwnd_size_t` 宽度，或同步开 `LWIP_WND_SCALE`。

### 2.4 tcp_write 的 COPY 标志与 GEM 异步 BD（F-16b）
- **错误写法**：帧头/CRC 用**栈上**数组 + `tcp_write(..., apiflags=0)`。
- **测试暴露**：READY 帧到达，12B 头 + 4B CRC 是垃圾，96B payload 完好。
- **根因**：GEM 发送走 BD 描述符**异步**读内存；`apiflags=0` 承诺缓冲在
  `tcp_write` 返回后仍然有效——栈帧一弹出就失效，BD 读到死栈。
- **回改/规范**：非静态 DDR 缓冲一律 `TCP_WRITE_FLAG_COPY`；只有指向静态
  DDR 的大块数据（行 payload）保持零拷贝（契约 §5 的性能前提）。

### 2.5 tcp_write 失败必须检查，控制帧必须重试（F-23）
- **错误写法**：END 帧在引擎 DONE 跳变处**一次性**发送，不看返回值、
  发完就清 end_pending。
- **测试暴露**：溢出门禁下 END 永久缺失（END 被拥塞丢弃后永不重试）。
- **回改/规范**：END 帧改到行排空之后；`tcp_sndbuf(pcb) >= 帧长` 才写；
  写不进就下轮重试；只有"客户端已断开"才放弃。所有**必达**控制帧
  （END/状态）照此模式；普通数据行可丢（丢最旧语义兜底）。

### 2.6 CRC 热路径：表位置 + 位宽 + 硬件卸载（F-17）
- **错误写法**：逐位 CRC32（41ms/行）→ 字节表放普通 DDR（5.9ms/行）。
- **测试暴露**：整图吞吐封顶 ~23 行/s（0.22MB/s），距离 2MB/s 差 9 倍。
- **根因**：D-cache OFF 时普通 DDR 读是**未缓存**的，查表每字节都是慢读。
- **回改/规范**：
  1. slice-by-4（zlib BYFOUR 同构，4×256 表），PC 侧 500 组随机对拍 + READY
     KAT `54ACFE90` 校验；
  2. 表复制进 **OCM**（0xFFFF0000 高别名 SRAM，绕开未缓存 DDR）；
  3. 行数据按 **32 位字**装载再拆字节（DDR 读次数 ÷4）；
  4. `CHECKSUM_GEN_TCP=0`：XEmacPs `CfgInitialize` 的 DEFAULT_OPTIONS 已含
     `XEMACPS_TX_CHKSUM_ENABLE_OPTION`（DMACR TCPCKSUM 位），硬件已算 TCP
     校验和，软件再算一遍纯属浪费内存带宽。**关软件、靠硬件是有前提的，
     前提要先验证**（本会话已从 xemacps.c:407-412 证实）。

### 2.7 MMU/D-cache 使能的连锁代价（F-18）
- **错误写法**：`Xil_EnableMMU()+Xil_DCacheEnable()` 试图提速。
- **测试暴露**：boot 文本正常、PHY link 正常，但 GEM 数据面全断（ping/ARP
  超时）；且该实验后板子残留 MMU 状态——纯 halt→dow 重下发报
  `ELF download failed: Memory write error at 0x100000. MMU section
  translation fault`，一次还跳进 `Xil_UndefinedExceptionHandler`（PC=0x11dfe0）。
- **回改/规范**：回退 D-cache OFF + OCM 表（2MB/s 目标已达成，不再追 cache）。
  若未来使能：必须配套全套缓存维护（DMA 缓冲 invalidate/flush、
  lwIP pbuf 对齐、GEM BD 描述符一致性），并在部署链里显式复位 MMU 状态。
- **防再犯**：D-cache/MMU 实验后必须走**全量部署**（reset+ps7_init+program_fpga），
  不允许 plain redep 接续。

### 2.8 链接脚本：默认栈太小（D-04）
- **错误写法**：沿用 app create 生成的 lscript.ld 默认栈 8KB/堆 8KB。
- **测试暴露**：selftest 后崩溃；上板背 trace `_stack_end()+8076`（8KB 栈用掉
  8076 字节）。
- **回改/规范**：`_STACK_SIZE=0x10000`（64KB）、`_HEAP_SIZE=0x8000`。
  注意 lscript.ld 只在 app create 时生成，改后持久；重生成 app 会覆盖，要重改。

### 2.9 PL 外设 strobe 型控制位要成对显式（F-22）
- **错误写法**：`capture_begin` 只写 gear/stream/RUN，指望计数器"默认在数"。
- **测试暴露**：全新上板（没先跑过 SELFTEST）三档 STATUS 实测速率全 0。
- **根因**：引擎事件计数器由 `cnt_arm_q` 门控，只有 CTRL 位 5（ARM）写入才
  产生 arm 脉冲；先前速率"正常"是因为 SELFTEST L2 恰好武装过——隐式状态。
- **回改/规范**：每次采集开始前 `ENG_CTRL_CLR` → `ENG_CTRL_ARM` 成对写入
  （与 l2_counter_check 同款序列）；strobe 类寄存器的**状态不得跨调用依赖**。

### 2.10 复位假设：PL 数据通路状态跨 ELF 重启存活（F-24）
- **错误写法**：调试/复验时"重下 ELF = 复位"。
- **测试暴露**：溢出门禁排空未完时 halt + 仅重下 ELF，之后每次采集头部
  固定丢失 ~927 个样点（连续两采首值 926/930，全量部署后恢复 0/1/2...）。
- **根因**：引擎样点计数器、xpm FIFO、axis_register_slice、DMA S2MM 的残态
  都在 FPGA 里，ELF 重启不清除。
- **回改/规范**：任何跨会话续跑以**全量部署**（halt→reset→ps7_init→
  program_fpga→loadhw→dow→run）为起点；验收链内禁止中途重下 ELF。

---

## 3. PL RTL（Verilog）规范问题

### 3.1 CDC 多位同步：位拼接移位只适用 1 位（F-21）
- **错误写法**：
  ```verilog
  gear_sync0 <= {gear_sync0[0], gear_reg};      // 3 位拼 2 位寄存器：截断
  gear_sync1 <= {gear_sync1[0], gear_sync0[1]}; // 只把 bit1 灌进二级链
  ```
- **测试暴露**：gear1(01)→00 变 2k；gear2(10)→11 走 default 碰巧 1M。
- **回改/规范**：
  ```verilog
  gear_sync0 <= gear_reg;
  gear_sync1 <= gear_sync0;
  ```
  规则：**1 位信号**用 `{s[0], in}` 移位同步没问题；**多位信号**必须整宽
  两级/三级赋值。review 时凡是见到多位的 `{x[0], y}` 直接标红。

### 3.2 case default 不得伪装成合法档（F-21 教训）
- **错误写法**：`(gear==2'b10)?50 : 50` —— default 兜底值恰好等于 1M 档的
  正确值，把档位错配**静默掩盖**成"1M 正常"。
- **测试暴露**：SELFTEST L2 的 1M 计数一直 PASS，直到三档 FSCAL 才露馅。
- **回改/规范**：default 分支要么显式非法处理（挂起/告警位），要么至少与
  合法档的值**不同**；验收必须覆盖**全部**档位枚举，不能只测 default 命中的档。

### 3.3 计数器/状态位的 arm 与清零语义要在接口约定里写死（F-22）
- 事件计数器（conv/busyf/rd/frstd/rerr/cerr/wdg）是"清零脉冲 + arm 门控 +
  边沿计数"三段式；写 RTL 注释与固件调用序列必须一一对应（CLR 先 ARM 后、
  ARM 必须在首个事件前落地）。状态回读（snapshot）是跨域握手（strobe→latch→
  toggle 回传），固件侧必须等 SNAPVAL 而不是盲读。

---

## 4. PC 机具（Python）规范问题

### 4.1 判据阈值从契约容差推导，禁止写死标称（F-19）
- 契约规定三档速率容差 ±0.5%，板载晶振实测 FSCAL=998944（在容差内），
  数据面吞吐上限 1.9979MB/s——硬写 2.0 会误杀合格板。
- **规范**：`阈值 = 标称 × (1 − 容差)`；原始实测值照实落盘进证据 JSON，
  判据与原始值同报，裁定权交主代理。

### 4.2 判据要写账目守恒式，别写"全集到达式"（F-20）
- 丢最旧语义下 `rows == 6000` 必然假——被写指针追越的行永远收不到。
- **规范**：`crc_ok 行 + 坏 CRC 行 + max_overflow == 总行数` + 尾连续校验。
  任何"应收到全部"的断言先问一句：有没有合法的丢弃/覆盖路径。

### 4.3 拥塞模拟参数要按缓冲容量算，别制造病态 TCP 状态（F-25）
- `SO_RCVBUF=4096` 制造了零窗口 persist 楔死（lwIP 侧 cwnd 冻结 1 MSS，
  DBG 实证 sndwnd=65070 窗口开、0 字节排出 75s）。停读 35s 本身已足以让
  5000 行缓冲被追越（追越点 t≈25s，与窗口大小无关）。
- **规范**：拥塞注入用"停读时长"表达，不要叠加小窗口；判据 deadline 按
  "停读 + 采集 + 窗口受限排空"三段估算再留余量。

### 4.4 跨端常量单位对齐（本会话教训）
- 契约写"行=5000 点 / 10000 B"，PC 侧 `PTS_PER_ROW=5000`（点），字节/行是
  2×点。本会话曾把 30M 点（6000 行×5000 点）误判为"目标被减半"——就是单位
  没对齐。**规范**：PC 与固件共用一个常量表并互相校验；报告里点数/字节数
  必须标注单位。

### 4.5 串口独占纪律（D-01 + 本会话）
- COM4 只允许**单进程、单句柄、短开短关**；后台监听线程和 uart_cmd 同时
  开串口会直接 PermissionError（本会话 probe 首版即犯）。机具设计成
  "开→发→收一帧→关"的原子操作；需要连续监听时，一切收发都走那一个句柄。

---

## 5. Zynq/Vitis 库使用要点速查（正确姿势）

| 库/API | 正确姿势 |
|---|---|
| `XUartPs` | 发前排 FIFO、发后核计数；长帧分段；单一 `uart_send()` 收口 |
| `XAxiDma`（SG 模式） | reset 通道 → BD 清零（含 NDESC/STA）→ 写 `CURDESC` → `CR|=RUNSTOP` → 写 `TAILDESC`；BD 状态轮询前 `Xil_DCacheInvalidateRange`；cyclic 环的 wrap 由 `NDESC` 指回 base |
| `XEmacPs` | `CfgInitialize` 已带 TX 校验和卸载（DEFAULT_OPTIONS）；NO_SYS 下 `emacps_recv_handler`（ISR 体）可从主循环轮询调用；SI#692601 的 RXEN 翻转工作区与轮询接收**不要叠加** |
| `Xil_*` | `Xil_In32/Xil_Out32` 访问 PL 寄存器；D-cache/MMU 实验必须配套一致性维护，实验后全量部署 |
| `XTime` | `b13_now()` 封装，注意 `XTIME_PER_US` 换算；周期任务用"上次时刻+间隔"比较，禁止阻塞等待 |
| lwIP raw API | NO_SYS 主循环四件套（见 2.2）；`tcpwnd_size_t`=u16（`LWIP_WND_SCALE=0`）；栈缓冲必须 COPY；控制帧查返回值+重试；`tcp_sndbuf` 查询后再写大块 |

---

## 6. 未解决/移交项

- **F-25 残留**：lwIP NO_SYS 零窗口 persist 恢复（cwnd 冻结 1 MSS）在
  突发排空场景未根治；若主代理裁定继续修，建议方向（按成本升序）：
  1) 门禁层改判（本会话已改断连重连排空，仍受限于同缺陷）；
  2) lwIP 调参（`TCP_SND_BUF`/`TCP_MSS`/`TCP_WND` 组合、或开
     `LWIP_WND_SCALE` 换宽窗口）；
  3) 应用层排空策略（排空态按需 abort+重连循环直至 END）；
  4) lwIP persist/慢启动恢复路径打补丁或换版本。
- **F-19 口径**：吞吐判据阈值 1.99 MB/s 的最终裁定（含原始实测值同报）。
