# Agent1 Prompt: B05 Platform/AXI Round 4 Handoff Closure

Target: Agent1, external Claude Code. Continue B05 only.

Read first:

1. `D:/fpgaproject/docs/manager/B05_platform_axi_round3_review.md`
2. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_round3_prompt.md`

Status: `REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED`. Do not call Agent3 or
Agent2, do not start B06, and do not expand security hardening.

## Required functional fixes

1. Persist the Platform Manifest with relative paths:
   - `xsa_path: "platform.xsa"`
   - `bd_wrapper_path: "hdl/platform_bd_wrapper.v"`
   Keep validation file-aware by resolving a copy against `project_path`, or
   make the shared publisher accept an explicit project root while preserving
   the relative values in the published JSON. Do not publish absolute paths.

2. Prove direct Platform-to-PL handoff. The public test and black-box runner
   must derive the wrapper path from the returned artifact and project root,
   then call public `pl_generate_system_top` with that relative path. Do not
   copy, rename, or hardcode a replacement wrapper path.

3. Strengthen black-box artifact facts using only stdlib + MCP SDK:
   independently SHA256 hash returned XSA, wrapper, and manifest; compare each
   disk hash with the operation result; compare manifest `xsa_sha256` and
   `bd_wrapper_sha256` with the same hashes; verify manifest revision equals the
   context revision; verify the reported address map contains the GPIO base
   `0x41200000` and 64 KiB range.

4. Keep checked-in `expected_outputs/*.json` as the comparator source. The
   runner must fail if an expected ID is missing, duplicated, or unconsumed.

## Required execution

Run the actual commands, with no deselection:

```text
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_component.py -v
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -v -m host_live
python validation_projects/phase_blackbox/b05_platform_axi/runner.py --run-id <unique-id>
python -m pytest mcps -q -W error::RuntimeWarning
```

The report must include exit codes and the actual host-live operation trace:
operation ID, terminal status, stage before/after, worker PID, Vivado version,
artifact paths/sizes/independent SHA256, manifest validation result, and the
direct `pl_generate_system_top` terminal result. A test file that merely
collects or deselects the Vivado case is not evidence.

If Vivado cannot be executed, report exactly
`BLOCKED: REAL VIVADO NOT EXECUTED`; do not claim ready.

