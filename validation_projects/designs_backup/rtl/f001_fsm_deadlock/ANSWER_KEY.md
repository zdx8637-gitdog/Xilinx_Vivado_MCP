# F-RTL-001: FSM Deadlock

> **此文件仅供验证团队使用，不得提供给测试代理**

---

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-RTL-001 |
| 类别 | RTL |
| 检测阶段 | Simulation |
| 文件 | rtl/breath_led.v |
| 行号 | ~126 |

## 注入的缺陷

`breath_led.v` 中 RAMP_DOWN → RAMP_UP 的转换条件被改为:

```verilog
// 原始 (正确):
if (duty <= DUTY_MIN + DUTY_STEP) begin
    state <= RAMP_UP;

// 故障 (buggy):
if (duty <= 32'd0) begin  // BUG
    state <= RAMP_UP;
```

由于 `duty` 最小值是 `DUTY_MIN` (0x19999999 > 0)，这个条件永远不成立。
FSM 进入 RAMP_DOWN 后将永远无法返回 RAMP_UP。

## 预期症状

1. **Simulation**: duty 值从 DUTY_MAX 递减到 DUTY_MIN，然后停止变化
2. **Simulation**: direction_changes 断言 FAIL（检测到的方向变化次数 < 2）
3. **LED 行为**: 呼吸效果在第一个下降沿之后停止——LED 保持最暗状态
4. **Build**: 正常通过（语法正确）

## 推荐诊断

测试代理应:
1. 运行仿真，观察到 duty 在达到最小值后不再变化
2. 检查 breath_led.v 的 RAMP_DOWN 状态转换条件
3. 识别 `duty <= 32'd0` 为错误的比较值
4. 修复为 `duty <= DUTY_MIN + DUTY_STEP`

## 推荐恢复

```verilog
// 修复:
if (duty <= DUTY_MIN + DUTY_STEP) begin
    state <= RAMP_UP;
```
