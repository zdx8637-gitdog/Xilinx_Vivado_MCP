"""B06 PS Domain second batch — BSP/Build MCP SDK contract tests.

Covers the B06 second-batch integration (11 ps_* BSP/Build tools: import
hardware, platform/BSP/app create, source/compiler config, compile, and
BSP/build/ELF status queries) through the REAL MCP SDK — a
`python -m mcps.zynq_mcp.server` subprocess reached via stdio_client +
ClientSession (same pattern as test_b06_ps_public.py).

Three groups:

  - TestBspDiscovery (no marker)  — the 11 ps_* BSP tools exist in
    list_tools with exact inputSchema, and capabilities counts reach 33.
    These exercise the contract/discovery path only (no XSCT needed).
  - TestBspErrorPaths (no marker) — a missing required argument is
    rejected (SDK validation or a well-formed server error envelope).
  - TestBspRealXsct (host_live)   — real XSCT flow against the B05 XSA:
    import hardware, create a platform, create an app and compile to an
    ELF. SKIP with a precise reason when the xsct executable or the XSA
    prerequisite is absent; FAIL on any real XSCT command error.

Pre-integration note: until the B06 second-batch agent registers the 11
BSP tools in control/capabilities.py + dispatcher.py, list_tools exposes
none of them. Tests that REQUIRE the tools to exist therefore SKIP with an
explicit reason instead of failing.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcps.zynq_mcp.adapters.xsct.xsct_bridge import find_xsct

pytestmark = pytest.mark.asyncio(loop_scope="function")

BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"
_XSA = os.path.join(_PROJECT_ROOT, "zynq_platforms", "xsa", "ax7020_base.xsa")

# B06 second batch — exactly 11 BSP/Build tools.
EXPECTED_BSP_TOOLS = {
    "ps_import_hardware", "ps_create_platform", "ps_create_bsp",
    "ps_update_hardware", "ps_get_bsp_status", "ps_create_app",
    "ps_add_sources", "ps_set_compiler_options", "ps_compile",
    "ps_get_build_status", "ps_read_elf_info",
}
assert len(EXPECTED_BSP_TOOLS) == 11, "B06 second batch must define 11 tools"

# Skip reasons for host_live tests: environment / prerequisite absent. A real
# XSCT command error (IMPORT_HW_FAILED, PLATFORM_CREATE_FAILED, BUILD_FAILED,
# ...) FAILS the test — only the xsct/XSA prerequisites are skippable.
_BSP_SKIP_REASONS = {"BRIDGE_NOT_READY", "XSA_NOT_FOUND", "INVALID_XSA_PATH"}


def _install_o4_platform_provenance(project_path):
    """Provision a valid locked Platform Manifest for the O4 PS build gate.

    This is white-box host-test setup only.  The product path still receives
    all build/deploy actions through public MCP calls.
    """
    from mcps.common.artifact_schema import publish_manifest, _revision_to_filename
    from mcps.common.board_profile import board_profile_load
    from mcps.common.revision import compute_revision, sha256_file

    root = Path(project_path)
    xsa = root / "platform.xsa"
    shutil.copy2(_XSA, xsa)
    wrapper = root / "hdl" / "platform_bd_wrapper.v"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("module platform_bd_wrapper(); endmodule\n",
                       encoding="utf-8")
    profile = board_profile_load(BOARD)
    revision_inputs = {
        "board_profile_sha256": profile["sha256"],
        "tool_versions": {"vivado": "2023.1", "vitis": "2023.1"},
        "source_files": [], "config_files": [],
    }
    revision = compute_revision(revision_inputs)
    manifest = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": profile["sha256"],
        "platform_revision": revision, "manifest_revision": revision,
        "revision_inputs": revision_inputs,
        "xsa_path": "platform.xsa", "xsa_sha256": sha256_file(xsa),
        "bd_wrapper_path": "hdl/platform_bd_wrapper.v",
        "bd_wrapper_sha256": sha256_file(wrapper),
        "address_map": {}, "clock_tree": {},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "locked",
    }
    path = root / "manifests" / "platform" / _revision_to_filename(revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    publish_manifest(json.dumps(manifest, sort_keys=True), str(path),
                     resolve_root=str(root))
    return str(path)


def _server_params(runtime_root):
    """StdioServerParameters for the zynq MCP server subprocess."""
    env = os.environ.copy()
    env["PYTHONPATH"] = _PROJECT_ROOT
    env["ZYNQ_RUNTIME_ROOT"] = str(runtime_root)
    return StdioServerParameters(
        command=sys.executable, args=["-m", "mcps.zynq_mcp.server"], env=env)


async def _sdk_call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return json.loads(r.content[0].text)


async def _list_tools(session):
    res = await session.list_tools()
    return list(res.tools)


async def _tool_by_name(session, name):
    tools = await _list_tools(session)
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not found in list_tools")


async def _create_session(session, project_path):
    d = await _sdk_call(session, "create_session",
                        {"board_id": BOARD, "project_path": str(project_path)})
    assert d["status"] == "success", f"create_session failed: {d}"
    return d["data"]["session_id"]


async def _ps_call(session, tool, session_id, extra=None):
    """Call a ps_* tool with session_id merged into the arguments.

    B06 dispatcher contract: ps_* tools take session_id as a transport
    argument (stripped before the domain function is invoked). BSP tools
    additionally take their real arguments (xsa_path/project_path/...).
    """
    args = {"session_id": session_id}
    if extra:
        args.update(extra)
    return await _sdk_call(session, tool, args)


async def _await_op(session, op_id, timeout_s=120):
    """Wait for a domain command to reach a terminal state.

    Returns {"status", "reason", "payload", "data"} — same shape as
    test_b06_ps_public.py._await_op.
    """
    wait = await _sdk_call(session, "wait_operation",
                           {"operation_id": op_id, "timeout_s": timeout_s})
    assert wait["status"] == "success", f"wait_operation failed: {wait}"
    data = wait["data"]
    status = data.get("status", "")
    reason = data.get("reason_code", "") or ""
    payload = {}
    if status == "SUCCEEDED":
        result = data.get("result")
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            payload = result["data"]
    return {"status": status, "reason": reason, "payload": payload, "data": data}


def _skip_unless_bsp_integrated(tools):
    """Skip the strict contract check when the B06 second batch is absent."""
    if "ps_compile" not in [t.name for t in tools]:
        pytest.skip(
            "B06 second-batch BSP/Build integration not yet present — no "
            "ps_* BSP tools registered (integration agent pending). Strict "
            "contract is verified after the 11 BSP tools land in "
            "control/capabilities.py + dispatcher.py.")


def _require_xsct():
    if find_xsct() is None:
        pytest.skip("xsct executable not found (find_xsct() returned None)")


def _require_xsa():
    if not os.path.isfile(_XSA):
        pytest.skip(f"XSA prerequisite absent: {_XSA} does not exist")


def _expect_ok(out, label):
    """Assert a host_live step succeeded; skip on env prereq, else fail."""
    if out["status"] == "SUCCEEDED":
        return out["payload"]
    if out["status"] == "FAILED" and out["reason"] in _BSP_SKIP_REASONS:
        pytest.skip(f"{label}: environment prerequisite absent "
                    f"({out['reason']})")
    pytest.fail(f"{label} unexpected outcome: {out}")


# ═══════════════════════════════════════════════════════════════════════
#  Discovery + schema (no marker — contract path only, no XSCT)
# ═══════════════════════════════════════════════════════════════════════

class TestBspDiscovery:

    async def test_ps_import_hardware_schema(self, tmp_runtime_root):
        """ps_import_hardware schema: xsa_path + project_path required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                tool = await _tool_by_name(s, "ps_import_hardware")
                schema = tool.inputSchema
                assert schema["type"] == "object"
                props = schema.get("properties", {})
                for k in ("xsa_path", "project_path"):
                    assert k in props, f"ps_import_hardware missing property {k}"
                req = schema.get("required", [])
                assert "xsa_path" in req, "xsa_path must be required"
                assert "project_path" in req, "project_path must be required"

    async def test_ps_create_app_schema(self, tmp_runtime_root):
        """ps_create_app schema: name + project_path required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                tool = await _tool_by_name(s, "ps_create_app")
                schema = tool.inputSchema
                props = schema.get("properties", {})
                for k in ("name", "project_path"):
                    assert k in props, f"ps_create_app missing property {k}"
                req = schema.get("required", [])
                assert "name" in req, "name must be required"
                assert "project_path" in req, "project_path must be required"

    async def test_ps_compile_schema(self, tmp_runtime_root):
        """ps_compile schema: app_name required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                tool = await _tool_by_name(s, "ps_compile")
                schema = tool.inputSchema
                assert "app_name" in schema.get("properties", {})
                assert "app_name" in schema.get("required", []), \
                    "app_name must be required"

    async def test_ps_add_sources_schema(self, tmp_runtime_root):
        """ps_add_sources schema (C2): app_name + files both required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                tool = await _tool_by_name(s, "ps_add_sources")
                props = tool.inputSchema.get("properties", {})
                assert "app_name" in props, \
                    "ps_add_sources missing app_name property"
                assert "files" in props, \
                    "ps_add_sources missing files property"
                req = tool.inputSchema.get("required", [])
                assert "app_name" in req, "app_name must be required"
                assert "files" in req, "files must be required"

    async def test_ps_create_bsp_schema(self, tmp_runtime_root):
        """ps_create_bsp schema: platform_name + project_path required."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                tool = await _tool_by_name(s, "ps_create_bsp")
                props = tool.inputSchema.get("properties", {})
                for k in ("platform_name", "project_path"):
                    assert k in props, f"ps_create_bsp missing property {k}"
                req = tool.inputSchema.get("required", [])
                assert "platform_name" in req, "platform_name must be required"
                assert "project_path" in req, "project_path must be required"

    async def test_bsp_tools_present(self, tmp_runtime_root):
        """All 11 BSP tools are registered in list_tools (count >= 11)."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                ps_tools = {t.name for t in tools if t.name.startswith("ps_")}
                missing = EXPECTED_BSP_TOOLS - ps_tools
                assert not missing, \
                    f"BSP tools missing from list_tools: {sorted(missing)}"
                assert len(ps_tools) >= 33, f"ps_* tool count = {len(ps_tools)}"

    async def test_capabilities_ps_count(self, tmp_runtime_root):
        """get_capabilities reports the PS domain at >= 33 implemented."""
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                caps = (await _sdk_call(s, "get_capabilities", {}))["data"]
                ps = caps["domains"]["ps"]
                assert ps["implemented"] >= 33, \
                    f"ps.implemented={ps['implemented']}"


# ═══════════════════════════════════════════════════════════════════════
#  Error paths (no marker — verified without XSCT)
# ═══════════════════════════════════════════════════════════════════════

class TestBspErrorPaths:

    async def test_ps_add_sources_invalid(self, tmp_runtime_root):
        """Missing required files must be rejected (never succeed).

        Post-integration the MCP SDK validates the required `files` property
        before dispatch and returns an "Input validation error" result;
        pre-integration the server returns a well-formed error envelope for
        the unregistered tool. Either way the call must not succeed.
        """
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                # Valid app_name, missing required `files` (C2 contract).
                res = await s.call_tool("ps_add_sources", {"app_name": "myapp"})
                text = res.content[0].text if res.content else ""
                if text.startswith("Input validation error"):
                    assert "files" in text, \
                        f"expected files validation, got: {text}"
                else:
                    data = json.loads(text)
                    assert data["status"] == "error", \
                        f"missing required files must error, got: {data}"
                    assert data.get("error", {}).get("details", {}).get(
                        "reason_code"), \
                        "error envelope must carry details.reason_code"


# ═══════════════════════════════════════════════════════════════════════
#  Real XSCT (host_live — xsct on PATH + real B05 XSA)
# ═══════════════════════════════════════════════════════════════════════

class TestBspRealXsct:

    @pytest.mark.host_live
    async def test_ps_import_hardware_real(self, tmp_runtime_root, tmp_path):
        """Real XSCT: import the B05 XSA into a fresh workspace."""
        _require_xsct()
        _require_xsa()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                sid = await _create_session(s, tmp_path)
                adm = await _ps_call(
                    s, "ps_import_hardware", sid,
                    {"xsa_path": _XSA, "project_path": str(tmp_path)})
                assert adm["status"] == "success", f"admission failed: {adm}"
                assert adm["data"]["status"] == "accepted"
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_import_hardware")
                assert payload.get("imported") is True, out

    @pytest.mark.host_live
    async def test_ps_create_platform_real(self, tmp_runtime_root, tmp_path):
        """Real XSCT: import hardware then create a platform."""
        _require_xsct()
        _require_xsa()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                sid = await _create_session(s, tmp_path)
                pp = str(tmp_path)
                _install_o4_platform_provenance(pp)
                platform_name = "ax7020_platform"

                adm = await _ps_call(
                    s, "ps_import_hardware", sid,
                    {"xsa_path": _XSA, "project_path": pp})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_import_hardware")

                adm = await _ps_call(
                    s, "ps_create_platform", sid,
                    {"name": platform_name, "project_path": pp})
                assert adm["status"] == "success", f"admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_create_platform")
                assert payload.get("name") == platform_name, out
                assert payload.get("created") is True, out

    @pytest.mark.host_live
    async def test_ps_create_app_compile_real(self, tmp_runtime_root, tmp_path):
        """Real XSCT full flow: import → platform → BSP → app → build ELF.

        Verifies the pipeline end-to-end: the compiled ELF must exist in the
        workspace tree after ps_compile succeeds.
        """
        _require_xsct()
        _require_xsa()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                sid = await _create_session(s, tmp_path)
                pp = str(tmp_path)
                _install_o4_platform_provenance(pp)
                platform_name = "ax7020_platform"
                app_name = "ax7020_app"

                adm = await _ps_call(
                    s, "ps_import_hardware", sid,
                    {"xsa_path": _XSA, "project_path": pp})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_import_hardware")

                adm = await _ps_call(
                    s, "ps_create_platform", sid,
                    {"name": platform_name, "project_path": pp})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_create_platform")

                adm = await _ps_call(
                    s, "ps_create_bsp", sid,
                    {"platform_name": platform_name, "project_path": pp})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_create_bsp")

                adm = await _ps_call(
                    s, "ps_create_app", sid,
                    {"name": app_name, "project_path": pp})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_create_app")

                # create_app deliberately creates a template-less project;
                # provide an actual translation unit through the public MCP
                # API before claiming that a real application can compile.
                staging = tmp_path / "compile_staging"
                staging.mkdir()
                main = staging / "main.c"
                main.write_text("int main(void){ return 0; }\n",
                                encoding="utf-8")
                adm = await _ps_call(
                    s, "ps_add_sources", sid,
                    {"app_name": app_name, "files": [str(main)]})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_add_sources")

                adm = await _ps_call(s, "ps_compile", sid,
                                     {"app_name": app_name})
                real_observation = None
                for _ in range(100):
                    status = await _sdk_call(
                        s, "get_operation_status",
                        {"operation_id": adm["data"]["operation_id"]})
                    data = status.get("data", {})
                    if data.get("backend") == "XSCT" and \
                            data.get("worker_pid") and \
                            data.get("current_step") in {
                                "APP_BUILD", "MAKE_FALLBACK", "ELF_VERIFY"}:
                        real_observation = data
                        break
                    await asyncio.sleep(0.05)
                assert real_observation is not None, \
                    "ps_compile never exposed a real XSCT process observation"
                assert real_observation["status_source"] == "PROCESS"
                assert real_observation["progress_pct"] is None
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_compile")
                assert payload.get("built") is True, out
                assert out["data"]["artifact_state"] == "PUBLISHED"
                assert out["data"]["current_step"] == "MANIFEST_PUBLISH"
                evidence = out["data"]["completion_evidence"]
                assert evidence["elf_class"] == "ELFCLASS32"
                assert evidence["elf_machine"] == 40
                assert os.path.isfile(evidence["manifest_path"])
                state = await _sdk_call(s, "get_execution_state", {})
                assert state["data"]["worker_state"] == "ABSENT"
                assert state["data"]["worker_pid"] is None

                elfs = glob.glob(os.path.join(pp, "**", "*.elf"),
                                 recursive=True)
                assert elfs, \
                    f"ps_compile succeeded but no ELF produced under {pp}"

    @pytest.mark.host_live
    async def test_ps_compile_defines_reach_compiler_real(
            self, tmp_runtime_root, tmp_path):
        """D10 regression (real XSCT): defines set via ps_set_compiler_options
        must reach the compiler through ps_compile — the built ELF must
        contain the #ifdef-gated probe string of the ENABLED branch and NOT
        the disabled branch's string (proves the macro actually toggled
        compilation instead of being dropped at the build-Tcl boundary).
        """
        _require_xsct()
        _require_xsa()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                sid = await _create_session(s, tmp_path)
                pp = str(tmp_path)
                _install_o4_platform_provenance(pp)
                platform_name = "ax7020_platform"
                app_name = "ax7020_app"

                for tool, extra in (
                    ("ps_import_hardware",
                     {"xsa_path": _XSA, "project_path": pp}),
                    ("ps_create_platform",
                     {"name": platform_name, "project_path": pp}),
                    ("ps_create_bsp",
                     {"platform_name": platform_name, "project_path": pp}),
                    ("ps_create_app",
                     {"name": app_name, "project_path": pp}),
                ):
                    adm = await _ps_call(s, tool, sid, extra)
                    assert adm["status"] == "success", \
                        f"{tool} admission failed: {adm}"
                    out = await _await_op(s, adm["data"]["operation_id"],
                                          timeout_s=300)
                    _expect_ok(out, tool)

                staging = tmp_path / "compile_staging"
                staging.mkdir()
                main = staging / "main.c"
                main.write_text(
                    "#ifdef B11_PROBE_DEFINE\n"
                    "const char *B11_PROBE = \"B11_DEFINE_ACTIVE\";\n"
                    "#else\n"
                    "const char *B11_PROBE = \"B11_DEFINE_INACTIVE\";\n"
                    "#endif\n"
                    "int main(void){ return (B11_PROBE[0] == 'B') ? 0 : 1; }\n",
                    encoding="utf-8")
                adm = await _ps_call(
                    s, "ps_add_sources", sid,
                    {"app_name": app_name, "files": [str(main)]})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_add_sources")

                # D10: the defines must be forwarded into `app build -defines`.
                adm = await _ps_call(
                    s, "ps_set_compiler_options", sid,
                    {"opts": {"defines": "B11_PROBE_DEFINE"}})
                assert adm["status"] == "success", \
                    f"ps_set_compiler_options admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                _expect_ok(out, "ps_set_compiler_options")

                adm = await _ps_call(s, "ps_compile", sid,
                                     {"app_name": app_name})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_compile")
                assert payload.get("built") is True, out
                elf_path = payload.get("elf")
                assert elf_path, f"ps_compile payload missing elf: {payload}"
                elf_bytes = Path(elf_path).read_bytes()
                assert b"B11_DEFINE_ACTIVE" in elf_bytes, \
                    "D10: defines never reached the compiler — the #ifdef " \
                    "enabled branch string is absent from the ELF"
                assert b"B11_DEFINE_INACTIVE" not in elf_bytes, \
                    "D10: defines ignored — the #else branch string is " \
                    "present in the ELF (macro did not toggle compilation)"

    @pytest.mark.host_live
    async def test_ps_add_sources_real(self, tmp_runtime_root, tmp_path):
        """Real XSCT C2 regression: ps_add_sources must place the source in
        {ws}/{app}/src/main.c — never {ws}/src/main.c (B09) — and the app
        must compile it into an ELF.
        """
        _require_xsct()
        _require_xsa()
        params = _server_params(tmp_runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await _list_tools(s)
                _skip_unless_bsp_integrated(tools)
                sid = await _create_session(s, tmp_path)
                pp = str(tmp_path)
                _install_o4_platform_provenance(pp)
                platform_name = "ax7020_platform"
                app_name = "ax7020_app"

                for tool, extra in (
                    ("ps_import_hardware",
                     {"xsa_path": _XSA, "project_path": pp}),
                    ("ps_create_platform",
                     {"name": platform_name, "project_path": pp}),
                    ("ps_create_bsp",
                     {"platform_name": platform_name, "project_path": pp}),
                    ("ps_create_app",
                     {"name": app_name, "project_path": pp}),
                ):
                    adm = await _ps_call(s, tool, sid, extra)
                    assert adm["status"] == "success", \
                        f"{tool} admission failed: {adm}"
                    out = await _await_op(s, adm["data"]["operation_id"],
                                          timeout_s=300)
                    _expect_ok(out, tool)

                # Stage the source OUTSIDE the app dir (like B09's staging
                # location) and add it through the MCP tool.
                staging = tmp_path / "staging"
                staging.mkdir()
                main = staging / "main.c"
                main.write_text("int main(void){ return 0; }\n",
                                encoding="utf-8")

                adm = await _ps_call(
                    s, "ps_add_sources", sid,
                    {"app_name": app_name, "files": [str(main)]})
                assert adm["status"] == "success", \
                    f"ps_add_sources admission failed: {adm}"
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_add_sources")

                # C2 placement: the file must be under the app's src dir.
                app_src_main = os.path.join(pp, app_name, "src", "main.c")
                assert os.path.isfile(app_src_main), \
                    f"expected {app_src_main} to exist, payload={payload}"
                # The B09 wrong location must NOT exist.
                assert not os.path.isfile(
                    os.path.join(pp, "src", "main.c")), \
                    "source must not land in {workspace}/src/main.c"
                assert payload.get("app") == app_name, payload

                # The app must compile the added source to an ELF.
                adm = await _ps_call(s, "ps_compile", sid,
                                     {"app_name": app_name})
                out = await _await_op(s, adm["data"]["operation_id"],
                                      timeout_s=300)
                payload = _expect_ok(out, "ps_compile")
                assert payload.get("built") is True, out
                elfs = glob.glob(
                    os.path.join(pp, app_name, "**", "*.elf"), recursive=True)
                assert elfs, f"no ELF produced for {app_name} under {pp}"
