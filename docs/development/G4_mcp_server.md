# G4 — Vivado MCP Server

> 日期: 2026-07-31 – 2026-08-01
> 状态: ✅ COMPLETE

## Objective

Build the MCP infrastructure that bridges Claude Code to Vivado 2023.1.

## Architecture

```
Claude Code ←→ MCP stdio ←→ server.py ←→ VivadoTools ←→ VivadoProcess ←→ Vivado
```

## Key Components

| File | Lines | Purpose |
|------|-------|---------|
| `vivado_process.py` | ~460 | Subprocess lifecycle, completion marker, version guard |
| `config.py` | ~65 | Central config (paths, timeouts, version lock) |
| `models.py` | ~130 | Data models (dataclass) |
| `session.py` | ~70 | Design session context |
| `version_guard.py` | ~40 | Server-level version check |
| `tcl_templates.py` | ~240 | Tcl command templates |
| `vivado_tools.py` | ~370 | 12 structured tool implementations |
| `server.py` | ~270 | MCP stdio server entry point |

## G4.1 — AMD Official Source Review

Reviewed 1825-line official VivadoMCP server. Conclusion:
- MCP layer: 95% reusable
- Process control (pexpect): 0% reusable (Unix-only)
- Tool definitions: 70% reusable
- Overall: ~50% reuse rate

## G4.2 — Windows Vivado Process Adapter

Pure `subprocess.Popen` implementation. Uses `__FPGA_AGENT_DONE__` completion marker instead of fragile Vivado prompt regex. 7/7 smoketest PASS.

## G4.2.1 — Config Import Fix

Changed `from config import CONST` to `import config` + `config.CONST` — prevents import-time value snapshotting that masked the version guard test failure.

## G4.3 — MCP Server Layer

12 structured tools in Phase A:
- Query: `get_vivado_info`, `get_capabilities`, `get_cells`, `get_nets`, `get_clocks`, `get_ports`, `get_property`
- Project: `open_checkpoint`, `close_design`
- Analysis: `report_timing_summary`, `report_utilization`
- Admin: `run_tcl`

Architecture frozen in `docs/g4_3_architecture.md`.

## G4.4 — Protocol Testing

9/9 MCP protocol tests PASS: initialize, list_tools, call_tool, error propagation, shutdown.
