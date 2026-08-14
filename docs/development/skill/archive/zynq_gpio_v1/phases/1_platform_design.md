# Phase 1 — Platform Design

> 输入: session_id + project_path | 输出: Platform XSA + Platform Manifest

## Skill 决策

- 使用快捷路径 `platform_generate {}`——一键生成 PS7 + SmartConnect + 4-bit AXI GPIO Block Design
- GPIO 地址固定为 `0x41200000`（由 platform_generate 内部保证）
- 不需要 AI 选择 IP 或配置参数

## 执行序列

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `platform_generate` | `{}` (session_id 由 transport 携带) | `status == "success"`, 记录 `operation_id` |
| 2 | `wait_operation` | `{"operation_id": "<op_id>", "timeout_s": 900}` | `status == "SUCCEEDED"` |
| 3 | `get_execution_state` | `{}` | `current_stage == "PL_GENERATE"` |

## 产物验证

`wait_operation` 返回的 `result.data` 中包含：

| 字段 | 含义 | 验证 |
|------|------|------|
| `xsa_path` | Platform XSA 绝对路径 | 文件存在 |
| `xsa_sha256` | XSA 文件 SHA256 | 与磁盘计算一致 |
| `wrapper_path` | BD wrapper Verilog 绝对路径 | 文件存在 |
| `wrapper_rel` | BD wrapper 相对路径 | **记下此值，Phase 2 要用** |
| `wrapper_sha256` | wrapper 文件 SHA256 | 与磁盘计算一致 |
| `manifest_path` | Platform Manifest 绝对路径 | **记下此路径，Phase 4 要用** |
| `manifest_sha256` | manifest 文件 SHA256 | 与磁盘计算一致 |
| `platform_revision` | 平台版本标识 (sha256:...) | **记下此值，全程需要** |
| `address_map.axi_gpio_led.base` | 应为 `0x41200000` | 确认地址正确 |

## 失败恢复

| 症状 | reason_code | 动作 |
|------|------------|------|
| admission 拒绝 | `STAGE_PREREQUISITE_UNMET` | 当前 stage 不对——回 Phase 0 重开 session |
| `ADAPTER_NOT_READY` | 受控 Vivado backend 不可用 | 调用 `diagnose_execution`，按公开 `recommended_action` 报告环境问题；不得自行启动 Vivado |
| `BD_VALIDATION_FAILED` | Block Design 验证失败 | 检查 Tcl 输出中的 error/critical warning |
| `XSA_EXPORT_FAILED` | XSA 导出失败 | 检查磁盘空间，确认工程目录可写 |
| `wait_operation` 返回 `wait_timed_out=true` 且仍 `RUNNING` | 本轮等待预算用尽，Operation 仍在后端执行 | 保存 `status_source/vendor_status/current_step/last_progress_at`；按 `recommended_action` 继续 WAIT 或 DIAGNOSE，禁止重复提交 |

## 说明

`platform_generate {}` 内部完成了 BD 创建 + PS7 配置 + AXI GPIO + SmartConnect + 地址分配 + 验证 + wrapper + XSA 导出 + manifest 生成。AI 不需要知道内部细节。

> 注意：此步骤由 MCP Controller 启动并拥有 Vivado backend（5-10 分钟）。
> 智能体只观察公开 Operation；BD 综合是 `platform_generate` 的一部分。

**⏱ 超时预算**：内层 `platform_generate` op 由 worker 强制上限 **600s**；外层 `wait_operation` 的超时必须**显著大于**内层（每轮内部轮询有 0.5-1s 间隔开销）。规则：外层 ≥ 内层 + 30s。本例：内层 600s → 外层 900s（`wait_operation` 服务端上限）。
