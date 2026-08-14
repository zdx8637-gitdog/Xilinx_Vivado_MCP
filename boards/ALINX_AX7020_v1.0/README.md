# ALINX AX7020 v1.0 — Board Configuration Package

## Board Identification

- **Board ID**: `ALINX_AX7020_v1.0`
- **Chip**: XC7Z020-2CLG400I (Zynq-7020)
- **Vivado Part**: `xc7z020clg400-2`
- **Package**: clg400, speed grade -2

## Memory

### DDR3
- Chip: MT41J256M16 RE-125 × 2
- Physical: 1 GB (8 Gbit)
- Configured: 512 MB (HIGHADDR = 0x1FFFFFFF)
- Frequency: 533.333 MHz (DDR-1066)
- Bus width: 32-bit

### QSPI Flash
- Chip: W25Q256 (256 Mbit / 32 MB)
- Linear window: 16 MB (0xFC000000–0xFCFFFFFF)
- Data mode: x4

## Clocks

- PS clock: 33.333 MHz (on-board oscillator)
- PL oscillator: 50 MHz (on-board, pin U18)

## LEDs

- PL LEDs: 4, active-low (J16/LED3, K16/LED2, M15/LED1, M14/LED0)
  - FPGA pin sinks current through resistor/LED to VCCIO_35
  - Write 0 = ON, Write 1 = OFF
- PS LEDs: 2, active-low (MIO0, MIO13)
  - MIO pin sinks current through resistor/LED to VCC3V3
  - MIO low = ON, MIO high = OFF

## UART

- Controller: UART1
- MIO pins: 48 (TX), 49 (RX)
- Default baud: 115200
- USB bridge: CP2102-GM (Silicon Labs CP210x family)
- USB VID/PID: 0x10C4 / 0xEA60

## PL Resources

- 53,200 LUTs / 106,400 FFs / 140 BRAM36 / 220 DSP48E1

## Peripherals (reference)

- Ethernet: GEM0 → RTL8211E, MIO 16–27, RGMII
- SD: SDIO0, MIO 40–45
- USB OTG: USB0, MIO 28–39

## Authoritative Sources

- PS7 preset: `course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl`
- Schematic: `hardware/01_SCH/AX7020开发板原理图V2.0.pdf`
- Tutorials: ALINX S1 (FPGA) + S2 (Vitis)
