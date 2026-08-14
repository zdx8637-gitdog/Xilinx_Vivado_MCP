r"""
revision.py — Deterministic revision hash for all Zynq MCP artifacts.

revision = "sha256:" + SHA256(canonical_json(input_digest))

canonical_json: UTF-8, sorted keys, compact separators, allow_nan=False.
File paths in revision_inputs are normalized by common layer:
 - relative to project root
 - POSIX "/" separators
 - no absolute paths (Windows C:\, POSIX /, UNC \\)
 - no ".." escape
 - "." and "//" collapsed
 - sorted by normalized path, duplicate paths rejected

source_files_sha256 = SHA256 of the canonical source_files array.
"""

import hashlib
import json
import os
import re


def canonical_json(obj) -> bytes:
    """Serialize obj to canonical JSON bytes for hashing.
    allow_nan=False — raises ValueError on NaN/Infinity."""
    return json.dumps(
        obj, sort_keys=True, indent=None,
        separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _normalize_path(path) -> str:
    r"""Normalize a file path for revision hashing.

    Rejects: non-string, empty, absolute (C:\, C:/, /, \\), UNC, ".." escape.
    Collapses: backslash -> forward slash, "." components, repeated "/".
    Returns: non-empty relative POSIX path.
    """
    if not isinstance(path, str):
        raise ValueError(f"Path must be a string, got {type(path).__name__}: {path!r}")
    if not path.strip():
        raise ValueError(f"Path must not be empty")

    # Detect UNC: \\server\share
    if path.startswith("\\\\") or path.startswith("//"):
        raise ValueError(f"UNC paths not allowed in revision inputs: {path}")

    # Detect Windows absolute: C:\ or C:/
    if re.match(r'^[A-Za-z]:[\\/]', path):
        raise ValueError(f"Absolute path not allowed in revision inputs: {path}")

    # Detect Windows drive-relative: C:path  (no backslash after colon)
    # Depends on current drive working directory — not portable
    if re.match(r'^[A-Za-z]:[^\\/]', path):
        raise ValueError(f"Drive-relative paths not allowed in revision inputs: {path}")

    # Detect POSIX absolute
    if path.startswith("/"):
        raise ValueError(f"Absolute path not allowed in revision inputs: {path}")

    # Detect bare backslash-absolute: \root\...
    if path.startswith("\\"):
        raise ValueError(f"Absolute path not allowed in revision inputs: {path}")

    # Normalize separators
    normalized = path.replace("\\", "/")

    # Split and process components
    parts = normalized.split("/")
    result = []
    for p in parts:
        if p == "" or p == ".":
            continue
        if p == "..":
            raise ValueError(f"Path escapes project root (contains '..'): {path}")
        result.append(p)

    if not result:
        raise ValueError(f"Path resolves to empty after normalization: {path}")

    return "/".join(result)


def _validate_sha256(value, field_name: str) -> str | None:
    """Return error string if value is not a valid sha256:<64 hex>, else None."""
    if not isinstance(value, str):
        return f"{field_name} must be a string, got {type(value).__name__}"
    if not value.startswith("sha256:"):
        return f"{field_name} must start with 'sha256:', got {value[:20]!r}"
    hex_part = value[7:]
    if len(hex_part) != 64:
        return f"{field_name} hex part must be 64 chars, got {len(hex_part)}"
    if not all(c in "0123456789abcdef" for c in hex_part):
        return f"{field_name} hex part contains invalid chars"
    return None


def _normalize_file_list(files: list) -> list:
    """Normalize and sort a file list for revision hashing.

    Validates each entry is a dict with valid path and sha256.
    Returns a new sorted list (does not mutate input).
    Raises ValueError on any invalid entry or duplicate normalized paths.
    """
    if not isinstance(files, list):
        raise ValueError(f"source_files/config_files must be a list, got {type(files).__name__}")
    result = []
    seen = set()
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            raise ValueError(
                f"source_files/config_files[{i}] must be a dict, got {type(f).__name__}")
        path = f.get("path")
        sha = f.get("sha256")
        np = _normalize_path(path)
        err = _validate_sha256(sha, f"source_files/config_files[{i}].sha256")
        if err:
            raise ValueError(err)
        if np in seen:
            raise ValueError(f"Duplicate normalized path in revision inputs: {np}")
        seen.add(np)
        entry = {"path": np, "sha256": sha}
        result.append(entry)
    result.sort(key=lambda x: x["path"])
    return result


def validate_input_digest(digest) -> list[str]:
    """Validate input digest before hashing. Returns list of issues (empty = valid)."""
    issues: list[str] = []
    if not isinstance(digest, dict):
        issues.append(f"digest must be a dict, got {type(digest).__name__}")
        return issues
    for key in ("source_files", "config_files"):
        if key not in digest:
            continue
        files = digest[key]
        if not isinstance(files, list):
            issues.append(f"{key} must be a list, got {type(files).__name__}")
            continue
        seen_paths: set[str] = set()
        for i, f in enumerate(files):
            if not isinstance(f, dict):
                issues.append(f"{key}[{i}] must be a dict, got {type(f).__name__}")
                continue
            path = f.get("path")
            if not isinstance(path, str):
                issues.append(
                    f"{key}[{i}].path must be a string, got {type(path).__name__}")
                continue
            np: str
            try:
                np = _normalize_path(path)
            except ValueError as e:
                issues.append(str(e))
                continue
            if np in seen_paths:
                issues.append(f"Duplicate normalized path in {key}: {np}")
            seen_paths.add(np)
            sha = f.get("sha256")
            err = _validate_sha256(sha, f"{key}[{i}].sha256")
            if err:
                issues.append(err)
    return issues


def compute_revision(input_digest: dict) -> str:
    """Compute a deterministic revision hash from an input digest.

    Structural validation: source_files and config_files must be lists of
    {path, sha256} dicts with valid paths and SHA256. Duplicate paths rejected.
    The caller's dict is NOT modified.

    This function is general-purpose (not Manifest-specific).
    Manifest-level field requirements are enforced by validate_manifest().
    """
    digest = dict(input_digest)  # shallow copy — does not modify caller

    # Validate and normalize file lists
    for key in ("source_files", "config_files"):
        if key in digest:
            if not isinstance(digest[key], list):
                raise ValueError(
                    f"{key} must be a list, got {type(digest[key]).__name__}")
            digest[key] = _normalize_file_list(digest[key])

    content = canonical_json(digest)
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def compute_source_files_sha256(files: list) -> str:
    """Compute the source_files_sha256 field for PS Build Manifest.

    = SHA256(canonical_json(normalized and sorted {path, sha256} list))
    """
    normalized = _normalize_file_list(files)
    content = canonical_json(normalized)
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def sha256_file(path: str) -> str:
    """Compute SHA256 of a file. Returns 'sha256:...'"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def is_sha256(value) -> bool:
    """Check if a value is a valid sha256:<64 hex> string."""
    return _validate_sha256(value, "_") is None
