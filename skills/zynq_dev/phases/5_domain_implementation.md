# S5 — 分域实现（Domain Implementation）

> 输入: 已批准方案 | 输出: Platform XSA/Manifest → PL bitstream/Manifest → PS ELF/Manifest（全部 MCP 自动发布）

## 职责

按批准方案依次实现三个域。**全部 EDA/构建行为必须经过公开 MCP command 原子**
（执行纪律见 [appendix_mechanics.md](../appendix_mechanics.md)）。每个 command
调用后记录 `operation_id`，用 `wait_operation`/`get_operation_status` 保存真实
Ledger 观测时间线；终态不是 `SUCCEEDED`（Manifest 产物型还需
`artifact_state == "PUBLISHED"`）时立即停止串行链。

## 5.1 Platform（BD 原子序列）

按 [appendix_mechanics.md](../appendix_mechanics.md)「platform 原子序列模板」执行：
创建工程 → PS7 → 配置 → 实例化 IP → 连线 → 时钟/复位 → 地址 → 校验 → wrapper
→ XSA → Manifest。IP 选型、配置参数、连线关系、地址段**全部来自 S3 决策与需求
文档**（占位符），本 Skill 不预设任何具体外设。

产物：`<XSA_PATH>` + wrapper + Platform Manifest（含 `platform_revision` 与
`address_map`，S6 要用）。

## 5.2 PL（构建链）

按附录「PL 构建链」执行：`pl_generate_system_top` → 需求约束文件（写入
`<PROJECT_PATH>` 下，允许的工作区操作）→ `pl_create_project`（sources 含 BD +
wrapper + top；constraints；top 由方案决定）→ `pl_generate_target` →
`pl_synthesize` → `pl_place` → `pl_route` → `pl_analyze_timing`（timing 通过）
→ `pl_generate_bitstream`（产出 `<BITSTREAM_PATH>`）→ PL Manifest 自动发布。

> **约束/综合实现检查纪律（硬性，违反即视为实现不合格）：**
>
> 1. **XDC 注释必须独占行**：行内 `#` 会被 Vivado 误解析为 option 值，触发
>    `Common 17-161 Invalid option value '#' for 'objects'` 并使该端口约束失效
>    （进而 impl 报 `UCIO-1` 未约束端口、write_bitstream 失败——已两次踩坑）。
>    所有 XDC 注释必须以独占一行（`# ...`）书写，**禁止** 在
>    `set_property ... # 注释` 后追加行内注释。
> 2. **综合/实现后必须检查多驱动与未约束端口警告**：`pl_synthesize` /
>    `pl_place` / `pl_route` 成功后，必须核对对 Log 中的
>    `[Synth 8-XXXX] multiple drivers` 类多驱动警告与 `[DRC UCIO-1]` /
>    `[Common 17-XXXX]` 未约束端口警告。RTL 多驱动曾**静默成活板 bug**
>    （不报错、时序通过但行为错误）。任一此类警告都必须先定位到具体
>    端口/信号并确认无数据冲突，才允许继续下一阶段；无法确认时视为失败。

## 5.3 PS（软件链）

按附录「PS 软件链」执行：`ps_import_hardware`（XSA staging 规避同文件冲突）→
`ps_create_platform` → `ps_create_bsp` → `ps_create_app` → **自写程序源码**
（`<PROGRAM_SOURCE>`，按需求文档的判定规范编写）→ `ps_add_sources` →
`ps_compile`（唯一正式编译入口）→ `ps_get_build_status`（取 `<ELF_PATH>`）→
`ps_read_elf_info` 校验 → PS Manifest 自动发布。

## 智能体自主决策范围

- 各域实现全部细节：IP 配置、RTL、驱动、参数、代码、约束（工程层全归智能体）。

## 用户必须提供的物理事实

- 无（S1–S4 已锁定；涉及板级改动回 S1）。

## 失败恢复入口

| 症状 | 动作 |
|------|------|
| 任一域构建失败 | 按 S8 错误分类定位域；修复输入后从对应域重跑公开序列 |
| Manifest 终态门禁失败 | 保留证据并停止；不得手工补 Manifest |
| `TIMED_OUT` / `OUTCOME_UNKNOWN` | `diagnose_execution`；仅按 `recommended_action` 恢复 |

## 涉及的工具类别

- platform command 原子：`platform_create_design`、`platform_add_ps7`、
  `platform_configure_ps7`、`platform_add_ip`、`platform_connect_interface`、
  `platform_connect_clock`、`platform_connect_reset`、`platform_set_address`、
  `platform_validate`、`platform_generate_wrapper`、`platform_export_hardware`、
  `platform_export_manifest`；
- pl command：`pl_generate_system_top`、`pl_create_project`、`pl_generate_target`、
  `pl_synthesize`、`pl_place`、`pl_route`、`pl_analyze_timing`、`pl_generate_bitstream`；
- ps command：`ps_import_hardware`、`ps_create_platform`、`ps_create_bsp`、
  `ps_create_app`、`ps_add_sources`、`ps_compile`、`ps_get_build_status`、
  `ps_read_elf_info`。
