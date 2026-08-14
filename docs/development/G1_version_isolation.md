# G1 — Version Isolation

> 日期: 2026-07-31
> 状态: ✅ COMPLETE

## Objective

Establish clean Vivado environment sourcing without polluting system PATH.

## Approach

- Use `settings64.bat` on-demand (`call D:\Xilinx\Vivado\2023.1\settings64.bat`)
- Never permanently modify system PATH
- All tools (Python, Node, Claude Code) verified working after environment isolation

## Design Decision

`VivadoProcess` resolves Vivado executable from `config.py` (via `VIVADO_ROOT` env or hardcoded path), not from PATH. This avoids multi-version conflicts and makes the MCP server portable.
