# Task: FPGA Design Validation — Skill-Based Workflow

## Objective

Validate all FPGA design projects using the provided engineering skills.

## Project Location

```
D:\fpgaproject\validation_projects\designs\
```

Process every subdirectory. Each contains an independent FPGA project.

## Infrastructure

A Vivado MCP server is connected with 22 tools for simulation, synthesis, implementation, timing analysis, and design validation. These tools are the backend for the FPGA skills.

## Available Skills

Two FPGA engineering skills are available:

- **fpga-verify** — Complete design verification: Simulation → Build → Timing → Validation → Report
- **fpga-develop** — Iterative development: Diagnose → Fix → Re-verify → Repeat until PASS

Use these skills for your workflow. They know the correct sequence of MCP tools and the expected checks. Invoke them with `/fpga-verify` and `/fpga-develop`.

## Required Workflow

For each project:

1. Invoke the `verify_design` skill
2. If the design PASSES, move to the next project
3. If the design has failures, use the `develop_design` skill to diagnose and repair
4. After repair, re-run `verify_design` to confirm all issues are resolved
5. Report results for each project

## Deliverable

A structured report for each project including:
- Initial verification results
- Issues found (if any)
- Repairs performed (if any)
- Final verification results (PASS or remaining failures)
