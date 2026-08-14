# F-TIM-001: Impossible Clock Frequency

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-TIM-001 |
| 类别 | Timing |
| 检测阶段 | Timing Analysis |
| 文件 | constraints/top.xdc |

## 注入的缺陷

时钟周期从 20ns (50 MHz) 改为 2ns (500 MHz):

```xdc
# 原始 (正确):
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]

# 故障 (buggy):
create_clock -period 2.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]
```

XC7Z020CLG400-2 的速度等级为 -2，Fmax 通常在 300-400 MHz 范围。
500 MHz 时钟约束无法满足。

## 预期症状

1. **Synthesis**: 正常通过
2. **Implementation**: 可能通过（place/route 尝试满足时序但不一定失败）
3. **Timing Analysis**: WNS 为负值（严重不满足）
4. **report_timing_summary**: num_failing > 0

## 推荐诊断

测试代理应:
1. 运行 report_timing_summary
2. 发现 WNS 为负值，failing endpoints > 0
3. 检查时钟约束，发现 period 为 2ns 而非合理的 20ns
4. 推荐修复为 20ns 或合理的时钟频率

## 推荐恢复

```xdc
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]
```
