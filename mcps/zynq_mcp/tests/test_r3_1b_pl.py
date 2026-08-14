"""
test_r3_1b_pl.py — R3.1-B Component + Contract tests for system_top generator.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, uuid
from pathlib import Path
import pytest

from mcps.zynq_mcp.domains.pl.system_top import (
    generate_system_top,
    _parse_wrapper,
    _validate_and_bind_manifest,
    _atomic_write_text,
    ManifestBindingError,
    WrapperParseError,
    PathSafetyError,
    AtomicWriteError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "b04_pl_ready"
BD_REAL = str(FIXTURES / "bd_wrapper_realistic.v")
BD_ESCAPED = str(FIXTURES / "bd_wrapper_escaped.v")
BD_BUS = str(FIXTURES / "bd_wrapper_bus.v")
BD_ANSI = str(FIXTURES / "bd_wrapper_ansi.v")
BD_ANSI_ESC = str(FIXTURES / "bd_wrapper_ansi_esc.v")
BD_NO_END = str(FIXTURES / "bd_wrapper_malformed_no_end.v")
BD_DUP = str(FIXTURES / "bd_wrapper_malformed_dup.v")
BD_MULTI = str(FIXTURES / "bd_wrapper_malformed_multi.v")
MANIFEST = str(FIXTURES / "platform_manifest.json")
MANIFEST_BP = str(FIXTURES / "platform_manifest_bad_bp.json")
MANIFEST_REV = str(FIXTURES / "platform_manifest_bad_rev.json")
MANIFEST_INC = str(FIXTURES / "platform_manifest_incomplete.json")
MANIFEST_BAD_SCHEMA = str(FIXTURES / "platform_manifest_bad_schema.json")
MANIFEST_MISSING_FIELD = str(FIXTURES / "platform_manifest_missing_field.json")
MANIFEST_BAD_CONSISTENCY = str(FIXTURES / "platform_manifest_bad_consistency.json")
MANIFEST_BAD_XSA = str(FIXTURES / "platform_manifest_bad_xsa.json")
EXPECTED = str(FIXTURES / "system_top_expected.v")

BP_SHA = "sha256:3c95da56a6a9264ef42b6902f184d7d01c7229eafa70d1061cfd24cc0af0c90a"
PLAT_REV = "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"


def _sha256_str(content):
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _setup_project(tmp_path, wrapper_src=BD_REAL, manifest_src=MANIFEST,
                    fixup_xsa=True):
    proj = str(tmp_path)
    for d in ["manifests/platform", "hdl", "rtl"]:
        os.makedirs(os.path.join(proj, d), exist_ok=True)
    hdl_target = os.path.join(proj, "hdl", "bd_wrapper_realistic.v")
    shutil.copy(wrapper_src, hdl_target)
    wrapper_sha = _sha256_file(hdl_target)
    with open(manifest_src, "r") as f:
        m = json.load(f)
    m = dict(m)
    m["bd_wrapper_path"] = "hdl/bd_wrapper_realistic.v"
    m["bd_wrapper_sha256"] = wrapper_sha
    if fixup_xsa and "xsa_path" in m:
        xsa_path = os.path.join(proj, "platform.xsa")
        Path(xsa_path).write_text("dummy xsa content")
        m["xsa_path"] = "platform.xsa"
        m["xsa_sha256"] = _sha256_file(xsa_path)
    from mcps.common.artifact_schema import _revision_to_filename
    filename = _revision_to_filename(m["platform_revision"])
    manifest_target = os.path.join(proj, "manifests", "platform", filename)
    with open(manifest_target, "w") as f:
        json.dump(m, f)
    return proj


# ═══════════════════════════════════════════════════════════════════
# Parser Component Tests
# ═══════════════════════════════════════════════════════════════════

class TestParser:

    def test_r314_deterministic_output(self, tmp_path):
        proj = _setup_project(tmp_path)
        r1 = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        r2 = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert r1["output"] == r2["output"]
        assert r1["system_top_sha256"] == r2["system_top_sha256"]

    def test_r314b_two_isolated_projects(self, tmp_path):
        for pi in [0, 1]:
            pr = str(tmp_path / f"p{pi}")
            for d in ["manifests/platform", "hdl", "rtl"]:
                os.makedirs(os.path.join(pr, d), exist_ok=True)
            shutil.copy(BD_REAL, os.path.join(pr, "hdl", "bd_wrapper_realistic.v"))
            xp = os.path.join(pr, "platform.xsa"); Path(xp).write_text("xsa")
            xsh = _sha256_file(xp)
            with open(MANIFEST, "r") as f: m = json.load(f)
            m = dict(m); m["bd_wrapper_path"] = "hdl/bd_wrapper_realistic.v"
            m["xsa_path"] = "platform.xsa"; m["xsa_sha256"] = xsh
            from mcps.common.artifact_schema import _revision_to_filename
            fn = _revision_to_filename(m["platform_revision"])
            with open(os.path.join(pr, "manifests", "platform", fn), "w") as f:
                json.dump(m, f)
        r1 = generate_system_top("hdl/bd_wrapper_realistic.v", str(tmp_path/"p0"), PLAT_REV, BP_SHA)
        r2 = generate_system_top("hdl/bd_wrapper_realistic.v", str(tmp_path/"p1"), PLAT_REV, BP_SHA)
        assert r1["system_top_sha256"] == r2["system_top_sha256"]
        with open(r1["output_path"], "rb") as f1, open(r2["output_path"], "rb") as f2:
            assert f1.read() == f2.read()

    def test_r315_wrapper_module_instance(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert "design_1_wrapper design_1_wrapper_i" in r["output"]
        assert "design_1 design_1_i" not in r["output"]
        assert r["wrapper_module"] == "design_1_wrapper"
        assert r["instance_name"] == "design_1_wrapper_i"

    def test_r316_port_directions(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        dmap = {p["semantic_name"]: p["direction"] for p in r["ports"]}
        assert dmap["clk_in"] == "input"
        assert dmap["reset_n"] == "input"
        assert dmap["led_pins"] == "output"
        assert dmap["data_in"] == "input"
        assert dmap["data_out"] == "output"

    def test_r317_bus_widths(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        pmap = {p["semantic_name"]: p for p in r["ports"]}
        assert pmap["led_pins"]["width"] == "[3:0]"
        assert pmap["data_in"]["width"] == "[7:0]"
        assert pmap["data_out"]["width"] == "[7:0]"
        assert pmap["clk_in"]["width"] is None

    def test_r318_escaped_identifiers(self, tmp_path):
        proj = _setup_project(tmp_path, wrapper_src=BD_ESCAPED)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        pmap = {p["semantic_name"]: p for p in r["ports"]}
        assert "foo.bar" in pmap
        assert "bus[0]" in pmap
        assert pmap["foo.bar"]["escaped"] is True
        assert pmap["bus[0]"]["escaped"] is True
        assert pmap["foo.bar"]["emitted_token"] == "\\foo.bar "
        assert pmap["bus[0]"]["emitted_token"] == "\\bus[0] "
        out = r["output"]
        assert "\\foo.bar " in out
        assert "\\bus[0] " in out

    def test_r319_missing_endmodule(self):
        with pytest.raises(WrapperParseError) as exc:
            _parse_wrapper(BD_NO_END)
        assert exc.value.reason_code == "UNCLOSED_MODULE"

    def test_r3b01_non_ansi_primary(self):
        mod, ports = _parse_wrapper(BD_REAL)
        assert mod == "design_1_wrapper"
        assert len(ports) == 5
        assert all(p["escaped"] is False for p in ports)

    def test_r3b02_multi_module_rejected(self):
        with pytest.raises(WrapperParseError) as exc:
            _parse_wrapper(BD_MULTI)
        assert exc.value.reason_code == "MULTIPLE_MODULES"

    def test_r3b03_duplicate_port_rejected(self):
        with pytest.raises(WrapperParseError) as exc:
            _parse_wrapper(BD_DUP)
        assert exc.value.reason_code == "DUPLICATE_PORT"

    def test_r3s15_ansi_format(self):
        mod, ports = _parse_wrapper(BD_ANSI)
        assert mod == "design_ansi_wrapper"
        dmap = {p["semantic_name"]: p["direction"] for p in ports}
        assert dmap["clk"] == "input"
        assert dmap["led_pins"] == "output"

    def test_r3s15b_ansi_bus_widths(self):
        mod, ports = _parse_wrapper(BD_ANSI)
        pmap = {p["semantic_name"]: p for p in ports}
        assert pmap["led_pins"]["width"] == "[3:0]"
        assert pmap["data_in"]["width"] == "[7:0]"

    def test_r3b28_ansi_escaped_identifier(self):
        """R3B28: ANSI wrapper with \\foo.bar , \\bus[0]  ports.
        emitted_token always canonical: \\name + space.
        """
        mod, ports = _parse_wrapper(BD_ANSI_ESC)
        assert mod == "design_ansi_esc_wrapper"
        pmap = {p["semantic_name"]: p for p in ports}
        assert "foo.bar" in pmap
        assert "bus[0]" in pmap
        assert "clk" in pmap
        assert pmap["foo.bar"]["escaped"] is True
        assert pmap["bus[0]"]["escaped"] is True
        assert pmap["clk"]["escaped"] is False
        # All escaped emitted_tokens must be canonical "\\name "
        assert pmap["foo.bar"]["emitted_token"] == "\\foo.bar "
        assert pmap["bus[0]"]["emitted_token"] == "\\bus[0] "
        assert pmap["clk"]["emitted_token"] == "clk"
        assert pmap["bus[0]"]["width"] == "[7:0]"


# ═══════════════════════════════════════════════════════════════════
# Manifest Binder Contract Tests
# ═══════════════════════════════════════════════════════════════════

class TestManifestBinding:

    def test_r320_manifest_single_match(self, tmp_path):
        proj = _setup_project(tmp_path)
        manifest, bdw_abs, bdw_sha = _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert manifest["manifest_type"] == "platform"
        assert bdw_sha.startswith("sha256:")
        assert os.path.isfile(bdw_abs)

    def test_r3b10_manifest_not_found(self, tmp_path):
        proj = _setup_project(tmp_path)
        unknown = "sha256:" + "ee" * 32
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(unknown, proj, BP_SHA)
        assert exc.value.reason_code == "PLATFORM_MANIFEST_NOT_FOUND"

    def test_r3b11_bad_schema(self, tmp_path):
        proj = _setup_project(tmp_path, manifest_src=MANIFEST_BAD_SCHEMA)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_SCHEMA_INVALID"

    def test_r3b12_missing_field(self, tmp_path):
        proj = _setup_project(tmp_path, manifest_src=MANIFEST_MISSING_FIELD)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_MISSING_FIELD"

    def test_r3b13_inconsistent_revision(self, tmp_path):
        proj = _setup_project(tmp_path, manifest_src=MANIFEST_BAD_CONSISTENCY)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_REVISION_INCONSISTENT"

    def test_r3b14_xsa_not_found(self, tmp_path):
        proj = _setup_project(tmp_path, manifest_src=MANIFEST_BAD_XSA, fixup_xsa=False)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_FILE_MISSING"

    def test_r3b15_xsa_sha_mismatch(self, tmp_path):
        """R3B15: xsa file exists but SHA mismatch => MANIFEST_SHA_MISMATCH."""
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["xsa_sha256"] = "sha256:" + "ff" * 32
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_SHA_MISMATCH"

    def test_r3b16_multi_issue_priority_deterministic(self, tmp_path):
        """Multiple issues => deterministic priority order, same result every call."""
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["schema_version"] = "99.0"  # UNSUPPORTED_SCHEMA
        m["xsa_sha256"] = "sha256:" + "ff" * 32  # SHA256_MISMATCH
        with open(mp, "w") as f: json.dump(m, f)
        rc1 = None
        for _ in range(3):
            try:
                _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
            except ManifestBindingError as e:
                if rc1 is None:
                    rc1 = e.reason_code
                else:
                    assert e.reason_code == rc1, "Multiple calls must produce same reason_code"
        assert rc1 == "MANIFEST_SCHEMA_INVALID"  # UNSUPPORTED_SCHEMA has highest priority

    # ── Cross-reference errors ──

    def test_r3s08_board_profile_mismatch(self, tmp_path):
        proj = _setup_project(tmp_path)
        wrong = "sha256:" + "ab" * 32
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, wrong)
        assert exc.value.reason_code == "BOARD_PROFILE_MISMATCH"

    def test_r3s09_platform_revision_mismatch(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["platform_revision"] = "sha256:" + "cc" * 32
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
        assert exc.value.reason_code == "PLATFORM_REVISION_MISMATCH"

    def test_r3s10_bd_wrapper_path_empty(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["bd_wrapper_path"] = ""; m["bd_wrapper_sha256"] = ""
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_INCOMPLETE"

    def test_r3s11_bd_wrapper_sha_invalid(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["bd_wrapper_sha256"] = "not-a-sha256"
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_INCOMPLETE"

    # ── Revision format ──

    def test_r3s16_invalid_revision(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest("not-a-revision", proj, BP_SHA)
        assert exc.value.reason_code == "INVALID_PLATFORM_REVISION"

    def test_r3s17_revision_path_injection(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(ManifestBindingError) as exc:
            _validate_and_bind_manifest("../etc/passwd", proj, BP_SHA)
        assert exc.value.reason_code == "INVALID_PLATFORM_REVISION"

    # ── Manifest path safety ──

    def test_r3s18_manifest_dir_junction_escape(self, tmp_path):
        """R3S18: manifests/platform junction outside project => MANIFEST_PATH_ESCAPE."""
        proj = _setup_project(tmp_path)
        mp = os.path.join(proj, "manifests", "platform")
        shutil.rmtree(os.path.join(proj, "manifests"))
        os.makedirs(os.path.join(proj, "manifests"))
        outside = tempfile.mkdtemp()
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", mp, outside],
                              check=True, capture_output=True, text=True)
            else:
                os.symlink(outside, mp, target_is_directory=True)
            with pytest.raises(ManifestBindingError) as exc:
                _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
            assert exc.value.reason_code == "MANIFEST_PATH_ESCAPE"
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", mp], capture_output=True)
            else:
                if os.path.islink(mp):
                    os.unlink(mp)
            shutil.rmtree(outside, ignore_errors=True)

    def test_r3s18b_manifest_junction_same_project(self, tmp_path):
        """R3S18b: manifests/platform junction to another dir within same project
        => MANIFEST_PATH_ESCAPE (lexical base must be real directory, not redirect)."""
        proj = _setup_project(tmp_path)
        mp = os.path.join(proj, "manifests", "platform")
        shutil.rmtree(os.path.join(proj, "manifests"))
        os.makedirs(os.path.join(proj, "manifests"))
        # Target is hdl/ within same project
        target = os.path.join(proj, "hdl")
        os.makedirs(target, exist_ok=True)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", mp, target],
                          check=True, capture_output=True, text=True)
        else:
            os.symlink(target, mp, target_is_directory=True)
        try:
            with pytest.raises(ManifestBindingError) as exc:
                _validate_and_bind_manifest(PLAT_REV, proj, BP_SHA)
            assert exc.value.reason_code == "MANIFEST_PATH_ESCAPE"
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", mp], capture_output=True)
            else:
                if os.path.islink(mp):
                    os.unlink(mp)

    def test_r3s19_manifest_bd_wrapper_absolute(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["bd_wrapper_path"] = "/etc/passwd"
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_PATH_ESCAPE"

    def test_r3s20_manifest_bd_wrapper_dotdot(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["bd_wrapper_path"] = "../etc/passwd"
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_PATH_ESCAPE"

    def test_r3s21_manifest_bd_wrapper_outside(self, tmp_path):
        proj = _setup_project(tmp_path)
        from mcps.common.artifact_schema import _revision_to_filename
        fn = _revision_to_filename(PLAT_REV)
        mp = os.path.join(proj, "manifests", "platform", fn)
        with open(mp, "r") as f: m = json.load(f)
        m["bd_wrapper_path"] = "../../outside/file.v"
        with open(mp, "w") as f: json.dump(m, f)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "MANIFEST_PATH_ESCAPE"


# ═══════════════════════════════════════════════════════════════════
# Caller Argument Validation
# ═══════════════════════════════════════════════════════════════════

class TestCallerArgValidation:

    def test_r3s01_non_string(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(PathSafetyError) as exc:
            generate_system_top(123, proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "INVALID_ARGUMENT"

    def test_r3s02_empty(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(PathSafetyError) as exc:
            generate_system_top("", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "INVALID_ARGUMENT"

    def test_r3s03_absolute(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(PathSafetyError) as exc:
            generate_system_top("/etc/wrapper.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "PATH_ABSOLUTE"

    def test_r3s04_drive_relative(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(PathSafetyError) as exc:
            generate_system_top("C:rtl/wrapper.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "PATH_DRIVE_RELATIVE"

    def test_r3s05_dotdot_escape(self, tmp_path):
        proj = _setup_project(tmp_path)
        with pytest.raises(PathSafetyError) as exc:
            generate_system_top("../etc/passwd", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "PATH_ESCAPE"

    def test_r3s06_wrapper_path_differs(self, tmp_path):
        proj = _setup_project(tmp_path)
        other = os.path.join(proj, "hdl", "other.v")
        shutil.copy(os.path.join(proj, "hdl", "bd_wrapper_realistic.v"), other)
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/other.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "BD_WRAPPER_PATH_MISMATCH"

    def test_r3s07_sha_mismatch(self, tmp_path):
        proj = _setup_project(tmp_path)
        target = os.path.join(proj, "hdl", "bd_wrapper_realistic.v")
        with open(target, "w") as f:
            f.write("// tampered\nmodule foo (clk); input clk; wire clk;\n  foo foo_i(.clk(clk)); endmodule\n")
        with pytest.raises(ManifestBindingError) as exc:
            generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert exc.value.reason_code == "BD_WRAPPER_SHA_MISMATCH"


# ═══════════════════════════════════════════════════════════════════
# File Output Tests
# ═══════════════════════════════════════════════════════════════════

class TestFileOutput:

    def test_r3b20_output_matches_expected(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        with open(EXPECTED, "r", encoding="utf-8") as f:
            expected = f.read()
        actual = r["output"].replace("\r\n", "\n").strip()
        expected = expected.replace("\r\n", "\n").strip()
        assert actual == expected

    def test_r3b21_file_written_with_correct_sha(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert os.path.isfile(r["output_path"])
        assert "system_top.v" in r["output_path"]
        assert _sha256_file(r["output_path"]) == r["system_top_sha256"]

    def test_r3b22_output_within_project(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        real_proj = os.path.realpath(proj)
        real_out = os.path.realpath(r["output_path"])
        assert os.path.commonpath([real_proj, real_out]) == real_proj

    def test_r3b23_byte_identical(self, tmp_path):
        proj = _setup_project(tmp_path)
        r1 = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        r2 = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        assert r1["output"] == r2["output"]
        assert r1["system_top_sha256"] == r2["system_top_sha256"]

    def test_r3b24_verilog_structure(self, tmp_path):
        proj = _setup_project(tmp_path)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        out = r["output"]
        assert "module system_top" in out
        assert "endmodule" in out
        assert "design_1_wrapper design_1_wrapper_i" in out
        assert "input clk_in;" in out
        assert "output [3:0] led_pins;" in out

    def test_r3b25_rtl_dir_junction_escape(self, tmp_path):
        proj = _setup_project(tmp_path)
        rtl = os.path.join(proj, "rtl")
        shutil.rmtree(rtl)
        outside = tempfile.mkdtemp()
        try:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "mklink", "/J", rtl, outside],
                              check=True, capture_output=True, text=True)
            else:
                os.symlink(outside, rtl, target_is_directory=True)
            with pytest.raises(PathSafetyError) as exc:
                generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
            assert exc.value.reason_code == "PATH_ESCAPE"
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", rtl], capture_output=True)
            else:
                if os.path.islink(rtl):
                    os.unlink(rtl)
            shutil.rmtree(outside, ignore_errors=True)

    def test_r3b25b_rtl_junction_same_project(self, tmp_path):
        """R3B25b: rtl junction to another dir within same project => PATH_ESCAPE."""
        proj = _setup_project(tmp_path)
        rtl = os.path.join(proj, "rtl")
        shutil.rmtree(rtl)
        target = os.path.join(proj, "hdl")
        os.makedirs(target, exist_ok=True)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", rtl, target],
                          check=True, capture_output=True, text=True)
        else:
            os.symlink(target, rtl, target_is_directory=True)
        try:
            with pytest.raises(PathSafetyError) as exc:
                generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
            assert exc.value.reason_code == "PATH_ESCAPE"
        finally:
            if os.name == "nt":
                subprocess.run(["cmd", "/c", "rmdir", rtl], capture_output=True)
            else:
                if os.path.islink(rtl):
                    os.unlink(rtl)

    def test_r3b26_atomic_write_old_file_preserved(self, tmp_path):
        """Atomic write: os.replace fails => old file and bytes unchanged, no partial file."""
        out = os.path.join(str(tmp_path), "test_output.v")
        old_content = "module old; endmodule\n"
        with open(out, "w") as f:
            f.write(old_content)
        old_sha = _sha256_file(out)
        old_bytes = Path(out).read_bytes()

        orig_replace = os.replace
        def _fail(src, dst):
            raise OSError("simulated replace failure")
        import mcps.zynq_mcp.domains.pl.system_top as st_mod
        st_mod.os.replace = _fail
        try:
            with pytest.raises(OSError, match="simulated replace failure"):
                _atomic_write_text(out, "module new; endmodule\n")
        finally:
            st_mod.os.replace = orig_replace

        assert Path(out).read_bytes() == old_bytes, "Old file bytes changed"
        assert _sha256_file(out) == old_sha, "Old file SHA changed"
        # No temp files left
        tmp_files = [f for f in os.listdir(str(tmp_path)) if ".tmp." in f]
        assert not tmp_files, f"Temp files left: {tmp_files}"
        # After restore, write succeeds
        _atomic_write_text(out, "module new; endmodule\n")
        assert Path(out).read_text() == "module new; endmodule\n"

    def test_r3b26b_atomic_write_double_fault(self, tmp_path):
        """Atomic write: os.replace fails AND os.unlink cleanup fails.
        Per-test exceptions: PRIMARY_REPLACE_FAIL + CLEANUP_UNLINK_FAIL.
        Old output file bytes/SHA unchanged. Temp file still exists.
        AtomicWriteError.primary_error and .cleanup_error both set.
        exc.__cause__ is the primary replace error.
        """
        out = os.path.join(str(tmp_path), "test_atomic.v")
        # Pre-create old output file
        old_content = "module old; endmodule\n"
        Path(out).write_text(old_content)
        old_sha = _sha256_file(out)
        old_bytes = Path(out).read_bytes()

        import mcps.zynq_mcp.domains.pl.system_top as st_mod

        # We need os.replace to fail first (writes temp OK, then replace fails)
        # then os.unlink cleanup also fails
        orig_replace = os.replace
        orig_unlink = os.unlink
        replace_called = [0]

        def _fail_replace(src, dst):
            replace_called[0] += 1
            raise OSError("PRIMARY_REPLACE_FAIL")

        def _fail_unlink(path):
            raise OSError("CLEANUP_UNLINK_FAIL")

        os.replace = _fail_replace
        os.unlink = _fail_unlink
        # patch st_mod's os reference too (it imported os at module level)
        st_mod.os.replace = _fail_replace
        st_mod.os.unlink = _fail_unlink
        tmp_files_before = sorted(f for f in os.listdir(str(tmp_path)) if ".tmp." in f)
        try:
            with pytest.raises(AtomicWriteError) as exc_info:
                _atomic_write_text(out, "module new; endmodule\n")

            ae = exc_info.value
            # Primary
            assert ae.primary_error is not None, "primary_error must be set"
            assert "PRIMARY_REPLACE_FAIL" in str(ae.primary_error)
            # Cleanup
            assert ae.cleanup_error is not None, "cleanup_error must be set"
            assert "CLEANUP_UNLINK_FAIL" in str(ae.cleanup_error)
            # __cause__ is the primary replace exception
            assert exc_info.value.__cause__ is not None, "__cause__ must be set"
            assert "PRIMARY_REPLACE_FAIL" in str(exc_info.value.__cause__)
            # Old file unchanged
            assert Path(out).read_bytes() == old_bytes, "Old output file bytes changed"
            assert _sha256_file(out) == old_sha, "Old output file SHA changed"
            # Temp file still exists (cleanup failed)
            tmp_files_after = sorted(f for f in os.listdir(str(tmp_path)) if ".tmp." in f and f not in tmp_files_before)
            assert tmp_files_after, "Temp file must still exist after failed cleanup"
            # Save for cleanup below
            for tf in tmp_files_after:
                tmp_file_path = os.path.join(str(tmp_path), tf)
        finally:
            # Restore
            os.replace = orig_replace
            os.unlink = orig_unlink
            st_mod.os.replace = orig_replace
            st_mod.os.unlink = orig_unlink
        # Clean up leftover temp file
        if os.path.exists(tmp_file_path):
            os.unlink(tmp_file_path)
        assert not os.path.exists(tmp_file_path), "Temp file must be cleaned up"

    def test_r3b27_escaped_identifier_output(self, tmp_path):
        proj = _setup_project(tmp_path, wrapper_src=BD_ESCAPED)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        out = r["output"]
        assert "\\foo.bar " in out
        assert "\\bus[0] " in out
        assert ".\\foo.bar (\\foo.bar )" in out
        assert ".\\bus[0] (\\bus[0] )" in out
        assert "wire \\foo.bar ;" in out
        assert "wire [7:0] \\bus[0] ;" in out
        assert "design_esc_wrapper design_esc_wrapper_i" in out

    def test_r3b28_ansi_escaped_output_format(self, tmp_path):
        """R3B28: ANSI escaped identifiers produce correct canonical output."""
        proj = _setup_project(tmp_path, wrapper_src=BD_ANSI_ESC)
        r = generate_system_top("hdl/bd_wrapper_realistic.v", proj, PLAT_REV, BP_SHA)
        out = r["output"]
        # direction declarations
        assert "input \\foo.bar ;" in out
        assert "output [7:0] \\bus[0] ;" in out
        assert "input clk;" in out
        # wire declarations
        assert "wire \\foo.bar ;" in out
        assert "wire [7:0] \\bus[0] ;" in out
        assert "wire clk;" in out
        # named connections
        assert ".\\foo.bar (\\foo.bar )" in out
        assert ".\\bus[0] (\\bus[0] )" in out
        # escaping is canonical for all
        pmap = {p["semantic_name"]: p for p in r["ports"]}
        assert pmap["foo.bar"]["emitted_token"] == "\\foo.bar "
        assert pmap["bus[0]"]["emitted_token"] == "\\bus[0] "
