"""T-B02-004: Context — path normalization, immutability, rejection."""

import os
import pytest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from mcps.common.context import (
    create_session, close_session, get_session_info, get_context,
    SessionError, BoardProfileError, _update_lease_holder,
)


def test_valid_session(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    assert ctx.session_id.startswith("session-")
    info = get_session_info(ctx.session_id)
    assert info["board_id"] == "TEST_AX7020_MINIMAL"
    close_session(ctx.session_id)


def test_reject_fake_session_id():
    with pytest.raises(SessionError):
        get_session_info("nonexistent")


def test_reject_unknown_board(tmp_path):
    with pytest.raises(BoardProfileError):
        create_session(board_id="NONEXISTENT_BOARD", project_path=str(tmp_path))


def test_close_cleans_up(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    sid = ctx.session_id
    close_session(sid)
    with pytest.raises(SessionError):
        get_session_info(sid)


def test_project_path_normalized_to_absolute(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    # Use os.chdir directly (not monkeypatch) to preserve env vars
    import os as _os
    _orig_cwd = _os.getcwd()
    _os.chdir(str(tmp_path))
    try:
        # Pass relative path
        ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path="project")
    finally:
        _os.chdir(_orig_cwd)
    info = get_session_info(ctx.session_id)
    assert os.path.isabs(info["project_path"])
    close_session(ctx.session_id)


def test_equivalent_paths_same_normalization(tmp_path):
    proj = tmp_path / "my_proj"
    proj.mkdir()
    ctx1 = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(proj))
    info1 = get_session_info(ctx1.session_id)
    close_session(ctx1.session_id)
    ctx2 = create_session(board_id="TEST_AX7020_MINIMAL",
                          project_path=str(proj) + os.sep + ".")
    info2 = get_session_info(ctx2.session_id)
    close_session(ctx2.session_id)
    norm1 = os.path.normpath(info1["project_path"])
    norm2 = os.path.normpath(info2["project_path"])
    assert norm1 == norm2


# ---- Immutability ----

def test_create_session_returns_frozen_context(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    with pytest.raises(FrozenInstanceError):
        ctx.board_id = "HACKED"
    with pytest.raises(FrozenInstanceError):
        ctx.project_path = "/hacked"
    with pytest.raises(FrozenInstanceError):
        ctx.session_id = "hacked"
    close_session(ctx.session_id)


def test_get_context_returns_frozen(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    ctx2 = get_context(ctx.session_id)
    with pytest.raises(FrozenInstanceError):
        ctx2.board_id = "HACKED"
    with pytest.raises(FrozenInstanceError):
        ctx2.project_path = "/hacked"
    close_session(ctx.session_id)


def test_registry_unchanged_after_failed_mutation(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    try:
        ctx.board_id = "HACKED"
    except FrozenInstanceError:
        pass
    info = get_session_info(ctx.session_id)
    assert info["board_id"] == "TEST_AX7020_MINIMAL"
    close_session(ctx.session_id)


def test_update_lease_holder_preserves_immutability(tmp_path):
    """Internal _update_lease_holder uses replace() — registry update, not field mutation."""
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    _update_lease_holder(ctx.session_id, "PL_MCP")
    info = get_session_info(ctx.session_id)
    assert info["lease_holder"] == "PL_MCP"
    # Original returned object unchanged (it was a copy)
    assert ctx.lease_holder is None
    close_session(ctx.session_id)


def test_get_context_rejects_unknown():
    with pytest.raises(SessionError, match="Session not found"):
        get_context("unknown-session-id")


def test_close_session_rejects_already_closed(tmp_path):
    ctx = create_session(board_id="TEST_AX7020_MINIMAL", project_path=str(tmp_path))
    close_session(ctx.session_id)
    with pytest.raises(SessionError, match="Session not found"):
        close_session(ctx.session_id)
