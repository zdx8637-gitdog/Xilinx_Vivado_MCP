# F-RTL-002: Counter Bit-Width Overflow

> **此文件仅供验证团队使用，不得提供给测试代理**

---

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-RTL-002 |
| 类别 | RTL |
| 检测阶段 | Simulation |
| 文件 | rtl/breath_led.v |
| 行号 | ~63 |

## 注入的缺陷

`breath_led.v` 中 `breath_timer` 的位宽被从 32-bit 缩小为 16-bit:

```verilog
// 原始 (正确):
reg [31:0] breath_timer;

// 故障 (buggy):
reg [15:0] breath_timer;  // BUG
```

`breath_timer` 需要计数到 `BREATH_TIMER_MAX - 1` (249,999)，但 16-bit 计数器最大值仅为 65,535。
条件 `breath_timer >= 249999` 永远不会满足，FSM 永远停留在 RAMP_UP 状态，
duty 永远不会改变。

## 预期症状

1. **Simulation**: duty 值保持 DUTY_MIN 不变，永远不会递增
2. **Simulation**: direction_changes 断言 FAIL（检测到 0 次方向变化）
3. **Build**: 正常通过（语法正确，无位宽警告）
4. **注**: Vivado 综合可能产生位宽截断 WARNING，但不是 ERROR

## 推荐诊断

测试代理应:
1. 运行仿真，观察 duty 值始终不变
2. 检查 breath_timer 的位宽声明
3. 计算所需最小位宽: ceil(log2(250000)) = 18 bits
4. 修复为 `reg [31:0] breath_timer`

## 推荐恢复

```verilog
reg [31:0] breath_timer;  // 修复
```
