# F-CON-002: Invalid Pin Assignment

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-CON-002 |
| 类别 | Constraint |
| 检测阶段 | Synthesis / Implementation |
| 文件 | constraints/top.xdc |

## 注入的缺陷

`sys_clk` 的引脚号从 U18 改为不存在的 Z99:

```xdc
# 原始 (正确):
set_property PACKAGE_PIN U18 [get_ports sys_clk]

# 故障 (buggy):
set_property PACKAGE_PIN Z99 [get_ports sys_clk]
```

XC7Z020CLG400-2 封装没有 Z99 这个引脚。

## 预期症状

1. **Synthesis** 或 **opt_design**: FAIL
2. Vivado 报错: "PACKAGE_PIN Z99 is not a valid site" 或类似消息
3. 构建流程在综合后阶段中断

## 推荐诊断

测试代理应:
1. 运行 synth_design
2. 发现引脚分配错误
3. 检查 XDC 文件中的 PACKAGE_PIN 值
4. 查找正确的引脚号 (查阅板级文档或原理图确定 U18)
5. 修复为 U18

## 推荐恢复

```xdc
set_property PACKAGE_PIN U18 [get_ports sys_clk]
```
