"""xsa_normalize.py — B13-M3: deterministic XSA normalization.

Vivado's ``write_hw_platform`` emits a zip whose entry timestamps (and
possibly order/extra fields) vary per run, so the raw XSA bytes — and the
platform manifest revision derived from them — drift on every re-export even
when the Block Design content is unchanged (real-board evidence: platform
manifest 307130c4 -> 6bf2e166 with a structurally identical BD).

B13-F6 (修复轮#7): normalizing the ZIP LAYER alone is not enough — two
MEMBERS embed generation timestamps in their content, which also vary per
export (white-box evidence: three consecutive exports of the same BD gave
three different SHA256, with xsa_diff showing only xsa.json/xsa.xml
differing):
  - ``xsa.json``: ``"generatedTimestamp": "Fri Sep  4 23:23:52 2026"``
  - ``xsa.xml``:  ``<GenAppInfo ... TimeStamp="Fri Sep  4 23:23:55 2026"/>``

Re-pack the zip deterministically so content-equivalent exports are
byte-identical:
- entries written in sorted-name order,
- every entry timestamp fixed to the zip epoch (1980-01-01 00:00:00),
- comment / create_system / extra fields stripped,
- fixed compression (DEFLATE) for all entries,
- member-content normalization: ``generatedTimestamp`` in xsa.json and
  ``GenAppInfo/@TimeStamp`` in xsa.xml replaced with a fixed value (both
  re-serialized deterministically).

This runs as a post-processing step in ``platform_export_hardware``; the
manifest revision therefore depends on CONTENT only.
"""
import json
import os
import xml.etree.ElementTree as ET
import zipfile

_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_FIXED_TIMESTAMP = "1980-01-01 00:00:00"


def _normalize_xsa_json(data: bytes) -> bytes:
    """Replace every ``generatedTimestamp`` string value with the fixed
    timestamp and re-serialize deterministically (sorted keys, no extra
    whitespace). Unparseable JSON is passed through untouched."""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data

    def _walk(node):
        if isinstance(node, dict):
            for k in list(node.keys()):
                if k == "generatedTimestamp" and isinstance(node[k], str):
                    node[k] = _FIXED_TIMESTAMP
                else:
                    _walk(node[k])
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_xsa_xml(data: bytes) -> bytes:
    """Fix ``GenAppInfo/@TimeStamp`` (the only content field that varies
    per export in the white-box evidence) and re-serialize with
    xml.etree (deterministic per input tree). Unparseable XML passes
    through untouched."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "GenAppInfo" and \
                "TimeStamp" in el.attrib:
            el.set("TimeStamp", _FIXED_TIMESTAMP)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def normalize_xsa(path: str) -> None:
    """In-place deterministic re-pack of an XSA zip. No-op if not a file."""
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return
    tmp = path + ".norm.tmp"
    try:
        with zipfile.ZipFile(path, "r") as zin:
            names = sorted(zin.namelist())
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for name in names:
                    info = zin.getinfo(name)
                    data = zin.read(name)
                    if name.endswith("xsa.json"):
                        data = _normalize_xsa_json(data)
                    elif name.endswith("xsa.xml"):
                        data = _normalize_xsa_xml(data)
                    info.date_time = _FIXED_DATE_TIME
                    info.comment = b""
                    info.extra = b""
                    info.create_system = 0
                    if name.endswith("/"):
                        info.external_attr = (0o40775 << 16) | 0x10
                    else:
                        info.external_attr = (0o100644 << 16)
                    # writestr recomputes CRC/file_size/compress_size from
                    # the (possibly normalized) data.
                    zout.writestr(info, data)
        os.replace(tmp, path)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
