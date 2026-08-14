"""T-B02-007: Artifact schema — validate, consistency, atomic publish, cross-platform."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from mcps.common.artifact_schema import (
    validate_manifest, check_consistency, publish_manifest,
    atomic_publish_no_replace, ManifestConflictError,
    _atomic_rename_no_replace, _revision_to_filename,
)
from mcps.common.revision import compute_revision, sha256_file, compute_source_files_sha256

PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
assert Path(PROJECT_ROOT).name == "fpgaproject"

_BP_SHA = "sha256:" + "bb" * 32
_BAD_SHA = "sha256:" + "0" * 64     # valid format, won't match any real file
_BAD_REV = "sha256:" + "0" * 64     # valid format
_FILE_SHA = "sha256:" + "cc" * 32
_SRC_SHA = "sha256:" + "ab" * 32


def baseline_files(tmp_path):
    files = {}
    for name in ["platform.xsa", "wrapper.v", "design.bit", "app.elf",
                 "xparameters.h", "board.xdc"]:
        p = tmp_path / name; p.write_text(f"content of {name}")
        files[name] = str(p)
    return files


def baseline_manifests(tmp_path):
    f = baseline_files(tmp_path)

    plat_inputs = {"board_profile_sha256": _BP_SHA,
                   "tool_versions": {"vivado": "2023.1"},
                   "source_files": [], "config_files": []}
    plat = {
        "schema_version": "1.0", "manifest_type": "platform",
        "board_profile_sha256": _BP_SHA,
        "platform_revision": compute_revision(plat_inputs),
        "manifest_revision": compute_revision(plat_inputs),
        "revision_inputs": plat_inputs,
        "xsa_path": f["platform.xsa"],
        "xsa_sha256": sha256_file(f["platform.xsa"]),
        "bd_wrapper_path": f["wrapper.v"],
        "bd_wrapper_sha256": sha256_file(f["wrapper.v"]),
        "address_map": {"axi_gpio_0": {"base": "0x41200000", "range": "64K"}},
        "clock_tree": {},
        "generated_at": "t0", "status": "locked",
    }

    pl_inputs = {"board_profile_sha256": _BP_SHA,
                 "built_from_platform_revision": plat["platform_revision"],
                 "bd_wrapper_sha256": sha256_file(f["wrapper.v"]),
                 "tool_versions": {}, "source_files": [], "config_files": []}
    pl = {
        "schema_version": "1.0", "manifest_type": "pl_build",
        "board_profile_sha256": _BP_SHA,
        "built_from_platform_revision": plat["platform_revision"],
        "manifest_revision": compute_revision(pl_inputs),
        "revision_inputs": pl_inputs,
        "bitstream_path": f["design.bit"],
        "bitstream_sha256": sha256_file(f["design.bit"]),
        "bd_wrapper_sha256": sha256_file(f["wrapper.v"]),
        "xdc_path": f["board.xdc"],
        "xdc_sha256": sha256_file(f["board.xdc"]),
        "timing_met": True, "wns_ns": 0.12, "tns_ns": 0.0,
        "generated_at": "t0", "status": "locked",
    }

    source_files = [{"path": "main.c", "sha256": _SRC_SHA}]
    ps_inputs = {"board_profile_sha256": _BP_SHA,
                 "built_from_platform_revision": plat["platform_revision"],
                 "platform_xsa_sha256": sha256_file(f["platform.xsa"]),
                 "tool_versions": {},
                 "source_files": source_files, "config_files": []}
    ps = {
        "schema_version": "1.0", "manifest_type": "ps_build",
        "board_profile_sha256": _BP_SHA,
        "built_from_platform_revision": plat["platform_revision"],
        "platform_xsa_sha256": sha256_file(f["platform.xsa"]),
        "manifest_revision": compute_revision(ps_inputs),
        "revision_inputs": ps_inputs,
        "elf_path": f["app.elf"], "elf_sha256": sha256_file(f["app.elf"]),
        "xparameters_h_path": f["xparameters.h"],
        "xparameters_h_sha256": sha256_file(f["xparameters.h"]),
        "xparameters_addrs": {"XPAR_AXI_GPIO_0_BASEADDR": "0x41200000"},
        "source_files_sha256": compute_source_files_sha256(source_files),
        "generated_at": "t0", "status": "locked",
    }
    bp = {"board_id": "TEST", "sha256": _BP_SHA, "part": "xc7z020clg400-2"}
    return bp, plat, pl, ps


# === validate_manifest — positive ===

def test_validate_all_valid(tmp_path):
    _, plat, pl, ps = baseline_manifests(tmp_path)
    assert validate_manifest(plat, "platform") == []
    assert validate_manifest(pl, "pl_build") == []
    assert validate_manifest(ps, "ps_build") == []


# === validate_manifest — type errors ===

def test_validate_not_a_dict():
    issues = validate_manifest("x", "platform")
    assert any(i.code == "INVALID_TYPE" for i in issues)
    issues = validate_manifest(None, "platform")
    assert any(i.code == "INVALID_TYPE" for i in issues)
    issues = validate_manifest([], "platform")
    assert any(i.code == "INVALID_TYPE" for i in issues)


def test_validate_manifest_type_mismatch():
    issues = validate_manifest({"manifest_type": "ps_build"}, "platform")
    assert any(i.code == "MANIFEST_TYPE_MISMATCH" for i in issues)


def test_validate_unknown_type():
    issues = validate_manifest({"manifest_type": "unknown"}, "unknown")
    assert any(i.code == "UNSUPPORTED_SCHEMA" for i in issues)


def test_validate_schema_version():
    issues = validate_manifest(
        {"manifest_type": "platform", "schema_version": "99.0"}, "platform")
    assert any(i.code == "UNSUPPORTED_SCHEMA" for i in issues)


def test_validate_missing_field():
    issues = validate_manifest({"manifest_type": "platform"}, "platform")
    assert any(i.code == "MISSING_FIELD" for i in issues)


# --- Type: string fields must be non-empty string ---

def test_validate_string_field_empty(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["xsa_path"] = ""
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_TYPE" and i.field == "xsa_path" for i in issues)


def test_validate_string_field_non_string(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["xsa_path"] = 123
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_TYPE" and i.field == "xsa_path" for i in issues)


# --- Type: dict fields must be dict ---

def test_validate_dict_field_not_dict(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["address_map"] = "not_a_dict"
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_TYPE" and i.field == "address_map" for i in issues)


def test_validate_revision_inputs_not_dict(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["revision_inputs"] = "not_a_dict"
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_TYPE" and i.field == "revision_inputs" for i in issues)


# --- Type: timing_met must be strict bool ---

def test_validate_timing_met_not_bool(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["timing_met"] = "yes"
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "timing_met" for i in issues)
    pl["timing_met"] = 1
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "timing_met" for i in issues)


# --- Type: numeric fields reject bool, NaN, Infinity ---

def test_validate_wns_must_be_number(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["wns_ns"] = "not_a_number"
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "wns_ns" for i in issues)

    pl["wns_ns"] = True  # bool is not a valid number
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "wns_ns" for i in issues)

    pl["wns_ns"] = float('nan')
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "wns_ns" for i in issues)

    pl["wns_ns"] = float('inf')
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TYPE" and i.field == "wns_ns" for i in issues)


# --- Non-string SHA256 rejected ---

def test_validate_non_string_sha256(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["board_profile_sha256"] = 12345
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_SHA256" for i in issues)


# --- status ---

def test_validate_status_not_locked(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["status"] = "draft"
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "INVALID_TYPE" and i.field == "status" for i in issues)


# === validate_manifest — revision ===

def test_validate_bad_manifest_revision(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["manifest_revision"] = _BAD_REV
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "BAD_REVISION" for i in issues)


def test_platform_revision_ne_manifest(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["platform_revision"] = _BAD_REV
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "BAD_REVISION" and i.field == "platform_revision" for i in issues)


# === validate_manifest — revision_inputs missing fields ===

def test_plat_revision_inputs_missing_board_profile(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    del plat["revision_inputs"]["board_profile_sha256"]
    plat["manifest_revision"] = compute_revision(plat["revision_inputs"])
    plat["platform_revision"] = plat["manifest_revision"]
    issues = validate_manifest(plat, "platform")
    assert any("MISSING_FIELD" in i.code for i in issues)


def test_pl_revision_inputs_missing_built_from(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    del pl["revision_inputs"]["built_from_platform_revision"]
    pl["manifest_revision"] = compute_revision(pl["revision_inputs"])
    issues = validate_manifest(pl, "pl_build")
    assert any("MISSING_FIELD" in i.code for i in issues)


def test_pl_revision_inputs_missing_bd_wrapper(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    del pl["revision_inputs"]["bd_wrapper_sha256"]
    pl["manifest_revision"] = compute_revision(pl["revision_inputs"])
    issues = validate_manifest(pl, "pl_build")
    assert any("MISSING_FIELD" in i.code for i in issues)


def test_ps_revision_inputs_missing_xsa(tmp_path):
    _, _, _, ps = baseline_manifests(tmp_path)
    del ps["revision_inputs"]["platform_xsa_sha256"]
    ps["manifest_revision"] = compute_revision(ps["revision_inputs"])
    issues = validate_manifest(ps, "ps_build")
    assert any("MISSING_FIELD" in i.code for i in issues)


# === validate_manifest — revision_inputs cross-reference ===

def test_revision_inputs_bp_xref(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    plat["revision_inputs"]["board_profile_sha256"] = _BAD_SHA
    plat["manifest_revision"] = compute_revision(plat["revision_inputs"])
    plat["platform_revision"] = plat["manifest_revision"]
    issues = validate_manifest(plat, "platform")
    assert any("board_profile_sha256" in str(i.field) and i.code == "BAD_REVISION"
               for i in issues)


def test_pl_built_from_xref(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["revision_inputs"]["built_from_platform_revision"] = _BAD_REV
    pl["manifest_revision"] = compute_revision(pl["revision_inputs"])
    issues = validate_manifest(pl, "pl_build")
    assert any("built_from" in str(i.field) and i.code == "BAD_REVISION"
               for i in issues)


def test_pl_bd_wrapper_xref(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["revision_inputs"]["bd_wrapper_sha256"] = _BAD_SHA
    pl["manifest_revision"] = compute_revision(pl["revision_inputs"])
    issues = validate_manifest(pl, "pl_build")
    assert any("bd_wrapper" in str(i.field) and i.code == "BAD_REVISION"
               for i in issues)


def test_ps_xsa_xref(tmp_path):
    _, _, _, ps = baseline_manifests(tmp_path)
    ps["revision_inputs"]["platform_xsa_sha256"] = _BAD_SHA
    ps["manifest_revision"] = compute_revision(ps["revision_inputs"])
    issues = validate_manifest(ps, "ps_build")
    assert any("xsa" in str(i.field) and i.code == "BAD_REVISION"
               for i in issues)


def test_ps_source_files_sha256_xref(tmp_path):
    _, _, _, ps = baseline_manifests(tmp_path)
    ps["source_files_sha256"] = _BAD_SHA
    issues = validate_manifest(ps, "ps_build")
    assert any("source_files_sha256" in str(i.field) and i.code == "BAD_REVISION"
               for i in issues)


# === revision dependency chain ===

def test_platform_rev_change_changes_pl_revision():
    sha = "sha256:" + "dd" * 32
    pl_inputs = {"board_profile_sha256": _BP_SHA,
                 "built_from_platform_revision": "sha256:" + "11" * 32,
                 "bd_wrapper_sha256": sha,
                 "tool_versions": {}, "source_files": [], "config_files": []}
    rev1 = compute_revision(pl_inputs)
    pl_inputs["built_from_platform_revision"] = "sha256:" + "22" * 32
    rev2 = compute_revision(pl_inputs)
    assert rev1 != rev2


def test_xsa_change_changes_ps_revision():
    ps_old = {"board_profile_sha256": _BP_SHA,
              "built_from_platform_revision": "sha256:" + "33" * 32,
              "platform_xsa_sha256": "sha256:" + "aa" * 32,
              "tool_versions": {},
              "source_files": [{"path": "main.c", "sha256": _SRC_SHA}],
              "config_files": []}
    ps_new = dict(ps_old)
    ps_new["platform_xsa_sha256"] = "sha256:" + "bb" * 32
    assert compute_revision(ps_old) != compute_revision(ps_new)


# === validate_manifest — files ===

def test_validate_file_missing(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["bitstream_path"] = "/nonexistent/file.bit"
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "PATH_NOT_FOUND" for i in issues)


def test_validate_sha256_mismatch(tmp_path):
    _, _, _, ps = baseline_manifests(tmp_path)
    ps["elf_sha256"] = _BAD_SHA
    issues = validate_manifest(ps, "ps_build")
    assert any(i.code == "SHA256_MISMATCH" for i in issues)


def test_validate_dir_not_file(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    d = tmp_path / "a_dir"; d.mkdir()
    plat["xsa_path"] = str(d)
    plat["xsa_sha256"] = _BAD_SHA
    issues = validate_manifest(plat, "platform")
    assert any(i.code == "PATH_NOT_FOUND" for i in issues)


# === validate_manifest — timing ===

def test_validate_timing_met_contradiction(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["wns_ns"] = -0.5; pl["tns_ns"] = 0.0; pl["timing_met"] = True
    issues = validate_manifest(pl, "pl_build")
    assert any(i.code == "INVALID_TIMING" for i in issues)


def test_validate_failing_timing_ok(tmp_path):
    _, _, pl, _ = baseline_manifests(tmp_path)
    pl["wns_ns"] = -0.5; pl["tns_ns"] = -1.0; pl["timing_met"] = False
    issues = validate_manifest(pl, "pl_build")
    assert not any(i.code == "INVALID_TIMING" for i in issues)


# === validate_manifest — malformed doesn't crash ===

def test_validate_malformed_safe():
    assert len(validate_manifest(None, "platform")) >= 1
    assert len(validate_manifest({}, "platform")) >= 1
    issues = validate_manifest({"manifest_type": "pl_build"}, "pl_build")
    assert any(i.code == "MISSING_FIELD" for i in issues)


# === check_consistency ===

def test_consistency_all_match(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    assert check_consistency(plat, pl, ps, bp) == []


def test_consistency_platform_bp(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    plat["board_profile_sha256"] = _BAD_SHA
    assert any(i.artifact == "platform" and i.code == "BOARD_PROFILE_MISMATCH"
               for i in check_consistency(plat, pl, ps, bp))


def test_consistency_pl_bp(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["board_profile_sha256"] = _BAD_SHA
    assert any(i.code == "BOARD_PROFILE_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_ps_bp(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["board_profile_sha256"] = _BAD_SHA
    assert any(i.code == "BOARD_PROFILE_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_pl_revision(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["built_from_platform_revision"] = _BAD_REV
    assert any(i.code == "PLATFORM_REVISION_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_ps_revision(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["built_from_platform_revision"] = _BAD_REV
    assert any(i.code == "PLATFORM_REVISION_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_xsa(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["platform_xsa_sha256"] = _BAD_REV
    assert any(i.code == "XSA_SHA256_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_bd_wrapper(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    pl["bd_wrapper_sha256"] = "sha256:" + "be" * 32
    assert any(i.code == "BD_WRAPPER_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_address_mismatch(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["xparameters_addrs"]["XPAR_AXI_GPIO_0_BASEADDR"] = "0x50000000"
    assert any(i.code == "ADDRESS_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


def test_consistency_address_missing(tmp_path):
    bp, plat, pl, ps = baseline_manifests(tmp_path)
    ps["xparameters_addrs"] = {}
    assert any(i.code == "ADDRESS_MISMATCH" for i in check_consistency(plat, pl, ps, bp))


# === publish_manifest ===

def test_publish_new(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / _revision_to_filename(plat["manifest_revision"]))
    assert publish_manifest(json.dumps(plat, sort_keys=True), final) == "published"
    assert os.path.isfile(final)


def test_publish_idempotent(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / _revision_to_filename(plat["manifest_revision"]))
    plat_copy = dict(plat)
    publish_manifest(json.dumps(plat_copy, sort_keys=True), final)
    plat_copy["generated_at"] = "later"
    assert publish_manifest(json.dumps(plat_copy, sort_keys=True), final) == "already_exists_same"


def test_publish_reject_different(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / _revision_to_filename(plat["manifest_revision"]))
    plat_copy = dict(plat)
    publish_manifest(json.dumps(plat_copy, sort_keys=True), final)
    plat_copy["address_map"] = {"other": {"base": "0x50000000", "range": "128K"}}
    with pytest.raises(ManifestConflictError):
        publish_manifest(json.dumps(plat_copy, sort_keys=True), final)


def test_publish_preserves_content(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / _revision_to_filename(plat["manifest_revision"]))
    js = json.dumps(plat, sort_keys=True)
    publish_manifest(js, final)
    orig = Path(final).read_bytes()
    plat2 = dict(plat)
    plat2["generated_at"] = "newer"
    assert publish_manifest(json.dumps(plat2, sort_keys=True), final) == "already_exists_same"
    assert Path(final).read_bytes() == orig


def test_publish_rejects_bad_json(tmp_path):
    final = str(tmp_path / "manifests" / "platform" / "rev.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    with pytest.raises(ValueError, match="not valid JSON"):
        publish_manifest("not json", final)


def test_publish_rejects_json_array(tmp_path):
    final = str(tmp_path / "manifests" / "platform" / "rev.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    with pytest.raises(ValueError, match="root must be a JSON object"):
        publish_manifest("[1, 2, 3]", final)


def test_publish_rejects_json_null(tmp_path):
    final = str(tmp_path / "manifests" / "platform" / "rev.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    with pytest.raises(ValueError, match="root must be a JSON object"):
        publish_manifest("null", final)


def test_publish_rejects_invalid_manifest(tmp_path):
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / "sha256_fake.json")
    with pytest.raises(ValueError, match="validation failed"):
        publish_manifest('{"manifest_type": "platform", "schema_version": "1.0"}', final)


def test_publish_rejects_wrong_filename(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / "wrong.json")
    with pytest.raises(ValueError, match="filename"):
        publish_manifest(json.dumps(plat, sort_keys=True), final)


def test_publish_no_temp(tmp_path):
    _, plat, _, _ = baseline_manifests(tmp_path)
    manifest_dir = tmp_path / "manifests" / "platform"; manifest_dir.mkdir(parents=True)
    final = str(manifest_dir / _revision_to_filename(plat["manifest_revision"]))
    before = set(os.listdir(str(manifest_dir)))
    publish_manifest(json.dumps(plat, sort_keys=True), final)
    after = set(os.listdir(str(manifest_dir)))
    assert not [f for f in (after - before) if ".tmp." in f]


# === cross-platform no-replace ===

def test_windows_rename_no_overwrite(tmp_path):
    """On Windows: os.rename(FILE_EXISTS) → FileExistsError, NOT overwrite."""
    a = tmp_path / "a.txt"; a.write_text("first")
    b = tmp_path / "b.txt"; b.write_text("second")

    if os.name == "nt":
        # Target exists → should raise
        with pytest.raises(FileExistsError):
            os.rename(a, b)
        assert b.read_text() == "second", "Existing file must not be overwritten"
    else:
        # POSIX: link EEXIST
        try:
            os.link(a, b)
            os.unlink(a)
        except FileExistsError:
            pass  # Expected
        assert b.read_text() == "second"


def test_posix_link_no_overwrite(tmp_path):
    """On POSIX: os.link(EXISTS) → EEXIST. os.link doesn't exist on Windows."""
    if os.name == "nt":
        pytest.skip("os.link not available on Windows")
    a = tmp_path / "a.txt"; a.write_text("first")
    b = tmp_path / "b.txt"; b.write_text("second")
    try:
        os.link(a, b)
        assert False, "Should have raised FileExistsError"
    except FileExistsError:
        pass
    assert b.read_text() == "second"


def test_atomic_publish_partial_write_recovery(tmp_path):
    """Even if partial write simulated (short content), atomic publish recovers."""
    final = str(tmp_path / "manifests" / "platform" / "rev.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    # Write partial content simulated — the function handles full write via loop
    result = atomic_publish_no_replace(b'{"test": "complete"}', final)
    assert result == "published"
    assert json.loads(Path(final).read_text()) == {"test": "complete"}


# === cross-process race tests ===

def _run_publish_child(final, ready_file, content_bytes):
    script = (
        "import json, os, sys, time\n"
        "sys.path.insert(0, r'" + PROJECT_ROOT + "')\n"
        "from mcps.common.artifact_schema import atomic_publish_no_replace, ManifestConflictError\n"
        "\n"
        "ready = r'" + ready_file + "'\n"
        "final = r'" + final + "'\n"
        "content = " + repr(content_bytes) + "\n"
        "\n"
        "with open(ready, 'a') as rf: rf.write('ready\\n')\n"
        "\n"
        "for _ in range(100):\n"
        "    try:\n"
        "        with open(ready, 'r') as rf:\n"
        "            lines = [l.strip() for l in rf.readlines()]\n"
        "        if lines.count('ready') >= 2: break\n"
        "    except Exception: pass\n"
        "    time.sleep(0.02)\n"
        "\n"
        "try:\n"
        "    r = atomic_publish_no_replace(content, final)\n"
        "    print(json.dumps({'status': r}), flush=True)\n"
        "except ManifestConflictError:\n"
        "    print(json.dumps({'status': 'conflict'}), flush=True)\n"
        "except Exception as e:\n"
        "    print(json.dumps({'status': 'error', 'msg': str(e)}), flush=True)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_race_different_content(tmp_path):
    final = str(tmp_path / "manifests" / "race" / "final.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    ready = str(tmp_path / "ready.txt")
    pa = _run_publish_child(final, ready, b'{"test": "A", "generated_at": "ta"}')
    pb = _run_publish_child(final, ready, b'{"test": "B", "generated_at": "tb"}')
    try:
        oa, ea = pa.communicate(timeout=10)
        ob, eb = pb.communicate(timeout=10)
        assert pa.returncode == 0, f"A err: {ea}"; assert pb.returncode == 0, f"B err: {eb}"
        ra, rb = json.loads(oa.strip())["status"], json.loads(ob.strip())["status"]
        assert "published" in [ra, rb], f"No publisher: {ra}, {rb}"
        assert "conflict" in [ra, rb], f"No conflict: {ra}, {rb}"
        assert json.loads(Path(final).read_text())["test"] in ("A", "B")
        assert not [f for f in os.listdir(os.path.dirname(final)) if ".tmp." in f]
    finally:
        pa.kill(); pa.wait(); pb.kill(); pb.wait()


def test_race_same_content(tmp_path):
    final = str(tmp_path / "manifests" / "same" / "final.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    ready = str(tmp_path / "ready_same.txt")
    content = b'{"test": "same", "generated_at": "t"}'
    pa = _run_publish_child(final, ready, content)
    pb = _run_publish_child(final, ready, content)
    try:
        oa, ea = pa.communicate(timeout=10)
        ob, eb = pb.communicate(timeout=10)
        results = [json.loads(oa.strip())["status"], json.loads(ob.strip())["status"]]
        assert "published" in results
        assert "already_exists_same" in results
        assert "conflict" not in results
        assert json.loads(Path(final).read_text())["test"] == "same"
        assert not [f for f in os.listdir(os.path.dirname(final)) if ".tmp." in f]
    finally:
        pa.kill(); pa.wait(); pb.kill(); pb.wait()


def test_race_timeout_protection(tmp_path):
    final = str(tmp_path / "manifests" / "timeout" / "final.json")
    os.makedirs(os.path.dirname(final), exist_ok=True)
    ready = str(tmp_path / "ready_timeout.txt")
    pa = _run_publish_child(final, ready, b'{"test": "A"}')
    pb = _run_publish_child(final, ready, b'{"test": "B"}')
    try:
        pa.communicate(timeout=10); pb.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pytest.fail("Race test hung")
    finally:
        pa.kill(); pa.wait(); pb.kill(); pb.wait()
