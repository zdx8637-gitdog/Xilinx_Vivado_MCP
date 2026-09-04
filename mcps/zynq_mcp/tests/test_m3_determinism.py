"""test_m3_determinism.py — B13-M3: deterministic XSA normalization.

Real-board evidence: identical BD content re-exported as XSA produced
different bytes (timestamps), drifting the platform manifest revision
(307130c4 -> 6bf2e166). normalize_xsa re-packs deterministically so
content-equivalent exports are byte-identical and the manifest revision
depends on content only.
"""
import asyncio
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from mcps.zynq_mcp.domains.platform.xsa_normalize import normalize_xsa
from mcps.zynq_mcp.domains.platform import platform_atoms
from mcps.zynq_mcp.domains.platform.platform_atoms import (
    platform_export_hardware,
)


def _make_xsa(path: str, entries, date_time=(2023, 1, 1, 12, 0, 0),
              comment=b"vivado"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.comment = comment
            z.writestr(info, data)


class TestNormalize:
    def test_same_content_different_timestamps_normalize_equal(self, tmp_path):
        a = str(tmp_path / "a.xsa")
        b = str(tmp_path / "b.xsa")
        entries = [("design_1.hwh", b"hwh-content"),
                   ("ps7_init.tcl", b"init-tcl"),
                   ("meta.json", b"{}")]
        _make_xsa(a, entries, date_time=(2024, 5, 6, 7, 8, 10))
        _make_xsa(b, entries, date_time=(2021, 2, 3, 4, 5, 6))
        normalize_xsa(a)
        normalize_xsa(b)
        assert Path(a).read_bytes() == Path(b).read_bytes()

    def test_same_content_different_order_normalize_equal(self, tmp_path):
        a = str(tmp_path / "a.xsa")
        b = str(tmp_path / "b.xsa")
        _make_xsa(a, [("x.hwh", b"1"), ("y.tcl", b"2")])
        _make_xsa(b, [("y.tcl", b"2"), ("x.hwh", b"1")])
        normalize_xsa(a)
        normalize_xsa(b)
        assert Path(a).read_bytes() == Path(b).read_bytes()

    def test_normalize_idempotent(self, tmp_path):
        p = str(tmp_path / "x.xsa")
        _make_xsa(p, [("a.hwh", b"payload")])
        normalize_xsa(p)
        first = Path(p).read_bytes()
        normalize_xsa(p)
        assert Path(p).read_bytes() == first

    def test_normalize_preserves_content(self, tmp_path):
        p = str(tmp_path / "x.xsa")
        _make_xsa(p, [("a.hwh", b"payload-1"), ("b/", b""),
                      ("b/c.tcl", b"payload-2")])
        normalize_xsa(p)
        with zipfile.ZipFile(p) as z:
            assert z.read("a.hwh") == b"payload-1"
            assert z.read("b/c.tcl") == b"payload-2"

    def test_normalize_noop_on_missing_file(self, tmp_path):
        normalize_xsa(str(tmp_path / "absent.xsa"))  # must not raise

    # ── B13-F6 修复轮#7: 成员内容中的生成时间戳也必须归一 ──────────
    # 白盒真板证据: 同 BD 三次导出 SHA256 两两不同, xsa_diff 显示仅
    # xsa.json 的 generatedTimestamp 与 xsa.xml 的 GenAppInfo/@TimeStamp
    # 每次变化——只归一 zip 层不够。

    def _ts_entries(self, ts):
        return [
            ("xsa.json",
             (b'{"name": "p", "generatedTimestamp": "' + ts.encode()
              + b'", "n": 1}')),
            ("xsa.xml",
             (b'<?xml version="1.0"?><GenAppInfo App="x" '
              b'TimeStamp="' + ts.encode() + b'"/>')),
            ("design.hwh", b"same-hwh-content"),
        ]

    def test_member_timestamps_normalize_equal(self, tmp_path):
        a = str(tmp_path / "a.xsa")
        b = str(tmp_path / "b.xsa")
        _make_xsa(a, self._ts_entries("Fri Sep  4 23:23:52 2026"),
                  date_time=(2024, 9, 4, 23, 23, 52))
        _make_xsa(b, self._ts_entries("Fri Sep  4 23:23:55 2026"),
                  date_time=(2024, 9, 4, 23, 23, 55))
        normalize_xsa(a)
        normalize_xsa(b)
        assert Path(a).read_bytes() == Path(b).read_bytes()

    def test_member_timestamps_fixed_in_content(self, tmp_path):
        p = str(tmp_path / "x.xsa")
        _make_xsa(p, self._ts_entries("Fri Sep  4 23:23:52 2026"))
        normalize_xsa(p)
        with zipfile.ZipFile(p) as z:
            js = z.read("xsa.json").decode("utf-8")
            xml = z.read("xsa.xml").decode("utf-8")
        assert '"generatedTimestamp":"1980-01-01 00:00:00"' in js
        assert "Fri Sep" not in js
        assert 'TimeStamp="1980-01-01 00:00:00"' in xml
        assert "Fri Sep" not in xml
        assert "same-hwh-content" not in (js + xml)  # other members untouched

    def test_unparseable_members_pass_through(self, tmp_path):
        p = str(tmp_path / "x.xsa")
        _make_xsa(p, [("xsa.json", b"{not valid json"),
                      ("xsa.xml", b"<not xml")])
        normalize_xsa(p)  # must not raise
        with zipfile.ZipFile(p) as z:
            assert z.read("xsa.json") == b"{not valid json"
            assert z.read("xsa.xml") == b"<not xml"


class _FakeAdapter:
    """Minimal adapter: _run_tcl is monkeypatched in the atom test."""

    def __init__(self):
        self.calls = []

    async def call_tool(self, *a, **kw):  # pragma: no cover - unused
        raise AssertionError("_run_tcl must be monkeypatched")


class TestAtomWiring:
    def test_export_hardware_deterministic_sha(self, tmp_path, monkeypatch):
        out = str(tmp_path / "platform.xsa")
        call_count = {"n": 0}

        async def _fake_run_tcl(adapter, tcl, label):
            # Simulate Vivado emitting non-deterministic bytes each run —
            # zip-entry timestamps AND member-content timestamps (F6).
            call_count["n"] += 1
            ts = f"run-{call_count['n']}"
            entries = [("design.hwh", b"same-content"),
                       ("xsa.json", b'{"generatedTimestamp": "' +
                        ts.encode() + b'"}'),
                       ("xsa.xml", b'<GenAppInfo TimeStamp="' +
                        ts.encode() + b'"/>')]
            _make_xsa(out, entries,
                      date_time=(2020 + call_count["n"], 1, 1, 0, 0, 0))

        monkeypatch.setattr(platform_atoms, "_run_tcl", _fake_run_tcl)
        r1 = asyncio.run(platform_export_hardware(_FakeAdapter(), path=out))
        sha1 = r1["data"]["xsa_sha256"]
        r2 = asyncio.run(platform_export_hardware(_FakeAdapter(), path=out))
        sha2 = r2["data"]["xsa_sha256"]
        assert sha1 == sha2  # same content → same normalized bytes → same sha
