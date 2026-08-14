# B05 Platform/AXI vs Legacy zynq_platforms — Tcl Flow Comparison

Date: 2026-08-08

## Sources compared

| Source | Path |
|--------|------|
| Legacy G10 | `zynq_platforms/ax7020_base/block_design/create_platform.tcl` |
| Legacy G10 build | `zynq_platforms/ax7020_base/block_design/build_g10.tcl` |
| Legacy G11 build | `zynq_platforms/ax7020_base/block_design/build_g11.tcl` |
| B05 current | `mcps/zynq_mcp/domains/platform/platform_domain.py` (generate_platform) |

## Scope difference

The legacy projects are **full build flows** (BD → synthesis → implementation → bitstream → XSA).
B05 is a **minimum vertical slice** (BD → wrapper → XSA, no synthesis/implementation/bitstream).
This is by design — B05 produces the platform artifact (XSA + wrapper + manifest) that B04
(PL domain) consumes; the PL domain owns synthesis through bitstream.

## Per-step comparison

### 1. Project creation

| Aspect | Legacy G10/G11 | B05 |
|--------|---------------|-----|
| Command | `create_project -force <name> <dir> -part xc7z020clg400-2` | Same via `_run_tcl(adapter, 'create_project platform_project {...} -part {part} -force')` |
| Language | G11 sets `target_language Verilog` | Not set (Vivado default) |

**Verdict: REUSED.** Same create_project pattern; target_language is cosmetic for a BD-only flow.

### 2. PS7 instantiation and configuration

| Aspect | Legacy G10 | Legacy G11 | B05 |
|--------|-----------|-----------|-----|
| IP version | `processing_system7:5.5` | Same | Same |
| Config method | Inline `set_property -dict` (6 params) | External `ps_config.tcl` (537 params) | External `ps7_preset.tcl` via `set_ps_config` |
| Automation | None (manual `make_bd_intf_pins_external`) | `pl_config.tcl` (external) | `apply_bd_automation -rule xilinx.com:bd_rule:processing_system7` |

**Verdict: ADAPTED.** B05 uses the board package's `ps7_preset.tcl` (analogous to G11's
approach of sourcing an external config) but uses Vivado's `apply_bd_automation` for
DDR/FIXED_IO port externalization instead of manual `make_bd_intf_pins_external`.
The automation approach is more robust — it also configures the AXI GP0 interface
automatically, which the legacy scripts configure manually via `CONFIG.PCW_USE_M_AXI_GP0 {1}`.

### 3. AXI Interconnect

| Aspect | Legacy G10/G11 | B05 |
|--------|---------------|-----|
| IP | `axi_interconnect:2.1` | `smartconnect:1.0` |
| Config | `CONFIG.NUM_MI {1 or 2} CONFIG.NUM_SI {1}` | `CONFIG.NUM_SI {1}` (SmartConnect auto-sizes MI) |

**Verdict: DIVERGED intentionally.** B05 uses SmartConnect (newer Xilinx IP, auto-negotiates
data width and clock domain crossing). Legacy uses AXI Interconnect (explicit configuration).
SmartConnect is the recommended IP for Zynq-7000 in Vivado 2023.1. The legacy
`create_platform.tcl` uses 2 MI ports (LED + status GPIO); B05 uses 1 MI (LED only) because
the status GPIO is a B09 concern.

### 4. AXI GPIO configuration

| Aspect | Legacy | B05 |
|--------|--------|-----|
| Channels | 2 (LED 4-bit output + status 32-bit input) | 1 (LED 4-bit output only) |
| LED config | `C_GPIO_WIDTH {4} C_ALL_OUTPUTS {1} C_IS_DUAL {0}` | Same |
| External port | `create_bd_port -dir O -from 3 -to 0 led_pins` | `create_bd_port -dir O -from 3 -to 0 gpio_led` |

**Verdict: PARTIALLY REUSED.** LED GPIO configuration is identical. Status GPIO channel
is deferred to B09. Port naming differs (`led_pins` vs `gpio_led`) — cosmetic.

### 5. Reset controller

| Aspect | Legacy | B05 |
|--------|--------|-----|
| IP | `proc_sys_reset:5.0` | Same |
| Instance name | `proc_sys_reset_0` | `rst_ps7_50M` |
| Clock source | `FCLK_CLK0` → `slowest_sync_clk` | Same |
| Reset source | `FCLK_RESET0_N` → `ext_reset_in` | Same |

**Verdict: REUSED with cosmetic rename.** The instance name difference is cosmetic.
All clock/reset topology connections match.

### 6. Clock connections

| Legacy | B05 |
|--------|-----|
| `FCLK_CLK0` → `M_AXI_GP0_ACLK` | Same |
| `FCLK_CLK0` → every `ACLK`/`S00_ACLK`/`M00_ACLK`/`s_axi_aclk` explicitly | `FCLK_CLK0` → `smartconnect_0/aclk`, `axi_gpio_led/s_axi_aclk`, `rst_ps7_50M/slowest_sync_clk`, `M_AXI_GP0_ACLK` |
| Legacy enumerates all IC ACLK ports explicitly | SmartConnect has a single `aclk` port |

**Verdict: ADAPTED for SmartConnect.** The clock topology is semantically identical:
FCLK_CLK0 drives all AXI and reset clocks. SmartConnect's simpler clock interface
(a single `aclk` vs AXI Interconnect's per-port `ACLK`/`Sxx_ACLK`/`Mxx_ACLK`)
reduces the connection count.

### 7. Reset connections

| Legacy | B05 |
|--------|-----|
| `peripheral_aresetn` → all IC `ARESETN`/`S00_ARESETN`/`M00_ARESETN` + GPIO `s_axi_aresetn` | `peripheral_aresetn` → `axi_gpio_led/s_axi_aresetn`; `interconnect_aresetn` → `smartconnect_0/aresetn` |

**Verdict: ADAPTED for SmartConnect.** SmartConnect has different reset port semantics
(`aresetn` single port, `interconnect_aresetn` from reset controller instead of
`peripheral_aresetn`). Functionally equivalent.

### 8. AXI bus connections

| Legacy | B05 |
|--------|-----|
| `M_AXI_GP0` → `IC/S00_AXI`; `IC/M00_AXI` → `axi_gpio_led/S_AXI` | Same topology: `M_AXI_GP0` → `smartconnect_0/S00_AXI`; `smartconnect_0/M00_AXI` → `axi_gpio_led/S_AXI` |

**Verdict: REUSED.** Identical AXI topology.

### 9. Address assignment

| Legacy | B05 |
|--------|-----|
| `assign_bd_address` | Same, plus explicit address verification against `EXPECTED_GPIO_ADDRESS = 0x41200000` |

**Verdict: REUSED AND HARDENED.** B05 adds address verification — if Vivado assigns a
different address, `generate_platform` fails with `BD_VALIDATION_FAILED`.

### 10. BD validation

| Legacy | B05 |
|--------|-----|
| `validate_bd_design` only | `validate_bd_design` + output scan for "error" and "critical warning" |

**Verdict: REUSED AND HARDENED.** B05 inspects the Tcl output for hidden errors.

### 11. Wrapper generation

| Legacy | B05 |
|--------|-----|
| `make_wrapper -files [get_files <bd>.bd] -top` + `add_files` + `set_property top` | `make_wrapper -files [get_files *platform_bd*.bd] -top` then OS-level file copy to `hdl/` |

**Verdict: ADAPTED.** B05 does not add the wrapper to the Vivado project (no synthesis
follows) but copies it to the project's `hdl/` directory for B04 consumption.
The legacy projects also call `generate_target all` before wrapper creation — B05
omits this because it doesn't synthesize. If B04 encounters IP OOC synthesis issues,
`generate_target` should be added.

### 12. XSA export

| Legacy G10 | Legacy G11 | B05 |
|-----------|-----------|-----|
| `write_hw_platform -fixed -force -file <path>` | `write_hw_platform -fixed -include_bit -force -file <path>` | `write_hw_platform -fixed -force -file <path>` |

**Verdict: REUSED from G10.** B05 matches G10's no-bitstream XSA export. G11's
`-include_bit` is not applicable (B05 doesn't produce a bitstream).

### 13. Build flow (synthesis → implementation → bitstream)

| Legacy | B05 |
|--------|-----|
| G10/G11: `generate_target all` → `launch_runs synth_1`/`impl_1` → `wait_on_run` → `write_bitstream` | NOT PRESENT |

**Verdict: DEFERRED to B04.** Synthesis/implementation/bitstream are PL domain
responsibilities. The platform domain only produces BD + wrapper + XSA. This is
an architectural decision, not an omission.

### 14. Pin constraints

| Legacy | B05 |
|--------|-----|
| G10/G11: Dynamic LED pin assignment (J16/K16/M15/M14, LVCMOS33) after synthesis | NOT PRESENT |

**Verdict: DEFERRED to B04.** Pin constraints are applied during PL build, not platform
generation. The wrapper provides the port interface; B04 constrains and connects.

## Items NOT reused from legacy (with justification)

1. **AXI Interconnect → SmartConnect**: SmartConnect is Vivado 2023.1's recommended IP.
   Legacy used AXI Interconnect because it was written for an earlier Vivado version.
   SmartConnect simplifies clock/reset wiring and auto-configures data width.

2. **Dual GPIO channel**: B05 implements only the LED output channel. The status input
   channel from `create_platform.tcl` is a B09 (full GPIO workflow) concern. Adding it
   now would expand B05's scope beyond the minimum vertical slice.

3. **`generate_target all` before wrapper**: B05 omits this because no synthesis follows.
   If B04 encounters missing IP output products, this step should be added to B05.

4. **Synthesis/implementation/bitstream**: Architectural division — platform domain
   stops at XSA; PL domain handles the build.

5. **ALINX 537-param PS7 config (G11)**: B05 uses the board package's `ps7_preset.tcl`
   which is derived from the same ALINX reference but maintained in the board package
   as the single source of truth.

## Items REUSED from legacy

1. PS7 IP version (`processing_system7:5.5`)
2. FCLK_CLK0 as single clock source for all AXI + reset
3. FCLK_RESET0_N → ext_reset_in → peripheral_aresetn topology
4. AXI GPIO 4-bit all-output configuration
5. External GPIO port creation pattern
6. `assign_bd_address` for automatic address assignment
7. `validate_bd_design` before save
8. `write_hw_platform -fixed -force` for no-bitstream XSA
9. Project structure: `create_project -force` with xc7z020clg400-2
10. PS7 preset sourced from external file (analogous to G11's `ps_config.tcl`)

## Conclusion

B05 reuses the proven Tcl topology (PS7 → AXI bus → GPIO) from the legacy projects
while making two intentional divergences: SmartConnect (modern IP recommendation)
and scope reduction (no synthesis/bitstream — deferred to PL domain). All clock,
reset, AXI, and GPIO configuration patterns trace directly to the legacy flow.
