# G8 — Skill Workflow Layer

> 日期: 2026-08-02
> 状态: ✅ COMPLETE

## Objective

Introduce Workflow Layer (Layer 3) above MCP Tools — standardize FPGA engineering processes as reusable skills.

## Architecture v1.0

```
Layer 3: Workflow  (Skills: fpga-verify, fpga-develop)
Layer 2: Tool      (27 MCP Tools)
Layer 1: Process   (VivadoProcess, XSimProcess)
```

## Four Immutable Principles

| P1 | Process only manages external program lifecycle |
| P2 | One Tool = one engineering action |
| P3 | Workflow orchestrates Tools for AI engineering |
| P4 | All Workflows must be recoverable |

## Skills

| Skill | Purpose |
|-------|---------|
| `fpga-verify` | Complete verification: Sim → Build → Timing → Validate → Report |
| `fpga-develop` | Iterative development: Diagnose → Fix → Re-verify → Repeat |

## Test Results

Agent 2 with Skills: 11/11 projects fully repaired (up from 8/11 without Skills). Skills eliminated ad-hoc decision-making — no more "diagnosed but skipped" failures.
