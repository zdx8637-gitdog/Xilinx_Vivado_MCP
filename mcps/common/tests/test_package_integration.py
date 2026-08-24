"""B03-T-4xx: Integration & freeze lifecycle tests on tmp_path copies."""

import json, os, sys, pytest, threading
from pathlib import Path

from mcps.common.board_profile import board_profile_load, BoardProfileError, _cache
from mcps.common.board_package import (
    freeze_package,
    FreezeCleanupError,
    compute_package_revision,
    find_manifest_status,
)
from mcps.common.artifact_schema import ManifestConflictError
from mcps.common.revision import sha256_file
from mcps.common.env_probe import probe_all

PKG_DIR = str(Path(__file__).resolve().parents[3] / "boards" / "ALINX_AX7020_v1.0")
FIXTURE_DIR = str(Path(__file__).resolve().parent / "fixtures")

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

def _seal_pkg(tmp_path, board_id):
    """Create a SHA-consistent draft package. Returns (pkg_dir, revision)."""
    from mcps.common.revision import compute_revision as _cr
    pkg = os.path.join(str(tmp_path), board_id)
    os.makedirs(pkg, exist_ok=True)
    for fn, content in [("ps7_preset.tcl", "# preset " + board_id),
                         ("board.xdc", _dfl_xdc),
                         ("SOURCES.md", "# sources " + board_id),
                         ("README.md", "# readme " + board_id)]:
        with open(os.path.join(pkg, fn), "w") as f:
            f.write(content)
    prof = _min_prof(board_id)
    prof["ps7_preset_sha256"] = sha256_file(os.path.join(pkg, "ps7_preset.tcl"))
    prof["xdc_sha256"] = sha256_file(os.path.join(pkg, "board.xdc"))
    with open(os.path.join(pkg, f"board_profile_{board_id}.json"), "w") as f:
        json.dump(prof, f)
    p_sha = sha256_file(os.path.join(pkg, f"board_profile_{board_id}.json"))
    ri = {"board_profile_sha256": p_sha,
          "ps7_preset_sha256": prof["ps7_preset_sha256"],
          "board_xdc_sha256": prof["xdc_sha256"],
          "sources_md_sha256": sha256_file(os.path.join(pkg, "SOURCES.md")),
          "readme_md_sha256": sha256_file(os.path.join(pkg, "README.md"))}
    rev = _cr(ri)
    manifest = {
        "schema_version": "1.0", "manifest_type": "board_configuration",
        "board_id": board_id, "package_version": "1.0",
        "status": "draft", "manifest_revision": rev,
        "revision_inputs": ri,
        "generated_at": "2026-08-04T12:00:00+08:00",
        "files": [
            {"path": f"board_profile_{board_id}.json", "sha256": p_sha, "role": "primary"},
            {"path": "ps7_preset.tcl", "sha256": prof["ps7_preset_sha256"], "role": "preset"},
            {"path": "board.xdc", "sha256": prof["xdc_sha256"], "role": "xdc"},
            {"path": "SOURCES.md", "sha256": ri["sources_md_sha256"], "role": "sources"},
            {"path": "README.md", "sha256": ri["readme_md_sha256"], "role": "readme"},
        ],
    }
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return pkg, rev


# -- T-401 --
def test_fresh_session_precheck(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T401")
    p = board_profile_load("T401", search_dirs=[pkg], allow_draft=True)
    assert p["board_id"] == "T401"
    report = probe_all(
        runner=lambda _a, _t: ("SW Build 0\n__VERSION=2023.1\n", "", 0),
        device_enumerator=lambda: ([], []),
    )
    assert report.vivado is not None


# -- T-402 freeze lifecycle: test 1 --
def test_freeze_draft_only_publishes(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T402_1")
    result = freeze_package(pkg)
    assert result == "published"
    assert not os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert os.path.isfile(os.path.join(pkg, "package_manifest.json"))
    assert len(os.listdir(pkg)) == 6

# -- test 2 --
def test_freeze_idempotent_locked_only(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T402_2")
    freeze_package(pkg)
    result = freeze_package(pkg)
    assert result == "already_exists_same"

# -- test 3: locked + same draft recovers --
def test_freeze_locked_same_draft_recovers(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T402_3")
    freeze_package(pkg)
    locked_path = os.path.join(pkg, "package_manifest.json")
    with open(locked_path, "r") as f:
        locked = json.load(f)
    draft = dict(locked)
    draft["status"] = "draft"
    draft_path = os.path.join(pkg, "package_manifest.draft.json")
    with open(draft_path, "w") as f:
        json.dump(draft, f, indent=2)
    result = freeze_package(pkg)
    assert result == "already_exists_same"
    assert not os.path.isfile(draft_path)

# -- test 4: locked + different draft → ManifestConflictError --
def test_freeze_locked_different_draft_conflict(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T402_4")
    freeze_package(pkg)
    with open(os.path.join(pkg, "board.xdc"), "a") as f:
        f.write("\n# changed")
    from mcps.common.revision import compute_revision as _cr
    prof = _min_prof("T402_4")
    prof["ps7_preset_sha256"] = sha256_file(os.path.join(pkg, "ps7_preset.tcl"))
    prof["xdc_sha256"] = sha256_file(os.path.join(pkg, "board.xdc"))
    with open(os.path.join(pkg, "board_profile_T402_4.json"), "w") as f:
        json.dump(prof, f)
    p_sha = sha256_file(os.path.join(pkg, "board_profile_T402_4.json"))
    ri = {"board_profile_sha256": p_sha,
          "ps7_preset_sha256": prof["ps7_preset_sha256"],
          "board_xdc_sha256": prof["xdc_sha256"],
          "sources_md_sha256": sha256_file(os.path.join(pkg, "SOURCES.md")),
          "readme_md_sha256": sha256_file(os.path.join(pkg, "README.md"))}
    rev2 = _cr(ri)
    new_draft = {
        "schema_version": "1.0", "manifest_type": "board_configuration",
        "board_id": "T402_4", "package_version": "1.0",
        "status": "draft", "manifest_revision": rev2,
        "revision_inputs": ri,
        "generated_at": "2026-08-04T12:00:00+08:00",
        "files": [
            {"path": "board_profile_T402_4.json", "sha256": p_sha, "role": "primary"},
            {"path": "ps7_preset.tcl", "sha256": prof["ps7_preset_sha256"], "role": "preset"},
            {"path": "board.xdc", "sha256": prof["xdc_sha256"], "role": "xdc"},
            {"path": "SOURCES.md", "sha256": ri["sources_md_sha256"], "role": "sources"},
            {"path": "README.md", "sha256": ri["readme_md_sha256"], "role": "readme"},
        ],
    }
    with open(os.path.join(pkg, "package_manifest.draft.json"), "w") as f:
        json.dump(new_draft, f, indent=2)
    with pytest.raises(ManifestConflictError):
        freeze_package(pkg)
    assert os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert os.path.isfile(os.path.join(pkg, "package_manifest.json"))

# -- test 5: publish failure --
def test_freeze_publish_failure_preserves_draft(tmp_path, monkeypatch):
    pkg, _ = _seal_pkg(tmp_path, "T402_5")
    called = False
    def fail_publish(*args, **kwargs):
        nonlocal called; called = True
        raise OSError("Simulated disk error")
    monkeypatch.setattr("mcps.common.artifact_schema.atomic_publish_no_replace", fail_publish)
    with pytest.raises(ValueError, match="Failed to publish"):
        freeze_package(pkg)
    assert called
    assert os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert not os.path.isfile(os.path.join(pkg, "package_manifest.json"))
    for fn in os.listdir(pkg):
        assert not fn.endswith(".tmp")

# -- test 6: draft deletion failure → FreezeCleanupError --
def test_freeze_draft_cleanup_failure(tmp_path, monkeypatch):
    pkg, _ = _seal_pkg(tmp_path, "T402_6")
    real_unlink = os.unlink
    import mcps.common.board_package as _bp
    def fail_unlink(path):
        if os.path.basename(path) == "package_manifest.draft.json":
            raise OSError("Permission denied")
        return real_unlink(path)
    monkeypatch.setattr(_bp.os, "unlink", fail_unlink)
    with pytest.raises(FreezeCleanupError):
        freeze_package(pkg)
    assert os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert os.path.isfile(os.path.join(pkg, "package_manifest.json"))
    with open(os.path.join(pkg, "package_manifest.json"), "r") as f:
        lm = json.load(f)
    assert lm["status"] == "locked"

# -- test 7: recovery after cleanup failure --
def test_freeze_recover_after_cleanup_failure(tmp_path, monkeypatch):
    pkg, _ = _seal_pkg(tmp_path, "T402_7")
    real_unlink = os.unlink
    import mcps.common.board_package as _bp
    def fail_unlink(path):
        if os.path.basename(path) == "package_manifest.draft.json":
            raise OSError("Permission denied")
        return real_unlink(path)
    monkeypatch.setattr(_bp.os, "unlink", fail_unlink)
    with pytest.raises(FreezeCleanupError):
        freeze_package(pkg)
    assert os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert os.path.isfile(os.path.join(pkg, "package_manifest.json"))
    monkeypatch.undo()
    result = freeze_package(pkg)
    assert result == "already_exists_same"
    assert not os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))

# -- test 8: locked-only corrupted → fail-closed --
def test_freeze_locked_only_corrupted_fails(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T402_8")
    freeze_package(pkg)
    with open(os.path.join(pkg, "package_manifest.json"), "w") as f:
        f.write("{ not json")
    with pytest.raises(ValueError):
        freeze_package(pkg)

# -- test 9: no manifest → ValueError --
def test_freeze_no_manifest(tmp_path):
    pkg = os.path.join(str(tmp_path), "T402_9")
    os.makedirs(pkg, exist_ok=True)
    with pytest.raises(ValueError, match="No package manifest"):
        freeze_package(pkg)

# -- test 10: frozen → default load + expected_revision --
def test_frozen_load_and_expected_revision(tmp_path):
    pkg, rev = _seal_pkg(tmp_path, "T402_10")
    freeze_package(pkg)
    _clear_cache()
    p = board_profile_load("T402_10", search_dirs=[pkg])
    assert p["package_status"] == "locked"
    _clear_cache()
    p2 = board_profile_load("T402_10", search_dirs=[pkg], expected_package_revision=rev)
    assert p2["package_revision"] == rev
    _clear_cache()
    wrong = "sha256:" + "ff" * 32
    with pytest.raises(BoardProfileError) as e:
        board_profile_load("T402_10", search_dirs=[pkg], expected_package_revision=wrong)
    assert e.value.code == "ARTIFACT_STALE"

# -- test 11: concurrent freeze (threads) --
def test_concurrent_freeze(tmp_path):
    """Two threads freezing the same draft → one published, one already_exists_same."""
    pkg, rev = _seal_pkg(tmp_path, "T402_11")
    results = []

    def do_freeze():
        results.append(freeze_package(pkg))

    t1 = threading.Thread(target=do_freeze)
    t2 = threading.Thread(target=do_freeze)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(results) == 2
    assert set(results) == {"published", "already_exists_same"}

    # Final state: locked exists, draft gone, exactly 6 files
    assert not os.path.isfile(os.path.join(pkg, "package_manifest.draft.json"))
    assert os.path.isfile(os.path.join(pkg, "package_manifest.json"))
    assert len(os.listdir(pkg)) == 6

    # Default load succeeds
    _clear_cache()
    p = board_profile_load("T402_11", search_dirs=[pkg])
    assert p["package_status"] == "locked"
    assert p["package_revision"] == rev


# -- T-403 --
def test_profile_sha_consistent(tmp_path):
    pkg, _ = _seal_pkg(tmp_path, "T403")
    shas = set()
    for _ in range(5):
        _clear_cache()
        p = board_profile_load("T403", search_dirs=[pkg], allow_draft=True)
        shas.add(p["sha256"])
    assert len(shas) == 1

# -- T-404 --
def test_b02_regression_baseline():
    p = board_profile_load("TEST_AX7020_MINIMAL", search_dirs=[FIXTURE_DIR])
    assert p["fixture_only"] is True

# -- T-405 --
def test_fixture_still_loads():
    _clear_cache()
    p = board_profile_load("TEST_AX7020_MINIMAL", search_dirs=[FIXTURE_DIR])
    assert p["fixture_only"] is True

def test_fixture_not_in_production_default(monkeypatch):
    _clear_cache()
    monkeypatch.delenv("ZYNQ_BOARD_PROFILE_DIRS", raising=False)
    with pytest.raises((FileNotFoundError, BoardProfileError)):
        board_profile_load("TEST_AX7020_MINIMAL")

# -- T-406 --
def test_cache_invalidates_on_file_change(tmp_path):
    """T-406 (B12-B03 erratum): profile change invalidates cache; fingerprint change recorded (no reject)."""
    pkg, _ = _seal_pkg(tmp_path, "T406")
    _clear_cache()
    p1 = board_profile_load("T406", search_dirs=[pkg], allow_draft=True)
    sha1 = p1["sha256"]
    pp = os.path.join(pkg, "board_profile_T406.json")
    with open(pp, "r") as f:
        prof = json.load(f)
    prof["ddr_physical_bytes"] = 999999999
    with open(pp, "w") as f:
        json.dump(prof, f)
    _clear_cache()
    p2 = board_profile_load("T406", search_dirs=[pkg], allow_draft=True)
    assert p2["sha256"] != sha1
    assert p2["sha256"].startswith("sha256:")
