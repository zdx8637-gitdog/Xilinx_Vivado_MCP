# B03 — Authoritative Asset & Source Inventory v0.2.2

> Brick: B03 Sub-step 0  |  日期: 2026-08-04  |  状态: **COMPLETE / FROZEN ✅**
> 下一动作: 子步骤1 (需先审核)

---

## 1. Vendor ps_config.tcl SHA256 Analysis

### 1.1 Overview

47 `ps_config.tcl` files across `AX7020_2023.1`, 43 unique SHA256 groups. Only 3 groups
have >1 member. Differences are peripheral-enable only; all platform-defining fields
(DDR, UART, QSPI, clock) are identical across every file targeting `clg400`.

### 1.2 Canonical Selection

**File**: `course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl`
**SHA256**: `sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299`
**Size**: 25,482 bytes, 571 lines, 537 PCW params

Rationale: simplest baseline (UART only), architecture doc reference, `clg400` package,
50 MHz FCLK0, ALINX copyright with redistribution permission.

---

## 2. Board Static Parameter Table

### 2.1 FPGA Device

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `vivado_part` | `xc7z020clg400-2` | `ALINX_AX7020_2023_1_PS_CONFIG` | `CONFIG.PCW_PACKAGE_NAME {clg400}` | ✅ |
| `board_id` | `ALINX_AX7020_v1.0` | Architecture doc Appendix B | — | ✅ |

### 2.2 DDR3

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `ddr_chip` | `MT41J256M16 RE-125` | ps_config.tcl | `CONFIG.PCW_UIPARAM_DDR_PARTNO` | ✅ |
| `ddr_chip_count` | 2 | README_CN.md §12 | "2× 4Gbit" | ✅ |
| `ddr_physical_bytes` | `1073741824` | Chip datasheet | 2×4Gbit = 8Gbit | ✅ |
| `ddr_configured_bytes` | `536870912` | ps_config.tcl | `PCW_DDR_RAM_HIGHADDR {0x1FFFFFFF}` | ✅ |
| `ddr_configured_highaddr` | `536870911` | Same | 0x1FFFFFFF decimal | ✅ |
| `ddr_frequency_hz` | `533333333` | ps_config.tcl | `PCW_UIPARAM_DDR_FREQ_MHZ {533.333333}`. Source: 533.333333 MHz. Conversion: ×1,000,000 = 533,333,333 Hz. ⚠ rounding documented. | ✅ |
| `ddr_bus_width_bits` | `32` | ps_config.tcl | `PCW_UIPARAM_DDR_BUS_WIDTH {32 Bit}` | ✅ |

### 2.3 QSPI Flash

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `qspi_chip` | `W25Q256` | README_CN.md §12 | — | ✅ |
| `qspi_physical_bytes` | `33554432` | Winbond datasheet | 256 Mbit | ✅ |
| `qspi_linear_window_bytes` | `16777216` | Zynq-7000 TRM UG585 §11.1 | Max x4 linear window | ✅ |
| `qspi_base_address` | `4227858432` | Zynq-7000 TRM | 0xFC000000 decimal | ✅ |
| `qspi_data_mode` | `x4` | ps_config.tcl | `CONFIG.PCW_SINGLE_QSPI_DATA_MODE {x4}` | ✅ |

### 2.4 PL LEDs

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `pl_leds.count` | 4 | README_CN.md §12 | "4 PL控制" | ✅ |
| `pl_leds.pins` | `["J16","K16","M15","M14"]` | 3 XDC files | `led_pins.xdc`, `hello_fpga/top.xdc`, `g9_hw_test/top.xdc` — all identical | ✅ 3-way |
| `pl_leds.polarity` | `active-low` | Schematic: VCCIO_35 → resistor/LED → FPGA pin; FPGA output low sinks current → LED ON | Write 0 = LED ON | ✅ |

> hello_fpga XDC and g9_hw_test XDC are byte-identical: `sha256:11cd1c79...`.

### 2.5 PS LEDs

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `ps_leds.count` | 2 | README_CN.md §12 | "2个PS控制" | ✅ |
| `ps_leds.mio_pins` | `[0, 13]` | Schematic page 4: `PS_MIO0_500 → MIO0_LED`, `PS_MIO13_500 → MIO13_LED` | helloworld.c indirectly confirms: operates GPIO 0 and GPIO 13 | ✅ schematic verified; code cross-checked |
| `ps_leds.polarity` | `active-low` | Schematic page 12: LED/resistor to VCC3V3; MIO low → sink current → LED ON | — | ✅ schematic verified |

**Prior error**: MIO[7,8] was incorrectly identified as PS LED. `MIO_TREE_SIGNALS` lists
`gpio[7]` and `gpio[8]` only as GPIO-muxed signals — they are general-purpose I/O, not
evidence of LED connection. The schematic is the authoritative source.

### 2.6 Clocks

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `pl_oscillator_hz` | `50000000` | Schematic + 3 XDC | `PACKAGE_PIN U18` + `create_clock -period 20.000` | ✅ |
| `pl_oscillator_pin` | `U18` | 3 XDC files | All confirm | ✅ |
| `ps_clock_hz` | `33333333` | Schematic | 33.333 MHz oscillator. ⚠ rounding: actual may be 100/3 MHz | ✅ |

### 2.7 PS UART

| Parameter | Value | Source | Locator | Status |
|-----------|-------|--------|---------|--------|
| `uart.controller` | `UART1` | ps_config.tcl | `CONFIG.PCW_UART1_PERIPHERAL_ENABLE {1}` | ✅ |
| `uart.mio_pins` | `[48, 49]` | ps_config.tcl | `CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49}` | ✅ |
| `uart.default_baud` | `115200` | ps_config.tcl | `CONFIG.PCW_UART1_BAUD_RATE {115200}` | ✅ |
| `uart.usb_bridge_chip` | `CP2102-GM` | Schematic page 13 | — | ✅ |
| `uart.usb_bridge_family` | `CP210x` | Silicon Labs driver | Windows enumeration: "Silicon Labs CP210x USB to UART Bridge" | ✅ |
| `uart.usb_vid` | `0x10C4` | Live USB enumeration | `USB\VID_10C4&PID_EA60\...` | ✅ |
| `uart.usb_pid` | `0xEA60` | Live USB enumeration | Same | ✅ |

### 2.8 Three USB Interfaces — Distinct Roles

| Interface | Chip | VID:PID | Role | Board Profile Status |
|-----------|------|---------|------|---------------------|
| **PS UART** | CP2102-GM (CP210x family) | `10C4:EA60` | Bidirectional UART1 on MIO[48,49] for ARM printf/control | ✅ Board static fact — VID/PID goes in profile; COM port is runtime |
| **PL UART** | CH340 (external) | `1A86:7523` | Receive-only: FPGA TX only (pin0=GND, pin3=TX per user wiring). Used for PL-side debug output capture | `USER_OBSERVED / LAB_FIXTURE` — not a board static fact; connection details are per-session |
| **JTAG** | FT232HL | N/A (not enumerated in B03) | Schematic page 3; Windows shows "USB Serial Converter" | Recorded for B04 handoff only; no JTAG enumeration in B03 scope |

> COM port numbers (COM3/COM4/COM5) are runtime state — never in Board Package.
> PL UART pin connection details are `LAB_FIXTURE` — not a standard board interface;
> connector numbering not yet standardized for automated use.

### 2.9 Reference: Ethernet, SD, USB, PL Resources

| Parameter | Value | Source |
|-----------|-------|--------|
| Ethernet | GEM0 → RTL8211E, MIO 16-27, RGMII | Architecture doc + ps_config.tcl |
| SD | SDIO0, MIO 40-45 | Architecture doc + ps_config.tcl |
| USB OTG | USB0, MIO 28-39 | Architecture doc + ps_config.tcl |
| PL LUTs/FFs/BRAM/DSP | 53,200 / 106,400 / 140 / 220 | XC7Z020 datasheet |

---

## 3. Source Catalog

```json
"source_catalog": [
  {"source_id": "ALINX_AX7020_2023_1_PS_CONFIG",
   "distribution_path": "course_s2_vitis/08_ps_uart/Vivado/auto_create_project/ps_config.tcl",
   "sha256": "sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299",
   "role": "authoritative_ps7_preset",
   "notes": "571 lines, 537 PCW params. Copyright ALINX 2017 — redistribution permitted with notice retained."},
  {"source_id": "ALINX_AX7020_SCHEMATIC_V2.0",
   "distribution_path": "hardware/01_SCH/AX7020开发板原理图V2.0.pdf",
   "sha256": "sha256:e35eb1b654774a6f314de0140f5608d38a1c6be72d1c3e1008f5e288ac83c87e",
   "role": "authoritative_schematic",
   "notes": "Page 4: PS LED MIO0/MIO13. Page 12: LED driver (active-low via VCC3V3 sink). Page 13: CP2102-GM PS UART bridge. Page 3: FT232HL JTAG."},
  {"source_id": "ALINX_AX7020_PS_MIO_EXAMPLE_C",
   "distribution_path": "course_s2_vitis/03_ps_mio/Vitis/auto_create_vitis/src/ps_led/helloworld.c",
   "sha256": "sha256:90f4a2b0636f79c766a651352166f4c923314d07a508c00bd65e9786c139dd80",
   "role": "ps_led_cross_check",
   "notes": "Lines 83-96: configures GPIO 0 and GPIO 13 as outputs; toggles 0x0/0x1. Code itself does not document which value lights the LED — polarity is from schematic."},
  {"source_id": "ALINX_AX7020_README_CN",
   "distribution_path": "README_CN.md",
   "sha256": "sha256:0b16aa3f541cf3a963931a559e6c88555ff6c0e63c5ea31a55b4bd0df188459d",
   "role": "board_specifications_reference"},
  {"source_id": "ALINX_AX7020_README_EN",
   "distribution_path": "README.md",
   "sha256": "sha256:b2eef2a3e6f131239ac50f854285a46670cfb0e932b8ef2c1545593234348e3c",
   "role": "board_specifications_reference_en"},
  {"source_id": "ALINX_AX7020_FPGA_TUTORIAL_S1",
   "distribution_path": "cource_s1_ALINX_ZYNQ(AX7020)2023开发平台FPGA教程V1.01.pdf",
   "sha256": "sha256:561d1b36ba7d83147868093c4ab21c1d1d52b41a7747b3a777de6fac584162f4",
   "role": "fpga_development_reference"},
  {"source_id": "ALINX_AX7020_VITIS_TUTORIAL_S2",
   "distribution_path": "course_s2_ALINX_ZYNQ(AX7010_AX7020)2023开发平台Vitis应用教程V1.01.pdf",
   "sha256": "sha256:47d221e66649e03d30e441cd30f12e2b1e21d0274e46fc85e61d618d797d0b13",
   "role": "vitis_development_reference"}
]
```

---

## 4. board.xdc Constraint Provenance

| Constraint | Source | Evidence |
|-----------|--------|----------|
| PL LED pins J16/K16/M15/M14 + LVCMOS33 | Schematic | 3-way XDC cross-check |
| PL clock pin U18 + 20ns period | Schematic + oscillator spec | 3-way XDC cross-check |

**Excluded**: PL UART pin F17, RST_N pin N15, PS MIO constraints (Vivado-generated).

---

## 5. PS7 Preset Copyright

Vendor header permits use and redistribution with copyright notice retained.
Sub-step 1 copy MUST retain header verbatim.

---

## 6. USB Enumeration Evidence

| Device | COM | VID:PID | Status | Notes |
|--------|-----|---------|--------|-------|
| Silicon Labs CP210x (CP2102-GM) | COM4 | `10C4:EA60` | Active | AX7020 PS UART |
| Silicon Labs CP210x | COM3 | `10C4:EA60` | Disconnected | Second CP210x device |
| CH340 | COM5 | `1A86:7523` | Active | PL UART — external/lab fixture |
| ACPI PNP0501 | COM1 | N/A | — | Standard serial port |

---

## 7. EDA Environment (Host Baseline — NOT Board Static Facts)

| Tool | Discovered At | PATH | Status |
|------|--------------|------|--------|
| Vivado 2023.1 | `D:\Xilinx\Vivado\2023.1\bin\vivado.bat` + `settings64.bat` | NOT on PATH | ✅ Installed at `D:\Xilinx`; build number deferred to sub-step 2 env probe |
| Vitis 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\vitis.bat` | NOT on PATH | ✅ Installed at `D:\Xilinx` |
| XSCT 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\xsct.bat` | NOT on PATH | ✅ Installed at `D:\Xilinx` (bundled with Vitis) |
| Python 3.12 | `C:\Users\zdx86\AppData\Local\Programs\Python\Python312\python.EXE` | — | ✅ (B02 baseline) |

> `D:\Xilinx` is the host install root. This is NOT a board static fact.
> env_probe must support injectable search roots (not only PATH + `C:\Xilinx`).

---

## 8. Sub-Step 0 Gate Status

- [x] All Board Profile static fields have authoritative sources
- [x] Canonical ps_config.tcl selected with SHA256
- [x] LED pins cross-checked across ≥3 independent XDC files
- [x] PL clock pin + frequency verified
- [x] PS LED: count=2, MIO[0,13] — schematic verified, MIO mapping cross-checked with helloworld.c
- [x] PS LED polarity: active-low — schematic verified (VCC3V3 sink through LED/resistor to MIO)
- [x] PS UART: CP2102-GM, VID/PID 10C4:EA60 verified
- [x] Three USB interfaces distinguished (PS UART, PL UART/lab fixture, JTAG)
- [x] USB-UART VID/PID verified via live enumeration
- [x] DDR, QSPI, clock parameters verified from vendor sources
- [x] ps_config.tcl copyright permits redistribution
- [x] Machine numeric conversion rules documented
- [x] EDA tools discovered at `D:\Xilinx` (host baseline; not in Board Package)
- [x] No production files or code created; no vendor files copied; no JTAG enumeration; no Vivado GUI

**Gate result**: ALL ITEMS VERIFIED. Sub-step 0 is COMPLETE. ✅

---

## 9. Host Media Root (reference only)

```
Vendor distribution:  D:\BaiduNetdiskDownload\AX7020_2023.1\
Project boardinfo:    D:\fpgaproject\docs\boardinformation\
Project zynq_plat:    D:\fpgaproject\zynq_platforms\ax7020_base\
EDA install:          D:\Xilinx\
```
