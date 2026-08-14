# G5 — Software Closed Loop

> 日期: 2026-08-01
> 状态: ✅ COMPLETE

## Objective

Close the full chain: Claude Code → MCP → Vivado → Build → Program → Verify.

## G5.1 — MCP Registration

Registered `server.py` as Claude Code MCP server via `.mcp.json` + `launch_server.bat`. Verified connectivity: 12 tools available through MCP protocol.

## G5.2 — Build Tool Implementation

Extended Phase A tools to Phase B: `create_project`, `synth_design`, `place_design`, `route_design`, `write_bitstream`. Enabled granular build control vs. monolithic `build.tcl`.

## G5.3 — End-to-End Validation

Complete FPGA development cycle tested:
- RTL Design: `breath_led.v` (PWM breathing LED)
- Build: synthesis → implementation → bitstream
- MCP Analysis: timing, utilization, cells, ports
- Hardware: JTAG programming (xc7z020_1) → DONE=HIGH
- UART: J11 pin F17 → COM5 → `DUTY=XXXXXXXX` confirmed

10/10 validation checks PASS. Full report in `docs/g5_3_report.md`.

## Key Fixes

- JTAG device selection: filter `PART =~ *xc7z020*` to exclude ARM DAP
- `hierarchical` default changed to `True` for `get_cells`, `get_nets`
- Utilization parser updated for Vivado 2023.1 6-column format
