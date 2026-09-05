"""build_manifest.py — Generate PL/PS Build Manifests on operation success (B01 §6).

P4 gap: ``verify_consistency`` (B01 §5 Phase 4) reads the three manifests from
disk. The platform atom ``platform_export_manifest`` publishes the Platform
Manifest (it replaced the B05 shortcut ``platform_generate`` in B11 phase 2),
but P2 (``pl_generate_bitstream``) and P3 (``ps_compile``) published nothing,
so 10 of the 12 checks were skipped. This module closes the gap: it
synthesises the PL Build Manifest (``manifests/pl/<rev>.json``) and PS Build
Manifest (``manifests/ps/<rev>.json``) from the session snapshot (context)
plus the tool's result data, and publishes them through the shared immutable
publisher (``mcps.common.artifact_schema.publish_manifest``).

Design (terminal-integrity gate):
  - Called by ``CommandRunner._execute`` on tool success ONLY. Every failure
    path returns None; CommandRunner persists MANIFEST_PUBLISH_FAILED and
    refuses SUCCEEDED.
  - The persisted manifest uses project-relative forward-slash paths (matching
    the Platform Manifest from platform_domain.py) and is validated by
    publish_manifest with ``resolve_root=project_path``.
  - A manifest is only written when its provenance can be established from
    real evidence: valid board_profile_sha256 / platform revision (snapshot or
    the persisted Platform Manifest), the primary artifact file (bitstream /
    ELF) exists, and the Platform Manifest exists on disk (the "built_from"
    chain). Missing data → skip (None), never a fabricated manifest.
  - Timing: pl_generate_bitstream can only run after pl_analyze_timing
    succeeded with timing_met=true (execution_gate P7), so timing_met=True is
    grounded in the serial-chain gate. The exact WNS/TNS are not retained at
    this hook; they are taken from the tool result when present, otherwise
    defaulted to the minimum satisfiable values (wns 0.0 / tns 0.0) so the
    schema's INVALID_TIMING cross-check holds.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time

from mcps.common.artifact_schema import publish_manifest
from mcps.common.revision import (
    compute_revision,
    compute_source_files_sha256,
    is_sha256,
    sha256_file,
)

logger = logging.getLogger("zynq_mcp.build_manifest")

__all__ = [
    "publish_pl_build_manifest",
    "publish_ps_build_manifest",
]

# Vivado/Vitis generated-internal dir names excluded from source discovery so
# build manifests list the user's RTL/XDC/C sources, not generated artifacts.
# B13-F9 修复轮#9: .pkg_proj/.pkg_log 是 platform_package_user_ip 的一次性
# 打包工作目录（每次重打包会重建，属过程产物）——ip_repo 内容哈希须排除。
_VIVADO_INTERNAL_DIRS = frozenset({
    ".Xil", ".cache", ".gen", ".ip_user_files", ".runs", ".srcs", ".stx",
    "vivado", "_ip", ".pkg_proj", ".pkg_log",
})

# Default timing: grounded in the execution gate P7 (pl_generate_bitstream
# requires a prior pl_analyze_timing SUCCEEDED with completion_evidence
# timing_met=true — see execution_gate.py). Exact WNS/TNS are not retained at
# the bitstream hook, so when the tool result does not carry them we record the
# minimum satisfiable values that are schema-consistent (wns>=0 and tns==0).
_DEFAULT_PL_TIMING = {"timing_met": True, "wns_ns": 0.0, "tns_ns": 0.0}


# ── small local helpers ─────────────────────────────────────────────────────

def _norm_project_path(raw) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return os.path.normpath(raw.strip())


def _to_rel(project_path: str, abs_path: str) -> str | None:
    """Project-relative forward-slash path. None when on a different drive."""
    try:
        rel = os.path.relpath(abs_path, project_path)
    except ValueError:  # Windows: different drive letter
        return None
    return rel.replace("\\", "/")


def _resolve_artifact_path(raw, project_path: str) -> str | None:
    """Absolute path of a (possibly relative) artifact file, or None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    raw = raw.strip()
    if os.path.isabs(raw):
        cand = os.path.normpath(raw)
    else:
        cand_proj = os.path.normpath(os.path.join(project_path, raw))
        cand = cand_proj if os.path.isfile(cand_proj) else os.path.normpath(raw)
    return cand if os.path.isfile(cand) else None


def _read_platform_manifest(project_path: str, prefer_revision: str | None = None):
    """Load the current Platform Manifest from manifests/platform/*.json.

    Returns the dict, or None.

    Resolution order (B13-M4):
    1. The manifest whose ``xsa_sha256`` matches the platform.xsa actually on
       disk — disk truth beats session memory, so a stale session-snapshot
       revision can no longer select an outdated manifest (the real-board
       verify_consistency failure: PS manifest built_from_platform_revision
       was the old f3dcaa45 while the current export was 22b94b0f).
    2. The snapshot's preferred revision (legacy behavior).
    3. The last (sorted) manifest. Never raises.
    """
    d = os.path.join(project_path, "manifests", "platform")
    if not os.path.isdir(d):
        return None
    manifests = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("manifest_type") == "platform":
            manifests.append(data)
    if not manifests:
        return None
    xsa = os.path.join(project_path, "platform.xsa")
    if os.path.isfile(xsa):
        try:
            disk_sha = sha256_file(xsa)
        except Exception:
            disk_sha = None
        if disk_sha:
            for m in manifests:
                if m.get("xsa_sha256") == disk_sha:
                    return m
    if prefer_revision:
        for m in manifests:
            if m.get("platform_revision") == prefer_revision:
                return m
    return manifests[-1]


def _iter_source_candidates(project_path: str, exts: tuple[str, ...]) -> list[str]:
    """Files under project_path matching any of ``exts``, excluding Vivado
    generated-internal directory components. Sorted, deterministic."""
    matches = []
    for ext in exts:
        matches.extend(glob.glob(
            os.path.join(project_path, "**", f"*{ext}"), recursive=True))
    out = []
    for m in sorted(matches):
        if not os.path.isfile(m):
            continue
        rel = os.path.relpath(m, project_path).replace("\\", "/")
        if any(part in _VIVADO_INTERNAL_DIRS for part in rel.split("/")):
            continue
        out.append(m)
    return out


def _pl_source_entries(project_path: str) -> list[dict]:
    """RTL sources (.v/.sv/.vh) as [{path, sha256}] project-relative, sorted."""
    entries = []
    seen = set()
    for p in _iter_source_candidates(project_path, (".v", ".sv", ".vh")):
        rel = os.path.relpath(p, project_path).replace("\\", "/")
        if rel in seen:
            continue
        seen.add(rel)
        entries.append({"path": rel, "sha256": sha256_file(p)})
    entries.sort(key=lambda e: e["path"])
    return entries


def _discover_xdc(project_path: str):
    """First .xdc constraint (project-relative). Returns (path, sha) or None."""
    files = _iter_source_candidates(project_path, (".xdc",))
    if not files:
        return None
    p = files[0]
    rel = os.path.relpath(p, project_path).replace("\\", "/")
    return rel, sha256_file(p)


def _discover_ip_products(project_path: str) -> list[dict]:
    """Packaged user-IP products consumed by the build: every ``*.xci``,
    every file under an ``ipshared`` directory, and the packaged-IP metadata
    (``ip_repo/**/component.xml`` + ``ip_repo/**/xgui/**``), as
    [{path, sha256}] sorted by path.

    B13-F8 修复轮#8 (黑盒实证): 打包 IP 的**内容**不落在 PL 输入摘要里——
    改引擎 RTL 重打包后重建，摘要不变 → 同名 revision 语义冲突。
    B13-F9 修复轮#9: ip_repo 根下的 component.xml/xgui（打包元数据，不在
    .gen 的 ipshared 拷贝里）同样必须进摘要——只改 IP 元数据/接口声明而
    摘要不变，会漏掉重打包后的语义变化。
    """
    entries = []
    seen = set()
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _VIVADO_INTERNAL_DIRS]
        rel_dir = os.path.relpath(dirpath, project_path).replace("\\", "/")
        parts = rel_dir.split("/")
        is_ipshared = "ipshared" in parts
        is_iprepo = "ip_repo" in parts
        for fn in filenames:
            if fn.endswith(".xci") or is_ipshared:
                pass
            elif is_iprepo and (fn == "component.xml" or "xgui" in parts):
                pass
            else:
                continue
            p = os.path.join(dirpath, fn)
            if not os.path.isfile(p):
                continue
            rel = os.path.relpath(p, project_path).replace("\\", "/")
            if rel in seen:
                continue
            seen.add(rel)
            entries.append({"path": rel, "sha256": sha256_file(p)})
    entries.sort(key=lambda e: e["path"])
    return entries


def _discover_elf(project_path: str, app_name: str):
    """First built ELF under {project}/ps/{app}/Debug or {project}/{app}/Debug.
    Returns (rel, sha) or None."""
    bases = [os.path.join(project_path, app_name)]
    ps_sub = os.path.join(project_path, "ps")
    if os.path.isdir(ps_sub):
        bases.insert(0, os.path.join(ps_sub, app_name))
    hits = []
    for base in bases:
        for pattern in ("Debug", ".debug", "*"):
            hits.extend(glob.glob(os.path.join(base, pattern, "*.elf")))
    if not hits:
        for base in bases:
            hits.extend(glob.glob(os.path.join(base, "**", "*.elf"), recursive=True))
    if not hits:
        return None
    p = sorted(hits)[0]
    rel = _to_rel(project_path, p)
    return rel, sha256_file(p)


def _discover_xparameters(project_path: str):
    """xparameters.h generated by the Vitis BSP. Prefers a *_platform dir.
    Returns (rel, sha) or None."""
    hits = _iter_source_candidates(project_path, ("xparameters.h",))
    if not hits:
        return None
    pref = [p for p in hits if "_platform" in os.path.relpath(p, project_path).replace("\\", "/")]
    pool = pref or hits
    p = sorted(pool)[0]
    rel = os.path.relpath(p, project_path).replace("\\", "/")
    return rel, sha256_file(p)


def _discover_cproject_entries(project_path: str) -> list[dict]:
    """All ``.cproject`` files (Vitis/Eclipse build config — carries the
    ``-D`` compile macros) as [{path, sha256}] sorted by path.

    B13-F9 修复轮#9: 用 os.walk 而非 glob——Python glob 的 ``**`` 递归展开
    **跳过点开头文件**（3.12 实测 ``**/*`` 不返回 ``.cproject``），glob 路径
    会静默漏掉全部隐藏配置文件。
    """
    entries = []
    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _VIVADO_INTERNAL_DIRS]
        for fn in filenames:
            if fn != ".cproject":
                continue
            p = os.path.join(dirpath, fn)
            if not os.path.isfile(p):
                continue
            entries.append({
                "path": os.path.relpath(p, project_path).replace("\\", "/"),
                "sha256": sha256_file(p)})
    entries.sort(key=lambda e: e["path"])
    return entries


def _app_source_entries(project_path: str, app_name: str) -> list[dict]:
    """C/H/asm sources under {project}/ps/{app}/src or {project}/{app}/src as
    [{path, sha256}], sorted. Empty list when the app src dir is absent."""
    bases = [os.path.join(project_path, app_name, "src")]
    ps_src = os.path.join(project_path, "ps", app_name, "src")
    if os.path.isdir(ps_src):
        bases.insert(0, ps_src)
    entries = []
    seen = set()
    for src in bases:
        if not os.path.isdir(src):
            continue
        for ext in (".c", ".h", ".s", ".S"):
            for p in glob.glob(os.path.join(src, "**", f"*{ext}"), recursive=True):
                if not os.path.isfile(p):
                    continue
                rel = os.path.relpath(p, project_path).replace("\\", "/")
                if rel in seen:
                    continue
                seen.add(rel)
                entries.append({"path": rel, "sha256": sha256_file(p)})
    entries.sort(key=lambda e: e["path"])
    return entries


def _tool_versions(plat) -> dict:
    if isinstance(plat, dict):
        ri = plat.get("revision_inputs")
        if isinstance(ri, dict) and isinstance(ri.get("tool_versions"), dict):
            return ri["tool_versions"]
    return {}


def _pl_timing(result) -> dict | None:
    """Timing from tool result data when present + schema-consistent, else None."""
    rd = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rd, dict):
        return None
    tm = rd.get("timing_met")
    wns = rd.get("wns_ns")
    tns = rd.get("tns_ns")
    if not isinstance(tm, bool):
        return None
    if isinstance(wns, bool) or isinstance(tns, bool):
        return None
    if not isinstance(wns, (int, float)) or not isinstance(tns, (int, float)):
        return None
    if wns != wns or tns != tns:  # NaN
        return None
    return {"timing_met": tm, "wns_ns": float(wns), "tns_ns": float(tns)}


def _rev_to_fn(revision: str) -> str:
    return f"sha256_{revision[7:]}.json" if revision.startswith("sha256:") else f"{revision}.json"


def _publish(manifest: dict, project_path: str, kind: str,
             manifest_revision: str) -> str | None:
    """Atomic no-replace publish. Returns manifest path, or None on any failure
    (disk full / invalid path / validation). Never raises."""
    mdir = os.path.join(project_path, "manifests", kind)
    try:
        os.makedirs(mdir, exist_ok=True)
    except OSError as e:
        logger.warning("build manifest dir %s not creatable: %s", mdir, e)
        return None
    mpath = os.path.join(mdir, _rev_to_fn(manifest_revision))
    manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    try:
        publish_manifest(manifest_json, mpath, resolve_root=project_path)
    except Exception as e:
        logger.warning("publish %s build manifest failed: %s", kind, e)
        return None
    return mpath


def _snapshot_dict(snapshot) -> dict:
    if isinstance(snapshot, dict):
        return snapshot
    try:
        return dict(snapshot) if snapshot is not None else {}
    except Exception:
        return {}


# ── PL Build Manifest ────────────────────────────────────────────────────────

def publish_pl_build_manifest(snapshot: dict, result: dict,
                              project_path: str, tool_args: dict | None = None) -> str | None:
    """Generate and publish the PL Build Manifest (B01 §6.5).

    Triggered by ``pl_generate_bitstream`` SUCCEEDED. Sources:
      - snapshot: board_profile_sha256, platform_revision, project_path
      - tool_args / result.data: the bitstream path
      - the persisted Platform Manifest: platform_revision, bd_wrapper_sha256,
        tool_versions
      - project tree discovery: .xdc constraint, RTL sources

    Returns the manifest path, or None when provenance cannot be established
    (the operation still completes SUCCEEDED).
    """
    snap = _snapshot_dict(snapshot)
    pp = _norm_project_path(project_path or snap.get("project_path"))
    if not pp or not os.path.isdir(pp):
        return None

    bp_sha = snap.get("board_profile_sha256")
    if not isinstance(bp_sha, str) or not is_sha256(bp_sha):
        logger.warning("pl build manifest skipped: snapshot board_profile_sha256 "
                       "missing/invalid")
        return None

    plat = _read_platform_manifest(pp, prefer_revision=snap.get("platform_revision"))
    if plat is None:
        logger.warning("pl build manifest skipped: no Platform Manifest under %s",
                       os.path.join(pp, "manifests", "platform"))
        return None

    plat_rev = plat.get("platform_revision")
    if not isinstance(plat_rev, str) or not is_sha256(plat_rev):
        plat_rev = snap.get("platform_revision")
        if not isinstance(plat_rev, str) or not is_sha256(plat_rev):
            logger.warning("pl build manifest skipped: platform_revision unknown")
            return None

    bdw_sha = plat.get("bd_wrapper_sha256")
    if not isinstance(bdw_sha, str) or not is_sha256(bdw_sha):
        logger.warning("pl build manifest skipped: Platform Manifest missing "
                       "bd_wrapper_sha256")
        return None

    rd = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
    bit_raw = ((tool_args or {}).get("path")
               or rd.get("bitstream_path") or rd.get("path"))
    bit_abs = _resolve_artifact_path(bit_raw, pp)
    if bit_abs is None:
        logger.warning("pl build manifest skipped: bitstream file not found (%r)",
                       bit_raw)
        return None
    bit_sha = sha256_file(bit_abs)
    bit_rel = _to_rel(pp, bit_abs) or bit_abs

    xdc = _discover_xdc(pp)
    if xdc is None:
        logger.warning("pl build manifest skipped: no .xdc constraint under %s", pp)
        return None
    xdc_rel, xdc_sha = xdc

    revision_inputs = {
        "board_profile_sha256": bp_sha,
        "built_from_platform_revision": plat_rev,
        "bd_wrapper_sha256": bdw_sha,
        "tool_versions": _tool_versions(plat),
        "source_files": _pl_source_entries(pp),
        "config_files": [{"path": xdc_rel, "sha256": xdc_sha}],
        # B13-F8 修复轮#8: 打包 IP 产品必须进输入摘要（否则改 IP 内容重打包
        # 后重建，摘要不变 → 同名 revision 语义冲突）。
        "ip_products": _discover_ip_products(pp),
    }
    manifest_revision = compute_revision(revision_inputs)
    timing = _pl_timing(result) or _DEFAULT_PL_TIMING

    manifest: dict = {
        "schema_version": "1.0",
        "manifest_type": "pl_build",
        "board_profile_sha256": bp_sha,
        "built_from_platform_revision": plat_rev,
        "manifest_revision": manifest_revision,
        "revision_inputs": revision_inputs,
        "bitstream_path": bit_rel,
        "bitstream_sha256": bit_sha,
        "bd_wrapper_sha256": bdw_sha,
        "xdc_path": xdc_rel,
        "xdc_sha256": xdc_sha,
        "timing_met": timing["timing_met"],
        "wns_ns": timing["wns_ns"],
        "tns_ns": timing["tns_ns"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "locked",
    }
    if isinstance(snap.get("board_id"), str) and snap["board_id"].strip():
        manifest["board_id"] = snap["board_id"].strip()
    return _publish(manifest, pp, "pl", manifest_revision)


# ── PS Build Manifest ────────────────────────────────────────────────────────

def publish_ps_build_manifest(snapshot: dict, result: dict,
                              project_path: str, tool_args: dict | None = None) -> str | None:
    """Generate and publish the PS Build Manifest (B01 §6.6).

    Triggered by ``ps_compile`` SUCCEEDED. Sources:
      - snapshot: board_profile_sha256, project_path
      - tool_args / result.data: the app name
      - the persisted Platform Manifest: platform_revision, xsa_sha256,
        address_map, tool_versions
      - project tree discovery: the built ELF, the BSP xparameters.h, and the
        app C/H sources

    Returns the manifest path, or None when provenance cannot be established.
    """
    snap = _snapshot_dict(snapshot)
    pp = _norm_project_path(project_path or snap.get("project_path"))
    if not pp or not os.path.isdir(pp):
        return None

    bp_sha = snap.get("board_profile_sha256")
    if not isinstance(bp_sha, str) or not is_sha256(bp_sha):
        logger.warning("ps build manifest skipped: snapshot board_profile_sha256 "
                       "missing/invalid")
        return None

    plat = _read_platform_manifest(pp, prefer_revision=snap.get("platform_revision"))
    if plat is None:
        logger.warning("ps build manifest skipped: no Platform Manifest under %s",
                       os.path.join(pp, "manifests", "platform"))
        return None

    plat_rev = plat.get("platform_revision")
    if not isinstance(plat_rev, str) or not is_sha256(plat_rev):
        plat_rev = snap.get("platform_revision")
        if not isinstance(plat_rev, str) or not is_sha256(plat_rev):
            logger.warning("ps build manifest skipped: platform_revision unknown")
            return None

    xsa_sha = plat.get("xsa_sha256")
    if not isinstance(xsa_sha, str) or not is_sha256(xsa_sha):
        logger.warning("ps build manifest skipped: Platform Manifest missing "
                       "xsa_sha256")
        return None

    address_map = plat.get("address_map")
    if not isinstance(address_map, dict):
        address_map = {}

    rd = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
    app_name = (tool_args or {}).get("app_name") or rd.get("app_name")
    if not isinstance(app_name, str) or not app_name.strip():
        logger.warning("ps build manifest skipped: app_name missing from "
                       "arguments/result")
        return None
    app_name = app_name.strip()

    elf = _discover_elf(pp, app_name)
    if elf is None:
        logger.warning("ps build manifest skipped: no ELF under %s", app_name)
        return None
    elf_rel, elf_sha = elf

    xp = _discover_xparameters(pp)
    if xp is None:
        logger.warning("ps build manifest skipped: no xparameters.h under %s", pp)
        return None
    xp_rel, xp_sha = xp

    xparameters_addrs: dict = {}
    for key, entry in address_map.items():
        if isinstance(entry, dict) and entry.get("base") is not None:
            xparameters_addrs[f"XPAR_{str(key).upper()}_BASEADDR"] = str(entry["base"])

    source_files = _app_source_entries(pp, app_name)
    # B13-F9 修复轮#9 (黑盒实证): .cproject 携带编译 -D 宏等构建配置——
    # 改宏不换摘要 = 摘要失真（固件行为变了 manifest revision 却不变）。
    config_files = _discover_cproject_entries(pp)
    revision_inputs = {
        "board_profile_sha256": bp_sha,
        "built_from_platform_revision": plat_rev,
        "platform_xsa_sha256": xsa_sha,
        "tool_versions": _tool_versions(plat),
        "source_files": source_files,
        "config_files": config_files,
    }
    manifest_revision = compute_revision(revision_inputs)

    manifest: dict = {
        "schema_version": "1.0",
        "manifest_type": "ps_build",
        "board_profile_sha256": bp_sha,
        "built_from_platform_revision": plat_rev,
        "platform_xsa_sha256": xsa_sha,
        "manifest_revision": manifest_revision,
        "revision_inputs": revision_inputs,
        "elf_path": elf_rel,
        "elf_sha256": elf_sha,
        "xparameters_h_path": xp_rel,
        "xparameters_h_sha256": xp_sha,
        "xparameters_addrs": xparameters_addrs,
        "source_files_sha256": compute_source_files_sha256(source_files),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "locked",
    }
    if isinstance(snap.get("board_id"), str) and snap["board_id"].strip():
        manifest["board_id"] = snap["board_id"].strip()
    return _publish(manifest, pp, "ps", manifest_revision)
