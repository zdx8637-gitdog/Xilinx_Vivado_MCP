# Task: FPGA Project Diagnostic Assessment

## Objective

Perform independent diagnostic assessment on a set of FPGA projects using the Vivado MCP toolchain.

## Project Location

```
D:\fpgaproject\validation_projects\designs\
```

This directory contains multiple subdirectories organized by category. Each subdirectory contains an independent FPGA project with:
- RTL source files in `rtl/`
- Constraints in `constraints/`
- Testbench in `sim/`
- Build script in `scripts/`

## Required Workflow

For each project, perform the following diagnostic steps:

### Step 1 — Simulation

Compile, elaborate, and run simulation. Record assertion results.

### Step 2 — Build (if applicable)

Execute the build script to attempt synthesis, implementation, and bitstream generation. If any stage fails, record the failure stage and error details.

### Step 3 — Analysis (if build reaches analysis-capable stage)

For projects that complete synthesis, collect timing summary, resource utilization, clock information, and port information.

### Step 4 — Diagnostic Report

For each project, provide:

1. Project path
2. Simulation result (PASS/FAIL, assertion details)
3. Build result (PASS/FAIL, which stage failed, error summary)
4. Analysis data (if available)
5. Root cause assessment — what is preventing this project from completing successfully?

## Important

- Treat each project independently
- Do not compare projects to each other
- Focus on what the tools tell you: simulation assertions, synthesis errors, timing violations
- Report concrete observations, not speculation

## Tools Available

The Vivado MCP server provides 16 tools including simulation, build execution, timing analysis, and design queries. Use them directly.