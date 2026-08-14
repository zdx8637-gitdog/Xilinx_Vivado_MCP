# G7 — EDA Validation Layer

> 日期: 2026-08-02
> 状态: ✅ COMPLETE

## Objective

Add engineering semantics validation to catch Vivado "false pass" scenarios.

## Problem

Vivado reports `passed: true` even when:
- No clock constraint defined (unconstrained timing)
- Invalid pins silently ignored
- Wrong FPGA part used
- Critical warnings present

These are dangerous false positives for AI-driven development.

## Solution

### validate_design Tool

Post-condition checks after build:
1. `clocks_defined` — at least one clock exists
2. `ports_assigned` — all ports have non-null LOC
3. `timing_valid` — WNS is not null
4. `part_known` — device part is recognized

Returns structured status: `passed` | `warning` | `failed` with actionable details.

### Build Tool Upgrade

`_build_result()` shared parser now exposes:
- `critical_warnings` count
- `warning_details` and `error_details` arrays
- 3-state status: `passed` | `warning` | `failed`

### Timing Upgrade

`report_timing_summary` now returns warnings when WNS is null (indicates no clock constraint).

## Validation Results

11-project benchmark: Agent 2 correctly identified all defects. 4 false-pass projects (F006/F007/F008/F011) now caught by `validate_design`.
