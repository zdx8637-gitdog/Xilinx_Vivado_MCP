# G9 — Hardware Integration Loop

> 日期: 2026-08-02
> 状态: ✅ COMPLETE

## Objective

Add JTAG programming and UART debug monitoring as first-class MCP tools.

## Hardware Tools

| Tool | Purpose | Layer |
|------|---------|:--:|
| `connect_hw_server` | Open Vivado HW Manager, connect to local hw_server | VivadoTools |
| `get_device_status` | Scan JTAG chain, report device DONE status | VivadoTools |
| `program_device` | Program bitstream to FPGA via JTAG | VivadoTools |
| `read_uart` | Read serial port for FPGA runtime logs | HwTools |
| `list_serial_ports` | List available COM ports | HwTools |

## JTAG Chain

AX7020 JTAG chain: `arm_dap_0` (Zynq PS) + `xc7z020_1` (FPGA fabric). Device selection uses `PART =~ *xc7z020*` filter.

## UART Debug

- ARM UART: COM4 (CP2102 → PS MIO48/49, 115200 8N1)
- PL UART: J11 Pin1(GND) + Pin3(F17), external CH340 → COM5

## Test Results

10/10 hardware validation checks PASS. `program_device` → DONE=HIGH. `read_uart` → COM5 confirmed DUTY breathing data.

## Crash Recovery

`VivadoProcess.send_tcl()` auto-restarts Vivado on crash (uses `is_running` property + catches `OSError`). Tested: kill Vivado → next `send_tcl` auto-restarts successfully.

## Total Tools

27 MCP tools: 12 query + 5 build + 1 validate + 4 sim + 5 hardware.
