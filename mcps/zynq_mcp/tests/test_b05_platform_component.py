"""B05 Platform Domain component tests — no Vivado required.

B11 phase 2: the B05 shortcut generate_platform / platform_generate was
removed (see docs/development/mcp/B11_platform_generate_erratum.md). The
shortcut-only component tests were remapped 1→1 to the surviving atom-layer
equivalents (address-map parsing via _parse_manifest_address_map, adapter
fail-closed via platform_add_ps7); all other tests (board package, top-BD
selection, error types, manifest contract) are unchanged.
"""
import json, os, re, hashlib, pytest
from pathlib import Path

from mcps.zynq_mcp.domains.platform.platform_domain import (
    PlatformError, BoardPackageNotFoundError,
    BoardProfileMismatchError, BdValidationError, XsaExportError,
    WrapperExportError, ManifestError, AdapterError,
    _resolve_board_package, _top_bd_command,
)
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    _parse_manifest_address_map, platform_add_ps7,
)


class TestBoardPackageResolution:
    def test_resolve_already_exists(self):
        d = _resolve_board_package("ALINX_AX7020_v1.0")
        assert os.path.isdir(d)
        assert "boards" in d

    def test_nonexistent_board(self):
        with pytest.raises(BoardPackageNotFoundError) as exc:
            _resolve_board_package("NONEXISTENT_BOARD")
        assert exc.value.reason_code == "BOARD_PACKAGE_NOT_FOUND"


class TestManifestAddressMapParsing:
    """Equivalent replacement for the removed TestAddressMap tests.

    The old tests covered platform_domain._parse_gpio_address /
    EXPECTED_GPIO_ADDRESS, which were removed together with generate_platform
    (B11 phase 2). The surviving general address-normalization logic lives in
    platform_atoms._parse_manifest_address_map, which canonicalizes per-master
    get_bd_addr_segs output for the published manifest.
    """

    def test_parse_manifest_address_map_normalizes_offset(self):
        tcl_out = ("processing_system7_0/M_AXI_GP0 my_slave_0/S_AXI/reg0 "
                   "0x0000000040000000 64K")
        amap = _parse_manifest_address_map(tcl_out)
        assert amap["my_slave_0"]["base"] == "0x40000000"
        assert amap["my_slave_0"]["range"] == "64K"
        assert amap["my_slave_0"]["master"] == "processing_system7_0/M_AXI_GP0"

    def test_parse_manifest_address_map_ignores_partial_lines(self):
        assert _parse_manifest_address_map("only three tokens") == {}
        assert _parse_manifest_address_map("") == {}
        assert _parse_manifest_address_map(
            "processing_system7_0/M_AXI_GP0") == {}

    def test_parse_manifest_address_map_keeps_canonical_base(self):
        tcl_out = ("processing_system7_0/M_AXI_GP0 my_slave_0/S_AXI/reg0 "
                   "0x40000000 64K")
        amap = _parse_manifest_address_map(tcl_out)
        assert amap["my_slave_0"]["base"] == "0x40000000"


class TestTopBdSelection:
    def test_generate_target_selects_exact_parent_bd(self):
        tcl = _top_bd_command("platform_bd", "generate_target")
        assert "get_files -quiet {platform_bd.bd}" in tcl
        assert "[llength $__platform_bd] != 1" in tcl
        assert "generate_target all $__platform_bd" in tcl
        assert "*platform_bd*.bd" not in tcl

    def test_make_wrapper_reuses_exact_parent_selection(self):
        tcl = _top_bd_command("platform_bd", "make_wrapper")
        assert "get_files -quiet {platform_bd.bd}" in tcl
        assert "make_wrapper -files $__platform_bd -top" in tcl
        assert "*platform_bd*.bd" not in tcl

    @pytest.mark.parametrize("name", ["", "bad-name", "a b", "x;puts PWN"])
    def test_invalid_bd_name_rejected_before_tcl(self, name):
        with pytest.raises(ValueError, match="plain Tcl identifier"):
            _top_bd_command(name, "generate_target")


class TestErrorTypes:
    def test_board_not_found(self):
        e = BoardPackageNotFoundError()
        assert e.reason_code == "BOARD_PACKAGE_NOT_FOUND"
        assert isinstance(e, PlatformError)

    def test_profile_mismatch(self):
        e = BoardProfileMismatchError()
        assert e.reason_code == "BOARD_PROFILE_MISMATCH"

    def test_bd_validation(self):
        e = BdValidationError("bad")
        assert e.reason_code == "BD_VALIDATION_FAILED"

    def test_xsa_export(self):
        e = XsaExportError()
        assert e.reason_code == "XSA_EXPORT_FAILED"

    def test_wrapper_export(self):
        e = WrapperExportError()
        assert e.reason_code == "WRAPPER_EXPORT_FAILED"

    def test_manifest_error(self):
        e = ManifestError()
        assert e.reason_code == "MANIFEST_GENERATION_FAILED"

    def test_adapter_error(self):
        e = AdapterError()
        assert e.reason_code == "ADAPTER_NOT_READY"


class TestAdapterFailClosed:
    """Equivalent replacement for the removed TestAdapterRequired.

    The old test called generate_platform(adapter=None) and asserted
    AdapterError / ADAPTER_NOT_READY. The atom path fails closed the same
    way: the first _run_tcl with a missing adapter raises AdapterError with
    the same stable reason code.
    """

    @pytest.mark.asyncio
    async def test_atom_no_adapter_fails_closed(self):
        with pytest.raises(AdapterError) as exc:
            await platform_add_ps7(adapter=None, board_id="ALINX_AX7020_v1.0")
        assert exc.value.reason_code == "ADAPTER_NOT_READY"


class TestManifestContract:
    def test_validate_manifest_no_issues(self, tmp_path):
        """Generate a minimal valid manifest and prove validate_manifest returns 0 issues."""
        from mcps.common.revision import compute_revision
        from mcps.common.artifact_schema import validate_manifest

        pp = str(tmp_path)
        wrapper = os.path.join(pp, "wrapper.v")
        xsa = os.path.join(pp, "p.xsa")
        Path(wrapper).write_text("module w(); endmodule")
        Path(xsa).write_text("dummy")

        h = hashlib.sha256
        def sha(p):
            return "sha256:" + h(open(p, "rb").read()).hexdigest()

        bp_sha = "sha256:" + "3c" * 32
        wrapper_sha = sha(wrapper)
        xsa_sha = sha(xsa)
        revision_inputs = {
            "board_profile_sha256": bp_sha,
            "tool_versions": {"vivado": "2023.1"},
            "source_files": [{"path": "wrapper.v", "sha256": wrapper_sha}],
            "config_files": [],
        }
        rev = compute_revision(revision_inputs)

        manifest = {
            "schema_version": "1.0", "manifest_type": "platform",
            "board_profile_sha256": bp_sha,
            "platform_revision": rev, "manifest_revision": rev,
            "revision_inputs": revision_inputs,
            "xsa_path": xsa, "xsa_sha256": xsa_sha,
            "bd_wrapper_path": wrapper, "bd_wrapper_sha256": wrapper_sha,
            "address_map": {}, "clock_tree": {},
            "generated_at": "2026-01-01T00:00:00Z", "status": "locked",
        }
        issues = validate_manifest(manifest, "platform")
        assert len(issues) == 0, f"Unexpected issues: {issues}"

    def test_publish_manifest_with_resolve_root(self, tmp_path):
        """publish_manifest with resolve_root validates relative paths
        against the project root but persists the relative-path manifest."""
        from mcps.common.revision import compute_revision
        from mcps.common.artifact_schema import publish_manifest, validate_manifest

        pp = str(tmp_path)
        # Create files in subdirectories that match relative manifest paths
        hdl_dir = os.path.join(pp, "hdl")
        os.makedirs(hdl_dir)
        wrapper_path = os.path.join(hdl_dir, "platform_bd_wrapper.v")
        Path(wrapper_path).write_text("module w(); endmodule")

        xsa_path = os.path.join(pp, "platform.xsa")
        Path(xsa_path).write_text("dummy")

        h = hashlib.sha256
        def sha(p):
            return "sha256:" + h(open(p, "rb").read()).hexdigest()

        bp_sha = "sha256:" + "3c" * 32
        wrapper_sha = sha(wrapper_path)
        xsa_sha = sha(xsa_path)
        revision_inputs = {
            "board_profile_sha256": bp_sha,
            "tool_versions": {"vivado": "2023.1"},
            "source_files": [{"path": "hdl/platform_bd_wrapper.v", "sha256": wrapper_sha}],
            "config_files": [],
        }
        rev = compute_revision(revision_inputs)

        # Manifest uses RELATIVE paths
        manifest = {
            "schema_version": "1.0", "manifest_type": "platform",
            "board_profile_sha256": bp_sha,
            "platform_revision": rev, "manifest_revision": rev,
            "revision_inputs": revision_inputs,
            "xsa_path": "platform.xsa", "xsa_sha256": xsa_sha,
            "bd_wrapper_path": "hdl/platform_bd_wrapper.v",
            "bd_wrapper_sha256": wrapper_sha,
            "address_map": {}, "clock_tree": {},
            "generated_at": "2026-01-01T00:00:00Z", "status": "locked",
        }

        # Without resolve_root, relative paths should fail validation
        issues_no_root = validate_manifest(dict(manifest), "platform")
        path_issues = [i for i in issues_no_root if i.code == "PATH_NOT_FOUND"]
        assert len(path_issues) >= 1, f"Expected PATH_NOT_FOUND without resolve_root, got: {issues_no_root}"

        # With resolve_root, should validate OK
        issues_with_root = validate_manifest(dict(manifest), "platform", resolve_root=pp)
        assert len(issues_with_root) == 0, f"Unexpected issues with resolve_root: {issues_with_root}"

        # publish_manifest with resolve_root
        manifest_dir = os.path.join(pp, "manifests", "platform")
        os.makedirs(manifest_dir)
        fn = f"sha256_{rev[7:]}.json"
        final_path = os.path.join(manifest_dir, fn)
        manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False)

        result = publish_manifest(manifest_json, final_path, resolve_root=pp)
        assert result == "published"

        # Verify file exists and has relative paths
        assert os.path.isfile(final_path)
        with open(final_path) as f:
            persisted = json.load(f)
        assert persisted["xsa_path"] == "platform.xsa"
        assert persisted["bd_wrapper_path"] == "hdl/platform_bd_wrapper.v"
        assert not os.path.isabs(persisted["xsa_path"])
        assert not os.path.isabs(persisted["bd_wrapper_path"])

    def test_publish_manifest_rejects_missing_file_with_resolve_root(self, tmp_path):
        """Even with resolve_root, a non-existent file should cause rejection."""
        from mcps.common.revision import compute_revision
        from mcps.common.artifact_schema import publish_manifest

        pp = str(tmp_path)
        bp_sha = "sha256:" + "3c" * 32
        wrapper_sha = "sha256:" + "ab" * 32
        revision_inputs = {
            "board_profile_sha256": bp_sha,
            "tool_versions": {"vivado": "2023.1"},
            "source_files": [],
            "config_files": [],
        }
        rev = compute_revision(revision_inputs)

        manifest = {
            "schema_version": "1.0", "manifest_type": "platform",
            "board_profile_sha256": bp_sha,
            "platform_revision": rev, "manifest_revision": rev,
            "revision_inputs": revision_inputs,
            "xsa_path": "nonexistent.xsa", "xsa_sha256": wrapper_sha,
            "bd_wrapper_path": "hdl/missing.v", "bd_wrapper_sha256": wrapper_sha,
            "address_map": {}, "clock_tree": {},
            "generated_at": "2026-01-01T00:00:00Z", "status": "locked",
        }

        manifest_dir = os.path.join(pp, "manifests", "platform")
        os.makedirs(manifest_dir)
        fn = f"sha256_{rev[7:]}.json"
        final_path = os.path.join(manifest_dir, fn)
        manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False)

        with pytest.raises(ValueError, match="PATH_NOT_FOUND"):
            publish_manifest(manifest_json, final_path, resolve_root=pp)
