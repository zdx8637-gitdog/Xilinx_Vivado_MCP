# F-BLD-001: Wrong Top Module Name

> **此文件仅供验证团队使用，不得提供给测试代理**

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-BLD-001 |
| 类别 | Build |
| 检测阶段 | Synthesis |
| 文件 | scripts/build.tcl |

## 注入的缺陷

`build.tcl` 中 top module 名从 `top` 改为不存在的 `wrong_top_name`:

```tcl
# 原始 (正确):
set_property top top [current_fileset]

# 故障 (buggy):
set_property top wrong_top_name [current_fileset]  ;# BUG
```

RTL 文件中不存在名为 `wrong_top_name` 的模块。

## 预期症状

1. **Synthesis**: FAIL — Vivado 报错 "Module wrong_top_name not found"
2. 构建在 synth_design 阶段立即中断

## 推荐诊断

测试代理应:
1. 运行 synth_design
2. 发现 top module 找不到的错误
3. 检查 build.tcl 中的 set_property top 行
4. 搜索 RTL 中的实际 module 名称 (top)
5. 修复为正确的 module 名

## 推荐恢复

```tcl
set_property top top [current_fileset]
```
