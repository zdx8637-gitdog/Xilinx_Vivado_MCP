# F-BLD-002: Missing RTL Source

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-BLD-002 |
| 类别 | Build |
| 检测阶段 | Synthesis |
| 文件 | scripts/build.tcl |

## 注入的缺陷

`build.tcl` 中添加 RTL 源文件的命令被注释掉:

```tcl
# 原始 (正确):
add_files -fileset sources_1 [glob $rtl_dir/*.v]

# 故障 (buggy):
# BUG: RTL source not added
# add_files -fileset sources_1 [glob $rtl_dir/*.v]
```

约束文件仍然正常添加，但 RTL 源文件没有被添加到工程中。

## 预期症状

1. **Synthesis**: FAIL — Vivado 报错 "No source files" 或找不到顶层模块
2. 工程被创建但为空（只有约束没有 RTL）

## 推荐诊断

测试代理应:
1. 运行 synth_design
2. 发现工程缺少 RTL 源文件
3. 检查 build.tcl 中的 add_files 行
4. 取消注释 add_files 行

## 推荐恢复

```tcl
add_files -fileset sources_1 [glob $rtl_dir/*.v]
```
