"""F-06 (fix round #12): instance-conflict report must never pollute stdout.

stdout carries JSONRPC frames only; a bare dict on stdout crashed MCP
clients. second_instance_report now writes to stderr, and main() exits
non-zero on the secondary/fatal paths.
"""
from pathlib import Path

from mcps.zynq_mcp import server


class _FakeGuard:
    workspace_id = "ws-f06"


def test_report_goes_to_stderr_only(capsys):
    server.second_instance_report(_FakeGuard(), Path("does/not/exist/ledger.json"))
    out = capsys.readouterr()
    assert "INSTANCE_ALREADY_RUNNING" not in out.out, \
        f"stdout polluted: {out.out[:300]!r}"
    assert "INSTANCE_ALREADY_RUNNING" in out.err
    assert '"code": "INSTANCE_ALREADY_RUNNING"' in out.err
    assert "recommended_action" in out.err


class _ExitStub:
    def __init__(self):
        self.rc = None

    def exit(self, rc):
        self.rc = rc


def test_main_honors_int_return_codes(monkeypatch):
    async def _fake_main():
        return 1
    monkeypatch.setattr(server, "_main", _fake_main)
    stub = _ExitStub()
    monkeypatch.setattr(server, "sys", stub)
    server.main()
    assert stub.rc == 1


def test_main_zero_for_none(monkeypatch):
    async def _fake_main():
        return None
    monkeypatch.setattr(server, "_main", _fake_main)
    stub = _ExitStub()
    monkeypatch.setattr(server, "sys", stub)
    server.main()
    assert stub.rc == 0
