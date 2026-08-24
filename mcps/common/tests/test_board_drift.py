"""B03-T-2xx: Drift & error configuration detection tests.

All tampering on tmp_path copies. Real boards/ALINX_AX7020_v1.0/ never modified.
Every test asserts BOTH top-level ErrorCode AND internal reason_code.
"""

import json, os, re, pytest
from pathlib import Path

from mcps.common.board_profile import board_profile_load, BoardProfileError, _cache
from mcps.common.revision import sha256_file as _sha256_file
from mcps.common.board_package import (
    compute_package_revision,
    ValidationIssue,
)

PKG_DIR = str(Path(__file__).resolve().parents[3] / "boards" / "ALINX_AX7020_v1.0")

def _clear_cache():
    _cache.clear()

# -- helpers --

def _min_prof(board_id):
    return {
        "board_id": board_id, "vendor": "TEST", "model": "TEST",
        "part": "xc7z020clg400-2", "vivado_part": "xc7z020clg400-2",
        "fixture_only": False,
        "ddr_chip": "MT41J256M16 RE-125", "ddr_chip_count": 2,
        "ddr_physical_bytes": 1073741824, "ddr_configured_bytes": 536870912,
        "ddr_configured_highaddr": 536870911, "ddr_frequency_hz": 533333333,
        "ddr_bus_width_bits": 32,
        "qspi_chip": "W25Q256", "qspi_physical_bytes": 33554432,
        "qspi_linear_window_bytes": 16777216, "qspi_base_address": 4227858432,
        "qspi_data_mode": "x4",
        "pl_leds": {"count": 4, "pins": ["J16","K16","M15","M14"],
                     "polarity": "active-low"},
        "ps_leds": {"count": 2, "mio_pins": [0, 13],
                     "polarity": "active-low"},
        "pl_oscillator_hz": 50000000, "pl_oscillator_pin": "U18",
        "ps_clock_hz": 33333333,
        "uart": {"controller": "UART1", "mio_pins": [48, 49],
                 "default_baud": 115200},
        "usb_bridge": {"chip": "CP2102-GM", "family": "CP210x",
                       "vid": "0x10C4", "pid": "0xEA60"},
        "pl_resources": {"luts": 53200, "ffs": 106400,
                         "bram36": 140, "dsp48": 220},
        "ps7_preset_sha256": "", "xdc_sha256": "",
        "source_catalog": [{"source_id": "TEST", "distribution_path": "path/file.tcl",
                            "sha256": "sha256:" + "ab" * 32, "role": "test"}],
    }

_dfl_xdc = (
    "set_property PACKAGE_PIN U18 [get_ports sys_clk]\n"
    "set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]\n"
    "create_clock -period 20.000 -name pl_clk [get_ports sys_clk]\n"
    "set_property PACKAGE_PIN J16 [get_ports {led_pins[3]}]\n"
    "set_property PACKAGE_PIN K16 [get_ports {led_pins[2]}]\n"
    "set_property PACKAGE_PIN M15 [get_ports {led_pins[1]}]\n"
    "set_property PACKAGE_PIN M14 [get_ports {led_pins[0]}]\n"
    "set_property IOSTANDARD LVCMOS33 [get_ports {led_pins[*]}]\n"
)

def _write_content_files(pkg, board_id, preset_content=None, xdc_content=None,
                         sources_content=None, readme_content=None):
    for fn, content in [
        ("ps7_preset.tcl", preset_content or ("# preset " + board_id)),
        ("board.xdc", xdc_content or _dfl_xdc),
        ("SOURCES.md", sources_content or ("# sources " + board_id)),
        ("README.md", readme_content or ("# readme " + board_id)),
    ]:
        with open(os.path.join(pkg, fn), "w") as f:
            f.write(content)

def _seal_package(pkg, board_id, profile_overrides=None):
    """Build/re-build a SHA-consistent draft package.

    Writes all files, computes SHA256s, builds revision_inputs and manifest.
    """
    from mcps.common.revision import compute_revision as _compute_rev

    profile = _min_prof(board_id)
    if profile_overrides:
        profile.update(profile_overrides)

    preset_sha = _sha256_file(os.path.join(pkg, "ps7_preset.tcl"))
    xdc_sha = _sha256_file(os.path.join(pkg, "board.xdc"))
    sources_sha = _sha256_file(os.path.join(pkg, "SOURCES.md"))
    readme_sha = _sha256_file(os.path.join(pkg, "README.md"))

    profile["ps7_preset_sha256"] = preset_sha
    profile["xdc_sha256"] = xdc_sha
    prof_path = os.path.join(pkg, f"board_profile_{board_id}.json")
    with open(prof_path, "w") as f:
        json.dump(profile, f)
    profile_sha = _sha256_file(prof_path)

    ri = {
        "board_profile_sha256": profile_sha,
        "ps7_preset_sha256": preset_sha,
        "board_xdc_sha256": xdc_sha,
        "sources_md_sha256": sources_sha,
        "readme_md_sha256": readme_sha,
    }
    rev = _compute_rev(ri)
    manifest = {
        "schema_version": "1.0",
        "manifest_type": "board_configuration",
        "board_id": board_id,
        "package_version": "1.0",
        "status": "draft",
        "manifest_revision": rev,
        "revision_inputs": ri,
        "generated_at": "2026-08-04T12:00:00+08:00",
        "files": [
            {"path": f"board_profile_{board_id}.json", "sha256": profile_sha,
             "role": "primary_data_source"},
            {"path": "ps7_preset.tcl", "sha256": preset_sha,
             "role": "ps7_hardware_preset"},
            {"path": "board.xdc", "sha256": xdc_sha,
             "role": "pl_pin_constraints"},
            {"path": "SOURCES.md", "sha256": sources_sha,
             "role": "provenance_record"},
            {"path": "README.md", "sha256": readme_sha,
             "role": "human_description"},
        ],
    }
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return profile, manifest, rev

def _fresh_pkg(tmp_path, board_id, profile_overrides=None, xdc_content=None):
    pkg = os.path.join(str(tmp_path), board_id)
    os.makedirs(pkg, exist_ok=True)
    _write_content_files(pkg, board_id, xdc_content=xdc_content)
    return pkg, *_seal_package(pkg, board_id, profile_overrides=profile_overrides)

# == T-201: Profile SHA drift → fingerprint change recorded (no reject) ==

def test_profile_sha_drift_recorded(tmp_path):
    """T-201 (B12-B03 erratum): profile tamper → board_profile_sha256 changes and is recorded (no reject)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T201")
    p1 = board_profile_load("T201", search_dirs=[pkg], allow_draft=True)
    sha1 = p1["sha256"]
    pp = os.path.join(pkg, "board_profile_T201.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    prof["ddr_physical_bytes"] = 999999999
    with open(pp, "w") as f:
        json.dump(prof, f)
    _clear_cache()
    p2 = board_profile_load("T201", search_dirs=[pkg], allow_draft=True)
    assert p2["sha256"] != sha1
    assert p2["sha256"].startswith("sha256:")

# == T-202: DDR configured > physical (framework trusts user input) ==

def test_ddr_capacity_inconsistency_accepted(tmp_path):
    """T-202 (B12-B03 erratum): cross-field semantic inconsistency → no longer rejects."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T202", profile_overrides={
        "ddr_physical_bytes": 268435456,
        "ddr_configured_bytes": 536870912,
    })
    p = board_profile_load("T202", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T202"
    assert p["sha256"].startswith("sha256:")

# == T-203: QSPI window > 16MB (framework trusts user input) ==

def test_qspi_window_inconsistency_accepted(tmp_path):
    """T-203 (B12-B03 erratum): qspi window > 16MB → no longer rejects."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T203", profile_overrides={
        "qspi_linear_window_bytes": 33554432,
    })
    p = board_profile_load("T203", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T203"
    assert p["sha256"].startswith("sha256:")

# == T-204: LED count vs XDC (framework trusts user input) ==

def test_led_count_xdc_mismatch_accepted(tmp_path):
    """T-204 (B12-B03 erratum): profile LED count vs XDC → no longer rejects."""
    _clear_cache()
    xdc_3 = (
        "set_property PACKAGE_PIN U18 [get_ports sys_clk]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]\n"
        "create_clock -period 20.000 -name pl_clk [get_ports sys_clk]\n"
        "set_property PACKAGE_PIN J16 [get_ports {led_pins[3]}]\n"
        "set_property PACKAGE_PIN K16 [get_ports {led_pins[2]}]\n"
        "set_property PACKAGE_PIN M15 [get_ports {led_pins[1]}]\n"
    )
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T204", xdc_content=xdc_3)
    p = board_profile_load("T204", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T204"
    assert p["sha256"].startswith("sha256:")

# == T-205: Clock freq vs XDC (framework trusts user input) ==

def test_clock_freq_xdc_mismatch_accepted(tmp_path):
    """T-205 (B12-B03 erratum): profile clock vs XDC → no longer rejects."""
    _clear_cache()
    xdc_100mhz = _dfl_xdc.replace("-period 20.000", "-period 10.000")
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T205", xdc_content=xdc_100mhz)
    p = board_profile_load("T205", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T205"
    assert p["sha256"].startswith("sha256:")

# == T-206: Version unsupported vs mismatch ==

def test_env_version_unsupported(tmp_path):
    """Install dir=2019.1, command output=2019.1 → ENV_VERSION_UNSUPPORTED."""
    from mcps.common.env_probe import probe_vivado
    root = str(tmp_path)
    bd = os.path.join(root, "Vivado", "2019.1", "bin")
    os.makedirs(bd, exist_ok=True)
    with open(os.path.join(bd, "vivado.bat"), "w") as f:
        f.write("@echo fake")
    r = probe_vivado(search_roots=[root],
                     runner=lambda _a, _t: ("SW Build 123\n__VERSION=2019.1\n", "", 0))
    assert r.found is True
    assert r.supported is False
    assert r.error_code == "ENV_ERROR"
    assert r.reason_code == "ENV_VERSION_UNSUPPORTED"

def test_env_version_mismatch(tmp_path):
    """Install dir=2023.1, command output=2019.1 → ENV_VERSION_MISMATCH."""
    from mcps.common.env_probe import probe_vivado
    root = str(tmp_path)
    bd = os.path.join(root, "Vivado", "2023.1", "bin")
    os.makedirs(bd, exist_ok=True)
    with open(os.path.join(bd, "vivado.bat"), "w") as f:
        f.write("@echo fake")
    r = probe_vivado(search_roots=[root],
                     runner=lambda _a, _t: ("__VERSION=2019.1\n", "", 0))
    assert r.found is True
    assert r.supported is False
    assert r.reason_code == "ENV_VERSION_MISMATCH"
    assert r.error_code == "ENV_ERROR"

# == T-207: Vivado not found (COVERED in test_env_probe.py) ==

# == T-208: Vitis not found (COVERED in test_env_probe.py) ==

# == T-209: XSCT not found (COVERED in test_env_probe.py) ==

# == T-210: Profile absolute path (SHA-consistent) ==

def test_profile_absolute_path_rejected(tmp_path):
    """Profile contains C:\\Users\\... → CONTEXT_INVALID + ABSOLUTE_PATH_FORBIDDEN."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T210", profile_overrides={
        "extra_path": r"C:\Users\zdx86\Xilinx\Vivado\2023.1",
    })
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T210", search_dirs=[pkg], allow_draft=True)
    assert e.value.code == "CONTEXT_INVALID"
    assert e.value.reason_code == "ABSOLUTE_PATH_FORBIDDEN"

# == T-211: Manifest files path (COVERED in test_board_package.py) ==

# == T-212: Manifest self-reference (COVERED in test_board_package.py) ==

# == T-213: expected_package_revision public contract ==

def test_expected_revision_valid_match(tmp_path):
    """expected_package_revision matches actual → success."""
    _clear_cache()
    pkg, _, _, rev = _fresh_pkg(tmp_path, "T213A")
    p = board_profile_load("T213A", search_dirs=[pkg], allow_draft=True,
                           expected_package_revision=rev)
    assert p["package_revision"] == rev

def test_expected_revision_mismatch(tmp_path):
    """expected_package_revision != actual → ARTIFACT_STALE + PACKAGE_REVISION_MISMATCH."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T213B")
    wrong_rev = "sha256:" + "ff" * 32
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T213B", search_dirs=[pkg], allow_draft=True,
                           expected_package_revision=wrong_rev)
    assert e.value.code == "ARTIFACT_STALE"
    assert e.value.reason_code == "PACKAGE_REVISION_MISMATCH"

def test_expected_revision_invalid_format(tmp_path):
    """Invalid expected_package_revision → INVALID_ARGUMENT + INVALID_SHA256."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T213C")
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T213C", search_dirs=[pkg], allow_draft=True,
                           expected_package_revision="not-a-sha")
    assert e.value.code == "INVALID_ARGUMENT"
    assert e.value.reason_code == "INVALID_SHA256"

# == Extra drift tests (B12-B03 erratum: drift no longer rejects) ==

def test_sources_md_tamper_accepted(tmp_path):
    """B12-B03 erratum: SOURCES.md changed → load succeeds (validated at use point)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_SRC")
    board_profile_load("T_SRC", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "SOURCES.md"), "a") as f:
        f.write("\n# extra")
    p = board_profile_load("T_SRC", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_SRC"

def test_readme_md_tamper_accepted(tmp_path):
    """B12-B03 erratum: README.md changed → load succeeds (validated at use point)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_RDM")
    board_profile_load("T_RDM", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "README.md"), "a") as f:
        f.write("\n# extra")
    p = board_profile_load("T_RDM", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RDM"

def test_manifest_revision_wrong_value_accepted(tmp_path):
    """B12-B03 erratum: manifest_revision drift → load succeeds (revision read as evidence)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_BR")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["manifest_revision"] = "sha256:" + "ff" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_BR", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_BR"

def test_preset_sha_field_wrong_accepted(tmp_path):
    """B12-B03 erratum: profile.ps7_preset_sha256 drift → load succeeds."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_PPS")
    pp = os.path.join(pkg, "board_profile_T_PPS.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    prof["ps7_preset_sha256"] = "sha256:" + "ff" * 32
    with open(pp, "w") as f:
        json.dump(prof, f)
    p = board_profile_load("T_PPS", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_PPS"

def test_xdc_sha_field_wrong_accepted(tmp_path):
    """B12-B03 erratum: profile.xdc_sha256 drift → load succeeds."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_XDC")
    pp = os.path.join(pkg, "board_profile_T_XDC.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    prof["xdc_sha256"] = "sha256:" + "ee" * 32
    with open(pp, "w") as f:
        json.dump(prof, f)
    p = board_profile_load("T_XDC", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_XDC"

def test_revision_inputs_sha_wrong_accepted(tmp_path):
    """B12-B03 erratum: revision_inputs drift → load succeeds (SHA table → doc level)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_RIB")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["revision_inputs"]["board_profile_sha256"] = "sha256:" + "ff" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_RIB", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RIB"

def test_files_sha_wrong_in_manifest_accepted(tmp_path):
    """B12-B03 erratum: files[].sha256 drift → load succeeds."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_FSW")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    for entry in m["files"]:
        if entry.get("path") == "board.xdc":
            entry["sha256"] = "sha256:" + "ff" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_FSW", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_FSW"

def test_profile_extra_file_on_disk_accepted(tmp_path):
    """B12-B03 erratum: extra file in directory → load succeeds (directory seal retired)."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_EFD")
    board_profile_load("T_EFD", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "stowaway.txt"), "w") as f:
        f.write("extra")
    p = board_profile_load("T_EFD", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_EFD"
    assert p["sha256"].startswith("sha256:")

def test_manifest_invalid_json(tmp_path):
    """Corrupt manifest JSON → INVALID_JSON."""
    _clear_cache()
    pkg, _, _, _ = _fresh_pkg(tmp_path, "T_MIJ")
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        f.write("{ corrupt json !!!")
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_MIJ", search_dirs=[pkg], allow_draft=True)
    assert e.value.code == "CONTEXT_INVALID"
    assert e.value.reason_code == "INVALID_JSON"

def test_env_vivado_not_found():
    """Vivado not found → ENV_ERROR + ENV_VIVADO_NOT_FOUND."""
    from mcps.common.env_probe import probe_vivado
    r = probe_vivado(search_roots=[])
    assert r.found is False
    assert r.error_code == "ENV_ERROR"
    assert r.reason_code == "ENV_VIVADO_NOT_FOUND"

def test_recovery_after_tamper(tmp_path):
    """B12-B03 erratum: tamper no longer rejects — load always succeeds and records current sha."""
    _clear_cache()
    pkg = os.path.join(str(tmp_path), "T_REC")
    os.makedirs(pkg, exist_ok=True)
    _write_content_files(pkg, "T_REC")
    _seal_package(pkg, "T_REC")
    p1 = board_profile_load("T_REC", search_dirs=[pkg], allow_draft=True)
    assert p1["board_id"] == "T_REC"
    with open(os.path.join(pkg, "board.xdc"), "a") as f:
        f.write("\n# tampered")
    p2 = board_profile_load("T_REC", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_REC"
