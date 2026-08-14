"""
Manager-only readiness verifier for R3.1-C. v9.0.0
Uses shared _provenance module for unified validation.
Calls validate_all_identity_bindings_reporting for full contract enforcement.
FAIL-CLOSED on all provenance, SHA, identity, artifact, fixture, and ledger checks.
"""
import argparse, json, os, sys

_prov_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "r3_1c_smoke"))
if _prov_dir not in sys.path:
    sys.path.insert(0, _prov_dir)
from _provenance import (
    HARNESS_VERSION, REQUIRED_SCENARIOS, SET_REQUIRED, SHA256_RE,
    EXPECTED_INITIAL_LANE, EXPECTED_WORKER_STATE, EXPECTED_WORKER_PID,
    _sha256_file, _strict_within, _is_symlink_or_junction,
    validate_manifest_provenance, validate_ee_shas,
    validate_all_identity_bindings_reporting,
)


def verify(manifest_path, approved_base=None):
    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    # ── Unified provenance ──
    ok, reason = validate_manifest_provenance(manifest, manifest_path, exit_on_fail=False)
    if not ok:
        return {"overall": "FAIL", "error": f"provenance: {reason}", "scenario_results": {}}

    run_root_real = os.path.realpath(manifest["run_root"])

    if approved_base:
        approved = os.path.realpath(approved_base)
    else:
        approved = os.path.realpath(manifest.get("base_dir", os.path.dirname(run_root_real)))
    if not (_strict_within(approved, run_root_real) or approved == run_root_real):
        return {"overall": "FAIL", "error": f"run_root {run_root_real} not within approved base {approved}",
                "scenario_results": {}}

    report = {"verified_at": __import__('datetime').datetime.now(
              __import__('datetime').timezone.utc).isoformat(),
              "manifest": manifest_path, "approved_base": approved,
              "scenario_results": {}, "overall": "PASS"}

    # ── Validate SHAs ──
    try:
        validate_ee_shas(manifest)
    except SystemExit:
        report["overall"] = "FAIL"; report["error"] = "effective_expected_shas validation failed"
        return report
    ee_shas = manifest["effective_expected_shas"]
    eed_real = os.path.realpath(manifest["effective_expected_dir"])

    # ── Unified reporting validator — single call replaces entire per-scenario loop ──
    all_ok, bound_results = validate_all_identity_bindings_reporting(manifest, eed_real, ee_shas)

    for name in REQUIRED_SCENARIOS:
        entry = manifest["scenarios"][name]
        result = {"status": "PASS", "checks": []}
        sc_ok, errors, receipt, ee_data = bound_results[name]

        if not sc_ok:
            result["status"] = "FAIL"
            for e in errors:
                result["checks"].append(e)
            report["scenario_results"][name] = result
            continue

        # Collect PASS-only checks for the structured report
        result["checks"].append("identity_bindings_ok")
        rts = os.path.realpath(entry["runtime_root"])
        pp = os.path.realpath(receipt["project_path"])

        # Ledger state (already validated by the shared validator; record friendly checks)
        lp = os.path.join(rts, "execution_ledger.json")
        try:
            ld = json.load(open(lp, "r"))
            ctx = ld.get("context", {}); wo = ld.get("worker", {})
            result["checks"].append(f"lane={ld.get('execution_lane')}")
            result["checks"].append(f"worker={wo.get('state')}")
            result["checks"].append("active_op=None" if ld.get("active_operation") is None else "active_op_present")
            result["checks"].append(f"stage={ctx.get('current_stage')}")
            result["checks"].append("session_id_match")
            result["checks"].append("board_id_match")
            result["checks"].append("project_path_match")
            result["checks"].append("rev_match")
        except Exception as e:
            result["status"] = "FAIL"
            result["checks"].append(f"ledger_report_error:{e}")

        # Effective expected SHA (already validated)
        result["checks"].append("effective_expected_sha_ok")
        result["checks"].append("entry_sha_ok")
        result["checks"].append("receipt_sha_ok")

        # Artifacts — re-read for friendly check names (already validated)
        for art in receipt.get("input_artifacts", []):
            rel = art.get("relative_path", "")
            if rel:
                result["checks"].append(f"sha_ok:{rel}")

        report["scenario_results"][name] = result

    for r in report["scenario_results"].values():
        if r["status"] == "FAIL":
            report["overall"] = "FAIL"; break
    if len(report["scenario_results"]) != 5:
        report["overall"] = "FAIL"
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--approved-base", default=None)
    p.add_argument("--output", default=None)
    a = p.parse_args()
    r = verify(a.manifest, a.approved_base)
    print(json.dumps(r, indent=2, default=str))
    if a.output:
        with open(a.output, "w") as f: f.write(json.dumps(r, indent=2, default=str))
    sys.exit(0 if r["overall"] == "PASS" else 1)


if __name__ == "__main__":
    main()
