"""T-B02-005: Revision — deterministic hash, path normalization, validation."""

import pytest
from mcps.common.revision import (
    compute_revision, canonical_json, sha256_file,
    _normalize_path, _normalize_file_list,
    validate_input_digest, compute_source_files_sha256, is_sha256,
    _validate_sha256,
)


# ---- Hash determinism ----

def test_same_input_same_hash():
    d = {"board_profile_sha256": "sha256:" + "aa" * 32,
         "tool_versions": {"vivado": "2023.1"}}
    assert compute_revision(d) == compute_revision(d)


def test_result_prefix():
    h = compute_revision({"board_profile_sha256": "sha256:" + "aa" * 32})
    assert h.startswith("sha256:")
    assert len(h) == 71


def test_different_inputs():
    assert compute_revision({"a": "1"}) != compute_revision({"a": "2"})


def test_key_order_normalized():
    assert compute_revision({"a": 1, "b": 2}) == compute_revision({"b": 2, "a": 1})


def test_file_list_sorted():
    sha = "sha256:" + "aa" * 32
    d1 = {"source_files": [{"path": "b.v", "sha256": sha}, {"path": "a.v", "sha256": sha}]}
    d2 = {"source_files": [{"path": "a.v", "sha256": sha}, {"path": "b.v", "sha256": sha}]}
    assert compute_revision(d1) == compute_revision(d2)


def test_config_files_sorted():
    sha = "sha256:" + "aa" * 32
    d1 = {"config_files": [{"path": "z.xdc", "sha256": sha}, {"path": "a.xdc", "sha256": sha}]}
    d2 = {"config_files": [{"path": "a.xdc", "sha256": sha}, {"path": "z.xdc", "sha256": sha}]}
    assert compute_revision(d1) == compute_revision(d2)


# ---- Path normalization ----

def test_backslash_normalized():
    sha = "sha256:" + "aa" * 32
    d1 = {"source_files": [{"path": "a\\b.v", "sha256": sha}]}
    d2 = {"source_files": [{"path": "a/b.v", "sha256": sha}]}
    assert compute_revision(d1) == compute_revision(d2)


def test_leading_dot_slash():
    sha = "sha256:" + "aa" * 32
    d1 = {"source_files": [{"path": "./rtl/top.v", "sha256": sha}]}
    d2 = {"source_files": [{"path": "rtl/top.v", "sha256": sha}]}
    assert compute_revision(d1) == compute_revision(d2)


def test_double_slash_collapsed():
    sha = "sha256:" + "aa" * 32
    d1 = {"source_files": [{"path": "a//b.v", "sha256": sha}]}
    d2 = {"source_files": [{"path": "a/b.v", "sha256": sha}]}
    assert compute_revision(d1) == compute_revision(d2)


def test_canonical_stable():
    sha = "sha256:" + "aa" * 32
    raw1 = canonical_json({"source_files": [{"path": "rtl/top.v", "sha256": sha}]})
    raw2 = canonical_json({"source_files": [{"path": "rtl/top.v", "sha256": sha}]})
    assert raw1 == raw2


# ---- Immutability ----

def test_does_not_mutate_caller():
    sha = "sha256:" + "aa" * 32
    digest = {"source_files": [{"path": "b.v", "sha256": sha}, {"path": "a.v", "sha256": sha}]}
    orig = repr(digest)
    compute_revision(digest)
    assert repr(digest) == orig


# ---- Absolute path rejection ----

def test_windows_absolute():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="Absolute"):
        compute_revision({"source_files": [{"path": "C:\\rtl\\top.v", "sha256": sha}]})

    with pytest.raises(ValueError, match="Absolute"):
        compute_revision({"source_files": [{"path": "C:/rtl/top.v", "sha256": sha}]})


def test_posix_absolute():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="Absolute"):
        compute_revision({"source_files": [{"path": "/home/rtl/top.v", "sha256": sha}]})


def test_bare_backslash_absolute():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="Absolute"):
        compute_revision({"source_files": [{"path": "\\root\\top.v", "sha256": sha}]})


def test_unc_rejected():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError):
        compute_revision({"source_files": [{"path": "\\\\server\\share\\f.v", "sha256": sha}]})


def test_drive_relative_rejected():
    """C:path (no backslash) depends on current drive CWD — must reject."""
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="Drive-relative"):
        compute_revision({"source_files": [{"path": "C:rtl/top.v", "sha256": sha}]})

    # Also in validate_input_digest
    issues = validate_input_digest({"source_files": [{"path": "d:foo.v", "sha256": sha}]})
    assert any("Drive-relative" in i for i in issues)


# ---- .. escape rejection ----

def test_dotdot_rejected():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="escape"):
        compute_revision({"source_files": [{"path": "../outside.v", "sha256": sha}]})

    with pytest.raises(ValueError, match="escape"):
        compute_revision({"source_files": [{"path": "rtl/../../outside.v", "sha256": sha}]})


# ---- Non-string / empty path rejection ----

def test_non_string_path_rejected():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="must be a string"):
        compute_revision({"source_files": [{"path": 123, "sha256": sha}]})


def test_empty_path_rejected():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="must not be empty"):
        compute_revision({"source_files": [{"path": "", "sha256": sha}]})

    with pytest.raises(ValueError, match="must not be empty"):
        compute_revision({"source_files": [{"path": "   ", "sha256": sha}]})


def test_all_dots_rejected():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="empty"):
        compute_revision({"source_files": [{"path": "././.", "sha256": sha}]})


# ---- NaN/Infinity rejection ----

def test_nan_rejected():
    with pytest.raises(ValueError):
        compute_revision({"board_profile_sha256": float('nan')})


def test_infinity_rejected():
    with pytest.raises(ValueError):
        compute_revision({"board_profile_sha256": float('inf')})


# ---- Structural validation ----

def test_source_files_not_list():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="must be a list"):
        compute_revision({"source_files": "not_a_list", "board_profile_sha256": sha,
                          "tool_versions": {}})


def test_entry_not_dict():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="must be a dict"):
        compute_revision({"source_files": ["not_a_dict"], "board_profile_sha256": sha,
                          "tool_versions": {}})


def test_duplicate_normalized_path():
    """a/b.v and a\b.v normalize to identical path a/b.v — duplicate rejected."""
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError, match="Duplicate"):
        compute_revision({"source_files": [
            {"path": "a/b.v", "sha256": sha},
            {"path": "a\\b.v", "sha256": sha},
        ]})


def test_invalid_sha256_rejected():
    with pytest.raises(ValueError, match="sha256"):
        compute_revision({"source_files": [{"path": "a.v", "sha256": "bad"}]})

    with pytest.raises(ValueError, match="sha256"):
        compute_revision({"source_files": [{"path": "a.v", "sha256": 123}]})


# ---- validate_input_digest ----

def test_validate_clean():
    sha = "sha256:" + "aa" * 32
    assert validate_input_digest({"source_files": [{"path": "a.v", "sha256": sha}]}) == []


def test_validate_non_dict_digest():
    issues = validate_input_digest("not_a_dict")
    assert any("dict" in i for i in issues)


def test_validate_bad_list():
    issues = validate_input_digest({"source_files": "x"})
    assert any("list" in i for i in issues)


def test_validate_bad_entry():
    issues = validate_input_digest({"source_files": ["x"]})
    assert any("dict" in i for i in issues)


def test_validate_non_string_path():
    sha = "sha256:" + "aa" * 32
    issues = validate_input_digest({"source_files": [{"path": 123, "sha256": sha}]})
    assert any("must be a string" in i for i in issues)


def test_validate_bad_path():
    sha = "sha256:" + "aa" * 32
    issues = validate_input_digest({"source_files": [{"path": "/abs.v", "sha256": sha}]})
    assert any("Absolute" in i for i in issues)


def test_validate_duplicate_paths():
    sha = "sha256:" + "aa" * 32
    issues = validate_input_digest({"source_files": [
        {"path": "a/b.v", "sha256": sha},
        {"path": "a\\b.v", "sha256": sha},
    ]})
    assert any("Duplicate" in i for i in issues)


def test_validate_bad_sha():
    issues = validate_input_digest({"source_files": [{"path": "a.v", "sha256": "bad"}]})
    assert any("sha256" in i for i in issues)


# ---- source_files_sha256 ----

def test_source_files_sha256_deterministic():
    sha = "sha256:" + "aa" * 32
    files = [{"path": "a.v", "sha256": sha}, {"path": "b.v", "sha256": sha}]
    assert compute_source_files_sha256(files) == compute_source_files_sha256(files)


def test_source_files_sha256_normalizes():
    sha = "sha256:" + "aa" * 32
    f1 = [{"path": "a\\b.v", "sha256": sha}]
    f2 = [{"path": "a/b.v", "sha256": sha}]
    assert compute_source_files_sha256(f1) == compute_source_files_sha256(f2)


def test_source_files_sha256_rejects_non_list():
    with pytest.raises(ValueError):
        compute_source_files_sha256("x")


def test_source_files_sha256_rejects_absolute():
    sha = "sha256:" + "aa" * 32
    with pytest.raises(ValueError):
        compute_source_files_sha256([{"path": "C:\\a.v", "sha256": sha}])


# ---- is_sha256 ----

def test_is_sha256():
    assert is_sha256("sha256:" + "a" * 64)
    assert not is_sha256("sha256:short")
    assert not is_sha256("not_prefixed")
    assert not is_sha256(123)
    assert not is_sha256(None)
    assert not is_sha256({"key": "val"})
