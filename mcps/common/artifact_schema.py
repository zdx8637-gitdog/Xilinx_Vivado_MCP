"""
artifact_schema.py — Manifest validation, consistency check, atomic no-replace publish.
"""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from typing import Any

from mcps.common.revision import (
    compute_revision, sha256_file, is_sha256, canonical_json,
    compute_source_files_sha256,
)

# ---- Data types ----

@dataclass
class ValidationIssue:
    code: str
    manifest: str
    field: str | None = None
    expected: Any | None = None
    actual: Any | None = None


@dataclass
class ConsistencyIssue:
    code: str
    artifact: str
    field: str
    expected: str
    actual: str


class ManifestConflictError(Exception):
    """Manifest already exists with different semantic content."""
    pass


CURRENT_SCHEMA = "1.0"
VALID_MANIFEST_TYPES = {"platform", "pl_build", "ps_build"}

# ---- Required fields by manifest type ----

_REQUIRED_FIELDS: dict[str, set[str]] = {
    "platform": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "platform_revision", "manifest_revision", "revision_inputs",
        "xsa_path", "xsa_sha256",
        "bd_wrapper_path", "bd_wrapper_sha256",
        "address_map", "clock_tree",
        "generated_at", "status",
    },
    "pl_build": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "built_from_platform_revision", "manifest_revision", "revision_inputs",
        "bitstream_path", "bitstream_sha256",
        "bd_wrapper_sha256", "xdc_path", "xdc_sha256",
        "timing_met", "wns_ns", "tns_ns",
        "generated_at", "status",
    },
    "ps_build": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "built_from_platform_revision", "manifest_revision", "revision_inputs",
        "platform_xsa_sha256",
        "elf_path", "elf_sha256",
        "xparameters_h_path", "xparameters_h_sha256",
        "xparameters_addrs", "source_files_sha256",
        "generated_at", "status",
    },
}

# ---- Type table: field → expected Python type ----

_STRING_FIELDS: dict[str, set[str]] = {
    "platform": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "platform_revision", "manifest_revision",
        "xsa_path", "xsa_sha256", "bd_wrapper_path", "bd_wrapper_sha256",
        "generated_at", "status",
    },
    "pl_build": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "built_from_platform_revision", "manifest_revision",
        "bitstream_path", "bitstream_sha256",
        "bd_wrapper_sha256", "xdc_path", "xdc_sha256",
        "generated_at", "status",
    },
    "ps_build": {
        "schema_version", "manifest_type", "board_profile_sha256",
        "built_from_platform_revision", "manifest_revision",
        "platform_xsa_sha256",
        "elf_path", "elf_sha256",
        "xparameters_h_path", "xparameters_h_sha256",
        "source_files_sha256",
        "generated_at", "status",
    },
}

_DICT_FIELDS: dict[str, set[str]] = {
    "platform": {"revision_inputs", "address_map", "clock_tree"},
    "pl_build": {"revision_inputs"},
    "ps_build": {"revision_inputs", "xparameters_addrs"},
}

_BOOL_FIELDS: dict[str, set[str]] = {
    "pl_build": {"timing_met"},
}

_NUMERIC_FIELDS: dict[str, set[str]] = {
    "pl_build": {"wns_ns", "tns_ns"},
}

# Fields that must be valid SHA256
_SHA256_FIELDS: dict[str, set[str]] = {
    "platform": {
        "board_profile_sha256", "platform_revision", "manifest_revision",
        "xsa_sha256", "bd_wrapper_sha256",
    },
    "pl_build": {
        "board_profile_sha256", "built_from_platform_revision",
        "manifest_revision", "bitstream_sha256",
        "bd_wrapper_sha256", "xdc_sha256",
    },
    "ps_build": {
        "board_profile_sha256", "built_from_platform_revision",
        "manifest_revision", "platform_xsa_sha256",
        "elf_sha256", "xparameters_h_sha256", "source_files_sha256",
    },
}

# File path → sha256 pairing (must exist + match)
_FILE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "platform": [
        ("xsa_path", "xsa_sha256"), ("bd_wrapper_path", "bd_wrapper_sha256"),
    ],
    "pl_build": [
        ("bitstream_path", "bitstream_sha256"), ("xdc_path", "xdc_sha256"),
    ],
    "ps_build": [
        ("elf_path", "elf_sha256"),
        ("xparameters_h_path", "xparameters_h_sha256"),
    ],
}

# Mandatory revision_inputs fields per manifest type
_REVISION_INPUTS_REQUIRED: dict[str, set[str]] = {
    "platform": {"board_profile_sha256", "tool_versions", "source_files", "config_files"},
    "pl_build": {"board_profile_sha256", "built_from_platform_revision",
                 "bd_wrapper_sha256", "tool_versions", "source_files", "config_files"},
    "ps_build": {"board_profile_sha256", "built_from_platform_revision",
                 "platform_xsa_sha256", "tool_versions", "source_files", "config_files"},
}

_REVISION_INPUTS_SHA_FIELDS: dict[str, set[str]] = {
    "platform": {"board_profile_sha256"},
    "pl_build": {"board_profile_sha256", "built_from_platform_revision", "bd_wrapper_sha256"},
    "ps_build": {"board_profile_sha256", "built_from_platform_revision", "platform_xsa_sha256"},
}

_REVISION_INPUTS_DICT_FIELDS = {"tool_versions"}


# ---- validate_manifest ----

def _check_type(val, expected: str, manifest_type: str, field: str) -> ValidationIssue | None:
    if val is None:
        return ValidationIssue("INVALID_TYPE", manifest_type, field, expected, "null")
    if expected == "string":
        if not isinstance(val, str) or not val:
            return ValidationIssue("INVALID_TYPE", manifest_type, field,
                                   "non-empty string", type(val).__name__)
    elif expected == "dict":
        if not isinstance(val, dict):
            return ValidationIssue("INVALID_TYPE", manifest_type, field,
                                   "dict", type(val).__name__)
    elif expected == "bool":
        if val is not True and val is not False:
            return ValidationIssue("INVALID_TYPE", manifest_type, field,
                                   "bool", type(val).__name__)
    elif expected == "number":
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return ValidationIssue("INVALID_TYPE", manifest_type, field,
                                   "number (not bool)", type(val).__name__)
        if math.isnan(val) or math.isinf(val):
            return ValidationIssue("INVALID_TYPE", manifest_type, field,
                                   "finite number", str(val))
    return None


def validate_manifest(manifest: Any, manifest_type: str,
                      resolve_root: str | None = None) -> list[ValidationIssue]:
    """Validate schema, required fields, types, revision, file existence + SHA256.

    When resolve_root is provided and a file path is relative, resolve it
    against resolve_root before checking existence and computing SHA256.
    This lets persisted manifests use project-relative paths while still
    validating against real files on disk.

    Returns structured ValidationIssue list. Never raises on malformed manifests.
    """
    issues: list[ValidationIssue] = []

    if not isinstance(manifest, dict):
        issues.append(ValidationIssue(
            "INVALID_TYPE", manifest_type, "(root)", "dict", type(manifest).__name__))
        return issues

    declared = manifest.get("manifest_type")
    if declared != manifest_type:
        issues.append(ValidationIssue(
            "MANIFEST_TYPE_MISMATCH", manifest_type, "manifest_type",
            manifest_type, str(declared)))

    if manifest_type not in VALID_MANIFEST_TYPES:
        issues.append(ValidationIssue(
            "UNSUPPORTED_SCHEMA", manifest_type, "manifest_type",
            sorted(VALID_MANIFEST_TYPES), manifest_type))
        return issues

    # Schema version
    schema_ver = manifest.get("schema_version")
    if schema_ver != CURRENT_SCHEMA:
        issues.append(ValidationIssue(
            "UNSUPPORTED_SCHEMA", manifest_type, "schema_version",
            CURRENT_SCHEMA, str(schema_ver)))

    # Required fields existence
    for field in _REQUIRED_FIELDS.get(manifest_type, set()):
        if field not in manifest:
            issues.append(ValidationIssue(
                "MISSING_FIELD", manifest_type, field, "(required)", "(missing)"))

    # --- Type validation ---

    # String fields
    for field in _STRING_FIELDS.get(manifest_type, set()):
        if field in manifest:
            issue = _check_type(manifest[field], "string", manifest_type, field)
            if issue:
                issues.append(issue)

    # Dict fields
    for field in _DICT_FIELDS.get(manifest_type, set()):
        if field in manifest:
            issue = _check_type(manifest[field], "dict", manifest_type, field)
            if issue:
                issues.append(issue)

    # Bool fields
    for field in _BOOL_FIELDS.get(manifest_type, set()):
        if field in manifest:
            issue = _check_type(manifest[field], "bool", manifest_type, field)
            if issue:
                issues.append(issue)

    # Numeric fields
    for field in _NUMERIC_FIELDS.get(manifest_type, set()):
        if field in manifest:
            issue = _check_type(manifest[field], "number", manifest_type, field)
            if issue:
                issues.append(issue)

    # --- SHA256 format validation ---
    for field in _SHA256_FIELDS.get(manifest_type, set()):
        val = manifest.get(field)
        if val is not None:
            if not isinstance(val, str) or not is_sha256(val):
                issues.append(ValidationIssue(
                    "INVALID_SHA256", manifest_type, field,
                    "sha256:<64 hex>", str(val)[:30] if val else "None"))

    # --- status must be "locked" ---
    status = manifest.get("status")
    if status is not None and status != "locked":
        issues.append(ValidationIssue(
            "INVALID_TYPE", manifest_type, "status", "locked", str(status)))

    # --- Revision self-consistency ---
    declared_rev = manifest.get("manifest_revision")
    inputs = manifest.get("revision_inputs")
    if declared_rev and isinstance(inputs, dict):
        try:
            computed = compute_revision(inputs)
            if declared_rev != computed:
                issues.append(ValidationIssue(
                    "BAD_REVISION", manifest_type, "manifest_revision",
                    computed, declared_rev))
        except (ValueError, TypeError) as e:
            issues.append(ValidationIssue(
                "BAD_REVISION", manifest_type, "revision_inputs",
                "(valid)", str(e)))

    # platform_revision vs manifest_revision (platform type)
    plat_rev = manifest.get("platform_revision")
    if plat_rev and declared_rev and manifest_type == "platform":
        if plat_rev != declared_rev:
            issues.append(ValidationIssue(
                "BAD_REVISION", manifest_type, "platform_revision",
                declared_rev, plat_rev))

    # --- Revision inputs mandatory fields ---
    if isinstance(inputs, dict):
        for field in _REVISION_INPUTS_REQUIRED.get(manifest_type, set()):
            if field not in inputs:
                issues.append(ValidationIssue(
                    "MISSING_FIELD", manifest_type, f"revision_inputs.{field}",
                    "(required)", "(missing)"))

        # Revision inputs type: tool_versions must be dict
        for field in _REVISION_INPUTS_DICT_FIELDS:
            if field in inputs:
                issue = _check_type(inputs[field], "dict", manifest_type,
                                    f"revision_inputs.{field}")
                if issue:
                    issues.append(issue)

        # Revision inputs SHA fields
        for field in _REVISION_INPUTS_SHA_FIELDS.get(manifest_type, set()):
            val = inputs.get(field)
            if val is not None:
                if not isinstance(val, str) or not is_sha256(val):
                    issues.append(ValidationIssue(
                        "INVALID_SHA256", manifest_type,
                        f"revision_inputs.{field}",
                        "sha256:<64 hex>", str(val)[:30] if val else "None"))

        # Cross-reference: revision_inputs.board_profile_sha256 == top-level
        inputs_bp = inputs.get("board_profile_sha256")
        top_bp = manifest.get("board_profile_sha256")
        if inputs_bp and top_bp and inputs_bp != top_bp:
            issues.append(ValidationIssue(
                "BAD_REVISION", manifest_type,
                "revision_inputs.board_profile_sha256", top_bp, inputs_bp))

        # PL/PS: built_from_platform_revision must match revision_inputs
        bfpr = manifest.get("built_from_platform_revision")
        if bfpr is not None:
            inputs_bfpr = inputs.get("built_from_platform_revision")
            if inputs_bfpr is not None and inputs_bfpr != bfpr:
                issues.append(ValidationIssue(
                    "BAD_REVISION", manifest_type,
                    "revision_inputs.built_from_platform_revision",
                    bfpr, inputs_bfpr))

        # PL: bd_wrapper_sha256 must match revision_inputs
        if manifest_type == "pl_build":
            top_bdw = manifest.get("bd_wrapper_sha256")
            inputs_bdw = inputs.get("bd_wrapper_sha256")
            if top_bdw and inputs_bdw and top_bdw != inputs_bdw:
                issues.append(ValidationIssue(
                    "BAD_REVISION", manifest_type,
                    "revision_inputs.bd_wrapper_sha256",
                    top_bdw, inputs_bdw))

        # PS: platform_xsa_sha256 must match revision_inputs
        if manifest_type == "ps_build":
            ps_xsa = manifest.get("platform_xsa_sha256")
            inputs_xsa = inputs.get("platform_xsa_sha256")
            if ps_xsa and inputs_xsa and ps_xsa != inputs_xsa:
                issues.append(ValidationIssue(
                    "BAD_REVISION", manifest_type,
                    "revision_inputs.platform_xsa_sha256",
                    ps_xsa, inputs_xsa))

            # PS: source_files_sha256 must match computed
            sfs = manifest.get("source_files_sha256")
            source_files = inputs.get("source_files")
            if sfs and isinstance(source_files, list):
                try:
                    computed_sfs = compute_source_files_sha256(source_files)
                    if sfs != computed_sfs:
                        issues.append(ValidationIssue(
                            "BAD_REVISION", manifest_type,
                            "source_files_sha256", computed_sfs, sfs))
                except (ValueError, TypeError) as e:
                    issues.append(ValidationIssue(
                        "BAD_REVISION", manifest_type,
                        "revision_inputs.source_files",
                        "(valid)", str(e)))

    # --- PL timing ---
    if manifest_type == "pl_build":
        timing_met = manifest.get("timing_met")
        wns = manifest.get("wns_ns")
        tns = manifest.get("tns_ns")
        if isinstance(timing_met, bool) and isinstance(wns, (int, float)) and isinstance(tns, (int, float)):
            if not (isinstance(wns, bool) or isinstance(tns, bool)):
                if not (math.isnan(wns) or math.isinf(wns) or math.isnan(tns) or math.isinf(tns)):
                    expected_tm = (wns >= 0 and tns == 0)
                    if timing_met != expected_tm:
                        issues.append(ValidationIssue(
                            "INVALID_TIMING", manifest_type, "timing_met",
                            str(expected_tm), str(timing_met)))

    # --- File existence + SHA256 ---
    for path_key, sha_key in _FILE_PAIRS.get(manifest_type, []):
        fpath = manifest.get(path_key)
        expected_sha = manifest.get(sha_key)
        if not fpath or not isinstance(fpath, str):
            continue
        check_path = fpath
        if resolve_root and not os.path.isabs(fpath):
            check_path = os.path.join(resolve_root, fpath)
        if not os.path.isfile(check_path):
            issues.append(ValidationIssue(
                "PATH_NOT_FOUND", manifest_type, path_key, "(file)", fpath))
            continue
        if expected_sha and is_sha256(str(expected_sha)):
            actual = sha256_file(check_path)
            if actual != expected_sha:
                issues.append(ValidationIssue(
                    "SHA256_MISMATCH", manifest_type, sha_key, expected_sha, actual))

    return issues


# ---- check_consistency ----

def check_consistency(platform: dict, pl: dict, ps: dict,
                      board_profile: dict) -> list[ConsistencyIssue]:
    """Cross-manifest invariant check. Empty list = all clear."""
    issues: list[ConsistencyIssue] = []

    def _add(code, artifact, field, expected, actual):
        issues.append(ConsistencyIssue(
            code=code, artifact=artifact, field=field,
            expected=str(expected), actual=str(actual)))

    bp_sha = board_profile.get("sha256", "")
    plat_rev = platform.get("platform_revision", "")

    if platform.get("board_profile_sha256") != bp_sha:
        _add("BOARD_PROFILE_MISMATCH", "platform", "board_profile_sha256",
             bp_sha, platform.get("board_profile_sha256", ""))
    if pl.get("board_profile_sha256") != bp_sha:
        _add("BOARD_PROFILE_MISMATCH", "pl_build", "board_profile_sha256",
             bp_sha, pl.get("board_profile_sha256", ""))
    if ps.get("board_profile_sha256") != bp_sha:
        _add("BOARD_PROFILE_MISMATCH", "ps_build", "board_profile_sha256",
             bp_sha, ps.get("board_profile_sha256", ""))
    if pl.get("built_from_platform_revision") != plat_rev:
        _add("PLATFORM_REVISION_MISMATCH", "pl_build",
             "built_from_platform_revision", plat_rev,
             pl.get("built_from_platform_revision", ""))
    if ps.get("built_from_platform_revision") != plat_rev:
        _add("PLATFORM_REVISION_MISMATCH", "ps_build",
             "built_from_platform_revision", plat_rev,
             ps.get("built_from_platform_revision", ""))
    if ps.get("platform_xsa_sha256") != platform.get("xsa_sha256"):
        _add("XSA_SHA256_MISMATCH", "ps_build", "platform_xsa_sha256",
             platform.get("xsa_sha256", ""), ps.get("platform_xsa_sha256", ""))
    if pl.get("bd_wrapper_sha256") != platform.get("bd_wrapper_sha256"):
        _add("BD_WRAPPER_MISMATCH", "pl_build", "bd_wrapper_sha256",
             platform.get("bd_wrapper_sha256", ""), pl.get("bd_wrapper_sha256", ""))
    plat_addrs = platform.get("address_map", {})
    ps_addrs = ps.get("xparameters_addrs", {})
    for key, entry in plat_addrs.items():
        ps_key = f"XPAR_{key.upper()}_BASEADDR"
        ps_value = ps_addrs.get(ps_key)
        if ps_value is None:
            _add("ADDRESS_MISMATCH", "ps_build", ps_key,
                 entry.get("base", "(unknown)"), "(missing)")
        elif ps_value != entry.get("base"):
            _add("ADDRESS_MISMATCH", "ps_build", ps_key,
                 entry.get("base", ""), str(ps_value))
    return issues


# ---- publish_manifest ----

def _semantic_content(manifest_json: str) -> dict:
    d = json.loads(manifest_json)
    d.pop("generated_at", None)
    return d


def _tmp_name(final_path: str) -> str:
    return final_path + ".tmp." + uuid.uuid4().hex[:12]


def _write_temp(content_bytes: bytes, tmp_path: str) -> None:
    """Write content to a temp file atomically, with exclusive creation."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp_path, flags)
    try:
        total = len(content_bytes)
        written = 0
        while written < total:
            n = os.write(fd, content_bytes[written:])
            if n == 0:
                raise OSError("Short write to temp file")
            written += n
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_rename_no_replace(tmp_path: str, final_path: str) -> bool:
    """Try to atomically move tmp → final without overwriting.

    Windows: os.rename raises FileExistsError if target exists.
    POSIX: os.link(tmp, final) then unlink(tmp); EEXIST → target exists.
    os.replace is never used.
    """
    if os.name == "nt":
        # Windows: os.rename raises FileExistsError on existing target
        try:
            os.rename(tmp_path, final_path)
            return True
        except FileExistsError:
            return False
        except OSError as e:
            import errno
            if getattr(e, "errno", 0) == errno.EEXIST:
                return False
            raise
    else:
        # POSIX: os.link creates a hardlink → no overwrite
        try:
            os.link(tmp_path, final_path)
            os.unlink(tmp_path)
            return True
        except FileExistsError:
            return False
        except OSError as e:
            import errno
            if getattr(e, "errno", 0) == errno.EEXIST:
                return False
            raise


def atomic_publish_no_replace(content_bytes: bytes, final_path: str) -> str:
    """Publish manifest atomically. Never overwrites existing final file.

    Returns: "published" | "already_exists_same"
    Raises: ManifestConflictError if different content already exists.
    """
    tmp_path = ""
    try:
        tmp_path = _tmp_name(final_path)
        _write_temp(content_bytes, tmp_path)

        ok = _atomic_rename_no_replace(tmp_path, final_path)
        if ok:
            return "published"

        # final already exists — compare semantics
        return _handle_existing(content_bytes, final_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _handle_existing(new_content_bytes: bytes, final_path: str) -> str:
    with open(final_path, "rb") as f:
        existing_bytes = f.read()
    new_semantic = _semantic_content(new_content_bytes.decode("utf-8"))
    existing_semantic = _semantic_content(existing_bytes.decode("utf-8"))
    if new_semantic == existing_semantic:
        return "already_exists_same"
    raise ManifestConflictError(
        f"Manifest at {final_path} already exists with different semantic content. "
        f"Use a new revision path."
    )


def _revision_to_filename(revision: str) -> str:
    """sha256:<64 hex> → sha256_<64 hex>.json  (colon-safe for Windows)."""
    if revision.startswith("sha256:"):
        return f"sha256_{revision[7:]}.json"
    return f"{revision}.json"


def publish_manifest(manifest_json: str, final_path: str,
                     resolve_root: str | None = None) -> str:
    """Publish a manifest JSON string to final_path.

    Validates JSON syntax, validate_manifest(), and filename.
    When resolve_root is provided, relative file paths in the manifest
    are resolved against it for existence/SHA validation, but the
    persisted manifest retains the original relative paths.

    Returns "published" | "already_exists_same".
    """
    # Parse with strict settings
    try:
        manifest = json.loads(manifest_json, parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("JSON contains NaN/Infinity")))
    except json.JSONDecodeError as e:
        raise ValueError(f"Manifest is not valid JSON: {e}") from e
    except ValueError as e:
        if "NaN" in str(e) or "Infinity" in str(e):
            raise ValueError(f"Manifest JSON contains NaN/Infinity: {e}") from e
        raise

    # Must be a dict
    if not isinstance(manifest, dict):
        raise ValueError(
            f"Manifest root must be a JSON object (dict), got {type(manifest).__name__}")

    mtype = manifest.get("manifest_type", "platform")
    issues = validate_manifest(manifest, mtype, resolve_root=resolve_root)
    if issues:
        msgs = [f"{i.code}: {i.field} ({i.expected})" for i in issues]
        raise ValueError(f"Manifest validation failed: {'; '.join(msgs)}")

    # Filename check
    revision = manifest.get("manifest_revision", "")
    expected_name = _revision_to_filename(revision)
    actual_name = os.path.basename(final_path)
    if actual_name != expected_name:
        raise ValueError(
            f"Manifest filename must be {expected_name}, got {actual_name}")

    # Write canonical JSON bytes
    content_bytes = canonical_json(manifest)
    return atomic_publish_no_replace(content_bytes, final_path)
