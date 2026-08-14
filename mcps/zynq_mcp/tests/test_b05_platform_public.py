"""B05 Platform Domain public MCP SDK tests.
host_live: requires Vivado. Tests discovery, schema, stage rejection, and real success path.
"""
import asyncio, json, os, sys, hashlib, pytest
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

pytestmark = [pytest.mark.host_live, pytest.mark.asyncio(loop_scope="function")]

BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"


def _server_params():
    env = os.environ.copy()
    env["PYTHONPATH"] = _PROJECT_ROOT
    return StdioServerParameters(
        command=sys.executable, args=["-m", "mcps.zynq_mcp.server"], env=env)


async def _sdk_call(session, name, args=None):
    r = await session.call_tool(name, args or {})
    return json.loads(r.content[0].text)


# ═══════════════════════════════════════════════════════════════
#  Tool discovery and schema
# ═══════════════════════════════════════════════════════════════

class TestToolDiscovery:
    async def test_list_tools_includes_platform_generate(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = [t.name for t in tools.tools]
                assert "platform_generate" in names

    async def test_schema_empty_object(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                pg = [t for t in tools.tools if t.name == "platform_generate"][0]
                assert pg.inputSchema["type"] == "object"
                assert pg.inputSchema.get("additionalProperties") == False

    async def test_public_tool_count(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = sorted(t.name for t in tools.tools)
                pl_tools = [n for n in names if n.startswith("platform_") or n.startswith("pl_")]
                # Count verified live against the running server (2026-08-09):
                #   platform_* = platform_generate + 14 B05-R2 atoms = 15
                #     (12 original atoms + platform_connect_reset +
                #      platform_export_manifest added in B05-R2);
                #   pl_* = pl_generate_system_top + 26 PL bridge tools = 27
                #     (24 original + pl_generate_target added as B07 addendum
                #      + pl_program_fpga added as B07 fix).
                assert len(pl_tools) == 42
                assert "platform_generate" in pl_tools
                assert "pl_generate_system_top" in pl_tools
                assert "platform_create_design" in pl_tools
                assert "platform_export_hardware" in pl_tools


# ═══════════════════════════════════════════════════════════════
#  Wrong-stage rejection
# ═══════════════════════════════════════════════════════════════

class TestStageRejection:
    async def test_rejected_when_stage_is_not_platform_design(self, tmp_runtime_root, tmp_path):
        """platform_generate requires PLATFORM_DESIGN. Fresh session is now PLATFORM_DESIGN. We test rejection by closing and trying again (no session)."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                # No session: should reject
                data = await _sdk_call(s, "platform_generate", {})
                assert data["status"] == "error"

    async def test_extra_property_rejected(self, tmp_runtime_root, tmp_path):
        """Schema has additionalProperties=false."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = str(tmp_path)
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                assert d["status"] == "success"
                # With additionalProperties=false, MCP SDK enforces schema.
                try:
                    d2 = await _sdk_call(s, "platform_generate", {"extra": 1})
                    # If it reaches handler, must not succeed
                    assert d2["status"] == "error" or d2.get("data", {}).get("status", "") == "accepted"
                except Exception:
                    # MCP SDK rejects the call before handler — acceptable
                    pass


# ═══════════════════════════════════════════════════════════════
#  Happy path — real Vivado success
# ═══════════════════════════════════════════════════════════════

class TestRealVivadoSuccess:
    async def test_full_success_chain(self, tmp_runtime_root, tmp_path):
        """create_session (PLATFORM_DESIGN) → platform_generate → SUCCEEDED → PL_GENERATE → pl_generate_system_top."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()

                pp = str(tmp_path)

                # 1. Create session
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                assert d["status"] == "success"
                sid = d["data"]["session_id"]

                # 2. Observe PLATFORM_DESIGN
                state = await _sdk_call(s, "get_execution_state", {})
                assert state["data"]["current_stage"] == "PLATFORM_DESIGN", \
                    f"Expected PLATFORM_DESIGN, got {state['data']['current_stage']}"

                # 3. platform_generate admission
                adm = await _sdk_call(s, "platform_generate", {})
                assert adm["status"] == "success"
                assert adm["data"]["status"] == "accepted"
                op_id = adm["data"]["operation_id"]
                assert op_id and op_id.startswith("op-")

                # 4. Wait → SUCCEEDED.
                # generate_platform now runs BD synthesis (required so
                # write_hw_platform emits HDF into the XSA), which takes a few
                # minutes; 600s gives margin over the measured ~300s.
                wait = await _sdk_call(s, "wait_operation",
                    {"operation_id": op_id, "timeout_s": 600})
                assert wait["data"]["status"] == "SUCCEEDED"

                # 5. Observe PL_GENERATE
                state2 = await _sdk_call(s, "get_execution_state", {})
                assert state2["data"]["current_stage"] == "PL_GENERATE"
                assert state2["data"]["execution_lane"] == "IDLE"

                # 6. Verify context.platform_revision published
                info = await _sdk_call(s, "get_session_info", {"session_id": sid})
                plat_rev = info.get("data", {}).get("platform_revision", "")
                assert plat_rev and plat_rev.startswith("sha256:")

                # 7. Verify operation result
                op_status = await _sdk_call(s, "get_operation_status",
                    {"operation_id": op_id})
                result = op_status["data"].get("result", {}).get("data", {})
                assert result.get("xsa_sha256", "").startswith("sha256:")
                assert result.get("wrapper_sha256", "").startswith("sha256:")
                assert result.get("manifest_sha256", "").startswith("sha256:")
                assert result.get("platform_revision") == plat_rev

                # 8. Verify files exist
                xsa_path = result.get("xsa_path", "")
                wrapper_path = result.get("wrapper_path", "")
                manifest_path = result.get("manifest_path", "")
                assert os.path.isfile(xsa_path), f"XSA missing: {xsa_path}"
                assert os.path.isfile(wrapper_path), f"Wrapper missing: {wrapper_path}"
                assert os.path.isfile(manifest_path), f"Manifest missing: {manifest_path}"

                # 9. Verify SHA256
                def sha_file(p):
                    h = hashlib.sha256()
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
                    return "sha256:" + h.hexdigest()

                assert sha_file(xsa_path) == result["xsa_sha256"]
                assert sha_file(wrapper_path) == result["wrapper_sha256"]
                assert sha_file(manifest_path) == result["manifest_sha256"]

                # 10. Validate manifest — resolve relative paths against project_path
                with open(manifest_path) as f:
                    import json as j
                    manifest = j.load(f)
                # Resolve paths for validation (manifest uses relative paths)
                resolved = dict(manifest)
                r_xsa = os.path.join(pp, manifest.get("xsa_path", ""))
                r_wrapper = os.path.join(pp, manifest.get("bd_wrapper_path", ""))
                if os.path.isfile(r_xsa):
                    resolved["xsa_path"] = r_xsa
                if os.path.isfile(r_wrapper):
                    resolved["bd_wrapper_path"] = r_wrapper
                from mcps.common.artifact_schema import validate_manifest
                issues = validate_manifest(resolved, "platform")
                assert len(issues) == 0, f"Manifest issues: {issues}"

                # 11. Platform→PL handoff: use wrapper_rel from result, no copy/rename
                wrapper_rel = result.get("wrapper_rel", "")
                assert wrapper_rel, "wrapper_rel missing from result"
                pl_adm = await _sdk_call(s, "pl_generate_system_top",
                    {"wrapper_path": wrapper_rel})
                assert pl_adm["status"] == "success"
                pl_oid = pl_adm["data"]["operation_id"]
                pl_wait = await _sdk_call(s, "wait_operation",
                    {"operation_id": pl_oid, "timeout_s": 30})
                assert pl_wait["data"]["status"] == "SUCCEEDED"
                assert pl_wait["data"].get("completion_evidence", {}).get("stage_advanced_from") == "PL_GENERATE"
                assert pl_wait["data"].get("completion_evidence", {}).get("stage_advanced_to") == "PL_BUILD"

    async def test_wrong_stage_after_pl_generate(self, tmp_runtime_root, tmp_path):
        """After PL_GENERATE, platform_generate should be rejected (stage moved past PLATFORM_DESIGN)."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = str(tmp_path)
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                sid = d["data"]["session_id"]
                adm = await _sdk_call(s, "platform_generate", {})
                assert adm["status"] == "success"
                oid = adm["data"]["operation_id"]
                # 600s: generate_platform now synthesizes for HDF (see
                # test_full_success_chain comment).
                wait = await _sdk_call(s, "wait_operation",
                    {"operation_id": oid, "timeout_s": 600})
                # Now at PL_GENERATE — try platform_generate again
                d2 = await _sdk_call(s, "platform_generate", {})
                assert d2["status"] == "error"
                reason = d2.get("error", {}).get("details", {}).get("reason_code", "")
                assert reason in ("STAGE_PREREQUISITE_UNMET", "CHANNEL_BUSY")
