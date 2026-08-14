# F-RTL-003: Multiple Drivers

> **此文件仅供验证团队使用，不得提供给测试代理**

---

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-RTL-003 |
| 类别 | RTL |
| 检测阶段 | Synthesis |
| 文件 | rtl/breath_led.v |
| 行号 | ~158 |

## 注入的缺陷

在 `breath_led.v` 中，在 `endmodule` 前添加了第二个 `always` 块来驱动 `pwm_out`:

```verilog
// 原始设计中 pwm_out 已经在第一个 always 块中被赋值

// BUG: 第二个 always 块同时驱动 pwm_out — 多驱动冲突
always @(posedge clk) begin
    pwm_out <= 1'b0;
end
```

Verilog 不允许同一个 `reg` 在多个 `always` 块中被赋值。综合工具会报 multi-driver 错误。

## 预期症状

1. **Synthesis**: FAIL — "multiple drivers for net pwm_out" 或类似的多驱动错误
2. **Simulation**: 可能通过（某些仿真器允许多驱动，输出为 X）
3. **Build**: 在 `synth_design` 阶段失败

## 推荐诊断

测试代理应:
1. 运行综合，观察到 multi-driver 错误
2. 搜索文件中所有对 `pwm_out` 的赋值
3. 识别第二个 always 块为冗余驱动源
4. 删除第二个 always 块

## 推荐恢复

```verilog
// 删除整个第二个 always 块:
// always @(posedge clk) begin
//     pwm_out <= 1'b0;
// end
```
