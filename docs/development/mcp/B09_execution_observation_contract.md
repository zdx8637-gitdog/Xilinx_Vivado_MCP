# B09 Execution Observation Contract

> 版本：v1.0
> 日期：2026-08-12
> 状态：**COMPLETE / FROZEN**
> 范围：统一 `zynq_mcp` 的 Platform、PL、PS、JTAG、UART 与本地 Artifact 操作
> 冻结类型：产品行为契约；不冻结内部类名、文件划分或具体轮询实现

## 1. 目的

本契约保证黑盒智能体能够只依赖公开 MCP 响应，可靠判断一个 Zynq 开发步骤是尚未开始、正在执行、已经完成、明确失败、超时，还是结果未知并需要恢复。

Execution Ledger 必须保存**最近一次对真实工具、真实进程、真实资源或真实 Artifact 的观测**，不得仅凭 MCP 后台 Task 或定时协程仍存在就声称外部工具健康。

本契约关闭以下产品风险：

- MCP Task 为 RUNNING，但 Vivado/XSCT/XSDB 已死亡或失去身份；
- 定时器无条件刷新 heartbeat，造成虚假健康；
- 外部工具在 Skill 中绕过 MCP，Ledger 完全不可见；
- Artifact 或 Manifest 发布失败，但 Operation 仍进入 SUCCEEDED；
- MCP 重启后自动猜测旧命令结果或自动重跑；
- Platform、PL、PS 分别使用不同且互不兼容的状态语义。

## 2. 规范词

- **MUST / 必须**：不满足即违反冻结契约。
- **MUST NOT / 禁止**：任何正式 GPIO v1 路径均不得出现。
- **SHOULD / 应当**：除非有记录且审核通过的理由，否则必须实现。
- **MAY / 可以**：可选能力，不构成验收门禁。

## 3. 核心不变量

### C01 — Ledger 是执行状态唯一持久化真值

公开 MCP 对当前 Operation、Worker、资源租约、Artifact 和恢复状态的回答必须来自 Execution Ledger 或由 Ledger 明确引用的持久化记录。内存缓存只可加速，不得覆盖 Ledger 真值。

### C02 — 禁止未受监管的外部执行器

正式 Skill 不得直接启动或导入 Vivado、VivadoTclBridge、XSCT、XSDB、Tcl、`make`、链接器或内部 Manifest publisher。外部工具可以作为 MCP 内部实现，但必须受统一生命周期控制并进入 Ledger。

### C03 — 单执行通道

任意时刻最多存在一个非终态 command Operation。所有 Platform、PL、PS 命令共享同一 admission、dedup、stage、revision、resource 和 recovery gate。

### C04 — 至多一个受监管 EDA 后端

任意时刻最多存在一个活动 EDA 后端进程：`VIVADO`、`XSCT` 或 `XSDB`。切换后端前必须关闭旧后端并确认其真实 PID 不再存活。UART 是独立观测资源，不计作第二个 EDA 后端，但必须有独立资源租约。

### C05 — 真实观测与控制器心跳分离

`controller_heartbeat_at` 只表示 MCP 控制任务存活；`observed_at` 必须来自真实工具状态查询、PID/身份验证、资源活动或 Artifact 校验。禁止用控制器定时器无条件刷新 `observed_at`。

### C06 — 百分比可选，状态必选

`progress_pct` 可以缺失或为 `null`，不得估算。`status`、`current_step`、`status_source`、`worker_health` 和 `observed_at` 必须足以让智能体选择 WAIT、RECOVER、CONFIRM_RETRY 或 NEXT_STEP。

### C07 — 原子 Admission

Preflight、request signature、dedup、execution snapshot、Lane 占用和 `ACCEPTED` 写入必须在同一个 Ledger 事务中完成。失败不得创建后台 Task、Worker 或外部进程。

### C08 — 终态必须由证据闭合

外部工具返回成功不等于 Operation 成功。需要 Artifact 的命令只有在文件存在、SHA256/Revision 校验通过、必需 Manifest 原子发布成功后才能进入 `SUCCEEDED`。

### C09 — 结果未知禁止自动重跑

进程死亡、身份不符、通信丢失、Ledger 写失败或 MCP 崩溃导致结果无法证明时，Operation 必须进入 `OUTCOME_UNKNOWN`，Lane 必须进入 `RECOVERY_REQUIRED`。禁止自动重启并重跑原命令。

### C10 — 查询不得干扰活动后端

控制查询（如 `get_operation_status`、`wait_operation`、`get_execution_state`、`diagnose_execution`）在 Operation 活动时必须可用。需要占用同一 Tcl/XSCT/XSDB 通道的领域查询必须 fail-fast 返回 `CHANNEL_BUSY`，除非该查询由专用 observer 安全执行。

### C11 — 状态必须有来源

每次公开状态必须声明 `status_source`。MCP 不得把推断值伪装成供应商工具的直接状态。

### C12 — 重启先 reconcile，后接单

新 MCP 实例启动后必须先核对 Ledger、Owner Lock、Worker PID/身份、资源租约和非终态 Operation。reconcile 完成前禁止接收新的领域命令。

## 4. 冻结状态模型

### 4.1 Operation status

Operation 的公开 `status` 只允许：

| 状态 | 语义 |
|---|---|
| `ACCEPTED` | 已原子占用执行通道，尚未确认后端开始 |
| `RUNNING` | 已有真实后端、资源或本地步骤正在执行 |
| `SUCCEEDED` | 工具结果、Artifact、Manifest 和阶段推进全部闭合 |
| `FAILED` | 有确定性失败结果，未产生不可判定副作用 |
| `TIMED_OUT` | Deadline 到期；清理结果必须单独记录 |
| `INTERRUPTED` | 受控中断，结果和清理证据已记录 |
| `CANCELLED` | 在供应商工具产生不可逆副作用前完成取消 |
| `OUTCOME_UNKNOWN` | 无法证明命令成功或失败，禁止自动重跑 |

`RECOVERY_REQUIRED` 是 Execution Lane，不是 Operation status。

### 4.2 Execution Lane

| Lane | 语义 |
|---|---|
| `IDLE` | 无活动命令，可以接收下一命令 |
| `BUSY` | 有且仅有一个非终态命令 |
| `CLOSING` | Session 正在有序关闭，不接收新命令 |
| `RECOVERY_REQUIRED` | 必须先 diagnose/recover，禁止普通命令 |

### 4.3 Normalized observed state

对供应商工具、进程和资源的规范化观测只允许：

| `observed_state` | 语义 |
|---|---|
| `NOT_STARTED` | 尚未观察到真实后端启动 |
| `STARTING` | 后端已启动但尚未进入可执行状态 |
| `RUNNING` | 真实后端/资源仍在执行或活动 |
| `COMPLETE` | 真实工具明确结束；尚不代表 Artifact 闭合 |
| `FAILED` | 真实工具明确报告失败 |
| `UNKNOWN` | 无法获得足以判定的真实状态 |
| `NOT_APPLICABLE` | 纯本地、无需外部状态的步骤 |

### 4.4 Worker health

只允许：

```text
NOT_STARTED
STARTING
ALIVE
UNRESPONSIVE
DEAD
IDENTITY_MISMATCH
NOT_APPLICABLE
```

`ALIVE` 必须建立在真实 PID 存活和身份字段匹配之上，不能仅由 Python 对象存在推导。

### 4.5 Artifact state

只允许：

```text
NOT_APPLICABLE
PENDING
VERIFYING
PUBLISHING_MANIFEST
PUBLISHED
FAILED
```

## 5. 冻结 observation schema

每个非终态 command Operation 必须包含 `observation` 对象。终态 Operation 必须保留最后一次 observation。

```json
{
  "status_source": "VENDOR_RUN | PROCESS | RESOURCE | LOCAL | RECOVERY",
  "backend": "VIVADO | XSCT | XSDB | UART | PYTHON | NONE",
  "observed_state": "NOT_STARTED | STARTING | RUNNING | COMPLETE | FAILED | UNKNOWN | NOT_APPLICABLE",
  "vendor_status": null,
  "current_step": "domain-defined stable string",
  "progress_pct": null,
  "worker_health": "NOT_STARTED | STARTING | ALIVE | UNRESPONSIVE | DEAD | IDENTITY_MISMATCH | NOT_APPLICABLE",
  "pid": null,
  "process_start_time": null,
  "executable_path": null,
  "worker_generation": 0,
  "instance_id": null,
  "controller_heartbeat_at": null,
  "observed_at": null,
  "last_output_at": null,
  "detail": {}
}
```

约束：

- `vendor_status` 保留供应商原始文本，不作为跨域逻辑的唯一判断条件；
- `current_step` 必须是稳定、机器可判定的字符串；
- `progress_pct` 仅允许 `null` 或 0–100 的真实值；
- `pid` 非空时必须同时保存 `process_start_time`、`executable_path`、`worker_generation` 和 `instance_id`；
- `observed_at` 只在完成真实观测后更新；
- `detail` 不得包含决定主流程但未提升为固定字段的隐藏状态。

## 6. 公开 Operation 状态响应

`get_operation_status` 和 `wait_operation` 的成功响应至少必须包含：

```json
{
  "operation_id": "op-...",
  "tool_name": "pl_synthesize",
  "status": "RUNNING",
  "execution_lane": "BUSY",
  "workflow_stage": "PL_BUILD",
  "current_step": "SYNTHESIS",
  "status_source": "VENDOR_RUN",
  "vendor_status": "Running",
  "worker_health": "ALIVE",
  "worker_pid": 1234,
  "observed_at": "...",
  "elapsed_s": 120,
  "deadline_at": "...",
  "artifact_state": "PENDING",
  "recommended_action": "WAIT",
  "poll_after_s": 10
}
```

`progress_pct` 为可选字段。没有真实值时必须省略或返回 `null`。

### 6.1 recommended_action

只允许：

```text
WAIT
NEXT_STEP
DIAGNOSE
RECOVER
CONFIRM_RETRY
CLOSE_SESSION
NONE
```

### 6.2 CHANNEL_BUSY 响应

命令被活动 Operation 拒绝时，必须返回 `LOCK_BUSY + CHANNEL_BUSY`，并至少包含：

```json
{
  "active_operation_id": "op-...",
  "tool_name": "pl_synthesize",
  "status": "RUNNING",
  "current_step": "SYNTHESIS",
  "worker_health": "ALIVE",
  "observed_at": "...",
  "elapsed_s": 120,
  "deadline_at": "...",
  "recommended_action": "WAIT",
  "poll_after_s": 10
}
```

智能体无需猜测上一任务是谁或是否应该等待。

## 7. 统一进程与资源所有权

### 7.1 EDA 后端记录

Ledger 的 Worker 记录必须指向实际 EDA 后端，不能只记录包装它的 Python MCP Server PID。允许同时记录：

- `supervisor_pid`：可选的内部 Worker/Supervisor；
- `pid`：实际 Vivado/XSCT/XSDB 后端 PID；
- 五字段身份：PID、start time、executable path、generation、instance id。

### 7.2 后端切换

`VIVADO -> XSCT -> XSDB` 的切换必须：

1. Lane 为 IDLE；
2. 停止旧后端；
3. 确认旧 PID 消失；
4. 增加 generation；
5. 启动并验证新后端；
6. 原子更新 Ledger Worker；
7. 才允许下一 Operation 进入 RUNNING。

关闭失败必须进入 `RECOVERY_REQUIRED`，禁止并行启动新后端。

### 7.3 UART 资源

UART 不写入 EDA Worker，但必须持久化：

```text
capture_id
session_id
port
baudrate
status
started_at
last_rx_at
bytes_received
markers_found
deadline_at
```

同一端口同一时刻只能有一个 owner。MCP 重启后旧 capture 必须标为 `INTERRUPTED` 或 `OUTCOME_UNKNOWN`，不得声称仍在捕获。

## 8. Preflight 与 Admission

每个 command 必须在同一 Ledger 事务检查：

1. 请求工具存在且 category 正确；
2. Session 精确匹配；
3. 无非终态 active Operation；
4. Lane 允许；
5. Worker PID和身份与 Ledger一致，或后端为安全的 ABSENT；
6. Worker heartbeat/observation没有过期；
7. previous Operation不存在未解决的结果未知状态；
8. workflow stage和前置证据满足；
9. Board/Platform/PL/PS输入 revision匹配；
10. Project/JTAG/UART资源由本 Session持有或可安全获取；
11. request signature没有命中未确认终态；
12. 写入 immutable execution snapshot、ACCEPTED和BUSY。

任何检查失败时外部进程启动次数必须为 0。

## 9. 观测策略映射

### 9.1 Platform

- 普通 BD Tcl步骤：`status_source=PROCESS`，`current_step`按稳定步骤更新；
- `generate_target`和 synthesis：优先使用 Vivado run对象；
- 禁止在同一 Tcl eval中长期 `wait_on_run`并同时伪造心跳；
- Platform XSA和Manifest校验完成后才能 SUCCEEDED。

### 9.2 PL

- synthesis/place/route/bitstream 应使用可查询的 Vivado run状态；
- 推荐模式：`launch_runs`返回后，由 observer轮询 `STATUS/PROGRESS`；
- 如果某命令只能前台阻塞，使用真实 PID/身份、最后输出和 deadline，`progress_pct=null`；
- Bitstream SHA和 PL Manifest发布是 Operation成功条件。

### 9.3 PS Build

- XSCT PID必须进入 Worker记录；
- `ps_compile`的稳定步骤至少包括 `APP_BUILD`、可选 `MAKE_FALLBACK`、`ELF_VERIFY`、`MANIFEST_PUBLISH`；
- `SUCCEEDED`意味着最终 ELF存在、格式/架构正确且 PS Manifest已发布；
- Skill不得手工执行 `make`。

### 9.4 PS JTAG

- XSDB PID和 JTAG lease必须持久化；
- connect/select/reset/init/download/run分别记录 `current_step`；
- 短命令以明确 ToolResponse为真值；长 download/wait同时检查进程身份和 deadline；
- MCP重启后旧连接不得自动复用。

### 9.5 UART

- 状态来源为 `RESOURCE`；
- `last_rx_at`和 `bytes_received`来自真实串口读取；
- marker命中、超时、端口断开必须产生机器可判定终态。

### 9.6 Local/Manifest

- 状态来源为 `LOCAL`；
- 原子写、SHA、revision和 schema验证必须完整；
- Manifest发布失败不得 best-effort吞掉并继续 SUCCEEDED。

## 10. Deadline、超时和失效

- 每个 command在 Admission时必须写入非空 `deadline_at`；
- `wait_operation`的等待预算与 Operation deadline是两个独立概念；
- Deadline到期必须停止工具进程树并验证 PID结果；
- 清理成功：`TIMED_OUT`，记录 `pid_cleaned=true`；
- 清理失败或无法确认副作用：`OUTCOME_UNKNOWN + RECOVERY_REQUIRED`；
- Worker身份不符：立即停止普通命令接入，进入 `OUTCOME_UNKNOWN + RECOVERY_REQUIRED`；
- 观察过期但 PID仍活着：先标 `UNRESPONSIVE`，不得继续刷新虚假 `RUNNING`。

## 11. Terminal 原子性

需要 Artifact 的命令终态事务必须一次性提交：

```text
Operation terminal status
Execution Lane
Workflow stage
output_artifact_revision
completion_evidence
artifact_state
manifest path/revision
final observation
recommended_action
```

禁止出现可持久观察到的半状态，例如：

- `SUCCEEDED + manifest missing`；
- `SUCCEEDED + stage未推进`；
- `IDLE + active Operation仍RUNNING`；
- `PUBLISHED + Manifest文件不存在`。

## 12. Restart Reconciliation

启动时必须覆盖以下情况：

| Ledger/进程情况 | 结果 |
|---|---|
| 无 active op，无后端 | IDLE |
| active op非终态，后端PID已死 | OUTCOME_UNKNOWN + RECOVERY_REQUIRED |
| active op非终态，PID活但Controller已丢失 | OUTCOME_UNKNOWN + RECOVERY_REQUIRED；不自动接管命令 |
| 后端PID活但身份不匹配 | IDENTITY_MISMATCH + RECOVERY_REQUIRED |
| 无 active op但旧后端仍活 | ORPHANED/RECOVERY_REQUIRED |
| Ledger损坏或不可读 | fail-closed，拒绝领域命令 |
| UART capture遗留 | INTERRUPTED或OUTCOME_UNKNOWN，释放/恢复前禁止同端口新capture |

只有显式 `recover_execution`通过全部资源安全检查后才能回到 IDLE。

## 13. Manifest 与成功契约

### 13.1 Platform

`platform_generate SUCCEEDED`必须意味着 XSA、wrapper和 Platform Manifest全部存在且 revision一致。

### 13.2 PL

`pl_generate_bitstream SUCCEEDED`必须意味着 bitstream、XDC交叉引用、timing前置证据和 PL Manifest全部有效。

### 13.3 PS

`ps_compile SUCCEEDED`必须意味着最终 ELF、ELF架构校验、XSA/Platform cross-reference和 PS Manifest全部有效。

Manifest是成功门禁，不再是可缺失的审计附属品。

## 14. 强制测试门禁

实现本契约至少必须具有以下生产入口测试：

1. 两个并发跨域 command：恰好一个ACCEPTED，一个CHANNEL_BUSY；
2. 后端启动前 Admission失败：进程启动次数0；
3. Vivado run真实状态轮询：RUNNING到COMPLETE/FAILED；
4. 无PROGRESS工具：`progress_pct=null`但状态仍可判定；
5. MCP heartbeat存活、真实PID死亡：不得继续报告ALIVE/RUNNING；
6. PID复用/身份不符：IDENTITY_MISMATCH；
7. observer停止更新：UNRESPONSIVE；
8. Deadline到期：精确清理进程树并记录结果；
9. Ledger写失败：不得声称SUCCEEDED；
10. Manifest发布失败：Operation失败且stage不推进；
11. Platform/PL/PS后端切换：旧PID消失后新PID才出现；
12. MCP崩溃后重启：不自动重跑，进入RECOVERY_REQUIRED；
13. XSCT build内部make fallback：Agent不执行shell，ELF仍自动产出；
14. JTAG连接跨重启不可静默复用；
15. UART capture状态、last_rx和marker来自真实资源；
16. Skill和Agent脚本机械扫描无内部Bridge、publisher、直接make或EDA启动。

## 15. 冻结边界与变更规则

v1.0冻结以下内容：

- C01–C12不变量；
- Operation/Lane/observed/health/artifact枚举；
- observation必需字段；
- `status_source`、`recommended_action`语义；
- 单执行通道、单EDA后端、真实进程身份；
- Manifest成功门禁；
- restart/recovery fail-closed语义；
- Platform/PL/PS/JTAG/UART最低映射和强制测试门禁。

不冻结：

- Python类名和模块拆分；
- 轮询间隔的具体数值；
- 内部IPC形式；
- 可选的供应商原始字段；
- `progress_pct`是否存在。

任何破坏冻结行为的修改必须新增带编号 Erratum，由用户审核；不得静默改变。

## 16. 状态声明

- 本契约：**v1.0 COMPLETE / FROZEN**；
- B09公开MCP契约勘误：仍OPEN；
- B10：仍BLOCKED；
- 本文档本身不授权修改生产代码、Skill或运行硬件测试。
