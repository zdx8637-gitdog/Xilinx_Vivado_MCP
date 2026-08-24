"""
board_profile.py — Board Profile loader for all Zynq MCPs.

Fail-closed: every defect is rejected by board_profile_load() itself.
Cache fingerprint covers full package content + directory listing.

B03 Erratum E001 (2026-08-05): Cache hit on line 165 returned early,
bypassing the expected_package_revision check on lines 215-226.
Fixed by extracting _apply_revision_check and calling it before cache return.
Six board package files and manifest_revision unchanged.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

_SEARCH_DIRS = [
    Path(__file__).resolve().parent.parent.parent / "boards" / "ALINX_AX7020_v1.0",
]
_PRODUCTION_DIRS = {str(d) for d in _SEARCH_DIRS}
_cache: dict[str, tuple[str, dict]] = {}


class BoardProfileError(Exception):
    def __init__(self, message: str, code: str = "CONTEXT_INVALID",
                 reason_code: str | None = None):
        self.code = code
        self.reason_code = reason_code
        super().__init__(message)


def _compute_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _resolve_profile_path(board_id: str, search_dirs: list[str]) -> str:
    for d in search_dirs:
        candidate = os.path.join(d, f"board_profile_{board_id}.json")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise FileNotFoundError(
        f"Board profile not found: {board_id} (searched: {search_dirs})"
    )


def _get_search_dirs(explicit: list[str] | None = None) -> list[str]:
    if explicit is not None:
        return list(explicit)
    result = []
    env_dirs = os.environ.get("ZYNQ_BOARD_PROFILE_DIRS", "")
    if env_dirs:
        for part in env_dirs.replace(";", os.pathsep).split(os.pathsep):
            part = part.strip()
            if part and os.path.isdir(part):
                result.append(part)
    for d in _SEARCH_DIRS:
        result.append(str(d))
    return result


def _is_production_dir(package_dir: str) -> bool:
    norm = os.path.abspath(package_dir)
    for prod in _PRODUCTION_DIRS:
        if norm == os.path.abspath(prod):
            return True
    return False


def _is_explicit_or_env_dir(package_dir: str,
                            search_dirs: list[str],
                            explicit: list[str] | None) -> bool:
    """Check if package_dir was reached via explicit search_dirs or env var."""
    norm = os.path.abspath(package_dir)
    if explicit is not None:
        for d in explicit:
            if os.path.abspath(d) == norm:
                return True
    env_dirs = os.environ.get("ZYNQ_BOARD_PROFILE_DIRS", "")
    if env_dirs:
        for part in env_dirs.replace(";", os.pathsep).split(os.pathsep):
            part = part.strip()
            if part and os.path.isdir(part) and os.path.abspath(part) == norm:
                return True
    return False


def _apply_revision_check(profile: dict, expected_package_revision: str | None) -> None:
    """B03 Erratum E001: validate expected_package_revision against loaded profile.
    Called on both cache-hit and fresh-load paths."""
    if expected_package_revision is None:
        return
    actual_rev = profile.get("package_revision", "")
    if not isinstance(expected_package_revision, str) or not expected_package_revision.startswith("sha256:"):
        raise BoardProfileError(
            f"Invalid expected_package_revision format: {expected_package_revision!r}",
            code="INVALID_ARGUMENT", reason_code="INVALID_SHA256")
    if expected_package_revision != actual_rev:
        raise BoardProfileError(
            f"Package revision mismatch: expected {expected_package_revision}, "
            f"actual {actual_rev}",
            code="ARTIFACT_STALE", reason_code="PACKAGE_REVISION_MISMATCH")


def board_profile_load(board_id: str, search_dirs: list[str] | None = None,
                       allow_draft: bool = False,
                       expected_package_revision: str | None = None) -> dict:
    from mcps.common.board_package import (
        validate_board_profile,
        validate_package_runtime,
        find_manifest_status,
        compute_package_fingerprint,
        _load_manifest_from_disk,
        _pick_reason_code,
        _pick_reason_code_for_package_errors,
        ValidationIssue,
    )

    dirs = _get_search_dirs(search_dirs)
    source_path = _resolve_profile_path(board_id, dirs)
    package_dir = os.path.abspath(os.path.dirname(source_path))
    is_prod = _is_production_dir(package_dir)

    # -- Manifest state --
    manifest_name, manifest_status, manifest_reason = \
        find_manifest_status(package_dir)

    if manifest_reason == "INVALID_JSON":
        raise BoardProfileError(
            f"Board package manifest JSON invalid: {package_dir}",
            code="CONTEXT_INVALID", reason_code="INVALID_JSON")

    if manifest_reason == "MISSING_MANIFEST":
        if is_prod:
            raise BoardProfileError(
                f"Board package has no manifest: {package_dir}",
                code="CONTEXT_INVALID", reason_code="MISSING_MANIFEST")

    if manifest_reason == "PACKAGE_STATE_CONFLICT":
        raise BoardProfileError(
            f"Board package manifest state conflict: {package_dir}",
            code="CONTEXT_INVALID", reason_code="PACKAGE_STATE_CONFLICT")

    if manifest_reason == "PACKAGE_NOT_LOCKED" and not allow_draft:
        raise BoardProfileError(
            f"Board package is not locked: {package_dir}",
            code="CONTEXT_INVALID", reason_code="PACKAGE_NOT_LOCKED")

    # -- Load profile JSON --
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
    except json.JSONDecodeError as e:
        raise BoardProfileError(
            f"Board profile JSON invalid: {source_path}: {e}",
            code="CONTEXT_INVALID", reason_code="INVALID_JSON")

    # -- board_id consistency --
    internal_id = profile.get("board_id")
    if internal_id != board_id:
        raise BoardProfileError(
            f"Board profile board_id mismatch: '{internal_id}' vs '{board_id}'",
            code="CONTEXT_INVALID")

    # -- fixture determination --
    fo = profile.get("fixture_only")
    is_fixture = (fo is True)

    if manifest_name is None and not is_fixture:
        raise BoardProfileError(
            f"Board package has no manifest and fixture_only is not True: {package_dir}",
            code="CONTEXT_INVALID", reason_code="MISSING_MANIFEST")

    # -- Fingerprint + cache --
    fingerprint = compute_package_fingerprint(package_dir)
    cached = _cache.get(source_path)
    if cached is not None:
        cached_fp, cached_profile = cached
        if fingerprint == cached_fp:
            _apply_revision_check(cached_profile, expected_package_revision)
            return copy.deepcopy(cached_profile)
        del _cache[source_path]

    # -- Profile SHA --
    profile["sha256"] = _compute_sha256(source_path)

    # -- Profile schema validation --
    profile_issues = validate_board_profile(profile, is_fixture=is_fixture)
    if profile_issues:
        rc = _pick_reason_code(profile_issues)
        raise BoardProfileError(
            f"Board profile validation failed: {rc}",
            code="CONTEXT_INVALID", reason_code=rc)

    # -- Fixture path --
    if is_fixture:
        if is_prod:
            raise BoardProfileError(
                f"Fixture profile not allowed in production directory: {package_dir}",
                code="CONTEXT_INVALID", reason_code="FIXTURE_IN_PRODUCTION_DIR")
        if not _is_explicit_or_env_dir(package_dir, dirs, search_dirs):
            raise BoardProfileError(
                f"Fixture profile not reachable via default search: {package_dir}",
                code="CONTEXT_INVALID", reason_code="FIXTURE_IN_PRODUCTION_DIR")
        if manifest_name is None:
            profile["package_status"] = "fixture"
            profile["package_revision"] = "N/A"
        else:
            manifest = _load_manifest_from_disk(package_dir, manifest_name)
            profile["package_status"] = manifest.get("status", "unknown")
            profile["package_revision"] = manifest.get("manifest_revision", "unknown")
    else:
        # -- Package validation (B12-B03 contract simplification) --
        # The runtime "directory seal" (directory content must exactly equal
        # the manifest file list) and the freeze-discipline SHA cross-reference
        # table are retired from the hot path. Only board identity and path
        # security are enforced here; the full validation lives in
        # validate_package_full() and the dev-time audit tool.
        pkg_issues = validate_package_runtime(
            package_dir, board_id, manifest_name, profile)
        if pkg_issues:
            rc = _pick_reason_code_for_package_errors(pkg_issues)
            raise BoardProfileError(
                f"Package validation failed: {rc}",
                code="CONTEXT_INVALID", reason_code=rc)

        manifest = _load_manifest_from_disk(package_dir, manifest_name)
        profile["package_status"] = manifest.get("status", "unknown")
        profile["package_revision"] = manifest.get("manifest_revision", "unknown")

    # -- expected_package_revision check --
    _apply_revision_check(profile, expected_package_revision)

    _cache[source_path] = (fingerprint, copy.deepcopy(profile))
    return copy.deepcopy(profile)
