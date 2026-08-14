# B09 Execution Observation O2 实施报告

> 日期：2026-08-12
> 状态：**COMPLETE / FROZEN**
> 范围：O2 — ToolProcessController 与统一 EDA 后端所有权
> 上位契约：`B09_execution_observation_contract.md` v1.0 FROZEN
> 后续：O3 未开始；B09 勘误保持 OPEN；B10 BLOCKED

## 1. 结论与边界

O2 已建立 server-scoped `ToolProcessController`，统一拥有直接 Vivado、XSCT、XSDB Tcl 后端。Ledger 现在能够记录真实工具 PID、可选包装进程 PID、进程身份、generation，并由真实 PID/身份检查写入 `PROCESS` observation。

本报告不宣称已经完成供应商 Tcl 状态观测。Vivado run `STATUS`、XSCT build 子阶段和 XSDB/JTAG 资源状态分别属于 O3、O4、O5。现有 Platform/PL/PS 领域执行器也要在这些阶段迁移到 Controller 后，才能删除旧生命周期路径。

## 2. 生产代码

| 文件 | O2 变更 |
|---|---|
| `control/tool_process_controller.py` | 新增唯一后端所有者、切换、观测、关闭和失败持久化 |
| `control/process_guard.py` | 精确 PID 存活、五字段身份、时间有效进程树、actual/supervisor 解析 |
| `control/single_worker.py` | 与新 Controller 共享生命周期锁；直接后端存在时拒绝旧 Worker 启动 |
| `server.py` | 创建 server-scoped Controller；退出时先回收直接后端；启动时 reconcile actual/supervisor |
| `tests/test_o2_tool_process_controller.py` | O201–O229，共 29 项 contract/component/cross-process/host-live 测试 |
| `tests/test_server_startup_reconcile.py` | 8 项重启恢复测试，修正旧进程仍活时错误自动恢复 |

O2 没有修改 O1 的 Ledger/Operation/Runner/Dispatcher 冻结文件。

## 3. 真实进程所有权模型

Ledger `worker` 至少持久化：

- `backend = NONE | VIVADO | XSCT | XSDB`；
- `pid`：实际 EDA/Tcl 工具 PID；
- `supervisor_pid`：可选 `.bat`/shell 包装进程 PID；
- `process_start_time`、`executable_path`、`worker_generation`、`instance_id`；
- supervisor 的 start time 与 executable path；
- `last_heartbeat_at` 与 Worker state。

Windows 进程树不只按 PPID 判定。每一条父子边还验证启动时间，防止父 PID 复用后把历史子进程误认成当前后端。该缺口曾由测试真实捕获为无关 `msedgewebview2.exe`，修复后包装进程解析连续 5 次通过。

停止策略只处理本 Controller 记录且身份匹配的 PID 树；源码没有 `/IM` 或按名称批量 kill。PID 已复用或身份不可验证时拒绝杀进程并进入 `RECOVERY_REQUIRED`。

## 4. 单后端与切换语义

- 两个并发 `ensure_backend(VIVADO)` 只创建一个实际 PID；
- `VIVADO -> XSCT -> XSDB` 必须先停止并验证旧 actual/supervisor PID 消失，再启动新后端；
- cleanup、Ledger commit 或身份确认失败时禁止启动新后端；
- crash、identity mismatch、generation tamper 均不自动 rebuild/retry；
- 旧 `SingleWorkerController` 与新 Controller 共享一把 server-scoped 生命周期锁；
- 直接后端记录阻止旧 Worker 启动，旧 Worker 记录也阻止直接后端启动。
- 原子 Admission 后只有精确 `operation_id` 可以在 BUSY Lane 启动/关闭后端；同步 Set 必须使用显式内部 owner；错误或缺失 owner 的进程启动次数为 0；
- 活动 Operation 中禁止切换到另一 EDA backend；跨 backend 的上一步必须在终态前完成关闭。

## 5. Ledger 真实 PROCESS 观测

`observe_backend()` 必须同时满足：

1. bridge 报告 ready；
2. actual PID 存活；
3. PID、start time、executable、generation、instance 全匹配；
4. supervisor 存在时其身份也匹配 Ledger；
5. active Operation ID 和状态匹配（如指定）。

成功后原子写入：`status_source=PROCESS`、实际 backend、`observed_state=RUNNING`、`worker_health=ALIVE`、实际 PID/身份和真实 `observed_at`。进程死亡或身份不符时写入 recovery observation，将未决 Operation 置为 `OUTCOME_UNKNOWN`，Lane 进入 `RECOVERY_REQUIRED`。不存在后端的查询为只读错误，不增加 Ledger sequence。

## 6. 测试证据

### 6.1 O2 专项

```text
python -m pytest \
  mcps/zynq_mcp/tests/test_o2_tool_process_controller.py \
  mcps/zynq_mcp/tests/test_server_startup_reconcile.py \
  -q -W error::RuntimeWarning
37 passed
```

其中 O2 专项 29 项，startup reconcile 8 项；真实工具测试为 1 项 `host_live`。重点覆盖：

- concurrent ensure 一 PID；
- actual/supervisor 分离与时间有效后代关系；
- PROCESS observation 写入真实 active Operation；
- 严格后端切换事件顺序；
- busy、cleanup failure、PID reuse、generation tamper fail-closed；
- Ledger commit/start identity 失败后的进程回收；
- startup reconcile 与 server finalizer；
- 旧/新生命周期互斥。
- Admission 后精确 owner 授权，错误 owner 和 BUSY backend switch 拒绝；
- SUCCEEDED 历史记录不得掩盖仍存活的旧 PID；
- live active Operation 在重启时移动为 `previous_operation=OUTCOME_UNKNOWN`；
- backend-specific executable 分类与多匹配 fail-closed。

### 6.2 真实 Vivado/XSCT/XSDB

真实 host-live 依次启动 `Vivado -> XSCT -> XSDB`，每一步验证：

- 当前 actual PID 存活且 executable 可读；
- supervisor 存在时 actual 是其时间有效后代；
- 切换后旧 actual 与 supervisor 均消失；
- generation 每次只增加 1；
- 三个后端的 `observe_backend()` 均成功；
- 最终 shutdown 后无本轮 EDA/Tcl PID 残留。

本测试没有连接 JTAG、没有选择 target、没有触碰板卡。既有 `hw_server.exe` PID 19880（2026-08-09 启动）未被触碰。

### 6.3 回归

```text
python -m pytest mcps -q \
  -m "not host_live and not device_live" \
  -W error::RuntimeWarning
1259 passed, 1 skipped, 34 deselected

python -m pytest mcps --collect-only -q
1294 tests collected
```

O1 基线为 1264 collected，本轮净增加 30 项（O2 专项29项，加1项新的reconcile缺失PID测试；其余reconcile覆盖为既有测试修正）。相邻 server/R2/R3/MCP SDK 回归为 `180 passed, 1 deselected`。RuntimeWarning 为 0。

## 7. SHA256 与机械扫描

| 文件 | SHA256 |
|---|---|
| `control/process_guard.py` | `7018645af26d2cf89d8f2f1719e0a8bedfdfee9daa9ce9dbdded57bd24ecedd6` |
| `control/tool_process_controller.py` | `ec68ae813199142d07ac01762804b6857c5b7e8cfd5c1e87dbd7258de4465cce` |
| `control/single_worker.py` | `5ab57dda17bcedcf1c2f724b66c54681bded8e057c0b819ccf23e639ae91e18d` |
| `server.py` | `e515663adac1793130f1516a3eef505531424203d41bfc0212bdfbdd2313992f` |
| `test_o2_tool_process_controller.py` | `b289265d9e8d55b4838e1dedd45361bc14a4d00dcdf5360abc67dc7047dbfbdc` |
| `test_server_startup_reconcile.py` | `14dae0383589a0aa5336f8040cfd280f837c47cf9f3a4b82894c7e8d6542fe64` |

上述为文档编写前机械值；文档修改不影响这些生产/测试文件。

O1 九个冻结 SHA256 全部匹配 O1 完成报告。以下外部冻结资产也未变：

- `.mcp.json`：`d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`；
- `Xilinx_Vivado_MCP/server.py`：`9fa66a0ca56389b73fb49cd17492306bf470f3d0b0964eb7fac0724c27b7d47b`；
- `CLAUDE.md`：`b03a060f8afde582ad91ff8d57b8ffd44c763d7ef2b5ce1853311aefee6cdee4`；
- locked Board Package manifest：`ca931987a5843a0bbc627faa40d8842c15e774662dc51e945dafaf03999c97fb`；
- Board Package 仍为 6 文件，`manifest_revision=sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`。

## 8. 待审核与后续

O2 已经用户授权独立审核，三项阻塞缺口关闭后状态为 **COMPLETE / FROZEN**。

O3 尚未开始。O3 的职责是把 Platform 与 PL Vivado 执行器迁移到本 Controller，并通过短 Tcl 查询把真实 Vivado run `STATUS`/`PROGRESS` 写入 Ledger。O4、O5 分别迁移 XSCT build 与 XSDB/JTAG/UART。只有完成 O3–O5，用户要求的“Ledger 反馈完整真实 Tcl/资源状态”才算实现。
