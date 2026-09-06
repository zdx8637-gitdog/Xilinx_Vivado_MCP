"""已知问题运行时附注（修复轮 #12：响应附注机制）。

两层数据源：
- 通用层：本包内 ``known_issues.json``（框架资产，零项目特化词，随回流
  审查更新）；
- 项目层：``<PROJECT_PATH>/evidence/hints.json``（智能体发现即落盘，
  每次调用时读取——即写即生效，无需重启）。

匹配只在响应文本上做正则命中的附注（独立 annotations 字段），**绝不修改
任何原始字段、绝不自动修复**——判定仍以原始输出为准。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_GENERIC_HINTS = Path(__file__).with_name("known_issues.json")
_REQUIRED_FIELDS = ("symptom_pattern", "advice", "source_ref", "added_at")
MAX_ANNOTATIONS = 5


def _load_json(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("hints", []) if isinstance(data, dict) else []
    return [e for e in entries if isinstance(e, dict)]


def validate_hints(entries: list[dict]) -> None:
    """格式门禁：每个条目必填字段齐全且 symptom_pattern 可编译为正则，
    否则抛 ValueError（fail-closed，坏条目不得静默跳过）。"""
    for i, e in enumerate(entries):
        for field in _REQUIRED_FIELDS:
            if not isinstance(e.get(field), str) or not e[field].strip():
                raise ValueError(f"hint #{i} missing/non-string field: {field}")
        try:
            re.compile(e["symptom_pattern"])
        except re.error as exc:
            raise ValueError(f"hint #{i} invalid symptom_pattern: {exc}") from exc
        if not isinstance(e.get("hint_id"), str) or not e["hint_id"].strip():
            raise ValueError(f"hint #{i} missing hint_id")


def load_hints(project_path: str | None = None) -> list[dict]:
    """加载通用层 + 项目层条目（项目层追加在后，命中即并列列出）。"""
    entries: list[dict] = []
    for path in (_GENERIC_HINTS,
                 Path(project_path) / "evidence" / "hints.json"
                 if project_path else None):
        if path is None:
            continue
        try:
            entries.extend(_load_json(path))
        except (OSError, json.JSONDecodeError):
            # 项目层文件损坏不能拖垮框架响应——坏文件跳过但通用层仍生效。
            # 格式问题由 validate_hints 的格式门禁测试在框架侧兜底。
            continue
    validate_hints(entries)
    return entries


def match_annotations(entries: list[dict], response_text: str,
                      limit: int = MAX_ANNOTATIONS) -> list[dict]:
    """对响应文本做症状正则匹配，返回附注列表（有上限）。

    返回 [{hint_id, advice, source_ref}, ...]，绝不改动 response_text 本身。
    """
    hits: list[dict] = []
    for e in entries:
        try:
            rx = re.compile(e["symptom_pattern"], re.IGNORECASE)
        except re.error:
            continue
        if rx.search(response_text):
            hits.append({"hint_id": e["hint_id"], "advice": e["advice"],
                         "source_ref": e["source_ref"]})
            if len(hits) >= limit:
                break
    return hits


__all__: list[str] = ["load_hints", "validate_hints", "match_annotations",
                       "MAX_ANNOTATIONS"]
