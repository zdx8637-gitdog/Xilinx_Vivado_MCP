"""test_m2_atoms.py — B13-M2: platform_package_user_ip / platform_set_bd_object_property.

Unit tests over the atom logic + host_live real-tool tests. The atoms are
fail-closed on both the catalog read-back and the property read-back.
"""
import asyncio
import os
import re
from pathlib import Path

import pytest

from mcps.zynq_mcp.adapters.vivado.vivado_bridge import (
    VivadoTclBridge, find_vivado,
)
from mcps.zynq_mcp.domains.platform import platform_atoms
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    platform_package_user_ip, platform_set_bd_object_property,
)
from mcps.zynq_mcp.domains.platform.platform_domain import (
    AdapterError, PlatformError,
)

needs_vivado = pytest.mark.skipif(
    find_vivado() is None, reason="vivado executable not found on this host")


@pytest.fixture
def files(tmp_path):
    src = tmp_path / "rtl" / "my_ip.v"
    src.parent.mkdir()
    src.write_text("module my_ip(input a, output b); assign b = a; endmodule",
                   encoding="utf-8")
    root = tmp_path / "repo"
    return str(src), str(root)


# B13-F1 修复轮#7: 假响应必须用 _run_tcl 的**真实契约形状**
# {"status": "success", "data": {"output": ...}}——output 在 data 层。
# 修复轮#6 曾用顶层 "output" 的假形状，与真实契约脱节，导致两原子在真板
# 恒报 FAILED 而单测全绿（F1 教训）。
def _run_tcl_ok(output):
    return {"status": "success", "data": {"output": output}}


def _fake_run_tcl_maker(output, *, create_component_dir=None):
    async def _fake(adapter, tcl, label):
        if create_component_dir is not None:
            os.makedirs(create_component_dir, exist_ok=True)
            Path(create_component_dir, "component.xml").write_text(
                "<spirit:component/>", encoding="utf-8")
        return _run_tcl_ok(output)
    return _fake


class _FakeVivadoBatch:
    """Replaces _run_vivado_batch: records the packaging script and
    simulates a successful standalone run (creates component.xml, parsing
    the save_dir from the emitted script — no hardcoded vendor/library)."""

    def __init__(self, rc=0, stdout="PACKAGE_DONE"):
        self.rc = rc
        self.stdout = stdout
        self.script_text = None
        self.log_path = None
        self.cwd = None

    async def __call__(self, script_path, log_path, *, cwd=None,
                       timeout_s=600.0):
        self.script_text = Path(script_path).read_text(encoding="utf-8")
        self.log_path = log_path
        self.cwd = cwd
        if self.rc == 0:
            m = re.search(r"ipx::package_project -root_dir \{([^}]*)\}",
                          self.script_text)
            if m:
                os.makedirs(m.group(1), exist_ok=True)
                Path(m.group(1), "component.xml").write_text(
                    "<spirit:component/>", encoding="utf-8")
        return self.rc, self.stdout


class _RegCapture:
    def __init__(self, output="VLNV user.org:user:my_ip:1.0"):
        self._output = output
        self.tcl = None

    async def __call__(self, adapter, tcl, label):
        self.tcl = tcl
        return _run_tcl_ok(self._output)


class TestPackageUserIp:
    def test_package_split_flow(self, files, monkeypatch):
        src, root = files
        batch = _FakeVivadoBatch()
        reg = _RegCapture()
        monkeypatch.setattr(platform_atoms, "_run_vivado_batch", batch)
        monkeypatch.setattr(platform_atoms, "_run_tcl", reg)
        r = asyncio.run(platform_package_user_ip(
            None, sources=[src], ip_name="my_ip", part="xc7z020clg400-2",
            root_dir=root))
        assert r["status"] == "success"
        assert r["data"]["vlnv"] == "user.org:user:my_ip:1.0"
        assert r["data"]["repo_root"] == root.replace("\\", "/")
        # packaging script: ipx flow + PACKAGE_DONE marker; NO in-session
        # registration (that would disturb the open design project)
        assert "ipx::package_project -root_dir" in batch.script_text
        assert "ipx::save_core" in batch.script_text
        assert 'puts "PACKAGE_DONE"' in batch.script_text
        assert "ip_repo_paths" not in batch.script_text
        assert batch.cwd == root.replace("\\", "/")  # fwd-slash normalization
        # registration Tcl: non-destructive append + catalog + VLNV read-back
        assert "NO_OPEN_PROJECT" in reg.tcl
        assert "[concat $__repos {" in reg.tcl  # existing repos preserved
        assert "update_ip_catalog -rebuild" in reg.tcl
        assert "get_ipdefs -all user.org:user:my_ip:1.0" in reg.tcl

    def test_no_open_project_fails_closed(self, files, monkeypatch):
        src, root = files
        monkeypatch.setattr(platform_atoms, "_run_vivado_batch",
                            _FakeVivadoBatch())
        monkeypatch.setattr(platform_atoms, "_run_tcl",
                            _RegCapture("NO_OPEN_PROJECT"))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[src], ip_name="my_ip",
                part="xc7z020clg400-2", root_dir=root))
        assert ei.value.reason_code == "USER_IP_NO_OPEN_PROJECT"

    def test_catalog_verify_fails_closed(self, files, monkeypatch):
        src, root = files
        monkeypatch.setattr(platform_atoms, "_run_vivado_batch",
                            _FakeVivadoBatch())
        monkeypatch.setattr(platform_atoms, "_run_tcl", _RegCapture(""))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[src], ip_name="my_ip",
                part="xc7z020clg400-2", root_dir=root))
        assert ei.value.reason_code == "USER_IP_CATALOG_VERIFY_FAILED"

    def test_batch_failure_maps_reason(self, files, monkeypatch):
        src, root = files
        monkeypatch.setattr(
            platform_atoms, "_run_vivado_batch",
            _FakeVivadoBatch(rc=1, stdout="ERROR: [IP_Flow 19-123] boom"))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[src], ip_name="my_ip",
                part="xc7z020clg400-2", root_dir=root))
        assert ei.value.reason_code == "USER_IP_PACKAGE_FAILED"
        assert "boom" in str(ei.value)

    def test_vivado_not_found_fails_closed(self, files, monkeypatch):
        src, root = files
        monkeypatch.setattr(platform_atoms, "_find_vivado_batch_exe",
                            lambda: None)
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[src], ip_name="my_ip",
                part="xc7z020clg400-2", root_dir=root))
        assert ei.value.reason_code == "VIVADO_NOT_FOUND"

    def test_missing_source_rejected(self, files, monkeypatch):
        _, root = files
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[str(Path(root) / "absent.v")],
                ip_name="my_ip", part="xc7z020clg400-2", root_dir=root))
        assert ei.value.reason_code == "SOURCE_NOT_FOUND"

    def test_empty_sources_rejected(self):
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_package_user_ip(
                None, sources=[], ip_name="my_ip",
                part="xc7z020clg400-2", root_dir="r"))
        assert ei.value.reason_code == "INVALID_ARGUMENT"


class TestPackagingHelpers:
    def test_find_vivado_exec_env_priority(self, tmp_path, monkeypatch):
        fake = tmp_path / "fake_vivado.bat"
        fake.write_text("@echo off", encoding="utf-8")
        monkeypatch.setenv("VIVADO_EXEC", str(fake))
        assert platform_atoms._find_vivado_batch_exe() == str(fake)

    def test_find_vivado_falls_back_to_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv("VIVADO_EXEC", raising=False)
        monkeypatch.setenv("VIVADO_ROOT", str(tmp_path))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "vivado.bat"
        fake.write_text("@echo off", encoding="utf-8")
        assert platform_atoms._find_vivado_batch_exe() == str(fake)

    def test_windows_bat_launched_under_cmd(self):
        if os.name == "nt":
            cmd = platform_atoms._windows_launch_cmd(
                r"D:\x\vivado.bat", ["-mode", "batch"])
            assert cmd[:4] == ["cmd.exe", "/d", "/c", r"D:\x\vivado.bat"]
            assert cmd[4:] == ["-mode", "batch"]


class _ErrorRunTcl:
    """_run_tcl stand-in that raises AdapterError (simulates a Tcl error
    from the real bridge, e.g. the atom's own ``error "BD_OBJECT_NOT_FOUND"``
    propagating through the VivadoAdapter)."""

    def __init__(self, message):
        self._message = message

    async def __call__(self, adapter, tcl, label):
        raise AdapterError(self._message)


def _tcl_capture(monkeypatch, output="OBJVAL 100000000"):
    """Monkeypatch _run_tcl to succeed and capture the emitted Tcl."""
    captured = {}

    async def _fake(adapter, tcl, label):
        captured["tcl"] = tcl
        return _run_tcl_ok(output)
    monkeypatch.setattr(platform_atoms, "_run_tcl", _fake)
    return captured


class _ErrorRunTcl:
    """_run_tcl stand-in that raises AdapterError (simulates a Tcl error
    from the real bridge, e.g. the atom's own ``error "BD_OBJECT_NOT_FOUND"``
    propagating through the VivadoAdapter)."""

    def __init__(self, message):
        self._message = message

    async def __call__(self, adapter, tcl, label):
        raise AdapterError(self._message)


class TestSetBdObjectProperty:
    def test_port_kind_success(self, monkeypatch):
        captured = _tcl_capture(monkeypatch)
        r = asyncio.run(platform_set_bd_object_property(
            None, bd_object="m_clk_port", property="CONFIG.FREQ_HZ",
            value="100000000"))
        assert r["status"] == "success"
        assert r["data"]["object"] == "m_clk_port"
        tcl = captured["tcl"]
        # object kind auto-detection order: ports first, then pins, then
        # interface pins (real-Vivado verified: wrong-kind queries match
        # nothing — D8)
        assert tcl.index("get_bd_ports -quiet {m_clk_port}") \
            < tcl.index("get_bd_pins -quiet {m_clk_port}") \
            < tcl.index("get_bd_intf_pins -quiet {m_clk_port}")
        assert ("set_property -dict [list {CONFIG.FREQ_HZ} {100000000}] "
                "$__obj") in tcl
        assert 'puts "OBJVAL [get_property {CONFIG.FREQ_HZ} $__obj]"' in tcl

    def test_pin_kind_success(self, monkeypatch):
        captured = _tcl_capture(monkeypatch, "OBJVAL S_AXI")
        r = asyncio.run(platform_set_bd_object_property(
            None, bd_object="m2_probe_0/aclk",
            property="CONFIG.ASSOCIATED_BUSIF", value="S_AXI"))
        assert r["status"] == "success"
        # pin path: the port query finds nothing, the pin query resolves it;
        # real-Vivado verified this is the true home of ASSOCIATED_BUSIF
        assert "[get_bd_pins -quiet {m2_probe_0/aclk}]" in captured["tcl"]
        assert ("set_property -dict [list {CONFIG.ASSOCIATED_BUSIF} {S_AXI}] "
                "$__obj") in captured["tcl"]
        assert ('puts "OBJVAL [get_property {CONFIG.ASSOCIATED_BUSIF} '
                '$__obj]"') in captured["tcl"]

    def test_intf_pin_kind_success(self, monkeypatch):
        captured = _tcl_capture(monkeypatch, "OBJVAL AXI4LITE")
        r = asyncio.run(platform_set_bd_object_property(
            None, bd_object="axi_gpio_0/S_AXI",
            property="CONFIG.PROTOCOL", value="AXI4LITE"))
        assert r["status"] == "success"
        assert "get_bd_intf_pins -quiet {axi_gpio_0/S_AXI}" in captured["tcl"]

    def test_readback_mismatch_fails_closed(self, monkeypatch):
        monkeypatch.setattr(platform_atoms, "_run_tcl",
                            _fake_run_tcl_maker("OBJVAL "))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_set_bd_object_property(
                None, bd_object="axi_ic_0/S00_AXI",
                property="CONFIG.DATA_WIDTH", value="64"))
        # real-Vivado verified: read-only params (axi_interconnect S00_AXI
        # DATA_WIDTH) raise CRITICAL WARNING and read back empty — the atom
        # must fail closed, not report a silent success
        assert ei.value.reason_code == "BD_OBJECT_PROPERTY_VERIFY_FAILED"

    def test_object_not_found_maps_reason(self, monkeypatch):
        monkeypatch.setattr(platform_atoms, "_run_tcl",
                            _ErrorRunTcl("BD_OBJECT_NOT_FOUND:nope"))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_set_bd_object_property(
                None, bd_object="nope", property="P", value="V"))
        assert ei.value.reason_code == "BD_OBJECT_NOT_FOUND"

    def test_ambiguous_object_maps_reason(self, monkeypatch):
        monkeypatch.setattr(platform_atoms, "_run_tcl",
                            _ErrorRunTcl("BD_OBJECT_AMBIGUOUS:aclk"))
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_set_bd_object_property(
                None, bd_object="aclk", property="P", value="V"))
        assert ei.value.reason_code == "BD_OBJECT_AMBIGUOUS"

    def test_invalid_args_rejected(self):
        with pytest.raises(PlatformError) as ei:
            asyncio.run(platform_set_bd_object_property(
                None, bd_object="", property="P", value="V"))
        assert ei.value.reason_code == "INVALID_ARGUMENT"


# ── host_live real-tool tests (Vivado 2023.1) ─────────────────────────
# Excluded from the non-hardware regression via the host_live marker.

class TestPackagingHostLive:
    @pytest.mark.host_live
    @needs_vivado
    def test_real_vivado_batch_packages_user_ip_twice(self, tmp_path):
        """Real vivado -mode batch subprocess packages RTL into a user IP
        repo; a second run (idempotent re-packaging) must also succeed."""
        src = tmp_path / "rtl" / "probe_ip.v"
        src.parent.mkdir()
        src.write_text(
            "module probe_ip(input wire aclk, input wire aresetn,\n"
            "  input wire [3:0] probe_in, output reg [3:0] probe_out);\n"
            "  always @(posedge aclk or negedge aresetn) begin\n"
            "    if (!aresetn) probe_out <= 4'h0;\n"
            "    else probe_out <= probe_in;\n"
            "  end\n"
            "endmodule\n", encoding="utf-8")
        root = str(tmp_path / "repo")
        Path(root).mkdir(parents=True)
        save_dir = os.path.join(root, "user.org", "user", "probe_ip", "1.0")
        pkg_proj = os.path.join(root, ".pkg_proj")
        script = platform_atoms._package_user_ip_tcl(
            [str(src)], "xc7z020clg400-2", save_dir, pkg_proj, "probe_ip",
            "user.org", "user")
        script_path = str(tmp_path / "package.tcl")
        Path(script_path).write_text(script, encoding="utf-8")
        for run in (1, 2):
            rc, stdout = asyncio.run(platform_atoms._run_vivado_batch(
                script_path, str(tmp_path / f"vivado{run}.log"), cwd=root))
            assert rc == 0, stdout[-800:]
            assert "PACKAGE_DONE" in stdout, stdout[-800:]
            assert os.path.isfile(os.path.join(save_dir, "component.xml"))

    @pytest.mark.host_live
    @needs_vivado
    @pytest.mark.asyncio
    async def test_real_vivado_registration_preserves_open_design(self,
                                                                  tmp_path):
        """Real Vivado: a persistent session with an open design project +
        live BD keeps working while a THROWAWAY batch subprocess packages an
        IP; the registration Tcl then makes the VLNV instantiable without
        disturbing the BD (the production split of platform_package_user_ip)."""
        root = str(tmp_path / "repo")
        Path(root).mkdir(parents=True)
        src = tmp_path / "probe_ip.v"
        src.write_text(
            "module probe_ip(input wire aclk, input wire aresetn,\n"
            "  input wire [3:0] probe_in, output reg [3:0] probe_out);\n"
            "  always @(posedge aclk or negedge aresetn) begin\n"
            "    if (!aresetn) probe_out <= 4'h0;\n"
            "    else probe_out <= probe_in;\n"
            "  end\n"
            "endmodule\n", encoding="utf-8")
        save_dir = os.path.join(root, "user.org", "user", "probe_ip", "1.0")
        pkg_proj = os.path.join(root, ".pkg_proj")
        script = platform_atoms._package_user_ip_tcl(
            [str(src)], "xc7z020clg400-2", save_dir, pkg_proj, "probe_ip",
            "user.org", "user")
        script_path = str(tmp_path / "package.tcl")
        Path(script_path).write_text(script, encoding="utf-8")

        bridge = VivadoTclBridge()
        await bridge.start()
        try:
            proj = str(tmp_path / "design_proj")
            r = await bridge.eval(
                f"create_project -force m2_design {{{proj}}} "
                "-part xc7z020clg400-2")
            assert r["status"] == "success"
            r = await bridge.eval("create_bd_design m2_design_bd")
            assert r["status"] == "success"
            r = await bridge.eval(
                "create_bd_port -dir I -type clk m_clk_port\n"
                "set_property -dict [list CONFIG.FREQ_HZ {100000000}] "
                "[get_bd_ports m_clk_port]")
            assert r["status"] == "success"
            # packaging runs in a separate Vivado process while the session
            # holds the open design — it must not disturb it
            rc, stdout = await platform_atoms._run_vivado_batch(
                script_path, str(tmp_path / "vivado.log"), cwd=root)
            assert rc == 0 and "PACKAGE_DONE" in stdout, stdout[-800:]
            # the design session is still alive
            r = await bridge.eval("puts [get_bd_ports *]")
            assert r["status"] == "success" and "m_clk_port" in r["data"]
            # registration: exact production Tcl string
            vlnv = "user.org:user:probe_ip:1.0"
            r = await bridge.eval(platform_atoms._register_user_ip_tcl(
                root, vlnv))
            assert r["status"] == "success"
            assert f"VLNV {vlnv}" in r["data"]
            # the packaged IP is now instantiable, and the BD is intact
            r = await bridge.eval(
                f"create_bd_cell -type ip -vlnv {vlnv} m2_probe_0")
            assert r["status"] == "success", r
            r = await bridge.eval("puts [get_bd_ports *]")
            assert "m_clk_port" in r["data"]
        finally:
            await bridge.stop()
        assert not bridge.ready
