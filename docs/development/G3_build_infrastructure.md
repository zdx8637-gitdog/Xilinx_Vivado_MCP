# G3 — Build Infrastructure (hello_fpga)

> 日期: 2026-07-31
> 状态: ✅ COMPLETE

## Objective

Create a minimal FPGA project with full Vivado build flow and establish a golden baseline.

## Project

`hello_fpga/` — 32-bit counter @ 50 MHz on AX7020 (XC7Z020CLG400-2)

| File | Purpose |
|------|---------|
| `rtl/top.v` | Top module (counter → LED) |
| `constraints/top.xdc` | Pinout + clock (50 MHz, U18) |
| `scripts/build.tcl` | Version-locked full build script |
| `scripts/run_g3.bat` | Batch launcher with Vivado env |

## Golden Baseline

| Metric | Value |
|--------|-------|
| WNS | +17.954 ns |
| TNS | 0.000 ns |
| LUTs | 2 / 53,200 |
| FFs | 28 / 106,400 |
| BRAM | 0 |
| DSP | 0 |
| BUFG | 1 |
| IO | 6 |

## Build Flow

```
synth_design → opt_design → place_design → phys_opt_design
→ route_design → report_timing_summary → write_bitstream
```

All reports stored in `hello_fpga/reports/`.
