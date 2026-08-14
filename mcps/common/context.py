"""
context.py — MCPContext and session lifecycle for all Zynq MCPs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class MCPContext:
    """Immutable session context. Use dataclasses.replace() to update fields."""
    session_id: str
    board_id: str
    project_path: str            # normalized absolute path
    lease_holder: str | None = None
    created_at: str = ""


class SessionError(Exception):
    """Session not found, already closed, or invalid."""
    def __init__(self, message: str, code: str = "CONTEXT_INVALID"):
        self.code = code
        super().__init__(message)


class BoardProfileError(Exception):
    """Board profile not found or validation failed."""
    def __init__(self, message: str, code: str = "CONTEXT_INVALID"):
        self.code = code
        super().__init__(message)


# ---- In-memory session registry ----
_sessions: dict[str, MCPContext] = {}


def _update_lease_holder(session_id: str, lease_holder: str | None) -> None:
    """Internal: update lease_holder on a stored session. Batch 2 will use this."""
    ctx = _sessions.get(session_id)
    if ctx is None:
        raise SessionError(f"Session not found: {session_id}")
    _sessions[session_id] = replace(ctx, lease_holder=lease_holder)


def create_session(board_id: str, project_path: str) -> MCPContext:
    """Create a new MCP session.

    Validates board_id against known profiles.
    Normalizes project_path to absolute path.
    """
    from mcps.common.board_profile import board_profile_load, BoardProfileError as BPE

    try:
        board_profile_load(board_id)
    except (FileNotFoundError, BPE):
        raise BoardProfileError(f"Unknown or invalid board_id: {board_id}")

    sid = f"session-{uuid.uuid4().hex}"
    normalized = str(Path(project_path).resolve(strict=False))
    ctx = MCPContext(
        session_id=sid,
        board_id=board_id,
        project_path=normalized,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _sessions[sid] = ctx
    return ctx


def get_context(session_id: str) -> MCPContext:
    """Return the MCPContext for a session_id.

    All domain API handlers must call this to get board/project info
    from a session_id. Callers cannot substitute board_id or project_path.
    """
    ctx = _sessions.get(session_id)
    if ctx is None:
        raise SessionError(f"Session not found: {session_id}")
    return ctx


def get_session_info(session_id: str) -> dict:
    """Return metadata for an active session."""
    ctx = get_context(session_id)
    return {
        "session_id": ctx.session_id,
        "board_id": ctx.board_id,
        "project_path": ctx.project_path,
        "lease_holder": ctx.lease_holder,
        "created_at": ctx.created_at,
    }


def close_session(session_id: str) -> None:
    """Close a session.

    B02: releases session registry entry only.
    Batch 2 will invoke _update_lease_holder for lock cleanup.
    """
    if session_id not in _sessions:
        raise SessionError(f"Session not found: {session_id}")
    del _sessions[session_id]
