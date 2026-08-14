"""B05 Platform Domain public MCP SDK tests.
host_live: requires Vivado. Tests discovery, schema, stage rejection, and the
real success path — driven through the B05-R2 platform ATOM sequence, which
replaced the B05 shortcut ``platform_generate`` in B11 phase 2 (see
docs/development/mcp/B11_platform_generate_erratum.md, decision (a):
``platform_export_manifest`` is the terminal atom that advances
PLATFORM_DESIGN → PL_GENERATE).
"""
import asyncio, json, os, sys, hashlib, pytest
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

pytestmark = [pytest.mark.host_live, pytest.mark.asyncio(loop_scope="function")]

BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"

# The 14 B05-R2 platform atoms that replace the removed platform_generate
# shortcut (12 command + 2 query).
PLATFORM_ATOMS = [
    "platform_create_design", "platform_get_status", "platform_add_ps7",
    "platform_configure_ps7", "platform_add_ip", "platform_list_ips",
    "platform_connect_interface", "platform_connect_clock",
    "platform_connect_reset", "platform_set_address", "platform_validate",
    "platform_generate_wrapper", "platform_export_hardware",
    "platform_export_manifest",
]


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
    async def test_shortcut_removed_atoms_registered(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = [t.name for t in tools.tools]
                # B11 phase 2: platform_generate is removed from the public
                # contract; every replacement atom is registered.
                assert "platform_generate" not in names
                for atom in PLATFORM_ATOMS:
                    assert atom in names, atom

    async def test_export_manifest_schema_is_closed_object(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                t = [t for t in tools.tools
                     if t.name == "platform_export_manifest"][0]
                assert t.inputSchema["type"] == "object"
                assert t.inputSchema.get("additionalProperties") == False

    async def test_public_tool_count(self, tmp_runtime_root):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = await s.list_tools()
                names = sorted(t.name for t in tools.tools)
                pl_tools = [n for n in names if n.startswith("platform_") or n.startswith("pl_")]
                # platform_* = 14 B05-R2 atoms (the platform_generate shortcut
                # was removed in B11 phase 2);
                # pl_* = pl_generate_system_top + 26 PL bridge tools = 27.
                assert len(pl_tools) == 41
                assert "platform_generate" not in pl_tools
                assert "pl_generate_system_top" in pl_tools
                assert "platform_create_design" in pl_tools
                assert "platform_export_manifest" in pl_tools


# ═══════════════════════════════════════════════════════════════
#  Wrong-stage / removed-shortcut rejection
# ═══════════════════════════════════════════════════════════════

class TestStageRejection:
    async def test_removed_shortcut_is_unknown_tool(self, tmp_runtime_root):
        """The old shortcut must be gone from the public contract (B11 phase 2):
        calling it fails closed with UNKNOWN_TOOL — never a success."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                data = await _sdk_call(s, "platform_generate", {})
                assert data["status"] == "error"
                assert data["error"]["details"]["reason_code"] == "UNKNOWN_TOOL"

    async def test_export_manifest_rejected_without_session(self, tmp_runtime_root):
        """platform_export_manifest requires an active session (fail-closed)."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                data = await _sdk_call(s, "platform_export_manifest", {})
                assert data["status"] == "error"
                assert data["error"]["details"]["reason_code"] == "NO_ACTIVE_SESSION"

    async def test_extra_property_rejected(self, tmp_runtime_root, tmp_path):
        """Schema has additionalProperties=false — an extra property must be
        rejected (MCP SDK / JSON-RPC schema validation or a handler error);
        it must never be admitted as an accepted operation."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = str(tmp_path)
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                assert d["status"] == "success"
                try:
                    res = await s.call_tool(
                        "platform_export_manifest", {"extra": 1})
                except Exception:
                    # SDK / JSON-RPC schema validation rejected the call
                    # before the handler — fail-closed outcome.
                    return
                assert not res.isError, res
                d2 = json.loads(res.content[0].text)
                assert d2["status"] == "error", d2


# ═══════════════════════════════════════════════════════════════
#  Happy path — real Vivado success via the atom sequence
# ═══════════════════════════════════════════════════════════════

class TestRealVivadoSuccess:
    async def _run_atom_sequence(self, s):
        """Drive the platform atom sequence (replaces platform_generate {}).

        Minimal sequence that reproduces the shortcut's artifact contract
        (BD → wrapper → XSA → Platform Manifest) and reaches the
        platform_export_manifest stage-advance (decision (a)).
        Returns {atom_name: wait_operation view}.
        """
        seq = [
            ("platform_create_design",
             {"name": "platform", "part": "xc7z020clg400-2"}),
            ("platform_add_ps7", {"preset_name": "ps7_preset.tcl"}),
            ("platform_validate", {}),
            ("platform_generate_wrapper", {}),
            ("platform_export_hardware", {}),
            ("platform_export_manifest", {}),
        ]
        results = {}
        for atom, args in seq:
            adm = await _sdk_call(s, atom, args)
            assert adm["status"] == "success", (atom, adm)
            assert adm["data"]["status"] == "accepted", (atom, adm)
            wait = await _sdk_call(s, "wait_operation",
                {"operation_id": adm["data"]["operation_id"], "timeout_s": 600})
            assert wait["data"]["status"] == "SUCCEEDED", (atom, wait)
            results[atom] = wait
        return results

    async def test_full_success_chain_atom_sequence(self, tmp_runtime_root, tmp_path):
        """create_session (PLATFORM_DESIGN) → atom sequence →
        platform_export_manifest (SUCCEEDED → PL_GENERATE) → pl_generate_system_top."""
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

                # 3. Run the atom sequence (the shortcut is gone; each atom is
                #    independently observable and recoverable).
                results = await self._run_atom_sequence(s)

                # 4. Observe PL_GENERATE — platform_export_manifest advanced the
                #    stage (B11 phase 2 decision (a)).
                state2 = await _sdk_call(s, "get_execution_state", {})
                assert state2["data"]["current_stage"] == "PL_GENERATE"
                assert state2["data"]["execution_lane"] == "IDLE"

                # 5. Verify context.platform_revision published
                info = await _sdk_call(s, "get_session_info", {"session_id": sid})
                plat_rev = info.get("data", {}).get("platform_revision", "")
                assert plat_rev and plat_rev.startswith("sha256:")

                # 6. Verify the export_manifest operation result + artifact SHAs
                export_view = results["platform_export_manifest"]["data"]
                result = export_view.get("result", {}).get("data", {})
                assert result.get("manifest_sha256", "").startswith("sha256:")
                assert result.get("platform_revision") == plat_rev

                hw_view = results["platform_export_hardware"]["data"]
                hw = hw_view.get("result", {}).get("data", {})
                xsa_path = hw.get("xsa_path", "")
                assert hw.get("xsa_sha256", "").startswith("sha256:")

                wr_view = results["platform_generate_wrapper"]["data"]
                wr = wr_view.get("result", {}).get("data", {})
                wrapper_path = wr.get("wrapper_path", "")
                assert wr.get("wrapper_sha256", "").startswith("sha256:")
                wrapper_rel = "hdl/" + wr.get("wrapper_name", "")

                manifest_path = result.get("manifest_path", "")

                # 7. Verify files exist
                assert os.path.isfile(xsa_path), f"XSA missing: {xsa_path}"
                assert os.path.isfile(wrapper_path), f"Wrapper missing: {wrapper_path}"
                assert os.path.isfile(manifest_path), f"Manifest missing: {manifest_path}"

                # 8. Verify SHA256
                def sha_file(p):
                    h = hashlib.sha256()
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
                    return "sha256:" + h.hexdigest()

                assert sha_file(xsa_path) == hw["xsa_sha256"]
                assert sha_file(wrapper_path) == wr["wrapper_sha256"]
                assert sha_file(manifest_path) == result["manifest_sha256"]

                # 9. Validate manifest — resolve relative paths against project_path
                with open(manifest_path) as f:
                    import json as j
                    manifest = j.load(f)
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

                # 10. Platform→PL handoff: use wrapper_rel from the wrapper atom
                assert wrapper_rel, "wrapper_rel missing"
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
        """After PL_GENERATE, platform_export_manifest is rejected: the stage
        gate admits it only from PLATFORM_DESIGN (decision (a)), so a
        later-stage call cannot push the frozen stage machine forward."""
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = str(tmp_path)
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                assert d["status"] == "success"
                await self._run_atom_sequence(s)
                # Now at PL_GENERATE — export_manifest again must be rejected
                d2 = await _sdk_call(s, "platform_export_manifest", {})
                assert d2["status"] == "error"
                reason = d2.get("error", {}).get("details", {}).get("reason_code", "")
                assert reason in ("STAGE_PREREQUISITE_UNMET", "CHANNEL_BUSY")
