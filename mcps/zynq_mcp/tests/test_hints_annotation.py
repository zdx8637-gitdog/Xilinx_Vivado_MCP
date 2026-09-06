"""修复轮 #12: 响应附注机制 + 两层 hints 格式门禁。

- 通用层 known_issues.json（框架资产，零项目特化词）+ 项目层
  <PROJECT_PATH>/evidence/hints.json（即写即生效）。
- 命中症状正则 → 响应 JSON 顶层独立 annotations 字段；原始字段原样不动。
"""
import json

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio(loop_scope="function")

from mcps.common import hints
from mcps.common.hints import load_hints, match_annotations, validate_hints


class TestFormatGate:
    async def test_generic_hints_validate(self):
        entries = load_hints()
        assert len(entries) >= 2
        validate_hints(entries)  # 不抛即过

    async def test_missing_field_raises(self):
        bad = [{"symptom_pattern": "X", "advice": "do this",
                "source_ref": "s", "added_at": "t", "hint_id": "h1"},
               {"symptom_pattern": "Y", "source_ref": "s",
                "added_at": "t", "hint_id": "h2"}]  # advice 缺失
        with pytest.raises(ValueError, match="advice"):
            validate_hints(bad)

    async def test_invalid_regex_raises(self):
        bad = [{"symptom_pattern": "(unclosed", "advice": "a",
                "source_ref": "s", "added_at": "t", "hint_id": "h1"}]
        with pytest.raises(ValueError, match="symptom_pattern"):
            validate_hints(bad)

    async def test_generic_hints_zero_project_terms(self):
        """泛化红线自查：通用层 hints 不得携带当前项目特化词。"""
        raw = json.loads(
            open(hints._GENERIC_HINTS, encoding="utf-8").read())
        text = json.dumps(raw, ensure_ascii=False)
        banned = ["B13", "B12", "AX7020", "ALINX", "AD7606", "agent1",
                  "b13_engine", "receiver.py", "uart_cmd"]
        assert all(word not in text for word in banned)


class TestMatching:
    async def test_hit_case_insensitive(self):
        entries = [{"hint_id": "h1", "symptom_pattern": "mem_read_no_data",
                    "advice": "check mapping", "source_ref": "§13.1",
                    "added_at": "t"}]
        ann = match_annotations(entries, '{"error": "MEM_READ_NO_DATA hit"}')
        assert ann == [{"hint_id": "h1", "advice": "check mapping",
                        "source_ref": "§13.1"}]

    async def test_no_hit_returns_empty(self):
        ann = match_annotations(
            [{"hint_id": "h1", "symptom_pattern": "NEVER_MATCHES",
              "advice": "a", "source_ref": "s", "added_at": "t"}],
            "all good")
        assert ann == []

    async def test_multi_hit_capped(self):
        entries = [{"hint_id": f"h{i}", "symptom_pattern": f"SYM{i}",
                    "advice": "a", "source_ref": "s", "added_at": "t"}
                   for i in range(8)]
        text = " ".join(f"SYM{i}" for i in range(8))
        ann = match_annotations(entries, text, limit=hints.MAX_ANNOTATIONS)
        assert len(ann) == hints.MAX_ANNOTATIONS
        assert [a["hint_id"] for a in ann] == [f"h{i}" for i in range(5)]

    async def test_project_hints_merged(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "evidence").mkdir(parents=True)
        (proj / "evidence" / "hints.json").write_text(
            json.dumps({"hints": [
                {"hint_id": "proj-1", "symptom_pattern": "PROJ_SYMPTOM",
                 "advice": "project advice", "source_ref": "F-99",
                 "added_at": "t"}]}), encoding="utf-8")
        entries = load_hints(str(proj))
        assert any(e["hint_id"] == "proj-1" for e in entries)
        # 通用层条目仍在前
        assert any(e["hint_id"].startswith("hint-") for e in entries)

    async def test_corrupt_project_hints_ignored(self, tmp_path):
        proj = tmp_path / "proj"
        (proj / "evidence").mkdir(parents=True)
        (proj / "evidence" / "hints.json").write_text(
            "{not json", encoding="utf-8")
        entries = load_hints(str(proj))  # 不抛；通用层仍生效
        assert any(e["hint_id"].startswith("hint-") for e in entries)


class TestDispatcherAnnotate:
    def _disp(self, project_path=None):
        from mcps.zynq_mcp.dispatcher import ZynqDispatcher

        class _Ledger:
            context = {"project_path": project_path} if project_path else {}

        class _Guard:
            workspace_id = "ws-annotate"

        d = ZynqDispatcher.__new__(ZynqDispatcher)
        d._ledger = _Ledger()
        d._guard = _Guard()
        return d

    async def test_error_response_gets_annotations(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # 避免真实运行目录存在 evidence/hints.json
        from mcp.types import TextContent
        disp = self._disp()
        payload = {"status": "error",
                   "error": {"code": "MEM_READ_NO_DATA",
                             "message": "read returned nothing"}}
        contents = [TextContent(type="text", text=json.dumps(payload))]
        out = disp.annotate(contents)
        obj = json.loads(out[0].text)
        assert "annotations" in obj
        assert obj["annotations"][0]["hint_id"] == "hint-ps-mem-no-data"
        # 原始字段原样
        assert obj["error"] == payload["error"]
        assert obj["status"] == "error"
        assert "data" not in obj

    async def test_no_hit_leaves_text_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from mcp.types import TextContent
        disp = self._disp()
        raw = json.dumps({"status": "success", "data": {"x": 1}})
        contents = [TextContent(type="text", text=raw)]
        out = disp.annotate(contents)
        assert out[0].text == raw

    async def test_non_json_text_passthrough(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from mcp.types import TextContent
        disp = self._disp()
        contents = [TextContent(type="text", text="plain text MEM_READ_NO_DATA")]
        out = disp.annotate(contents)
        assert out[0].text == "plain text MEM_READ_NO_DATA"

    async def test_annotations_never_enter_result_field(self, monkeypatch,
                                                        tmp_path):
        """附注在顶层 annotations 字段，绝不混入被解析的 result 字段。"""
        monkeypatch.chdir(tmp_path)
        from mcp.types import TextContent
        disp = self._disp()
        payload = {"status": "success",
                   "data": {"result": {"status": "success",
                                       "data": {"text": "MEM_READ_NO_DATA"}}}}
        contents = [TextContent(type="text", text=json.dumps(payload))]
        out = disp.annotate(contents)
        obj = json.loads(out[0].text)
        assert obj["data"]["result"]["data"]["text"] == "MEM_READ_NO_DATA"
        assert "annotations" not in json.dumps(obj["data"]["result"])
