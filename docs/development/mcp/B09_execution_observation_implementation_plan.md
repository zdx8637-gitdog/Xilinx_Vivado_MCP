# B09 Execution Observation 总体完善方案

> 版本：v0.11
> 日期：2026-08-13
> 状态：O1–O6 COMPLETE / FROZEN；O7 R3 PASS；勘误已关闭；O8 NOT STARTED
> 上位契约：[B09_execution_observation_contract.md](B09_execution_observation_contract.md) v1.0 FROZEN
> 目标：关闭 B09 公开 MCP 契约勘误，允许全新 Agent2 只用 Skill + 公开 MCP 重验

## 1. 当前问题基线

当前统一 MCP 已有单通道、Operation、Ledger、Preflight、Recovery 和多域工具，但真实执行观测仍不一致：

| 区域 | 当前行为 | 缺口 |
|---|---|---|
| Platform | O3已迁移到统一Controller与真实Vivado STATUS轮询 | COMPLETE / FROZEN |
| PL | O3已迁移到统一Controller；不再生成虚假Operation heartbeat | COMPLETE / FROZEN |
| PL Manifest | bitstream、XDC和锁定Manifest成为SUCCEEDED硬门禁 | COMPLETE / FROZEN |
| PS Build | O4已迁移到统一XSCT所有权、真实PID/步骤观测和ELF/Manifest硬门禁 | COMPLETE / FROZEN |
| PS JTAG | O5已迁移到Controller所有的XSDB与持久化JTAG lease | COMPLETE / FROZEN |
| UART | O5已接入独立Resource registry与持久化capture真值 | COMPLETE / FROZEN |
| Skill | O6已删除standalone Vivado、手工Manifest、手工make等逃生路径 | COMPLETE / FROZEN |

## 2. 目标架构

```text
Public MCP Tool
  -> Atomic Preflight + Admission
  -> Execution Ledger: ACCEPTED/BUSY + immutable snapshot + deadline
  -> ToolProcessController
       -> VIVADO | XSCT | XSDB（最多一个）
       -> 真实PID和五字段身份
  -> Domain Observer
       -> VENDOR_RUN | PROCESS | RESOURCE | LOCAL
  -> Ledger Observation Update
  -> Tool Result
  -> Artifact Verify
  -> Manifest Atomic Publish
  -> Atomic Terminal Commit
```

建议引入的内部责任边界：

| 组件 | 职责 |
|---|---|
| `ToolProcessController` | 唯一EDA后端所有者、后端切换、PID身份、shutdown/kill/reconcile |
| `ExecutionObserver` | 将供应商/进程/资源状态规范化为冻结observation schema |
| `OperationService` | 原子admission、observation更新、终态事务 |
| Domain executor | 只负责领域命令和稳定current_step，不自行管理未登记Bridge |
| Artifact finalizer | 校验Artifact并原子发布Manifest；失败阻止SUCCEEDED |
| Resource registry | JTAG/UART owner、lease、活动时间和重启失效 |

类名可以调整，但责任不可重新混合。

## 3. 实施原则

1. 先实现公共Ledger/Observer基础，再逐域迁移；禁止三个域各写一套状态模型。
2. 保持现有公开工具名称和主要参数兼容；新增字段必须向后兼容。
3. 不删除旧测试；迁移测试必须保留旧行为覆盖并增加真实观测断言。
4. 每个子步骤独立审核、独立回归；未过门禁不得进入下一步。
5. host-live和device-live不得用mock结果代替。
6. 在正式工具通过前不删除Skill逃生通道；删除逃生通道是后置门禁。
7. B10始终BLOCKED，直到全新Agent2重验通过。

## 4. 分阶段路线

### O0 — 契约冻结与现状审计

状态：**COMPLETE / FROZEN（docs-only）**。

交付：

- Execution Observation Contract v1.0；
- B09公开MCP契约勘误；
- 当前Platform/PL/PS/JTAG/UART执行后端映射；
- B10阻塞声明。

禁止：生产代码、Skill、测试行为修改。

### O1 — Ledger v2兼容扩展

状态：**COMPLETE / FROZEN**。

实施证据：[B09_O1_completion_report.md](B09_O1_completion_report.md)。

目标：让Operation可以持久化冻结observation schema，而不改变现有领域执行器。

预期修改区域：

- `control/execution_ledger.py`
- `control/operation_service.py`
- `control/operation_registry.py`
- `dispatcher.py` 的状态查询输出
- schema/validation和迁移测试

交付：

- Ledger schema_version升级与旧Ledger安全迁移；
- observation、artifact_state、deadline_at、recommended_action字段；
- `op_observe()`原子更新入口；
- `get_operation_status`、`wait_operation`和CHANNEL_BUSY返回冻结字段；
- controller heartbeat与observed_at彻底分离。

门禁：

- 旧Ledger加载不丢Session/Operation；
- 损坏或未知schema fail-closed；
- query不无条件增加sequence；
- observation更新不移动Operation终态；
- RuntimeWarning=0，R1–R3历史测试不删不降级。

### O2 — ToolProcessController与统一后端所有权

状态：**COMPLETE / FROZEN**。实施证据见 [B09_O2_implementation_report.md](B09_O2_implementation_report.md)。

目标：统一管理实际Vivado/XSCT/XSDB进程，至多一个活动EDA后端。

预期修改区域：

- `control/single_worker.py`重构或兼容包装
- `control/process_guard.py`
- `adapters/vivado/`
- `adapters/xsct/`
- `server.py`启动/终止/reconcile

交付：

- backend=`NONE|VIVADO|XSCT|XSDB`；
- 实际工具PID与可选supervisor_pid分离；
- 五字段身份和generation；
- `ensure_backend()`、`observe_backend()`、`shutdown_backend()`；
- 后端切换必须验证旧PID消失；
- MCP退出的有界cleanup；
- startup reconcile覆盖FROZEN契约表。

门禁：

- 两个并发ensure只有一个实际PID；
- VIVADO到XSCT切换严格事件顺序；
- 旧PID清理失败禁止启动新后端；
- PID复用/身份不符fail-closed；
- MCP crash后不自动重跑；
- 零按进程名称批量kill，只精确处理本轮PID树。

### O3 — Platform + PL Vivado真实观测

状态：**COMPLETE / FROZEN**。实施证据见 [B09_O3_implementation_report.md](B09_O3_implementation_report.md)。

目标：将所有Vivado路径接入同一Controller和Observer，删除运行期虚假heartbeat。

实现策略：

- 普通BD/Tcl步骤使用`PROCESS`观测并写稳定`current_step`；
- synthesis/place/route/bitstream使用Vivado run对象；
- `launch_runs`后返回控制权，由observer轮询`STATUS/PROGRESS`；
- 不再把长时间`wait_on_run`与无法查询的同一Tcl eval绑定；
- 对无PROGRESS步骤保留`progress_pct=null`；
- 真正的Vivado PID进入Worker记录。

Platform稳定步骤至少包括：

```text
PROJECT_CREATE
BD_CREATE
PS7_CONFIGURE
AXI_CONNECT
ADDRESS_ASSIGN
BD_VALIDATE
GENERATE_TARGET
SYNTHESIS
XSA_EXPORT
PLATFORM_MANIFEST_PUBLISH
```

PL稳定步骤至少包括：

```text
PROJECT_OPEN
SYNTHESIS
PLACE
ROUTE
TIMING_ANALYSIS
BITSTREAM_WRITE
BITSTREAM_VERIFY
PL_MANIFEST_PUBLISH
```

门禁：

- host-live捕获Vivado原始STATUS值并测试规范化映射；
- RUNNING期间`get_operation_status`可用；
- PID死亡时不再继续刷新ALIVE/RUNNING；
- observer停更进入UNRESPONSIVE；
- timeout清理真实Vivado PID树；
- Platform Manifest和PL Manifest失败均阻止SUCCEEDED；
- 现有Platform/PL产物SHA和功能无回归。

### O4 — PS Build与XSCT真实观测

状态：**COMPLETE / FROZEN**。实施证据见 [B09_O4_implementation_report.md](B09_O4_implementation_report.md)。

目标：`ps_compile`从公开调用到ELF/PS Manifest完整闭环。

实现策略：

- XsctBridge只能通过ToolProcessController获取；
- XSCT实际PID/身份写入Ledger；
- app build、make fallback、ELF verify、Manifest publish写入current_step；
- make fallback保留为MCP内部实现，Agent不可见；
- ELF格式必须为Zynq目标所需架构；
- PS Manifest发布失败阻止SUCCEEDED。

门禁：

- Vitis/XSCT host-live：无需手工make产出真实ELF；
- 强制app build不产ELF时内部fallback恰好调用一次；
- fallback失败返回精确BUILD_FAILED；
- ELF不存在/错误架构/旧revision均拒绝；
- `ps_compile SUCCEEDED`后立即可被`ps_download_elf`和consistency使用；
- Agent脚本直接make扫描为0。

### O5 — PS JTAG、UART与资源观测

状态：**COMPLETE / FROZEN**。实施证据见 [B09_O5_implementation_report.md](B09_O5_implementation_report.md)。

目标：把连接型资源状态纳入Ledger，而不错误占用第二个EDA后端。

JTAG：

- XsdbBridge由Controller所有；
- JTAG lease包含session、连接、target、last_observed_at；
- connect/select/reset/init/program/loadhw/download/run均写current_step；
- 重启后连接必须失效并显式重建。

UART：

- 独立Resource registry；
- capture_id、owner、port、baudrate、last_rx、bytes、markers、deadline持久化；
- UART可与严格串行JTAG步骤协同，但不得允许第二个command Operation并发；
- capture后台资源状态与command lane语义分离。

门禁：

- device-live验证真实COM4活动时间和marker；
- 拔出串口或端口失效产生明确终态；
- 同端口foreign owner被拒绝；
- MCP重启不声称旧capture仍RUNNING；
- JTAG target/lease不可跨实例静默复用。

### O6 — Skill去逃生通道 + Agent1全公开MCP白盒重放

状态：**COMPLETE / FROZEN**。实施证据见 [B09_O6_completion_report.md](B09_O6_completion_report.md)。

Skill修改：

- 删除standalone VivadoTclBridge示例与强制要求；
- 删除手工`publish_pl_build_manifest()`；
- 删除手工`make`；
- 所有长任务统一使用`wait_operation`/`get_operation_status`；
- 根据recommended_action决定WAIT/DIAGNOSE/RECOVER；
- 禁止导入`mcps.zynq_mcp.*`内部模块。

Agent1白盒重放门禁：

- 干净workspace；
- Platform、PL、PS、Consistency、JTAG、UART、Observation全部真实通过；
- 所有EDA/build/deploy动作来自公开`call_tool`；
- 每个长任务保存Ledger观测时间线；
- Manifest自动生成；
- 结束后无本轮遗留进程；
- Skill机械逃生模式扫描为0。

### O7 — 全新Agent2重新B09

进入条件：O6通过且用户审核允许。

必须启动**全新无记忆Agent2会话**，不得沿用旧Agent2。

Agent2只获得：

- 用户需求；
- 修订后的`skills/zynq_gpio/`；
- 锁定Board Package；
- 已注册公开`zynq_mcp` schema；
- 干净workspace。

禁止给Agent2：

- Agent1对话或操作脚本；
- 内部模块路径；
- standalone bridge/publisher/make提示；
- 预期错误方向或修复答案。

验收：

- 公开MCP调用审计完整；
- 逃生模式扫描为0；
- GPIO真实UART PASS + WROTE/READ；
- consistency全通过；
- Ledger状态时间线完整；
- 进程清理完整。

O7 PASS后，B09公开MCP契约勘误才可关闭。

#### O7 第一轮结果（2026-08-13）

第一轮由项目外隔离环境中的全新无记忆 Agent2 执行，终态为 **FAIL / NOT FROZEN**：

- 公开 `platform_generate` 已受理，但 operation `op-e686280a58e04e3cb3c93b7c2a1c84fb` 以 `BACKEND_START_FAILED` 终止；
- 公开错误为 `vivado init command failed: failed to write to vivado: Connection lost`，终态观测为 `backend=NONE`、`worker_state=ABSENT`；
- 未产生 Platform/PL/PS Manifest、bitstream、ELF、UART 或 GPIO 判定证据；
- Agent2 通过 9 次 PowerShell `Get-Content` 读取外部 Skill 文档，违反本轮零-shell硬门禁，且其报告中的“未使用禁止路径”声明与 JSONL 轨迹不一致；
- `ps_disconnect_hw_server` 的公开 schema 与运行时 `SESSION_ID_REQUIRED` 行为不一致。

详细证据见 [B09_O7_R1_failure_report.md](B09_O7_R1_failure_report.md)。修复并通过相关回归后，必须创建新的隔离环境并使用**另一全新无记忆 Agent2**执行 O7 下一轮；不得恢复或复用第一轮会话。

#### O7 R2 整改与重验边界（2026-08-13）

R2 前置整改已经执行：Vivado 首条用户 Tcl 前的 vendor launcher 失败允许一次安全重启并保留 stdout/stderr/退出码；全部 `ps_*` schema 显式公开 session 语义；`ps_import_hardware` 支持同 workspace XSA 幂等导入。专项回归 66 项、真实 Vivado host-live 1 项通过，最终完整非硬件回归为 `1329 passed, 1 skipped, 37 deselected`。

Codex 的 Skill 发现/加载本身会产生文件读取命令，因此 R2 采用可机械审计的最小例外：只允许一次只读 `Get-Content` 加载项目外插件中的单一 ASCII `SKILL.md`；此后所有 command execution 必须为 0，所有 EDA、构建、Manifest、部署、UART 和二进制文件处理必须只走公开 MCP。任何额外 shell、脚本或仓库读取均令 R2 FAIL。

R2 使用全新 `D:\_o7_external\agent2_20260813_r2` 隔离根目录，先执行只到 `platform_generate` 终态的后端预检；预检通过后切换到另一套空 runtime/workspace，并由全新无记忆 Agent2 执行完整黑盒，不复用预检 session。

隔离诊断确认 Xilinx Windows `loader.bat` 在 Codex 插件 MCP 的窄环境中因缺少 `PROCESSOR_ARCHITECTURE` 而静默退出码 1。桥接层已为 vendor 子进程恢复缺失的核心 Windows 变量并保留启动 stdout/stderr/退出码。第三套全新预检已 PASS：operation `op-21e06e94c2174212baae9b09a1f6778a` 为 `SUCCEEDED / PUBLISHED`，公共清理完整，轨迹只有 1 次 Skill 加载命令、加载后 command execution 为 0。正式 R2 将使用预检从未使用的 runtime/workspace。

#### O7 第二轮结果（2026-08-13）

R2 由另一全新监督子代理启动的无记忆 Agent2 执行，终态 **FAIL / NOT FROZEN**。Platform、PL synthesis/place/route/timing 均真实成功；`pl_generate_bitstream` operation `op-5c27bf8ab3254858b623f901d94f4ea4` 在 `PL_MANIFEST_PUBLISH` 以 `ARTIFACT_STALE / MANIFEST_PUBLISH_FAILED` 失败。根因是公开 bitstream 目标父目录未由 MCP 创建，且 Tcl copy 结果未在工具 success 前机械确认。整改为 MCP 创建父目录、catch copy 错误、要求 `BIT_DONE + 文件存在` 双门禁；下一轮使用全新 R3 runtime/workspace/Agent2。详见 [B09_O7_R2_failure_report.md](B09_O7_R2_failure_report.md)。

#### O7 第三轮结果（2026-08-13）

R3 使用新的 `D:\_o7_external\agent2_20260813_r3` runtime/workspace 和另一全新无记忆 Agent2，终态 **PASS**（用户已于 2026-08-13 审核通过）：

- P1 Platform operation `op-17205f3e42c34076832f4dd904f5bf9d` 为 `SUCCEEDED / PUBLISHED`；
- P2 bitstream operation `op-faa8f9ecc95549d098806e1b3cb1791d` 为 `SUCCEEDED / PUBLISHED`，证明 R2 输出路径/复制验证整改有效；
- P3 PS build 发布 ARM ELFCLASS32 ELF 与 PS Manifest；P4 `verify_consistency` 为 12/12；
- P5/P6 真实 JTAG/UART/GPIO 验收通过：8/8 `WROTE/READ` 相等、存在 `GPIO_E2E_PASS`、不存在 `GPIO_E2E_FAIL`，`evaluate_observation=PASS`；
- trace 仅允许并实际出现一次只读 `Get-Content ...\SKILL.md`，Skill 加载后 command execution 为 0；
- UART 已停止、JTAG 已断开、session 已关闭，trace 以 `turn.completed` 结束；本轮无遗留 Vivado/XSCT/XSDB/Agent2 进程。

详细证据见 [B09_O7_R3_pass_report.md](B09_O7_R3_pass_report.md)。O7 的技术门禁已满足；用户已审核通过并关闭勘误。按治理边界，不自动执行 O8/B10，等待用户确认基线与下一切片。

### O8 — B10 GPIO v1冻结

仅在O7 PASS后执行：

- 冻结Skill、MCP公开schema、Ledger契约实现和Board Package引用；
- 归档Agent1/Agent2报告、Ledger、Manifest、Artifact SHA和真实硬件证据；
- 记录已知限制；
- 用户确认GPIO v1稳定基线及下一纵向切片。

## 5. 测试分层

| 层级 | 目的 |
|---|---|
| Contract | schema、枚举、原子状态机、错误映射 |
| Component | Controller、Observer、Artifact finalizer、Resource registry |
| Cross-process | 双实例、PID身份、crash、后端切换、cleanup |
| MCP SDK | 公开tools、busy/status/wait/diagnose/recover响应 |
| Host-live | Vivado/Vitis/XSCT真实状态和Artifact |
| Device-live | JTAG、UART、GPIO板卡行为 |
| Agent1 white-box | 全公开MCP端到端并解释全部证据 |
| Agent2 black-box | 全新上下文，仅Skill+公开MCP复现 |

测试数量必须由`pytest --collect-only`机械生成，计划不预填承诺数字。

## 6. 错误码最小集合

实现应复用已有顶层ErrorCode，并至少稳定区分：

```text
CHANNEL_BUSY
WORKER_START_FAILED
WORKER_PID_DEAD
WORKER_IDENTITY_MISMATCH
WORKER_UNRESPONSIVE
BACKEND_SWITCH_FAILED
OPERATION_TIMED_OUT
OPERATION_OUTCOME_UNKNOWN
LEDGER_READ_FAILED
LEDGER_WRITE_FAILED
ARTIFACT_VERIFY_FAILED
MANIFEST_PUBLISH_FAILED
ELF_VERIFY_FAILED
JTAG_LEASE_MISSING
JTAG_OWNER_MISMATCH
UART_OWNER_MISMATCH
UART_DISCONNECTED
RECOVERY_REQUIRED
```

最终reason_code表在O1实施前由Agent1机械核对现有枚举，禁止创建语义重复代码。

## 7. 风险与回退

### 风险1：Vivado run轮询本身占用Tcl通道

处置：`launch_runs`后不进入长阻塞`wait_on_run`；observer短查询串行执行。若某命令只能前台阻塞，则降级为PROCESS观测，不伪造vendor_status。

### 风险2：统一后端切换影响现有JTAG会话

处置：JTAG阶段显式进入XSDB backend；切换前要求Lane IDLE，旧后端关闭失败即RECOVERY_REQUIRED。

### 风险3：Manifest从best-effort改为强门禁导致历史成功变失败

处置：这是预期的fail-closed改变。先增加故障注入和恢复证据，再修改Skill。

### 风险4：旧Ledger不兼容

处置：O1提供只向前一次迁移；未知/损坏schema拒绝接单，不进行猜测修复。

### 回退原则

- 每个O阶段保留上一阶段全量回归基线；
- 不修改锁定Board Package；
- 不删除旧Skill逃生说明，直到O6一次性替换并有白盒PASS；
- 不用旧B09报告替代新Agent2验收。

## 8. 审核与角色

- Agent1：白盒实现、机械审计、host/device live、完整报告；不得自行冻结下一阶段。
- 审核Codex：对照冻结契约检查真实生产入口、测试完整性和报告机械一致性。
- Agent3：可用于阶段黑盒，不替代B09 Agent2。
- Agent2：只在O7调用；调用前必须提醒用户选择**全新无记忆Agent2**。
- 用户：批准契约Erratum、阶段冻结、硬件验收和最终B10。

## 9. 当前状态

- O0：✅ COMPLETE / FROZEN；
- O1：✅ COMPLETE / FROZEN；
- O2：✅ COMPLETE / FROZEN（统一所有权、真实PID和PROCESS观测）；
- O3：✅ COMPLETE / FROZEN（Platform/PL真实Vivado STATUS观测与Manifest硬门禁）；
- O4：✅ COMPLETE / FROZEN（PS Build真实XSCT进程/步骤观测与ELF/Manifest硬门禁）；
- O5：✅ COMPLETE / FROZEN（Controller-owned XSDB、JTAG lease、UART capture与RESOURCE观测）；
- O6：✅ COMPLETE / FROZEN（Skill公共边界 + Agent1全公开MCP真实GPIO重放）；
- O7：✅ R3 PASS；用户已审核通过并关闭勘误；R1/R2 FAIL 作为历史整改证据保留；
- O8：未开始；
- 生产代码修改：O1 Ledger v2兼容扩展；O2 ToolProcessController；O3 Vivado observer；O4 XSCT observer及PS终态门禁；O5 JTAG/UART Resource registry与公开资源状态；O6公开重放发现的四项窄范围产品修复；
- Skill修改：O6已删除bridge/publisher/manual make/内部模块逃生通道；
- 测试修改：O1专项24项；O2专项22项；O3/O4专项27项；O5专项10项；O6公共边界11项并完成126项相关窄回归；未删除历史测试；
- Agent1：O6白盒公开重放已完成；Agent2：O7 R3 全公开 MCP 黑盒已 PASS（另在本仓库隔离环境独立复测 PASS）；Agent3：未调用；
- B10：勘误已关闭，等待用户确认 GPIO v1 稳定基线并选择下一纵向切片后启动 O8 冻结。
