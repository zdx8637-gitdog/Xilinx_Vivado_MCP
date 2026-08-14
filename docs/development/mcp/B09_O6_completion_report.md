# B09 Execution Observation O6 完成报告

> 日期：2026-08-13  
> 状态：**COMPLETE / FROZEN**  
> 范围：Skill 去逃生通道 + Agent1 全公开 MCP 白盒重放  
> 后续：O7 **NOT STARTED**；Agent2 未调用；B10 保持 BLOCKED

## 1. 结论

O6 已完成。修订后的 `skills/zynq_gpio/` 只允许通过公开 `zynq_mcp` 完成 GPIO 纵向流程，不再给出 standalone Vivado bridge、内部 Python publisher、手工 Manifest、手工 `make`、直接 Tcl/EDA 进程或 Ledger 编辑逃生路径。

Agent1 随后从全新 workspace 和全新 runtime root，通过 MCP SDK `ClientSession.call_tool()` 完成真实 Platform → PL → PS → 三 Manifest 一致性 → JTAG/FPGA/ELF → COM4 UART → Observation 全链路。最终 GPIO 8 组写入/回读一致并得到 `GPIO_E2E_PASS`；公开清理完成且本轮进程零残留。

## 2. Skill 公共边界

- 所有产品动作只使用 Skill + 公开 `zynq_mcp` tool schema；
- 长任务统一使用 `wait_operation`、`get_operation_status` 和 `recommended_action`；
- Manifest 必须由对应公开终态工具发布，状态必须为 `PUBLISHED`；
- 禁止导入 `mcps.zynq_mcp.*`，禁止直接创建 bridge/子进程，禁止手工 Tcl/Manifest/make/gcc；
- 失败时只能按公开 `diagnose_execution` / `recover_execution` 证据处理，禁止篡改 Ledger；
- Platform、PL、PS 三份 Manifest 缺一不可进入硬件部署；
- UART 判定同时要求 PASS marker、无 FAIL marker、8 组 WROTE/READ 一致。

机械测试 `test_o6_skill_contract.py` 共 10 项，覆盖逃生标识扫描、直接进程/构建配方扫描、完整公开工具链、三 Manifest 门禁、UART 判定，以及重放器 AST 只导入标准库和 MCP SDK。

## 3. Agent1 公开重放

正式通过运行：

- workspace：`workspaces/o6_agent1_public_20260813_r7/`
- runtime：`.o6_runtime_agent1_20260813_r7/`
- summary：`workspaces/o6_agent1_public_20260813_r7/evidence/summary.json`
- public calls：`workspaces/o6_agent1_public_20260813_r7/evidence/public_calls.jsonl`
- Operation timeline：`workspaces/o6_agent1_public_20260813_r7/evidence/operation_timeline.jsonl`
- tool schema：`workspaces/o6_agent1_public_20260813_r7/evidence/tools_schema.json`
- UART：`workspaces/o6_agent1_public_20260813_r7/evidence/uart.txt`

| Gate | 结果 |
|---|---|
| Summary | `PASS` |
| Public MCP only | `true` |
| 可发现公开工具 | 101 |
| 公开 MCP 调用 | 71 |
| Operation 时间线记录 | 65 |
| Platform/PL/PS consistency | 12 passed / 0 failed / 0 skipped |
| JTAG | XSDB generation 3；真实 ARM Cortex-A9 MPCore #0 |
| UART | COM4 / 115200；199 bytes；marker matched |
| GPIO | 8/8 WROTE == READ |
| 最终判定 | `GPIO_E2E_PASS` / Observation `PASS` |
| 公开 cleanup | UART stopped、JTAG disconnected、session close；0 errors |
| 最终 Ledger | Lane `IDLE`；worker `ABSENT` / backend `NONE` / PID null；JTAG `DISCONNECTED`；UART `STOPPED` / 199 bytes |

### 3.1 产物证据

| Artifact | SHA256 |
|---|---|
| Platform XSA | `sha256:5fa842738af0fc3e760e4f8544dc2194130ab3089bf08692709d835b217098d9` |
| Platform Manifest | `sha256:dc9cfd9698144518fc426605c1879ab688ee23a932b282712c1ead0f71f1a09f` |
| Bitstream | `sha256:aeb8e21589180ee31b5a6bb30de327e0383fee69bee33a51ce902c1eb6e96bd1` |
| PL Manifest | `sha256:368a71d0baa0b26f2065ba6b2c1a93e8da4248635411db440820db1d03cf413a` |
| ARM ELF | `sha256:da6403001e3f7a15854a85f22f8878cddbed222e5a410024959ed05996e0e871` |
| PS Manifest | `sha256:445cbd4c37e685f496dcb103d7255168d8ea7296e3ee070c84c8410c11c9746a` |
| UART evidence | `sha256:6ea56c31b16d266b0a504105e6ed8b952a31178112cb80fc5770a117b1366279` |

## 4. 白盒重放发现并关闭的产品缺口

O6 没有放宽单执行通道或后端切换门禁。每个缺口均由全新公开重放暴露，再以窄范围生产修复和回归测试关闭：

| Replay | 暴露问题 | 修复 |
|---|---|---|
| r1 | Platform 通配查询误选 SmartConnect 内嵌 BD | 只选择精确顶层 parent BD |
| r2 | `launch_runs synth_1 -top` 不是合法 Vivado 2023.1 语法 | 先对 fileset 设置 `top`，再合法 `launch_runs` |
| r3 | PLACE 被后续未启动步骤误判 | observer 按当前 step 解释供应商状态 |
| r4 | BITSTREAM_WRITE 被旧 route 完成状态提前终止 | 只有对应 step 或通用完成状态可终结当前操作 |
| r5 | 测试把冻结 Artifact 状态误写为 `VALID` | 统一使用 Ledger 冻结值 `PUBLISHED` |
| r6 | 纯 Python `ps_read_elf_info` 错启 XSCT，随后 XSDB 切换被正确拒绝 | 设为 process-free 本地工具，不启动 EDA backend |
| r7 | 全公开链路 | PASS |

`ps_read_elf_info` 修复没有改变 `BACKEND_SWITCH_REQUIRES_IDLE`：专项测试从真实 `CommandRunner` 入口执行 ELF 解析，并用陷阱 Controller 证明 EDA backend 启动次数为 0。

## 5. 测试与机械统计

| Gate | 结果 |
|---|---|
| `mcps` collect | **1360 collected** |
| O6 Skill + 本地进程边界 | **11 passed** |
| O6 相关窄回归 | **126 passed** |
| 非硬件全量 | **1322 passed, 1 skipped, 37 deselected** |
| 唯一 skip | B02 POSIX-only `test_posix_link_no_overwrite` |
| RuntimeWarning | 0（`-W error::RuntimeWarning`） |
| Agent1 真实公开硬件 replay | **PASS** |
| 本轮残留 server/Vivado/XSCT/XSDB/UART 进程 | 0 |

系统原有 `hw_server.exe` PID 19880 创建于 2026-08-09，早于 O6；本轮只连接，不终止、不认领。禁止按进程名清理的规则保持不变。

## 6. O6 冻结 SHA256

| 文件 | SHA256 |
|---|---|
| `skills/zynq_gpio/SKILL.md` | `9645d0cb817bd98106b3df95e70501dfe98d1d913ded8047a3b5b5af95c900df` |
| `control/domain_runner.py` | `bb56355e6e7950f3480d1e91d3dbaa3b3f6bb9aefabd8112fff685cc0e4c3850` |
| `dispatcher.py` | `7843781fc7697898e16f64059029a515573c368f5625d08badcb28c7a8c96fa2` |
| `domains/platform/platform_domain.py` | `c7383dcdb307dc7c94dc508cfb2f431f224e3e5251b054bc0f535259bdabd96b` |
| `domains/pl/pl_bridge_tools.py` | `3d1ccaeefe53903a81dbd73c24b4e5677d0d390ee6f49f1c06ff1cb923d0ec59` |
| `control/vivado_execution_observer.py` | `928ca63b6064da4790613141bb42b29e73a12a39d601ff26c2b8730acc712334` |
| `test_o6_skill_contract.py` | `d7e97da0df54c0890a78c45d25f26ef1a30fdbe5837b197d4a18427764804d19` |
| `test_o6_ps_local_boundary.py` | `76a9d90bd83a483bc9e60cfaa138253045d48c44fef4484ec351e6d73e215245` |
| `run_public_replay.py` | `1e3caf13da967195998fe756b5d3f451303aa6f174475946db29a4de33098ea8` |

## 7. 冻结资产不变

- `.mcp.json`: `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`
- `CLAUDE.md`: `b03a060f8afde582ad91ff8d57b8ffd44c763d7ef2b5ce1853311aefee6cdee4`
- `Xilinx_Vivado_MCP/server.py`: `9fa66a0ca56389b73fb49cd17492306bf470f3d0b0964eb7fac0724c27b7d47b`
- `mcps/common/context.py`: `37bb0d1ad7ec85385f2cd753dc5e0bb09b9a8edd4b0516b3418624e6e373833c`
- Board Package 六文件 SHA256 与 B03 冻结值一致；`manifest_revision=sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`。

## 8. 最终声明

- O6：**COMPLETE / FROZEN**；
- O7：**NOT STARTED**；
- Agent2：**未调用**；下一步必须先由用户审核，并明确新建无记忆 Agent2 会话；
- B09 公开 MCP 契约勘误：实现整改与 Agent1 白盒门禁完成，但在 O7 Agent2 黑盒重验前仍不关闭；
- B10：继续 **BLOCKED**。
