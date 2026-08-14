"""
Cleanup script for R3.1-C smoke runner evidence. v8.0.0
Requires --manifest. Uses shared _provenance module.
Calls validate_all_identity_bindings_reporting for full contract enforcement.
Atomically scans for junctions/symlinks before any deletion.
Default: dry-run. Requires --execute for actual deletion.
"""
import argparse, json, os, shutil, sys

_sys_path_here = os.path.dirname(os.path.abspath(__file__))
if _sys_path_here not in sys.path:
    sys.path.insert(0, _sys_path_here)
from _provenance import (
    _validate_run_id, _strict_within, _is_symlink_or_junction,
    _scan_for_symlinks_or_junctions, _sha256_file,
    validate_manifest_provenance, validate_ee_shas,
    validate_all_identity_bindings_reporting,
)


def main():
    parser = argparse.ArgumentParser(description="R3.1-C Smoke Cleanup v8")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute", action="store_true", default=False,
                        help="Actually perform deletion (default: dry-run only)")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    # Validate run_id
    ok_rid, reason_rid = _validate_run_id(args.run_id)
    if not ok_rid:
        print(f"ERROR: invalid run_id: {reason_rid}", file=sys.stderr); sys.exit(1)

    # Load manifest
    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr); sys.exit(1)
    with open(args.manifest, "r") as f:
        manifest = json.load(f)

    # ── Unified provenance ──
    ok_prov, reason_prov = validate_manifest_provenance(manifest, args.manifest, exit_on_fail=False)
    if not ok_prov:
        print(f"ERROR: manifest provenance invalid: {reason_prov}", file=sys.stderr); sys.exit(1)

    # ── Validate SHAs ──
    try:
        validate_ee_shas(manifest)
    except SystemExit:
        print("ERROR: effective_expected_shas validation failed", file=sys.stderr); sys.exit(1)

    # ── Unified identity/signing validation — single call replaces all local checks ──
    eed_real = os.path.realpath(manifest["effective_expected_dir"])
    ee_shas = manifest["effective_expected_shas"]
    # post_execution=True: uses SCENARIO_POST_EXEC_STAGES (success may have PL_BUILD)
    all_ok, bound_results = validate_all_identity_bindings_reporting(
        manifest, eed_real, ee_shas, post_execution=True)
    if not all_ok:
        errors = []
        for sc, (ok, errs, _, _) in bound_results.items():
            if not ok:
                for e in errs:
                    errors.append(f"  {sc}: {e}")
        print("ERROR: receipt/contract validation failed:", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        print("All files preserved — no deletion.", file=sys.stderr)
        sys.exit(1)

    aer_real = os.path.realpath(manifest["approved_evidence_root"])
    if not os.path.isdir(aer_real):
        summary_detail = {"overall": "NOT_FOUND", "evidence_base": aer_real}
        print(json.dumps(summary_detail, indent=2))
        sys.exit(0)

    target = os.path.join(aer_real, args.run_id)
    target_real = os.path.realpath(target)

    if not os.path.isdir(target_real):
        print(f"run_id directory not found: {target_real}")
        summary_detail = {"overall": "NOT_FOUND", "run_id": args.run_id,
                         "evidence_base": aer_real, "target": target_real}
        print(json.dumps(summary_detail, indent=2))
        sys.exit(0)

    # Path containment
    if not _strict_within(aer_real, target_real):
        print(f"ERROR: target not strictly within evidence-base: {target_real}", file=sys.stderr); sys.exit(1)

    # Basename match
    if os.path.basename(target_real) != args.run_id:
        print(f"ERROR: evidence dir basename mismatch", file=sys.stderr); sys.exit(1)

    # Summary.json exists and run_id matches
    summary_path = os.path.join(target_real, "summary.json")
    if not os.path.isfile(summary_path):
        print("ERROR: summary.json not found in evidence directory", file=sys.stderr); sys.exit(1)
    with open(summary_path, "r") as f:
        summary = json.load(f)
    if summary.get("run_id", "") != args.run_id:
        print(f"ERROR: summary run_id mismatch: {summary.get('run_id')!r} != {args.run_id!r}",
              file=sys.stderr); sys.exit(1)

    # Atomic scan for ALL symlinks/junctions BEFORE any deletion
    symlink_errors = _scan_for_symlinks_or_junctions(target_real)
    if symlink_errors:
        for se in symlink_errors:
            print(f"ERROR: symlink/junction found in evidence: {se}", file=sys.stderr)
        print("All files preserved — no partial deletion.", file=sys.stderr)
        sys.exit(1)

    # Collect items to clean
    items = []
    for entry in os.listdir(target_real):
        full = os.path.join(target_real, entry)
        if full != summary_path:
            items.append(full)
    items.append(summary_path)

    summary_detail = {
        "overall": "DRY_RUN" if not args.execute else "CLEANED",
        "run_id": args.run_id,
        "manifest": os.path.abspath(args.manifest),
        "evidence_base": aer_real,
        "target": target_real,
        "items": sorted([os.path.basename(i) if i != summary_path else "summary.json" for i in items]),
        "item_count": len(items),
    }

    if args.execute:
        try:
            for item in items:
                if os.path.isdir(item):
                    shutil.rmtree(item, ignore_errors=False)
                elif os.path.isfile(item):
                    os.remove(item)
            try:
                os.rmdir(target_real)
            except OSError:
                pass
            summary_detail["overall"] = "CLEANED"
            print(f"[CLEANED] {args.run_id}")
        except Exception as e:
            summary_detail["overall"] = "ERROR"
            summary_detail["error"] = str(e)
            print(f"ERROR during cleanup: {e}", file=sys.stderr)
            print(json.dumps(summary_detail, indent=2))
            if args.output:
                with open(args.output, "w") as f: json.dump(summary_detail, f, indent=2)
            sys.exit(1)
    else:
        summary_detail["overall"] = "DRY_RUN"
        print(f"[DRY-RUN] Would clean {args.run_id} ({len(items)} items)")

    print(json.dumps(summary_detail, indent=2))
    if args.output:
        with open(args.output, "w") as f: json.dump(summary_detail, f, indent=2)
    print("Note: only the specified run_id directory under the manifest-declared evidence root is cleaned.")
    sys.exit(0)


if __name__ == "__main__":
    main()
