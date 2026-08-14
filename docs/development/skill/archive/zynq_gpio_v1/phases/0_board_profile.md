# Phase 0 — Board Profile Validation

> 输入: 用户需求中的 `board_id` | 输出: 验证通过的 session + board profile

## Skill 决策

- board_id 固定为 `ALINX_AX7020_v1.0`（当前唯一支持的板卡）
- 不需要 AI 选择板卡——直接使用这个 ID

## 执行序列

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `create_session` | `{"board_id": "ALINX_AX7020_v1.0", "project_path": "<工作目录>"}` | `status == "success"`, 记录 `session_id` |
| 2 | `get_execution_state` | `{}` | `current_stage == "PLATFORM_DESIGN"`, `execution_lane == "IDLE"` |

## 产物

- `session_id`（后续所有 domain tool 调用都需要携带此参数）
- `project_path`（所有产物将写入此目录）

## 失败恢复

| 症状 | 原因 | 动作 |
|------|------|------|
| `create_session` 失败 | board_id 不存在或已有活动执行上下文 | 检查 board_id；调用 `get_execution_state` 与 `diagnose_execution` 获取公开证据，禁止删除 runtime 文件 |
| `execution_lane != "IDLE"` | 前次 operation 或资源仍活动/待恢复 | 按 `recommended_action` 处理；只有诊断明确建议恢复时才调用 `recover_execution`，失败则停止并报告 |
| `current_stage != "PLATFORM_DESIGN"` | session 状态不对 | 调用 `close_session` 后重新 `create_session` |

## 参考

- Board Profile 数据: `boards/ALINX_AX7020_v1.0/board_profile_ALINX_AX7020_v1.0.json`
- board_profile_sha256: `sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18`
- 板卡信息: PL LED × 4 (active-low), UART1/COM4/115200, xc7z020clg400-2
