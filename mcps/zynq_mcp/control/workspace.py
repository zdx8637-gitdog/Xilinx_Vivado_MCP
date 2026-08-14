"""
workspace.py — Single entry point for workspace root resolution.

Resolves the canonical workspace root from module file location,
NOT from cwd, NOT from environment, NOT from active project_path.

Uses project markers (mcps/ directory + docs/brick_development_plan.md)
for fail-closed validation.
"""
from __future__ import annotations

import hashlib, os
from pathlib import Path


class WorkspaceNotFoundError(Exception):
    """No valid workspace root found."""

class WorkspaceAmbiguousError(Exception):
    """Multiple candidates for workspace root."""


def resolve_workspace_root(start_path: str | None = None) -> Path:
    """
    Walk upward from start_path (or this module's file) to find the workspace root.

    start_path: for test injection — overrides the search start point.
    Production: omits start_path, uses __file__.

    Valid workspace must contain:
      - mcps/ directory
      - docs/brick_development_plan.md

    Returns resolve() + normcase() canonical absolute path.
    """
    start = Path(start_path).resolve().parent if start_path else Path(__file__).resolve().parent
    candidates = []

    current = start
    for _ in range(6):
        if _is_workspace_root(current):
            candidates.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

    if len(candidates) == 0:
        raise WorkspaceNotFoundError(
            f"No workspace root found upward from {start}. "
            f"Expected mcps/ directory and docs/brick_development_plan.md."
        )
    if len(candidates) > 1:
        raise WorkspaceAmbiguousError(
            f"Multiple workspace candidates: {[str(c) for c in candidates]}"
        )

    return _canonical(candidates[0])


def _is_workspace_root(path: Path) -> bool:
    return (path / "mcps").is_dir() and (path / "docs" / "brick_development_plan.md").is_file()


def _canonical(path: Path) -> Path:
    """Resolve + normcase for Windows path consistency."""
    p = path.resolve()
    return Path(os.path.normcase(str(p)))


def compute_workspace_id(root: Path) -> str:
    """SHA256 of normcase resolved root path, first 16 hex chars."""
    h = hashlib.sha256(str(root).encode("utf-8")).hexdigest()
    return f"ws-{h[:16]}"


def resolve_runtime_root() -> Path:
    """
    Resolve the runtime root directory.

    Override: ZYNQ_RUNTIME_ROOT environment variable (for testing).
    Default: <workspace_root>/.zynq_runtime/
    """
    env = os.environ.get("ZYNQ_RUNTIME_ROOT")
    if env:
        return _canonical(Path(env))
    return _canonical(resolve_workspace_root() / ".zynq_runtime")
