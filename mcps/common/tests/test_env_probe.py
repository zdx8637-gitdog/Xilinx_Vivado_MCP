"""B03-T-3xx: Environment probing — mandatory (mock) + optional (host-live/device-live)."""

import json, os, re, sys, subprocess, pytest
from pathlib import Path

from mcps.common.env_probe import (
    probe_vivado, probe_vitis, probe_xsct,
    probe_uart_devices, probe_all,
    ToolProbeResult, UartDevice, EnvReport,
    _get_search_roots,
    _verify_vivado, _verify_vitis, _verify_xsct,
    _mark_uart_presence,
    _parse_version_output, _parse_xsct_version,
)

PKG_DIR = str(Path(__file__).resolve().parents[3] / "boards" / "ALINX_AX7020_v1.0")


# -- helpers --

def _mock_runner(output):
    def r(args, timeout):
        return output, "", 0
    return r

def _mock_runner_fail(exit_code=1):
    def r(args, timeout):
        return "", "ERROR", exit_code
    return r

def _mock_runner_timeout():
    def r(args, timeout):
        raise subprocess.TimeoutExpired(args, timeout)
    return r

def _mock_runner_not_found():
    def r(args, timeout):
        raise FileNotFoundError
    return r

def _make_install(tmp_path, install_subdir, version, exe_name):
    root = str(tmp_path)
    bd = os.path.join(root, install_subdir, version, "bin")
    os.makedirs(bd, exist_ok=True)
    with open(os.path.join(bd, exe_name), "w") as f:
        f.write("@echo fake tool")
    # For Vitis, create data/version.bat
    if install_subdir == "Vitis":
        dd = os.path.join(root, install_subdir, version, "data")
        os.makedirs(dd, exist_ok=True)
        vb = os.path.join(dd, "version.bat")
        with open(vb, "w") as f:
            f.write("SET XILINX_VERSION_DEFAULT=2023.1\nSET XILINX_VERSION_VITIS=2023.1\n")
    return root


# -- T-301 --
def test_probe_all_returns_env_report():
    result = probe_all(
        runner=_mock_runner("dummy"),
        device_enumerator=lambda: ([], []),
    )
    assert isinstance(result, EnvReport)
    assert result.vivado is not None
    assert result.vitis is not None
    assert result.xsct is not None
    d = json.loads(result.to_json())
    assert "vivado" in d
    assert "vitis" in d
    assert "xsct" in d
    for forbidden in ("jtag_targets", "cable_serial", "hw_server", "FT232"):
        assert forbidden not in result.to_json().lower()


# -- T-302, T-303 --

def test_vivado_found_parses_version(tmp_path):
    """Vivado Tcl mode: __VERSION=2023.1 output."""
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    vivado_output = "SW Build 3865809\n__VERSION=2023.1\n"
    r = probe_vivado(search_roots=[root], runner=_mock_runner(vivado_output))
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.version_source == "version_command"
    assert r.error_code is None
    assert r.build == "3865809"

def test_vivado_not_found():
    r = probe_vivado(search_roots=[])
    assert r.found is False
    assert r.reason_code == "ENV_VIVADO_NOT_FOUND"

def test_vitis_not_found():
    r = probe_vitis(search_roots=[])
    assert r.found is False
    assert r.reason_code == "ENV_VITIS_NOT_FOUND"

def test_xsct_not_found():
    r = probe_xsct(search_roots=[])
    assert r.found is False
    assert r.reason_code == "ENV_XSCT_NOT_FOUND"

def test_vivado_prefers_2023_1(tmp_path):
    root = str(tmp_path)
    _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    _make_install(tmp_path, "Vivado", "2022.2", "vivado.bat")
    r = probe_vivado(search_roots=[root],
                     runner=_mock_runner("__VERSION=2023.1\n"))
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert any("2022.2" in w for w in r.warnings)

def test_vivado_version_mismatch(tmp_path):
    """Directory=2023.1 but Tcl returns 2022.2."""
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root],
                     runner=_mock_runner("__VERSION=2022.2\n"))
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_MISMATCH"
    assert r.version == "2022.2"

def test_xsct_parses_version(tmp_path):
    root = _make_install(tmp_path, "Vitis", "2023.1", "xsct.bat")
    r = probe_xsct(search_roots=[root],
                   runner=_mock_runner("xsct 2023.1.0\nSW Build 0 on ..."))
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.full_version == "xsct 2023.1.0"
    assert r.version_source == "version_command"

def test_vitis_metadata_version(tmp_path):
    root = _make_install(tmp_path, "Vitis", "2023.1", "vitis.bat")
    r = probe_vitis(search_roots=[root])
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.version_source == "install_metadata"

def test_vitis_metadata_missing(tmp_path):
    """Vitis without data/version.bat — fallback to unsupported."""
    root = str(tmp_path)
    bd = os.path.join(root, "Vitis", "2023.1", "bin")
    os.makedirs(bd, exist_ok=True)
    with open(os.path.join(bd, "vitis.bat"), "w") as f:
        f.write("@echo fake")
    # No data/version.bat created
    r = probe_vitis(search_roots=[root])
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_QUERY_FAILED"


# -- T-304 --
def test_no_tool_paths_in_board_package():
    prohibited = [r"D:\Xilinx", r"C:\Xilinx", "vivado.bat", "vitis.bat", "xsct.bat"]
    for root, dirs, files in os.walk(PKG_DIR):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            for p in prohibited:
                assert p not in content, f"Prohibited '{p}' in {fp}"
        for dn in dirs:
            assert dn not in prohibited, f"Prohibited dir '{dn}' in {root}"
    for root, dirs, files in os.walk(PKG_DIR):
        for fn in files:
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(root, fn)
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            coms = re.findall(r'\bCOM\d+\b', content)
            assert len(coms) == 0, f"COM port(s) {coms} in {fp}"


# -- T-305 --
def test_no_hardcoded_com_ports():
    src_path = Path(__file__).resolve().parent.parent / "env_probe.py"
    with open(src_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for lineno, line in enumerate(lines, 1):
        code = line.split("#")[0]
        matches = re.findall(r'\bCOM\d+\b', code)
        if matches:
            raise AssertionError(
                f"env_probe.py:{lineno}: hardcoded COM port(s) "
                f"{matches} in: {code.strip()}")


# -- version failures --
def test_timeout_version_query(tmp_path):
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root], runner=_mock_runner_timeout())
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_QUERY_FAILED"

def test_nonzero_exit_version_query(tmp_path):
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root], runner=_mock_runner_fail(1))
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_QUERY_FAILED"

def test_empty_output_version_query(tmp_path):
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root], runner=_mock_runner(""))
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_UNPARSEABLE"

def test_unparseable_output(tmp_path):
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root], runner=_mock_runner("garbage output"))
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_UNPARSEABLE"

def test_xsct_command_not_found():
    """XSCT runner throws FileNotFoundError → query failed."""
    r = probe_xsct(search_roots=[],
                   runner=_mock_runner_not_found())
    assert r.found is False

def test_xsct_nonzero_exit():
    r = probe_xsct(search_roots=[],
                   runner=_mock_runner_fail(1))
    assert r.found is False

def test_path_discovery(tmp_path):
    root = _make_install(tmp_path, "Vivado", "2023.1", "vivado.bat")
    r = probe_vivado(search_roots=[root],
                     runner=_mock_runner("__VERSION=2023.1\n"))
    assert r.found is True
    assert r.supported is True


# -- direct verifier tests --
def test_verify_vivado_parses():
    sup, ver, full, build, src, rc = _verify_vivado(
        "F:/test.exe",
        runner=_mock_runner("SW Build 3865809\n__VERSION=2023.1\n"),
    )
    assert sup is True
    assert ver == "2023.1"
    assert build == "3865809"

def test_verify_xsct_normalizes():
    sup, ver, full, build, src, rc = _verify_xsct(
        "F:/test.exe",
        runner=_mock_runner("xsct 2023.1.0\nSW Build 0 on ..."),
    )
    assert sup is True
    assert ver == "2023.1"
    assert full == "xsct 2023.1.0"

def test_verify_vitis_metadata():
    import tempfile
    d = tempfile.mkdtemp()
    try:
        os.makedirs(os.path.join(d, "data"), exist_ok=True)
        with open(os.path.join(d, "data", "version.bat"), "w") as f:
            f.write("SET XILINX_VERSION_DEFAULT=2023.1\nSET XILINX_VERSION_VITIS=2023.1\n")
        sup, ver, full, build, src, rc = _verify_vitis(d)
        assert sup is True
        assert ver == "2023.1"
        assert src == "install_metadata"
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


# -- version parsing --
def test_version_parse_standard():
    v, b = _parse_version_output("vivado", "Vivado v2023.1 (64-bit) Build 3457360", None)
    assert v == "2023.1"
    assert b == "3457360"

def test_xsct_parse():
    assert _parse_xsct_version("xsct 2023.1.0") == "2023.1"
    assert _parse_xsct_version("xsct 2022.2.1") == "2022.2"
    assert _parse_xsct_version("garbage") is None

def test_search_roots_env_var(monkeypatch, tmp_path):
    root = str(tmp_path)
    monkeypatch.setenv("ZYNQ_EDA_SEARCH_ROOTS", root)
    assert root in _get_search_roots()

def test_search_roots_explicit_priority():
    r = probe_vivado(search_roots=[])
    assert r.found is False


# -- UART mock --
def test_uart_empty_on_non_windows():
    ps, pl = probe_uart_devices(device_enumerator=lambda: ([], []))
    assert ps == []
    assert pl == []

def test_uart_mock_enumerator():
    def mock():
        ps = [UartDevice(port="COM99", vid="0x10C4", pid="0xEA60",
                         friendly_name="CP210x Test", present=True,
                         role="ps_uart", direction="bidirectional")]
        pl = [UartDevice(port="COM88", vid="0x1A86", pid="0x7523",
                         friendly_name="CH340 Test", present=True,
                         role="pl_uart_lab_fixture", direction="board_to_host_only")]
        return (ps, pl)
    ps, pl = probe_uart_devices(device_enumerator=mock)
    assert len(ps) == 1
    assert ps[0].direction == "bidirectional"
    assert len(pl) == 1
    assert pl[0].direction == "board_to_host_only"

def test_uart_historical_device_present_false():
    """Production _mark_uart_presence: only COM4 is active, COM3 is not."""
    # Raw devices from registry (present not yet determined)
    raw_devices = [
        UartDevice(port="COM3", vid="0x10C4", pid="0xEA60",
                   present=False, role="ps_uart",
                   direction="bidirectional"),
        UartDevice(port="COM4", vid="0x10C4", pid="0xEA60",
                   present=False, role="ps_uart",
                   direction="bidirectional"),
    ]
    active_ports = {"COM4", "COM5"}
    marked = _mark_uart_presence(raw_devices, active_ports)
    assert len(marked) == 2
    com4 = [d for d in marked if d.port == "COM4"][0]
    assert com4.present is True
    com3 = [d for d in marked if d.port == "COM3"][0]
    assert com3.present is False


# -- JSON + JTAG --
def test_env_report_json_serializable():
    r = EnvReport(generated_at="2026-08-04T12:00:00Z",
                  vivado=ToolProbeResult(name="vivado", found=False),
                  vitis=ToolProbeResult(name="vitis", found=False),
                  xsct=ToolProbeResult(name="xsct", found=False))
    d = json.loads(r.to_json())
    assert d["vivado"]["name"] == "vivado"

def test_env_report_no_jtag():
    r = probe_all(runner=_mock_runner("dummy"),
                  device_enumerator=lambda: ([], []))
    j = r.to_json()
    d = json.loads(j)
    for forbidden in ("jtag_targets", "cable_serial", "hw_server", "FT232"):
        assert forbidden not in j.lower()

def test_tool_probe_result_to_dict():
    r = ToolProbeResult(name="test", found=False,
                        error_code="ENV_ERROR", reason_code="ENV_TEST_NOT_FOUND")
    d = r.to_dict()
    assert d["name"] == "test"


# ══════════════════════════════════════════════════════════════════
# -- Host-live (T-501 to T-504) -- STRICT assertions --

@pytest.mark.host_live
def test_vivado_host_live():
    """T-501: Vivado — must be found, supported, version 2023.1, confirmed by Tcl."""
    r = probe_vivado()
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.error_code is None
    assert r.reason_code is None
    assert r.version_source == "version_command", \
        f"Expected version_command, got {r.version_source}"

@pytest.mark.host_live
def test_vitis_host_live():
    """T-502: Vitis — must be found, supported, version 2023.1, confirmed by metadata."""
    r = probe_vitis()
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.error_code is None
    assert r.reason_code is None
    assert r.version_source == "install_metadata", \
        f"Expected install_metadata, got {r.version_source}"

@pytest.mark.host_live
def test_xsct_host_live():
    """T-503: XSCT — must be found, supported, version 2023.1, confirmed by -eval."""
    r = probe_xsct()
    assert r.found is True
    assert r.supported is True
    assert r.version == "2023.1"
    assert r.error_code is None
    assert r.reason_code is None
    assert r.version_source == "version_command", \
        f"Expected version_command, got {r.version_source}"

@pytest.mark.host_live
def test_probe_all_host_live():
    """T-504: Full probe — all three tools must pass strict assertions."""
    r = probe_all()
    for tool in (r.vivado, r.vitis, r.xsct):
        assert tool is not None
        assert tool.found is True, f"{tool.name} not found"
        assert tool.supported is True, f"{tool.name} not supported: {tool.reason_code}"
        assert tool.version == "2023.1", f"{tool.name} version={tool.version}"
        assert tool.error_code is None, f"{tool.name} error_code={tool.error_code}"
        assert tool.reason_code is None, f"{tool.name} reason_code={tool.reason_code}"
    # version_source must not be install_directory
    assert r.vivado.version_source == "version_command"
    assert r.vitis.version_source == "install_metadata"
    assert r.xsct.version_source == "version_command"


# ══════════════════════════════════════════════════════════════════
# -- Device-live (T-601) -- STRICT assertions --

@pytest.mark.device_live
def test_uart_device_live():
    """T-601: Real CP210x + CH340 — must find active devices."""
    ps, pl = probe_uart_devices()
    ps_active = [d for d in ps if d.present]
    pl_active = [d for d in pl if d.present]

    assert len(ps_active) >= 1, \
        f"No active PS UART (10C4:EA60 CP210x). All: {[(d.port, d.present) for d in ps]}"
    for d in ps_active:
        assert d.vid == "0x10C4"
        assert d.pid == "0xEA60"
        assert d.port is not None and re.match(r"COM\d+", d.port)
        assert d.direction == "bidirectional"

    assert len(pl_active) >= 1, \
        f"No active PL UART (1A86:7523 CH340). All: {[(d.port, d.present) for d in pl]}"
    for d in pl_active:
        assert d.vid == "0x1A86"
        assert d.pid == "0x7523"
        assert d.port is not None and re.match(r"COM\d+", d.port)
        assert d.direction == "board_to_host_only"
