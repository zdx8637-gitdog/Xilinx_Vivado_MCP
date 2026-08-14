# Evidence Directory — R3.1-C Smoke

Agent3 populates this directory during phase black-box execution.

## Subdirectories

| Directory | Content | Agent3 fills |
|-----------|---------|-------------|
| `responses/` | Raw MCP ToolResult JSON per step | Yes |
| `state_traces/` | get_execution_state snapshots before/after operations | Yes |
| `operation_logs/` | operation_id → terminal tracking | Yes |
| `artifacts/` | Generated system_top.v + SHA | Yes |

## Template

See `report_template.md` for the Agent3 report format.
