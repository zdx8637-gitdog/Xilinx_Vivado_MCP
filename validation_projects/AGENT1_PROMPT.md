# Task: FPGA Project Baseline Verification

## Objective

Verify a complete FPGA development workflow on a reference design using the Vivado MCP toolchain.

## Project Location

```
D:\fpgaproject\validation_projects\golden\breath_led\
```

The project contains:
- RTL source files in `rtl/`
- Constraints in `constraints/`
- Testbench in `sim/`
- Build script in `scripts/`

## Required Workflow

Complete the following steps in order. Report results at each stage.

### Phase 1 — Simulation

1. Compile RTL and testbench sources using `compile_sim`
2. Elaborate the design using `elaborate_sim`
3. Run simulation using `run_simulation`
4. Report: assertion pass/fail counts, simulation time, any warnings or errors

### Phase 2 — Build

1. Execute the build script to run synthesis, optimization, placement, routing, and bitstream generation
2. Report: each stage status, any errors encountered

### Phase 3 — Analysis

Using the post-synthesis checkpoint from the build, collect:

1. Vivado version (`get_vivado_info`)
2. Device capabilities (`get_capabilities`)
3. Timing summary: WNS, TNS, WHS, THS, failing endpoints (`report_timing_summary`)
4. Resource utilization: LUT, FF, BRAM, DSP, BUFG, IO counts (`report_utilization`)
5. Clock information: names, periods, frequencies (`get_clocks`)
6. Top-level ports: names, directions (`get_ports`)
7. Cell statistics: total count, sequential count, top cell types (`get_cells`)

### Phase 4 — Report

Provide a structured summary:

- Simulation: PASS/FAIL with assertion details
- Build: PASS/FAIL with stage details
- Timing: all values, whether timing is met
- Utilization: resource table
- Any anomalies or issues found

## Expected Outcome

This is a known reference design. All phases should complete successfully. Report any deviations from expected behavior.

## Tools Available

The Vivado MCP server provides 16 tools including simulation, build execution, timing analysis, and design queries. Use them directly.
