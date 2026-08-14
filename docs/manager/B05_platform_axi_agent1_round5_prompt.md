# Agent1 Prompt: B05 Platform/AXI Round 5 Evidence And Legacy-Flow Closure

Target: Agent1, external Claude Code. Continue B05 only.

Read first:

1. `D:/fpgaproject/docs/manager/B05_platform_axi_round4_review.md`
2. `D:/fpgaproject/docs/manager/B05_platform_axi_agent1_round4_prompt.md`

Status: `REMEDIATION REQUIRED / AGENT3 NOT AUTHORIZED`. Do not call Agent3 or
Agent2, do not start B06, and do not add generalized security mechanisms.

## Required work

1. Preserve inspectable evidence from a real host-live run:
   - run the actual `test_full_success_chain` and wrong-stage test with no
     deselection;
   - run `validation_projects/phase_blackbox/b05_platform_axi/runner.py`;
   - preserve each run's `summary.json`, per-scenario result JSON, public
     response/state traces, operation ID, exit code, worker PID, Vivado version,
     artifact paths, sizes, and independent SHA256 values under a unique
     evidence directory;
   - if Vivado cannot be run, report exactly
     `BLOCKED: REAL VIVADO NOT EXECUTED` and do not claim ready.

2. Correct the publisher discrepancy. The source currently imports
   `validate_manifest` and performs its own temp-file `os.rename()`. Either:
   - use `mcps.common.artifact_schema.publish_manifest()` while preserving
     relative persisted paths and validating a project-root-resolved copy; or
   - if a shared-publisher change is required, implement and test that narrow
     compatibility change, then prove the final manifest is accepted by the
     existing B04 `pl_generate_system_top` binder.

3. Inspect and reuse the existing platform flow before editing Tcl again:

   - `D:/fpgaproject/zynq_platforms/ax7020_base/block_design/create_platform.tcl`
   - `D:/fpgaproject/zynq_platforms/ax7020_base/block_design/build_g10.tcl`
   - `D:/fpgaproject/zynq_platforms/ax7020_base/block_design/build_g11.tcl`

   Compare their PS7 preset application, PS7 external ports, clock/reset
   connections, AXI topology, address assignment, wrapper generation, and XSA
   export sequence with B05. Reuse the proven sequence where applicable and
   record the comparison in the B05 README or report. Do not use old generated
   outputs as a substitute for generating a fresh project artifact.

4. Keep the B05 black-box contract unchanged: public MCP SDK only, no ledger,
   no `run_tcl`, no internal imports, no wrapper copying/renaming, and expected
   JSON remains the source of assertion values.

## Required commands

```text
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_component.py -v
python -m pytest mcps/zynq_mcp/tests/test_b05_platform_public.py -v -m host_live
python validation_projects/phase_blackbox/b05_platform_axi/runner.py --run-id <unique-id>
python -m pytest mcps -q -W error::RuntimeWarning
```

Report full SHA256 for modified files and exact evidence paths. Agent3 remains
unauthorized until the evidence is present and the legacy-flow comparison is
documented.

