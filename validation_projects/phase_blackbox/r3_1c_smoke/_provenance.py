"""
Shared provenance validation module for R3.1-C phase black-box. v3.0.0
Pure stdlib — no mcps.zynq_mcp imports. Used by runner, verify_readiness, cleanup.
Defines the single unified provenance contract for provisioning manifests.
"""
import hashlib, json, os, re, sys

# ═══════════════════════════════════════════════════════════════
#  Constants — single source of truth for all three tools
# ═══════════════════════════════════════════════════════════════

HARNESS_VERSION      = "4.0.0"
PROVISION_RUN_ID_RE  = re.compile(r'^prov_[0-9a-f]{12}$')
SHA256_RE            = re.compile(r'^sha256:[0-9a-fA-F]{64}$')
SHA256_HEX_RE        = re.compile(r'^sha256:([0-9a-fA-F]{64})$')
_VALID_RUN_ID        = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')

REQUIRED_SCENARIOS   = ["capabilities", "success", "missing_revision", "wrong_stage", "invalid_schema"]
SET_REQUIRED         = set(REQUIRED_SCENARIOS)
VALID_SCENARIOS      = {"all"} | SET_REQUIRED

# The fixed platform_revision used by capabilities/success/wrong_stage/invalid_schema
FIXED_PLATFORM_REVISION = "sha256:7f7cd446fa3c4c01e8d3c5fa4d07e56cb750b3555e258e10410b0345c737f1b3"

SCENARIO_PLATFORM_REVISION = {
    "capabilities":     FIXED_PLATFORM_REVISION,
    "success":          FIXED_PLATFORM_REVISION,
    "missing_revision": "",
    "wrong_stage":      FIXED_PLATFORM_REVISION,
    "invalid_schema":   FIXED_PLATFORM_REVISION,
}

# platform_revision_public_expected: equals the platform manifest template revision
SCENARIO_PLATFORM_REVISION_PUBLIC = {
    "capabilities":     FIXED_PLATFORM_REVISION,
    "success":          FIXED_PLATFORM_REVISION,
    "missing_revision": "",
    "wrong_stage":      FIXED_PLATFORM_REVISION,
    "invalid_schema":   FIXED_PLATFORM_REVISION,
}

# Canonical checked-in fixture directory (computed relative to this file location)
_HERE = os.path.dirname(os.path.abspath(__file__))
CANONICAL_FIXTURE_DIR = os.path.realpath(os.path.join(_HERE, "inputs", "b04_pl_ready"))

# Frozen fixture SHA — verified from canonical directory, not self-reported
FROZEN_FIXTURE_MAP = {
    "bd_wrapper_realistic.v":     ("bd_wrapper_realistic.v",
        "sha256:a3fe5b0b5f4b1fffa629536f1d9e792bfe03234d2eb5e98d8f7fc7635b26f418"),
    "platform_manifest_template": ("platform_manifest.json",
        "sha256:045335f34a63ce1e605e697ea908b7f998c1c37524ff2128ac55be848c718151"),
}
_EXPECTED_FIXTURE_KEYS = set(FROZEN_FIXTURE_MAP.keys())

# Expected input artifact count — every scenario must have exactly 3 artifacts
EXPECTED_ARTIFACT_COUNT = 3
_EXPECTED_ARTIFACT_SET = {"hdl/bd_wrapper_realistic.v", "platform.xsa"}

# ── Exact platform manifest filename (same as mcps.common.artifact_schema._revision_to_filename) ──
def _revision_to_filename(rev):
    """sha256:<hex> → sha256_<hex>.json — mirrors provisioning contract."""
    if not isinstance(rev, str) or not rev:
        return None
    m = SHA256_HEX_RE.match(rev)
    if not m:
        return None
    return f"sha256_{m.group(1)}.json"

def _expected_manifest_relative_path(scenario):
    """Returns the exact manifests/platform/<filename> expected for a scenario,
    or None for missing_revision (which has no platform manifest artifact)."""
    rev = SCENARIO_PLATFORM_REVISION.get(scenario)
    if not rev:  # missing_revision has empty string revision
        return None
    fname = _revision_to_filename(rev)
    if not fname:
        return None
    return os.path.join("manifests", "platform", fname)

# ── Per-scenario ledger stage contracts ──
# Initial stage: the stage each scenario's runtime must be in before runner execution
SCENARIO_INITIAL_STAGE = {
    "capabilities":     "PL_GENERATE",
    "success":          "PL_GENERATE",
    "missing_revision": "PL_GENERATE",
    "wrong_stage":      "PLATFORM_DESIGN",
    "invalid_schema":   "PL_GENERATE",
}

# Post-execution valid stages: what cleanup accepts (runner may advance some scenarios)
SCENARIO_POST_EXEC_STAGES = {
    "capabilities":     {"PL_GENERATE"},
    "success":          {"PL_GENERATE", "PL_BUILD"},
    "missing_revision": {"PL_GENERATE"},
    "wrong_stage":      {"PLATFORM_DESIGN"},
    "invalid_schema":   {"PL_GENERATE"},
}

# Expected precondition state constants
EXPECTED_INITIAL_LANE   = "IDLE"
EXPECTED_WORKER_STATE   = "ABSENT"
EXPECTED_WORKER_PID     = None  # None in JSON = null


# ═══════════════════════════════════════════════════════════════
#  Canonical path helpers within run_root
# ═══════════════════════════════════════════════════════════════

def _canonical_runtime_root(run_root, scenario):
    return os.path.join(run_root, scenario, "_provisioned")

def _canonical_receipt_path(runtime_root):
    return os.path.join(runtime_root, "provision_receipt.json")

def _canonical_evidence_root(run_root):
    return os.path.join(run_root, "evidence")

def _canonical_effective_expected_dir(run_root):
    return os.path.join(run_root, "effective_expected")

def _canonical_manifest_path(run_root):
    return os.path.join(run_root, "scenario_manifest.json")


# ═══════════════════════════════════════════════════════════════
#  Utility functions
# ═══════════════════════════════════════════════════════════════

def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
    return "sha256:" + h.hexdigest()


def _strict_within(parent, child):
    rp = os.path.realpath(parent)
    rc = os.path.realpath(child)
    if rp == rc:
        return False
    try:
        if os.path.commonpath([rp, rc]) != rp:
            return False
    except ValueError:
        return False
    if os.name == "nt":
        if not os.path.normcase(rc).startswith(os.path.normcase(rp) + os.sep):
            return False
    return True


def _is_symlink_or_junction(p):
    if os.path.islink(p):
        return True
    if os.name == "nt":
        try:
            if hasattr(os.path, 'isjunction') and os.path.isjunction(p):
                return True
        except (OSError, NotImplementedError):
            pass
    return False


def _scan_for_symlinks_or_junctions(root_dir):
    found = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            if _is_symlink_or_junction(full):
                found.append(full)
    return found


def _validate_run_id(run_id):
    if not isinstance(run_id, str) or not run_id:
        return False, "run_id must be a non-empty string"
    if run_id in (".", ".."):
        return False, f"run_id rejected: '{run_id}'"
    if "/" in run_id or "\\" in run_id:
        return False, f"run_id contains path separator: '{run_id}'"
    if os.path.isabs(run_id):
        return False, f"run_id is absolute path: '{run_id}'"
    if " " in run_id or run_id != run_id.strip():
        return False, f"run_id contains whitespace: '{run_id}'"
    if not _VALID_RUN_ID.match(run_id):
        return False, f"run_id contains invalid characters: '{run_id}'"
    return True, "ok"


def _validate_sha256(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string, got {value!r}")
    if not SHA256_RE.match(value):
        raise ValueError(f"{label} must match sha256:<64 hex>, got {value!r}")


def _validate_scenario_name(scenario):
    if scenario not in VALID_SCENARIOS:
        return False, f"invalid scenario: {scenario!r}, allowed: {sorted(VALID_SCENARIOS)}"
    return True, "ok"


def _safe_mkdir_evidence(parent_real, lexical_target):
    if not os.path.isdir(parent_real):
        print(f"ERROR: evidence parent is not a directory: {parent_real}", file=sys.stderr)
        sys.exit(1)
    target_lex = os.path.abspath(lexical_target)
    if os.path.lexists(target_lex):
        if os.path.islink(target_lex):
            print(f"ERROR: pre-existing symlink at evidence target: {target_lex}", file=sys.stderr)
        elif os.path.isdir(target_lex):
            print(f"ERROR: pre-existing directory at evidence target: {target_lex}", file=sys.stderr)
        elif os.path.isfile(target_lex):
            print(f"ERROR: pre-existing file at evidence target: {target_lex}", file=sys.stderr)
        else:
            print(f"ERROR: pre-existing entry at evidence target: {target_lex}", file=sys.stderr)
        sys.exit(1)
    try:
        os.mkdir(target_lex)
    except FileExistsError:
        print(f"ERROR: evidence target already exists (race): {target_lex}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"ERROR: cannot create evidence directory: {target_lex} — {e}", file=sys.stderr)
        sys.exit(1)
    if _is_symlink_or_junction(target_lex):
        print(f"ERROR: created evidence target is a symlink: {target_lex}", file=sys.stderr)
        try: os.unlink(target_lex)
        except OSError: pass
        sys.exit(1)
    if not _strict_within(parent_real, target_lex):
        print(f"ERROR: evidence target escaped containment: {target_lex}", file=sys.stderr)
        try: os.rmdir(target_lex)
        except OSError: pass
        sys.exit(1)
    return os.path.realpath(target_lex)


# ═══════════════════════════════════════════════════════════════
#  Manifest provenance validation (unified)
# ═══════════════════════════════════════════════════════════════

def validate_manifest_provenance(manifest, manifest_path, exit_on_fail=True):
    manifest_real = os.path.realpath(manifest_path)

    def _fail(msg):
        if exit_on_fail:
            print(f"ERROR: {msg}", file=sys.stderr); sys.exit(1)
        return False, msg

    for field in ["provision_run_id", "harness_version", "base_dir", "run_root",
                   "approved_evidence_root", "effective_expected_dir", "effective_expected_shas",
                   "scenarios"]:
        if not manifest.get(field):
            return _fail(f"manifest missing required field: {field}")

    if manifest["harness_version"] != HARNESS_VERSION:
        return _fail(f"harness_version mismatch: got {manifest['harness_version']!r}, expected {HARNESS_VERSION!r}")

    prid = manifest["provision_run_id"]
    if not isinstance(prid, str) or not PROVISION_RUN_ID_RE.match(prid):
        return _fail(f"provision_run_id bad format: {prid!r}")

    base_dir = os.path.realpath(manifest["base_dir"])
    if not os.path.isdir(base_dir):
        return _fail(f"base_dir is not a directory: {base_dir}")

    run_root_real = os.path.realpath(manifest["run_root"])
    if not os.path.isdir(run_root_real):
        return _fail(f"run_root is not a directory: {run_root_real}")
    if not _strict_within(base_dir, run_root_real):
        return _fail(f"run_root {run_root_real} not within base_dir {base_dir}")
    if os.path.basename(run_root_real) != prid:
        return _fail(f"provision_run_id {prid!r} != basename(run_root) {os.path.basename(run_root_real)!r}")

    canonical_mp = _canonical_manifest_path(run_root_real)
    if manifest_real != os.path.realpath(canonical_mp):
        return _fail(f"manifest path {manifest_real} != canonical {canonical_mp}")

    aer_real = os.path.realpath(manifest["approved_evidence_root"])
    canonical_aer = os.path.realpath(_canonical_evidence_root(run_root_real))
    if aer_real != canonical_aer:
        return _fail(f"approved_evidence_root {aer_real} != canonical {canonical_aer}")
    if not _strict_within(run_root_real, aer_real):
        return _fail(f"approved_evidence_root {aer_real} not within run_root {run_root_real}")
    if not os.path.isdir(aer_real):
        return _fail(f"approved_evidence_root is not a directory: {aer_real}")

    eed_real = os.path.realpath(manifest["effective_expected_dir"])
    canonical_eed = os.path.realpath(_canonical_effective_expected_dir(run_root_real))
    if eed_real != canonical_eed:
        return _fail(f"effective_expected_dir {eed_real} != canonical {canonical_eed}")
    if not _strict_within(run_root_real, eed_real):
        return _fail(f"effective_expected_dir {eed_real} not within run_root {run_root_real}")
    if not os.path.isdir(eed_real):
        return _fail(f"effective_expected_dir is not a directory: {eed_real}")

    scenarios = manifest["scenarios"]
    if not isinstance(scenarios, dict) or set(scenarios.keys()) != SET_REQUIRED:
        return _fail(f"scenarios keys mismatch, required {sorted(REQUIRED_SCENARIOS)}")

    for sc in REQUIRED_SCENARIOS:
        entry = scenarios[sc]
        if not isinstance(entry, dict):
            return _fail(f"scenario {sc} entry is not a dict")
        if entry.get("scenario") != sc:
            return _fail(f"scenario {sc} entry.scenario mismatch: {entry.get('scenario')!r}")
        srt = entry.get("runtime_root")
        canonical_srt = _canonical_runtime_root(run_root_real, sc)
        srt_real = os.path.realpath(srt) if srt else None
        canonical_srt_real = os.path.realpath(canonical_srt)
        if srt_real != canonical_srt_real:
            return _fail(f"scenario {sc} runtime_root {srt_real} != canonical {canonical_srt_real}")
        if not os.path.isdir(srt_real):
            return _fail(f"scenario {sc} runtime_root not a directory: {srt_real}")
        srp = entry.get("receipt_path")
        canonical_srp = _canonical_receipt_path(srt_real)
        srp_real = os.path.realpath(srp) if srp else None
        canonical_srp_real = os.path.realpath(canonical_srp)
        if srp_real != canonical_srp_real:
            return _fail(f"scenario {sc} receipt_path {srp_real} != canonical {canonical_srp_real}")
        if not os.path.isfile(srp_real):
            return _fail(f"scenario {sc} receipt not found: {srp_real}")

    if exit_on_fail:
        return run_root_real, aer_real, eed_real
    return True, "ok"


# ═══════════════════════════════════════════════════════════════
#  Effective expected SHAs validation
# ═══════════════════════════════════════════════════════════════

def validate_ee_shas(manifest):
    ee_shas = manifest.get("effective_expected_shas")
    if not isinstance(ee_shas, dict):
        print("ERROR: manifest missing or invalid effective_expected_shas", file=sys.stderr); sys.exit(1)
    sha_keys = set(ee_shas.keys())
    if sha_keys != SET_REQUIRED:
        extra = sha_keys - SET_REQUIRED; missing_ = SET_REQUIRED - sha_keys
        parts = []
        if missing_: parts.append(f"missing: {sorted(missing_)}")
        if extra: parts.append(f"extra: {sorted(extra)}")
        print(f"ERROR: effective_expected_shas keys mismatch — {', '.join(parts)}", file=sys.stderr)
        sys.exit(1)
    for sc in REQUIRED_SCENARIOS:
        try:
            _validate_sha256(ee_shas[sc], f"effective_expected_shas.{sc}")
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)


# ═══════════════════════════════════════════════════════════════
#  Receipt↔Entry↔Contract↔Ledger identity binding
# ═══════════════════════════════════════════════════════════════

def _extract_ee_identity(effective_json):
    identity = {}
    for a in effective_json.get("assertions", []):
        aid = a.get("id", "")
        if aid == "session_info_session_id":
            identity["session_id"] = a.get("expect")
        elif aid == "session_info_board_id":
            identity["board_id"] = a.get("expect")
        elif aid == "session_info_project_path":
            identity["project_path"] = a.get("expect")
        elif aid == "session_info_stage":
            identity["stage"] = a.get("expect")
    return identity


def _load_effective(ed, name, expected_sha):
    if not expected_sha or not isinstance(expected_sha, str):
        raise ValueError(f"effective expected SHA missing for {name}: {expected_sha!r}")
    p = os.path.join(ed, f"{name}.json")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"effective expected file missing: {p}")
    actual_sha = _sha256_file(p)
    if actual_sha != expected_sha:
        raise ValueError(f"effective expected SHA mismatch for {name}: manifest says {expected_sha}, file is {actual_sha}")
    with open(p, "r", encoding="utf-8") as f:
        text = f.read()
    if "PLACEHOLDER_" in text:
        remaining = [line.strip() for line in text.splitlines() if "PLACEHOLDER_" in line]
        raise ValueError(f"PLACEHOLDER_* not resolved in effective expected {name}: {remaining}")
    d = json.loads(text)
    if "scenario" not in d:
        raise ValueError(f"{name}.json missing 'scenario'")
    if d["scenario"] != name:
        raise ValueError(f"effective expected scenario field mismatch: file says {d['scenario']!r}, expected {name!r}")
    if "assertions" not in d or not isinstance(d["assertions"], list) or len(d["assertions"]) == 0:
        raise ValueError(f"{name}.json missing non-empty 'assertions' list")
    ids = [a.get("id") for a in d["assertions"]]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{name}.json has duplicate assertion ids")
    for a in d["assertions"]:
        if "id" not in a:
            raise ValueError(f"{name}.json assertion missing 'id'")
        if "expect" not in a and "expect_not_contains" not in a:
            raise ValueError(f"{name}.json assertion {a.get('id','?')} missing expect/expect_not_contains")
    return d


def _validate_one_scenario(sc, entry, entry_rt, eed_real, ee_shas, rts_seen, sids_seen, pps_seen,
                          allowed_ledger_stages=None):
    """Validate one scenario's receipt/entry/contract bindings.
    allowed_ledger_stages: set of acceptable ledger current_stage values.
      Default None means use SCENARIO_INITIAL_STAGE (pre-execution).
      Pass SCENARIO_POST_EXEC_STAGES[sc] for cleanup (post-execution).
    Returns (ok, errors_list, receipt, ee_data). Does NOT exit."""
    errors = []
    bound_receipt = None
    bound_ee = None
    srp = os.path.realpath(entry.get("receipt_path", ""))

    if not os.path.isfile(srp):
        errors.append("receipt_missing"); return False, errors, None, None
    try:
        receipt = json.load(open(srp, "r"))
    except Exception:
        errors.append("receipt_invalid_json"); return False, errors, None, None

    # Required receipt fields
    required_rec = [
        "scenario", "runtime_root", "project_path", "session_id", "board_id",
        "expected_initial_stage", "expected_initial_lane", "expected_worker_state",
        "expected_worker_pid", "platform_revision", "platform_revision_public_expected",
        "effective_expected_sha256", "harness_version", "input_artifacts",
        "fixture_provenance",
    ]
    for fld in required_rec:
        if fld not in receipt:
            errors.append(f"receipt_missing_field:{fld}"); return False, errors, None, None

    # harness_version
    if receipt["harness_version"] != HARNESS_VERSION:
        errors.append("receipt_harness_version_mismatch")
    # scenario
    if receipt["scenario"] != sc:
        errors.append("receipt_scenario_mismatch")
    # runtime_root
    rec_rt = os.path.realpath(receipt.get("runtime_root", ""))
    if rec_rt != entry_rt:
        errors.append("receipt_runtime_root_mismatch")
    # project_path
    rec_pp = os.path.realpath(receipt.get("project_path", ""))
    if not _strict_within(rec_rt, rec_pp):
        errors.append("project_path_not_within_runtime_root")
        return False, errors, None, None

    # precondition state expectations
    if receipt.get("expected_initial_lane") != EXPECTED_INITIAL_LANE:
        errors.append("expected_initial_lane_mismatch")
    if receipt.get("expected_worker_state") != EXPECTED_WORKER_STATE:
        errors.append("expected_worker_state_mismatch")
    if receipt.get("expected_worker_pid") is not None:
        errors.append("expected_worker_pid_not_null")

    # entry.expected_stage vs receipt.expected_initial_stage
    if entry.get("expected_stage") != receipt.get("expected_initial_stage"):
        errors.append("expected_stage_mismatch")

    # SHA binding
    rec_sha = receipt.get("effective_expected_sha256", "")
    exp_sha = ee_shas[sc]
    if rec_sha != exp_sha:
        errors.append("receipt_ee_sha_mismatch")
    entry_sha = entry.get("effective_expected_sha256")
    if not entry_sha or entry_sha != exp_sha:
        errors.append("entry_ee_sha_mismatch")

    # Platform revision
    expected_prev = SCENARIO_PLATFORM_REVISION[sc]
    if receipt.get("platform_revision") != expected_prev:
        errors.append("platform_revision_mismatch")
    # platform_revision_public_expected
    expected_prev_public = SCENARIO_PLATFORM_REVISION_PUBLIC[sc]
    if receipt.get("platform_revision_public_expected") != expected_prev_public:
        errors.append("platform_revision_public_expected_mismatch")

    # Uniqueness
    if rec_rt in rts_seen: errors.append("runtime_root_duplicate")
    sid = receipt.get("session_id")
    if sid and sid in sids_seen: errors.append("session_id_duplicate")
    if rec_pp in pps_seen: errors.append("project_path_duplicate")

    # Stop here if fundamental errors (no point loading EE or ledger)
    if errors:
        return False, errors, None, None

    rts_seen.append(rec_rt)
    if sid: sids_seen.append(sid)
    pps_seen.append(rec_pp)

    # Load effective expected
    try:
        ee_data = _load_effective(eed_real, sc, exp_sha)
    except Exception as e:
        errors.append(f"effective_expected_load_failed:{e}")
        return False, errors, None, None

    # Cross-check EE identity vs receipt
    ee_id = _extract_ee_identity(ee_data)
    for ee_field, rec_key in [("session_id", "session_id"), ("board_id", "board_id"),
                               ("project_path", "project_path"), ("stage", "expected_initial_stage")]:
        ee_val = ee_id.get(ee_field)
        rec_val = receipt.get(rec_key)
        if ee_val is None:
            errors.append(f"ee_missing_identity:{ee_field}")
        elif rec_val != ee_val:
            errors.append(f"identity_mismatch:{ee_field}")

    # Ledger cross-check
    lp = os.path.join(rec_rt, "execution_ledger.json")
    if not os.path.isfile(lp):
        errors.append("ledger_missing")
        return False if errors else True, errors, None, None
    try:
        ld = json.load(open(lp, "r"))
        ctx = ld.get("context", {}); wo = ld.get("worker", {})
        if ld.get("execution_lane") != EXPECTED_INITIAL_LANE:
            errors.append("ledger_lane_mismatch")
        if wo.get("state") != EXPECTED_WORKER_STATE:
            errors.append("ledger_worker_state_mismatch")
        if wo.get("pid") is not None:
            errors.append("ledger_worker_pid_not_null")
        if ld.get("active_operation") is not None:
            errors.append("ledger_active_op_not_null")
        ledger_stage = ctx.get("current_stage")
        if allowed_ledger_stages is None:
            allowed = {SCENARIO_INITIAL_STAGE[sc]}
        else:
            allowed = allowed_ledger_stages
        if ledger_stage not in allowed:
            errors.append(f"ledger_stage_mismatch:{ledger_stage}")
        ledger_prev = ctx.get("platform_revision")
        if ledger_prev != expected_prev:
            errors.append("ledger_platform_revision_mismatch")
        if ctx.get("session_id") != receipt.get("session_id"):
            errors.append("ledger_session_id_mismatch")
        if ctx.get("board_id") != receipt.get("board_id"):
            errors.append("ledger_board_id_mismatch")
        if os.path.realpath(ctx.get("project_path", "")) != rec_pp:
            errors.append("ledger_project_path_mismatch")
    except Exception as e:
        errors.append(f"ledger_read_error:{e}")

    # Input artifacts
    arts = receipt.get("input_artifacts")
    if not isinstance(arts, list) or len(arts) == 0:
        errors.append("input_artifacts_missing_or_empty")
    elif len(arts) != EXPECTED_ARTIFACT_COUNT:
        errors.append(f"input_artifacts_count:{len(arts)}")
    else:
        seen_rels = set()
        for art in arts:
            if not isinstance(art, dict):
                errors.append("input_artifact_not_dict"); break
            rel = art.get("relative_path", "")
            if not rel or not isinstance(rel, str):
                errors.append("input_artifact_no_relative_path"); break
            if rel in seen_rels:
                errors.append(f"input_artifact_duplicate:{rel}"); break
            seen_rels.add(rel)
            art_sha = art.get("sha256", "")
            if not isinstance(art_sha, str) or not SHA256_RE.match(art_sha):
                errors.append(f"input_artifact_bad_sha:{rel}"); break
            ap = os.path.join(rec_pp, rel)
            rap = os.path.realpath(ap)
            if not _strict_within(rec_pp, rap):
                errors.append(f"input_artifact_escape:{rel}"); break
            if not os.path.isfile(rap):
                errors.append(f"input_artifact_missing:{rel}"); break
            if _is_symlink_or_junction(rap):
                errors.append(f"input_artifact_symlink:{rel}"); break
            disk_sha = _sha256_file(rap)
            if disk_sha != art_sha:
                errors.append(f"input_artifact_sha_mismatch:{rel}"); break
        if not errors:
            fixed_count = sum(1 for r in seen_rels if r in _EXPECTED_ARTIFACT_SET)
            expected_manifest = _expected_manifest_relative_path(sc)
            if expected_manifest:
                manifest_ok = expected_manifest in seen_rels
            else:
                # missing_revision has no platform revision, so no manifest artifact
                manifest_ok = True
            if fixed_count != 2 or not manifest_ok:
                errors.append("input_artifacts_set_mismatch")

    # Fixture provenance
    fp = receipt.get("fixture_provenance")
    if not isinstance(fp, dict):
        errors.append("fixture_provenance_not_dict")
    else:
        expected_fp_keys = _EXPECTED_FIXTURE_KEYS | {"source"}
        actual_fp_keys = set(fp.keys())
        if actual_fp_keys != expected_fp_keys:
            extra = actual_fp_keys - expected_fp_keys
            missing_fp = expected_fp_keys - actual_fp_keys
            parts = []
            if missing_fp: parts.append(f"missing:{','.join(sorted(missing_fp))}")
            if extra: parts.append(f"extra:{','.join(sorted(extra))}")
            errors.append(f"fixture_provenance_keys:{';'.join(parts)}")
        else:
            fp_source = fp.get("source", "")
            fp_source_real = os.path.realpath(fp_source) if fp_source else None
            if fp_source_real != CANONICAL_FIXTURE_DIR:
                errors.append("fixture_provenance_source_mismatch")
            for rec_key, (src_fname, frozen_sha) in FROZEN_FIXTURE_MAP.items():
                src_path = os.path.join(CANONICAL_FIXTURE_DIR, src_fname)
                if not os.path.isfile(src_path):
                    errors.append(f"fixture_source_file_missing:{src_fname}")
                else:
                    actual_src_sha = _sha256_file(src_path)
                    if actual_src_sha != frozen_sha:
                        errors.append(f"fixture_source_sha_mismatch:{src_fname}")
                    rec_fp_val = fp.get(rec_key)
                    if rec_fp_val is None:
                        errors.append(f"fixture_provenance_missing_key:{rec_key}")
                    elif rec_fp_val != frozen_sha:
                        errors.append(f"fixture_provenance_hash_mismatch:{rec_key}")

    if errors:
        return False, errors, None, None
    bound_receipt = receipt
    bound_ee = ee_data
    return True, [], receipt, ee_data


def validate_all_identity_bindings(manifest, eed_real, ee_shas):
    """Pre-validate ALL receipt/artifact/provenance/ledger bindings
    for ALL five scenarios BEFORE evidence/MCP. Exits on first error.
    Returns {scenario: (receipt_dict, ee_data)}."""
    bound = {}
    rts_seen, sids_seen, pps_seen = [], [], []

    for sc in REQUIRED_SCENARIOS:
        entry = manifest["scenarios"][sc]
        entry_rt = os.path.realpath(entry["runtime_root"])
        ok, errors, receipt, ee_data = _validate_one_scenario(
            sc, entry, entry_rt, eed_real, ee_shas, rts_seen, sids_seen, pps_seen)
        if not ok:
            for e in errors:
                print(f"ERROR: scenario {sc} {e}", file=sys.stderr)
            sys.exit(1)
        rts_seen.append(os.path.realpath(receipt["runtime_root"]))
        sid = receipt.get("session_id")
        if sid: sids_seen.append(sid)
        pps_seen.append(os.path.realpath(receipt["project_path"]))
        bound[sc] = (receipt, ee_data)

    return bound


def validate_all_identity_bindings_reporting(manifest, eed_real, ee_shas,
                                            post_execution=False):
    """Same as validate_all_identity_bindings but does NOT exit.
    post_execution=True uses SCENARIO_POST_EXEC_STAGES (cleanup after runner).
    post_execution=False (default) uses SCENARIO_INITIAL_STAGE (verify/runner pre-exec).
    Returns (overall_ok, {scenario: (ok, errors_list, receipt_or_None, ee_data_or_None)})."""
    results = {}
    rts_seen, sids_seen, pps_seen = [], [], []
    all_ok = True

    for sc in REQUIRED_SCENARIOS:
        entry = manifest["scenarios"][sc]
        entry_rt = os.path.realpath(entry["runtime_root"])
        allowed = SCENARIO_POST_EXEC_STAGES[sc] if post_execution else None
        ok, errors, receipt, ee_data = _validate_one_scenario(
            sc, entry, entry_rt, eed_real, ee_shas, rts_seen, sids_seen, pps_seen,
            allowed_ledger_stages=allowed)
        results[sc] = (ok, errors, receipt, ee_data)
        if not ok:
            all_ok = False
        else:
            rts_seen.append(os.path.realpath(receipt["runtime_root"]))
            sid = receipt.get("session_id")
            if sid: sids_seen.append(sid)
            pps_seen.append(os.path.realpath(receipt["project_path"]))

    return all_ok, results


# ═══════════════════════════════════════════════════════════════
#  Output artifact path safety
# ═══════════════════════════════════════════════════════════════

def validate_output_path(opath, project_path):
    if not opath or not isinstance(opath, str):
        return False, "output_path is empty or not a string"
    opath_real = os.path.realpath(opath)
    pp_real = os.path.realpath(project_path)
    if not _strict_within(pp_real, opath_real):
        return False, f"output_path {opath_real} not within project {pp_real}"
    if _is_symlink_or_junction(opath_real):
        return False, f"output_path is symlink/junction: {opath_real}"
    canonical = os.path.join(pp_real, "rtl", "system_top.v")
    if opath_real != os.path.realpath(canonical):
        return False, f"output_path {opath_real} != canonical {canonical}"
    if not os.path.isfile(opath_real):
        return False, f"output_path not a file: {opath_real}"
    return True, "ok"
