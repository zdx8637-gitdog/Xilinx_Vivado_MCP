# Vivado MCP Validation Benchmark v1.0

> 状态: ACTIVE
> 目标: 独立测试代理的 FPGA 验证基准套件

---

## 目的

本仓库包含一组 FPGA 项目，用于验证 Vivado MCP 平台的正确性、稳定性和可恢复性。

每个项目包含完整的源代码、约束、testbench、构建脚本和验收标准。

---

## 目录结构

```
validation_projects/
├── README.md                    ← 本文件
│
├── golden/                      ← 已知正确的参考设计
│   └── breath_led/              ← PWM 呼吸灯 (基线)
│       ├── rtl/
│       ├── constraints/
│       ├── sim/
│       ├── scripts/
│       └── README.md            ← 预期行为和验收标准
│
└── designs/             ← 注入单一缺陷的故障项目
    ├── rtl/                     ← RTL 缺陷
    ├── simulation/              ← 仿真缺陷
    ├── timing/                  ← 时序缺陷
    ├── constraint/              ← 约束缺陷
    ├── build/                   ← 构建缺陷
    ├── project/                 ← 工程配置缺陷
    └── workflow/                ← 工作流缺陷
```

---

## 故障注入规则

1. **每个项目只注入一个明确定义的工程缺陷**
2. 每个故障具有唯一 ID (格式: `F-{CATEGORY}-{NNN}`)
3. 每个故障记录在对应的 `ANSWER_KEY.md` 中
4. **测试代理只能看到项目文件，不能看到 ANSWER_KEY.md**
5. 除非特别说明，所有故障项目基于 `golden/breath_led/`

---

## 故障类别和检测阶段

| 类别 | 应被哪个阶段检测到 |
|------|-------------------|
| RTL | Simulation (功能性故障) 或 Synthesis (语法故障) |
| Simulation | run_simulation (断言失败) |
| Timing | report_timing_summary (WNS 为负) |
| Constraint | Synthesis 或 Implementation (Vivado 报错) |
| Build | synth_design 或 place_design (流程中断) |
| Project | open_checkpoint 或 create_project (配置错误) |
| Workflow | Workflow 恢复机制 (部分产物) |

---

## 故障清单

| ID | 类别 | 项目名 | 检测阶段 |
|----|------|--------|----------|
| F-RTL-001 | RTL | fsm_deadlock | Simulation |
| F-RTL-002 | RTL | counter_overflow | Simulation |
| F-RTL-003 | RTL | multiple_drivers | Synthesis |
| F-SIM-001 | Simulation | failing_assertion | Simulation |
| F-TIM-001 | Timing | impossible_clock | Timing Analysis |
| F-CON-001 | Constraint | missing_clock | Implementation |
| F-CON-002 | Constraint | invalid_pin | Synthesis |
| F-BLD-001 | Build | wrong_top_module | Synthesis |
| F-BLD-002 | Build | missing_source | Synthesis |
| F-PRJ-001 | Project | wrong_part_number | opt_design |
| F-WKF-001 | Workflow | corrupt_dcp | open_checkpoint |

---

## 扩展指南

添加新故障时:
1. 在对应类别目录下创建 `fNNN_descriptive_name/`
2. 包含完整的可构建项目
3. 编写 `ANSWER_KEY.md` (不提供给测试代理)
4. 更新本 README 的故障清单

---

## 验收标准

验证平台通过标准:
- Golden 项目: Build + Sim 全部 PASS
- 故障项目: 测试代理正确识别每个故障的类别和根因
- 恢复: 测试代理能够针对每个故障提出修复建议

---

## Phase Black-Box Acceptance Tests (新增, v0.3 工作流架构)

> 新增于 2026-08-07。三 Agent 分阶段测试工作流的一部分。
> 完整架构见 `docs/development/tests/brick_test_workflow_architecture.md`。

| 目录 | 用途 | 状态 |
|------|------|------|
| `phase_blackbox/_manager/` | Manager Reviewer provisioning harness (Manager-only) | READY |
| `phase_blackbox/r3_1c_smoke/` | B04 R3.1-C preconditioned public MCP smoke (Agent3) | READY FOR MANAGER REVIEW / NOT EXECUTED |
| `phase_blackbox/r3_5_integration/` | B04 R3.5 integration black-box (Agent3) | NOT STARTED |

### 安全边界

- `_manager/` 目录仅供 Manager Reviewer 使用，不得交付 Agent3 或 Agent2。
- 所有 runner 只能导入 Python stdlib + `mcp` SDK 公共包。
- Agent3/Agent2 不得导入 `mcps.zynq_mcp`、读取 Ledger、调用内部 API 或使用 hidden Tcl。
- 硬件效果由用户确认，Agent 只能验证软件/协议结果。

### 当前状态

- R3.1-C smoke 项目已就绪，等待 Manager Reviewer 审核和 Agent3 执行。
- R3.2–R3.5 测试项目尚未建立。
- B05–B10 测试项目尚未建立。
- Agent3 未调用。Agent2 未调用。
