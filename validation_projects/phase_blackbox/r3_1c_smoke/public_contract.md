# R3.1-C Public Contract

## Public MCP Tools (list_tools = 10)

1. `create_session` — Create a new Zynq development session
2. `close_session` — Close a Zynq session
3. `get_session_info` — Get metadata for an active Zynq session
4. `get_capabilities` — Get Zynq MCP capability declaration
5. `get_operation_status` — Get status of an operation by ID
6. `wait_operation` — Wait for an operation to complete (bounded, max 300s)
7. `get_execution_state` — Get full execution state
8. `diagnose_execution` — Return structured diagnosis
9. `recover_execution` — Attempt recovery from RECOVERY_REQUIRED
10. **`pl_generate_system_top`** — Generate system_top.v instantiating BD wrapper

## pl_generate_system_top Schema

```json
{
  "name": "pl_generate_system_top",
  "inputSchema": {
    "type": "object",
    "properties": {
      "wrapper_path": {"type": "string", "minLength": 1}
    },
    "required": ["wrapper_path"],
    "additionalProperties": false
  }
}
```

## Operation Lifecycle

- Command returns `{"status": "success", "data": {"operation_id": "...", "status": "accepted"}}`
- Terminal status via `wait_operation` or polled `get_operation_status`
- SUCCEEDED: lane=IDLE, stage advances from PL_GENERATE→PL_BUILD, worker=ABSENT
- FAILED: lane=IDLE, stage unchanged, reason_code in error details
- Admission rejection: direct error response, no operation_id, lane/stage unchanged

## Stage Gates

- Pre-stage: `PL_GENERATE` (exactly)
- Other stages: `STAGE_PREREQUISITE_UNMET`
