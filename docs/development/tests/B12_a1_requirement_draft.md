# B12-A1 考题需求草案：DMA 数据通路环回验证（DRAFT）

> 日期：2026-08-24 | 状态：DRAFT（白盒用；黑盒冻结时以冻结基线为准）
> 目标切片：B12-A1 = 图案 → DMA → DDR3 → 读回校验 → UART 机读判定。**无需任何新硬件**。

## 1. 目标（一句话）

在 AX7020 上验证 **AXI DMA 数据通路**：确定性数据经 DMA 写入 DDR3，PS 读回逐字节校验，UART 输出机器可判定结果。

## 2. 板卡物理事实（黑盒可见，来源=板卡包）

- 板卡 `ALINX_AX7020_v1.0`；PS UART1（MIO48/49）115200，USB 桥 CP2102-GM（COM4，以 `ps_list_serial_ports` 实测为准）；
- 本切片不接任何外部模块（ADC 不在本切片范围）。

## 3. 目标行为（要什么）

1. **图案**：每轮生成长度 N = 1 MB 的确定性字节图案（递增序列或伪随机种子序列，由实现方自定但必须可独立重算）；
2. **传输**：图案经 DMA 写入 DDR3；
3. **校验**：PS 从 DDR 读回并**逐字节比对**（校验必须针对经历 DMA 的 DDR 内容，不得用未经历 DMA 的原始缓冲区冒充）；
4. **UART 输出**（机读格式，每轮一行）：`ROUND:<k> BYTES:<n> <OK|ERR>`；首行 banner 建议形如 `=== AX7020 DMA LOOP B12 ===`；
5. **循环**：每轮通过后继续下一轮（持续循环，不复位不停止）；累计**不少于 4 轮全部 OK** 后打印一次 `DMA_LOOP_PASS` 并**继续循环**；任一字节不一致 → 打印 `DMA_LOOP_FAIL` 并停止。

## 4. PASS/FAIL 判据（机读）

| 条件 | 输出 | 判定 |
|---|---|---|
| ≥4 轮全部 OK + `DMA_LOOP_PASS`（仅一次） | PASS | evaluate_observation（显式 marker） |
| 任一字节不一致 | `DMA_LOOP_FAIL`（随后停止） | FAIL |
| 120s 内无 marker | — | TIMEOUT |

## 5. 不限定实现（智能体自主决策）

- DMA 模式（简单/分散聚集）、中断或轮询、缓冲大小与布局、图案生成方式——均由实现方按框架 S3 选型决定；
- 约束：全部 EDA/构建/部署/观测经公开 MCP；三 Manifest 经 `verify_consistency` 全过；收尾按 Skill 7d（本需求要求持续循环 → 保持运行态）。

## 6. 白盒附加门禁

- 必须验证 **FAIL 路径**：构造一次读回不一致（如篡改 DDR 内容或校验种子偏移）→ `DMA_LOOP_FAIL` 机读判定 FAIL；
- 全部操作轨迹与产物留证据（mcp_calls.jsonl、三 Manifest、UART 捕获全文）。

## 7. 黑盒供给边界

- 黑盒可见：本需求 + 板卡包公开事实面（README/board_profile/adc/ 事实卡等）+ 泛化 Skill + 公开 MCP；
- 黑盒禁入：厂商例程与教程（`docs/ad7606boardinformation/`、`docs/boardinformation/` 教程 PDF、`D:\BaiduNetdiskDownload\...` 厂商工程）、本仓库其余一切。
