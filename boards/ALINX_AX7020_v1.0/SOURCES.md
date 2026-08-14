# Source Provenance — ALINX AX7020 v1.0 Board Configuration Package

## board_profile_ALINX_AX7020_v1.0.json

Machine-readable board facts derived from:
- `ALINX_AX7020_2023_1_PS_CONFIG` (authoritative PS7 preset)
- `ALINX_AX7020_SCHEMATIC_V2.0` (authoritative schematic)
- `ALINX_AX7020_README_CN` (board specifications)
- `ALINX_AX7020_PS_MIO_EXAMPLE_C` (cross-check)
- `ALINX_AX7020_FPGA_TUTORIAL_S1` (FPGA development reference)
- `ALINX_AX7020_VITIS_TUTORIAL_S2` (Vitis development reference)
- XC7Z020 datasheet (PL resources)
- Zynq-7000 TRM UG585 (QSPI linear window)

## ps7_preset.tcl

Byte-identical copy of:
- Source ID: `ALINX_AX7020_2023_1_PS_CONFIG`
- Distribution path: `course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl`
- Original SHA256: `sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299`
- 571 lines, 537 PCW parameters
- Copyright ALINX 2017 — redistribution permitted with notice retained

## board.xdc

Derived from:
- ALINX AX7020 schematic v2.0:
  - PL clock: 50MHz oscillator on U18
  - PL LEDs: J16/K16/M15/M14, active-low via VCCIO_35 sink
- Cross-checked against (all three independently confirm same pin assignments):
  - `hello_fpga/constraints/top.xdc` (G3 golden baseline)
  - `g9_hw_test/constraints/top.xdc`
  - `zynq_platforms/ax7020_base/constraints/led_pins.xdc`

Only PL clock + 4 PL LED constraints are included (GPIO vertical slice scope).
Excluded: PL UART, buttons, PS MIO constraints (Vivado-generated from PS7 config).

## Source Catalog SHA256 Reference

| Source ID | SHA256 |
|-----------|--------|
| `ALINX_AX7020_2023_1_PS_CONFIG` | `sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299` |
| `ALINX_AX7020_SCHEMATIC_V2.0` | `sha256:e35eb1b654774a6f314de0140f5608d38a1c6be72d1c3e1008f5e288ac83c87e` |
| `ALINX_AX7020_PS_MIO_EXAMPLE_C` | `sha256:90f4a2b0636f79c766a651352166f4c923314d07a508c00bd65e9786c139dd80` |
| `ALINX_AX7020_README_CN` | `sha256:0b16aa3f541cf3a963931a559e6c88555ff6c0e63c5ea31a55b4bd0df188459d` |
| `ALINX_AX7020_FPGA_TUTORIAL_S1` | `sha256:561d1b36ba7d83147868093c4ab21c1d1d52b41a7747b3a777de6fac584162f4` |
| `ALINX_AX7020_VITIS_TUTORIAL_S2` | `sha256:47d221e66649e03d30e441cd30f12e2b1e21d0274e46fc85e61d618d797d0b13` |
