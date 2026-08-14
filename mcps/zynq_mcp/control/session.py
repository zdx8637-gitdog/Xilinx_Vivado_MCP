"""
session.py — Ledger-backed session. B02 as rollback safety net. Ledger is sole truth for queries.
"""
import json, logging
from pathlib import Path
from typing import Optional, Callable

from mcps.common.tool_response import success, error
from mcps.common.context import create_session as b02_create_session
from mcps.common.context import close_session as b02_close_session
from mcps.common.context import get_session_info as b02_get_session_info
from mcps.common.context import SessionError, BoardProfileError as CtxBoardProfileError
from mcps.common.board_profile import BoardProfileError
from mcps.common.board_profile import board_profile_load
from mcps.zynq_mcp.control.context import ZynqContext, STAGE_IDLE, STAGE_PLATFORM_DESIGN
from mcps.zynq_mcp.control.execution_ledger import (
    ExecutionLedger, ledger_transaction, ledger_read_shared,
    EXECUTION_LANE_IDLE, EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED,
    OP_NON_TERMINAL, ChannelBusyError,
)
from mcps.zynq_mcp.control.workspace import resolve_workspace_root

logger = logging.getLogger("zynq_mcp.session")
_sessions: dict[str, ZynqContext] = {}


def load_board_package_revision(board_id: str) -> str:
    """Get current revision from locked B03 Board Package."""
    if not board_id or not isinstance(board_id, str) or not board_id.strip():
        raise ValueError("board_id must be non-empty string")
    profile = board_profile_load(board_id)
    actual = profile.get("expected_package_revision", "") if isinstance(profile, dict) else ""
    if not actual:
        ws = resolve_workspace_root()
        pkg = ws / "boards" / board_id / "package_manifest.json"
        if pkg.is_file():
            data = json.loads(pkg.read_text(encoding="utf-8"))
            actual = data.get("manifest_revision", "")
    if not actual:
        raise BoardProfileError(f"Cannot determine board_package_revision for {board_id}")
    return actual


def verify_board_revision(board_id: str, expected_revision: str):
    """B03 drift check. Raises ChannelBusyError on mismatch."""
    if not board_id or not expected_revision:
        raise ChannelBusyError("BOARD_VERIFY_FAILED")
    try:
        board_profile_load(board_id, expected_package_revision=expected_revision)
    except (BoardProfileError, CtxBoardProfileError) as e:
        raise ChannelBusyError(f"BOARD_REVISION_DRIFT: {e}") from e


def create_session_mutator(arguments, instance_id, op_id, signature) -> Callable:
    board_id = arguments["board_id"].strip(); project_path = arguments["project_path"].strip()
    # E005: one authoritative board_profile_load — both values come from same profile
    try: profile = board_profile_load(board_id)
    except BoardProfileError as e: raise ChannelBusyError(f"BOARD_INVALID: {e}")
    except Exception as e: raise ChannelBusyError(f"BOARD_UNVERIFIABLE: {e}")
    if not isinstance(profile, dict):
        raise ChannelBusyError("BOARD_PROFILE_INVALID")
    from mcps.common.revision import is_sha256
    rev = profile.get("package_revision", "")
    profile_sha = profile.get("sha256", "")
    if not isinstance(rev, str) or not rev or not is_sha256(rev):
        raise ChannelBusyError("BOARD_PACKAGE_REVISION_INVALID")
    if not isinstance(profile_sha, str) or not profile_sha or not is_sha256(profile_sha):
        raise ChannelBusyError("BOARD_PROFILE_SHA_INVALID")

    try: b02_ctx = b02_create_session(board_id, project_path)
    except BoardProfileError as e: raise ChannelBusyError(f"CONTEXT_INVALID: {e}")
    except Exception as e: raise ChannelBusyError(f"CONTEXT_ERROR: {e}")
    norm_project = b02_ctx.project_path

    def _mutator(current):
        if current.execution_lane in (EXECUTION_LANE_BUSY, EXECUTION_LANE_RECOVERY_REQUIRED):
            raise ChannelBusyError("CHANNEL_BUSY")
        if current.context.get("session_id"): raise ChannelBusyError("SESSION_EXISTS")
        current.execution_lane = EXECUTION_LANE_IDLE
        current.context = {"session_id": b02_ctx.session_id, "board_id": board_id,
            "project_path": norm_project, "board_package_revision": rev,
            "expected_board_revision": rev, "current_stage": STAGE_PLATFORM_DESIGN,
            "board_profile_sha256": profile_sha,  # E005
            "platform_revision": None, "pl_revision": None, "ps_revision": None}
        # P1-B: dedup_registry is a within-session optimization. It MUST NOT
        # survive the session boundary: an entry recorded in the previous
        # session can collide with a same-tool call in the new session and be
        # wrongly rejected with CONFIRM_RETRY_REQUIRED (the P10 dedup gate
        # matches a signature against previous_operation, which also persists).
        # Reset it to a fresh dict seeded only with this new session's
        # create_session signature.
        current.dedup_registry = {signature: op_id}
        return current

    def _commit(guard, ledger_path):
        try: ledger = ledger_transaction(guard, ledger_path, _mutator)
        except Exception:
            try: b02_close_session(b02_ctx.session_id)
            except Exception: pass
            raise
        _sessions[b02_ctx.session_id] = ZynqContext(
            base=b02_ctx, board_package_revision=rev,
            board_profile_sha256=profile_sha,  # E005
            current_stage=STAGE_IDLE)
        return ledger
    return _commit


def close_session_mutator(arguments) -> Callable:
    session_id = arguments["session_id"].strip()
    def _mutator(current):
        cur_sid = current.context.get("session_id", "")
        if not cur_sid: raise ChannelBusyError("NO_ACTIVE_SESSION")
        if cur_sid != session_id: raise ChannelBusyError("SESSION_ID_MISMATCH")
        ao = current.active_operation
        if ao and ao.get("status") in OP_NON_TERMINAL: raise ChannelBusyError("ACTIVE_OPERATION_PRESENT")
        current.context = {}; return current
    def _commit(guard, ledger_path):
        ledger = ledger_transaction(guard, ledger_path, _mutator)
        _sessions.pop(session_id, None)
        try: b02_close_session(session_id)
        except SessionError: pass
        return ledger
    return _commit


def handle_get_session_info(args, guard, ledger_path, workspace_id=None):
    session_id = args.get("session_id", "")
    if not isinstance(session_id, str) or not session_id.strip():
        return error("session_id must be a non-empty string", code="INVALID_ARGUMENT").to_dict()
    # Ledger is always the source of truth
    try:
        ledger, _ = ledger_read_shared(guard, ledger_path, workspace_id)
    except Exception: ledger = None
    if ledger and ledger.context.get("session_id") == session_id:
        return success(ledger.context).to_dict()
    return error(f"Session not found: {session_id}", code="CONTEXT_INVALID").to_dict()


def get_zynq_context(session_id: str) -> Optional[ZynqContext]:
    return _sessions.get(session_id)
