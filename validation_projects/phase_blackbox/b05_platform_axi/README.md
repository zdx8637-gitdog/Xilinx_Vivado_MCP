# B05 Platform/AXI — Agent3 Black-Box Acceptance

> **Status**: READY FOR AGENT3 — Manager Review PASSED
> **Phase**: B05 Platform/AXI | **Type**: FRESH_SESSION
> **Agent**: Agent3 (fresh context only)
> **Reviewed**: 2026-08-08 — 9/9 mechanical audit items PASS | Vivado host-live 7/7 | Black-box 3/3 back-to-back | Full regression 905/0

## Purpose

Verify the B05 public MCP boundary: `platform_generate` through a real `zynq_mcp` server using only MCP SDK ClientSession and public tools.

This is a FRESH_SESSION project. The runner calls public `create_session(board_id, project_path)` and proceeds from `PLATFORM_DESIGN`. No Manager provisioning or preconditioned runtimes.

## Scenarios

| # | Scenario | Stage Flow | Expected | Assertions |
|---|----------|------------|----------|------------|
| 1 | discovery | PLATFORM_DESIGN | `platform_generate` in tool list, empty schema `{}`, correct tool count | 8 |
| 2 | success | PLATFORM_DESIGN → PL_GENERATE → PL_BUILD | BD created, PS7+GPIO+SmartConnect, XSA/wrapper/manifest valid, `0x41200000`, Platform→PL handoff via `pl_generate_system_top` | 31 |
| 3 | stage_rejection | PL_GENERATE or later → unchanged | Rejected with `STAGE_PREREQUISITE_UNMET`, public state unchanged | 3 |

## Quick Start (Agent3)

```bash
cd D:\fpgaproject
python validation_projects\phase_blackbox\b05_platform_axi\runner.py --run-id agent3_b05_<unique_id>
```

That's it. Read `CLAUDE.md` then `AGENT3_EXECUTION_PROMPT.md` for full instructions.

## Architecture

```
expected_outputs/*.json     ← CHECKED-IN assertion contracts
         │
         ▼  runner.py loads them
         │
         ▼  runner.py starts MCP SDK server → calls public tools
evidence/<run_id>/          ← RUNNER OUTPUT
├── summary.json
├── *_result.json
└── <scenario>/
```

- `runner.py` imports ONLY stdlib + `mcp` SDK. No `mcps.zynq_mcp` imports.
- Server starts its own Vivado Worker when `platform_generate` is called.
- Runner uses unique `ZYNQ_RUNTIME_ROOT` temp directory — no cross-run state leakage.

## Public Contract

See `public_contract.md` for tool schema, stage admission, error codes, and terminal evidence format.

## Constraints

- Agent3 MUST NOT import `mcps.zynq_mcp` or read `execution_ledger.json`.
- Agent3 MUST NOT configure `zynq_mcp` as a Claude Code MCP server.
- Agent3 MUST NOT call `run_tcl`.
- All Worker, Vivado (beyond what the server manages), JTAG, hardware = NOT_APPLICABLE.
