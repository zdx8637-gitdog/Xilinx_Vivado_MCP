"""B03-T-1xx: Board Profile loading — fail-closed tests."""

import json, os, pytest
from pathlib import Path
from mcps.common.board_profile import board_profile_load, BoardProfileError, _cache

PKG_DIR = str(Path(__file__).resolve().parents[3] / "boards" / "ALINX_AX7020_v1.0")
FIXTURE_DIR = str(Path(__file__).resolve().parent / "fixtures")

def _clear_cache(): _cache.clear()


# ══════════════════════════════════════════════════════════════════════ T-101
def test_load_real_profile_allow_draft():
    _clear_cache()
    p = board_profile_load("ALINX_AX7020_v1.0", allow_draft=True)
    assert p["board_id"] == "ALINX_AX7020_v1.0"
    assert p["sha256"].startswith("sha256:")
    assert p["fixture_only"] is False

# ══════════════════════════════════════════════════════════════════════ T-102
def test_board_id_mismatch_rejected(tmp_path):
    _clear_cache()
    d = str(tmp_path)
    with open(os.path.join(d, "board_profile_TEST_102.json"), "w") as f:
        json.dump({"board_id": "DIFFERENT_ID", "fixture_only": True}, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("TEST_102", search_dirs=[d])
    assert e.value.code == "CONTEXT_INVALID"

# ══════════════════════════════════════════════════════════════════════ T-103a
def test_prof_sha_changes_on_modify(tmp_path):
    _clear_cache()
    d = str(tmp_path)
    prof = {"board_id": "T103A", "fixture_only": True}
    prof_path = os.path.join(d, "board_profile_T103A.json")
    with open(prof_path, "w") as f:
        json.dump(prof, f)
    p1 = board_profile_load("T103A", search_dirs=[d])
    sha1 = p1["sha256"]
    prof["board_id"] = "T103A"  # unchanged
    prof["extra"] = "modified"
    with open(prof_path, "w") as f:
        json.dump(prof, f)
    p2 = board_profile_load("T103A", search_dirs=[d])
    assert sha1 != p2["sha256"]

# ══════════════════════════════════════════════════════════════════════ T-104 T-105 T-108
def test_ps7_preset_missing_accepted(tmp_path):
    """T-104 (B12-B03 erratum): ps7_preset missing → validated at use point, not at load."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T104")
    os.unlink(os.path.join(pkg, "ps7_preset.tcl"))
    p = board_profile_load("T104", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T104"
    assert p["sha256"].startswith("sha256:")

def test_board_xdc_missing_accepted(tmp_path):
    """T-105 (B12-B03 erratum): board.xdc missing → validated at use point, not at load."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T105")
    os.unlink(os.path.join(pkg, "board.xdc"))
    p = board_profile_load("T105", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T105"
    assert p["sha256"].startswith("sha256:")

def test_missing_required_field_rejected(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T108")
    pp = os.path.join(pkg, "board_profile_T108.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    del prof["ddr_physical_bytes"]
    with open(pp, "w") as f:
        json.dump(prof, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T108", search_dirs=[pkg], allow_draft=True)
    assert e.value.reason_code == "MISSING_REQUIRED_FIELD"

# ══════════════════════════════════════════════════════════════════════ T-109
def test_malformed_json_rejected(tmp_path):
    _clear_cache()
    d = str(tmp_path)
    with open(os.path.join(d, "board_profile_T109B.json"), "w") as f:
        f.write("{ bad json {{{")
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T109B", search_dirs=[d])
    assert e.value.reason_code == "INVALID_JSON"

# ══════════════════════════════════════════════════════════════════════ T-110
def test_locked_loads_without_allow_draft():
    _clear_cache()
    p = board_profile_load("ALINX_AX7020_v1.0")
    assert p["package_status"] == "locked"
    assert p["package_revision"].startswith("sha256:")

def test_draft_rejected_without_allow_draft(tmp_path):
    """Default load rejects draft-only package. Test uses synthetic draft."""
    _clear_cache()
    from mcps.common.revision import sha256_file
    pkg = os.path.join(str(tmp_path), "T110_DRAFT")
    os.makedirs(pkg, exist_ok=True)
    prof = {"board_id": "T110_DRAFT", "fixture_only": False,
            "vendor": "TEST", "model": "TEST",
            "part": "xc7z020clg400-2", "vivado_part": "xc7z020clg400-2",
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
            "ps7_preset_sha256": "sha256:" + "ff" * 32,
            "xdc_sha256": "sha256:" + "ee" * 32,
            "source_catalog": [{"source_id": "TEST", "distribution_path": "path/file.tcl",
                                "sha256": "sha256:" + "ab" * 32, "role": "test"}]}
    prof_path = os.path.join(pkg, "board_profile_T110_DRAFT.json")
    with open(prof_path, "w") as f:
        json.dump(prof, f)
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        json.dump({"schema_version": "1.0", "manifest_type": "board_configuration",
                   "board_id": "T110_DRAFT", "package_version": "1.0",
                   "status": "draft", "manifest_revision": "sha256:ff" + "ff" * 31,
                   "revision_inputs": {"board_profile_sha256": "sha256:" + "ff" * 32,
                       "ps7_preset_sha256": "sha256:" + "ff" * 32,
                       "board_xdc_sha256": "sha256:" + "ff" * 32,
                       "sources_md_sha256": "sha256:" + "ff" * 32,
                       "readme_md_sha256": "sha256:" + "ff" * 32},
                   "generated_at": "2026-08-04T12:00:00+08:00",
                   "files": [{"path": "board_profile_T110_DRAFT.json",
                              "sha256": "sha256:" + "ff" * 32, "role": "primary"},
                             {"path": "ps7_preset.tcl",
                              "sha256": "sha256:" + "ff" * 32, "role": "preset"},
                             {"path": "board.xdc",
                              "sha256": "sha256:" + "ff" * 32, "role": "xdc"},
                             {"path": "SOURCES.md",
                              "sha256": "sha256:" + "ff" * 32, "role": "sources"},
                             {"path": "README.md",
                              "sha256": "sha256:" + "ff" * 32, "role": "readme"}]}, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T110_DRAFT", search_dirs=[pkg])
    assert e.value.reason_code == "PACKAGE_NOT_LOCKED"

# ══════════════════════════════════════════════════════════════════════ Fixture isolation
def test_fixture_still_loads():
    _clear_cache()
    p = board_profile_load("TEST_AX7020_MINIMAL", search_dirs=[FIXTURE_DIR])
    assert p["fixture_only"] is True

def test_fixture_not_in_production_default(monkeypatch):
    _clear_cache()
    monkeypatch.delenv("ZYNQ_BOARD_PROFILE_DIRS", raising=False)
    with pytest.raises((FileNotFoundError, BoardProfileError)):
        board_profile_load("TEST_AX7020_MINIMAL")

# ══════════════════════════════════════════════════════════════════════ No auto-fixture upgrade
def test_no_manifest_no_fixture_rejected(tmp_path):
    """Non-fixture profile in non-prod dir without manifest → MISSING_MANIFEST."""
    _clear_cache()
    pkg = os.path.join(str(tmp_path), "T_NOFIX")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "board_profile_T_NOFIX.json"), "w") as f:
        json.dump({"board_id": "T_NOFIX", "fixture_only": False}, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_NOFIX", search_dirs=[pkg])
    assert e.value.reason_code == "MISSING_MANIFEST"

def test_fixture_missing_field_no_manifest_rejected(tmp_path):
    """fixture_only field absent, no manifest → MISSING_MANIFEST."""
    _clear_cache()
    pkg = os.path.join(str(tmp_path), "T_NOFO")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "board_profile_T_NOFO.json"), "w") as f:
        json.dump({"board_id": "T_NOFO"}, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_NOFO", search_dirs=[pkg])
    assert e.value.reason_code == "MISSING_MANIFEST"

def test_fixture_string_not_bool_rejected(tmp_path):
    """fixture_only='true' string, no manifest → MISSING_MANIFEST."""
    _clear_cache()
    pkg = os.path.join(str(tmp_path), "T_NOFS")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "board_profile_T_NOFS.json"), "w") as f:
        json.dump({"board_id": "T_NOFS", "fixture_only": "true"}, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_NOFS", search_dirs=[pkg])
    assert e.value.reason_code == "MISSING_MANIFEST"

def test_fixture_true_explicit_dir_accepted(tmp_path):
    """fixture_only=true, explicit test dir → accepted."""
    _clear_cache()
    pkg = os.path.join(str(tmp_path), "T_FIXOK")
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "board_profile_T_FIXOK.json"), "w") as f:
        json.dump({"board_id": "T_FIXOK", "fixture_only": True}, f)
    p = board_profile_load("T_FIXOK", search_dirs=[pkg])
    assert p["fixture_only"] is True

# ══════════════════════════════════════════════════════════════════════ Manifest integrity
def test_locked_and_draft_both_rejected(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_BOTH")
    import shutil
    shutil.copy(os.path.join(pkg, "package_manifest.draft.json"),
                os.path.join(pkg, "package_manifest.json"))
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_BOTH", search_dirs=[pkg])
    assert e.value.reason_code == "PACKAGE_STATE_CONFLICT"

def test_locked_file_status_draft_rejected(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_LSD")
    os.rename(os.path.join(pkg, "package_manifest.draft.json"),
              os.path.join(pkg, "package_manifest.json"))
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_LSD", search_dirs=[pkg])
    assert e.value.reason_code == "PACKAGE_STATE_CONFLICT"

def test_draft_file_status_locked_rejected(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_DSL")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["status"] = "locked"
    with open(mp, "w") as f:
        json.dump(m, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_DSL", search_dirs=[pkg], allow_draft=True)
    assert e.value.reason_code == "PACKAGE_STATE_CONFLICT"

def test_manifest_board_id_mismatch(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_BIDM")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["board_id"] = "WRONG"
    with open(mp, "w") as f:
        json.dump(m, f)
    with pytest.raises(BoardProfileError):
        board_profile_load("T_BIDM", search_dirs=[pkg], allow_draft=True)

def test_files_missing_entry_accepted(tmp_path):
    """B12-B03 erratum: manifest missing a file entry → no longer rejects (directory seal retired)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_FME")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["files"] = [e for e in m["files"] if e.get("path") != "board.xdc"]
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_FME", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_FME"
    assert p["sha256"].startswith("sha256:")

def test_files_extra_entry_accepted(tmp_path):
    """B12-B03 erratum: manifest extra file entry → no longer rejects (directory seal retired)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_FEE")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["files"].append({"path": "bonus.txt", "sha256": "sha256:" + "ff" * 32, "role": "extra"})
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_FEE", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_FEE"
    assert p["sha256"].startswith("sha256:")

def test_files_sha_vs_revision_inputs_mismatch_accepted(tmp_path):
    """B12-B03 erratum: SHA cross-ref drift → no longer rejects (freeze discipline → doc level)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_FSRI")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["revision_inputs"]["board_xdc_sha256"] = "sha256:" + "de" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_FSRI", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_FSRI"
    assert p["sha256"].startswith("sha256:")

def test_revision_inputs_vs_disk_mismatch_accepted(tmp_path):
    """B12-B03 erratum: tampered board.xdc → no longer rejects (validated at use point)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_RID")
    with open(os.path.join(pkg, "board.xdc"), "a") as f:
        f.write("\n# tamper")
    p = board_profile_load("T_RID", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RID"
    assert p["sha256"].startswith("sha256:")

def test_profile_preset_sha_vs_disk_mismatch_accepted(tmp_path):
    """B12-B03 erratum: tampered ps7_preset → no longer rejects (validated at use point)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_PPS")
    with open(os.path.join(pkg, "ps7_preset.tcl"), "w") as f:
        f.write("# tampered")
    p = board_profile_load("T_PPS", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_PPS"
    assert p["sha256"].startswith("sha256:")

# ══════════════════════════════════════════════════════════════════════ Cache invalidation
def test_cache_invalidates_on_preset_change(tmp_path):
    """B12-B03 erratum: ps7_preset change invalidates cache but no longer rejects."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CIPC")
    p = board_profile_load("T_CIPC", search_dirs=[pkg], allow_draft=True)
    rev_before = p["package_revision"]
    with open(os.path.join(pkg, "ps7_preset.tcl"), "a") as f:
        f.write("\n# change")
    p2 = board_profile_load("T_CIPC", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_CIPC"
    assert p2["package_revision"] == rev_before

def test_cache_invalidates_on_xdc_change(tmp_path):
    """B12-B03 erratum: board.xdc change invalidates cache but no longer rejects."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CIXC")
    board_profile_load("T_CIXC", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "board.xdc"), "a") as f:
        f.write("\n# change")
    p2 = board_profile_load("T_CIXC", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_CIXC"

def test_cache_invalidates_on_manifest_change(tmp_path):
    """B12-B03 erratum: manifest_revision change no longer rejects (evidence read as-is)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CIMC")
    board_profile_load("T_CIMC", search_dirs=[pkg], allow_draft=True)
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["manifest_revision"] = "sha256:" + "ff" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p2 = board_profile_load("T_CIMC", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_CIMC"

def test_cache_invalidates_on_extra_file(tmp_path):
    """B12-B03 erratum: extra file in directory no longer rejects (directory seal retired)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CIEF")
    board_profile_load("T_CIEF", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "extra.txt"), "w") as f:
        f.write("stowaway")
    p2 = board_profile_load("T_CIEF", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_CIEF"
    assert p2["sha256"].startswith("sha256:")

def test_cache_invalidates_on_second_profile(tmp_path):
    """B12-B03 erratum: second board_profile_*.json no longer rejects (directory seal retired)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CISP")
    board_profile_load("T_CISP", search_dirs=[pkg], allow_draft=True)
    with open(os.path.join(pkg, "board_profile_EXTRA.json"), "w") as f:
        json.dump({"board_id": "EXTRA"}, f)
    p2 = board_profile_load("T_CISP", search_dirs=[pkg], allow_draft=True)
    assert p2["board_id"] == "T_CISP"

def test_cache_invalidates_on_locked_draft_conflict(tmp_path):
    """locked+draft appearing after cache → PACKAGE_STATE_CONFLICT."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_CILDC")
    board_profile_load("T_CILDC", search_dirs=[pkg], allow_draft=True)
    import shutil
    shutil.copy(os.path.join(pkg, "package_manifest.draft.json"),
                os.path.join(pkg, "package_manifest.json"))
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_CILDC", search_dirs=[pkg], allow_draft=True)
    assert e.value.reason_code == "PACKAGE_STATE_CONFLICT"

# ══════════════════════════════════════════════════════════════════════ Path security (manifest files paths → runtime no longer enforced)
def test_path_backslash_accepted(tmp_path):
    _assert_bad_path_accepted(tmp_path, "T_BS", "sub\\bad.txt")
def test_path_absolute_drive_accepted(tmp_path):
    _assert_bad_path_accepted(tmp_path, "T_AD", "C:/bad.txt")
def test_path_drive_relative_accepted(tmp_path):
    _assert_bad_path_accepted(tmp_path, "T_DR", "C:bad.txt")
def test_path_dotdot_accepted(tmp_path):
    _assert_bad_path_accepted(tmp_path, "T_DD", "../bad.txt")
def test_path_duplicate_accepted(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_DUP")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["files"].append({"path": "ps7_preset.tcl", "sha256": m["files"][1]["sha256"], "role": "dup"})
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_DUP", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_DUP"

# ══════════════════════════════════════════════════════════════════════ Manifest INVALID_JSON
def test_manifest_invalid_json_rejected(tmp_path):
    """Manifest exists but has malformed JSON → INVALID_JSON (not PACKAGE_STATE_CONFLICT)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_MIJ")
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        f.write("{ not json {{{")
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_MIJ", search_dirs=[pkg], allow_draft=True)
    assert e.value.reason_code == "INVALID_JSON"

# ══════════════════════════════════════════════════════════════════════ Reason code propagation (retired → now acceptance)
def test_preset_sha_tamper_accepted(tmp_path):
    """B12-B03 erratum: tampered ps7_preset → load succeeds (use point validates)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_RCPS")
    with open(os.path.join(pkg, "ps7_preset.tcl"), "w") as f:
        f.write("# tampered")
    p = board_profile_load("T_RCPS", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RCPS"

def test_missing_field_reason_code(tmp_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_RCMF")
    pp = os.path.join(pkg, "board_profile_T_RCMF.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    del prof["ddr_physical_bytes"]
    with open(pp, "w") as f:
        json.dump(prof, f)
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T_RCMF", search_dirs=[pkg], allow_draft=True)
    assert e.value.reason_code == "MISSING_REQUIRED_FIELD"

def test_extra_file_manifest_entry_accepted(tmp_path):
    """B12-B03 erratum: extra manifest file entry → load succeeds (directory seal retired)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_RCEF")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["files"].append({"path": "extra.txt", "sha256": "sha256:" + "ff" * 32, "role": "extra"})
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_RCEF", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RCEF"

def test_bad_revision_accepted(tmp_path):
    """B12-B03 erratum: manifest_revision drift → load succeeds (revision read as evidence)."""
    _clear_cache()
    pkg = _make_pkg(tmp_path, "T_RCBR")
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["manifest_revision"] = "sha256:" + "ff" * 32
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load("T_RCBR", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T_RCBR"


# ══════════════════════════════════════════════════════════════════════ Helpers
def _assert_bad_path_accepted(tmp_path, board_id, bad_path):
    _clear_cache()
    pkg = _make_pkg(tmp_path, board_id)
    mp = os.path.join(pkg, "package_manifest.draft.json")
    with open(mp, "r") as f:
        m = json.load(f)
    m["files"][1]["path"] = bad_path
    with open(mp, "w") as f:
        json.dump(m, f)
    p = board_profile_load(board_id, search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == board_id


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


_DFL_XDC = (
    "set_property PACKAGE_PIN U18 [get_ports sys_clk]\n"
    "set_property IOSTANDARD LVCMOS33 [get_ports sys_clk]\n"
    "create_clock -period 20.000 -name pl_clk [get_ports sys_clk]\n"
    "set_property PACKAGE_PIN J16 [get_ports {led_pins[3]}]\n"
    "set_property PACKAGE_PIN K16 [get_ports {led_pins[2]}]\n"
    "set_property PACKAGE_PIN M15 [get_ports {led_pins[1]}]\n"
    "set_property PACKAGE_PIN M14 [get_ports {led_pins[0]}]\n"
    "set_property IOSTANDARD LVCMOS33 [get_ports {led_pins[*]}]\n"
)

def _make_pkg(tmp_path, board_id):
    from mcps.common.revision import sha256_file, compute_revision
    pkg = os.path.join(str(tmp_path), board_id)
    os.makedirs(pkg, exist_ok=True)
    for fn, content in [("ps7_preset.tcl", "# preset " + board_id),
                         ("board.xdc", _DFL_XDC),
                         ("SOURCES.md", "# sources " + board_id),
                         ("README.md", "# readme " + board_id)]:
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
