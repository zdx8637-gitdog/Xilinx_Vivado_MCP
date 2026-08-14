"""
Manager provisioning harness for R3.1-C phase black-box smoke. v4.0.0
Creates unique, immutable provisioned runtimes per invocation.
Generates effective expected contracts from checked-in templates.
Agent3 must NOT receive or run this file.
"""
import argparse, json, os, shutil, sys, uuid, hashlib
from pathlib import Path
from datetime import datetime, timezone

_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcps.zynq_mcp.control.instance_guard import InstanceGuard
from mcps.zynq_mcp.control.execution_ledger import (
    ledger_transaction, EXECUTION_LANE_IDLE, WORKER_STATE_ABSENT,
)
from mcps.zynq_mcp.control.workspace import resolve_workspace_root, compute_workspace_id

WSID = compute_workspace_id(resolve_workspace_root())
HARNESS_VERSION = "4.0.0"

SCENARIOS = {
    "capabilities": ("PL_GENERATE", "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"),
    "success": ("PL_GENERATE", "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"),
    "missing_revision": ("PL_GENERATE", ""),
    "wrong_stage": ("PLATFORM_DESIGN", "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"),
    "invalid_schema": ("PL_GENERATE", "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"),
}

TEMPLATE_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "r3_1c_smoke", "expected_outputs"))

PLACEHOLDER_SID = "PLACEHOLDER_SID"
PLACEHOLDER_PROJ = "PLACEHOLDER_PROJ"
PLACEHOLDER_STAGE = "PLACEHOLDER_STAGE"


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
           f"{int(datetime.now(timezone.utc).timestamp()*1e6)%1000000:06d}Z"


def _sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return "sha256:" + h.hexdigest()


def _generate_effective_expected(scenario, session_id, project_path, expected_initial_stage,
                                  output_dir):
    """Read template expected JSON, substitute PLACEHOLDER_* in-memory, write effective.
    Returns (path, sha256). Raises if any PLACEHOLDER* remains after substitution."""
    template_path = os.path.join(TEMPLATE_DIR, f"{scenario}.json")
    if not os.path.isfile(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")
    with open(template_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Walk and substitute all PLACEHOLDER_* values in-memory
    substitutions = {
        PLACEHOLDER_SID: session_id,
        PLACEHOLDER_PROJ: project_path,
        PLACEHOLDER_STAGE: expected_initial_stage,
    }

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and v in substitutions:
                    obj[k] = substitutions[v]
                elif isinstance(v, str) and v.startswith("PLACEHOLDER_"):
                    raise ValueError(f"Unknown placeholder in {scenario}: {v}")
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                if isinstance(item, str) and item in substitutions:
                    obj[i] = substitutions[item]
                elif isinstance(item, str) and item.startswith("PLACEHOLDER_"):
                    raise ValueError(f"Unknown placeholder in {scenario}: {item}")
                else:
                    _walk(item)

    _walk(data)

    # Verify no PLACEHOLDER_* remains
    text = json.dumps(data)
    if "PLACEHOLDER_" in text:
        remaining = [line.strip() for line in text.splitlines() if "PLACEHOLDER_" in line]
        raise ValueError(
            f"PLACEHOLDER_* not fully substituted in {scenario}: {remaining}")

    # Validate structure
    if "scenario" not in data:
        raise ValueError(f"{scenario} effective expected missing 'scenario'")
    if data["scenario"] != scenario:
        raise ValueError(f"{scenario} effective expected scenario mismatch: {data['scenario']}")
    if "assertions" not in data or not isinstance(data["assertions"], list) or len(data["assertions"]) == 0:
        raise ValueError(f"{scenario} effective expected missing non-empty 'assertions' list")

    ids = [a.get("id") for a in data["assertions"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{scenario} effective expected has duplicate assertion ids")
    for a in data["assertions"]:
        if "id" not in a:
            raise ValueError(f"{scenario} effective expected assertion missing 'id'")
        if "expect" not in a and "expect_not_contains" not in a:
            raise ValueError(f"{scenario} effective expected assertion {a.get('id','?')} missing expect/expect_not_contains")

    out_path = os.path.join(output_dir, f"{scenario}.json")
    os.makedirs(output_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return out_path, _sha256(out_path)


def provision(provision_run_dir, scenario, fixtures_dir, platform_revision, current_stage,
              effective_expected_dir):
    """Create one immutable provisioned runtime. Fails if target exists."""
    rt_root = os.path.join(provision_run_dir, scenario, "_provisioned")
    if os.path.exists(rt_root):
        raise FileExistsError(f"Target already exists — provisioned runtimes are immutable: {rt_root}")
    rt = Path(rt_root)
    rt.mkdir(parents=True, exist_ok=False)

    proj = os.path.join(rt_root, "project")
    for d in ["manifests/platform", "hdl", "rtl"]:
        os.makedirs(os.path.join(proj, d), exist_ok=True)

    # Copy wrapper
    target = os.path.join(proj, "hdl", "bd_wrapper_realistic.v")
    shutil.copy(os.path.join(fixtures_dir, "bd_wrapper_realistic.v"), target)
    bd_wrapper_sha = _sha256(target)

    # Create dummy xsa
    xsa_path = os.path.join(proj, "platform.xsa")
    Path(xsa_path).write_text("dummy xsa content for R3.1-C smoke test")
    xsa_sha = _sha256(xsa_path)

    # Load and customise manifest template
    with open(os.path.join(fixtures_dir, "platform_manifest.json"), "r") as f:
        m = json.load(f)
    m = dict(m)
    rev_for_manifest = m["platform_revision"]
    m["bd_wrapper_path"] = "hdl/bd_wrapper_realistic.v"
    m["bd_wrapper_sha256"] = bd_wrapper_sha
    m["xsa_path"] = "platform.xsa"
    m["xsa_sha256"] = xsa_sha
    m["generated_at"] = _now_iso()

    from mcps.common.artifact_schema import _revision_to_filename
    filename = _revision_to_filename(rev_for_manifest)
    manifest_target = os.path.join(proj, "manifests", "platform", filename)
    os.makedirs(os.path.dirname(manifest_target), exist_ok=True)
    with open(manifest_target, "w") as f:
        json.dump(m, f)
    manifest_sha = _sha256(manifest_target)

    # Create ledger
    g = InstanceGuard(rt, WSID)
    g.determine_role()
    assert g.is_primary
    lp = rt / "execution_ledger.json"
    session_id = f"session-{uuid.uuid4().hex[:8]}"

    def _init(l):
        l.instance_id = g.instance_id; l.workspace_id = WSID
        l.execution_lane = EXECUTION_LANE_IDLE; l.primary_instance_id = g.instance_id
        l.worker["state"] = WORKER_STATE_ABSENT; l.worker["pid"] = None
        l.context = {
            "session_id": session_id, "board_id": "ALINX_AX7020_v1.0",
            "project_path": proj,
            "board_package_revision": "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7",
            "expected_board_revision": "sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7",
            "board_profile_sha256": "sha256:3c95da56a6a9264ef42b6902f184d7d01c7229eafa70d1061cfd24cc0af0c90a",
            "current_stage": current_stage, "platform_revision": platform_revision,
            "pl_revision": None, "ps_revision": None,
        }
        return l

    ledger = ledger_transaction(g, lp, _init)
    g.release_owner_lock()

    # Generate effective expected contract
    effective_path, effective_sha = _generate_effective_expected(
        scenario, session_id, os.path.abspath(proj), current_stage, effective_expected_dir)

    receipt = {
        "scenario": scenario, "runtime_root": os.path.abspath(rt_root),
        "project_path": os.path.abspath(proj),
        "session_id": session_id, "board_id": "ALINX_AX7020_v1.0",
        "expected_initial_stage": current_stage, "expected_initial_lane": "IDLE",
        "expected_worker_state": "ABSENT", "expected_worker_pid": None,
        "platform_revision": platform_revision,
        "platform_revision_public_expected": m["platform_revision"] if platform_revision else "",
        "input_artifacts": [
            {"relative_path": "hdl/bd_wrapper_realistic.v", "sha256": bd_wrapper_sha},
            {"relative_path": "platform.xsa", "sha256": xsa_sha},
            {"relative_path": os.path.join("manifests", "platform", filename), "sha256": manifest_sha},
        ],
        "fixture_provenance": {
            "source": os.path.abspath(fixtures_dir),
            "bd_wrapper_realistic.v": _sha256(os.path.join(fixtures_dir, "bd_wrapper_realistic.v")),
            "platform_manifest_template": _sha256(os.path.join(fixtures_dir, "platform_manifest.json")),
        },
        "effective_expected_sha256": effective_sha,
        "created_at": _now_iso(), "harness_version": HARNESS_VERSION,
        "notes": "PRECONDITIONED_SESSION — Agent3 observes via get_session_info/get_execution_state only."
    }
    with open(os.path.join(rt_root, "provision_receipt.json"), "w") as f:
        json.dump(receipt, f, indent=2)
    return os.path.abspath(rt_root), receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=["all","success","missing_revision","wrong_stage","invalid_schema"], default="all")
    parser.add_argument("--base-dir", required=True)
    args = parser.parse_args()

    fixtures_dir = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "r3_1c_smoke", "inputs", "b04_pl_ready"))
    assert os.path.isdir(fixtures_dir), f"Fixtures not found: {fixtures_dir}"
    assert os.path.isdir(TEMPLATE_DIR), f"Templates not found: {TEMPLATE_DIR}"

    base = os.path.abspath(args.base_dir)
    run_id = f"prov_{uuid.uuid4().hex[:12]}"
    run_dir = os.path.join(base, run_id)
    os.makedirs(run_dir, exist_ok=False)  # fail if exists

    # Approved evidence root — runner writes here
    approved_evidence_root = os.path.join(run_dir, "evidence")
    os.makedirs(approved_evidence_root, exist_ok=True)

    # Effective expected directory — generated contracts live here
    effective_expected_dir = os.path.join(run_dir, "effective_expected")
    os.makedirs(effective_expected_dir, exist_ok=True)

    targets = list(SCENARIOS.keys()) if args.scenario == "all" else [args.scenario]
    entries = {}
    expected_shas = {}
    for sc in targets:
        stage, rev = SCENARIOS[sc]
        rt_root, receipt = provision(run_dir, sc, fixtures_dir, rev, stage, effective_expected_dir)
        print(f"[PROVISIONED] {sc} -> {rt_root}")
        entries[sc] = {
            "scenario": sc, "runtime_root": rt_root,
            "receipt_path": os.path.join(rt_root, "provision_receipt.json"),
            "expected_stage": stage,
            "effective_expected_sha256": receipt["effective_expected_sha256"],
        }
        expected_shas[sc] = receipt["effective_expected_sha256"]

    manifest = {
        "provision_run_id": run_id, "base_dir": base,
        "run_root": os.path.abspath(run_dir), "scenarios": entries,
        "approved_evidence_root": os.path.abspath(approved_evidence_root),
        "effective_expected_dir": os.path.abspath(effective_expected_dir),
        "effective_expected_shas": expected_shas,
        "created_at": _now_iso(), "harness_version": HARNESS_VERSION,
    }
    mp = os.path.join(run_dir, "scenario_manifest.json")
    with open(mp, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"\n[MANIFEST] -> {mp}")
    print(f"[EFFECTIVE EXPECTED] -> {effective_expected_dir}")
    print(f"[APPROVED EVIDENCE ROOT] -> {approved_evidence_root}")


if __name__ == "__main__":
    main()
