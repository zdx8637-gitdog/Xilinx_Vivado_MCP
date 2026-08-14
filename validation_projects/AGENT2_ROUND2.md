# Task: Continue FPGA Project Validation

## Context

You previously validated 3 of 11 projects in the test suite. Complete validation of all remaining projects.

## Project Location

```
D:\fpgaproject\validation_projects\designs\
```

Process every subdirectory under this path. Some you have already tested; continue with those you have not.

## Updated Tooling

The MCP platform now includes additional tools beyond the simulation and build tools used previously:

- `validate_design` — Runs post-condition checks on the current design state
- `synth_design` — Runs synthesis as an independent step
- `place_design` — Runs placement
- `route_design` — Runs routing
- `write_bitstream` — Generates bitstream
- `create_project` — Creates a Vivado project from RTL and constraint files

All previously available tools remain available.

## Required Workflow

For each project:

1. Determine the appropriate entry point (simulation, synthesis, or project creation)
2. Execute the relevant workflow steps
3. After synthesis or implementation, run `validate_design` and report its output
4. Diagnose any issues found
5. If repair is possible, perform it and re-validate
6. Provide a structured report for each project

## Deliverable

One structured report per project, including validation status, diagnostic findings, and any repairs performed.
