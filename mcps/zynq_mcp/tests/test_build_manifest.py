"""test_build_manifest.py — PL/PS Build Manifest generation (B01 §6.5/§6.6).

P4: verify_consistency had 12 checks but only 2 ran — P1 platform_generate
published a Platform Manifest, but P2 (pl_generate_bitstream) and P3
(ps_compile) published no PL/PS Build Manifest, so 10 checks were skipped.

These tests prove:
  - publish_pl_build_manifest / publish_ps_build_manifest write schema-valid
    manifests (validate_manifest == []) and persist them to
    manifests/pl|ps/<rev>.json.
  - They skip (return None) when provenance is missing from the snapshot or
    the tool result — never a fabricated manifest.
  - Existing manifests are not overwritten (immutable, atomic no-replace).
  - The CommandRunner hook publishes a manifest on pl_generate_bitstream /
    ps_compile SUCCEEDED (production entry path).
  - With the generated manifests, verify_consistency runs all 12 checks
    (0 skipped, 0 failed) — the P4 gap is closed.

No EDA tools / hardware required.
"""
import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from mcps.common.tool_response import success
from mcps.common.artifact_schema import validate_manifest
from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, OP_SUCCEEDED, WORKER_STATE_ABSENT,
)
from mcps.zynq_mcp.control.operation_registry import OperationRegistry
from mcps.zynq_mcp.control.domain_runner import (
    CommandRunner, DomainExecutionMutex,
)
from mcps.zynq_mcp.domains.verification.build_manifest import (
    publish_pl_build_manifest, publish_ps_build_manifest,
)
from mcps.zynq_mcp.domains.verification.consistency_check import verify_consistency

SH_PKG = "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7"
BOARD = "ALINX_AX7020_v1.0"


# ── project builders ────────────────────────────────────────────────────────

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


def _snapshot(root, *, rev=None, bp=None, stage="PL_BUILD"):
    return {
        "session_id": f"session-{uuid.uuid4().hex[:8]}",
        "board_id": BOARD,
        "project_path": root,
        "current_stage": stage,
        "platform_revision": rev or ("sha256:" + "22" * 32),
        "board_profile_sha256": bp or ("sha256:" + "11" * 32),
        "board_package_revision": SH_PKG,
    }


def _write_platform_manifest(root, *, bp_sha, rev, xsa_sha, bdw_sha, addr_map):
    d = Path(root) / "manifests" / "platform"
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": bp_sha,
        "platform_revision": rev, "manifest_revision": rev,
        "revision_inputs": {"board_profile_sha256": bp_sha,
                            "tool_versions": {"vivado": "2023.1"},
                            "source_files": [], "config_files": []},
        "xsa_path": "platform.xsa", "xsa_sha256": xsa_sha,
        "bd_wrapper_path": "hdl/platform_bd_wrapper.v", "bd_wrapper_sha256": bdw_sha,
        "address_map": addr_map, "clock_tree": {},
        "generated_at": "2026-08-09T00:00:00Z", "status": "locked",
    }
    p = d / f"sha256_{rev[7:]}.json"
    p.write_text(json.dumps(manifest, sort_keys=True, ensure_ascii=False),
                 encoding="utf-8")
    return str(p)


def _pl_project(tmp_path):
    """Project with Platform Manifest + the real files a PL manifest needs."""
    root = str(tmp_path / "proj")
    rev = "sha256:" + "22" * 32
    bp = "sha256:" + "11" * 32
    xsa = _write(root, "platform.xsa", b"xsa")
    bdw = _write(root, "hdl/platform_bd_wrapper.v", b"wrapper")
    bit = _write(root, "build/system_top.bit", b"bit")
    xdc = _write(root, "constraints/top.xdc", b"xdc")
    _write(root, "rtl/top.v", b"module top; endmodule")
    _write_platform_manifest(
        root, bp_sha=bp, rev=rev, xsa_sha=_sha(xsa), bdw_sha=_sha(bdw),
        addr_map={"axi_gpio_led": {"base": "0x41200000", "range": "64K"}})
    return root, _snapshot(root, rev=rev, bp=bp), {"bit": bit, "xdc": xdc}


def _ps_project(tmp_path):
    """_pl_project + the real files a PS manifest needs (app, ELF, BSP)."""
    root, snapshot, files = _pl_project(tmp_path)
    _write(root, "app/src/main.c", b"int main(void){return 0;}")
    _write(root, "app/src/app.h", b"#define APP_X 1")
    elf_header = bytearray(52)
    elf_header[:4] = b"\x7fELF"
    elf_header[4] = 1  # ELFCLASS32
    elf_header[5] = 1  # little-endian
    elf_header[16:18] = (2).to_bytes(2, "little")
    elf_header[18:20] = (40).to_bytes(2, "little")  # EM_ARM
    elf = _write(root, "app/Debug/app.elf", bytes(elf_header))
    xp = _write(root, "app_platform/ps7_cortexa9_0/standalone_domain/"
                     "bsp/xparameters.h", b"#define XPAR_AXI_GPIO_LED_BASEADDR")
    files["elf"] = elf
    files["xp"] = xp
    return root, snapshot, files


# ── pure-function generation ────────────────────────────────────────────────

class TestPublishManifest:

    def test_pl_generate_success(self, tmp_path):
        root, snapshot, files = _pl_project(tmp_path)
        result = {"status": "success",
                  "data": {"timing_met": True, "wns_ns": 0.12, "tns_ns": 0.0}}
        path = publish_pl_build_manifest(snapshot, result, root,
                                         tool_args={"path": files["bit"]})
        assert path is not None
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["manifest_type"] == "pl_build"
        assert m["schema_version"] == "1.0"
        assert m["board_profile_sha256"] == snapshot["board_profile_sha256"]
        assert m["built_from_platform_revision"] == snapshot["platform_revision"]
        assert m["bitstream_path"].replace("\\", "/") == "build/system_top.bit"
        assert m["bitstream_sha256"] == _sha(files["bit"])
        assert m["timing_met"] is True
        assert m["wns_ns"] == 0.12
        assert m["status"] == "locked"
        assert validate_manifest(m, "pl_build", resolve_root=root) == []

    def test_ps_generate_success(self, tmp_path):
        root, snapshot, files = _ps_project(tmp_path)
        result = {"status": "success", "data": {"app_name": "app", "built": True}}
        path = publish_ps_build_manifest(snapshot, result, root,
                                         tool_args={"app_name": "app"})
        assert path is not None
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["manifest_type"] == "ps_build"
        assert m["board_profile_sha256"] == snapshot["board_profile_sha256"]
        assert m["built_from_platform_revision"] == snapshot["platform_revision"]
        assert m["elf_path"].replace("\\", "/") == "app/Debug/app.elf"
        assert m["elf_sha256"] == _sha(files["elf"])
        assert m["xparameters_addrs"] == {
            "XPAR_AXI_GPIO_LED_BASEADDR": "0x41200000"}
        assert m["source_files_sha256"].startswith("sha256:")
        assert m["status"] == "locked"
        assert validate_manifest(m, "ps_build", resolve_root=root) == []

    def test_manifest_dir_created_when_missing(self, tmp_path):
        root, snapshot, files = _pl_project(tmp_path)
        pl_dir = os.path.join(root, "manifests", "pl")
        assert not os.path.exists(pl_dir)
        result = {"status": "success",
                  "data": {"timing_met": True, "wns_ns": 0.0, "tns_ns": 0.0}}
        path = publish_pl_build_manifest(snapshot, result, root,
                                         tool_args={"path": files["bit"]})
        assert path is not None
        assert os.path.isdir(pl_dir)
        assert os.path.isfile(path)

    def test_pl_revision_changes_when_ip_product_changes(self, tmp_path):
        # B13-F8 修复轮#8 (黑盒实证): 打包 IP 的内容不进 PL 输入摘要——
        # 改引擎 RTL 重打包后重建，摘要不变 → 同名 revision 语义冲突。
        # IP 产品文件 (.xci / ipshared) 变化必须改变 PL manifest revision。
        root, snapshot, files = _pl_project(tmp_path)
        ip_shared = _write(root, "bd/design.gen/sources_1/bd/design/"
                                "ipshared/a1f7/engine.v", b"engine v1")
        _write(root, "bd/design.gen/sources_1/bd/design/ip/engine_0/"
                     "engine_0.xci", b"xci v1")
        result = {"status": "success",
                  "data": {"timing_met": True, "wns_ns": 0.0, "tns_ns": 0.0}}
        p1 = publish_pl_build_manifest(snapshot, result, root,
                                       tool_args={"path": files["bit"]})
        assert p1 is not None
        # IP 内容变化（engine v1 -> v2），其余全部不变
        with open(ip_shared, "wb") as fh:
            fh.write(b"engine v2")
        p2 = publish_pl_build_manifest(snapshot, result, root,
                                       tool_args={"path": files["bit"]})
        assert p2 is not None and p2 != p1
        with open(p2, encoding="utf-8") as fh:
            m2 = json.load(fh)
        ip_products = m2["revision_inputs"]["ip_products"]
        assert any(e["path"].replace("\\", "/").endswith(
            "ipshared/a1f7/engine.v") for e in ip_products)
        assert validate_manifest(m2, "pl_build", resolve_root=root) == []

    def test_pl_revision_changes_when_ip_metadata_changes(self, tmp_path):
        # B13-F9 修复轮#9: ip_repo 根下 component.xml/xgui（打包元数据）
        # 也必须进 PL 摘要——只改元数据/接口声明而摘要不变会漏掉重打包
        # 后的语义变化（黑盒实证的"两份拷贝"问题的另一半）。
        root, snapshot, files = _pl_project(tmp_path)
        comp = _write(root, "ip_repo/user.org/user/engine/1.0/component.xml",
                      b"<component v1/>")
        _write(root, "ip_repo/user.org/user/engine/1.0/xgui/engine_v1_0.tcl",
               b"# xgui v1")
        _write(root, "ip_repo/.pkg_log/vivado.log", b"throwaway")  # 必须排除
        result = {"status": "success",
                  "data": {"timing_met": True, "wns_ns": 0.0, "tns_ns": 0.0}}
        p1 = publish_pl_build_manifest(snapshot, result, root,
                                       tool_args={"path": files["bit"]})
        assert p1 is not None
        with open(comp, "wb") as fh:
            fh.write(b"<component v2/>")
        p2 = publish_pl_build_manifest(snapshot, result, root,
                                       tool_args={"path": files["bit"]})
        assert p2 is not None and p2 != p1
        with open(p2, encoding="utf-8") as fh:
            m2 = json.load(fh)
        ip_products = m2["revision_inputs"]["ip_products"]
        paths = [e["path"].replace("\\", "/") for e in ip_products]
        assert any(p.endswith("ip_repo/user.org/user/engine/1.0/component.xml")
                   for p in paths)
        assert any("xgui/engine_v1_0.tcl" in p for p in paths)
        assert not any(".pkg_log" in p for p in paths)  # 一次性打包目录排除
        assert validate_manifest(m2, "pl_build", resolve_root=root) == []

    def test_ps_revision_changes_when_cproject_changes(self, tmp_path):
        # B13-F9 修复轮#9: .cproject 携带 -D 编译宏，改宏不换摘要 = 摘要失真。
        root, snapshot, files = _ps_project(tmp_path)
        cproj = _write(root, "app/.cproject", b"<cproject v1/>")
        result = {"status": "success", "data": {"app_name": "app", "built": True}}
        p1 = publish_ps_build_manifest(snapshot, result, root,
                                       tool_args={"app_name": "app"})
        assert p1 is not None
        with open(cproj, "wb") as fh:
            fh.write(b"<cproject v2/>")
        p2 = publish_ps_build_manifest(snapshot, result, root,
                                       tool_args={"app_name": "app"})
        assert p2 is not None and p2 != p1
        with open(p2, encoding="utf-8") as fh:
            m2 = json.load(fh)
        cfg = m2["revision_inputs"]["config_files"]
        assert any(e["path"].replace("\\", "/") == "app/.cproject"
                   for e in cfg)
        assert validate_manifest(m2, "ps_build", resolve_root=root) == []

    def test_snapshot_missing_fields_skip(self, tmp_path):
        root, snapshot, files = _pl_project(tmp_path)
        pl_dir = os.path.join(root, "manifests", "pl")

        # board_profile_sha256 missing/invalid in snapshot -> skip
        bad = dict(snapshot)
        bad["board_profile_sha256"] = ""   # not a valid sha256
        assert publish_pl_build_manifest(
            bad, {"status": "success", "data": {}},
            root, tool_args={"path": files["bit"]}) is None
        assert not os.path.isdir(pl_dir)

        # board_profile_sha256 missing for the PS manifest -> skip
        assert publish_ps_build_manifest(
            bad, {"status": "success", "data": {}},
            root, tool_args={"app_name": "app"}) is None
        assert not os.path.isdir(os.path.join(root, "manifests", "ps"))

    def test_result_missing_fields_skip(self, tmp_path):
        root, snapshot, files = _pl_project(tmp_path)
        pl_dir = os.path.join(root, "manifests", "pl")

        # no bitstream path in tool_args or result.data -> skip
        assert publish_pl_build_manifest(
            snapshot, {"status": "success", "data": {}}, root) is None
        assert not os.path.isdir(pl_dir)

        # no app_name in tool_args or result.data -> skip
        assert publish_ps_build_manifest(
            snapshot, {"status": "success", "data": {}}, root) is None
        assert not os.path.isdir(os.path.join(root, "manifests", "ps"))

    def test_existing_manifest_not_overwritten(self, tmp_path):
        root, snapshot, files = _pl_project(tmp_path)
        result = {"status": "success",
                  "data": {"timing_met": True, "wns_ns": 0.12, "tns_ns": 0.0}}
        args = {"path": files["bit"]}
        p1 = publish_pl_build_manifest(snapshot, result, root, tool_args=args)
        assert p1 is not None
        before = Path(p1).read_text(encoding="utf-8")
        p2 = publish_pl_build_manifest(snapshot, result, root, tool_args=args)
        assert p2 == p1
        after = Path(p2).read_text(encoding="utf-8")
        assert after == before  # immutable: no overwrite, no new file
        assert sorted(os.listdir(os.path.join(root, "manifests", "pl"))) == \
            [os.path.basename(p1)]

    @pytest.mark.asyncio
    async def test_verify_consistency_all_pass_with_generated_manifests(self, tmp_path):
        """P4: with PL/PS manifests generated on success, all 12 checks run."""
        root, snapshot, files = _ps_project(tmp_path)
        pl_path = publish_pl_build_manifest(
            snapshot, {"status": "success",
                       "data": {"timing_met": True, "wns_ns": 0.12, "tns_ns": 0.0}},
            root, tool_args={"path": files["bit"]})
        assert pl_path is not None
        ps_path = publish_ps_build_manifest(
            snapshot, {"status": "success", "data": {}},
            root, tool_args={"app_name": "app"})
        assert ps_path is not None
        plat_dir = os.path.join(root, "manifests", "platform")
        plat_path = os.path.join(plat_dir, sorted(os.listdir(plat_dir))[-1])

        result = await verify_consistency(
            platform_manifest_path=plat_path,
            pl_build_manifest_path=pl_path,
            ps_build_manifest_path=ps_path,
            board_profile_sha256=snapshot["board_profile_sha256"],
            resolve_root=root)
        data = result["data"]
        assert data["all_passed"] is True, data
        assert data["summary"]["total"] == 12
        assert data["summary"]["passed"] == 12
        assert data["summary"]["failed"] == 0
        assert data["summary"]["skipped"] == 0
        assert data["errors"] == []


# ── CommandRunner hook (production entry path) ──────────────────────────────

@pytest.fixture
def rtg():
    rt = Path(tempfile.mkdtemp())
    g = InstanceGuard(rt, "ws-build-manifest")
    g.determine_role()
    yield rt, g
    g.release_owner_lock()
    shutil.rmtree(str(rt), ignore_errors=True)


def _prep_ledger(rt, g, project_path, *, stage, platform_revision,
                 board_profile_sha256, session_id, prev=None):
    lp = rt / "l.json"
    def _i(l):
        l.instance_id = g.instance_id
        l.workspace_id = "ws-build-manifest"
        l.execution_lane = EXECUTION_LANE_IDLE
        l.primary_instance_id = g.instance_id
        l.worker["state"] = WORKER_STATE_ABSENT
        l.worker["pid"] = None
        l.context = {
            "session_id": session_id,
            "board_id": BOARD,
            "project_path": project_path,
            "board_package_revision": SH_PKG,
            "expected_board_revision": SH_PKG,
            "board_profile_sha256": board_profile_sha256,
            "current_stage": stage,
            "platform_revision": platform_revision,
            "pl_revision": None, "ps_revision": None,
        }
        if prev:
            l.previous_operation = prev
        return l
    ledger_transaction(g, lp, _i)
    return lp


async def _wait_terminal(guard, ledger_path, op_id, timeout_s=5.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        l, _ = ledger_read_shared(guard, ledger_path)
        if l.active_operation is None or l.active_operation.get("operation_id") != op_id:
            return l
        await asyncio.sleep(0.01)
    return None


class _FakeVivadoBridge:
    """VivadoTclBridge stand-in: returns a canned eval() result dict.

    B08: PL bridge tools (pl_generate_bitstream) run on the VivadoTclBridge,
    not the old-MCP VivadoAdapter.
    """

    def __init__(self, response=None):
        self.response = response or {"status": "success", "data": ""}
        self.calls = []

    @property
    def ready(self):
        return True

    async def eval(self, tcl, timeout_s=None):
        self.calls.append((tcl, timeout_s))
        return self.response


class _FakeXsctBridge:
    def __init__(self):
        self.ready = False
        self.workspace = ""
        self.stop_calls = 0

    async def start(self, workspace=""):
        self.ready = True
        self.workspace = workspace

    async def stop(self):
        self.stop_calls += 1
        self.ready = False

    async def eval(self, tcl, timeout_s=None, tolerate_stderr=False):
        return {"status": "success", "data": ""}


class TestRunnerHook:

    @pytest.mark.asyncio
    async def test_runner_publishes_pl_manifest_on_bitstream_success(self, rtg, tmp_path):
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        root, snapshot, files = _pl_project(tmp_path)
        rt, g = rtg
        lp = _prep_ledger(
            rt, g, root, stage="PL_BITSTREAM",
            platform_revision=snapshot["platform_revision"],
            board_profile_sha256=snapshot["board_profile_sha256"],
            session_id=snapshot["session_id"],
            prev={"operation_id": "op-prev", "tool_name": "pl_analyze_timing",
                  "status": "SUCCEEDED",
                  "completion_evidence": {"timing_met": True}})
        bridge = _FakeVivadoBridge({"status": "success", "data": "write_bitstream ok"})
        oreg = OperationRegistry()
        mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, vivado_bridge=bridge)
        r = await runner.run_command(
            "pl_generate_bitstream", {"path": files["bit"]},
            snapshot["session_id"], BOARD, root,
            executor="local",
            local_fn=_make_pl_bridge_local_fn("pl_generate_bitstream"),
            timeout_s=5, next_stage="PS_BUILD")
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]
        await _wait_terminal(g, lp, oid)
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["artifact_state"] == "PUBLISHED"
        assert l2.previous_operation["observation"]["current_step"] == \
            "PL_MANIFEST_PUBLISH"
        assert l2.previous_operation["completion_evidence"]["manifest_path"]
        assert l2.context["current_stage"] == "PS_BUILD"
        pl_dir = os.path.join(root, "manifests", "pl")
        assert os.path.isdir(pl_dir)
        names = sorted(os.listdir(pl_dir))
        assert names, "pl_generate_bitstream success did not publish a manifest"
        with open(os.path.join(pl_dir, names[0]), encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["manifest_type"] == "pl_build"
        assert m["bitstream_path"].replace("\\", "/") == "build/system_top.bit"

    @pytest.mark.asyncio
    async def test_runner_rejects_bitstream_success_when_manifest_cannot_publish(
            self, rtg, tmp_path):
        """O3 C08: tool success is not Operation success without PL Manifest."""
        from mcps.zynq_mcp.dispatcher import _make_pl_bridge_local_fn
        root, snapshot, files = _pl_project(tmp_path)
        os.unlink(files["xdc"])
        rt, g = rtg
        lp = _prep_ledger(
            rt, g, root, stage="PL_BITSTREAM",
            platform_revision=snapshot["platform_revision"],
            board_profile_sha256=snapshot["board_profile_sha256"],
            session_id=snapshot["session_id"],
            prev={"operation_id": "op-prev", "tool_name": "pl_analyze_timing",
                  "status": "SUCCEEDED",
                  "completion_evidence": {"timing_met": True}})
        bridge = _FakeVivadoBridge(
            {"status": "success", "data": "write_bitstream ok"})
        runner = CommandRunner(
            g, lp, OperationRegistry(), DomainExecutionMutex(),
            vivado_bridge=bridge)
        accepted = await runner.run_command(
            "pl_generate_bitstream", {"path": files["bit"]},
            snapshot["session_id"], BOARD, root,
            executor="local",
            local_fn=_make_pl_bridge_local_fn("pl_generate_bitstream"),
            timeout_s=5, next_stage="PS_BUILD")
        assert accepted["status"] == "success"
        await _wait_terminal(g, lp, accepted["data"]["operation_id"])
        final, _ = ledger_read_shared(g, lp)
        previous = final.previous_operation
        assert previous["status"] == "FAILED"
        assert previous["reason_code"] == "MANIFEST_PUBLISH_FAILED"
        assert previous["artifact_state"] == "FAILED"
        assert previous["observation"]["current_step"] == "PL_MANIFEST_PUBLISH"
        assert final.context["current_stage"] == "PL_BITSTREAM"
        assert not os.path.isdir(os.path.join(root, "manifests", "pl"))

    @pytest.mark.asyncio
    async def test_runner_publishes_ps_manifest_on_compile_success(self, rtg, tmp_path):
        from mcps.zynq_mcp.domains.ps import ps_bsp
        root, snapshot, files = _ps_project(tmp_path)
        rt, g = rtg
        lp = _prep_ledger(
            rt, g, root, stage="PS_BUILD",
            platform_revision=snapshot["platform_revision"],
            board_profile_sha256=snapshot["board_profile_sha256"],
            session_id=snapshot["session_id"])
        bridge = _FakeXsctBridge()
        oreg = OperationRegistry()
        mutex = DomainExecutionMutex()
        runner = CommandRunner(g, lp, oreg, mutex, worker=None, xsct_bridge=bridge)
        r = await runner.run_command(
            "ps_compile", {"app_name": "app"},
            snapshot["session_id"], BOARD, root,
            executor="local", local_fn=ps_bsp.compile_app,
            timeout_s=5, next_stage=None)
        assert r["status"] == "success"
        oid = r["data"]["operation_id"]
        await _wait_terminal(g, lp, oid)
        l2, _ = ledger_read_shared(g, lp)
        assert l2.previous_operation["status"] == OP_SUCCEEDED
        assert l2.previous_operation["artifact_state"] == "PUBLISHED"
        assert l2.previous_operation["observation"]["current_step"] == \
            "MANIFEST_PUBLISH"
        evidence = l2.previous_operation["completion_evidence"]
        assert evidence["elf_class"] == "ELFCLASS32"
        assert evidence["elf_machine"] == 40
        assert evidence["manifest_path"]
        ps_dir = os.path.join(root, "manifests", "ps")
        assert os.path.isdir(ps_dir)
        names = sorted(os.listdir(ps_dir))
        assert names, "ps_compile success did not publish a manifest"
        with open(os.path.join(ps_dir, names[0]), encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["manifest_type"] == "ps_build"
        assert m["elf_path"].replace("\\", "/") == "app/Debug/app.elf"


# ── B13-M4: disk-XSA truth beats stale session revision ──────────────────────

class TestDiskXsaTruth:
    def test_stale_snapshot_revision_loses_to_disk_xsa(self, tmp_path):
        """Real-board failure: the session snapshot still carries the OLD
        platform revision, so the PS manifest was built from the outdated
        platform manifest (built_from_platform_revision mismatch). With
        B13-M4 the manifest matching the on-disk platform.xsa wins."""
        root, snapshot, files = _ps_project(tmp_path)
        new_rev = snapshot["platform_revision"]
        # Simulate a stale session revision pointing at an OLD platform
        # manifest whose xsa_sha256 does NOT match the platform.xsa on disk.
        old_rev = "sha256:" + "33" * 32
        _write_platform_manifest(
            root, bp_sha=snapshot["board_profile_sha256"], rev=old_rev,
            xsa_sha=_sha(_write(root, "old.xsa", b"old-bytes")),
            bdw_sha="sha256:" + "44" * 32,
            addr_map={})
        stale_snap = dict(snapshot)
        stale_snap["platform_revision"] = old_rev
        result = {"status": "success", "data": {"app_name": "app", "built": True}}
        path = publish_ps_build_manifest(stale_snap, result, root,
                                         tool_args={"app_name": "app"})
        assert path is not None
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
        assert m["built_from_platform_revision"] == new_rev
