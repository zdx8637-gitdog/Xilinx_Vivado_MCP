# Manager Review Records

## Agent Routing Boundary

- Agent1, Agent2, and Agent3 are external Claude Code agents operated by the user. They are not Codex sub-agents.
- Codex (Manager Reviewer) must not spawn or call them through internal sub-agent tooling.
- Codex produces a clearly labeled prompt for the target agent; the user manually forwards that prompt and returns the agent's report for review.
- A prompt must name its target (`Agent1`, `Agent2`, or `Agent3`), memory mode, allowed scope, forbidden actions, command/evidence requirements, and report status.
- Agent3 and Agent2 remain independently routed gates. Agent2 is forbidden until B08 is complete.

本目录保存项目主审核智能体（Manager Reviewer）的交接、审核门禁和工作流记录。

## 当前主文档

- `B04_R3_1C_review_codex_prompt.md`：R3.1-C 独立审核任务约束和交付要求。
- `B04_R3_1C_codex_audit_handoff.md`：R3.1-C 审核证据、当前阻塞和复审要求。
- `manager_reviewer_workflow.md`：主审核智能体的角色、消息流、审核阶段和交接方法。
- `B04_R3_1C_agent1_round6_handoff.md`：当前 Agent1 上下文溢出后的 Round 6 交接状态和新记忆 Agent1 继续任务。
- `B04_R3_1C_round12_audit.md`：Round 12 独立复核结果、三项阻塞问题和闭环证据要求。
- `B04_R3_1C_agent1_round13_handoff.md`：发给新记忆 Agent1 的一次性完整修复 prompt。
- `B04_R3_1C_round13_audit.md`：Round 13 独立复核结果，记录 verifier/cleanup 未执行全量共享校验的阻塞。
- `B04_R3_1C_agent1_round14_handoff.md`：发给新记忆 Agent1 的三入口统一 fail-closed 修复 prompt。
- `B04_R3_1C_round14_audit.md`：Round 14 独立复核结果，记录 artifact revision 和 cleanup stage 两项阻塞。
- `B04_R3_1C_agent1_round15_handoff.md`：发给新记忆 Agent1 的一次性修复 prompt。
- `B04_R3_1C_agent3_execution_prompt.md`：R3.1-C 阶段黑盒执行 Prompt，供用户转发给新记忆 Agent3。
- `B04_R3_1C_agent3_review.md`：Agent3 五场景执行结果和 Manager Reviewer 独立证据复核。
- `B05_platform_axi_agent1_prompt.md`：B05 Platform/AXI Domain 最小纵向切片开发 Prompt，供用户转发给新记忆 Agent1。
- `B05_platform_axi_round1_review.md`: B05 Round 1 functional review and blocking findings.
- `B05_platform_axi_agent1_round2_prompt.md`: one-pass B05 functional-closure prompt for Agent1.
- `B05_platform_axi_round2_review.md`: Round 2 audit; real Vivado success remains unproven and functional blockers remain.
- `B05_platform_axi_agent1_round3_prompt.md`: focused Round 3 prompt for Agent1; fixes functionality and executes the real success path.
- `B05_platform_axi_round3_review.md`: Round 3 audit; absolute manifest paths still break the B04 handoff and host-live evidence is missing.
- `B05_platform_axi_agent1_round4_prompt.md`: focused Round 4 prompt for direct artifact handoff and real execution evidence.
- `B05_platform_axi_round4_review.md`: Round 4 audit; host-live evidence and legacy platform-flow reuse are still unverified.
- `B05_platform_axi_agent1_round5_prompt.md`: focused Round 5 prompt for inspectable evidence, shared publisher correctness, and reuse of `zynq_platforms/ax7020_base`.
- `B05_platform_axi_manager_handoff.md`: current B05 Manager Reviewer handoff, evidence state, legacy-flow reuse, and Agent3 gate.

## 当前基线

- B04 R3.1-C：已实现，回归已转绿，但仍未通过独立审核，未冻结。
- R3.2：未开始。
- Agent2：未调用。
- 硬件开发阶段：需要真实硬件效果时，由用户进行人工验收并把结果反馈给 Manager Reviewer。

旧路径 `docs/development/B04_R3_1C_review_codex_prompt.md` 和
`docs/development/B04_R3_1C_codex_audit_handoff.md` 已迁移到本目录；旧位置保留迁移指针，
以兼容历史引用。
