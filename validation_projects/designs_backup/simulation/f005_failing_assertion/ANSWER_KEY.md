# F-SIM-001: Inverted Assertion

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-SIM-001 |
| 类别 | Simulation |
| 检测阶段 | Simulation |
| 文件 | sim/tb_breath_led.v |

## 注入的缺陷

Testbench 中 TEST 2 的通过消息被从 `PASS` 改为 `FAIL`:

```verilog
// 原始 (正确):
$display("  PASS: duty at valid level after reset release");

// 故障 (buggy):
$display("  FAIL: duty at valid level after reset release — BUG: assertion inverted");
```

注意: RTL 本身是正确的，只是 testbench 的断言标签被反转。

## 预期症状

1. **Simulation**: 返回 1 个 FAIL 断言
2. **Simulation**: 断言消息包含 "FAIL: duty at valid level"
3. **Build**: 正常通过
4. **RTL 功能**: 完全正确

## 推荐诊断

测试代理应:
1. 运行仿真，在断言报告中发现 FAIL
2. 检查对应 testbench 行的上下文
3. 识别 `$display("  FAIL: ...")` 与实际测试逻辑不匹配
   (测试条件 `duty_out >= 32'h1000_0000` 实际上通过了)
4. 修复断言消息为 `PASS`

## 推荐恢复

```verilog
$display("  PASS: duty at valid level after reset release");
```
