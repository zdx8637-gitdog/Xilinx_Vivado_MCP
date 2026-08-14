# Task: Hardware Integration Validation

## Objective

Verify the complete FPGA hardware development loop using the MCP platform.

## Project

```
D:\fpgaproject\g9_hw_test\
```

This is a breathing LED design with UART debug output. Source files are in `rtl/` and `constraints/`.

## Hardware

- ALINX AX7020 board (XC7Z020CLG400-2)
- JTAG connected and powered
- UART debug on J11 Pin3 (F17), 115200 8N1

## Required Workflow

### Phase 1 — Build

Create the project and run the complete build flow through MCP tools. Do NOT use build scripts — use the individual MCP build tools. Generate a bitstream.

### Phase 2 — Hardware Programming

Connect to the hardware server, detect the FPGA device in the JTAG chain, and program the bitstream.

### Phase 3 — Runtime Verification

Read the UART debug output from the FPGA. Verify that duty cycle values change over time, confirming the breathing LED is running correctly.

### Phase 4 — Report

Provide a structured report covering Build, Program, and UART verification results.

## Available Tools

The MCP server provides hardware programming tools (`connect_hw_server`, `get_device_status`, `program_device`, `read_uart`, `list_serial_ports`) in addition to build, simulation, and analysis tools.
