#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bypass_audit.py — 行为偏离审计器（docs/development/validation_methodology.md §二）

用法:
  python tools/audit/bypass_audit.py <workspace_dir> [--out report.json]

扫描工作区下所有调用记录（*.jsonl 每行 JSON、*.json 文档，凡含 "tool" /
"tool_name" 键的节点计一次调用），对照 MCP 公开工具面
（capabilities.ALL_TOOLS，109 工具），机械取证六类偏离中可机器检测的四类:

  1. 工具使用矩阵：每工具 调用数 / 成功 / 失败 / 未定 / 重试数
  2. 未调用工具清单（Bypass 候选——「未涉及」或「已绕开」由审核者标注）
  3. 失败调用清单（Workaround 被吸收候选，逐条登记）
  4. 重试突发（Retry：同文件内同工具同签名 ≥3 次）
  5. 替代候选（Substitution：失败调用后同文件内出现另一工具的成功）
  6. 外部替代线索（External Path：xsdb/tcl/vivado batch 脚本）

Workaround / Silent Fix 属认知层，机器抓不到——由 Agent 自述 + 主代理复核
（见方法论 §二）。最终 A–F 分类（合法跳过/MCP缺陷/Skill缺陷/Agent误判/
能力重叠/环境问题）由审核者判定，本工具只取证不判刑。

退出码: 0=审计完成。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from mcps.zynq_mcp.control.capabilities import ALL_TOOLS  # noqa: E402

_SUCCESS_MARKERS = ("SUCCEEDED", '"status": "success"', '"status": "accepted"',
                    '"verdict": "PASS"', '"all_passed": true')
_FAILURE_MARKERS = ("OUTCOME_UNKNOWN", '"status": "error"', '"verdict": "FAIL"',
                    "TIMED_OUT", "INTERRUPTED", "RECOVERY_REQUIRED",
                    "FAILED", "STAGE_PREREQUISITE_UNMET", "CHANNEL_BUSY",
                    "LOCK_BUSY", "ADAPTER_NOT_READY", "ROLLBACK_TARGET_INVALID",
                    "Preflight: CHANNEL")

_EXTERNAL_PY_RE = re.compile(r"\bxsdb\b|\bmrd\b|\bfpga -f\b|vivado.*-mode|xelab\.exe")
_SKIP_DIRS = {".git", "__pycache__"}


def _iter_json_docs(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if not (fn.endswith(".json") or fn.endswith(".jsonl")):
                continue
            path = os.path.join(dirpath, fn)
            yield path


def _docs_from_file(path: str):
    docs = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if path.endswith(".jsonl"):
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        docs.append(json.loads(line))
                    except ValueError:
                        pass
            else:
                try:
                    docs.append(json.load(f))
                except ValueError:
                    pass
    except OSError:
        pass
    return docs


def _flatten(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("tool", "tool_name") and isinstance(v, str):
                out.append({"name": v, "node": node, "key": k})
            _flatten(v, out)
    elif isinstance(node, list):
        for item in node:
            _flatten(item, out)


def _classify(node: dict) -> str:
    # 用原始字符串值分类（json.dumps 会把嵌入 JSON 的引号转义，导致
    # '"status": "success"' 这类 marker 匹配不到真实文本）。
    parts = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if isinstance(v, str):
                    parts.append(v)
                else:
                    walk(v)
        elif isinstance(n, list):
            for item in n:
                walk(item)

    walk(node)
    text = " ".join(parts)
    if any(m in text for m in _FAILURE_MARKERS):
        return "failure"
    if any(m in text for m in _SUCCESS_MARKERS):
        return "success"
    return "unknown"


def _external_scripts(root: str):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, root)
            if fn.endswith(".tcl"):
                hits.append({"path": rel, "matched": "tcl-script"})
                continue
            if fn.endswith(".py"):
                try:
                    with open(path, "r", encoding="utf-8",
                              errors="replace") as f:
                        head = f.read(20000)
                except OSError:
                    continue
                m = _EXTERNAL_PY_RE.search(head)
                if m:
                    hits.append({"path": rel, "matched": m.group(0)[:40]})
    return hits


def _log_index(root: str) -> dict:
    """索引全部 *.log 文件: stem(小写) -> 路径。用于 seq 步骤 tag/args_file
    与每步结果日志的配对（白盒 mcp_sequence 跑法的惯例: step.tag == 日志名）。"""
    index = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".log"):
                index.setdefault(fn[:-4].lower(), []).append(
                    os.path.join(dirpath, fn))
    return index


def _classify_text(text: str) -> str:
    if any(m in text for m in _FAILURE_MARKERS):
        return "failure"
    if any(m in text for m in _SUCCESS_MARKERS):
        return "success"
    return "unknown"


def _paired_log_verdict(node: dict, log_index: dict) -> str:
    """step 节点带 tag/args_file 时，从同名 .log 提取真实结果判定。"""
    for key in ("tag", "args_file"):
        stem = node.get(key)
        if not isinstance(stem, str) or not stem.strip():
            continue
        stem = stem.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if stem.lower().endswith(".json"):
            stem = stem[:-5]
        paths = log_index.get(stem.lower())
        if not paths:
            continue
        for p in paths[:3]:
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(100000)
            except OSError:
                continue
            verdict = _classify_text(text)
            if verdict != "unknown":
                return verdict
    return "unknown"


def _signature(node: dict) -> str:
    """同一次调用在不同文件间的稳定签名（重试突发检测用）：
    tag > args_file > args > 节点本身。"""
    for key in ("tag", "args_file"):
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return f"{key}:{v.strip()}"
    args = node.get("args")
    if isinstance(args, (dict, list)):
        return "args:" + json.dumps(args, sort_keys=True, ensure_ascii=False)
    return "node:" + json.dumps(node, sort_keys=True, ensure_ascii=False)[:120]


_EQUIVALENT_PAIRS = {
    # 同一能力存在两个 MCP 工具时的真实替代对（其余跨工具序列顺序不视为替代）
    "pl_program_fpga": "pl_program_device",
    "pl_program_device": "pl_program_fpga",
    "ps_import_hardware": "ps_update_hardware",
    "ps_update_hardware": "ps_import_hardware",
    "ps_write_uart": "ps_write_register",
}


def _detect_retries_and_substitutions(per_file):
    """重试突发 = 同文件内同 (tool, signature) ≥3 次；
    替代候选 = 失败调用后同文件内出现**等价工具对**的成功调用
    （等价对白名单 _EQUIVALENT_PAIRS——弱信号，仍须人工甄别）。"""
    retry_bursts = []
    substitution_candidates = []
    for file_rel, occ in per_file.items():
        occ = sorted(occ, key=lambda o: o["idx"])
        # 重试突发: 按 (tool, sig) 分组
        groups = {}
        for o in occ:
            groups.setdefault((o["tool"], o["sig"]), []).append(o)
        for (tool, sig), items in groups.items():
            if len(items) >= 3:
                retry_bursts.append({
                    "tool": tool, "signature": sig, "file": file_rel,
                    "count": len(items),
                    "verdicts": [i["verdict"] for i in items]})
        # 替代候选: 失败 → 之后同文件内等价工具成功
        failed = [o for o in occ if o["verdict"] == "failure"]
        for f in failed:
            equiv = _EQUIVALENT_PAIRS.get(f["tool"])
            if not equiv:
                continue
            for o in occ:
                if o["idx"] > f["idx"] and o["tool"] == equiv \
                        and o["verdict"] == "success":
                    substitution_candidates.append({
                        "from": f["tool"], "to": o["tool"],
                        "file": file_rel,
                        "from_snippet": f["snippet"][:100]})
                    break  # 只记最近的一个成功者
    return retry_bursts, substitution_candidates


def audit(workspace: str) -> dict:
    declared = sorted(t.name for t in ALL_TOOLS)
    counts = {name: {"calls": 0, "success": 0, "failure": 0, "unknown": 0,
                     "retries": 0}
              for name in declared}
    failed_calls = []
    files_scanned = 0
    log_index = _log_index(workspace)
    per_file = {}
    for path in _iter_json_docs(workspace):
        docs = _docs_from_file(path)
        if not docs:
            continue
        files_scanned += 1
        rel = os.path.relpath(path, workspace)
        occ = per_file.setdefault(rel, [])
        for doc in docs:
            hits = []
            _flatten(doc, hits)
            for h in hits:
                name = h["name"]
                verdict = _classify(h["node"])
                if verdict == "unknown":
                    verdict = _paired_log_verdict(h["node"], log_index)
                occ.append({"tool": name, "sig": _signature(h["node"]),
                            "verdict": verdict, "idx": len(occ),
                            "snippet": json.dumps(h["node"],
                                                  ensure_ascii=False)[:200]})
                if name in counts:
                    counts[name]["calls"] += 1
                    counts[name][verdict] += 1
                    if verdict == "failure":
                        failed_calls.append({
                            "tool": name, "file": rel,
                            "snippet": json.dumps(h["node"],
                                                  ensure_ascii=False)[:200]})
    retry_bursts, substitution_candidates = \
        _detect_retries_and_substitutions(per_file)
    # 重试计数: 突发中除第一次外的次数记为该工具 retries
    for b in retry_bursts:
        if b["tool"] in counts:
            counts[b["tool"]]["retries"] += b["count"] - 1
    never = sorted(n for n, c in counts.items() if c["calls"] == 0)
    report = {
        "workspace": workspace,
        "declared_tools": len(declared),
        "files_scanned": files_scanned,
        "tool_matrix": counts,
        "tools_never_called": never,
        "never_called_count": len(never),
        "failed_calls": failed_calls,
        "failed_call_count": len(failed_calls),
        "retry_bursts": retry_bursts,
        "retry_burst_count": len(retry_bursts),
        "substitution_candidates": substitution_candidates,
        "substitution_count": len(substitution_candidates),
        "external_scripts": _external_scripts(workspace),
    }
    return report


def print_summary(report: dict) -> None:
    called = {n: c for n, c in report["tool_matrix"].items() if c["calls"]}
    total_calls = sum(c["calls"] for c in called.values())
    print(f"workspace: {report['workspace']}")
    print(f"声明工具 {report['declared_tools']} | 实际调用 {len(called)} | "
          f"从未调用 {report['never_called_count']} | "
          f"失败调用 {report['failed_call_count']} | "
          f"重试突发 {report['retry_burst_count']} | "
          f"替代候选 {report['substitution_count']} | "
          f"外部脚本线索 {len(report['external_scripts'])} | "
          f"扫描文件 {report['files_scanned']} | 总调用 {total_calls}")
    print("\n== 调用最多的 15 个工具 ==")
    for n, c in sorted(called.items(), key=lambda kv: -kv[1]["calls"])[:15]:
        print(f"  {n:32s} calls={c['calls']:4d} ok={c['success']:4d} "
              f"fail={c['failure']:3d} unk={c['unknown']:3d} "
              f"retry={c['retries']:3d}")
    print("\n== 从未调用工具（审计对象）==")
    for n in report["tools_never_called"]:
        print(f"  {n}")
    if report["retry_bursts"]:
        print("\n== 重试突发（Retry 偏离证据，前 15）==")
        for b in report["retry_bursts"][:15]:
            print(f"  {b['tool']} x{b['count']} @ {b['file']} "
                  f"({b['signature'][:60]})")
    if report["substitution_candidates"]:
        print("\n== 替代候选（Substitution 偏离证据，前 15）==")
        for s in report["substitution_candidates"][:15]:
            print(f"  {s['from']} -> {s['to']} @ {s['file']}")
    if report["failed_calls"]:
        print("\n== 失败调用（被吸收错误候选，前 20）==")
        for f in report["failed_calls"][:20]:
            print(f"  {f['tool']} @ {f['file']} :: {f['snippet'][:110]}")
    if report["external_scripts"]:
        print("\n== 外部替代线索（前 20）==")
        for e in report["external_scripts"][:20]:
            print(f"  {e['path']} ({e['matched']})")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="白盒绕行审计器")
    ap.add_argument("workspace")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    report = audit(args.workspace)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"[report written] {args.out}")
    print_summary(report)


if __name__ == "__main__":
    main()
