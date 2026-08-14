# B09 O7 第三轮黑盒通过报告

> 日期：2026-08-13
> 结论：**PASS / AWAITING USER REVIEW**
> 隔离根：`D:\_o7_external\agent2_20260813_r3`
> Agent2：全新无记忆外部 Agent，仅启动一次并自然终止

## 1. 结论

O7 R3 通过全部 P1–P6 门禁。另一全新 Agent2 在新的 runtime/workspace 中，仅凭单一 Skill 和公开 `zynq_mcp` 完成 Platform、PL、PS、Manifest 一致性、真实 JTAG 部署、UART 观测与 GPIO readback。R2 的 bitstream 输出路径/复制验证修复在真实 Vivado 流程中得到验证。

O7 技术判定为 **PASS**。按照项目治理边界，本报告不自动冻结 B10；关闭契约勘误和启动 O8 仍需用户确认。

## 2. 关键证据

| 门禁 | 结果 | 证据 |
|---|---|---|
| P1 Platform | PASS | `op-17205f3e42c34076832f4dd904f5bf9d`，`SUCCEEDED / PUBLISHED` |
| P2 PL | PASS | synthesis/place/route/timing 全部成功；bitstream `op-faa8f9ecc95549d098806e1b3cb1791d`，`SUCCEEDED / PUBLISHED` |
| P3 PS | PASS | ARM `ELFCLASS32`，machine 40；PS Manifest `PUBLISHED` |
| P4 Consistency | PASS | 12/12 passed，failed=0，skipped=0，errors=[] |
| P5 部署 | PASS | `localhost:3121`、ARM Cortex-A9 #0、FPGA 编程与 ELF 下载成功 |
| P6 UART/GPIO | PASS | 8/8 `WROTE/READ` 相等；`GPIO_E2E_PASS` 存在，`GPIO_E2E_FAIL` 不存在 |
| 观测判定 | PASS | `evaluate_observation=PASS`，UART 202 bytes |
| 清理 | PASS | UART stopped、JTAG disconnected、session closed，`incomplete=[]` |

主要产物：

- Platform revision：`sha256:0c2071c30f99c93e5d8e74398af728c46cee49c8b42acc07d267a78f560800ae`
- XSA SHA-256：`sha256:6148aa709ca18e6675317aba525cc0d50b730a23606e10e2337648776b921922`
- PL Manifest revision：`sha256:4364961c1eeabf42879b440afebcdc5bdb5736853460fb5b0d35e88e7e9ed483`
- Bitstream SHA-256：`sha256:c90d13358f2100d3f06903edd73d0b072987f6788d9cd2bee4b91a03e1a071d1`
- PS Manifest revision：`sha256:3057cbeb3d0e95f35ef4590e22712e64923f36e8e57498febaa4220080538f33`
- ELF SHA-256：`sha256:9026e3d0df437822d0782e74522e1de10c4c9b51faad166aa36ead58e8bca5dd`

## 3. 黑盒边界机械审计

- Trace 中 command execution 事件为一组 started/completed，对应且仅对应一次只读 `Get-Content ...\SKILL.md`。
- Skill 加载后 command execution 数为 0。
- Agent2 未直接访问 `D:\fpgaproject`，未导入内部 `mcps.zynq_mcp` 模块，未直接启动 Vivado/XSCT/Tcl，未手工调用 Manifest publisher 或 `make`。
- XDC、应用源码和最终报告仅在所分配 workspace 中通过 `apply_patch` 创建；全部 EDA、构建、Manifest、JTAG、UART 和观测操作均通过公开 MCP。
- Trace 最后一条为 `turn.completed`，最终报告与 last message 均为 PASS。
- Windows `Start-Process` 返回对象未提供可信的外部 Agent2 数值退出码；监督包装 PowerShell 的退出码 0 未被冒充为 Agent2 退出码。自然终止、`turn.completed` 和最终 PASS 报告共同作为终态证据。

## 4. 回归与整改验证

- bitstream 修复专项与关联回归：107 passed。
- 真实 Vivado host-live：1 passed。
- 最终完整非硬件回归：1331 passed，1 skipped，37 deselected。
- 完整回归首次运行出现 1 次与本次修复无关的 project-lock heartbeat 微秒时间戳瞬态失败；单测重跑和完整重跑均通过，未隐藏该历史结果。

## 5. 证据位置

- Agent2 报告：`D:\_o7_external\agent2_20260813_r3\workspace\O7_AGENT2_R3_BLACKBOX_REPORT.md`
- JSONL trace：`D:\_o7_external\agent2_20260813_r3\evidence\o7_r3_trace.jsonl`
- stderr：`D:\_o7_external\agent2_20260813_r3\evidence\o7_r3_stderr.txt`
- 最终消息：`D:\_o7_external\agent2_20260813_r3\evidence\o7_r3_last.txt`

本轮未遗留 R3 Vivado/XSCT/XSDB/Agent2 进程。系统中仍有测试前已存在的 `hw_server.exe` PID 19880（启动于 2026-08-09），本轮未终止或修改该进程。
