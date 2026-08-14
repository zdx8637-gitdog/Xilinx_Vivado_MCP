"""Agent3 black-box runner for B05 Platform/AXI. v2.0.0
Uses ONLY stdlib + MCP SDK. No mcps.zynq_mcp or mcps.common imports.
Loads expected assertions from checked-in expected_outputs/*.json.
"""
import asyncio, hashlib, json, os, re, shutil, sys, tempfile, uuid
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp import ClientSession

VERSION = "2.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = "ALINX_AX7020_v1.0"
_PROJECT_ROOT = "D:/fpgaproject"

SHA256_RE = re.compile(r'^sha256:[0-9a-fA-F]{64}$')


def _sha256_file(p):
    if not os.path.isfile(p): return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return "sha256:" + h.hexdigest()


def _server_params(runtime_root):
    env = os.environ.copy()
    env["PYTHONPATH"] = _PROJECT_ROOT
    # Per-run runtime isolation: unique ZYNQ_RUNTIME_ROOT temp dir so the server
    # writes its Execution Ledger / instance locks there, never re-reading a
    # previous run's dead-worker / RECOVERY_REQUIRED state from .zynq_runtime/
    # (which would fail-closed on admission).
    env["ZYNQ_RUNTIME_ROOT"] = runtime_root
    return StdioServerParameters(command=sys.executable,
        args=["-m", "mcps.zynq_mcp.server"], env=env)


async def _sdk_call(s, n, a=None):
    r = await s.call_tool(n, a or {})
    return json.loads(r.content[0].text)


def _collect(id_, actual):
    return {"id": id_, "actual": actual}


def _load_expected(ed, name):
    p = os.path.join(ed, f"{name}.json")
    if not os.path.isfile(p): raise FileNotFoundError(f"expected missing: {p}")
    with open(p, "r", encoding="utf-8") as f:
        d = json.load(f)
    if "scenario" not in d or "assertions" not in d or not isinstance(d["assertions"], list):
        raise ValueError(f"{name}.json missing scenario/assertions")
    return d


def _run_expected(exp_list, facts):
    fm = {f["id"]: f for f in facts}
    results = []; consumed = set()
    for ea in exp_list:
        eid = ea["id"]
        ff = fm.get(eid)
        if ff is None:
            results.append({"id": eid, "status": "FAIL", "msg": "not collected",
                           "expected": ea.get("expect", ea.get("expect_not_contains", "")),
                           "actual": None, "field": ea.get("field", "")})
            continue
        consumed.add(eid)
        actual = ff["actual"]
        if "expect_not_contains" in ea:
            ev = ea["expect_not_contains"]
            ok = isinstance(actual, list) and ev not in actual
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": f"not {ev}", "actual": "absent" if ok else "present",
                           "field": ea.get("field", ""),
                           "msg": f"{ev} absent" if ok else f"{ev} present"})
        elif "expect_check_sha" in ea:
            ok = isinstance(actual, str) and SHA256_RE.match(actual)
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": ea["expect_check_sha"], "actual": actual,
                           "field": ea.get("field", ""),
                           "msg": "ok" if ok else f"bad SHA: {actual}"})
        elif "expect_pattern" in ea:
            ok = isinstance(actual, str) and bool(re.match(ea["expect_pattern"], actual))
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": f"matches {ea['expect_pattern']}", "actual": actual,
                           "field": ea.get("field", ""),
                           "msg": "ok" if ok else f"no match: {actual}"})
        elif "expect_file_sha" in ea:
            ok = os.path.isfile(actual) and _sha256_file(actual) == ea.get("expect_file_sha", "")
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": ea.get("expect_file_sha", ""),
                           "actual": _sha256_file(actual) if os.path.isfile(actual) else "missing",
                           "field": ea.get("field", ""),
                           "msg": "ok" if ok else "file SHA mismatch or missing"})
        elif "expect_file_exists" in ea:
            ok = os.path.isfile(actual)
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": "file exists", "actual": "exists" if ok else "missing",
                           "field": ea.get("field", ""),
                           "msg": "ok" if ok else "file not found"})
        else:
            ev = ea.get("expect"); ok = (actual == ev)
            results.append({"id": eid, "status": "PASS" if ok else "FAIL",
                           "expected": ev, "actual": actual,
                           "field": ea.get("field", ""),
                           "msg": "ok" if ok else f"got {actual!r}"})
    passed = all(r["status"] == "PASS" for r in results) and len(consumed) == len(exp_list)
    return results, passed, len(exp_list), len(consumed)


# ═══════════════════════════════════════════════
#  Scenarios
# ═══════════════════════════════════════════════

async def run_discovery(session, evidence, exp):
    facts = []
    tools = await session.list_tools()
    names = [t.name for t in tools.tools]
    facts.append(_collect("platform_generate_present", "platform_generate" in names))
    pl_tools = [n for n in names if n.startswith("platform_") or n.startswith("pl_")]
    facts.append(_collect("domain_tool_count", len(pl_tools)))

    pg = [t for t in tools.tools if t.name == "platform_generate"]
    if pg:
        schema = pg[0].inputSchema
        facts.append(_collect("schema_type", schema.get("type")))
        facts.append(_collect("schema_additional_false", schema.get("additionalProperties", False)))
        facts.append(_collect("schema_empty_props", schema.get("properties")))

    r = await _sdk_call(session, "get_capabilities", {})
    caps = r["data"]
    facts.append(_collect("total_tools", caps["total_tools"]))
    facts.append(_collect("platform_implemented", caps["domains"]["platform"]["implemented"]))
    facts.append(_collect("domain_apis_implemented", caps["domain_apis_implemented"]))
    return _run_expected(exp["assertions"], facts)


async def run_success(session, evidence, exp, pp):
    facts = []
    # create_session
    r = await _sdk_call(session, "create_session", {"board_id": BOARD, "project_path": pp})
    facts.append(_collect("session_created", r["status"] == "success"))
    sid = r.get("data", {}).get("session_id", "")
    facts.append(_collect("session_id", sid))

    # stage
    r = await _sdk_call(session, "get_execution_state", {})
    facts.append(_collect("initial_stage", r["data"]["current_stage"]))

    # admission
    r = await _sdk_call(session, "platform_generate", {})
    facts.append(_collect("admission_ok", r["status"] == "success"))
    oid = r.get("data", {}).get("operation_id", "")
    facts.append(_collect("op_id", oid))
    if not oid:
        return _run_expected(exp["assertions"], facts)

    # wait
    r = await _sdk_call(session, "wait_operation", {"operation_id": oid, "timeout_s": 300})
    terminal_ok = r["data"]["status"] == "SUCCEEDED"
    facts.append(_collect("terminal_succeeded", terminal_ok))
    ev = r.get("data", {}).get("completion_evidence", {})
    facts.append(_collect("stage_from", ev.get("stage_advanced_from")))
    facts.append(_collect("stage_to", ev.get("stage_advanced_to")))

    # final state
    r = await _sdk_call(session, "get_execution_state", {})
    facts.append(_collect("final_stage", r["data"]["current_stage"]))
    facts.append(_collect("final_lane", r["data"]["execution_lane"]))

    # platform_revision in context
    r = await _sdk_call(session, "get_session_info", {"session_id": sid})
    plat_rev = r.get("data", {}).get("platform_revision", "")
    facts.append(_collect("platform_revision", plat_rev))
    facts.append(_collect("platform_revision_format", SHA256_RE.match(plat_rev) is not None if plat_rev else False))

    # operation status
    r = await _sdk_call(session, "get_operation_status", {"operation_id": oid})
    op_data = r["data"]
    result = op_data.get("result", {}).get("data", {})
    facts.append(_collect("output_artifact_revision", op_data.get("output_artifact_revision", "")))
    facts.append(_collect("op_rev_matches_ctx", op_data.get("output_artifact_revision") == plat_rev))

    xsa = result.get("xsa_path", "")
    wrapper = result.get("wrapper_path", "")
    manifest = result.get("manifest_path", "")
    wrapper_rel = result.get("wrapper_rel", "")  # relative path for B04 handoff
    facts.append(_collect("xsa_exists", os.path.isfile(xsa) if xsa else False))
    facts.append(_collect("wrapper_exists", os.path.isfile(wrapper) if wrapper else False))
    facts.append(_collect("manifest_exists", os.path.isfile(manifest) if manifest else False))

    # Independent disk SHA comparison against operation result SHAs
    xsa_disk_sha = _sha256_file(xsa) if xsa and os.path.isfile(xsa) else ""
    wrapper_disk_sha = _sha256_file(wrapper) if wrapper and os.path.isfile(wrapper) else ""
    manifest_disk_sha = _sha256_file(manifest) if manifest and os.path.isfile(manifest) else ""
    facts.append(_collect("xsa_disk_matches_op", xsa_disk_sha == result.get("xsa_sha256", "")))
    facts.append(_collect("wrapper_disk_matches_op", wrapper_disk_sha == result.get("wrapper_sha256", "")))
    facts.append(_collect("manifest_disk_matches_op", manifest_disk_sha == result.get("manifest_sha256", "")))

    # Manifest cross-check: its artifact hashes match disk SHAs
    manifest_xsa_match = True; manifest_wrapper_match = True; manifest_rev_ok = True
    manifest_addr_ok = True
    if manifest and os.path.isfile(manifest):
        with open(manifest) as f:
            mdata = json.load(f)
        facts.append(_collect("manifest_schema", mdata.get("schema_version")))
        facts.append(_collect("manifest_type", mdata.get("manifest_type")))
        facts.append(_collect("manifest_status", mdata.get("status")))
        facts.append(_collect("manifest_rev_match", mdata.get("manifest_revision") == mdata.get("platform_revision")))
        facts.append(_collect("manifest_xsa_disk_match", mdata.get("xsa_sha256") == xsa_disk_sha))
        facts.append(_collect("manifest_wrapper_disk_match", mdata.get("bd_wrapper_sha256") == wrapper_disk_sha))
        facts.append(_collect("manifest_rev_ctx_match", mdata.get("platform_revision") == plat_rev))
        am = mdata.get("address_map", {}).get("axi_gpio_led", {})
        facts.append(_collect("manifest_addr_base", am.get("base")))
        facts.append(_collect("manifest_addr_range", am.get("range")))

    # Platform→PL handoff: use wrapper_rel from result, no hardcoding
    if wrapper_rel:
        r = await _sdk_call(session, "pl_generate_system_top", {"wrapper_path": wrapper_rel})
        facts.append(_collect("pl_handoff_ok", r["status"] == "success"))
        if r["status"] == "success":
            pl_oid = r["data"]["operation_id"]
            r2 = await _sdk_call(session, "wait_operation", {"operation_id": pl_oid, "timeout_s": 30})
            facts.append(_collect("pl_handoff_succeeded", r2["data"]["status"] == "SUCCEEDED"))
    else:
        facts.append(_collect("pl_handoff_ok", False))
        facts.append(_collect("pl_handoff_succeeded", False))

    return _run_expected(exp["assertions"], facts)


async def run_stage_rejection(session, evidence, exp):
    facts = []
    # Capture pre-rejection stage (may be PL_GENERATE or PL_BUILD if PL handoff ran)
    r0 = await _sdk_call(session, "get_execution_state", {})
    pre_stage = r0["data"]["current_stage"]

    r = await _sdk_call(session, "platform_generate", {})
    facts.append(_collect("rejected", r["status"] == "error"))
    reason = r.get("error", {}).get("details", {}).get("reason_code", "")
    facts.append(_collect("reason_stage", reason in ("STAGE_PREREQUISITE_UNMET", "CHANNEL_BUSY")))

    r = await _sdk_call(session, "get_execution_state", {})
    facts.append(_collect("stage_unchanged", r["data"]["current_stage"] == pre_stage))
    return _run_expected(exp["assertions"], facts)


SCENARIOS = {
    "discovery": run_discovery,
    "success": run_success,
    "stage_rejection": run_stage_rejection,
}


async def main():
    import argparse
    p = argparse.ArgumentParser(description="B05 Agent3 Runner v2")
    p.add_argument("--run-id", required=True)
    p.add_argument("--scenario", default="all")
    p.add_argument("--evidence-base", default=None)
    p.add_argument("--expected-dir", default=None)
    args = p.parse_args()

    run_id = args.run_id
    evidence_base = args.evidence_base or os.path.join(HERE, "evidence", run_id)
    expected_dir = args.expected_dir or os.path.join(HERE, "expected_outputs")
    os.makedirs(evidence_base, exist_ok=True)

    target = ["discovery", "success", "stage_rejection"] if args.scenario == "all" else [args.scenario]
    results = {}; executed = set(); failed = set()

    # Runtime isolation (Option A): unique ZYNQ_RUNTIME_ROOT temp dir per run so a
    # previous run's dead-worker / RECOVERY_REQUIRED state in the shared
    # .zynq_runtime/ can never fail-closed this run at admission.
    runtime_root = tempfile.mkdtemp(prefix="b05_runtime_")
    try:
        params = _server_params(runtime_root)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                pp = tempfile.mkdtemp(prefix="b05_agent3_")

                for name in target:
                    fn = SCENARIOS.get(name)
                    if fn is None:
                        results[name] = False; continue
                    try:
                        exp = _load_expected(expected_dir, name)
                    except Exception as e:
                        print(f"[{name}] SKIPPED: {e}"); results[name] = False; continue

                    ev = os.path.join(evidence_base, name)
                    os.makedirs(ev, exist_ok=True)
                    if name == "success":
                        res_list, passed, exp_cnt, consumed = await fn(s, ev, exp, pp)
                    else:
                        res_list, passed, exp_cnt, consumed = await fn(s, ev, exp)

                    results[name] = passed; executed.add(name)
                    if not passed: failed.add(name)
                    outcome = {"scenario": name, "passed": passed,
                              "expected_assertion_count": exp_cnt, "consumed_assertions": consumed,
                              "assertions": res_list}
                    with open(os.path.join(evidence_base, f"{name}_result.json"), "w") as f:
                        json.dump(outcome, f, indent=2)
                    print(f"[{name}] {'PASS' if passed else 'FAIL'} ({consumed}/{exp_cnt})")
    finally:
        shutil.rmtree(runtime_root, ignore_errors=True)

    req_set = set(target); passed_set = {s for s, p in results.items() if p}
    overall = req_set == executed and len(passed_set) == len(req_set)
    summary = {"run_id": run_id, "runner_version": VERSION,
        "requested": sorted(target), "executed": sorted(executed),
        "passed": sorted(passed_set), "failed": sorted(failed),
        "overall": overall}
    with open(os.path.join(evidence_base, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if not overall:
        print(f"\n[FAIL] passed={sorted(passed_set)} failed={sorted(failed)}")
        sys.exit(1)
    print(f"\n[PASS] {len(passed_set)}/{len(target)} ({run_id})")
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
