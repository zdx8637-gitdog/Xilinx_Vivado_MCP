"""xsa_normalize.py — B13-M3: deterministic XSA normalization.

Vivado's ``write_hw_platform`` emits a zip whose entry timestamps (and
possibly order/extra fields) vary per run, so the raw XSA bytes — and the
platform manifest revision derived from them — drift on every re-export even
when the Block Design content is unchanged (real-board evidence: platform
manifest 307130c4 -> 6bf2e166 with a structurally identical BD).

Re-pack the zip deterministically so content-equivalent exports are
byte-identical:
- entries written in sorted-name order,
- every entry timestamp fixed to the zip epoch (1980-01-01 00:00:00),
- comment / create_system / extra fields stripped,
- fixed compression (DEFLATE) for all entries.

This runs as a post-processing step in ``platform_export_hardware``; the
manifest revision therefore depends on CONTENT only.
"""
import os
import zipfile

_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


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
                    info.date_time = _FIXED_DATE_TIME
                    info.comment = b""
                    info.extra = b""
                    info.create_system = 0
                    if name.endswith("/"):
                        info.external_attr = (0o40775 << 16) | 0x10
                    else:
                        info.external_attr = (0o100644 << 16)
                    zout.writestr(info, data)
        os.replace(tmp, path)
    finally:
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
