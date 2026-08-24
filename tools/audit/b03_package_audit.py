"""b03_package_audit.py — Dev-time board package audit (B12-B03 erratum).

The B03 runtime "directory seal" (directory content must exactly equal the
manifest file list) and the freeze-discipline SHA cross-reference table were
retired from the hot path (``create_session`` → ``board_profile_load``) in the
B12-B03 contract simplification. They are now dev-time only.

This script manually re-runs the FULL package validation
(``validate_package_full`` via ``check_package_integrity``) against a board
package directory and reports every drift / extra file / missing file / SHA
mismatch. It does NOT modify anything.

Usage (from the project root):
    python tools/audit/b03_package_audit.py [package_dir]

If ``package_dir`` is omitted, defaults to ``boards/ALINX_AX7020_v1.0/``.

Exit codes:
    0 — package is consistent with its manifest (no issues)
    1 — issues found (drift / extra / missing / SHA mismatch)
    2 — usage or I/O error (package missing, unreadable manifest, etc.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcps.common.board_package import check_package_integrity  # noqa: E402


def _fmt_issue(i) -> str:
    parts = [f"[{i.code}]"]
    if getattr(i, "field", None):
        parts.append(f"field={i.field}")
    if getattr(i, "expected", None) is not None:
        parts.append(f"expected={i.expected!r}")
    if getattr(i, "actual", None) is not None:
        parts.append(f"actual={i.actual!r}")
    return " ".join(parts)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: python tools/audit/b03_package_audit.py [package_dir]",
              file=sys.stderr)
        return 2

    package_dir = argv[1] if len(argv) == 2 else str(
        _PROJECT_ROOT / "boards" / "ALINX_AX7020_v1.0")

    if not os.path.isdir(package_dir):
        print(f"ERROR: package directory not found: {package_dir}", file=sys.stderr)
        return 2

    print(f"B03 package audit: {package_dir}")
    try:
        issues = check_package_integrity(package_dir)
    except Exception as e:  # noqa: BLE001 — report any load failure as an audit error
        print(f"ERROR: audit raised {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    if not issues:
        print("CLEAN: package directory matches its manifest and SHA table.")
        return 0

    print(f"ISSUES FOUND: {len(issues)}")
    for i in issues:
        print(f"  - {_fmt_issue(i)}")
    print("\nResolve by updating the package manifest and re-freezing, or by "
          "removing the offending files (dev-time decision).")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
