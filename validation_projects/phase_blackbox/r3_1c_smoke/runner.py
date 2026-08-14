"""Agent3 black-box runner for R3.1-C phase public smoke. v12.0.0
Uses shared _provenance module for unified manifest/receipt/contract validation.
Runner collects actual facts. _run_expected compares against effective expected JSON.
NO dynamic expect injection — all expect values come from effective expected files.
Fails closed before evidence creation or MCP server start.
"""
import asyncio, json, os, shutil, sys, traceback, uuid
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

# Shared provenance contract (pure stdlib, no mcps.zynq_mcp imports)
import os as _os
_sys_path_here = _os.path.dirname(_os.path.abspath(__file__))
if _sys_path_here not in sys.path:
    sys.path.insert(0, _sys_path_here)
from _provenance import (
    HARNESS_VERSION, REQUIRED_SCENARIOS, SET_REQUIRED, SHA256_RE, VALID_SCENARIOS,
    FIXED_PLATFORM_REVISION, SCENARIO_PLATFORM_REVISION,
    _sha256_file, _strict_within, _is_symlink_or_junction, _validate_run_id,
    _validate_sha256, _validate_scenario_name, _safe_mkdir_evidence,
    validate_manifest_provenance, validate_ee_shas, validate_all_identity_bindings,
    _load_effective, validate_output_path,
)

VERSION = "12.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN_PATH = os.path.join(HERE, "inputs", "b04_pl_ready", "system_top_expected.v")


async def _call(s, n, a=None):
    return await s.call_tool(n, a or {})


def _tj(r):
    try: return json.loads(r.content[0].text)
    except: return None


def _save(d, sub, fn, data):
    p = os.path.join(d, sub); os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, fn), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _fact(id_, actual):
    return {"id": id_, "actual": actual}


def _run_expected(exp_list, facts):
    facts_by_id = {f["id"]: f for f in facts}
    results = []; consumed = set()
    for ea in exp_list:
        eid = ea["id"]
        ff = facts_by_id.get(eid)
        if ff is None:
            results.append({"assertion_id": eid, "status": "FAIL",
                           "msg": "not executed (missing from actuals)",
                           "expected": ea.get("expect") or ea.get("expect_not_contains"),
                           "actual": None, "field": ea.get("field", "")})
            continue
        consumed.add(eid)
        actual = ff["actual"]
        if "expect_not_contains" in ea:
            ev = ea["expect_not_contains"]
            present = isinstance(actual, (list,)) and ev in actual
            results.append({"assertion_id": eid, "status": "PASS" if not present else "FAIL",
                           "msg": f"{ev} absent" if not present else f"{ev} present",
                           "expected": f"not {ev}", "actual": "absent" if not present else "present",
                           "field": ea.get("field", "")})
        else:
            ev = ea["expect"]; ok = (actual == ev)
            results.append({"assertion_id": eid, "status": "PASS" if ok else "FAIL",
                           "msg": "ok" if ok else f"got {actual}",
                           "expected": ev, "actual": actual, "field": ea.get("field", "")})
    exp_ids = {ea["id"] for ea in exp_list}
    for f in facts:
        if f["id"] not in exp_ids:
            results.append({"assertion_id": f["id"], "status": "FAIL",
                           "msg": "unexpected assertion (not in expected)",
                           "expected": None, "actual": f["actual"], "field": ""})
    passed = all(r["status"] == "PASS" for r in results) and len(consumed) == len(exp_list)
    return results, passed, len(exp_list), len(consumed)


# ══════════ precondition observation ══════════

async def _observe(session, receipt, evidence):
    facts = []
    r = await _call(session, "get_session_info", {"session_id": receipt["session_id"]})
    d = _tj(r)
    _save(evidence, "responses", "get_session_info.json", {"isError": r.isError, "data": d})
    si_ok = d and d.get("status") == "success"
    facts.append(_fact("session_info_success", si_ok))
    si = d.get("data", {}) if si_ok else {}
    facts.append(_fact("session_info_session_id", si.get("session_id") if si_ok else None))
    facts.append(_fact("session_info_board_id", si.get("board_id") if si_ok else None))
    facts.append(_fact("session_info_project_path", si.get("project_path") if si_ok else None))
    facts.append(_fact("session_info_stage", si.get("current_stage") if si_ok else None))
    r2 = await _call(session, "get_execution_state", {})
    d2 = _tj(r2)
    _save(evidence, "state_traces", "0_precondition.json", d2)
    ds = d2["data"]
    facts.append(_fact("precondition_lane", ds.get("execution_lane")))
    facts.append(_fact("precondition_stage", ds.get("current_stage")))
    facts.append(_fact("precondition_worker", ds.get("worker_state")))
    facts.append(_fact("precondition_worker_pid", ds.get("worker_pid")))
    facts.append(_fact("precondition_active_op", ds.get("active_operation")))
    return facts, ds["ledger_sequence"]


# ══════════ capabilities ══════════

async def run_capabilities(session, receipt, evidence, exp):
    facts = []
    pf, _ = await _observe(session, receipt, evidence)
    facts += pf
    r = await session.list_tools()
    tl = r.tools if hasattr(r, 'tools') else list(r)
    names = sorted(t.name for t in tl)
    _save(evidence, "responses", "list_tools.json",
          {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in tl]})
    facts.append(_fact("list_tools_count", len(names)))
    pl_in = [n for n in names if n.startswith("pl_")]
    facts.append(_fact("pl_tools_only_one", pl_in))
    r2 = await _call(session, "get_capabilities", {})
    d2 = _tj(r2)
    _save(evidence, "responses", "get_capabilities.json", {"isError": r2.isError, "data": d2})
    caps = d2["data"] if d2 else {}
    facts.append(_fact("get_capabilities_total_tools", caps.get("total_tools")))
    facts.append(_fact("get_capabilities_control_apis", caps.get("control_apis")))
    facts.append(_fact("get_capabilities_domain_apis_implemented", caps.get("domain_apis_implemented")))
    pt = [t for t in tl if t.name == "pl_generate_system_top"]
    if pt:
        schema = pt[0].inputSchema
        _save(evidence, "responses", "pl_schema.json", schema)
        facts.append(_fact("schema_type", schema.get("type")))
        facts.append(_fact("schema_additional_false", schema.get("additionalProperties")))
        facts.append(_fact("schema_props_keys", sorted(schema.get("properties", {}).keys())))
        wp = schema.get("properties", {}).get("wrapper_path", {})
        facts.append(_fact("wrapper_path_type", wp.get("type")))
        facts.append(_fact("wrapper_path_minLength", wp.get("minLength")))
        facts.append(_fact("schema_required", schema.get("required")))
    for tname in ["pl_create_project","pl_set_top","pl_synthesize","pl_place_and_route",
                  "pl_analyze_timing","pl_generate_bitstream","pl_connect_hw_server",
                  "pl_open_hw_target","pl_select_device","pl_program","pl_get_device_status"]:
        facts.append(_fact(f"pl_{tname}_absent", names))
    return _run_expected(exp["assertions"], facts)


# ══════════ success ══════════

async def run_success(session, receipt, evidence, exp):
    facts = []
    pf, seq_before = await _observe(session, receipt, evidence)
    facts += pf
    r1 = await _call(session, "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
    d1 = _tj(r1)
    _save(evidence, "responses", "1_admission.json", {"isError": r1.isError, "data": d1})
    ok = d1 and d1["status"] == "success" and d1["data"]["status"] == "accepted"
    facts.append(_fact("admission_accepted", d1["status"] if d1 else None))
    oid = d1["data"]["operation_id"] if ok else None
    facts.append(_fact("operation_id_nonempty", oid is not None and oid != ""))
    if not ok:
        return _run_expected(exp["assertions"], facts)
    r2 = await _call(session, "wait_operation", {"operation_id": oid, "timeout_s": 30})
    d2 = _tj(r2)
    _save(evidence, "operation_logs", "terminal.json", {"isError": r2.isError, "data": d2})
    terminal_ok = d2 and d2["data"]["status"] == "SUCCEEDED"
    facts.append(_fact("terminal_succeeded", d2["data"]["status"] if d2 else None))
    if terminal_ok:
        ev = d2["data"].get("completion_evidence", {})
        facts.append(_fact("completion_from", ev.get("stage_advanced_from")))
        facts.append(_fact("completion_to", ev.get("stage_advanced_to")))
    r3 = await _call(session, "get_operation_status", {"operation_id": oid})
    d3 = _tj(r3)
    _save(evidence, "operation_logs", "persisted.json", {"isError": r3.isError, "data": d3})
    rd = d3["data"].get("result", {}).get("data", {}) if d3 else {}
    facts.append(_fact("persisted_fields", bool(rd.get("output_path") and rd.get("system_top_sha256"))))
    facts.append(_fact("compact_no_output", "output" not in rd))
    facts.append(_fact("compact_no_ports", "ports" not in rd))

    # ── P1-5: output_path safety ──
    opath = rd.get("output_path", "")
    pp = receipt.get("project_path", "")
    safe, reason = validate_output_path(opath, pp)
    if not safe:
        facts.append(_fact("disk_sha_matches", False))
        facts.append(_fact("golden_match", False))
        return _run_expected(exp["assertions"], facts)

    if os.path.isfile(opath):
        actual = _sha256_file(opath)
        os.makedirs(os.path.join(evidence, "artifacts"), exist_ok=True)
        shutil.copy2(opath, os.path.join(evidence, "artifacts", "system_top.v"))
        with open(os.path.join(evidence, "artifacts", "system_top.v.sha256"), "w") as f: f.write(actual)
        csha = rd.get("system_top_sha256")
        facts.append(_fact("disk_sha_matches", actual == csha))
        facts.append(_fact("golden_match", os.path.isfile(GOLDEN_PATH) and actual == _sha256_file(GOLDEN_PATH)))
    else:
        facts.append(_fact("disk_sha_matches", False))
        facts.append(_fact("golden_match", False))

    r4 = await _call(session, "get_execution_state", {})
    d4 = _tj(r4); ds2 = d4["data"]
    _save(evidence, "state_traces", "final.json", d4)
    facts.append(_fact("final_lane", ds2.get("execution_lane")))
    facts.append(_fact("final_stage", ds2.get("current_stage")))
    facts.append(_fact("final_worker", ds2.get("worker_state")))
    facts.append(_fact("final_worker_pid", ds2.get("worker_pid")))
    facts.append(_fact("final_active_op", ds2.get("active_operation")))
    facts.append(_fact("ledger_increased", ds2["ledger_sequence"] > seq_before))
    return _run_expected(exp["assertions"], facts)


# ══════════ missing_revision ══════════

async def run_missing_revision(session, receipt, evidence, exp):
    facts = []
    pf, _ = await _observe(session, receipt, evidence)
    facts += pf
    r1 = await _call(session, "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
    d1 = _tj(r1)
    _save(evidence, "responses", "1_admission.json", {"isError": r1.isError, "data": d1})
    ok = d1 and d1["status"] == "success" and d1["data"]["status"] == "accepted"
    facts.append(_fact("admission_accepted", d1["status"] if d1 else None))
    if not ok: return _run_expected(exp["assertions"], facts)
    oid = d1["data"]["operation_id"]
    r2 = await _call(session, "wait_operation", {"operation_id": oid, "timeout_s": 30})
    d2 = _tj(r2)
    _save(evidence, "operation_logs", "terminal.json", {"isError": r2.isError, "data": d2})
    facts.append(_fact("terminal_failed", d2["data"]["status"] if d2 else None))
    facts.append(_fact("reason_manifest_not_found", d2["data"].get("reason_code") if d2 else None))
    r3 = await _call(session, "get_execution_state", {})
    d3 = _tj(r3); ds3 = d3["data"]
    _save(evidence, "state_traces", "final.json", d3)
    facts.append(_fact("final_lane", ds3.get("execution_lane")))
    facts.append(_fact("final_stage", ds3.get("current_stage")))
    facts.append(_fact("final_worker", ds3.get("worker_state")))
    facts.append(_fact("final_active_op", ds3.get("active_operation")))
    return _run_expected(exp["assertions"], facts)


# ══════════ wrong_stage ══════════

async def run_wrong_stage(session, receipt, evidence, exp):
    facts = []
    pf, seq_before = await _observe(session, receipt, evidence)
    facts += pf
    r1 = await _call(session, "pl_generate_system_top", {"wrapper_path": "hdl/bd_wrapper_realistic.v"})
    d1 = _tj(r1)
    _save(evidence, "responses", "1_rejection.json", {"isError": r1.isError, "data": d1})
    facts.append(_fact("rejection_status", d1["status"]))
    facts.append(_fact("rejection_code", d1["error"]["code"]))
    facts.append(_fact("rejection_reason", d1["error"]["details"]["reason_code"]))
    facts.append(_fact("no_operation_id", "operation_id" not in d1.get("data", {})))
    r2 = await _call(session, "get_execution_state", {})
    d2 = _tj(r2); ds2 = d2["data"]
    _save(evidence, "state_traces", "final.json", d2)
    facts.append(_fact("final_lane", ds2.get("execution_lane")))
    facts.append(_fact("final_active_op", ds2.get("active_operation")))
    facts.append(_fact("final_stage_unchanged", ds2.get("current_stage")))
    facts.append(_fact("ledger_unchanged", ds2["ledger_sequence"] == seq_before))
    return _run_expected(exp["assertions"], facts)


# ══════════ invalid_schema ══════════

async def run_invalid_schema(session, receipt, evidence, exp):
    facts = []
    pf, _ = await _observe(session, receipt, evidence)
    facts += pf
    state_unchanged = True
    for label, bad_val in [("int", 123), ("null", None), ("object", {"x": 1})]:
        r_before = await _call(session, "get_execution_state", {})
        d_before = _tj(r_before)
        rv = await _call(session, "pl_generate_system_top", {"wrapper_path": bad_val})
        _save(evidence, "responses", f"schema_reject_{label}.json",
              {"isError": rv.isError, "data": _tj(rv)})
        facts.append(_fact(f"reject_{label}", rv.isError))
        r_after = await _call(session, "get_execution_state", {})
        d_after = _tj(r_after)
        _save(evidence, "state_traces", f"after_{label}.json", d_after)
        for field in ["execution_lane","current_stage","active_operation","previous_operation",
                      "worker_state","worker_pid","ledger_sequence"]:
            if d_before["data"].get(field) != d_after["data"].get(field):
                state_unchanged = False
    facts.append(_fact("state_unchanged", state_unchanged))
    return _run_expected(exp["assertions"], facts)


SCENARIO_FNS = {
    "capabilities": run_capabilities, "success": run_success,
    "missing_revision": run_missing_revision, "wrong_stage": run_wrong_stage,
    "invalid_schema": run_invalid_schema,
}


async def _run_one(scenario, fn, receipt, evidence, expected_dir, expected_sha):
    runtime = receipt["runtime_root"]
    old = os.environ.get("ZYNQ_RUNTIME_ROOT")
    os.environ["ZYNQ_RUNTIME_ROOT"] = runtime
    try:
        params = StdioServerParameters(command=sys.executable, args=["-m","mcps.zynq_mcp.server"], env=os.environ)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                exp_data = _load_effective(expected_dir, scenario, expected_sha)
                results, passed, exp_count, consumed = await fn(s, receipt, evidence, exp_data)
    except Exception as e:
        traceback.print_exc()
        results = [{"assertion_id":"_exception","status":"FAIL","msg":str(e),"expected":None,"actual":None,"field":""}]
        passed = False; exp_count = 0; consumed = 0
    finally:
        if old is not None: os.environ["ZYNQ_RUNTIME_ROOT"] = old
        else: os.environ.pop("ZYNQ_RUNTIME_ROOT", None)
    outcome = {"scenario":scenario,
               "expected_assertion_count":exp_count,"consumed_assertions":consumed,
               "assertions":results,"passed":passed}
    _save(os.path.dirname(evidence), "", f"{scenario}_result.json", outcome)
    n_pass = sum(1 for a in results if a["status"]=="PASS")
    print(f"[{scenario}] {'PASS' if passed else 'FAIL'} ({n_pass}/{len(results)}) expected={exp_count} consumed={consumed}")
    return scenario, passed


# ══════════ main ══════════

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="R3.1-C Agent3 Black-Box Runner v12")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--scenario", default="all")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--evidence-base", default=None)
    parser.add_argument("--expected-dir", default=None)
    args = parser.parse_args()

    # ── 0. Manifest file must exist and be valid JSON ──
    if not os.path.isfile(args.manifest):
        print(f"ERROR: manifest file not found: {args.manifest}", file=sys.stderr); sys.exit(1)
    with open(args.manifest, "r") as f:
        try:
            manifest = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: manifest is not valid JSON: {e}", file=sys.stderr); sys.exit(1)
    if not isinstance(manifest, dict):
        print("ERROR: manifest is not a JSON object", file=sys.stderr); sys.exit(1)

    # ── 1. Validate --scenario BEFORE any evidence/MCP ──
    ok_sc, reason_sc = _validate_scenario_name(args.scenario)
    if not ok_sc:
        print(f"ERROR: {reason_sc}", file=sys.stderr); sys.exit(1)

    # ── 2. Full manifest provenance (unified) ──
    run_root_real, aer_real, eed_real = validate_manifest_provenance(manifest, args.manifest)

    # ── 3. Validate effective_expected_shas ──
    validate_ee_shas(manifest)
    ee_shas = manifest["effective_expected_shas"]

    # ── 4. --expected-dir / --evidence-base consistency ──
    expected_dir = eed_real
    if args.expected_dir:
        user_dir = os.path.realpath(args.expected_dir)
        if user_dir != eed_real:
            print(f"ERROR: --expected-dir realpath {user_dir} != manifest effective_expected_dir {eed_real}",
                  file=sys.stderr); sys.exit(1)
    if args.evidence_base:
        user_ev = os.path.realpath(args.evidence_base)
        if user_ev != aer_real:
            print(f"ERROR: --evidence-base realpath {user_ev} != manifest approved_evidence_root {aer_real}",
                  file=sys.stderr); sys.exit(1)

    # ── 5. Validate run_id ──
    run_id = args.run_id or f"r3_1c_smoke_{uuid.uuid4().hex[:12]}"
    ok_rid, reason_rid = _validate_run_id(run_id)
    if not ok_rid:
        print(f"ERROR: invalid run_id: {reason_rid}", file=sys.stderr); sys.exit(1)

    # ── 6. Pre-validate ALL receipt↔entry↔contract identity bindings ──
    bound_scenarios = validate_all_identity_bindings(manifest, eed_real, ee_shas)

    # ── 7. Safe evidence directory creation ──
    evidence_base_lexical = os.path.join(aer_real, run_id)
    if os.path.basename(evidence_base_lexical) != run_id:
        print(f"ERROR: evidence dir basename mismatch: {os.path.basename(evidence_base_lexical)} != {run_id}",
              file=sys.stderr); sys.exit(1)
    evidence_base_real = _safe_mkdir_evidence(aer_real, evidence_base_lexical)

    # ── 8. Execute scenarios ──
    target = REQUIRED_SCENARIOS if args.scenario == "all" else [args.scenario]
    results = {}; executed = set(); skipped = set(); failed = set()

    for sc in target:
        if sc not in bound_scenarios:
            print(f"[{sc}] SKIPPED: scenario not in manifest"); skipped.add(sc); results[sc]=False; continue
        receipt, ee_data = bound_scenarios[sc]
        expected_sha = ee_shas[sc]
        evidence = os.path.join(evidence_base_real, sc)
        os.makedirs(evidence, exist_ok=True)
        _, passed = await _run_one(sc, SCENARIO_FNS[sc], receipt, evidence, expected_dir, expected_sha)
        results[sc] = passed; executed.add(sc)
        if not passed: failed.add(sc)

    req_set = set(target); missing = req_set - executed - skipped
    passed_set = {s for s, p in results.items() if p}
    overall = len(missing) == 0 and len(skipped) == 0 and len(failed) == 0
    summary = {"run_id": run_id, "runner_version": VERSION,
        "requested": sorted(target), "executed": sorted(executed),
        "skipped": sorted(skipped), "missing": sorted(missing),
        "passed": sorted(passed_set), "failed": sorted(failed), "overall": overall}
    _save(evidence_base_real, "", "summary.json", summary)
    if not overall:
        print(f"\n[FAIL] missing={sorted(missing)} skipped={sorted(skipped)} failed={sorted(failed)}")
        sys.exit(1)
    print(f"\n[PASS] {len(passed_set)}/{len(target)} ({run_id})")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
