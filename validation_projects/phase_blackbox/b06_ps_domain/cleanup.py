"""Cleanup script for B06 PS Domain black-box runner evidence. v1.0.0
Removes evidence/<run_id> directories. Dry-run by default. --execute to delete."""
import argparse
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_VALID_RUN_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')


def _validate_run_id(rid):
    if not isinstance(rid, str) or not rid:
        return False
    if rid in (".", ".."):
        return False
    if "/" in rid or "\\" in rid:
        return False
    if os.path.isabs(rid):
        return False
    if not _VALID_RUN_ID.match(rid):
        return False
    return True


def main():
    p = argparse.ArgumentParser(description="B06 Cleanup")
    p.add_argument("--run-id", required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()

    if not _validate_run_id(args.run_id):
        print(f"ERROR: invalid run_id", file=sys.stderr)
        sys.exit(1)

    target = os.path.join(HERE, "evidence", args.run_id)
    if not os.path.isdir(target):
        print(f"Not found: {target}")
        sys.exit(0)

    summary_path = os.path.join(target, "summary.json")
    items = []
    for entry in sorted(os.listdir(target)):
        full = os.path.join(target, entry)
        if full != summary_path:
            items.append(full)
    if os.path.isfile(summary_path):
        items.append(summary_path)

    if args.execute:
        for item in items:
            if os.path.isdir(item):
                shutil.rmtree(item, ignore_errors=False)
            elif os.path.isfile(item):
                os.remove(item)
        try:
            os.rmdir(target)
        except Exception:
            pass
        print(f"[CLEANED] {args.run_id}")
    else:
        print(f"[DRY-RUN] Would clean {args.run_id} ({len(items)} items)")
        for i in sorted(items):
            print(f"  {os.path.relpath(i, HERE)}")


if __name__ == "__main__":
    main()
