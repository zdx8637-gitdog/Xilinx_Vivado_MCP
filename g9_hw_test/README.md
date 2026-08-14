# G9 Hardware Loop Test Project

## Purpose

Verify the complete FPGA hardware development loop through MCP:

```
Build → Program → UART Debug → Verify
```

## Hardware Requirements

- ALINX AX7020 (XC7Z020CLG400-2)
- JTAG connected (Xilinx Platform Cable)
- USB-UART adapter on J11 Pin1(GND) + Pin3(F17), 115200 8N1

## Test Flow

### Phase 1: Build
Create project → Synthesis → Placement → Routing → Bitstream

### Phase 2: Program
connect_hw_server → get_device_status → program_device

### Phase 3: Debug
read_uart → monitor duty cycle values → confirm breathing pattern

## UART Debug Output

Expected: `DUTY=XXXXXXXX\r\n` every 500ms
Duty ranges from 0x19999999 (~10%) to 0xE6666666 (~90%) in a ~2 second breathing cycle.

## Run via MCP

```
test_g9_hardware.py
```
