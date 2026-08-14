"""O6 — public-only GPIO Skill contract.

These tests are deliberately mechanical.  The Skill is the only workflow
given to the fresh black-box agent in O7, so an obsolete escape example is a
product-contract defect even when the public MCP happens to work.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from mcps.zynq_mcp.control.capabilities import CONTROL_TOOLS, DOMAIN_TOOLS


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "zynq_gpio"
REPLAY = (ROOT / "validation_projects" / "o6_public_mcp_replay" /
          "run_public_replay.py")


def _documents() -> dict[str, str]:
    return {
        str(path.relative_to(SKILL_ROOT)).replace("\\", "/"): path.read_text(
            encoding="utf-8"
        )
        for path in sorted(SKILL_ROOT.rglob("*.md"))
    }


def _all_text() -> str:
    return "\n".join(_documents().values())


def test_o6_skill_contains_no_internal_escape_identifier() -> None:
    text = _all_text()
    forbidden = (
        "VivadoTclBridge",
        "publish_pl_build_manifest",
        "publish_ps_build_manifest",
        "mcps.zynq_mcp",
        ".zynq_runtime",
    )
    assert {token for token in forbidden if token in text} == set()


def test_o6_skill_contains_no_direct_process_or_build_recipe() -> None:
    text = _all_text()
    patterns = {
        "python internal import": r"(?m)^\s*(?:from|import)\s+mcps(?:\.|\s)",
        "process launcher": r"\b(?:subprocess|Popen|os\.system)\b",
        "direct EDA executable": r"\b(?:vivado|xsct|xsdb)(?:\.bat|\.exe)\b",
        "direct Tcl channel": r"\b(?:run_tcl|bridge\.eval)\b",
        "manual build command": r"(?im)(?:^|[`\s])make(?:\.exe)?(?:[`\s]|$)",
        "process killer": r"\b(?:taskkill|kill_process_tree)\b",
    }
    found = {
        name: sorted(set(re.findall(pattern, text, flags=re.IGNORECASE)))
        for name, pattern in patterns.items()
        if re.search(pattern, text, flags=re.IGNORECASE)
    }
    assert found == {}


def test_o6_skill_declares_public_boundary_and_recovery_policy() -> None:
    text = _all_text()
    required = (
        "公开边界（硬门禁）",
        "wait_operation",
        "get_operation_status",
        "diagnose_execution",
        "recover_execution",
        "recommended_action",
        "status_source",
        "observed_state",
        "vendor_status",
        "current_step",
        "observation_quality",
        "artifact_state",
        "last_progress_at",
        "deadline_at",
    )
    assert {token for token in required if token not in text} == set()
    assert "heartbeat_age_s" not in text
    assert "still_running" not in text


def test_o6_phase2_is_complete_public_mcp_chain() -> None:
    phase = _documents()["phases/2_pl_build.md"]
    expected = (
        "pl_generate_system_top",
        "pl_create_project",
        "pl_generate_target",
        "pl_synthesize",
        "pl_place",
        "pl_route",
        "pl_analyze_timing",
        "pl_generate_bitstream",
    )
    assert all(name in phase for name in expected)
    public_names = {tool.name for tool in CONTROL_TOOLS + DOMAIN_TOOLS}
    assert set(expected) <= public_names
    assert "自动发布" in phase
    assert "artifact_state=PUBLISHED" in phase


def test_o6_phase3_has_product_owned_compile_and_manifest_gate() -> None:
    phase = _documents()["phases/3_ps_software.md"]
    assert "`ps_compile` 是唯一正式编译入口" in phase
    assert "PS Manifest 已自动发布" in phase
    assert "artifact_state == \"PUBLISHED\"" in phase
    assert not re.search(r"(?im)(?:^|[`\s])make(?:\.exe)?(?:[`\s]|$)", phase)


def test_o6_consistency_is_fail_closed_with_all_three_manifests() -> None:
    phase = _documents()["phases/4_consistency.md"]
    assert "三类 Manifest 全部存在" in phase
    assert "all_passed == true" in phase
    assert "summary.failed == 0" in phase
    assert "summary.skipped == 0" in phase


def test_o6_uart_wait_requires_non_conflicting_markers() -> None:
    phase = _documents()["phases/5_deployment.md"]
    assert '"markers": ["WROTE:0x", "GPIO_E2E_PASS"]' in phase
    assert "要求列表中**全部** marker 出现" in phase
    assert "不存在\n`GPIO_E2E_FAIL`" in phase


def test_o6_public_workflow_tools_are_registered() -> None:
    public_names = {tool.name for tool in CONTROL_TOOLS + DOMAIN_TOOLS}
    required = {
        "create_session",
        "get_execution_state",
        "platform_generate",
        "pl_generate_system_top",
        "pl_create_project",
        "pl_generate_target",
        "pl_synthesize",
        "pl_place",
        "pl_route",
        "pl_analyze_timing",
        "pl_generate_bitstream",
        "ps_import_hardware",
        "ps_create_platform",
        "ps_create_bsp",
        "ps_create_app",
        "ps_add_sources",
        "ps_compile",
        "verify_consistency",
        "pl_program_fpga",
        "ps_start_uart_capture",
        "ps_wait_uart_capture",
        "ps_stop_uart_capture",
        "evaluate_observation",
    }
    assert required - public_names == set()


def test_o6_replay_harness_imports_only_public_sdk() -> None:
    source = REPLAY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name == "mcps" or name.startswith("mcps.")
                   for name in imported)
    assert any(name == "mcp" or name.startswith("mcp.")
               for name in imported)


def test_o6_replay_harness_contains_no_escape_path() -> None:
    source = REPLAY.read_text(encoding="utf-8")
    forbidden = (
        "VivadoTclBridge",
        "publish_pl_build_manifest",
        "publish_ps_build_manifest",
        "subprocess",
        "Popen",
        "run_tcl",
        "bridge.eval",
        "taskkill",
        "kill_process_tree",
    )
    assert {token for token in forbidden if token in source} == set()
    assert not re.search(
        r"(?im)(?:^|[`\s])make(?:\.exe)?(?:[`\s]|$)", source)
