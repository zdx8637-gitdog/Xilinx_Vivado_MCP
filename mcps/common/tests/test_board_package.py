"""B03 board_package.py unit tests: T-103b, T-106, T-107, T-110."""

import json, os, pytest
from pathlib import Path
from mcps.common.board_package import (
    validate_package_manifest,
    validate_board_profile,
    compute_package_revision,
    _safe_resolve_path,
    find_manifest_status,
    ValidationIssue,
)
from mcps.common.board_profile import board_profile_load, BoardProfileError, _cache

PKG_DIR = str(Path(__file__).resolve().parents[3] / "boards" / "ALINX_AX7020_v1.0")

def _clear_cache(): _cache.clear()


# ══════════════════════════════════════════════════════════════════════ T-103b
def test_package_file_change_changes_revision(tmp_path):
    pkg = _mini_pkg(tmp_path, "T103B")
    rev1 = compute_package_revision(pkg)
    with open(os.path.join(pkg, "board.xdc"), "a") as f:
        f.write("\n# changed")
    rev2 = compute_package_revision(pkg)
    assert rev1 != rev2


# ══════════════════════════════════════════════════════════════════════ T-106 T-107
def test_preset_sha_mismatch_at_load(tmp_path):
    _clear_cache()
    pkg = _make_draft_pkg(tmp_path, "T106")
    with open(os.path.join(pkg, "ps7_preset.tcl"), "w") as f:
        f.write("# tampered")
    with pytest.raises(BoardProfileError):
        board_profile_load("T106", search_dirs=[pkg], allow_draft=True)

def test_xdc_sha_mismatch_at_load(tmp_path):
    _clear_cache()
    pkg = _make_draft_pkg(tmp_path, "T107")
    with open(os.path.join(pkg, "board.xdc"), "w") as f:
        f.write("# tampered")
    with pytest.raises(BoardProfileError):
        board_profile_load("T107", search_dirs=[pkg], allow_draft=True)


# ══════════════════════════════════════════════════════════════════════ T-110
def test_default_rejects_draft(tmp_path):
    _clear_cache()
    pkg = _make_draft_pkg(tmp_path, "T110A")
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T110A", search_dirs=[pkg])
    assert e.value.reason_code == "PACKAGE_NOT_LOCKED"

def test_allow_draft_succeeds(tmp_path):
    _clear_cache()
    pkg = _make_draft_pkg(tmp_path, "T110B")
    p = board_profile_load("T110B", search_dirs=[pkg], allow_draft=True)
    assert p["package_status"] == "draft"


# ══════════════════════════════════════════════════════════════════════ Manifest schema
def test_validate_valid_manifest():
    m = _valid_manifest("TEST_V")
    assert len(validate_package_manifest(m)) == 0

def test_validate_missing_field():
    issues = validate_package_manifest({"manifest_type": "board_configuration"})
    assert any(i.code == "MISSING_FIELD" for i in issues)

def test_validate_bad_revision():
    m = _valid_manifest("TEST_BR")
    m["manifest_revision"] = "sha256:" + "ff" * 32
    assert any(i.code == "BAD_REVISION" for i in validate_package_manifest(m))

def test_validate_self_reference():
    m = _valid_manifest("TEST_SR")
    m["files"] = [{"path": "package_manifest.json", "sha256": "sha256:" + "ff" * 32, "role": "self"}]
    assert any(i.code == "MANIFEST_SELF_REFERENCE" for i in validate_package_manifest(m))

def test_manifest_path_backslash():
    m = _valid_manifest("TEST_BS")
    m["files"][0]["path"] = "sub\\file.json"
    assert any(i.code == "ABSOLUTE_PATH_FORBIDDEN" for i in validate_package_manifest(m))

def test_manifest_path_absolute_drive():
    m = _valid_manifest("TEST_AD")
    m["files"][0]["path"] = "C:/Windows/evil.json"
    assert any(i.code == "ABSOLUTE_PATH_FORBIDDEN" for i in validate_package_manifest(m))

def test_manifest_path_dotdot():
    m = _valid_manifest("TEST_DD")
    m["files"][0]["path"] = "../etc/passwd"
    assert any(i.code == "ABSOLUTE_PATH_FORBIDDEN" for i in validate_package_manifest(m))


# ══════════════════════════════════════════════════════════════════════ Profile schema — generic
def test_profile_missing_board_id():
    issues = validate_board_profile({"fixture_only": True}, is_fixture=True)
    assert any(i.code == "MISSING_REQUIRED_FIELD" and i.field == "board_id" for i in issues)

def test_profile_bool_for_int():
    p = _min_prof("T_BI")
    p["ddr_physical_bytes"] = True
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_TYPE" and i.field == "ddr_physical_bytes" for i in issues)

def test_profile_wrong_led_count():
    """count=3 but pins.length=4 → INVALID_TYPE on pins (length != count)."""
    p = _min_prof("T_WLC")
    p["pl_leds"]["count"] = 3  # pins still has 4
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_TYPE" and i.field == "pl_leds.pins" for i in issues)

def test_profile_wrong_ps_mio():
    p = _min_prof("T_WPM")
    p["ps_leds"]["mio_pins"] = [7]  # count=2, len=1
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_TYPE" and i.field == "ps_leds.mio_pins" for i in issues)

def test_profile_bad_polarity():
    p = _min_prof("T_BP")
    p["pl_leds"]["polarity"] = "bogus"
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_TYPE" and i.field == "pl_leds.polarity" for i in issues)

def test_profile_bad_sha_format():
    p = _min_prof("T_BSHA")
    p["ps7_preset_sha256"] = "not-a-hash"
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_SHA256" and i.field == "ps7_preset_sha256" for i in issues)

def test_profile_valid():
    p = _min_prof("T_OK")
    p["ps7_preset_sha256"] = "sha256:" + "aa" * 32
    p["xdc_sha256"] = "sha256:" + "bb" * 32
    issues = validate_board_profile(p)
    assert len(issues) == 0

def test_profile_duplicate_mio_pins():
    p = _min_prof("T_DMIO")
    p["ps_leds"]["mio_pins"] = [0, 0]
    issues = validate_board_profile(p)
    assert any("DUPLICATE" in i.code and i.field == "ps_leds.mio_pins" for i in issues)

def test_profile_duplicate_led_pins():
    p = _min_prof("T_DLED")
    p["pl_leds"]["pins"] = ["J16", "J16", "M15", "M14"]
    issues = validate_board_profile(p)
    assert any("DUPLICATE" in i.code and i.field == "pl_leds.pins" for i in issues)

def test_profile_empty_source_catalog():
    p = _min_prof("T_ESC")
    p["source_catalog"] = []
    issues = validate_board_profile(p)
    assert any(i.code == "INVALID_TYPE" and i.field == "source_catalog" for i in issues)

def test_profile_missing_usb_bridge():
    p = _min_prof("T_MUB")
    del p["usb_bridge"]
    issues = validate_board_profile(p)
    assert any(i.code == "MISSING_REQUIRED_FIELD" and i.field == "usb_bridge" for i in issues)

def test_profile_missing_pl_resources():
    p = _min_prof("T_MPR")
    del p["pl_resources"]
    issues = validate_board_profile(p)
    assert any(i.code == "MISSING_REQUIRED_FIELD" and i.field == "pl_resources" for i in issues)

def test_profile_source_catalog_abs_path():
    p = _min_prof("T_SCA")
    p["source_catalog"][0]["distribution_path"] = "C:/Users/evil/file.tcl"
    issues = validate_board_profile(p)
    assert any(i.code == "ABSOLUTE_PATH_FORBIDDEN" for i in issues)


# ══════════════════════════════════════════════════════════════════════ manifest JSON error
def test_manifest_invalid_json():
    """find_manifest_status returns INVALID_JSON, not PACKAGE_STATE_CONFLICT."""
    pkg = os.path.join(PKG_DIR, "..", "__test_mij")
    os.makedirs(pkg, exist_ok=True)
    try:
        with open(os.path.join(pkg, "package_manifest.json"), "w") as f:
            f.write("{ not json")
        name, status, reason = find_manifest_status(pkg)
        assert reason == "INVALID_JSON", f"Expected INVALID_JSON, got {reason}"
    finally:
        import shutil
        shutil.rmtree(pkg, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════ Backward compat
def test_error_old_style():
    e = BoardProfileError("msg")
    assert e.code == "CONTEXT_INVALID"
    assert e.reason_code is None
def test_error_new_style():
    e = BoardProfileError("msg", reason_code="PACKAGE_NOT_LOCKED")
    assert e.reason_code == "PACKAGE_NOT_LOCKED"
def test_canonical_ps7_preset_sha256():
    from mcps.common.revision import sha256_file
    a = sha256_file(os.path.join(PKG_DIR, "ps7_preset.tcl"))
    assert a == "sha256:142221866c21ea74b7d5040e3c7cae5bdc166498cd9daffe994648ca737b3299"


# ══════════════════════════════════════════════════════════════════════ _safe_resolve_path
def test_safe_resolve_ok():
    assert _safe_resolve_path("/tmp/pkg", "file.txt", "t") == os.path.abspath("/tmp/pkg/file.txt")
def test_safe_resolve_backslash():
    with pytest.raises(ValidationIssue, match="ABSOLUTE_PATH_FORBIDDEN"):
        _safe_resolve_path("/tmp/pkg", "sub\\file.txt", "t")
def test_safe_resolve_dotdot():
    with pytest.raises(ValidationIssue, match="ABSOLUTE_PATH_FORBIDDEN"):
        _safe_resolve_path("/tmp/pkg", "../etc/file.txt", "t")
def test_safe_resolve_absolute():
    with pytest.raises(ValidationIssue, match="ABSOLUTE_PATH_FORBIDDEN"):
        _safe_resolve_path("/tmp/pkg", "/etc/passwd", "t")
def test_safe_resolve_drive_absolute():
    with pytest.raises(ValidationIssue, match="ABSOLUTE_PATH_FORBIDDEN"):
        _safe_resolve_path("/tmp/pkg", "C:/bad.txt", "t")


# ══════════════════════════════════════════════════════════════════════ Helpers
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


def _valid_manifest(board_id):
    from mcps.common.revision import compute_revision
    ri = {"board_profile_sha256": "sha256:"+"aa"*32, "ps7_preset_sha256": "sha256:"+"bb"*32,
          "board_xdc_sha256": "sha256:"+"cc"*32, "sources_md_sha256": "sha256:"+"dd"*32,
          "readme_md_sha256": "sha256:"+"ee"*32}
    rev = compute_revision(ri)
    return {
        "schema_version": "1.0", "manifest_type": "board_configuration",
        "board_id": board_id, "package_version": "1.0", "status": "draft",
        "manifest_revision": rev, "revision_inputs": ri,
        "generated_at": "2026-08-04T12:00:00+08:00",
        "files": [
            {"path": f"board_profile_{board_id}.json", "sha256": "sha256:"+"aa"*32, "role": "primary"},
            {"path": "ps7_preset.tcl", "sha256": "sha256:"+"bb"*32, "role": "preset"},
            {"path": "board.xdc", "sha256": "sha256:"+"cc"*32, "role": "xdc"},
            {"path": "SOURCES.md", "sha256": "sha256:"+"dd"*32, "role": "sources"},
            {"path": "README.md", "sha256": "sha256:"+"ee"*32, "role": "readme"},
        ],
    }


def _mini_pkg(tmp_path, board_id):
    pkg = os.path.join(str(tmp_path), board_id)
    os.makedirs(pkg, exist_ok=True)
    prof = _min_prof(board_id)
    prof["ps7_preset_sha256"] = "sha256:"+"ff"*32
    prof["xdc_sha256"] = "sha256:"+"ee"*32
    with open(os.path.join(pkg, f"board_profile_{board_id}.json"), "w") as f:
        json.dump(prof, f)
    for fn in ["ps7_preset.tcl", "board.xdc", "SOURCES.md", "README.md"]:
        with open(os.path.join(pkg, fn), "w") as f:
            f.write(fn + " content")
    return pkg


def _make_draft_pkg(tmp_path, board_id):
    from mcps.common.revision import sha256_file, compute_revision
    pkg = os.path.join(str(tmp_path), board_id)
    os.makedirs(pkg, exist_ok=True)
    xdc_content = (
        "set_property PACKAGE_PIN U18 [get_ports sys_clk]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]\n"
        "create_clock -period 20.000 -name pl_clk [get_ports sys_clk]\n"
        "set_property PACKAGE_PIN J16 [get_ports {led_pins[3]}]\n"
        "set_property PACKAGE_PIN K16 [get_ports {led_pins[2]}]\n"
        "set_property PACKAGE_PIN M15 [get_ports {led_pins[1]}]\n"
        "set_property PACKAGE_PIN M14 [get_ports {led_pins[0]}]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports {led_pins[*]}]\n"
    )
    for fn, content in [("ps7_preset.tcl", "# preset"),
                         ("board.xdc", xdc_content),
                         ("SOURCES.md", "# sources"),
                         ("README.md", "# readme")]:
        with open(os.path.join(pkg, fn), "w") as f:
            f.write(content)
    preset_sha = sha256_file(os.path.join(pkg, "ps7_preset.tcl"))
    xdc_sha = sha256_file(os.path.join(pkg, "board.xdc"))
    s_sha = sha256_file(os.path.join(pkg, "SOURCES.md"))
    r_sha = sha256_file(os.path.join(pkg, "README.md"))
    prof = _min_prof(board_id)
    prof["ps7_preset_sha256"] = preset_sha
    prof["xdc_sha256"] = xdc_sha
    prof_path = os.path.join(pkg, f"board_profile_{board_id}.json")
    with open(prof_path, "w") as f:
        json.dump(prof, f)
    p_sha = sha256_file(prof_path)
    ri = {"board_profile_sha256": p_sha, "ps7_preset_sha256": preset_sha,
          "board_xdc_sha256": xdc_sha, "sources_md_sha256": s_sha,
          "readme_md_sha256": r_sha}
    rev = compute_revision(ri)
    manifest = {
        "schema_version": "1.0", "manifest_type": "board_configuration",
        "board_id": board_id, "package_version": "1.0",
        "status": "draft", "manifest_revision": rev,
        "revision_inputs": ri,
        "generated_at": "2026-08-04T12:00:00+08:00",
        "files": [
            {"path": f"board_profile_{board_id}.json", "sha256": p_sha, "role": "primary_data_source"},
            {"path": "ps7_preset.tcl", "sha256": preset_sha, "role": "ps7_hardware_preset"},
            {"path": "board.xdc", "sha256": xdc_sha, "role": "pl_pin_constraints"},
            {"path": "SOURCES.md", "sha256": s_sha, "role": "provenance_record"},
            {"path": "README.md", "sha256": r_sha, "role": "human_description"},
        ],
    }
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return pkg
