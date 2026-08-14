# F-CON-001: Missing Clock Constraint

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-CON-001 |
| 类别 | Constraint |
| 检测阶段 | Implementation / Timing Analysis |
| 文件 | constraints/top.xdc |

## 注入的缺陷

整个 `create_clock` 约束被删除:

```xdc
# 缺失:
# create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]
```

引脚约束仍然存在（PACKAGE_PIN 和 IOSTANDARD），但没有时钟定义。

## 预期症状

1. **Synthesis**: 正常通过
2. **Implementation**: 可能通过，但 Vivado 可能产生警告
3. **Timing Analysis**: 无时钟域可分析，或所有路径为 unconstrained
4. **report_timing_summary**: 可能返回空结果或 "No clocks found"

## 推荐诊断

测试代理应:
1. 运行 report_timing_summary 或 get_clocks
2. 发现没有时钟被定义
3. 检查 XDC 文件，发现缺少 create_clock
4. 添加时钟约束

## 推荐恢复

```xdc
create_clock -period 20.000 -name sys_clk -waveform {0.000 10.000} [get_ports sys_clk]
```
