# F-PRJ-001: Wrong FPGA Part Number

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-PRJ-001 |
| 类别 | Project |
| 检测阶段 | opt_design / place_design |
| 文件 | scripts/build.tcl |

## 注入的缺陷

FPGA 器件型号从 `xc7z020clg400-2` 改为不兼容的 `xc7k160tfbg676-1`:

```tcl
# 原始 (正确):
create_project -force $proj_name $proj_path/vivado_project -part xc7z020clg400-2

# 故障 (buggy):
create_project -force $proj_name $proj_path/vivado_project -part xc7k160tfbg676-1  ;# BUG
```

XC7K160T 是 Kintex-7 系列，引脚封装完全不同 (TFBG676 vs CLG400)。
XDC 约束中的引脚号 (U18, N15, J16 等) 在 Kintex-7 封装中不存在。

## 预期症状

1. **Synth**: 可能通过（综合不检查引脚映射）
2. **opt_design / place_design**: FAIL — 引脚不匹配，PACKAGE_PIN 错误
3. Vivado 报错: I/O 端口与指定器件不兼容

## 推荐诊断

测试代理应:
1. 运行构建流程
2. 在 implementation 阶段发现器件不匹配错误
3. 检查 build.tcl 中的 -part 参数
4. 确认为 xc7z020clg400-2

## 推荐恢复

```tcl
create_project -force $proj_name $proj_path/vivado_project -part xc7z020clg400-2
```
