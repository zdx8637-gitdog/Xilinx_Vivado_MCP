"""T-B02-003: Board Profile loader — cache isolation, immutability, validation."""

import copy
import json
import pytest
from mcps.common.board_profile import board_profile_load, BoardProfileError


def test_load_test_fixture():
    profile = board_profile_load("TEST_AX7020_MINIMAL")
    assert profile["board_id"] == "TEST_AX7020_MINIMAL"
    assert "sha256" in profile
    assert profile["sha256"].startswith("sha256:")
    # Must NOT leak internal metadata
    assert "_source_path" not in profile


def test_sha256_deterministic():
    p1 = board_profile_load("TEST_AX7020_MINIMAL")
    p2 = board_profile_load("TEST_AX7020_MINIMAL")
    assert p1["sha256"] == p2["sha256"]


def test_sha256_changes_when_file_changes(tmp_path):
    profile_json = tmp_path / "board_profile_TEST_MODIFY.json"
    original = {"board_id": "TEST_MODIFY", "part": "xc7z020clg400-2",
                "fixture_only": True}
    profile_json.write_text(json.dumps(original))
    p1 = board_profile_load("TEST_MODIFY", search_dirs=[str(tmp_path)])
    original["part"] = "xc7z010clg225-1"
    profile_json.write_text(json.dumps(original))
    p2 = board_profile_load("TEST_MODIFY", search_dirs=[str(tmp_path)])
    assert p1["sha256"] != p2["sha256"]


def test_reject_unknown_board():
    with pytest.raises(FileNotFoundError):
        board_profile_load("NONEXISTENT_BOARD_ID")


# ---- Cache isolation by source path ----

def test_same_board_id_different_dirs_isolated(tmp_path):
    """Two files with same board_id but different content in different dirs."""
    dir_a = tmp_path / "dir_a"; dir_a.mkdir()
    dir_b = tmp_path / "dir_b"; dir_b.mkdir()

    a = {"board_id": "SHARED_ID", "part": "xc7z020clg400-2", "fixture_only": True}
    b = {"board_id": "SHARED_ID", "part": "xc7z010clg225-1", "fixture_only": True}
    (dir_a / "board_profile_SHARED_ID.json").write_text(json.dumps(a))
    (dir_b / "board_profile_SHARED_ID.json").write_text(json.dumps(b))

    p_a = board_profile_load("SHARED_ID", search_dirs=[str(dir_a)])
    p_b = board_profile_load("SHARED_ID", search_dirs=[str(dir_b)])

    assert p_a["part"] == "xc7z020clg400-2"
    assert p_b["part"] == "xc7z010clg225-1"
    assert p_a["sha256"] != p_b["sha256"]


# ---- Board profile returns immutable (caller mutation does not pollute cache) ----

def test_caller_mutation_does_not_pollute_cache(tmp_path):
    profile_json = tmp_path / "board_profile_TEST_MUTATE.json"
    original = {"board_id": "TEST_MUTATE", "part": "xc7z020clg400-2",
                "fixture_only": True}
    profile_json.write_text(json.dumps(original))

    p1 = board_profile_load("TEST_MUTATE", search_dirs=[str(tmp_path)])
    p1["part"] = "CORRUPTED"
    p1["board_id"] = "HACKED"

    p2 = board_profile_load("TEST_MUTATE", search_dirs=[str(tmp_path)])
    assert p2["part"] == "xc7z020clg400-2"
    assert p2["board_id"] == "TEST_MUTATE"
    assert p2["sha256"] == board_profile_load("TEST_MUTATE", search_dirs=[str(tmp_path)])["sha256"]


# ---- board_id mismatch rejection ----

def test_reject_board_id_mismatch_in_json(tmp_path):
    profile_json = tmp_path / "board_profile_WRONG_ID.json"
    # JSON says board_id = "DIFFERENT", but we request "WRONG_ID"
    profile_json.write_text(json.dumps({"board_id": "DIFFERENT", "part": "xc7z020",
                                        "fixture_only": True}))

    with pytest.raises(BoardProfileError, match="board_id mismatch"):
        board_profile_load("WRONG_ID", search_dirs=[str(tmp_path)])


# ---- Cache invalidation ----

def test_cache_invalidated_on_file_change(tmp_path):
    profile_json = tmp_path / "board_profile_TEST_CACHE_INV.json"
    data = {"board_id": "TEST_CACHE_INV", "part": "xc7z020clg400-2",
            "fixture_only": True}
    profile_json.write_text(json.dumps(data))

    p1 = board_profile_load("TEST_CACHE_INV", search_dirs=[str(tmp_path)])
    sha1 = p1["sha256"]

    # Modify file
    data["part"] = "xc7z010clg225-1"
    profile_json.write_text(json.dumps(data))

    p2 = board_profile_load("TEST_CACHE_INV", search_dirs=[str(tmp_path)])
    sha2 = p2["sha256"]

    assert sha1 != sha2
    assert p2["part"] == "xc7z010clg225-1"


# ---- No _source_path in returned profile ----

def test_no_source_path_in_profile():
    p = board_profile_load("TEST_AX7020_MINIMAL")
    assert "_source_path" not in p
    # Additional safeguard: no absolute path strings that look like filesystem paths
    for key in p:
        val = p[key]
        if isinstance(val, str) and (val.startswith("/") or ":\\" in val):
            # Only sha256 starts with a known prefix
            assert key == "sha256" or val.startswith("sha256:"), \
                f"Field '{key}' has unexpected absolute path: {val}"
