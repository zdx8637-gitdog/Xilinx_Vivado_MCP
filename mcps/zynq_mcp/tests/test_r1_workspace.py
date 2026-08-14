"""test_r1_workspace.py — Workspace resolver tests."""
import os, sys, tempfile
from pathlib import Path
import pytest
from mcps.zynq_mcp.control.workspace import (
    resolve_workspace_root, resolve_runtime_root, compute_workspace_id,
    WorkspaceNotFoundError,
)


class TestWorkspace:
    def test_resolves_correctly(self):
        assert resolve_workspace_root().name == "fpgaproject"

    def test_runtime_root_default(self):
        rt = resolve_runtime_root()
        ws = resolve_workspace_root()
        assert str(rt).startswith(str(ws))

    def test_runtime_root_env_override(self, tmp_path):
        rt = tmp_path / ".zynq_test"
        os.environ["ZYNQ_RUNTIME_ROOT"] = str(rt)
        try:
            resolved = resolve_runtime_root()
            assert os.path.normcase(str(resolved)) == os.path.normcase(str(rt.resolve()))
        finally:
            del os.environ["ZYNQ_RUNTIME_ROOT"]

    def test_workspace_id_deterministic(self):
        ws = resolve_workspace_root()
        id1 = compute_workspace_id(ws)
        id2 = compute_workspace_id(ws)
        assert id1 == id2
        assert id1.startswith("ws-")

    def test_same_from_any_cwd(self, monkeypatch):
        """Workspace root is derived from __file__, not cwd."""
        ws1 = resolve_workspace_root()
        monkeypatch.chdir(tempfile.gettempdir())
        ws2 = resolve_workspace_root()
        assert ws1 == ws2
