"""B11 phase 1 — public-only generalized Skill contract (skills/zynq_dev).

The old O6 contract (10 tests) asserted the GPIO Skill's public-only discipline.
The GPIO Skill is archived (plan A) and replaced by the generalized framework
Skill ``skills/zynq_dev`` (S0-S8, zero project-specific terms).  These tests are
deliberately mechanical: the Skill is the only workflow given to the fresh
black-box agent, so an obsolete escape example or a leaked project term is a
product-contract defect even when the public MCP happens to work.

Old → new mapping (10 tests, same file, no net decrease):
  1 test_o6_skill_contains_no_internal_escape_identifier
      → test_skill_contains_no_internal_escape_identifier
  2 test_o6_skill_contains_no_direct_process_or_build_recipe
      → test_skill_contains_no_direct_process_or_build_recipe
  3 test_o6_skill_declares_public_boundary_and_recovery_policy
      → test_skill_declares_public_boundary_and_recovery_policy
  4 test_o6_phase2_is_complete_public_mcp_chain
      → test_skill_pl_build_chain_is_complete_public_mcp_chain (appendix §4)
  5 test_o6_phase3_has_product_owned_compile_and_manifest_gate
      → test_skill_ps_compile_is_product_owned_with_manifest_gate (appendix §5)
  6 test_o6_consistency_is_fail_closed_with_all_three_manifests
      → test_skill_consistency_is_fail_closed_with_all_three_manifests (appendix §2)
  7 test_o6_uart_wait_requires_non_conflicting_markers
      → test_skill_uart_wait_requires_non_conflicting_markers (appendix §6)
  8 test_o6_public_workflow_tools_are_registered
      → test_skill_public_workflow_tools_are_registered (platform_generate dropped)
  9 test_o6_replay_harness_imports_only_public_sdk
      → test_replay_harness_imports_only_public_sdk
 10 test_o6_replay_harness_contains_no_escape_path
      → test_replay_harness_contains_no_escape_path
  + NEW test_skill_mechanical_gate_zero_project_terms (the B11 phase-1 gate
    scan as a regression test: gpio / 0x41200000 / LED / breath / blink → 0).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from mcps.zynq_mcp.control.capabilities import CONTROL_TOOLS, DOMAIN_TOOLS


ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills" / "zynq_dev"
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


def test_skill_contains_no_internal_escape_identifier() -> None:
    text = _all_text()
    forbidden = (
        "VivadoTclBridge",
        "publish_pl_build_manifest",
        "publish_ps_build_manifest",
        "mcps.zynq_mcp",
        ".zynq_runtime",
    )
    assert {token for token in forbidden if token in text} == set()


def test_skill_contains_no_direct_process_or_build_recipe() -> None:
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


def test_skill_declares_public_boundary_and_recovery_policy() -> None:
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


def test_skill_pl_build_chain_is_complete_public_mcp_chain() -> None:
    appendix = _documents()["appendix_mechanics.md"]
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
    assert all(name in appendix for name in expected)
    public_names = {tool.name for tool in CONTROL_TOOLS + DOMAIN_TOOLS}
    assert set(expected) <= public_names
    assert "自动发布" in appendix
    assert 'artifact_state == "PUBLISHED"' in appendix


def test_skill_ps_compile_is_product_owned_with_manifest_gate() -> None:
    appendix = _documents()["appendix_mechanics.md"]
    assert "`ps_compile` 是唯一正式编译入口" in appendix
    assert "PS Manifest 已自动发布" in appendix
    assert 'artifact_state == "PUBLISHED"' in appendix
    assert not re.search(
        r"(?im)(?:^|[`\s])make(?:\.exe)?(?:[`\s]|$)", appendix
    )


def test_skill_consistency_is_fail_closed_with_all_three_manifests() -> None:
    appendix = _documents()["appendix_mechanics.md"]
    assert "三类 Manifest 全部存在" in appendix
    assert "all_passed == true" in appendix
    assert "summary.failed == 0" in appendix
    assert "summary.skipped == 0" in appendix


def test_skill_uart_wait_requires_non_conflicting_markers() -> None:
    appendix = _documents()["appendix_mechanics.md"]
    assert '"markers": ["<PASS_MARKER>"]' in appendix
    assert "要求列表中**全部** marker 出现" in appendix
    assert "不存在 `<FAIL_MARKER>`" in appendix


def test_skill_public_workflow_tools_are_registered() -> None:
    text = _all_text()
    public_names = {tool.name for tool in CONTROL_TOOLS + DOMAIN_TOOLS}
    required = {
        # control
        "create_session",
        "close_session",
        "get_capabilities",
        "get_execution_state",
        "get_operation_status",
        "wait_operation",
        "diagnose_execution",
        "recover_execution",
        # platform atoms (the generalized sequence; no shortcut tool)
        "platform_create_design",
        "platform_add_ps7",
        "platform_configure_ps7",
        "platform_add_ip",
        "platform_list_ips",
        "platform_connect_interface",
        "platform_connect_clock",
        "platform_connect_reset",
        "platform_set_address",
        "platform_validate",
        "platform_generate_wrapper",
        "platform_export_hardware",
        "platform_export_manifest",
        # pl
        "pl_generate_system_top",
        "pl_create_project",
        "pl_generate_target",
        "pl_synthesize",
        "pl_place",
        "pl_route",
        "pl_analyze_timing",
        "pl_generate_bitstream",
        "pl_program_fpga",
        # ps (build chain + JTAG deploy + UART capture + diagnostics)
        "ps_import_hardware",
        "ps_create_platform",
        "ps_create_bsp",
        "ps_create_app",
        "ps_add_sources",
        "ps_compile",
        "ps_get_build_status",
        "ps_read_elf_info",
        "ps_connect_hw_server",
        "ps_list_targets",
        "ps_select_target",
        "ps_get_target_status",
        "ps_halt_target",
        "ps_reset_target",
        "ps_initialize_ps",
        "ps_load_hardware",
        "ps_download_elf",
        "ps_run_target",
        "ps_ensure_arm_accessible",
        "ps_list_serial_ports",
        "ps_start_uart_capture",
        "ps_wait_uart_capture",
        "ps_stop_uart_capture",
        "ps_diagnose_uart_clock",
        "ps_recover_target",
        "ps_reconnect_target",
        "ps_clear_debug_session",
        "ps_reg_read",
        # verification
        "verify_consistency",
        "evaluate_observation",
    }
    assert required - public_names == set()
    # B11 phase 2: the specialized shortcut tool is removed from the public
    # contract — it must not be registered, and the skill must not reference
    # it. The contract is the atom sequence (12 command atoms + 2 query atoms).
    assert "platform_generate" not in public_names
    assert len(CONTROL_TOOLS) + len(DOMAIN_TOOLS) == 100  # 9 control + 91 domain
    # The generalized skill must not reference the specialized shortcut tool
    # (GPIO-fixed, removed in B11 phase 2): the contract is the atom sequence.
    # Whole-word match so platform_generate_wrapper / platform_export_manifest
    # (the legitimate atom names) are not flagged.
    assert not re.search(r"\bplatform_generate\b", text)


def test_replay_harness_imports_only_public_sdk() -> None:
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


def test_replay_harness_contains_no_escape_path() -> None:
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


def test_skill_mechanical_gate_zero_project_terms() -> None:
    """B11 phase-1 gate as a regression test: the generalized Skill must have
    zero hits for the old exam-peripheral vocabulary. This mirrors the manual
    Select-String gate run over ``skills/zynq_dev`` in the B11 phase-1 report.
    """
    text = _all_text()
    patterns = {
        "gpio (case-insensitive)": re.compile(r"gpio", re.IGNORECASE),
        "0x41200000 and hex variants": re.compile(
            r"0[xX]4120[0-9a-fA-F_]*|41200000"
        ),
        "LED whole word (case-sensitive)": re.compile(r"\bLED\b"),
        "legacy project-name terms (case-insensitive)": re.compile(
            r"breath|blink", re.IGNORECASE
        ),
    }
    hits = {
        name: sorted({match.group(0) for match in pattern.finditer(text)})
        for name, pattern in patterns.items()
        if pattern.search(text)
    }
    assert hits == {}
