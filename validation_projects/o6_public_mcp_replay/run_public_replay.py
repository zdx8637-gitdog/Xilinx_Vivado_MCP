"""O6 Agent1 replay: clean GPIO project through the public zynq MCP only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


BOARD_ID = "ALINX_AX7020_v1.0"
PART = "xc7z020clg400-2"
TERMINAL = {
    "SUCCEEDED", "FAILED", "TIMED_OUT", "INTERRUPTED",
    "OUTCOME_UNKNOWN", "RECOVERY_REQUIRED", "CANCELLED",
}

XDC_TEXT = """# O6 GPIO LED constraints for ALINX AX7020
set_property PACKAGE_PIN J16 [get_ports {gpio_led[3]}]
set_property PACKAGE_PIN K16 [get_ports {gpio_led[2]}]
set_property PACKAGE_PIN M15 [get_ports {gpio_led[1]}]
set_property PACKAGE_PIN M14 [get_ports {gpio_led[0]}]
set_property IOSTANDARD LVCMOS33 [get_ports {gpio_led[*]}]
"""

MAIN_C = r'''/* O6 public-MCP GPIO E2E for ALINX AX7020. */
#include "xil_io.h"

#define UART1_BASE  0xE0001000U
#define UART_SR     0x2CU
#define UART_FIFO   0x30U
#define UART_TXFULL (1U << 4)
#define LED_BASE    0x41200000U
#define LED_DATA    0x00U
#define LED_TRI     0x04U

static void uart_putc(char c)
{
    while ((Xil_In32(UART1_BASE + UART_SR) & UART_TXFULL) != 0U) { }
    Xil_Out8(UART1_BASE + UART_FIFO, (u8)c);
}

static void uart_send(const char *s)
{
    while (*s != '\0') { uart_putc(*s++); }
}

static void uart_hex4(unsigned int v)
{
    static const char hex[] = "0123456789ABCDEF";
    uart_putc(hex[v & 0xFU]);
}

static void uart_init(void)
{
    Xil_Out32(UART1_BASE + 0x00U, 0x00000000U);
    Xil_Out32(UART1_BASE + 0x04U, 0x00000020U);
    Xil_Out32(UART1_BASE + 0x18U, 49U);
    Xil_Out32(UART1_BASE + 0x34U, 16U);
    Xil_Out32(UART1_BASE + 0x00U, 0x00000014U);
}

static void delay_round(void)
{
    volatile unsigned int i;
    for (i = 0U; i < 200000000U; ++i) {
        __asm__ volatile("" ::: "memory");
    }
}

int main(void)
{
    unsigned int pattern = 0xAU;
    unsigned int i;
    uart_init();
    Xil_Out32(LED_BASE + LED_TRI, 0U);
    uart_send("=== AX7020 GPIO O6 ===\r\n");
    for (i = 0U; i < 8U; ++i) {
        unsigned int physical = (~pattern) & 0xFU;
        unsigned int read_pattern;
        Xil_Out32(LED_BASE + LED_DATA, physical);
        read_pattern = (~Xil_In32(LED_BASE + LED_DATA)) & 0xFU;
        uart_send("WROTE:0x"); uart_hex4(pattern);
        uart_send(" READ:0x"); uart_hex4(read_pattern); uart_send("\r\n");
        if (read_pattern != pattern) {
            uart_send("GPIO_E2E_FAIL\r\n");
            while (1) { }
        }
        pattern ^= 0xFU;
        delay_round();
    }
    uart_send("GPIO_E2E_PASS\r\n");
    while (1) { }
    return 0;
}
'''


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _text(result: Any) -> str:
    parts = [item.text for item in result.content
             if isinstance(item, mcp_types.TextContent)]
    return "".join(parts)


class Replay:
    def __init__(self, workspace: Path, runtime_root: Path):
        self.workspace = workspace
        self.runtime_root = runtime_root
        self.evidence = workspace / "evidence"
        self.calls_path = self.evidence / "public_calls.jsonl"
        self.timeline_path = self.evidence / "operation_timeline.jsonl"
        self.session: ClientSession | None = None
        self.session_id = ""
        self.capture_id = ""
        self.jtag_connected = False
        self.summary: dict[str, Any] = {
            "status": "RUNNING",
            "started_at": _utc(),
            "workspace": str(workspace),
            "runtime_root": str(runtime_root),
            "public_mcp_only": True,
            "phases": {},
            "artifacts": {},
            "cleanup": {},
        }

    def prepare(self) -> None:
        if self.workspace.exists():
            raise RuntimeError(f"workspace must not exist: {self.workspace}")
        if self.runtime_root.exists():
            raise RuntimeError(f"runtime_root must not exist: {self.runtime_root}")
        self.evidence.mkdir(parents=True)
        self.runtime_root.mkdir(parents=True)

    def append(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False,
                                    default=_jsonable, sort_keys=True) + "\n")

    async def call(self, tool: str, arguments: dict[str, Any] | None = None,
                   *, phase: str = "") -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("MCP session is not active")
        args = arguments or {}
        started = time.monotonic()
        result = await self.session.call_tool(tool, args)
        raw = _text(result)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            payload = {"status": "transport_error", "raw": raw,
                       "parse_error": str(exc)}
        self.append(self.calls_path, {
            "at": _utc(), "phase": phase, "tool": tool,
            "arguments": args, "elapsed_s": round(time.monotonic() - started, 3),
            "response": payload,
        })
        return payload

    async def diagnose(self, phase: str, operation_id: str,
                       reason: str) -> dict[str, Any]:
        diagnostic = await self.call("diagnose_execution", {}, phase=phase)
        self.append(self.timeline_path, {
            "at": _utc(), "phase": phase, "operation_id": operation_id,
            "event": "DIAGNOSE", "reason": reason,
            "diagnostic": diagnostic,
        })
        return diagnostic

    async def command(self, tool: str, arguments: dict[str, Any], phase: str,
                      *, wait_slice_s: float = 60.0,
                      overall_s: float = 7200.0) -> dict[str, Any]:
        admission = await self.call(tool, arguments, phase=phase)
        if admission.get("status") != "success":
            raise RuntimeError(f"{tool} admission failed: {admission}")
        data = admission.get("data") or {}
        operation_id = data.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id:
            raise RuntimeError(f"{tool} returned no operation_id: {admission}")
        self.append(self.timeline_path, {
            "at": _utc(), "phase": phase, "tool": tool,
            "operation_id": operation_id, "event": "ADMITTED",
            "admission": data,
        })
        deadline = time.monotonic() + overall_s
        while True:
            if time.monotonic() >= deadline:
                await self.diagnose(phase, operation_id, "WHITEBOX_WAIT_BUDGET")
                raise RuntimeError(f"{tool} exceeded replay wait budget")
            wait = await self.call(
                "wait_operation",
                {"operation_id": operation_id,
                 "timeout_s": min(wait_slice_s, 900.0)},
                phase=phase,
            )
            if wait.get("status") != "success":
                raise RuntimeError(f"wait_operation failed for {tool}: {wait}")
            observed = wait.get("data") or {}
            self.append(self.timeline_path, {
                "at": _utc(), "phase": phase, "tool": tool,
                "operation_id": operation_id, "event": "OBSERVED",
                "status": observed.get("status"),
                "status_source": observed.get("status_source"),
                "backend": observed.get("backend"),
                "observed_state": observed.get("observed_state"),
                "vendor_status": observed.get("vendor_status"),
                "current_step": observed.get("current_step"),
                "progress_pct": observed.get("progress_pct"),
                "observation_quality": observed.get("observation_quality"),
                "last_progress_at": observed.get("last_progress_at"),
                "artifact_state": observed.get("artifact_state"),
                "deadline_at": observed.get("deadline_at"),
                "recommended_action": observed.get("recommended_action"),
                "wait_timed_out": observed.get("wait_timed_out", False),
                "response": observed,
            })
            status = str(observed.get("status", ""))
            action = str(observed.get("recommended_action", "") or "").upper()
            if status in TERMINAL:
                if status != "SUCCEEDED":
                    diagnostic = await self.diagnose(
                        phase, operation_id, f"TERMINAL_{status}")
                    if action == "RECOVER":
                        recovered = await self.call(
                            "recover_execution", {}, phase=phase)
                        self.append(self.timeline_path, {
                            "at": _utc(), "phase": phase,
                            "operation_id": operation_id,
                            "event": "RECOVER_AFTER_FAILURE",
                            "diagnostic": diagnostic,
                            "response": recovered,
                        })
                    raise RuntimeError(f"{tool} terminal={status}: {observed}")
                return observed
            if action == "DIAGNOSE":
                await self.diagnose(phase, operation_id, "RECOMMENDED_ACTION")
            elif action in {"RECOVER", "STOP"}:
                await self.diagnose(phase, operation_id, action)
                raise RuntimeError(
                    f"{tool} requested {action} while status={status}")

    @staticmethod
    def payload(terminal: dict[str, Any]) -> dict[str, Any]:
        result = terminal.get("result")
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            return result["data"]
        return {}

    @staticmethod
    def exactly_one(directory: Path) -> Path:
        candidates = sorted(directory.glob("sha256_*.json"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"expected exactly one Manifest in {directory}, got {candidates}")
        return candidates[0]

    async def run(self) -> None:
        self.prepare()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        env["ZYNQ_RUNTIME_ROOT"] = str(self.runtime_root)
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcps.zynq_mcp.server"],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                self.session = session
                await session.initialize()
                listed = await session.list_tools()
                schemas = [{"name": t.name, "description": t.description,
                            "inputSchema": t.inputSchema} for t in listed.tools]
                (self.evidence / "tools_schema.json").write_text(
                    json.dumps(schemas, indent=2, ensure_ascii=False),
                    encoding="utf-8", newline="\n")
                names = {item["name"] for item in schemas}
                required = {
                    "create_session", "get_execution_state", "wait_operation",
                    "get_operation_status", "diagnose_execution", "recover_execution",
                    "platform_generate", "pl_generate_system_top",
                    "pl_create_project", "pl_generate_target", "pl_synthesize",
                    "pl_place", "pl_route", "pl_analyze_timing",
                    "pl_generate_bitstream", "ps_import_hardware",
                    "ps_create_platform", "ps_create_bsp", "ps_create_app",
                    "ps_add_sources", "ps_compile", "ps_get_build_status",
                    "ps_read_elf_info", "verify_consistency",
                    "ps_connect_hw_server", "ps_disconnect_hw_server",
                    "ps_list_targets", "ps_select_target", "ps_halt_target",
                    "ps_reset_target", "ps_initialize_ps", "pl_program_fpga",
                    "ps_load_hardware", "ps_download_elf", "ps_run_target",
                    "ps_list_serial_ports", "ps_start_uart_capture",
                    "ps_wait_uart_capture", "ps_stop_uart_capture",
                    "evaluate_observation", "close_session",
                }
                if required - names:
                    raise RuntimeError(
                        f"public tools missing: {sorted(required - names)}")
                try:
                    await self._workflow()
                finally:
                    await self._public_cleanup()
                    self.session = None

    async def _workflow(self) -> None:
        created = await self.call("create_session", {
            "board_id": BOARD_ID, "project_path": str(self.workspace)},
            phase="P0")
        if created.get("status") != "success":
            raise RuntimeError(f"create_session failed: {created}")
        cdata = created["data"]
        self.session_id = cdata["session_id"]
        board_sha = cdata["board_profile_sha256"]
        state = await self.call("get_execution_state", {}, phase="P0")
        if state.get("data", {}).get("current_stage") != "PLATFORM_DESIGN":
            raise RuntimeError(f"unexpected initial stage: {state}")
        self.summary["phases"]["P0"] = {
            "session_id": self.session_id,
            "board_profile_sha256": board_sha,
        }

        p1 = await self.command("platform_generate", {}, "P1",
                                wait_slice_s=60, overall_s=3600)
        p1data = self.payload(p1)
        wrapper = Path(p1data["wrapper_path"])
        xsa = Path(p1data["xsa_path"])
        for artifact in (wrapper, xsa, Path(p1data["manifest_path"])):
            if not artifact.is_file():
                raise RuntimeError(f"P1 artifact missing: {artifact}")
        self.summary["phases"]["P1"] = p1data

        systop = await self.command(
            "pl_generate_system_top",
            {"wrapper_path": p1data.get("wrapper_rel") or str(wrapper)},
            "P2_SYSTEM_TOP", wait_slice_s=20, overall_s=300)
        systop_data = self.payload(systop)
        system_top = Path(systop_data.get(
            "output_path", self.workspace / "rtl" / "system_top.v"))
        if not system_top.is_file():
            system_top = self.workspace / "rtl" / "system_top.v"
        if not system_top.is_file():
            raise RuntimeError("system_top.v missing after public generation")

        xdc = self.workspace / "xdc" / "gpio_led.xdc"
        xdc.parent.mkdir(parents=True)
        xdc.write_text(XDC_TEXT, encoding="utf-8", newline="\n")
        bd = (self.workspace / "vivado" / "platform" /
              "platform_project.srcs" / "sources_1" / "bd" /
              "platform_bd" / "platform_bd.bd")
        if not bd.is_file():
            raise RuntimeError(f"expected Platform BD missing: {bd}")
        pl_dir = self.workspace / "vivado" / "gpio_pl"
        await self.command("pl_create_project", {
            "name": "gpio_pl", "part": PART,
            "sources": [str(bd), str(wrapper), str(system_top)],
            "constraints": [str(xdc)], "project_dir": str(pl_dir),
            "top": "system_top", "force": True,
        }, "P2_CREATE_PROJECT", wait_slice_s=30, overall_s=600)
        await self.command("pl_generate_target", {"target_type": "synthesis"},
                           "P2_GENERATE_TARGET", wait_slice_s=60,
                           overall_s=1800)
        await self.command("pl_synthesize", {"top": "system_top"},
                           "P2_SYNTHESIS", wait_slice_s=60, overall_s=5400)
        await self.command("pl_place", {}, "P2_PLACE",
                           wait_slice_s=60, overall_s=5400)
        await self.command("pl_route", {}, "P2_ROUTE",
                           wait_slice_s=60, overall_s=5400)
        timing = await self.command("pl_analyze_timing", {}, "P2_TIMING",
                                    wait_slice_s=30, overall_s=600)
        timing_data = self.payload(timing)
        if timing_data.get("timing_met") is not True:
            raise RuntimeError(f"timing did not pass: {timing_data}")
        bitstream = self.workspace / "bitstream" / "gpio_led.bit"
        bitstream.parent.mkdir(parents=True)
        bit_op = await self.command(
            "pl_generate_bitstream", {"path": str(bitstream), "force": True},
            "P2_BITSTREAM", wait_slice_s=60, overall_s=3600)
        if not bitstream.is_file():
            raise RuntimeError(f"bitstream missing: {bitstream}")
        if bit_op.get("artifact_state") != "PUBLISHED":
            raise RuntimeError(f"PL artifact state not PUBLISHED: {bit_op}")
        pl_manifest = self.exactly_one(self.workspace / "manifests" / "pl")
        self.summary["phases"]["P2"] = {
            "system_top": str(system_top), "xdc": str(xdc),
            "bitstream": str(bitstream), "bitstream_sha256": _sha256(bitstream),
            "pl_manifest": str(pl_manifest),
            "pl_manifest_sha256": _sha256(pl_manifest),
            "timing": timing_data,
        }

        inputs = self.workspace / "inputs"
        inputs.mkdir()
        staged_xsa = inputs / "platform.xsa"
        shutil.copyfile(xsa, staged_xsa)
        ps_args = {"session_id": self.session_id}
        await self.command("ps_import_hardware", {
            **ps_args, "xsa_path": str(staged_xsa),
            "project_path": str(self.workspace)}, "P3_IMPORT",
            wait_slice_s=30, overall_s=600)
        await self.command("ps_create_platform", {
            **ps_args, "name": "gpio_platform",
            "project_path": str(self.workspace)}, "P3_PLATFORM",
            wait_slice_s=60, overall_s=1800)
        await self.command("ps_create_bsp", {
            **ps_args, "platform_name": "gpio_platform",
            "project_path": str(self.workspace)}, "P3_BSP",
            wait_slice_s=60, overall_s=1800)
        await self.command("ps_create_app", {
            **ps_args, "name": "gpio_app",
            "project_path": str(self.workspace)}, "P3_APP",
            wait_slice_s=30, overall_s=600)
        source = self.workspace / "src" / "main.c"
        source.parent.mkdir()
        source.write_text(MAIN_C, encoding="utf-8", newline="\n")
        await self.command("ps_add_sources", {
            **ps_args, "app_name": "gpio_app", "files": [str(source)]},
            "P3_SOURCES", wait_slice_s=30, overall_s=600)
        compiled = await self.command(
            "ps_compile", {**ps_args, "app_name": "gpio_app"},
            "P3_COMPILE", wait_slice_s=60, overall_s=3600)
        if compiled.get("artifact_state") != "PUBLISHED":
            raise RuntimeError(f"PS artifact state not PUBLISHED: {compiled}")
        compile_data = self.payload(compiled)
        elf_raw = compile_data.get("elf") or compile_data.get("elf_path")
        if not isinstance(elf_raw, str) or not elf_raw:
            status = await self.command("ps_get_build_status", ps_args,
                                        "P3_BUILD_STATUS", wait_slice_s=20,
                                        overall_s=300)
            apps = self.payload(status).get("apps", [])
            matches = [a.get("elf") for a in apps
                       if a.get("name") == "gpio_app" and a.get("elf")]
            if len(matches) != 1:
                raise RuntimeError(f"cannot identify gpio_app ELF: {apps}")
            elf_raw = matches[0]
        elf = Path(elf_raw)
        if not elf.is_absolute():
            elf = self.workspace / elf
        if not elf.is_file():
            raise RuntimeError(f"ELF missing: {elf}")
        elf_info = await self.command(
            "ps_read_elf_info", {**ps_args, "elf_path": str(elf)},
            "P3_ELF_VERIFY", wait_slice_s=20, overall_s=300)
        info = self.payload(elf_info)
        if info.get("class") not in ("ELFCLASS32", None) and \
                info.get("elf_class") != "ELFCLASS32":
            raise RuntimeError(f"unexpected ELF class: {info}")
        ps_manifest = self.exactly_one(self.workspace / "manifests" / "ps")
        self.summary["phases"]["P3"] = {
            "source": str(source), "source_sha256": _sha256(source),
            "elf": str(elf), "elf_sha256": _sha256(elf),
            "compile": compile_data, "elf_info": info,
            "ps_manifest": str(ps_manifest),
            "ps_manifest_sha256": _sha256(ps_manifest),
        }

        platform_manifest = self.exactly_one(
            self.workspace / "manifests" / "platform")
        consistency = await self.call("verify_consistency", {
            "platform_manifest_path": str(platform_manifest),
            "pl_build_manifest_path": str(pl_manifest),
            "ps_build_manifest_path": str(ps_manifest),
            "board_profile_sha256": board_sha,
            "resolve_root": str(self.workspace),
        }, phase="P4")
        cdata = consistency.get("data") or {}
        summary = cdata.get("summary") or {}
        if not (consistency.get("status") == "success"
                and cdata.get("all_passed") is True
                and cdata.get("errors") == []
                and summary.get("failed") == 0
                and summary.get("skipped") == 0):
            raise RuntimeError(f"consistency failed: {consistency}")
        self.summary["phases"]["P4"] = cdata

        await self.command("ps_connect_hw_server", {
            **ps_args, "url": "localhost:3121"}, "P5_JTAG_CONNECT",
            wait_slice_s=20, overall_s=300)
        self.jtag_connected = True
        ports_op = await self.command("ps_list_serial_ports", ps_args,
                                      "P5_UART_ENUM", wait_slice_s=20,
                                      overall_s=300)
        ports = self.payload(ports_op).get("ports", [])
        if len([p for p in ports if p.get("port") == "COM4"]) != 1:
            raise RuntimeError(f"COM4 not uniquely present: {ports}")
        targets_op = await self.command("ps_list_targets", ps_args,
                                        "P5_TARGETS", wait_slice_s=20,
                                        overall_s=300)
        targets = self.payload(targets_op).get("targets", [])
        arms = [t for t in targets if "ARM Cortex-A9" in str(t.get("name", ""))
                and "#0" in str(t.get("name", ""))]
        if len(arms) != 1:
            await self.command("ps_ensure_arm_accessible", ps_args,
                               "P5_ARM_RECOVERY", wait_slice_s=30,
                               overall_s=300)
            targets_op = await self.command("ps_list_targets", ps_args,
                                            "P5_TARGETS_RECHECK",
                                            wait_slice_s=20, overall_s=300)
            targets = self.payload(targets_op).get("targets", [])
            arms = [t for t in targets
                    if "ARM Cortex-A9" in str(t.get("name", ""))
                    and "#0" in str(t.get("name", ""))]
        if len(arms) != 1:
            raise RuntimeError(f"ARM Cortex-A9 #0 not unique: {targets}")

        capture = await self.command("ps_start_uart_capture", {
            **ps_args, "port": "COM4", "baudrate": 115200},
            "P5_UART_START", wait_slice_s=20, overall_s=300)
        self.capture_id = self.payload(capture)["capture_id"]
        await self.command("ps_select_target", {
            **ps_args, "target_id": arms[0]["id"]}, "P5_SELECT",
            wait_slice_s=20, overall_s=300)
        await self.command("ps_halt_target", ps_args, "P5_HALT",
                           wait_slice_s=20, overall_s=300)
        await self.command("ps_reset_target", {**ps_args, "scope": "system"},
                           "P5_RESET", wait_slice_s=20, overall_s=300)
        ps7_candidates = [
            self.workspace / "gpio_platform" / "hw" / "ps7_init.tcl",
            self.workspace / "gpio_platform" / "export" /
            "gpio_platform" / "hw" / "ps7_init.tcl",
        ]
        ps7 = next((p for p in ps7_candidates if p.is_file()), None)
        if ps7 is None:
            raise RuntimeError(f"public PS7 init artifact missing: {ps7_candidates}")
        await self.command("ps_initialize_ps", {
            **ps_args, "tcl_path": str(ps7)}, "P5_PS_INIT",
            wait_slice_s=30, overall_s=600)
        await self.command("pl_program_fpga", {"bitstream_path": str(bitstream)},
                           "P5_PROGRAM", wait_slice_s=30, overall_s=600)
        await self.command("ps_load_hardware", {
            **ps_args, "xsa_path": str(xsa)}, "P5_LOADHW",
            wait_slice_s=20, overall_s=300)
        await self.command("ps_download_elf", {
            **ps_args, "elf_path": str(elf)}, "P5_DOWNLOAD",
            wait_slice_s=20, overall_s=300)
        await self.command("ps_run_target", ps_args, "P5_RUN",
                           wait_slice_s=20, overall_s=300)
        uart_wait = await self.command("ps_wait_uart_capture", {
            **ps_args, "capture_id": self.capture_id,
            "markers": ["WROTE:0x", "GPIO_E2E_PASS"], "timeout_s": 90.0},
            "P5_UART_WAIT", wait_slice_s=60, overall_s=180)
        wait_data = self.payload(uart_wait)
        if wait_data.get("status") != "matched":
            raise RuntimeError(f"UART markers did not match: {wait_data}")
        stopped = await self.command("ps_stop_uart_capture", {
            **ps_args, "capture_id": self.capture_id}, "P5_UART_STOP",
            wait_slice_s=20, overall_s=300)
        self.capture_id = ""
        uart = self.payload(stopped).get("text", "")
        if not isinstance(uart, str):
            raise RuntimeError("UART text is not a string")
        (self.evidence / "uart.txt").write_text(
            uart, encoding="utf-8", newline="\n")
        if "GPIO_E2E_PASS" not in uart or "GPIO_E2E_FAIL" in uart:
            raise RuntimeError(f"UART verdict markers invalid: {uart!r}")
        pairs = re.findall(r"WROTE:0x([0-9A-F]) READ:0x([0-9A-F])", uart)
        if len(pairs) != 8 or any(w != r for w, r in pairs):
            raise RuntimeError(f"GPIO readback evidence invalid: {pairs}")
        verdict = await self.call("evaluate_observation", {
            "uart_text": uart, "pass_marker": "GPIO_E2E_PASS",
            "fail_marker": "GPIO_E2E_FAIL"}, phase="P6")
        if verdict.get("data", {}).get("verdict") != "PASS":
            raise RuntimeError(f"observation did not PASS: {verdict}")
        resources = await self.call("get_execution_state", {}, phase="P6")
        self.summary["phases"]["P5"] = {
            "uart_wait": wait_data, "gpio_pairs": pairs,
            "ps7_init": str(ps7), "resources": resources.get("data", {}).get("resources"),
        }
        self.summary["phases"]["P6"] = verdict["data"]
        self.summary["artifacts"] = {
            "platform_manifest": {"path": str(platform_manifest),
                                  "sha256": _sha256(platform_manifest)},
            "pl_manifest": {"path": str(pl_manifest),
                            "sha256": _sha256(pl_manifest)},
            "ps_manifest": {"path": str(ps_manifest),
                            "sha256": _sha256(ps_manifest)},
            "xsa": {"path": str(xsa), "sha256": _sha256(xsa)},
            "bitstream": {"path": str(bitstream),
                          "sha256": _sha256(bitstream)},
            "elf": {"path": str(elf), "sha256": _sha256(elf)},
            "uart": {"path": str(self.evidence / "uart.txt"),
                     "sha256": _sha256(self.evidence / "uart.txt")},
        }
        self.summary["status"] = "PASS"

    async def _public_cleanup(self) -> None:
        if self.session is None:
            return
        errors: list[str] = []
        if self.capture_id:
            try:
                await self.command("ps_stop_uart_capture", {
                    "session_id": self.session_id,
                    "capture_id": self.capture_id}, "CLEANUP_UART",
                    wait_slice_s=20, overall_s=120)
                self.capture_id = ""
            except Exception as exc:
                errors.append(f"uart:{exc}")
        if self.jtag_connected:
            try:
                await self.command("ps_disconnect_hw_server", {
                    "session_id": self.session_id}, "CLEANUP_JTAG",
                    wait_slice_s=20, overall_s=120)
                self.jtag_connected = False
            except Exception as exc:
                errors.append(f"jtag:{exc}")
        if self.session_id:
            try:
                closed = await self.call("close_session", {
                    "session_id": self.session_id}, phase="CLEANUP_SESSION")
                if closed.get("status") != "success":
                    errors.append(f"session:{closed}")
            except Exception as exc:
                errors.append(f"session:{exc}")
        self.summary["cleanup"] = {
            "public_cleanup_errors": errors,
            "capture_stopped": not self.capture_id,
            "jtag_disconnected": not self.jtag_connected,
            "session_close_requested": bool(self.session_id),
        }
        if errors and self.summary.get("status") == "PASS":
            self.summary["status"] = "FAILED_CLEANUP"

    def finish(self, error: BaseException | None = None) -> None:
        self.summary["finished_at"] = _utc()
        if error is not None:
            self.summary["status"] = "FAIL"
            self.summary["error"] = {
                "type": type(error).__name__, "message": str(error)}
        (self.evidence / "summary.json").write_text(
            json.dumps(self.summary, indent=2, ensure_ascii=False,
                       default=_jsonable, sort_keys=True),
            encoding="utf-8", newline="\n")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    return parser.parse_args()


async def _main() -> int:
    args = _arguments()
    replay = Replay(args.workspace.resolve(), args.runtime_root.resolve())
    error: BaseException | None = None
    try:
        await replay.run()
    except BaseException as exc:
        error = exc
    finally:
        if replay.evidence.exists():
            replay.finish(error)
    if error is not None:
        raise error
    if replay.summary.get("status") != "PASS":
        raise RuntimeError(f"replay incomplete: {replay.summary.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
