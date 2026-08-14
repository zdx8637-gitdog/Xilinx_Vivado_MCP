# B05 Platform/AXI — Public Contract

## Tool: `platform_generate`

- **Schema**: `{}` with `additionalProperties: false`
- **Stage admission**: `PLATFORM_DESIGN`
- **Stage advance**: `PL_GENERATE` on terminal SUCCEEDED
- **Operation lifecycle**: admission → accepted → RUNNING → terminal (SUCCEEDED/FAILED)
- **Worker lifecycle**: Vivado adapter started on first call via existing `SingleWorkerController`

## Terminal success evidence

On `SUCCEEDED`:
- `operation_id` present
- `completion_evidence.stage_advanced_from` = `PLATFORM_DESIGN`
- `completion_evidence.stage_advanced_to` = `PL_GENERATE`
- `output_artifact_revision` = platform_revision (sha256)
- Compact result includes: `xsa_path`, `xsa_sha256`, `wrapper_path`, `wrapper_sha256`, `manifest_path`, `manifest_sha256`, `platform_revision`, `address_map`

## Session context

- `create_session` initializes at `PLATFORM_DESIGN` with valid board profile SHA.
- On success, `context.platform_revision` is atomically published.
- `get_session_info({session_id})` returns `platform_revision` in data.

## Error codes

| Condition | Reason Code |
|-----------|-------------|
| Wrong stage | `STAGE_PREREQUISITE_UNMET` |
| Vivado not available | `ADAPTER_NOT_READY` |
| Board profile mismatch | `BOARD_PROFILE_MISMATCH` |
| BD validation failed | `BD_VALIDATION_FAILED` |
| XSA export failed | `XSA_EXPORT_FAILED` |
| Wrapper export failed | `WRAPPER_EXPORT_FAILED` |
| Manifest error | `MANIFEST_GENERATION_FAILED` |
| Vivado Tcl error | `ADAPTER_NOT_READY` |
