# O6 Agent1 Public-MCP White-Box Replay

This directory contains the O6 replay harness.  It is an Agent1 white-box
verification artifact, not a product implementation and not an Agent2 prompt.

The harness starts the **unified public MCP server** through the MCP SDK and
uses only `ClientSession.list_tools()` / `ClientSession.call_tool()` for every
Platform, PL, PS, consistency, JTAG, UART, observation, diagnostic, and
recovery action.  It may create GPIO requirement inputs (`main.c`, XDC) and
read public artifacts under the clean project directory.  It does not import
product internals, start an EDA executable, execute Tcl, build software through
a shell, publish a Manifest, edit runtime state, or kill a process.

Run from the repository root with a path that does not already exist:

```powershell
python validation_projects/o6_public_mcp_replay/run_public_replay.py `
  --workspace D:/fpgaproject/workspaces/o6_agent1_public_20260813 `
  --runtime-root D:/fpgaproject/.o6_runtime_agent1_20260813
```

Evidence is written under `<workspace>/evidence/`:

- `public_calls.jsonl`: every public MCP request/response;
- `operation_timeline.jsonl`: bounded waits and real Ledger observations;
- `tools_schema.json`: public schema snapshot;
- `summary.json`: artifact paths/SHA, consistency, GPIO/UART verdict, cleanup;
- `uart.txt`: complete UART evidence.

O7 is not part of this harness.  A fresh-memory Agent2 may only be started
after O6 is independently reviewed and the user authorizes O7.
