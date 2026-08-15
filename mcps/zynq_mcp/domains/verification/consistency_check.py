"""Cross-domain manifest consistency verification — B01 §5 Phase 4.

Loads all three manifests (Platform, PL Build, PS Build) from disk, runs the
7 frozen consistency checks, and returns a structured verdict.

This is a QUERY tool — no side effects, always idempotent.

The 7 checks (B01 §5 Phase 4 "Check list", §8 Artifact Flow — FROZEN):
  1. pl_build.built_from_platform_revision == platform.platform_revision
  2. ps_build.built_from_platform_revision == platform.platform_revision
  3. ps_build.platform_xsa_sha256 == platform.xsa_sha256
  4. ps_build.xparameters_addrs matches platform.address_map (field-by-field)
  5. ps_build.board_profile_sha256 == board_profile.sha256
  6. pl_build.board_profile_sha256 == board_profile.sha256   (direct comparison)
  7. All artifact files exist + SHA256 matches manifest

Each rule is evaluated independently. A missing optional manifest does not
crash the tool — the rules that depend on it are marked ``skipped``.
Invalid inputs (empty/non-string paths, unreadable/corrupt manifests, missing
required platform manifest) are recorded in ``data.errors`` (fail-closed):
a verdict where any rule is failed or skipped never reports all_passed=True.
"""
from __future__ import annotations

import hashlib
import json
import os

# ---- Rule IDs (stable, machine-consumable) ----
RULE_PL_REVISION = "pl_build_platform_revision_match"
RULE_PS_REVISION = "ps_build_platform_revision_match"
RULE_PS_XSA = "ps_build_platform_xsa_sha256_match"
RULE_PS_ADDRESS_MAP = "ps_build_address_map_match"
RULE_PS_BOARD_PROFILE = "ps_build_board_profile_sha256_match"
RULE_PL_BOARD_PROFILE = "pl_build_board_profile_sha256_match"
RULE_ARTIFACTS = "artifact_files_exist_and_match"

# Artifact file pairs per manifest type. Mirrors mcps.common.artifact_schema._FILE_PAIRS.
_FILE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "platform": [("xsa_path", "xsa_sha256"), ("bd_wrapper_path", "bd_wrapper_sha256")],
    "pl_build": [("bitstream_path", "bitstream_sha256"), ("xdc_path", "xdc_sha256")],
    "ps_build": [("elf_path", "elf_sha256"), ("xparameters_h_path", "xparameters_h_sha256")],
}

_MANIFEST_LABELS = ("platform", "pl_build", "ps_build")


def _sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _make_check(rule: str, passed, actual, expected, *,
                message: str = "", field: str | None = None,
                skipped: bool = False) -> dict:
    check: dict = {
        "rule": rule,
        "passed": passed,       # True | False | None (None only when skipped)
        "skipped": skipped,
        "actual": actual,
        "expected": expected,
        "message": message,
    }
    if field is not None:
        check["field"] = field
    return check


def _load_manifest(path, expected_type: str, errors: list[str],
                   warnings: list[str], *, required: bool = False,
                   resolve_root: str | None = None):
    """Load one manifest dict from disk. Returns dict or None. Never raises.

    ``resolve_root`` resolves *relative manifest paths* (D11): a relative
    path without ``resolve_root`` is rejected as INVALID_ARGUMENT instead of
    being silently resolved against the process CWD (which made every rule
    skipped). Absolute paths and relative paths with ``resolve_root`` are
    checked with ``os.path.isfile`` as before.
    """
    if path is None:
        if required:
            errors.append("platform_manifest_path is required")
        return None
    if not isinstance(path, str):
        errors.append(
            f"{expected_type} manifest path must be a string, got {type(path).__name__}")
        return None
    if not path.strip():
        errors.append(f"{expected_type} manifest path is empty")
        return None
    check_path = path
    if not os.path.isabs(path):
        if resolve_root:
            check_path = os.path.join(resolve_root, path)
        else:
            errors.append(
                f"{expected_type} manifest path is relative ({path!r}) and "
                "resolve_root is not provided — pass an absolute path or "
                "set resolve_root (INVALID_ARGUMENT)")
            return None
    if not os.path.isfile(check_path):
        errors.append(f"{expected_type} manifest NOT FOUND: {check_path} — Phase may need re-run")
        return None
    try:
        with open(check_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(f"{expected_type} manifest at {check_path} is not valid JSON: {e}")
        return None
    except OSError as e:
        errors.append(f"{expected_type} manifest at {check_path} unreadable: {e}")
        return None
    if not isinstance(data, dict):
        errors.append(f"{expected_type} manifest at {check_path} must be a JSON object")
        return None
    declared = data.get("manifest_type")
    if declared != expected_type:
        errors.append(
            f"{expected_type} manifest at {check_path} declares manifest_type={declared!r}, "
            f"expected {expected_type!r}")
        return None
    return data


def _check_field_eq(checks: list[dict], rule: str, source, dependent,
                    source_field: str, dependent_field: str,
                    label: str, source_label: str = "platform") -> None:
    """Rule 1/2/3: source.<source_field> == dependent.<dependent_field>."""
    if source is None or dependent is None:
        checks.append(_make_check(
            rule, None, None, None, skipped=True,
            message=f"{source_label} or {label} manifest not found — rule skipped"))
        return
    expected = source.get(source_field)
    actual = dependent.get(dependent_field)
    if expected is None or actual is None:
        checks.append(_make_check(
            rule, False, actual, expected,
            message=f"missing field: {label}.{dependent_field} expected to equal "
                    f"{source_label}.{source_field}"))
        return
    passed = (expected == actual)
    checks.append(_make_check(
        rule, passed, actual, expected,
        message="ok" if passed else "revision mismatch"))


def _check_address_map(checks: list[dict], plat, ps) -> None:
    """Rule 4: ps_build.xparameters_addrs matches platform.address_map (field-by-field).

    For every key in platform.address_map, ps_build.xparameters_addrs must hold
    XPAR_<KEY.upper()>_BASEADDR == entry["base"]. Base addresses are compared
    case-insensitively (hex). Mirrors mcps.common.artifact_schema.check_consistency.
    """
    if plat is None or ps is None:
        checks.append(_make_check(
            RULE_PS_ADDRESS_MAP, None, None, None, skipped=True,
            message="platform or ps_build manifest not loaded; rule skipped"))
        return
    plat_addrs = plat.get("address_map")
    ps_addrs = ps.get("xparameters_addrs")
    if not isinstance(plat_addrs, dict) or not isinstance(ps_addrs, dict):
        checks.append(_make_check(
            RULE_PS_ADDRESS_MAP, False, ps_addrs, plat_addrs,
            message="address_map / xparameters_addrs must be JSON objects"))
        return
    mismatches: list[str] = []
    for key, entry in plat_addrs.items():
        if not isinstance(entry, dict):
            mismatches.append(f"{key}: platform address entry is not an object")
            continue
        expected_base = entry.get("base")
        ps_key = f"XPAR_{str(key).upper()}_BASEADDR"
        ps_value = ps_addrs.get(ps_key)
        if ps_value is None:
            mismatches.append(f"{ps_key}: missing (expected {expected_base})")
        elif _addr_eq(ps_value, expected_base):
            continue
        else:
            mismatches.append(f"{ps_key}: {ps_value} != {expected_base}")
    passed = not mismatches
    checks.append(_make_check(
        RULE_PS_ADDRESS_MAP, passed, ps_addrs, plat_addrs,
        message="; ".join(mismatches) if mismatches else "all addresses match"))


def _addr_eq(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return str(a).strip().lower() == str(b).strip().lower()


def _check_board_profile(checks: list[dict], label: str, manifest,
                         rule: str, expected_bp) -> None:
    """Rule 5/6: manifest.board_profile_sha256 == provided board profile sha256."""
    if manifest is None:
        checks.append(_make_check(
            rule, None, None, None, skipped=True,
            message=f"{label} manifest not loaded; rule skipped"))
        return
    if expected_bp is None:
        checks.append(_make_check(
            rule, None, None, None, skipped=True,
            message="board_profile_sha256 not provided; rule skipped"))
        return
    actual = manifest.get("board_profile_sha256")
    passed = (actual == expected_bp)
    checks.append(_make_check(
        rule, passed, actual, expected_bp,
        message="ok" if passed else "board profile sha256 mismatch"))


def _check_artifact_files(checks: list[dict], label: str, manifest,
                          resolve_root) -> None:
    """Rule 7: every artifact file in a manifest exists and its SHA256 matches.

    Emits one check entry per file pair. Relative paths are resolved against
    resolve_root when provided (manifests persist project-relative paths).
    """
    for path_key, sha_key in _FILE_PAIRS[label]:
        field = f"{label}:{path_key}"
        if manifest is None:
            checks.append(_make_check(
                RULE_ARTIFACTS, None, None, None, skipped=True, field=field,
                message=f"{label} manifest not loaded; rule skipped"))
            continue
        fpath = manifest.get(path_key)
        expected_sha = manifest.get(sha_key)
        if not isinstance(fpath, str) or not fpath.strip():
            checks.append(_make_check(
                RULE_ARTIFACTS, False, None, expected_sha, field=field,
                message=f"manifest field {path_key} missing or empty"))
            continue
        check_path = fpath
        if resolve_root and not os.path.isabs(fpath):
            check_path = os.path.join(resolve_root, fpath)
        if not os.path.isfile(check_path):
            checks.append(_make_check(
                RULE_ARTIFACTS, False, "MISSING", expected_sha, field=field,
                message=f"file not found: {check_path}"))
            continue
        try:
            actual_sha = _sha256_file(check_path)
        except OSError as e:
            checks.append(_make_check(
                RULE_ARTIFACTS, False, "READ_ERROR", expected_sha, field=field,
                message=f"cannot read {check_path}: {e}"))
            continue
        passed = (expected_sha is not None and actual_sha == expected_sha)
        checks.append(_make_check(
            RULE_ARTIFACTS, passed, actual_sha, expected_sha, field=field,
            message="ok" if passed else "sha256 mismatch"))


async def verify_consistency(
    platform_manifest_path: str,
    pl_build_manifest_path: str | None = None,
    ps_build_manifest_path: str | None = None,
    board_profile_sha256: str | None = None,
    resolve_root: str | None = None,
) -> dict:
    """Run the 7 cross-domain consistency checks (B01 §5 Phase 4).

    ``platform_manifest_path`` is required; the PL/PS build manifests are
    optional (a missing manifest marks the dependent rules skipped). Relative
    artifact paths in manifests are resolved against ``resolve_root``.

    Returns:
        {"status": "success", "data": {
            "checks": [
                {"rule": "...", "passed": True|False|None, "skipped": bool,
                 "actual": ..., "expected": ..., "message": "...", "field": "..."},
                ...
            ],
            "all_passed": true/false,     # false if any check failed OR skipped
            "errors": [...],
            "warnings": [...],
            "summary": {"total": n, "passed": n, "failed": n, "skipped": n}
        }}
    """
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    plat = _load_manifest(platform_manifest_path, "platform", errors, warnings,
                          required=True, resolve_root=resolve_root)
    pl = _load_manifest(pl_build_manifest_path, "pl_build", errors, warnings,
                        resolve_root=resolve_root)
    ps = _load_manifest(ps_build_manifest_path, "ps_build", errors, warnings,
                        resolve_root=resolve_root)

    if board_profile_sha256 is None:
        warnings.append("board_profile_sha256 not provided; "
                        "board-profile rules skipped")
    elif not isinstance(board_profile_sha256, str) or not board_profile_sha256.strip():
        errors.append("board_profile_sha256 must be a non-empty string")
        board_profile_sha256 = None  # unusable expected value → rules 5/6 skipped

    # Rule 1/2: built_from_platform_revision == platform.platform_revision
    _check_field_eq(checks, RULE_PL_REVISION, plat, pl,
                    "platform_revision", "built_from_platform_revision",
                    "pl_build")
    _check_field_eq(checks, RULE_PS_REVISION, plat, ps,
                    "platform_revision", "built_from_platform_revision",
                    "ps_build")
    # Rule 3: ps_build.platform_xsa_sha256 == platform.xsa_sha256
    _check_field_eq(checks, RULE_PS_XSA, plat, ps,
                    "xsa_sha256", "platform_xsa_sha256", "ps_build")
    # Rule 4: xparameters_addrs vs address_map (field-by-field)
    _check_address_map(checks, plat, ps)
    # Rule 5/6: board_profile_sha256 (direct comparison)
    _check_board_profile(checks, "ps_build", ps, RULE_PS_BOARD_PROFILE,
                         board_profile_sha256)
    _check_board_profile(checks, "pl_build", pl, RULE_PL_BOARD_PROFILE,
                         board_profile_sha256)
    # Rule 7: artifact files exist + SHA256 matches
    manifests = {"platform": plat, "pl_build": pl, "ps_build": ps}
    for label in _MANIFEST_LABELS:
        _check_artifact_files(checks, label, manifests[label], resolve_root)

    failed = sum(1 for c in checks if c.get("passed") is False)
    skipped = sum(1 for c in checks if c.get("skipped") is True)
    passed = sum(1 for c in checks if c.get("passed") is True)
    # Fail-closed: a skipped rule is "not verified", so it can never claim
    # all_passed=True (B01: errors non-empty → abort).
    all_passed = (failed == 0 and skipped == 0)

    return {
        "status": "success",
        "data": {
            "checks": checks,
            "all_passed": all_passed,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "total": len(checks),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
            },
        },
    }
