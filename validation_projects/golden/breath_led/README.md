# Golden: breath_led (PWM Breathing LED)

> 类别: Golden Reference
> 目标: XC7Z020CLG400-2 (ALINX AX7020)

---

## 预期行为

1. **Synthesis**: 0 errors
2. **Implementation**: opt→place→route all pass
3. **Timing**: WNS > 0 ns (positive slack)
4. **Utilization**: ~64 LUT, ~75 FF
5. **Simulation**: All assertions PASS
6. **Bitstream**: Generated successfully

---

## 验收标准

| 阶段 | 预期结果 |
|------|----------|
| xvlog | returncode=0 |
| xelab | returncode=0 |
| xsim | returncode=0, 5+ PASS assertions |
| synth_design | 0 errors |
| place_design | 0 errors |
| route_design | 0 errors |
| report_timing_summary | WNS > 0, failing=0 |
| write_bitstream | 成功生成 .bit |

---

## 设计参数

- PWM 频率: ~200 Hz
- Duty 范围: ~10% – ~90%
- 呼吸周期: ~2 秒
- 时钟: 50 MHz (sys_clk, pin U18)
