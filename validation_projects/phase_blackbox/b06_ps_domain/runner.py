"""Agent3 black-box runner for B06 PS Domain. v1.0.0
Uses ONLY stdlib + MCP SDK. No mcps.zynq_mcp or mcps.common imports.
Loads expected assertions from checked-in expected_outputs/*.json.
Hardware-gated scenarios SKIP (with a recorded reason) when their
prerequisites are absent; they FAIL, never silently pass, when they run.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import sys
import tempfile

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"

XSA_PATH = r"D:\fpgaproject\zynq_platforms\xsa\ax7020_base.xsa"
SOURCE_C = r"D:\fpgaproject\embedded_projects\ps_led_test\src\main.c"
UART_MARKER = "AX7020 ARM Test G11"   # marker expected on COM4 after ELF run
UART_PORT = "COM4"
HW_URL = "localhost:3121"
_APP_NAME = "ps_led_test"
_PLATFORM_NAME = "ax7020_platform"

_VITIS_BIN = r"D:/Xilinx/Vitis/2023.1/bin"
# Search order mirrors the server's find_xsdb()/find_xsct() so the probe's
# answer matches what the running bridges will actually resolve.
_TOOL_ENV = {
    "xsct": ("XSCT_EXEC",),
    "xsdb": ("XSDM_EXEC", "XSDB_EXEC"),
    "hw_server": ("HW_SERVER_EXEC",),
}

SHA256_RE = re.compile(r'^sha256:[0-9a-fA-F]{64}$')

VALID_TARGET_STATES = ("halted", "running", "reset", "unknown")


# ─────────────────────────────────────────────────────────────────────────────
#  Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_tool(name: str) -> str | None:
    """Resolve an executable path using the server's search order."""
    for var in _TOOL_ENV.get(name, ()):
        val = os.environ.get(var, "").strip()
        if val:
            for v in (val, val + ".bat", val + ".exe"):
                if os.path.isfile(v):
                    return v
    root = os.environ.get("VITIS_ROOT", "").strip()
    if root:
        for v in (f"{name}.bat", name, f"{name}.exe"):
            p = os.path.join(root, "bin", v)
            if os.path.isfile(p):
                return p
    for v in (f"{name}.bat", name, f"{name}.exe"):
        p = os.path.join(_VITIS_BIN, v)
        if os.path.isfile(p):
            return p
    found = shutil.which(name)
    return found if found else None


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _list_uart_ports() -> list:
    try:
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]
    except Exception:
        return []


def _probe_environment() -> dict:
    """Return the availability of each capability a scenario depends on."""
    return {
        "xsct": _find_tool("xsct"),
        "xsdb": _find_tool("xsdb"),
        "hw_server_bin": _find_tool("hw_server"),
        "hw_server_reachable": _tcp_reachable("localhost", 3121),
        "uart_ports": _list_uart_ports(),
        "xsa": XSA_PATH if os.path.isfile(XSA_PATH) else None,
        "source_c": SOURCE_C if os.path.isfile(SOURCE_C) else None,
    }


def _sha256_file(p):
    if not os.path.isfile(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _server_params(runtime_root):
    env = os.environ.copy()
    env["PYTHONPATH"] = _PROJECT_ROOT
    # Per-run runtime isolation: unique ZYNQ_RUNTIME_ROOT temp dir so the
    # server never re-reads a previous run's dead-worker / RECOVERY_REQUIRED
    # state from the shared .zynq_runtime/.
    env["ZYNQ_RUNTIME_ROOT"] = runtime_root
    return StdioServerParameters(command=sys.executable,
                                 args=["-m", "mcps.zynq_mcp.server"], env=env)


async def _sdk_call(s, n, a=None):
    """Call a tool whose content[0].text is JSON; return the parsed dict."""
    r = await s.call_tool(n, a or {})
    return json.loads(r.content[0].text)


async def _sdk_call_raw(s, n, a=None):
    """Call a tool and return (isError, text, json_or_None)."""
    r = await s.call_tool(n, a or {})
    text = r.content[0].text if r.content else ""
    try:
        return r.isError, text, json.loads(text)
    except Exception:
        return r.isError, text, None


def _collect(id_, actual):
    return {"id": id_, "actual": actual}


def _op_succeeded(op) -> bool:
    return op is not None and op.get("status") == "SUCCEEDED"


def _op_data(op):
    """Return result.data for a SUCCEEDED op record, else None."""
    if not _op_succeeded(op):
        return None
    return (op.get("result") or {}).get("data")


def _op_reason(op) -> str:
    return (op or {}).get("reason_code") or ""


def _find_arm_dap(targets: list) -> dict | None:
    """Find the ARM DAP target in a ps_list_targets listing."""
    for t in targets or []:
        ty = (t.get("type") or "").lower()
        name = (t.get("name") or "").lower()
        if "dap" in ty or "cortex" in ty or "arm" in name:
            return t
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  ps tool calling convention
# ─────────────────────────────────────────────────────────────────────────────

async def _ps_call(s, tool: str, args: dict, timeout_s: int):
    """Call a ps_* tool (session_id in args) and wait for the terminal op.

    Returns (call_env, op_record). call_env is the tool's own envelope
    ({status, data:{operation_id,...}}); op_record is the wait_operation
    result ({status, ...} or None when the wait failed).
    """
    call = await _sdk_call(s, tool, args)
    if call.get("status") != "success":
        return call, None
    oid = call["data"].get("operation_id")
    if not oid:
        return call, None
    wr = await _sdk_call(s, "wait_operation",
                         {"operation_id": oid, "timeout_s": timeout_s})
    if wr.get("status") != "success":
        return call, {"status": "WAIT_FAILED", "wait_error": wr}
    return call, wr["data"]


# ─────────────────────────────────────────────────────────────────────────────
#  Expected-assertion evaluation (extended contract)
# ─────────────────────────────────────────────────────────────────────────────

def _load_expected(ed, name):
    p = os.path.join(ed, f"{name}.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"expected missing: {p}")
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    if "scenario" not in d or "assertions" not in d or \
            not isinstance(d["assertions"], list):
        raise ValueError(f"{name}.json missing scenario/assertions")
    return d


def _run_expected(exp_list, facts):
    fm = {f["id"]: f for f in facts}
    results = []
    consumed = set()
    for ea in exp_list:
        eid = ea["id"]
        ff = fm.get(eid)
        if ff is None:
            results.append({"id": eid, "status": "FAIL", "msg": "not collected",
                            "expected": _expect_repr(ea), "actual": None,
                            "field": ea.get("field", "")})
            continue
        consumed.add(eid)
        actual = ff["actual"]
        results.append(_evaluate(eid, ea, actual))
    passed = all(r["status"] == "PASS" for r in results) and \
        len(consumed) == len(exp_list)
    return results, passed, len(exp_list), len(consumed)


def _expect_repr(ea) -> str:
    for k in ("expect_one_of", "expect_min", "expect_contains",
              "expect_not_contains", "expect_check_sha", "expect_pattern",
              "expect_file_sha", "expect_file_exists", "expect"):
        if k in ea:
            return ea[k]
    return ""


def _evaluate(eid, ea, actual):
    def done(ok, expected, msg):
        return {"id": eid, "status": "PASS" if ok else "FAIL",
                "expected": expected, "actual": actual,
                "field": ea.get("field", ""), "msg": msg}

    if "expect_one_of" in ea:
        vals = ea["expect_one_of"]
        return done(actual in vals, f"one of {vals}",
                    "ok" if actual in vals else f"{actual!r} not in {vals}")
    if "expect_min" in ea:
        lo = ea["expect_min"]
        ok = isinstance(actual, (int, float)) and not isinstance(actual, bool) \
            and actual >= lo
        return done(ok, f">= {lo}", "ok" if ok else f"{actual!r} < {lo}")
    if "expect_contains" in ea:
        val = ea["expect_contains"]
        ok = (val in actual) if isinstance(actual, str) else \
            (isinstance(actual, list) and val in actual)
        return done(ok, f"contains {val}",
                    "ok" if ok else f"{val!r} not present in {actual!r}")
    if "expect_not_contains" in ea:
        val = ea["expect_not_contains"]
        ok = (val not in actual) if isinstance(actual, str) else \
            (isinstance(actual, list) and val not in actual)
        return done(ok, f"not contains {val}",
                    "ok" if ok else f"{val!r} present in {actual!r}")
    if "expect_check_sha" in ea:
        ok = isinstance(actual, str) and SHA256_RE.match(actual)
        return done(ok, ea["expect_check_sha"],
                    "ok" if ok else f"bad SHA: {actual}")
    if "expect_pattern" in ea:
        ok = isinstance(actual, str) and \
            bool(re.match(ea["expect_pattern"], actual))
        return done(ok, f"matches {ea['expect_pattern']}",
                    "ok" if ok else f"no match: {actual}")
    if "expect_file_sha" in ea:
        ok = os.path.isfile(actual) and \
            _sha256_file(actual) == ea.get("expect_file_sha", "")
        return done(ok, ea.get("expect_file_sha", ""),
                    "ok" if ok else "file SHA mismatch or missing")
    if "expect_file_exists" in ea:
        ok = os.path.isfile(actual)
        return done(ok, "file exists",
                    "ok" if ok else "file not found")
    ev = ea.get("expect")
    return done(actual == ev, ev,
                "ok" if actual == ev else f"got {actual!r}")


# ─────────────────────────────────────────────────────────────────────────────
#  Scenario scaffolding
# ─────────────────────────────────────────────────────────────────────────────

def _finish_scenario(name, exp, facts):
    results, passed, exp_cnt, consumed = _run_expected(exp["assertions"], facts)
    return {"scenario": name, "status": "PASS" if passed else "FAIL",
            "passed": passed, "expected_assertion_count": exp_cnt,
            "consumed_assertions": consumed, "assertions": results}


def _skip_result(name, gate, reason):
    return {"scenario": name, "status": "SKIP", "gate": gate,
            "skip_reason": reason, "passed": False,
            "expected_assertion_count": 0, "consumed_assertions": 0,
            "assertions": []}


async def _open_session(session, ws):
    """create_session; returns (sid, facts_prefix) or (None, error_env)."""
    r = await _sdk_call(session, "create_session",
                        {"board_id": BOARD, "project_path": ws})
    if r.get("status") != "success":
        return None, r
    return r["data"]["session_id"], r["data"]


async def _close_session(session, sid):
    try:
        r = await _sdk_call(session, "close_session", {"session_id": sid})
        return r.get("status") == "success"
    except Exception:
        return False


class _SessionLease:
    """Create a session and always close it (best-effort), even on exception.

    ``close_ok`` records the result of an explicit close so the scenario can
    assert it; a leaked session would corrupt a later scenario's preconditions.
    """

    def __init__(self, session, ws):
        self._session = session
        self._ws = ws
        self.sid = None
        self.data = None
        self.close_ok = False
        self._explicitly_closed = False

    async def open(self):
        self.sid, self.data = await _open_session(self._session, self._ws)
        return self

    async def close(self):
        """Explicit close; records result. Safe to call once."""
        if self.sid and not self._explicitly_closed:
            self.close_ok = await _close_session(self._session, self.sid)
            self._explicitly_closed = True
        return self.close_ok

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Scenarios
# ─────────────────────────────────────────────────────────────────────────────

async def run_discovery(session, evidence, exp, ctx, probe):
    facts = []
    tools = await session.list_tools()
    names = [t.name for t in tools.tools]
    ps_names = [n for n in names if n.startswith("ps_")]
    facts.append(_collect("ps_tool_count_min", len(ps_names)))

    def _schema(name):
        for t in tools.tools:
            if t.name == name:
                return t.inputSchema
        return None

    r = await _sdk_call(session, "get_capabilities", {})
    caps = r["data"]
    facts.append(_collect("ps_implemented_min",
                          caps["domains"]["ps"]["implemented"]))
    facts.append(_collect("ps_planned", caps["domains"]["ps"]["planned"]))
    facts.append(_collect("total_tools_min", caps["total_tools"]))

    for name in ("ps_connect_hw_server", "ps_list_targets",
                 "ps_import_hardware", "ps_compile", "ps_select_target"):
        facts.append(_collect(f"{name}_present", name in names))

    s = _schema("ps_connect_hw_server")
    facts.append(_collect("ps_connect_hw_server_schema_type",
                          (s or {}).get("type")))
    facts.append(_collect("ps_connect_hw_server_required",
                          (s or {}).get("required", [])))
    props = (s or {}).get("properties", {})
    facts.append(_collect("ps_connect_hw_server_url_type",
                          (props.get("url") or {}).get("type")))

    s = _schema("ps_list_targets")
    facts.append(_collect("ps_list_targets_schema_empty",
                          (s or {}).get("properties") == {}))

    s = _schema("ps_import_hardware")
    facts.append(_collect("ps_import_hardware_required",
                          (s or {}).get("required", [])))
    s = _schema("ps_compile")
    facts.append(_collect("ps_compile_required",
                          (s or {}).get("required", [])))
    s = _schema("ps_select_target")
    facts.append(_collect("ps_select_target_required",
                          (s or {}).get("required", [])))
    facts.append(_collect("ps_download_elf_present", "ps_download_elf" in names))
    return _finish_scenario("discovery", exp, facts)


async def run_bsp_build(session, evidence, exp, ctx, probe):
    facts = []
    if probe.get("xsct") is None:
        return _skip_result("bsp_build", "xsct",
                            "XSCT executable not found (xsct on PATH or "
                            f"{_VITIS_BIN})")
    ws = os.path.join(ctx.run_tmp, "bsp_workspace")
    os.makedirs(ws, exist_ok=True)

    lease = _SessionLease(session, ws)
    await lease.open()
    facts.append(_collect("session_created", lease.sid is not None))
    if lease.sid is None:
        return _finish_scenario("bsp_build", exp, facts)
    sid, data = lease.sid, lease.data
    try:
        facts.append(_collect("session_id", sid))
        facts.append(_collect("project_path_valid", bool(data.get("project_path"))))
        pp = data.get("project_path") or ws

        r = await _sdk_call(session, "get_execution_state", {})
        facts.append(_collect("initial_stage", r["data"]["current_stage"]))

        # 1. import hardware
        call, op = await _ps_call(session, "ps_import_hardware",
                                  {"session_id": sid, "xsa_path": XSA_PATH,
                                   "project_path": pp}, 60)
        facts.append(_collect("import_succeeded", _op_succeeded(op)))
        d = _op_data(op) or {}
        facts.append(_collect("import_workspace_xsa",
                              d.get("workspace_xsa", "")))

        # 2. create platform
        call, op = await _ps_call(session, "ps_create_platform",
                                  {"session_id": sid, "name": _PLATFORM_NAME,
                                   "project_path": pp}, 180)
        facts.append(_collect("platform_created", _op_succeeded(op)))
        d = _op_data(op) or {}
        facts.append(_collect("platform_cpu", d.get("cpu")))
        facts.append(_collect("platform_os", d.get("os")))

        # 3. create/generate BSP (platform generate; slow)
        call, op = await _ps_call(session, "ps_create_bsp",
                                  {"session_id": sid,
                                   "platform_name": _PLATFORM_NAME,
                                   "project_path": pp}, 300)
        facts.append(_collect("bsp_generated", _op_succeeded(op)))

        # 4. create app
        call, op = await _ps_call(session, "ps_create_app",
                                  {"session_id": sid, "name": _APP_NAME,
                                   "project_path": pp}, 180)
        facts.append(_collect("app_created", _op_succeeded(op)))
        d = _op_data(op) or {}
        facts.append(_collect("app_name", d.get("name")))

        # 5. add sources (real main.c)
        call, op = await _ps_call(session, "ps_add_sources",
                                  {"session_id": sid, "files": [SOURCE_C]}, 60)
        facts.append(_collect("sources_added", _op_succeeded(op)))
        d = _op_data(op) or {}
        facts.append(_collect("sources_app", d.get("app")))

        # 6. compiler options (Vitis 2023.1 XSCT only supports -D defines)
        call, op = await _ps_call(session, "ps_set_compiler_options",
                                  {"session_id": sid,
                                   "opts": {"defines": "B06_TEST=1"}}, 60)
        facts.append(_collect("options_configured", _op_succeeded(op)))

        # 7. compile (app build; slow)
        call, op = await _ps_call(session, "ps_compile",
                                  {"session_id": sid, "app_name": _APP_NAME},
                                  300)
        facts.append(_collect("compile_succeeded", _op_succeeded(op)))

        # 8. build status -> find ELF
        call, op = await _ps_call(session, "ps_get_build_status",
                                  {"session_id": sid}, 60)
        d = _op_data(op) or {}
        apps = d.get("apps", [])
        facts.append(_collect("build_status_count_min", d.get("count", 0)))
        app_entry = next((a for a in apps if a.get("name") == _APP_NAME), None)
        facts.append(_collect("app_built",
                              bool(app_entry and app_entry.get("built"))))
        elf = (app_entry or {}).get("elf", "") or ""
        facts.append(_collect("elf_path_present", bool(elf)))
        facts.append(_collect("elf_exists", os.path.isfile(elf) if elf else False))
        if elf:
            ctx.shared["elf"] = elf

        # 9. ELF header inspection
        if elf and os.path.isfile(elf):
            call, op = await _ps_call(session, "ps_read_elf_info",
                                      {"session_id": sid, "elf_path": elf}, 60)
            d = _op_data(op) or {}
            facts.append(_collect("elf_magic_valid", d.get("magic_valid")))
            facts.append(_collect("elf_class", d.get("elf_class")))
            facts.append(_collect("elf_machine", d.get("machine")))
        else:
            facts.append(_collect("elf_magic_valid", False))
            facts.append(_collect("elf_class", None))
            facts.append(_collect("elf_machine", None))

        facts.append(_collect("close_session_ok", await lease.close()))

        r = await _sdk_call(session, "get_execution_state", {})
        facts.append(_collect("final_lane", r["data"]["execution_lane"]))
    finally:
        await lease.close()
    return _finish_scenario("bsp_build", exp, facts)


async def run_jtag_connect(session, evidence, exp, ctx, probe):
    facts = []
    if not probe.get("hw_server_reachable"):
        return _skip_result("jtag_connect", "hw_server",
                            "hw_server not reachable at tcp:localhost:3121")
    if probe.get("xsdb") is None:
        return _skip_result("jtag_connect", "xsdb",
                            "XSDB executable not found")
    ws = os.path.join(ctx.run_tmp, "jtag_connect_ws")
    os.makedirs(ws, exist_ok=True)

    lease = _SessionLease(session, ws)
    await lease.open()
    facts.append(_collect("session_created", lease.sid is not None))
    if lease.sid is None:
        return _finish_scenario("jtag_connect", exp, facts)
    sid = lease.sid
    try:
        call, op = await _ps_call(session, "ps_connect_hw_server",
                                  {"session_id": sid, "url": HW_URL}, 60)
        facts.append(_collect("connect_succeeded", _op_succeeded(op)))
        facts.append(_collect("connect_status",
                              (_op_data(op) or {}).get("status")))

        call, op = await _ps_call(session, "ps_list_targets",
                                  {"session_id": sid}, 60)
        facts.append(_collect("list_targets_succeeded", _op_succeeded(op)))
        d = _op_data(op) or {}
        targets = d.get("targets", [])
        facts.append(_collect("target_count_min", d.get("count", 0)))
        arm = _find_arm_dap(targets)
        facts.append(_collect("arm_dap_present", arm is not None))

        if arm is not None:
            call, op = await _ps_call(session, "ps_select_target",
                                      {"session_id": sid,
                                       "target_id": arm["id"]}, 60)
            facts.append(_collect("select_succeeded", _op_succeeded(op)))
            sel = (_op_data(op) or {}).get("selected", {})
            facts.append(_collect("selected_id_matches",
                                  sel.get("id") == arm["id"]))
        else:
            facts.append(_collect("select_succeeded", False))
            facts.append(_collect("selected_id_matches", False))

        call, op = await _ps_call(session, "ps_get_target_status",
                                  {"session_id": sid}, 60)
        facts.append(_collect("target_status_succeeded", _op_succeeded(op)))
        facts.append(_collect("target_state_valid",
                              (_op_data(op) or {}).get("state")))

        call, op = await _ps_call(session, "ps_get_device_info",
                                  {"session_id": sid}, 60)
        facts.append(_collect("device_info_succeeded", _op_succeeded(op)))
        facts.append(_collect("device_info_keys_min",
                              len((_op_data(op) or {}).keys())))

        call, op = await _ps_call(session, "ps_disconnect_hw_server",
                                  {"session_id": sid}, 60)
        facts.append(_collect("disconnect_succeeded", _op_succeeded(op)))
        facts.append(_collect("disconnect_status",
                              (_op_data(op) or {}).get("status")))

        facts.append(_collect("close_session_ok", await lease.close()))
    finally:
        await lease.close()
    return _finish_scenario("jtag_connect", exp, facts)


async def run_jtag_deploy(session, evidence, exp, ctx, probe):
    facts = []
    if not probe.get("hw_server_reachable"):
        return _skip_result("jtag_deploy", "hw_server",
                            "hw_server not reachable at tcp:localhost:3121")
    if probe.get("xsdb") is None:
        return _skip_result("jtag_deploy", "xsdb",
                            "XSDB executable not found")
    ws = os.path.join(ctx.run_tmp, "jtag_deploy_ws")
    os.makedirs(ws, exist_ok=True)

    lease = _SessionLease(session, ws)
    await lease.open()
    facts.append(_collect("session_created", lease.sid is not None))
    if lease.sid is None:
        return _finish_scenario("jtag_deploy", exp, facts)
    sid = lease.sid
    try:
        call, op = await _ps_call(session, "ps_connect_hw_server",
                                  {"session_id": sid, "url": HW_URL}, 60)
        facts.append(_collect("connect_succeeded", _op_succeeded(op)))

        call, op = await _ps_call(session, "ps_list_targets",
                                  {"session_id": sid}, 60)
        d = _op_data(op) or {}
        arm = _find_arm_dap(d.get("targets", []))
        if arm is not None:
            call, op = await _ps_call(session, "ps_select_target",
                                      {"session_id": sid,
                                       "target_id": arm["id"]}, 60)
            facts.append(_collect("arm_dap_selected", _op_succeeded(op)))
        else:
            facts.append(_collect("arm_dap_selected", False))

        call, op = await _ps_call(session, "ps_get_device_info",
                                  {"session_id": sid}, 60)
        facts.append(_collect("device_info_succeeded", _op_succeeded(op)))
        facts.append(_collect("device_info_keys_min",
                              len((_op_data(op) or {}).keys())))

        call, op = await _ps_call(session, "ps_get_target_status",
                                  {"session_id": sid}, 60)
        facts.append(_collect("target_status_succeeded", _op_succeeded(op)))
        facts.append(_collect("target_state_valid",
                              (_op_data(op) or {}).get("state")))

        call, op = await _ps_call(session, "ps_initialize_ps",
                                  {"session_id": sid}, 120)
        facts.append(_collect("ps_initialize_succeeded", _op_succeeded(op)))
        facts.append(_collect("ps_initialize_status",
                              (_op_data(op) or {}).get("status")))

        call, op = await _ps_call(session, "ps_halt_target",
                                  {"session_id": sid}, 60)
        facts.append(_collect("halt_succeeded", _op_succeeded(op)))
        facts.append(_collect("halt_state", (_op_data(op) or {}).get("state")))

        call, op = await _ps_call(session, "ps_reg_read",
                                  {"session_id": sid, "register": "pc"}, 60)
        facts.append(_collect("reg_read_pc_succeeded", _op_succeeded(op)))
        facts.append(_collect("pc_value_hex", (_op_data(op) or {}).get("value")))

        call, op = await _ps_call(session, "ps_run_target",
                                  {"session_id": sid}, 60)
        facts.append(_collect("run_succeeded", _op_succeeded(op)))
        facts.append(_collect("run_state", (_op_data(op) or {}).get("state")))

        call, op = await _ps_call(session, "ps_disconnect_hw_server",
                                  {"session_id": sid}, 60)
        facts.append(_collect("disconnect_succeeded", _op_succeeded(op)))

        # ELF download + run + UART marker sub-flow (ps_download_elf registered).
        tools = await session.list_tools()
        names = [t.name for t in tools.tools]
        elf = ctx.shared.get("elf") or ""
        if "ps_download_elf" not in names:
            facts.append(_collect("download_subflow", "DEFERRED"))
        elif not elf:
            facts.append(_collect("download_subflow", "SKIPPED_NO_ELF"))
        else:
            call, op = await _ps_call(session, "ps_download_elf",
                                      {"session_id": sid, "elf_path": elf}, 120)
            if not _op_succeeded(op):
                facts.append(_collect("download_subflow", "DOWNLOAD_FAILED"))
            else:
                call, op = await _ps_call(session, "ps_run_target",
                                          {"session_id": sid}, 60)
                if not _op_succeeded(op):
                    facts.append(_collect("download_subflow", "RUN_FAILED"))
                else:
                    uart = UART_PORT if UART_PORT in probe["uart_ports"] else None
                    if uart is None:
                        facts.append(_collect("download_subflow", "NO_UART_PORT"))
                    else:
                        call, op = await _ps_call(
                            session, "ps_read_uart",
                            {"session_id": sid, "port": uart,
                             "baudrate": 115200, "duration_ms": 3000}, 30)
                        txt = (_op_data(op) or {}).get("text", "") \
                            if _op_succeeded(op) else ""
                        facts.append(_collect(
                            "download_subflow",
                            "EXECUTED_OK" if UART_MARKER in txt
                            else "MARKER_MISSING"))

        facts.append(_collect("close_session_ok", await lease.close()))
    finally:
        await lease.close()
    return _finish_scenario("jtag_deploy", exp, facts)


async def run_error_paths(session, evidence, exp, ctx, probe):
    facts = []
    r0 = await _sdk_call(session, "get_execution_state", {})
    if r0.get("data", {}).get("execution_lane") != "IDLE":
        return _skip_result("error_paths", "lane",
                            f"execution lane not IDLE at start: "
                            f"{r0.get('data', {}).get('execution_lane')}")

    # 1. No active session -> NO_ACTIVE_SESSION
    r = await _sdk_call(session, "ps_connect_hw_server",
                        {"session_id": "session-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"})
    facts.append(_collect("no_session_error", r.get("status") == "error"))
    err = r.get("error", {})
    facts.append(_collect("no_session_code", err.get("code")))
    facts.append(_collect("no_session_reason",
                          (err.get("details") or {}).get("reason_code")))

    ws = os.path.join(ctx.run_tmp, "error_ws")
    os.makedirs(ws, exist_ok=True)
    sid, _data = await _open_session(session, ws)
    facts.append(_collect("session_created", sid is not None))
    if sid is None:
        return _finish_scenario("error_paths", exp, facts)

    # 2. Empty session_id
    r = await _sdk_call(session, "ps_connect_hw_server", {"session_id": ""})
    facts.append(_collect("empty_sid_error", r.get("status") == "error"))
    err = r.get("error", {})
    facts.append(_collect("empty_sid_code", err.get("code")))
    facts.append(_collect("empty_sid_reason",
                          (err.get("details") or {}).get("reason_code")))

    # 3. Non-string session_id
    r = await _sdk_call(session, "ps_connect_hw_server", {"session_id": 12345})
    facts.append(_collect("nonstr_sid_error", r.get("status") == "error"))
    err = r.get("error", {})
    facts.append(_collect("nonstr_sid_reason",
                          (err.get("details") or {}).get("reason_code")))

    # 4. Mismatched session_id
    r = await _sdk_call(session, "ps_connect_hw_server",
                        {"session_id": "session-00000000000000000000000000000000"})
    facts.append(_collect("mismatch_sid_error", r.get("status") == "error"))
    err = r.get("error", {})
    facts.append(_collect("mismatch_sid_reason",
                          (err.get("details") or {}).get("reason_code")))

    # 5. Missing required param -> MCP input-schema rejection
    is_err, text, _j = await _sdk_call_raw(session, "ps_select_target",
                                           {"session_id": sid})
    facts.append(_collect("schema_rejected", is_err))
    facts.append(_collect("schema_rejected_message",
                          ("target_id" in text) and ("required" in text)))

    # 6. Wrong workflow stage -> shared P7 stage gate
    r = await _sdk_call(session, "pl_generate_system_top",
                        {"wrapper_path": "system_top_dummy.v"})
    facts.append(_collect("wrong_stage_error", r.get("status") == "error"))
    err = r.get("error", {})
    facts.append(_collect("wrong_stage_code", err.get("code")))
    facts.append(_collect("wrong_stage_reason",
                          (err.get("details") or {}).get("reason_code")))

    # 7. Invalid XSA path -> operation-level failure (no false success)
    call, op = await _ps_call(session, "ps_import_hardware",
                              {"session_id": sid,
                               "xsa_path": r"D:\nonexistent_abs\missing.xsa",
                               "project_path": ws}, 60)
    facts.append(_collect("invalid_xsa_error",
                          op is not None and op.get("status") == "FAILED"))
    facts.append(_collect("invalid_xsa_reason", _op_reason(op)))

    # 8. Channel stays clean after error paths
    r = await _sdk_call(session, "get_execution_state", {})
    facts.append(_collect("channel_clean", r["data"]["execution_lane"]))

    facts.append(_collect("close_session_ok", await _close_session(session, sid)))
    return _finish_scenario("error_paths", exp, facts)


SCENARIOS = {
    "discovery": run_discovery,
    "bsp_build": run_bsp_build,
    "jtag_connect": run_jtag_connect,
    "jtag_deploy": run_jtag_deploy,
    "error_paths": run_error_paths,
}
ORDER = ["discovery", "bsp_build", "jtag_connect", "jtag_deploy", "error_paths"]


# ─────────────────────────────────────────────────────────────────────────────
#  main
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    p = argparse.ArgumentParser(description="B06 Agent3 Runner v1")
    p.add_argument("--run-id", required=True)
    p.add_argument("--scenario", default="all")
    p.add_argument("--evidence-base", default=None)
    p.add_argument("--expected-dir", default=None)
    p.add_argument("--fail-on-skip", action="store_true",
                   help="treat any SKIP as an overall failure")
    args = p.parse_args()

    run_id = args.run_id
    evidence_base = args.evidence_base or os.path.join(HERE, "evidence", run_id)
    expected_dir = args.expected_dir or os.path.join(HERE, "expected_outputs")
    os.makedirs(evidence_base, exist_ok=True)

    target = ORDER if args.scenario == "all" else [args.scenario]
    results = {}
    skipped = []

    # Probe the environment ONCE and record it as evidence.
    probe = _probe_environment()
    with open(os.path.join(evidence_base, "environment.json"), "w") as f:
        json.dump(probe, f, indent=2, default=str)

    # Shared context across scenarios (bsp_build hands its ELF to jtag_deploy).
    class RunContext:
        pass
    ctx = RunContext()
    ctx.run_tmp = tempfile.mkdtemp(prefix="b06_agent3_")
    ctx.shared = {}

    runtime_root = tempfile.mkdtemp(prefix="b06_runtime_")
    try:
        params = _server_params(runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                for name in target:
                    fn = SCENARIOS.get(name)
                    if fn is None:
                        results[name] = {"status": "FAIL",
                                         "assertions": [],
                                         "skip_reason": f"unknown scenario {name}"}
                        continue
                    try:
                        exp = _load_expected(expected_dir, name)
                    except Exception as e:
                        print(f"[{name}] SKIPPED: {e}")
                        results[name] = {"status": "SKIP", "gate": "contract",
                                         "skip_reason": str(e),
                                         "assertions": []}
                        skipped.append({"name": name, "reason": str(e)})
                        continue
                    ev = os.path.join(evidence_base, name)
                    os.makedirs(ev, exist_ok=True)
                    try:
                        res = await fn(s, ev, exp, ctx, probe)
                    except Exception as e:
                        # A scenario must never take the whole run down without
                        # recorded evidence: capture it as a FAIL and continue.
                        import traceback as _tb
                        _tb.print_exc()
                        res = {"scenario": name, "status": "FAIL",
                               "passed": False, "skip_reason": None,
                               "expected_assertion_count": 0,
                               "consumed_assertions": 0,
                               "assertions": [],
                               "exception": str(e)}
                    results[name] = res
                    if res["status"] == "SKIP":
                        skipped.append({"name": name,
                                        "gate": res.get("gate", ""),
                                        "reason": res.get("skip_reason", "")})
                    with open(os.path.join(evidence_base, f"{name}_result.json"),
                              "w") as f:
                        json.dump(res, f, indent=2)
                    print(f"[{name}] {res['status']} "
                          f"({res['consumed_assertions']}/{res['expected_assertion_count']})"
                          + (f" — {res['skip_reason']}" if res["status"] == "SKIP"
                             else ""))
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)
        shutil.rmtree(ctx.run_tmp, ignore_errors=True)

    passed = sorted(n for n, r in results.items() if r["status"] == "PASS")
    failed = sorted(n for n, r in results.items() if r["status"] == "FAIL")
    ran = sorted(n for n, r in results.items()
                 if r["status"] in ("PASS", "FAIL"))
    any_fail = bool(failed)
    any_skip = bool(skipped)
    overall = not any_fail and (not any_skip or not args.fail_on_skip)

    summary = {
        "run_id": run_id, "runner_version": VERSION,
        "requested": sorted(target), "executed": ran, "skipped": skipped,
        "passed": passed, "failed": failed,
        "overall": overall, "fail_on_skip": args.fail_on_skip,
        "environment": probe,
    }
    with open(os.path.join(evidence_base, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    if not overall:
        print(f"\n[FAIL] passed={passed} failed={failed} "
              f"skipped={[s['name'] for s in skipped]}")
        sys.exit(1)
    print(f"\n[PASS] passed={len(passed)} skipped={len(skipped)} "
          f"({run_id})")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
