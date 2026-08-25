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

# The 17 B05-R2 + B11 ③.1 platform atoms that replace the removed
# platform_generate shortcut (15 command + 2 query).
PLATFORM_ATOMS = [
    "platform_create_design", "platform_get_status", "platform_add_ps7",
    "platform_configure_ps7", "platform_add_ip", "platform_list_ips",
    "platform_connect_interface", "platform_connect_clock",
    "platform_connect_reset", "platform_set_address",
    "platform_assign_addresses", "platform_make_external",
    "platform_validate", "platform_generate_wrapper", "platform_synthesize",
    "platform_export_hardware", "platform_export_manifest",
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
                # platform_* = 17 atoms (14 B05-R2 + B11 ③.1
                # assign_addresses/make_external/synthesize; the
                # platform_generate shortcut was removed in B11 phase 2);
                # pl_* = pl_generate_system_top + 27 PL bridge tools = 28
                # (B12 fix round #2 added pl_reset_run).
                assert len(pl_tools) == 45
                assert "platform_generate" not in pl_tools
                assert "pl_generate_system_top" in pl_tools
                assert "platform_create_design" in pl_tools
                assert "platform_export_manifest" in pl_tools
                assert "platform_assign_addresses" in pl_tools
                assert "platform_make_external" in pl_tools
                assert "platform_synthesize" in pl_tools


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
        it must never be admitted as an accepted operation.

        B11 ③.1 test fix: with mcp 1.28.1 the SDK surfaces schema rejection as
        an isError CallToolResult (Input validation error) rather than
        raising — both forms are the fail-closed outcome and are accepted.
        """
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
                if res.isError:
                    # SDK schema validation returned a rejection (fail-closed).
                    return
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

        B11 ③.1 (D7): platform_validate now runs `validate_bd_design -force`,
        which surfaces the real design state (the previously cached "already
        validated" could mask an incomplete design). The PS7-only design is
        genuinely invalid until FCLK_CLK0 is connected to M_AXI_GP0_ACLK
        (BD 41-758), so the clock connection is part of the sequence —
        mirroring the removed shortcut's proven Tcl.
        """
        seq = [
            ("platform_create_design",
             {"name": "platform", "part": "xc7z020clg400-2"}),
            ("platform_add_ps7", {"preset_name": "ps7_preset.tcl"}),
            ("platform_connect_clock",
             {"source": "processing_system7_0/FCLK_CLK0",
              "targets": ["processing_system7_0/M_AXI_GP0_ACLK"]}),
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


class TestB11RemediationAtoms:
    """B11 ③.1 host_live: the three added atoms (assign_addresses /
    make_external / synthesize) fix D1/D2/D3 end-to-end on real Vivado.

    Full BD chain with AXI GPIO + PS7 + SmartConnect + reset, then:
      assign_addresses → non-empty address_map;
      make_external    → the wrapper HDL contains the new port;
      synthesize       → the exported XSA contains HDF (hwdef/hwh/ps7_init);
    plus D5 (short segment name resolution), D7 (validate -force), D0 (EMIO
    GPIO config keys) and D8/D9 (manifest ip_list/address_map/clock_tree are
    populated with real data).
    """

    async def _run(self, s, atom, args=None, wait=600):
        adm = await _sdk_call(s, atom, args or {})
        assert adm["status"] == "success", (atom, adm)
        assert adm["data"]["status"] == "accepted", (atom, adm)
        wait_r = await _sdk_call(s, "wait_operation",
            {"operation_id": adm["data"]["operation_id"], "timeout_s": wait})
        assert wait_r["data"]["status"] == "SUCCEEDED", (atom, wait_r)
        return wait_r["data"]

    async def test_atom_sequence_assign_external_synthesize_hdf(
            self, tmp_runtime_root, tmp_path):
        params = _server_params()
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = str(tmp_path)
                d = await _sdk_call(s, "create_session",
                    {"board_id": BOARD, "project_path": pp})
                assert d["status"] == "success"

                await self._run(s, "platform_create_design",
                    {"name": "platform", "part": "xc7z020clg400-2"}, wait=300)
                await self._run(s, "platform_add_ps7",
                    {"preset_name": "ps7_preset.tcl"}, wait=180)
                # D0: EMIO GPIO keys accepted end-to-end
                await self._run(s, "platform_configure_ps7",
                    {"config": {"uart1": {"enable": True, "io": "MIO 48..49"},
                                "gpio": {"emio_enable": True, "width": 64,
                                         "io": "MIO 0..63"}}}, wait=60)
                await self._run(s, "platform_add_ip",
                    {"vlnv": "xilinx.com:ip:axi_gpio:2.0",
                     "instance_name": "axi_gpio_led",
                     "properties": {"C_GPIO_WIDTH": 4, "C_ALL_OUTPUTS": 1}},
                    wait=60)
                await self._run(s, "platform_add_ip",
                    {"vlnv": "xilinx.com:ip:proc_sys_reset:5.0",
                     "instance_name": "rst_ps7_50M"}, wait=60)
                await self._run(s, "platform_add_ip",
                    {"vlnv": "xilinx.com:ip:smartconnect:1.0",
                     "instance_name": "smartconnect_0",
                     "properties": {"NUM_SI": 1}}, wait=60)
                await self._run(s, "platform_connect_interface",
                    {"source": "processing_system7_0/M_AXI_GP0",
                     "destination": "smartconnect_0/S00_AXI"}, wait=60)
                await self._run(s, "platform_connect_interface",
                    {"source": "smartconnect_0/M00_AXI",
                     "destination": "axi_gpio_led/S_AXI"}, wait=60)
                await self._run(s, "platform_connect_clock",
                    {"source": "processing_system7_0/FCLK_CLK0",
                     "targets": ["processing_system7_0/M_AXI_GP0_ACLK",
                                 "smartconnect_0/aclk",
                                 "axi_gpio_led/s_axi_aclk",
                                 "rst_ps7_50M/slowest_sync_clk"]}, wait=60)
                await self._run(s, "platform_connect_reset",
                    {"source": "processing_system7_0/FCLK_RESET0_N",
                     "targets": ["rst_ps7_50M/ext_reset_in"]}, wait=60)
                await self._run(s, "platform_connect_reset",
                    {"source": "rst_ps7_50M/peripheral_aresetn",
                     "targets": ["axi_gpio_led/s_axi_aresetn"]}, wait=60)
                await self._run(s, "platform_connect_reset",
                    {"source": "rst_ps7_50M/interconnect_aresetn",
                     "targets": ["smartconnect_0/aresetn"]}, wait=60)

                # D1: assign addresses → non-empty per-master address_map
                assign = await self._run(s, "platform_assign_addresses", {}, wait=120)
                amap = assign["result"]["data"]["address_map"]
                assert amap, f"assign_addresses returned empty map: {amap}"
                assert "axi_gpio_led" in amap, amap.keys()
                assert amap["axi_gpio_led"]["base"].startswith("0x")

                # D5: short segment "<ip>/S_AXI" resolves without error
                # (set_property on a BD segment is read-only no-op in 2023.1 —
                # the resolution itself must not fail)
                await self._run(s, "platform_set_address",
                    {"segment": "axi_gpio_led/S_AXI", "base": "0x41200000"}, wait=60)

                # D2: externalize the GPIO output → top-level port
                ext = await self._run(s, "platform_make_external",
                    {"port_name": "gpio_led_pins",
                     "source_pin": "axi_gpio_led/gpio_io_o",
                     "direction": "out", "width": 4}, wait=60)
                assert ext["result"]["data"]["port_name"] == "gpio_led_pins"

                # D7: validate -force passes on the now-complete design
                await self._run(s, "platform_validate", {}, wait=180)

                # wrapper must contain the externalized port
                wr = await self._run(s, "platform_generate_wrapper", {}, wait=180)
                wrapper_path = wr["result"]["data"]["wrapper_path"]
                assert os.path.isfile(wrapper_path)
                with open(wrapper_path, encoding="utf-8") as f:
                    wrapper_text = f.read()
                assert "gpio_led_pins" in wrapper_text, \
                    "wrapper HDL missing the externalized port"

                # D3: synthesize → then export → XSA must contain HDF
                # (jobs=1: serial OOC runs — parallel OOC on this Vivado
                # install exceeds the license's concurrent-feature capacity)
                synth = await self._run(s, "platform_synthesize", {"jobs": 1},
                                        wait=900)
                assert "complete" in synth["result"]["data"]["status"].lower()
                hw = await self._run(s, "platform_export_hardware", {}, wait=180)
                xsa_path = hw["result"]["data"]["xsa_path"]
                assert os.path.isfile(xsa_path)
                xsa_size = os.path.getsize(xsa_path)
                assert xsa_size > 1500, \
                    f"XSA looks like the pre_synth shell: {xsa_size} bytes"
                import zipfile
                with zipfile.ZipFile(xsa_path) as zf:
                    entries = zf.namelist()
                    hdf_entries = [e for e in entries
                                   if e.lower().endswith((".hwh", ".hdf"))
                                   or "hwdef" in e.lower()
                                   or "ps7_init" in e.lower()]
                    assert hdf_entries, \
                        f"XSA has no HDF entries: {entries}"

                # D8/D9: manifest carries real ip_list / address_map and
                # full-path clock_tree
                man = await self._run(s, "platform_export_manifest", {}, wait=60)
                mdata = man["result"]["data"]
                assert "axi_gpio_led" in mdata["ip_list"], mdata["ip_list"]
                assert "axi_gpio_led" in mdata["address_map"], mdata["address_map"]
                clk = mdata["clock_tree"].get("FCLK_CLK0", [])
                assert any("/" in pin for pin in clk), clk
