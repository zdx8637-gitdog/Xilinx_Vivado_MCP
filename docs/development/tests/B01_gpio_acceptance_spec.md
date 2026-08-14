# B01 — GPIO Acceptance Specification v1.2.2

> Brick: B01
> 日期: 2026-08-04
> 状态: **✅ 完成 — B01 冻结**
> 关联: [B01_standard_zynq_flow.md](../skill/B01_standard_zynq_flow.md)

---

## 1. Purpose

Define three progressive test items (T00, T01, T02) that verify the GPIO vertical slice with machine-decidable evidence.

---

## 2. T00: PL MCP Baseline Regression

### Objective

Verify existing Vivado MCP remains functional as PL MCP foundation. Scope defined, execution deferred to B04.

| Area | Verified by |
|------|------------|
| Vivado process lifecycle | B04 |
| RTL simulation | B04 |
| Synthesis (0 errors) | B04 |
| Implementation | B04 |
| Timing (WNS >= 0, TNS = 0) | B04 |
| Bitstream generation | B04 |
| Golden breath_led (all stages) | B04 |
| Timeout handling + restart + version guard + shutdown | B04 |

### Pass Condition

All 11 areas produce expected results. Golden breath_led builds and simulates correctly.

---

## 3. T01: PS UART Hello

### Objective

Prove PS software → JTAG download → UART output closed loop **without** AXI GPIO or PL logic.

### Prerequisites

- AX7020 Board Profile validated via `board_profile_load()`
- Platform MCP: PS7 with UART1=MIO48/49, no AXI GPIO
- Platform XSA + Platform Manifest generated
- COM port detected from Board Profile + USB VID:PID scan (not hardcoded)

### ARM Application

```c
#include "xparameters.h"
#include "xil_printf.h"

int main() {
    xil_printf("PS_UART_START\r\n");
    xil_printf("PS_UART_PASS\r\n");
    while (1); // stable loop — CPU observable, UART capture can complete
}
```

### UART Capture Model

UART observation uses an explicit capture lifecycle, NOT a single synchronous read call:

```
capture_id = ps.start_uart_capture(port, baud)
→ ps.download(elf)
→ ps.run()
→ result = ps.wait_uart_capture(capture_id, markers=["PS_UART_START", "PS_UART_PASS"], timeout=10s)
→ full_text = ps.stop_uart_capture(capture_id)
```

This ensures the capture window is open **before** CPU starts executing.

### Deployment Sequence

```
1. PS MCP: lease_id = lock_acquire(hw_server_url, cable_serial)
2. ps.connect_hw_server() → ps.select_target(ARM_DAP)
3. ps.reset("system") → ps.initialize(manifest)  ← ps7_init + loadhw
4. capture_id = ps.start_uart_capture(port, 115200)
5. ps.download(elf) → ps.run()
6. lock_release(lease_id)  ← JTAG done; UART is independent serial port
7. result = ps.wait_uart_capture(capture_id, ["PS_UART_START", "PS_UART_PASS"], timeout=10s)
8. full_text = ps.stop_uart_capture(capture_id)
```

### Success Criteria

| # | Condition | Machine-Decidable |
|---|-----------|-------------------|
| 1 | JTAG download succeeds | `ps.download()` returns OK |
| 2 | capture_id obtained | `ps.start_uart_capture()` returns non-null id |
| 3 | Both markers found | `wait_uart_capture` result.status = "complete" |
| 4 | full_text contains PS_UART_START | Text match |
| 5 | full_text contains PS_UART_PASS | Text match |
| 6 | No "FAIL" or "ERROR" in output | Absence check |

### Failure Modes

| Mode | Expected Detection |
|------|-------------------|
| No UART output | `wait_uart_capture` → timeout → status="timeout" |
| Garbled/partial output | `wait_uart_capture` → timeout (no complete marker frame) |
| PS init failed | `ps.initialize()` returns error |
| Download failed | `ps.download()` returns error |
| CPU crashed before printing | `wait_uart_capture` timeout → then `lock_acquire(hw_server_url, cable_serial)` → `ps.halt()` → `ps.read_register("PC")` → `lock_release(lease_id)`. Shows PC in abort handler.

### UART Baud Diagnosis (when timeout occurs)

Follows the unified Phase 7 flow from B01_standard_zynq_flow.md. Requires JTAG lock re-acquire for halt/register reads.

```
1. lock_acquire(hw_server_url, cable_serial) → lease_id
2. ps.halt()
3. ps.read_register("PC")                    ← is CPU in main or abort handler?
4. ps.read_register("CPSR")                  ← verify CPU mode
5. ps.read_register(0xF8000154)              ← SLCR UART_CLK_CTRL (ref clock divisor)
6. ps.read_register(0xE0001000+0x18)         ← UART1 BAUDGEN register
7. ps.read_register(0xE0001000+0x34)         ← UART1 BAUDDIV register
8. Compute actual baud from SLCR + BAUDGEN + BAUDDIV
9. lock_release(lease_id)                    ← diagnostic reads done
10. If baud mismatch: ps.stop_uart_capture(old_capture_id) → clean up stale capture
11. ps.start_uart_capture(port, corrected_baud) → new_capture_id
12. ps.run()                                 ← resume CPU
13. ps.wait_uart_capture(new_capture_id, markers, timeout) → result
14. ps.stop_uart_capture(new_capture_id)
```

### Evidence

| Artifact | Content |
|----------|---------|
| Platform Manifest | PS7 config, xsa_sha256, board_profile_sha256 |
| PS Build Manifest | elf_sha256, xparameters_h_sha256, platform_xsa_sha256, built_from_platform_revision |
| Run Manifest | deployment_plan + execution_evidence + full UART capture text |

---

## 4. T02: AXI GPIO Complete Vertical Slice

### Objective

ARM controls 4 PL LEDs via AXI GPIO with full artifact chain, readback, and machine-decidable UART evidence.

### Design

| Parameter | Value |
|-----------|-------|
| PS7 | M_AXI_GP0, FCLK0=50MHz, UART1 MIO48/49, IRQ_F2P disabled |
| AXI GPIO | 4-bit, direction="output" (semantic contract → Platform MCP maps to IP config) |
| Address | 0x41200000 (set via `platform.set_address()`) |
| Reset chain | FCLK_RESET0_N → proc_sys_reset → GPIO aresetn |
| Clock | FCLK_CLK0 (50MHz) → GPIO s_axi_aclk |
| PL | PL MCP generates system_top via `pl.generate_system_top(wrapper_path)` |
| LED polarity | active-low: write 1 = LED OFF, write 0 = LED ON |

### GPIO Direction Handling

```
Platform MCP: AXI GPIO configured with direction="output" (semantic parameter)
              MCP maps to Vivado IP configuration as needed.
              Not specified as a raw IP register value at the Skill level.

PS program:   At init, writes GPIO_TRI=0x0 to set data direction at runtime.
              This is the ARM-side register write, not a Platform IP parameter.
              Both layers must agree: Platform sets IP up as output-capable,
              PS confirms data direction at boot.
```

### LED Write Sequence (active-low)

| Logical State | GPIO Write | Binary | Visible |
|--------------|-----------|--------|---------|
| LED0 ON | 0xE | 1110 | PL_LED0 lit |
| LED1 ON | 0xD | 1101 | PL_LED1 lit |
| LED2 ON | 0xB | 1011 | PL_LED2 lit |
| LED3 ON | 0x7 | 0111 | PL_LED3 lit |
| All OFF | 0xF | 1111 | all dark |

> **LED visual is auxiliary evidence only.** The 18 machine-decidable criteria verify AXI GPIO register readback. Physical LED confirmation that the correct pin lights up requires Board Profile + XDC pin mapping verification (B03). B01 does not claim to machine-verify the physical LED state.

### ARM Application Behavior

```
1. Set GPIO_TRI=0x0 (all outputs — runtime register write)
2. xil_printf("GPIO_E2E_START\r\n")
3. For value in [0xE, 0xD, 0xB, 0x7, 0xF]:
   a. Xil_Out32(GPIO_DATA_ADDR, value)
   b. readback = Xil_In32(GPIO_DATA_ADDR)
   c. xil_printf("GPIO_WRITE value=0x%X readback=0x%X\r\n", value, readback)
   d. if readback != value: xil_printf("GPIO_E2E_FAIL at value=0x%X\r\n", value); goto end
   e. spin-wait ~500ms
4. xil_printf("GPIO_E2E_PASS\r\n")
end:
5. while(1);
```

GPIO_BASE from xparameters.h. Consistency check verifies:
`PS Build Manifest.xparameters_addrs["XPAR_AXI_GPIO_0_BASEADDR"] == Platform Manifest.address_map["axi_gpio_0"].base`

### UART Capture Model (same lifecycle as T01)

```
capture_id = ps.start_uart_capture(port, 115200)
→ ps.download(elf) → ps.run()
→ lock_release (JTAG done; UART on independent serial port)
→ result = ps.wait_uart_capture(capture_id, ["GPIO_E2E_START", "GPIO_E2E_PASS"], timeout=15s)
→ full_text = ps.stop_uart_capture(capture_id)
```

### Deployment Sequence

```
1. PL MCP: lease_id = lock_acquire(hw_server_url, cable_serial)
2. pl.connect_hw_server() → pl.open_hw_target() → pl.select_device(FPGA_TAP)
3. pl.program(bitstream) → pl.get_device_status() → DONE=1
4. PL MCP: lock_release(lease_id)
5. PS MCP: lease_id = lock_acquire(hw_server_url, cable_serial)
6. ps.connect_hw_server() → ps.select_target(ARM_DAP)
7. ps.reset("system") → ps.initialize(manifest)
8. capture_id = ps.start_uart_capture(port, 115200)
9. ps.download(elf) → ps.run()
10. PS MCP: lock_release(lease_id)
11. result = ps.wait_uart_capture(capture_id, ["GPIO_E2E_START", "GPIO_E2E_PASS"], timeout=15s)
12. full_text = ps.stop_uart_capture(capture_id)
```

### Success Criteria

| # | Condition | Machine-Decidable |
|---|-----------|-------------------|
| 1 | Platform XSA generated + SHA256 in Manifest | File exists, hash match |
| 2 | Platform Manifest.address_map has axi_gpio_0 @ 0x41200000 | JSON field match |
| 3 | PS Build Manifest.xparameters_addrs["XPAR_AXI_GPIO_0_BASEADDR"] == "0x41200000" | JSON field match |
| 4 | PL MCP generated system_top.v via `pl.generate_system_top()` | File exists, instantiates wrapper |
| 5 | PL bitstream generated + SHA256 in PL Build Manifest | File exists, hash match |
| 6 | PL Build Manifest: timing_met = true (WNS >= 0, TNS = 0) | Boolean + numeric |
| 7 | pl/ps built_from_platform_revision == platform.platform_revision | String equality |
| 8 | ps.platform_xsa_sha256 == platform.xsa_sha256 | Hash equality |
| 9 | ps.board_profile_sha256 == board_profile.sha256 | Hash equality |
| 10 | pl.board_profile_sha256 == board_profile.sha256 (direct) | Hash equality |
| 11 | ps.xparameters_addrs matches platform.address_map | Field-by-field |
| 12 | pl.program() returns DONE=1 | Status field |
| 13 | ps.initialize() succeeds | Return OK |
| 14 | UART capture contains GPIO_E2E_START | Text match |
| 15 | All 5 GPIO_WRITE lines with readback == value | Regex: value == readback |
| 16 | UART contains GPIO_E2E_PASS | Text match |
| 17 | UART does NOT contain GPIO_E2E_FAIL | Absence check |
| 18 | Run Manifest execution_evidence populated | JSON completeness |

### UART Failure Diagnosis

| Symptom | Diagnosis via PS MCP |
|---------|---------------------|
| No output, timeout | `ps.halt()` → `ps.read_register("PC")` → CPU location |
| PC in abort handler | `ps.read_register("DFSR")` → data abort source |
| PC in main | Read SLCR UART_CLK_CTRL + BAUDGEN + BAUDDIV → baud check |
| Incomplete frame | capture started too late OR program crashed mid-sequence |
| Unreadable | Read SLCR/UART registers → baud mismatch confirmed |

---

## 5. Fault Injection Catalog (T02)

### F-GPIO-001: Stale ELF

| Field | Value |
|-------|-------|
| Inject | Build PS ELF against Platform v1. Rebuild Platform (v2, different address). Keep old ELF. |
| Detection | Phase 4: `ps_build.built_from_platform_revision != platform.platform_revision` |
| Error | "PS ELF built from platform sha256:aaa, current is sha256:bbb" |
| Auto-recovery | No |

### F-GPIO-002: Wrong Board Revision

| Field | Value |
|-------|-------|
| Inject | Replace board_profile.json with modified copy (different sha256) |
| Detection | Phase 4: `ps.board_profile_sha256 != board_profile.sha256` or `pl.board_profile_sha256 != board_profile.sha256` |
| Error | "board_profile mismatch" |
| Auto-recovery | No |

### F-GPIO-003: XSA/ELF Mismatch (dual detection)

| Field | Value |
|-------|-------|
| Inject | Build Platform v1 with GPIO @ 0x41200000 → produce `platform_v1.xsa` + Manifest v1. Swap Platform XSA to `platform_v2.xsa` (GPIO @ 0x41210000, different platform_revision) before PS compile, while keeping old Platform Manifest v1. |
| Detection #1 | Phase 4: `ps_build.platform_xsa_sha256 != platform.xsa_sha256` — XSA file used by PS compile doesn't match the Platform Manifest's recorded XSA |
| Error #1 | "PS ELF platform_xsa_sha256 doesn't match Platform Manifest xsa_sha256" |
| Detection #2 | (If Detection #1 is bypassed): `ps.xparameters_addrs["XPAR_AXI_GPIO_0_BASEADDR"] != platform.address_map["axi_gpio_0"].base` — address field mismatch |
| Error #2 | "PS xparameters GPIO address 0x41210000 != Platform Manifest 0x41200000" |
| Auto-recovery | No — requires matching Platform/PS rebuild |
| Mechanism | Both checks run in Phase 4 consistency check. Detection #1 fires first (XSA SHA256 mismatch); Detection #2 is the defense-in-depth layer if someone manually patches the SHA256 field. |
| Note | This is intentionally a dual-detection fault. The XSA swap causes both the SHA256 and the address to diverge. The consistency check reports both errors. |

### F-GPIO-004: AXI Address Conflict

| Field | Value |
|-------|-------|
| Inject | `platform.set_address(master, "axi_gpio_0", 0x41200000, 64K)` then `platform.set_address(master, "axi_gpio_1", 0x41200000, 64K)` |
| Detection | Phase 1: second `set_address()` detects overlap |
| Error | "Address conflict: 0x41200000 already assigned to axi_gpio_0" |
| Auto-recovery | No |

### F-GPIO-005: UART Baud Mismatch

| Field | Value |
|-------|-------|
| Inject | `ps.start_uart_capture(port, baud=9600)` while Platform PS7 baud=115200 |
| Detection | Phase 6: `wait_uart_capture` timeout (no complete marker frame) |
| Error | result.status = "timeout" |
| Auto-recovery | Yes — `lock_acquire(hw_server_url, cable_serial)` → `ps.halt()` → `ps.read_register(0xF8000154)` (SLCR UART_CLK_CTRL) → `ps.read_register(...UART1_BAUDGEN)` → `ps.read_register(...UART1_BAUDDIV)` → compute actual baud → `lock_release(lease_id)` → `ps.stop_uart_capture(old_capture_id)` → `ps.start_uart_capture(port, corrected_baud)` → `ps.run()` → `wait_uart_capture` → `stop_uart_capture` |
| Evidence | First attempt: timeout. Second attempt with corrected baud: complete frame. |

### F-GPIO-006: JTAG Connection Recovery

| Field | Value |
|-------|-------|
| Inject | Between PL program and PS connect, cause controlled connection loss (test-dedicated hw_server instance or TCP disconnect on test port — NOT kill system hw_server) |
| Detection | Phase 5: `ps.connect_hw_server()` or `ps.select_target()` returns error |
| Error | Connection refused / target not found |
| Auto-recovery | Yes — `ps.recover_target("auto")` cascade (internally: re-connect → re-select target → reset → initialize). No separate `reconnect_target()` API needed. |
| Evidence | Error log + recovery stage results + eventual deployment PASS |

---

## 6. Agent2 Black-Box Rules

### Agent2 Receives

- User requirement: "ARM controls 4 PL LEDs via AXI GPIO, UART outputs machine-decidable PASS/FAIL"
- Unified Zynq Skill (SKILL.md + internal modules)
- Registered MCPs: Platform MCP, PL MCP, PS MCP
- Board Profile (board_profile.json)
- Board information PDFs (S1 FPGA tutorial, S2 Vitis tutorial)
- Clean working directory

### Agent2 Must NOT Receive

- Agent1 conversation logs
- Golden project source files
- Hidden Tcl scripts
- `run_tcl` in normal flow
- Manual patching steps
- Pre-built XSA/Bitstream/ELF

### Agent2 Pass Condition

Independent reproduction of T02 (all 18 criteria) from clean directory. Correct rejection of all 6 fault injections.

---

## 7. Acceptance Matrix

| Test | Proven Capability | Evidence |
|------|------------------|----------|
| T00 | PL MCP baseline | 11 regression results (B04) |
| T01 | PS JTAG + UART with capture lifecycle | UART capture: PS_UART_START + PS_UART_PASS |
| T02 | GPIO E2E: Platform → PL(system_top) → PS → JTAG → UART → AXI GPIO register readback | All 18 criteria + manifests. Physical LED observation is auxiliary only. B03 adds static pin-mapping evidence via Board Profile + XDC verification. |
| F-001 | Stale ELF rejected | Run Manifest consistency error |
| F-002 | Board revision guard | Run Manifest consistency error |
| F-003 | XSA/ELF address mismatch (via xparameters_addrs field) | Run Manifest consistency error |
| F-004 | Address conflict via explicit set_address | Platform MCP error |
| F-005 | Baud mismatch → SLCR diagnosis → recovery | Timeout → corrected capture |
| F-006 | JTAG recovery | Error log + cascade + PASS |
| Agent2 | Skill portability | Independent reproduction |

---

## 8. B01 Gate Checklist

- [x] T00 scope defined (deferred to B04)
- [x] T01/T02 UART capture: start → wait → stop with capture_id lifecycle
- [x] T01/T02 UART capture started before ps.run()
- [x] T01 CPU enters stable loop after printing
- [x] T02 active-low LED sequence: 0xE → 0xD → 0xB → 0x7 → 0xF
- [x] T02 GPIO direction="output" (semantic) + ARM TRI=0x0 (runtime) — two-layer model
- [x] T02 GPIO address from Platform Manifest, verified against PS Build Manifest xparameters_addrs
- [x] PS Build Manifest: source_files_sha256, xparameters_h_sha256, xparameters_addrs, platform_xsa_sha256
- [x] PL Build Manifest: board_profile_sha256 (direct comparison), bd_wrapper_sha256
- [x] Platform Manifest: xsa_sha256, board_profile_sha256, bd_wrapper_sha256
- [x] `pl.generate_system_top(wrapper_path)` as explicit generate API
- [x] PS API: `halt`, `read_register(address_or_name)` for SLCR/CPU diagnosis
- [x] 6 fault injections: method + detection + error + auto-recovery decision
- [x] F-GPIO-003 uses PS Build Manifest xparameters_addrs field (no ELF binary parse)
- [x] F-GPIO-004 uses explicit set_address() for deterministic conflict
- [x] F-GPIO-006 uses controlled disconnect (not kill system hw_server)
- [x] JTAG lock: PL acquire→release → PS acquire→release (explicit)
- [x] JTAG lock released after download+run; UART capture is independent serial port
- [x] timing_met = (WNS >= 0 AND TNS = 0)
- [x] COM port from Board Profile + detection (not hardcoded COM4)
- [x] Agent2 black-box rules defined
- [x] B01 does not implement MCP, run hardware, or begin B02
