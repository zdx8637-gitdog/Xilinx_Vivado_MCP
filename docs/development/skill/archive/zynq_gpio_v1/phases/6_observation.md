# Phase 6 — Observation & Pass/Fail

> 输入: UART 输出文本
> 前提: Phase 5 成功完成，拿到了 UART 输出

## Skill 决策

- 判定规则固定，不需要 AI 判断。直接调用 tool。
- 判定结果写入证据链。

## 执行序列

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `evaluate_observation` | `{"uart_text": "...", "pass_marker": "GPIO_E2E_PASS", "fail_marker": "GPIO_E2E_FAIL"}` | `verdict` 为 `PASS` |

## 区分"UART 输出"和"GPIO 通路"

**这是一个关键的调试参照点。** UART 和 GPIO 走的是不同的硬件路径：

| 操作 | 地址空间 | 需要的初始化 | 证明什么 |
|------|---------|------------|------|
| UART 输出 (`Xil_Out32(0xE0001000, ...)`) | **PS 地址空间** | 仅需 `ps7_init`（系统时钟+DDR） | PS 软件在运行 |
| GPIO 写 (`Xil_Out32(0x41200000, ...)`) | **PL 地址空间**（AXI 总线） | `ps7_init` **+ `loadhw`**（注册 PL 内存映射） | PL AXI 通路完整 |
| GPIO 读 (`Xil_In32(0x41200000)`) | **PL 地址空间**（AXI 总线） | 同 GPIO 写 | **唯一可自动验证的证据** |

**因此 UART 输出 PASS marker 不能单独证明 GPIO 通路是活的！**

Phase 3 编写的 `main.c` 在每轮写入后立即 readback，并比较 wrote vs readback：
- 写入 = 0xA，读回 = 0xA → 输出 `WROTE:0xA READ:0xA`，继续
- 写入 = 0xA，读回 = 0x0 → 输出 `WROTE:0xA READ:0x0` 然后 `GPIO_E2E_FAIL`

**readback 匹配 + `GPIO_E2E_PASS` = AXI GPIO 通路完整且可自动验证。**

| 条件 | verdict |
|------|---------|
| 文本含 `GPIO_E2E_PASS` | `PASS` |
| 文本含 `GPIO_E2E_FAIL`（且无 PASS） | `FAIL` |
| 文本为空 | `TIMEOUT` |
| 文本有内容但无任何 marker | `INCOMPLETE` |

**PASS marker 优先于 FAIL marker。**

## GPIO 项目的预期输出

按 Phase 3 规范编写的 `main.c` 输出格式：

```
=== AX7020 GPIO B08 ===
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
WROTE:0xA READ:0xA
WROTE:0x5 READ:0x5
GPIO_E2E_PASS
```

判定 marker：`GPIO_E2E_PASS` → PASS。

## 产物

- verdict + 完整 UART 文本 → 保存为 `evidence/uart_result.json`
- 最终证据链：Phase 0-6 所有产物的路径 + SHA256 + 判定结果

## 失败恢复

| verdict | 动作 |
|---------|------|
| `FAIL` | 程序报告了 GPIO_E2E_FAIL。检查 readback 值与写入值是否匹配，检查 P5 是否执行了 `ps_load_hardware` |
| `TIMEOUT` | UART 无输出。进入 Phase 7 UART 诊断 cascade |
| `INCOMPLETE` | UART 有输出但不含预期标记。程序可能未完整运行。检查 ELF 是否正确编译、是否正确下载 |

## 结束

Phase 6 完成后，GPIO 项目即告完成。产出物：
- Bitstream + ELF（可复现）
- Platform Manifest（含 revision + address map）
- UART 输出 + PASS/FAIL 判定
- 完整步骤记录

> 如果要重新运行，从 Phase 0 的 `create_session` 开始，使用**新的** project_path 目录。
