"""
test_consistency_check.py — B01 §5 Phase 4 cross-domain manifest consistency.

Covers the 7 frozen checks implemented by verify_consistency:
  1. pl_build.built_from_platform_revision == platform.platform_revision
  2. ps_build.built_from_platform_revision == platform.platform_revision
  3. ps_build.platform_xsa_sha256 == platform.xsa_sha256
  4. ps_build.xparameters_addrs matches platform.address_map (field-by-field)
  5. ps_build.board_profile_sha256 == board_profile.sha256
  6. pl_build.board_profile_sha256 == board_profile.sha256   (direct comparison)
  7. All artifact files exist + SHA256 matches manifest

Also verifies registration as an MCP query tool (schema) and dispatch routing.
"""
import asyncio, hashlib, json, os, shutil, sys, tempfile, uuid
from pathlib import Path
import pytest

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, EXECUTION_LANE_IDLE, WORKER_STATE_ABSENT,
)
from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id
from mcps.zynq_mcp.control.capabilities import ALL_TOOLS
from mcps.zynq_mcp.dispatcher import ZynqDispatcher
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.domains.verification.consistency_check import (
    verify_consistency,
    RULE_PL_REVISION, RULE_PS_REVISION, RULE_PS_XSA, RULE_PS_ADDRESS_MAP,
    RULE_PS_BOARD_PROFILE, RULE_PL_BOARD_PROFILE, RULE_ARTIFACTS,
)

SH_PKG = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
SH_BP = "sha256:3c95da56a6a9264ef42b6902f184d7d01c7229eafa70d1061cfd24cc0af0c90a"
BOARD = "ALINX_AX7020_v1.0"
REAL_WSID = compute_workspace_id(resolve_workspace_root())

# ── Project/artifact builders ────────────────────────────────────────────

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return "sha256:" + h.hexdigest()


def _write(root, rel, content=b"artifact"):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return str(p)


def _write_manifest(root, mtype, data):
    d = Path(root) / "manifests" / mtype
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{mtype}.json"
    p.write_text(json.dumps(data, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _build_project(tmp_path, **overrides):
    """Build a project with three manifests + real artifact files.

    Relative artifact paths are used (as production manifests do), so callers
    must pass resolve_root=<root>.
    """
    root = str(tmp_path / "proj")
    bp_sha = overrides.get("bp_sha") or "sha256:" + "11" * 32
    rev = overrides.get("rev") or "sha256:" + "22" * 32

    xsa = _write(root, "platform.xsa", b"xsa")
    bdw = _write(root, "hdl/platform_bd_wrapper.v", b"wrapper")
    bit = _write(root, "build/system_top.bit", b"bit")
    xdc = _write(root, "constraints/top.xdc", b"xdc")
    elf = _write(root, "app/main.elf", b"elf")
    xp = _write(root, "bsp/xparameters.h", b"xparams")

    platform = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": bp_sha,
        "platform_revision": rev, "manifest_revision": rev,
        "revision_inputs": {},
        "xsa_path": "platform.xsa", "xsa_sha256": _sha(xsa),
        "bd_wrapper_path": "hdl/platform_bd_wrapper.v", "bd_wrapper_sha256": _sha(bdw),
        "address_map": overrides.get("platform_addr")
            if overrides.get("platform_addr") is not None
            else {"axi_gpio_led": {"base": "0x41200000", "range": "64K"}},
        "clock_tree": {},
        "generated_at": "2026-08-09T00:00:00Z", "status": "locked",
    }
    pl = {
        "schema_version": "1.0", "manifest_type": "pl_build",
        "board_profile_sha256": overrides.get("pl_bp", bp_sha),
        "built_from_platform_revision": overrides.get("pl_rev", rev),
        "manifest_revision": rev, "revision_inputs": {},
        "bitstream_path": "build/system_top.bit", "bitstream_sha256": _sha(bit),
        "bd_wrapper_sha256": _sha(bdw),
        "xdc_path": "constraints/top.xdc", "xdc_sha256": _sha(xdc),
        "timing_met": True, "wns_ns": 0.1, "tns_ns": 0.0,
        "generated_at": "2026-08-09T00:00:00Z", "status": "locked",
    }
    ps = {
        "schema_version": "1.0", "manifest_type": "ps_build",
        "board_profile_sha256": overrides.get("ps_bp", bp_sha),
        "built_from_platform_revision": overrides.get("ps_rev", rev),
        "manifest_revision": rev, "revision_inputs": {},
        "platform_xsa_sha256": overrides.get("ps_xsa", _sha(xsa)),
        "elf_path": "app/main.elf", "elf_sha256": _sha(elf),
        "xparameters_h_path": "bsp/xparameters.h", "xparameters_h_sha256": _sha(xp),
        "xparameters_addrs": overrides.get("ps_addrs")
            if overrides.get("ps_addrs") is not None
            else {"XPAR_AXI_GPIO_LED_BASEADDR": "0x41200000"},
        "source_files_sha256": "sha256:" + "33" * 32,
        "generated_at": "2026-08-09T00:00:00Z", "status": "locked",
    }

    return {
        "root": root, "bp_sha": bp_sha, "rev": rev,
        "paths": {
            "platform": _write_manifest(root, "platform", platform),
            "pl_build": _write_manifest(root, "pl_build", pl),
            "ps_build": _write_manifest(root, "ps_build", ps),
        },
    }


# ── Consistency checks (pure function) ───────────────────────────────────

class TestConsistencyChecks:

    @pytest.mark.asyncio
    async def test_all_pass(self, tmp_path):
        proj = _build_project(tmp_path)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        assert result["status"] == "success"
        data = result["data"]
        assert data["all_passed"] is True
        assert data["errors"] == []
        assert data["warnings"] == []
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PL_REVISION]["passed"] is True
        assert checks[RULE_PS_REVISION]["passed"] is True
        assert checks[RULE_PS_XSA]["passed"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["passed"] is True
        assert checks[RULE_PS_BOARD_PROFILE]["passed"] is True
        assert checks[RULE_PL_BOARD_PROFILE]["passed"] is True
        artifacts = [c for c in data["checks"] if c["rule"] == RULE_ARTIFACTS]
        assert len(artifacts) == 6          # 2 file pairs x 3 manifests
        assert all(c["passed"] is True for c in artifacts)
        assert data["summary"]["total"] == 12
        assert data["summary"]["failed"] == 0
        assert data["summary"]["skipped"] == 0

    @pytest.mark.asyncio
    async def test_pl_revision_mismatch_fails_only_that_rule(self, tmp_path):
        wrong = "sha256:" + "ab" * 32
        proj = _build_project(tmp_path, pl_rev=wrong)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        assert data["all_passed"] is False
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PL_REVISION]["passed"] is False
        assert checks[RULE_PL_REVISION]["actual"] == wrong
        assert checks[RULE_PL_REVISION]["expected"] == proj["rev"]
        # every other rule still passes
        assert checks[RULE_PS_REVISION]["passed"] is True
        assert checks[RULE_PS_XSA]["passed"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["passed"] is True
        assert checks[RULE_PS_BOARD_PROFILE]["passed"] is True
        assert checks[RULE_PL_BOARD_PROFILE]["passed"] is True
        assert data["summary"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_ps_revision_mismatch_fails_only_that_rule(self, tmp_path):
        wrong = "sha256:" + "cd" * 32
        proj = _build_project(tmp_path, ps_rev=wrong)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_REVISION]["passed"] is False
        assert checks[RULE_PS_REVISION]["actual"] == wrong
        assert data["summary"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_ps_xsa_sha_mismatch(self, tmp_path):
        wrong = "sha256:" + "ef" * 32
        proj = _build_project(tmp_path, ps_xsa=wrong)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_XSA]["passed"] is False
        assert checks[RULE_PS_XSA]["actual"] == wrong
        assert checks[RULE_PS_XSA]["expected"] == _sha(Path(proj["root"], "platform.xsa"))
        assert data["summary"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_missing_pl_manifest_skips_dependent_rules(self, tmp_path):
        proj = _build_project(tmp_path)
        missing = str(tmp_path / "nope" / "pl_build.json")
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=missing,
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        assert result["status"] == "success"        # no crash on missing manifest
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PL_REVISION]["skipped"] is True
        assert checks[RULE_PL_BOARD_PROFILE]["skipped"] is True
        pl_artifacts = [c for c in data["checks"] if c["rule"] == RULE_ARTIFACTS
                        and c.get("field", "").startswith("pl_build")]
        assert len(pl_artifacts) == 2
        assert all(c["skipped"] is True for c in pl_artifacts)
        # platform + ps rules still verified
        assert checks[RULE_PS_REVISION]["passed"] is True
        assert checks[RULE_PS_XSA]["passed"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["passed"] is True
        assert checks[RULE_PS_BOARD_PROFILE]["passed"] is True
        assert checks[RULE_PL_REVISION]["passed"] is None
        assert any("pl_build manifest NOT FOUND" in e for e in data["errors"])
        # fail-closed: skipped rules mean the run is NOT all-passed
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_missing_ps_manifest_skips_dependent_rules(self, tmp_path):
        proj = _build_project(tmp_path)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=None,
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_REVISION]["skipped"] is True
        assert checks[RULE_PS_XSA]["skipped"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["skipped"] is True
        assert checks[RULE_PS_BOARD_PROFILE]["skipped"] is True
        assert checks[RULE_PL_REVISION]["passed"] is True
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_board_profile_mismatch_fails_both_direct_rules(self, tmp_path):
        proj = _build_project(tmp_path)
        wrong_bp = "sha256:" + "44" * 32
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=wrong_bp, resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_BOARD_PROFILE]["passed"] is False
        assert checks[RULE_PL_BOARD_PROFILE]["passed"] is False
        assert checks[RULE_PS_BOARD_PROFILE]["expected"] == wrong_bp
        assert checks[RULE_PS_BOARD_PROFILE]["actual"] == proj["bp_sha"]
        assert data["summary"]["failed"] == 2
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_address_map_mismatch(self, tmp_path):
        proj = _build_project(tmp_path,
            ps_addrs={"XPAR_AXI_GPIO_LED_BASEADDR": "0x41201000"})
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_ADDRESS_MAP]["passed"] is False
        assert "XPAR_AXI_GPIO_LED_BASEADDR" in checks[RULE_PS_ADDRESS_MAP]["message"]
        assert "0x41201000" in checks[RULE_PS_ADDRESS_MAP]["message"]
        assert data["summary"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_address_map_missing_key(self, tmp_path):
        # PS manifest has no entry for a platform address_map key
        proj = _build_project(tmp_path, ps_addrs={})
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_ADDRESS_MAP]["passed"] is False
        assert "XPAR_AXI_GPIO_LED_BASEADDR" in checks[RULE_PS_ADDRESS_MAP]["message"]

    @pytest.mark.asyncio
    async def test_board_profile_not_provided_skips(self, tmp_path):
        proj = _build_project(tmp_path)
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=None, resolve_root=proj["root"])
        data = result["data"]
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_BOARD_PROFILE]["skipped"] is True
        assert checks[RULE_PL_BOARD_PROFILE]["skipped"] is True
        assert any("board_profile_sha256 not provided" in w for w in data["warnings"])
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_artifact_sha_mismatch(self, tmp_path):
        proj = _build_project(tmp_path)
        # tamper the bitstream so pl_build.bitstream_sha256 no longer matches
        Path(proj["root"], "build", "system_top.bit").write_bytes(b"tampered")
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        bit = [c for c in data["checks"] if c["rule"] == RULE_ARTIFACTS
               and c.get("field") == "pl_build:bitstream_path"]
        assert len(bit) == 1
        assert bit[0]["passed"] is False
        assert bit[0]["actual"] != bit[0]["expected"]
        assert data["summary"]["failed"] == 1
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_artifact_file_missing(self, tmp_path):
        proj = _build_project(tmp_path)
        os.remove(Path(proj["root"], "app", "main.elf"))
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        elf = [c for c in data["checks"] if c["rule"] == RULE_ARTIFACTS
               and c.get("field") == "ps_build:elf_path"]
        assert len(elf) == 1
        assert elf[0]["passed"] is False
        assert elf[0]["actual"] == "MISSING"
        assert data["summary"]["failed"] == 1

    @pytest.mark.asyncio
    async def test_empty_platform_path_errors(self, tmp_path):
        proj = _build_project(tmp_path)
        result = await verify_consistency(
            platform_manifest_path="",
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        assert data["errors"]                          # non-empty
        assert any("platform" in e for e in data["errors"])
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PL_REVISION]["skipped"] is True
        assert checks[RULE_PS_REVISION]["skipped"] is True
        assert checks[RULE_PS_XSA]["skipped"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["skipped"] is True
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_missing_required_platform_path_errors(self, tmp_path):
        proj = _build_project(tmp_path)
        result = await verify_consistency(
            platform_manifest_path=None,
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        assert data["errors"]
        assert any("platform_manifest_path is required" in e for e in data["errors"])

    @pytest.mark.asyncio
    async def test_corrupt_json_errors(self, tmp_path):
        proj = _build_project(tmp_path)
        Path(proj["paths"]["ps_build"]).write_text("{not json", encoding="utf-8")
        result = await verify_consistency(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        data = result["data"]
        assert data["errors"]
        assert any("ps_build manifest" in e for e in data["errors"])
        checks = {c["rule"]: c for c in data["checks"]}
        assert checks[RULE_PS_REVISION]["skipped"] is True
        assert checks[RULE_PS_XSA]["skipped"] is True
        assert checks[RULE_PS_ADDRESS_MAP]["skipped"] is True
        assert checks[RULE_PS_BOARD_PROFILE]["skipped"] is True
        assert data["all_passed"] is False

    @pytest.mark.asyncio
    async def test_idempotent(self, tmp_path):
        proj = _build_project(tmp_path)
        args = dict(
            platform_manifest_path=proj["paths"]["platform"],
            pl_build_manifest_path=proj["paths"]["pl_build"],
            ps_build_manifest_path=proj["paths"]["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        r1 = await verify_consistency(**args)
        r2 = await verify_consistency(**args)
        assert r1 == r2
        assert r1["data"]["all_passed"] is True

    # ── D11: relative manifest paths ──────────────────────────────────────
    # B11 phase 3 found the Skill template calling verify_consistency with
    # project-relative manifest paths: _load_manifest resolved them against
    # the process CWD, every rule was silently skipped (12/12 skipped +
    # NOT FOUND errors). Fix: resolve relative manifest paths against
    # resolve_root, and reject relative paths without resolve_root as an
    # explicit INVALID_ARGUMENT instead of a silent skip.

    @pytest.mark.asyncio
    async def test_relative_manifest_paths_with_resolve_root_pass(self, tmp_path):
        """D11: relative manifest paths + resolve_root → resolved, all
        checks run (no skipped rules)."""
        proj = _build_project(tmp_path)
        rel = {k: os.path.relpath(v, proj["root"]) for k, v in proj["paths"].items()}
        result = await verify_consistency(
            platform_manifest_path=rel["platform"],
            pl_build_manifest_path=rel["pl_build"],
            ps_build_manifest_path=rel["ps_build"],
            board_profile_sha256=proj["bp_sha"], resolve_root=proj["root"])
        assert result["status"] == "success"
        data = result["data"]
        assert data["all_passed"] is True
        assert data["errors"] == []
        assert data["summary"]["skipped"] == 0
        assert data["summary"]["total"] == 12

    @pytest.mark.asyncio
    async def test_relative_manifest_paths_without_resolve_root_error(
            self, tmp_path):
        """D11: relative manifest paths without resolve_root → explicit
        INVALID_ARGUMENT errors (never a silent all-skipped run)."""
        proj = _build_project(tmp_path)
        rel = {k: os.path.relpath(v, proj["root"]) for k, v in proj["paths"].items()}
        result = await verify_consistency(
            platform_manifest_path=rel["platform"],
            pl_build_manifest_path=rel["pl_build"],
            ps_build_manifest_path=rel["ps_build"],
            board_profile_sha256=proj["bp_sha"])
        data = result["data"]
        assert data["errors"]
        for label in ("platform", "pl_build", "ps_build"):
            assert any(label in e and "INVALID_ARGUMENT" in e
                       for e in data["errors"]), (label, data["errors"])
        # every rule is skipped → fail-closed, never all_passed
        assert data["summary"]["skipped"] == data["summary"]["total"]
        assert data["all_passed"] is False


# ── Query tool registration + dispatch ───────────────────────────────────

class TestQueryToolRegistration:

    def test_schema_registered(self):
        tools = {t.name: t for t in ALL_TOOLS}
        assert "verify_consistency" in tools
        t = tools["verify_consistency"]
        props = t.inputSchema.get("properties", {})
        assert set(props) >= {"platform_manifest_path", "pl_build_manifest_path",
                              "ps_build_manifest_path", "board_profile_sha256"}
        assert t.inputSchema["required"] == ["platform_manifest_path"]
        # every schema property is a string
        for p, spec in props.items():
            assert spec.get("type") == "string"


@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, REAL_WSID); g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _prep_ledger(rt, g, project_path=""):
    lp = rt / "execution_ledger.json"
    def _i(l):
        l.instance_id = g.instance_id
        l.workspace_id = REAL_WSID
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        l.worker["state"] = WORKER_STATE_ABSENT
        l.worker["pid"] = None
        l.context = {
            "session_id": f"session-{uuid.uuid4().hex[:8]}",
            "board_id": BOARD, "project_path": project_path,
            "board_package_revision": SH_PKG,
            "expected_board_revision": SH_PKG,
            "board_profile_sha256": SH_BP,
            "current_stage": "PL_BUILD",
            "platform_revision": "",
            "pl_revision": None, "ps_revision": None,
        }
        return l
    ledger_transaction(g, lp, _i)
    return lp


class TestQueryToolDispatch:

    @pytest.mark.asyncio
    async def test_dispatcher_routes_query(self, rtg, tmp_path):
        proj = _build_project(tmp_path)
        rt, g = rtg
        lp = _prep_ledger(rt, g, project_path=proj["root"])
        dispatcher = ZynqDispatcher(None, OperationRegistry(), g, lp, None)
        result = await dispatcher.dispatch("verify_consistency", {
            "platform_manifest_path": proj["paths"]["platform"],
            "pl_build_manifest_path": proj["paths"]["pl_build"],
            "ps_build_manifest_path": proj["paths"]["ps_build"],
            "board_profile_sha256": proj["bp_sha"],
            "resolve_root": proj["root"],
        }, True)
        parsed = json.loads(result[0].text)
        assert parsed["status"] == "success"
        assert parsed["data"]["all_passed"] is True
        assert len(parsed["data"]["checks"]) == 12

    def test_sdk_query_tool(self, rtg, tmp_path):
        proj = _build_project(tmp_path)
        rt, g = rtg
        _prep_ledger(rt, g, project_path=proj["root"])
        g.release_owner_lock()
        old = os.environ.get("ZYNQ_RUNTIME_ROOT")
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        try:
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "mcps.zynq_mcp.server"], env=os.environ)
            async def _run():
                async with stdio_client(params) as (r, w):
                    async with ClientSession(r, w) as s:
                        await s.initialize()
                        res = await s.call_tool("verify_consistency", {
                            "platform_manifest_path": proj["paths"]["platform"],
                            "pl_build_manifest_path": proj["paths"]["pl_build"],
                            "ps_build_manifest_path": proj["paths"]["ps_build"],
                            "board_profile_sha256": proj["bp_sha"],
                            "resolve_root": proj["root"],
                        })
                        assert not res.isError
                        d = json.loads(res.content[0].text)
                        assert d["status"] == "success"
                        assert d["data"]["all_passed"] is True
                        assert d["data"]["summary"]["total"] == 12
                        return True
            assert asyncio.run(_run())
        finally:
            if old: os.environ["ZYNQ_RUNTIME_ROOT"] = old
            else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
