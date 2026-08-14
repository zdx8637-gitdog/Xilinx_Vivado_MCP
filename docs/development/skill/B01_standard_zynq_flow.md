# B01 — Standard Zynq Development Flow v1.2.2

> Brick: B01
> 日期: 2026-08-04
> 状态: **✅ 完成 — B01 冻结**
> 架构依据: `docs/architecture_ai_zynq7020.md` v2.3.1 (FROZEN)

---

## 1. Purpose

Define how an AI Agent transforms a user requirement into a complete, verifiable Zynq-7020 project.

---

## 2. Input Requirement Template

| Field | Example (GPIO) | Required |
|-------|-----------------|----------|
| `board_id` | `ALINX_AX7020_v1.0` | Yes |
| `part` | `xc7z020clg400-2` | Yes |
| `functional_requirement` | ARM controls 4 PL LEDs via AXI GPIO | Yes |
| `ps_software` | Bare-metal C, UART 115200 8N1, stdout=PASS/FAIL markers | Yes |
| `pl_logic` | None (BD-only for GPIO slice); future: custom RTL | Yes |
| `ps_pl_communication` | AXI GPIO via M_AXI_GP0, 4-bit, direction=output | Yes |
| `clocks` | PS FCLK0 = 50 MHz | Yes |
| `resets` | FCLK_RESET0_N → Processor System Reset | Yes |
| `addresses` | AXI GPIO @ 0x41200000 (explicit) | Yes |
| `interrupts` | None | If applicable |
| `dma` | None | If applicable |
| `ddr_requirements` | None | If applicable |
| `deployment` | JTAG only | Yes |
| `observable_output` | UART markers + register readback + LED visual (auxiliary) | Yes |
| `pass_condition` | All GPIO_WRITE readback matches, UART outputs `GPIO_E2E_PASS` | Yes |
| `fail_condition` | Any readback mismatch, UART timeout > 10s, or artifact stale | Yes |

---

## 3. Requirement Decomposition

```
Requirement: "ARM controls 4 PL LEDs via AXI GPIO"
    │
    ├── PS:  bare-metal C app
    │        · Xil_Out32 to AXI GPIO address (from xparameters.h)
    │        · Xil_In32 for readback
    │        · xil_printf to UART
    │        · GPIO output direction: set via AXI GPIO TRI register at init
    │        · Artifacts: ELF, PS Build Manifest
    │
    ├── Platform: AXI GPIO control path
    │        · PS7: enable M_AXI_GP0, FCLK0=50MHz, UART1 MIO48/49
    │        · AXI GPIO IP: 4-bit, direction="output" (semantic contract)
    │        · Connect: M_AXI_GP0 → AXI GPIO S_AXI
    │        · Set address: 0x41200000 explicitly
    │        · Clock/Reset: FCLK0 → GPIO s_axi_aclk, FCLK_RESET0 → proc_sys_reset → GPIO aresetn
    │        · Artifacts: Platform XSA, Platform Manifest
    │
    └── PL: system_top integration (no user functional RTL)
             · PL MCP generates system_top that instantiates BD wrapper
             · XDC: LED pin constraints (active-low)
             · Build: synth → place → route → bitstream
             · Artifacts: Bitstream, PL Build Manifest
```

### Interface Contract

| Interface | Producer | Consumer | Content |
|-----------|----------|----------|---------|
| Platform XSA | Platform MCP | PS MCP, PL MCP | PS7 config, address map, IP list |
| Platform Manifest | Platform MCP | PS Skill, PL Skill, Workflow | JSON: addresses, clocks, xsa_sha256, bd_wrapper_sha256, platform_revision |
| PL Build Manifest | PL MCP | Workflow | JSON: bitstream_sha256, bd_wrapper_sha256, timing, board_profile_sha256, built_from |
| PS Build Manifest | PS MCP | Workflow | JSON: elf_sha256, xparameters_h_sha256, XPAR_*_BASEADDR, built_from |
| Board Profile | Shared library | All MCPs, Skills, Workflow | board_id, sha256, ddr, qspi, ps7 preset SHA256 |

---

## 4. Shared Components

Not MCP servers. Shared libraries imported by the MCPs, Skills, and Workflow that need them.

### Board Profile Loader

```
board_profile_load(board_id) → {
  board_id, sha256, part, vivado_part,
  ddr_physical, ddr_configured,
  ps7_preset: { <full preset dict> },
  ps7_preset_sha256,
  xdc_sha256,
  uart: { port: "UART1", mio: "48..49", initial_baud: 115200 },
  pl_leds: { count: 4, polarity: "active-low" },
  pl_buttons: { count: 4, polarity: "active-high" },
  ...
}
```
Consumers: All MCPs, Skills, Workflow.

### JTAG Lock Library

```
lock_acquire(hw_server_url, cable_serial, ttl_s=300) → lease_id
lock_heartbeat(lease_id)
lock_release(lease_id)
```
Implements P8 of the frozen architecture. Lock key = `hw_server URL + cable serial` (per architecture line 1188).
Consumers: PL MCP (during programming), PS MCP (during deploy/diagnostic). Workflow serializes the handoff.

---

## 5. Standard Zynq Development Phases

### Phase 0: Board Profile Validation

```
Skill decision: Load board profile by board_id.
                Verify profile SHA256 is known and unchanged.

Component:     board_profile_load(board_id) → profile + sha256

Input:         board_id from requirement
Output:        validated board_profile_sha256 + profile parameters
Test evidence: F-GPIO-002 (wrong board revision injection)
```

### Phase 1: Platform Design

```
Skill decision:  Decide PS7 config, AXI GPIO width, direction="output", address, clock, reset.

MCP capability:
  Platform MCP:  create_design, add_ps7, configure_ps7,
                 add_axi_gpio, connect_interface, connect_clock, connect_reset,
                 set_address, validate, generate_wrapper,
                 export_hardware, export_manifest

Input:          board_profile, user requirement
Output:         Platform XSA, Platform Manifest {
                   xsa_path, xsa_sha256,
                   board_profile_sha256,
                   bd_wrapper_path, bd_wrapper_sha256,
                   address_map: {axi_gpio_0: {base: "0x41200000", range: "64K"}},
                   clock_tree: {FCLK_CLK0: {freq_hz: 50000000, targets: [...]}},
                   platform_revision
                }
Test evidence:  T02 — xparameters.h XPAR_AXI_GPIO_0_BASEADDR == Manifest.address_map entry
```

### Phase 2: PL Integration & Build

```
Skill decision:  PL MCP generates system_top HDL.
                 PL MCP writes/verifies XDC constraints.
                 PL MCP runs full build.

MCP capability:
  PL MCP:        generate_system_top → produces system_top.v that instantiates BD wrapper
                 create_project, set_top,
                 synthesize, place_and_route, analyze_timing,
                 generate_bitstream

Input:          BD wrapper HDL (from Platform), constraints (from Board Profile + Skill)
Output:         {
                   system_top.v (PL MCP generates),
                   Bitstream (.bit), bitstream_sha256,
                   PL Build Manifest: {
                     bitstream_path, bitstream_sha256,
                     bd_wrapper_sha256,
                     board_profile_sha256,         ← direct comparison, not via revision
                     xdc_sha256,
                     timing_met: boolean, wns_ns, tns_ns,
                     built_from_platform_revision
                   }
                }
Test evidence:  T02 — timing_met=true, bitstream SHA256 matches
```

### Phase 3: PS Software

```
Skill decision:  Generate BSP from Platform XSA.
                 Write GPIO control C code (readback + UART markers).
                 PS program sets GPIO_TRI=0x0 at init (runtime register write, not IP param).

MCP capability:
  PS MCP:        import_hardware, create_platform, create_bsp,
                 create_app, add_sources, compile

PS MCP automatically produces (built into compile):
  PS Build Manifest: {
    elf_path, elf_sha256,
    platform_xsa_sha256,
    board_profile_sha256,
    xparameters_h_sha256,
    xparameters_addrs: { XPAR_AXI_GPIO_0_BASEADDR: "0x41200000", ... },
    source_files_sha256,            ← collective SHA256 of all .c/.h sources
    built_from_platform_revision
  }

Input:          Platform XSA, Platform Manifest
Output:         ELF, PS Build Manifest
Test evidence:  T01 (UART Hello), T02 (GPIO E2E)
```

### Phase 4: Consistency Check

```
Skill decision:  Verify all revisions and checksums match across manifests.

Check list:
  1. pl_build.built_from_platform_revision == platform.platform_revision
  2. ps_build.built_from_platform_revision == platform.platform_revision
  3. ps_build.platform_xsa_sha256 == platform.xsa_sha256
  4. ps_build.xparameters_addrs matches platform.address_map (field-by-field)
  5. ps_build.board_profile_sha256 == board_profile.sha256
  6. pl_build.board_profile_sha256 == board_profile.sha256   ← direct comparison
  7. All artifact files exist + SHA256 matches manifest

Input:          Platform Manifest, PL Build Manifest, PS Build Manifest, board_profile
Output:         Run Manifest {
                   components: {...},
                   consistency: { errors: [...], warnings: [...] },
                   deployment_plan: [...],
                   status: "ready" | "stale"
                }
Decision:       errors non-empty → abort with "stale artifact" report.
                errors empty → proceed.
```

### Phase 5: Deployment (JTAG)

```
JTAG lock handoff (lock key = hw_server URL + cable serial):
  1. PL MCP:    lock_acquire → program → lock_release
  2. PS MCP:    lock_acquire → initialize/download/run → lock_release
  3. UART capture runs on independent serial port (no JTAG lock needed)

唯一规范流程:
  PS MCP: lock_acquire(hw_server_url, cable_serial)
    → ps.connect_hw_server() → ps.select_target(ARM_DAP)
    → ps.reset("system") → ps.initialize(manifest)
    → ps.start_uart_capture(port, baud) → capture_id
    → ps.download(elf) → ps.run()
    → lock_release(lease_id)              ← JTAG done; UART is independent serial port
    → ps.wait_uart_capture(capture_id, markers, timeout=15s) → result
    → ps.stop_uart_capture(capture_id) → full text + evidence

  若 UART 超时后需要 halt/read_register/run 诊断:
    → lock_acquire(hw_server_url, cable_serial)   ← 重新获取 JTAG 锁
    → ps.halt() → ps.read_register(...) → ... → ps.run()
    → lock_release(lease_id)                      ← 诊断完成, 释放
```

MCP capability for deployment:
  PL MCP:   connect_hw_server, open_hw_target, select_device, program, get_device_status
  PS MCP:   connect_hw_server, select_target, reset, initialize,
            download, run, get_target_status,
            start_uart_capture, wait_uart_capture, stop_uart_capture

Input:     Bitstream, ELF, Run Manifest (deployment_plan)
Output:    execution_evidence (UART capture text, step results, timestamps)
```

### Phase 6: Observation & Pass/Fail

```
Decision rules (from wait_uart_capture result):
  · UART contains "GPIO_E2E_PASS"                         → PASS
  · UART contains "GPIO_E2E_FAIL"                         → FAIL
  · UART timeout (no complete frame within timeout)       → TIMEOUT
  · UART contains incomplete/partial markers              → INCOMPLETE
  · Any manifest consistency error                        → STALE (refuse before deploy)
```

### Phase 7: Debug & Recovery

```
ENV_ERROR:
  · Vivado/XSCT not found → check PATH / config.py
  · hw_server not reachable → check cable + firewall
  → Skill: report exact missing component

TOOL_ERROR:
  · Vivado/XSCT error → parse error, classify
  → Skill: report category, suggest fix

PLATFORM_ERROR:
  · validate fails → unconnected ports / address conflicts
  · Address mismatch → compare PS Build Manifest.xparameters_addrs vs Platform Manifest.address_map
  → Skill: report specific mismatch

PL_BUILD_ERROR:
  · Synthesis fails → check sources, set_top, missing BD wrapper
  · Timing fails (WNS < 0) → report WNS/TNS, suggest constraint review

PS_BUILD_ERROR:
  · Compile/link fails → check BSP config, xparameters.h, includes
  → Skill: report exact error line

JTAG_ERROR:
  · DAP not responding → ps.recover_target("auto") cascade
  · download fails → check ELF exists, ps7_init run, PL programmed
  · run fails → check ps7_init + loadhw executed
  → Skill: cascade recovery, report stage results

UART_ERROR:
  · No output → check: capture started before run()? baud match?
  · Incomplete frame → program crashed mid-sequence
  · Unreadable → baud mismatch
  → Diagnosis sequence (JTAG lock required for halt/read_register):
      1. lock_acquire(hw_server_url, cable_serial)
      2. ps.halt()
      3. ps.read_register("PC")             ← is CPU in abort handler?
      4. ps.read_register("CPSR")           ← verify mode
      5. ps.read_register(0xF8000154)       ← SLCR UART_CLK_CTRL (ref clock divisor)
      6. ps.read_register(0xE0001000+0x18)  ← UART1 BAUDGEN register
      7. ps.read_register(0xE0001000+0x34)  ← UART1 BAUDDIV register
      8. Compute actual baud rate from the above registers
      9. ps.stop_uart_capture(old_capture_id) → clean up stale capture
      10. ps.start_uart_capture(port, corrected_baud) → new capture_id
      11. ps.run()                             ← resume CPU
      12. lock_release(lease_id)               ← release JTAG
      13. ps.wait_uart_capture(new_capture_id, markers, timeout) → result
      14. ps.stop_uart_capture(new_capture_id)

ARTIFACT_STALE:
  · Revision/board_profile/checksum mismatch → refuse, report which field differs
  → Skill: abort, list all mismatches
```

---

## 6. Skill Architecture

### External Interface

```
zynq_skill.execute(requirement) → SkillResult
```

### Internal Modules

```
zynq_skill/
├── SKILL.md
├── requirement/decompose.md
├── platform/gpio_control.md
├── pl/system_top.md
├── ps/baremetal_gpio.md
├── deployment/jtag_deploy.md
├── debug/recovery_tree.md
├── artifact/verify.md
└── references/ax7020_constants.md
```

### Boundaries

- Skill decides: topology, addresses, clock, build order, retry, error classification.
- Skill never: opens Vivado/XSCT process, writes Tcl, directly accesses JTAG/UART hardware.
- MCP does: execute, produce artifact, populate manifest. MCP does not: decide.
- Shared libraries (board_profile, JTAG lock): imported by all MCPs, Skills, Workflow. Not MCP servers.

---

## 7. Minimum MCP Capability Table (GPIO Slice)

### Platform MCP

| # | API | Phase | Input | Output |
|---|-----|-------|-------|--------|
| 1 | `platform.create_design(name, part)` | 1 | name, part | design handle |
| 2 | `platform.add_ps7(preset)` | 1 | board preset name | PS7 instance |
| 3 | `platform.configure_ps7(config)` | 1 | {m_axi_gp0, fclk0_mhz, uart1} | updated config |
| 4 | `platform.add_axi_gpio(name, config)` | 1 | {width:4, direction:"output"} | GPIO instance |
| 5 | `platform.connect_interface(src, dst)` | 1 | port paths | connection |
| 6 | `platform.connect_clock(src, targets)` | 1 | clock source → target list | clock tree |
| 7 | `platform.connect_reset(src, targets)` | 1 | reset source → target list | reset tree |
| 8 | `platform.set_address(master, segment, base, size)` | 1 | explicit address params | address assigned |
| 9 | `platform.validate()` | 1 | — | pass/fail + errors |
| 10 | `platform.generate_wrapper()` | 1 | — | BD wrapper HDL path |
| 11 | `platform.export_hardware(path)` | 1 | output dir | .xsa file |
| 12 | `platform.export_manifest(path)` | 1 | output dir | Manifest JSON |
| | **Platform MCP: 12** | | | |

### PL MCP

| # | API | Phase | Input | Output |
|---|-----|-------|-------|--------|
| 13 | `pl.generate_system_top(wrapper_path)` | 2 | BD wrapper path | system_top.v that instantiates wrapper |
| 14 | `pl.create_project(name, part, sources, constraints)` | 2 | sources + constraints | project handle |
| 15 | `pl.set_top(module)` | 2 | "system_top" | — |
| 16 | `pl.synthesize()` | 2 | — | pass/fail + log |
| 17 | `pl.place_and_route()` | 2 | — | pass/fail + log |
| 18 | `pl.analyze_timing()` | 2 | — | wns_ns, tns_ns, timing_met |
| 19 | `pl.generate_bitstream(path)` | 2 | output path | .bit + PL Build Manifest |
| 20 | `pl.connect_hw_server()` | 5 | — | connection |
| 21 | `pl.open_hw_target()` | 5 | — | target handle |
| 22 | `pl.select_device(id)` | 5 | device index | — |
| 23 | `pl.program(bitstream)` | 5 | bitstream path | DONE status |
| 24 | `pl.get_device_status()` | 5 | — | DONE/INIT/IDCODE |
| | **PL MCP: 12** | | | |

### PS MCP

| # | API | Phase | Input | Output |
|---|-----|-------|-------|--------|
| 25 | `ps.import_hardware(xsa)` | 3 | XSA path | hardware imported |
| 26 | `ps.create_platform(name, hw, domain)` | 3 | platform config | platform handle |
| 27 | `ps.create_bsp(platform)` | 3 | platform handle | BSP generated |
| 28 | `ps.create_app(name, platform)` | 3 | app config | app handle |
| 29 | `ps.add_sources(files)` | 3 | [main.c] | sources added |
| 30 | `ps.compile()` | 3 | — | elf_path + PS Build Manifest |
| 31 | `ps.connect_hw_server()` | 5 | — | connection handle |
| 32 | `ps.select_target(id)` | 5 | ARM DAP id | target handle |
| 33 | `ps.reset(scope)` | 5 | "system" | reset done |
| 34 | `ps.initialize(manifest)` | 5 | manifest (ps7_init + loadhw) | PS initialized |
| 35 | `ps.download(elf)` | 5 | ELF path | download done |
| 36 | `ps.run()` | 5 | — | CPU executing |
| 37 | `ps.halt()` | 7 | — | CPU halted |
| 38 | `ps.get_target_status()` | 5,7 | — | running/halted + PC |
| 39 | `ps.read_register(address_or_name)` | 7 | 0xF8000154 or "PC"/"CPSR"/"SP" | 32-bit value |
| 40 | `ps.start_uart_capture(port, baud)` | 5 | port + baud | capture_id |
| 41 | `ps.wait_uart_capture(capture_id, markers, timeout)` | 5,6 | expected markers, timeout | {status, partial_text} |
| 42 | `ps.stop_uart_capture(capture_id)` | 5,6 | capture_id | full text + evidence |
| 43 | `ps.recover_target(strategy)` | 7 | "auto" | recovered or failure stage |
| | **PS MCP: 19** | | | |

### Shared Libraries

| # | Component | API | Consumers |
|---|-----------|-----|-----------|
| S1 | Board Profile Loader | `board_profile_load(board_id)` | All MCPs, Skills, Workflow |
| S2 | JTAG Lock Library | `lock_acquire / lock_heartbeat / lock_release` | PL MCP, PS MCP |

### API Count

| Component | Count |
|-----------|-------|
| Platform MCP | 12 |
| PL MCP | 12 |
| PS MCP | 19 |
| Shared Libraries | 2 |
| **Total MCP APIs** | **43** |

---

## 8. Artifact Flow

```
Board Profile ─────────────────────────────────────────────────────────┐
  sha256                                                                │
        │                                                               │
        ↓                                                               │
Phase 1: Platform Design                                                │
  Platform XSA (.xsa) + sha256                                          │
  Platform Manifest (.json)                                             │
    ├── xsa_path, xsa_sha256                                            │
    ├── board_profile_sha256                                            │
    ├── bd_wrapper_path, bd_wrapper_sha256                              │
    ├── address_map: {axi_gpio_0: {base: "0x41200000", range: "64K"}}  │
    ├── clock_tree                                                      │
    └── platform_revision                                               │
        │                                                               │
        ├──────────────────┬──────────────────────┐                     │
        ↓                  ↓                      ↓                     │
Phase 2: PL Build    Phase 3: PS Build    Phase 4: Consistency Check    │
  Bitstream (.bit)     ELF (.elf)          Run Manifest                 │
  PL Build Manifest    PS Build Manifest     ├── components              │
    ├── bitstream_sha256 ├── elf_sha256       ├── consistency            │
    ├── bd_wrapper_sha256├── platform_xsa_sha256                        │
    ├── board_profile_sha256 ← direct        ├── deployment_plan        │
    ├── xdc_sha256       ├── board_profile_sha256                       │
    ├── timing_met        │                  └── status: "ready"/"stale"│
    ├── wns_ns           ├── xparameters_h_sha256                       │
    ├── tns_ns           ├── xparameters_addrs                          │
    └── built_from_      │   {XPAR_AXI_GPIO_0_BASEADDR: "0x41200000"}  │
        platform_revision├── source_files_sha256                         │
                         └── built_from_                                │
                             platform_revision                          │
        │                  │                      │                     │
        └──────────────────┴──────────────────────┘                     │
                           │                                            │
      Consistency checks (all MUST pass):                               │
        1. pl/ps built_from_platform_revision == platform_revision      │
        2. ps.platform_xsa_sha256 == platform.xsa_sha256                │
        3. ps.xparameters_addrs == platform.address_map (field x field) │
        4. ps.board_profile_sha256 == board_profile.sha256              │
        5. pl.board_profile_sha256 == board_profile.sha256  (DIRECT)    │
        6. All artifact files exist + SHA256 match                      │
                           │                                            │
                     all pass?                                          │
                    ├── NO  → ABORT "artifact stale"                    │
                    └── YES → Phase 5                                  │
                                   │                                    │
Phase 5: Deployment                                                     │
  PL lock_acquire → pl.program → lock_release                           │
  PS lock_acquire                                                       │
    → ps.reset → ps.initialize → ps.start_uart_capture → capture_id    │
    → ps.download → ps.run                                              │
    → lock_release (JTAG done; UART capture is independent serial port) │
    → ps.wait_uart_capture(capture_id, markers, 15s)                    │
    → ps.stop_uart_capture(capture_id) → full text + evidence            │
                           │                                            │
Phase 6: Observation                                                    │
  UART contains GPIO_E2E_PASS?                                          │
  → Run Manifest execution_evidence populated, status: "verified"       │
```

---

## 9. Current Limitations (B01 scope)

| Limitation | Skill flag |
|------------|-----------|
| No user functional RTL (system_top only) | "Custom Verilog/VHDL not supported in current slice" |
| No interrupts | "Interrupt-driven design requires AXI INTC + GIC config" |
| No DMA | "DMA data path requires AXI DMA + HP port config" |
| No DDR sharing | "PL DDR access requires HP port + memory map config" |
| No Linux/FreeRTOS | "Only bare-metal standalone BSP is supported" |
| No QSPI/SD boot | "Deployment is JTAG-only. BOOT.BIN packaging is manual" |
| No ILA debug | "ILA insertion requires re-build with debug cores" |
| COM port from detection | "COM port from Board Profile + USB VID/PID scan, not hardcoded" |

---

## 10. B01 Completion Gate

- [x] Each phase has Skill decision + MCP capability + input + output + test evidence
- [x] Every MCP API has phase/input/output
- [x] UART: start_capture → wait_capture → stop_capture with capture_id lifecycle
- [x] PS: halt + read_register for SLCR/CPU diagnostic access
- [x] PL: generate_system_top as explicit MCP capability
- [x] AXI GPIO direction="output" as semantic contract (not GPIO_TRI as IP parameter)
- [x] Platform Manifest: xsa_sha256, board_profile_sha256, bd_wrapper_sha256
- [x] PL Build Manifest: board_profile_sha256 for direct comparison
- [x] PS Build Manifest: source_files_sha256 (unified field name)
- [x] Board profile SHA256 directly compared in PL manifest (not via revision)
- [x] JTAG lock: release after download+run (UART capture is independent serial port)
- [x] Shared components consumers: Board Profile → All MCPs, Skills, Workflow; JTAG Lock → PL MCP, PS MCP only. Workflow serializes handoff.
- [x] B01 does not implement MCP, run hardware, or begin B02
