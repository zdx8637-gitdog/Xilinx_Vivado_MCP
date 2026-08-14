# Test Reports & Evidence

> 目录: `docs/development/tests/`
> 当前 Brick: **B04 R3.1-C PENDING FREEZE CONFIRMATION**

## 索引

| Brick / Sub-step | 文件 | 状态 |
|-----------------|------|------|
| B01 | [B01_gpio_acceptance_spec.md](B01_gpio_acceptance_spec.md) | ✅ FROZEN |
| B02 | [B02_contract_test_plan.md](B02_contract_test_plan.md) | ✅ FROZEN |
| B03 | [B03_board_environment_test_plan.md](B03_board_environment_test_plan.md) | ✅ FROZEN |
| B04 R3.0 | [B04_R3_test_plan.md](B04_R3_test_plan.md) — R3.0 章节 | ✅ FROZEN |
| B04 R3.1-A | [B04_R3_test_plan.md](B04_R3_test_plan.md) — R3.1 章节 | ✅ FROZEN |
| B04 R3.1-B | [B04_R3_test_plan.md](B04_R3_test_plan.md) — R3.1 章节 | ✅ FROZEN |
| B04 R3.1-B → R3.1-C | [B04_R3_1B_to_R3_1C_handoff.md](../B04_R3_1B_to_R3_1C_handoff.md) | R3.1-B → R3.1-C handoff reference |
| B04 R3.1-C | test_r3_1c_public.py (25 passed) | PENDING FREEZE CONFIRMATION |
| B04 R3.2–R3.5 | 待实现 | NOT STARTED |
| B05 | 待实现 | NOT STARTED |
| B06 | 待实现 | NOT STARTED |
| B07 | 待实现 | NOT STARTED |
| B08 | 待实现 | NOT STARTED |
| B09 | 待实现 | NOT STARTED |
| B10 | 待实现 | NOT STARTED |
| — | [brick_test_workflow_architecture.md](brick_test_workflow_architecture.md) | DRAFT (三 Agent 工作流架构 v1.3) |

> **注**：`B04_R3_test_plan.md` 覆盖 R3.0 至 R3.1 的多个子阶段。其**冻结范围**限于 R3.0 / R3.1-A / R3.1-B 已冻结的章节。R3.1-C 的测试计划见该文档 R3.1-C 节（规划参考）；R3.1-C 的实现证据在 `test_r3_1c_public.py` 中已完成（25 passed）。

## 当前基线

| Suite | Collected | Passed | Skipped | Failed |
|-------|-----------|--------|---------|--------|
| test_r3_1c_public.py | 25 | 25 | 0 | 0 |
| zynq_mcp/tests | 253 | 253 | 0 | 0 |
| mcps full | 695 | 694 | 1 (B02 POSIX-only) | 0 |
| list_tools | 10 | — | — | — |

算术：695 collected = 694 passed + 1 skipped。`-W error::RuntimeWarning` → 0 warnings。

## R3.1-C 待完成

- [ ] Controlled Freeze Package Revision 3 用户确认
- [ ] Agent3 R3.1-C preconditioned public MCP smoke（Agent3 尚未调用）
- R3.1-C scope 尚未冻结；所有 R3.1-C 生产资产状态为 PENDING FREEZE CONFIRMATION。

## R3.2–R3.5 后续测试记录入口

| Sub-step | 测试文件 | 状态 |
|----------|---------|------|
| R3.2 | test_r3_2_build.py | NOT STARTED |
| R3.3 | test_r3_3_bitstream.py | NOT STARTED |
| R3.4 | test_r3_4_jtag.py | NOT STARTED |
| R3.5 | test_r3_5_integration.py；phase_blackbox/r3_5_integration/ | NOT STARTED |

## B05–B10 后续测试记录入口

| Brick | 测试文件 | 状态 |
|-------|---------|------|
| B05 | platform domain tests | NOT STARTED |
| B06 | ps domain tests | NOT STARTED |
| B07 | skill gpio workflow tests | NOT STARTED |
| B08 | gpio whitebox acceptance tests | NOT STARTED |
| B09 | agent2 blackbox acceptance tests | NOT STARTED |
| B10 | gpio v1 freeze evidence | NOT STARTED |

## 阶段黑盒测试项目入口

| 阶段 | 目录 | Profile | 状态 |
|------|------|---------|------|
| B04 R3.1-C | (preconditioned smoke via harness) | PRECONDITIONED_SESSION | NOT STARTED |
| B04 R3.5 | `validation_projects/phase_blackbox/r3_5_integration/` | HOST_INTEGRATION (default) + HARDWARE_REPLAY (optional) | NOT STARTED |

## 声明

- Agent3 尚未由 Manager Reviewer 调用
- Agent2 尚未调用
- R3.2–R3.5、B05–B10 尚未开始
- R3.1-C 尚未冻结；等待用户确认 Controlled Freeze Package Revision 3
