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


def _human_range(value: str) -> str:
    """RANGE 值人性化（hwh 惯例: 4K/64K/1G）。整除 1K 用 K，否则原样。"""
    try:
        n = int(value, 16) if str(value).lower().startswith("0x") \
            else int(value)
    except ValueError:
        return str(value)
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}K"
    return str(n)


def _addressing_fragment(address_map: dict) -> str:
    """从 manifest 形状的 address_map（{ip: {base, range, master}}）生成
    hwh 的 <ADDRESSING> XML 片段。

    schema 经真板验证（Vitis 2023.1 hsi 接受，loadhw rc=0 + 随后 DAP 可读
    PL 寄存器）：ADDRESS_SPACE 按 master 分组（NAME=<cell>/Data、
    MASTERBUSINTERFACE=<bus>），每段 SEGMENT NAME=SEG_<ip>_Reg +
    ADDRESS_MAP ABS/RANGE。空 map → None（不注入）。"""
    if not isinstance(address_map, dict) or not address_map:
        return None
    spaces = {}
    for ip, entry in address_map.items():
        if not isinstance(entry, dict):
            continue
        base = str(entry.get("base", ""))
        rng = str(entry.get("range", ""))
        master = str(entry.get("master", ""))
        if not base or not rng or not master:
            continue
        spaces.setdefault(master, []).append((ip, base, rng))
    if not spaces:
        return None
    parts = ["<ADDRESSING>"]
    for master, segs in spaces.items():
        cell = master.split("/")[0]
        bus = master.split("/", 1)[1] if "/" in master else master
        # 单个 master 的地址空间范围取覆盖区间的并集（上取整到 1G 边界）。
        bases = [int(s[1], 16) for s in segs]
        ends = [int(s[1], 16) + int(s[2], 16) for s in segs]
        begin, end = min(bases), max(ends)
        parts.append(
            f'<ADDRESS_SPACE RANGE="1G" MASTERBUSINTERFACE="{bus}" '
            f'BASENAME="Data" NAME="{cell}/Data" '
            f'BEGIN="{hex(begin)}" END="{hex(end)}">')
        for ip, base, rng in sorted(segs):
            parts.append(
                f'<SEGMENT NAME="SEG_{ip}_Reg">'
                f'<ADDRESS_MAP ABS="{base}" RANGE="{_human_range(rng)}" '
                f'USAGE="register" DELTAMAP="0x0" SUBTYPE="" '
                f'OFFSET="0x0" GAP="0x00000000"/>'
                f'</SEGMENT>')
        parts.append("</ADDRESS_SPACE>")
    parts.append("</ADDRESSING>")
    return "".join(parts)


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


def normalize_xsa(path: str, addressing_map: dict | None = None) -> None:
    """In-place deterministic re-pack of an XSA zip. No-op if not a file.

    ``addressing_map`` (B13-F8 修复轮#8/#10): manifest 形状的
    {ip: {base, range, master}}。Vivado 2023.1 的 write_hw_platform /
    write_hwdef **不输出 ADDRESSING 段**（真 Vivado 探针实证）→ hsi 调试
    映射非确定、dow 间歇受阻。提供 map 时向主 hwh 注入合成 <ADDRESSING>
    （schema 经真板 hsi 验证），loadhw 后 DAP 可确定读 PL 寄存器。
    """
    if not isinstance(path, str) or not path or not os.path.isfile(path):
        return
    fragment = _addressing_fragment(addressing_map)
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
                    elif (fragment and name.endswith(".hwh")
                          and "smc" not in name):
                        hwh = data.decode("utf-8", "replace")
                        if "<ADDRESSING" not in hwh and "</HWH>" in hwh:
                            hwh = hwh.replace(
                                "</HWH>", fragment + "</HWH>", 1)
                            data = hwh.encode("utf-8")
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
