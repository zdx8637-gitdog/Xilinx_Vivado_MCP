# B05 Platform/AXI Manager Handoff

Date: 2026-08-08

## Handoff Status

This document hands the B05 Manager Reviewer work to the next reviewer/Codex
context. B04 R3.1-C was accepted by Agent3 earlier, but B05 is not frozen.
Agent3 and Agent2 have not been called for B05, and B06 has not started.

Current gate: `REMEDIATION / VERIFY EVIDENCE BEFORE AGENT3`.

The latest Agent1 report says Round 4 is ready. Treat the report as a claim,
not as evidence. The next reviewer must use the files and preserved run output
on disk as the source of truth.

## Scope

B05 is the minimum Platform/AXI vertical slice with exactly one new public
tool:

```text
create_session
  -> PLATFORM_DESIGN
  -> platform_generate {}
  -> PS7 + SmartConnect + one-channel, four-bit AXI GPIO BD
  -> wrapper + no-bitstream XSA + Platform Manifest
  -> context.platform_revision and operation.output_artifact_revision
  -> PL_GENERATE
  -> pl_generate_system_top(wrapper_rel)
  -> PL_BUILD
```

No hardware/JTAG/UART/board programming is in scope. Vivado is required for
the real success path.

## Authoritative Manager Documents

Read these in order:

1. `D:/fpgaproject/docs/manager/B05_platform_axi_manager_handoff.md` (this file)
2. `D:/fpgaproject/docs/manager/B05_platform_axi_round4_review.md`
3. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_round5_prompt.md`
4. `D:/fpgaproject/mcps/zynq_mcp/domains/platform/LEGACY_COMPARISON.md`

Earlier Round 1-3 reviews and prompts remain historical context only.

## Current Implementation Snapshot

### Production and shared contract files

The latest observed SHA256 values are:

| File | SHA256 |
|---|---|
| `mcps/common/artifact_schema.py` | `381dac32c76b65febcd2aecffb4e2ccede0d32a4f7c7b4ca4a84f48b7cda4418` |
| `mcps/zynq_mcp/domains/platform/platform_domain.py` | `102264f09cb171724b0f273bdabb17b6744cab18c66057966231432d923c1ed2` |
| `mcps/zynq_mcp/domains/platform/LEGACY_COMPARISON.md` | `db757ddc0987ed3ccd5ed2e428fe93190e2b096475dfec571fc919681be19707` |
| `mcps/zynq_mcp/tests/test_b05_platform_component.py` | `3e05f787105123b3533f5e5f6bedfe6647475e16e2121bf47814881a6e043017` |
| `mcps/zynq_mcp/tests/test_b05_platform_public.py` | `4f34298da1098804a9b6794ae5fd6b77bfe334381aae13d4e7003111281302f7` |
| `validation_projects/phase_blackbox/b05_platform_axi/runner.py` | `8e2df3df070327805230b2e487da268b02b4de6da2176c87e72875cd1cb1518b` |
| `validation_projects/phase_blackbox/b05_platform_axi/expected_outputs/success.json` | `ce975efe1cc2671ea139f508adc53f30578d62439e586ee5df9c7682a678059d` |

The source currently contains the following claimed functional changes:

- `create_session` starts at `PLATFORM_DESIGN`.
- `platform_generate` uses `SingleWorkerController.ensure_worker()`.
- The board profile supplies the Vivado part.
- The Tcl flow creates PS7, applies `set_ps_config`, creates one-channel GPIO,
  SmartConnect and reset, connects clocks/resets, checks `0x41200000`, creates
  a wrapper, and exports an XSA without a bitstream.
- `artifact_schema.validate_manifest()` and `publish_manifest(...,
  resolve_root=project_path)` support relative persisted paths.
- The operation runner publishes `context.platform_revision` and
  `output_artifact_revision` on success.
- The black-box runner uses stdlib + MCP SDK only and loads checked-in expected
  JSON files.

## Legacy Platform Reuse

The pre-existing platform work is under:

```text
D:/fpgaproject/zynq_platforms/ax7020_base/
```

Agent1 added the comparison document:

`D:/fpgaproject/mcps/zynq_mcp/domains/platform/LEGACY_COMPARISON.md`

It compares and records reuse/adaptation of:

- PS7 IP version and external DDR/FIXED_IO pattern;
- FCLK_CLK0 and FCLK_RESET0_N reset topology;
- AXI GP0 -> interconnect -> GPIO topology;
- four-bit all-output LED GPIO configuration;
- `assign_bd_address`, `validate_bd_design`, and no-bitstream
  `write_hw_platform -fixed -force`;
- intentional differences: SmartConnect instead of legacy AXI Interconnect,
  one GPIO channel instead of the legacy LED + status channels, and no PL
  synthesis/bitstream in the Platform domain.

This comparison is now present, but it still needs to be considered part of
the functional review. It must not be treated as proof that a fresh B05 run
passed.

## Evidence State Observed

The claimed host-live log is present at:

`D:/fpgaproject/validation_projects/phase_blackbox/b05_platform_axi/evidence/round5_20260808/host_live_pytest.log`

Its recorded result is 7 tests passed in 336.64 seconds, including the real
success chain and wrong-stage case. A companion
`host_live_summary.json` records Vivado 2023.1, exit code 0, and the seven test
names.

However, the same evidence directory has been observed changing while Agent1
continues work. At the latest inspection it contained only partial black-box
outputs: `discovery_result.json` was present while the success/stage result and
summary files were absent or inconsistent. Earlier output showed a black-box
summary with `overall=false`, `success` consumed 5/31 assertions, and fresh
`create_session`/admission failure. Therefore the next reviewer must rerun the
black-box command and inspect the final directory after the process exits.

Do not infer a passing black-box run from `host_live_summary.json` alone.

## What Has Been Independently Verified Here

- `test_b05_platform_component.py`: `14 passed`.
- Public B05 collection contains 7 tests, including both Vivado tests.
- `platform_domain.py` currently references `publish_manifest(...,
  resolve_root=pp)` and publishes relative `platform.xsa` and
  `hdl/platform_bd_wrapper.v`.
- `artifact_schema.py` supports `resolve_root` for relative file validation.
- The B05 runner has no `mcps.common` or `mcps.zynq_mcp` imports and uses the
  returned `wrapper_rel` for the PL handoff.
- `LEGACY_COMPARISON.md` exists and references the three legacy Tcl sources.

The full 715-pass regression and the final black-box run have not been
independently rerun in this handoff turn.

## Required Next Actions

The next reviewer should not add another security audit. Perform this focused
functional verification:

1. Confirm no Agent1 process is still changing the target files; then capture
   current SHA256 values.
2. Run component tests:

   ```text
   python -m pytest mcps/zynq_mcp/tests/test_b05_platform_component.py -v
   ```

3. Run all seven public tests with no deselection:

   ```text
   python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -v -m host_live
   ```

4. Run the black-box project with a new run ID and preserve its complete
   evidence:

   ```text
   python validation_projects/phase_blackbox/b05_platform_axi/runner.py --run-id <unique-id>
   ```

5. Run the full regression:

   ```text
   python -m pytest mcps -q -W error::RuntimeWarning
   ```

6. Inspect the generated Manifest directly. It must contain relative paths,
   must be accepted by the B04 binder, and the public PL command must consume
   the exact `wrapper_rel` returned by `platform_generate` without copying.

7. Verify evidence includes operation ID, stage transition, worker PID,
   Vivado version, artifact sizes and SHA256 values, Manifest fields/address
   map, and Platform-to-PL terminal result.

## Agent3 Gate

Agent3 may be routed only when all conditions below are true:

- public seven-test host-live command exits 0 with both Vivado tests actually
  executed;
- B05 black-box runner exits 0 with all three scenarios passing and
  `expected_assertion_count == consumed_assertions` for each;
- complete evidence is preserved under a unique run directory;
- generated Manifest uses relative paths and direct B04 handoff succeeds;
- component and full regression pass;
- no production/test changes outside the reported B05 scope are unexplained;
- Agent1/Agent2/Agent3/B06 status remains explicitly recorded.

If Vivado is unavailable, use exactly:

`BLOCKED: REAL VIVADO NOT EXECUTED`

Do not report `READY` in that case.

## Agent Routing Boundary

Agent1, Agent2, and Agent3 are external Claude Code agents. This handoff does
not authorize Codex to call them as sub-agents. The user manually forwards
`B05_platform_axi_agent1_round5_prompt.md` to Agent1 and returns the report.

