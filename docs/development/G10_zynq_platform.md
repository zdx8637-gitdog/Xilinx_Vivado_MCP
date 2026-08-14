# G10 — Zynq-7000 Platform Integration

> 日期: 2026-08-02 – 2026-08-03
> 状态: ✅ Architecture Validated (G10.5 BOOT.BIN deferred)

## Objective

Establish the AX7020 Base Platform — a Zynq-7000 PS↔PL communication platform that future projects extend.

## Platform Architecture

```
PS (ARM Cortex-A9)              PL (FPGA)
─────────────────               ────────
UART1 (MIO48/49) → COM4
M_AXI_GP0 → AXI Interconnect → axi_gpio_led (4-bit output → LED pins)
                              → axi_gpio_status (32-bit input ← FPGA status)
FCLK_CLK0 (50 MHz) → PL clock
```

## G10.1 — Block Design ✅

14-step BD creation validated step-by-step via MCP `run_tcl`:
- PS7 configured: UART1 MIO48/49, M_AXI_GP0, 50 MHz FCLK
- AXI GPIO LED: 4-bit output @ 0x4120_0000
- AXI GPIO Status: 32-bit input @ 0x4121_0000
- Clock/Reset trees connected
- `validate_bd_design` PASS

## G10.2 — Build ✅

Full Zynq flow: `generate_target` → wrapper → `synth_design` → `place_design` → `route_design` → `write_bitstream`
- Synthesis: 0 errors, 0 critical warnings
- Implementation: WNS=13.8ns, TNS=0

## G10.3 — ARM Application ✅

Vitis bare-metal C application compiled:
- Running light patterns: LED[0]=2Hz, LED[1]=0.5Hz, LED[2]=heartbeat, LED[3]=runner
- Uses Xil_Out32 to AXI GPIO, xil_printf to UART1
- ELF entry: 0x00100000 (DDR)

## G10.4 — PS Init ✅

`ps7_init` verified via XSCT: level shifters enabled (LVL_SHFTR_EN=0x0F), AXI bridges configured.

## G10.5 — BOOT.BIN ⚠️ Deferred

### Root Cause Found
`-process_bitstream bin` flag caused bootgen to silently produce no output. Removed flag → BOOT.BIN generated (4,149,832 bytes).

### Remaining Issue
ARM app not reaching `main()` when booted from SD card. XSCT JTAG download also needs proper `ps7_init` → `dow` → `con` sequence (being addressed in G11.0).

### Decision
G10.5 split into independent issue. BOOT.BIN packaging belongs to release pipeline (G12), not development workflow (G9 JTAG).

## Key Learnings

- Zynq BD flow requires `launch_runs` (not direct `synth_design` for IP-based designs)
- Pin constraints must be after synthesis (ports only exist in elaborated design)
- `generate_target all` required for BD IP output products before synthesis
- XSCT manual download must follow: `fpga -f` → APU `ps7_init` → A9 `dow` → `rwr pc` → `con`

## Platform Files

```
zynq_platforms/ax7020_base/
├── block_design/create_platform.tcl   ← BD creation script
├── block_design/build_g10.tcl         ← Full build script
├── constraints/led_pins.xdc           ← LED pin assignments
├── xsa/ax7020_base.xsa               ← Hardware platform export
├── G10_PLAN.md                        ← Validation plan
└── G10_PL_uart_addon.md               ← PL UART add-on spec (for other agents)
```

---

## Handoff to G11 / G12

### What G10 Delivers ✅

| Asset | Location | Status |
|-------|----------|:--:|
| BD design (PS7 + AXI GPIO) | `zynq_platforms/ax7020_base/` | ✅ Validated (14/14 PASS) |
| Bitstream (ps_led.bit) | `g10_build/` | ✅ Built (WNS=13.8ns) |
| XSA hardware platform | `xsa/ax7020_base.xsa` | ✅ Exported |
| ARM application ELF | `vitis_workspace/ps_led_test/Debug/` | ✅ Compiled |
| FSBL ELF | `vitis_workspace/ax7020_platform/zynq_fsbl/` | ✅ Auto-generated |
| PS7 init verified | Level shifter = 0x0F | ✅ XSCT confirmed |
| AXI GPIO address | LED @ 0x41200000, Status @ 0x41210000 | ✅ xparameters.h confirmed |

### What G10 Does NOT Deliver (Explicitly Deferred)

| Item | Deferred To | Reason |
|------|:--:|------|
| ARM app running on hardware | G11 | Needs JTAG download workflow |
| `init_platform()` / UART init | G11 | Part of ARM software boot sequence |
| BOOT.BIN packaging | G12 | Release pipeline, not development |
| SD card / QSPI boot | G12 | Same as above |
| XSCT automated download | G11 | G11.0 reverse engineering first |

### Bootgen Issue Context

- Root cause: `-process_bitstream bin` flag → silent output failure
- Fix: remove the flag → BOOT.BIN generates correctly (4,149,832 bytes)
- This issue belongs to G12 and does NOT block G11 JTAG development

### Responsibility Boundary

```
G10 (this phase)                    G11 (next)                       G12 (future)
─────────────────                   ────────                         ──────
Hardware platform                   ARM software workflow             Release packaging
  BD design ✅                        JTAG download (G11.0)            BOOT.BIN generation
  Synthesis ✅                        XSCT command sequence            QSPI flash programming
  Bitstream ✅                        launch_on_hardware()             SD card boot verification
  XSA export ✅                      ARM UART init
  ARM ELF compilation ✅             ARM debug (XSDB)
  PS init verification ✅
```
