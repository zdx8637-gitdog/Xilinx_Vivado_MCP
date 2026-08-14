# B09 O7 第一轮黑盒失败报告

> 日期：2026-08-13
> 状态：FAIL / NOT FROZEN
> 角色：项目外隔离环境中的全新无记忆 Agent2
> 范围：只使用外部加载的 GPIO Skill、公开 `zynq_mcp`、锁定 Board Package 和干净 workspace

## 1. 终态

O7 第一轮必须判定为 **FAIL**。公开产品流程在 Phase 1 的 Vivado 后端启动阶段终止，未能生成任何可用于后续 PL、PS、Consistency、JTAG、UART 或 GPIO 验收的产物。同时，Agent2 的执行轨迹违反本轮零-shell硬门禁，其最终边界声明与机械轨迹不一致。

本轮不得冻结 O7，不得关闭 B09 公开 MCP 契约勘误，不得解除 B10 阻塞。

## 2. 产品流程失败

| 项目 | 证据 |
|---|---|
| Operation | `op-e686280a58e04e3cb3c93b7c2a1c84fb` |
| Tool | `platform_generate` |
| 终态 | `FAILED` |
| Reason | `BACKEND_START_FAILED` |
| 错误 | `vivado init command failed: failed to write to vivado: Connection lost` |
| Backend | `NONE` |
| Worker | `ABSENT` |
| Execution lane | `IDLE` |
| 当前阶段 | `PLATFORM_DESIGN` |

Operation 在公开 MCP 中被正常受理并持久化，但 Vivado backend 初始化连接立即丢失。失败后公开诊断未发现活动 Worker、JTAG lease 或 UART capture。

## 3. 未产生的验收证据

- 无 Platform Manifest；
- 无 PL Manifest、bitstream 或 bitstream SHA；
- 无 PS Manifest、ELF 或 ELF SHA；
- 未执行 `verify_consistency`；
- 未执行 JTAG 部署；
- 无 UART 文本、WROTE/READ 对或 `GPIO_E2E_PASS`；
- 无真实硬件终态判定。

## 4. 黑盒边界失败

完整 JSONL 轨迹中存在 9 个已完成的 PowerShell `Get-Content` command execution，用于读取外部插件中的 Skill 主文档和分阶段参考文档。尽管这些命令没有直接执行 EDA、构建或 Manifest 操作，但违反本轮任务明确规定的“完全禁止 shell”硬门禁。

Agent2 报告写有“No prohibited escape path was used”，与机械轨迹不一致，因此该声明不可采信。这一项本身已经足以令 O7 失败，独立于产品 backend 失败。

## 5. 额外公共契约不一致

`ps_disconnect_hw_server` 的公开工具 schema 未声明必填参数，但空参数调用返回：

`INVALID_ARGUMENT / SESSION_ID_REQUIRED`

该问题未导致本轮主流程失败，但属于下一轮前必须关闭的公共契约一致性缺陷。

## 6. 清理

Agent2 已调用 `close_session`。公开清理结果包含 UART resource cleanup、worker shutdown no-op、lease release no-op 和 context deletion。未遗留本轮新建的活动 MCP/EDA Worker；运行前已经存在的 `hw_server` 未被触碰。

## 7. 外部原始证据

- `D:\_o7_external\agent2_20260813\evidence\o7_agent2_trace.jsonl`
- `D:\_o7_external\agent2_20260813\evidence\o7_agent2_last.txt`
- `D:\_o7_external\agent2_20260813\workspace\O7_AGENT2_BLACKBOX_REPORT.md`
- `D:\_o7_external\agent2_20260813\workspace\evidence\`

这些外部文件按第一轮隔离环境保留，不作为第二轮 workspace 输入。

## 8. 下一轮门禁

O7 下一轮开始前必须：

1. 定位并修复隔离 MCP 环境中的 Vivado backend 启动/通信失败；
2. 用真实 Vivado host-live 路径验证修复；
3. 统一 `ps_disconnect_hw_server` 的公开 schema 与运行时 session 语义；
4. 将外部 Skill 合并为一个 ASCII `SKILL.md`；R2 允许 Codex 技能加载器且仅允许它执行一次只读 `Get-Content ...\\SKILL.md`，加载完成后任何其他 shell/command execution 均判失败；
5. 创建新的 runtime、workspace、插件版本、证据目录和 Agent2 会话，不复用第一轮状态；
6. 对 Agent2 JSONL 做独立机械审计，报告声明必须与轨迹一致。

只有下一轮同时满足公共 MCP 功能门禁、零逃生边界门禁、真实 GPIO/UART 门禁和清理门禁，O7 才能判 PASS。

## 9. 已执行整改（R2 前置）

- Vivado Bridge 在首条用户 Tcl 尚未执行前，针对 vendor launcher 立即死亡/管道断开增加一次且仅一次的安全重启；重启前完整清理旧进程；
- Vivado/XSCT Bridge 保留启动 stderr 和退出码，并把截断后的启动诊断写入结构化错误详情，不再静默丢弃；
- 全部公开 `ps_*` schema 均显式暴露 `session_id` 及 `SESSION_ID_REQUIRED` 语义；为保持冻结错误契约，该字段不交给 MCP SDK 的 JSON Schema `required` 预校验，而由 dispatcher 统一返回结构化错误；
- `ps_import_hardware` 支持 XSA 已位于目标 workspace 时的幂等导入，外部 Agent 不再需要复制二进制 XSA；
- 已通过 66 项专项回归、真实 Vivado host-live 1 项，以及从仓库根目录执行的最终完整非硬件回归 `1329 passed, 1 skipped, 37 deselected`；
- 已创建全新的 `D:\_o7_external\agent2_20260813_r2` runtime、workspace、插件、Board Package 副本和证据目录，未复用 R1 状态。

后续隔离预检把 R1/R2 前两次的启动表象进一步定位为 vendor 环境缺失：Codex 插件 MCP 使用窄环境启动时未提供 `PROCESSOR_ARCHITECTURE`，而 Xilinx Windows `loader.bat` 会在该变量缺失时无输出地 `exit /b 1`。这解释了隔离环境中的约 0.1 秒退出码 1，也解释了继承桌面环境的 host-live 始终通过。产品桥接层现已只为 vendor 子进程补齐缺失的 Windows 启动必需变量，不修改 MCP server 自身环境。

修复后的第三套全新预检环境中，`platform_generate` operation `op-21e06e94c2174212baae9b09a1f6778a` 终态 `SUCCEEDED`、`artifact_state=PUBLISHED`，Manifest/wrapper/XSA 均返回，`close_session` 完整清理；JSONL 机械审计为 1 次 Skill `Get-Content`、其后 0 次 command execution。

上述整改与隔离后端预检只表示具备正式重验条件，不改变 R1 的 FAIL 结论；R2 仍须由全新无记忆 Agent2 完成真实硬件黑盒验收。
