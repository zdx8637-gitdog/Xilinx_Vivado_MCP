# B09 Execution Observation O1 完成报告

> 日期：2026-08-12
> 状态：**COMPLETE / FROZEN**
> 范围：O1 — Ledger v2兼容扩展
> 后续：O2未开始；B09勘误OPEN；B10 BLOCKED

## 1. 结论

O1已经实现并冻结Ledger v2公共数据基础：旧Ledger可安全读取并在首次写事务中一次性迁移；Operation可以持久化observation、artifact_state、deadline_at和recommended_action；公开状态查询与CHANNEL_BUSY响应返回相同的冻结字段；controller heartbeat与真实observed_at已经分离。

本阶段没有接入真实Vivado/XSCT/XSDB轮询，也没有改变现有领域执行器或Skill逃生通道。这些分别属于O2–O6。

独立冻结审核发现并关闭了一项关键语义错误：初版曾由Admission、本地RUNNING心跳和本地终态写入`observed_at`。最终实现已改为保持`observed_at=null`；只有`op_observe()`收到真实供应商查询、PID身份、资源活动或Artifact校验证据后才允许写入。控制器心跳只写`controller_heartbeat_at`。

## 2. 生产代码变更

| 文件 | O1变更 |
|---|---|
| `mcps/zynq_mcp/control/execution_ledger.py` | schema `1.0`→`2.0`兼容迁移、冻结枚举、observation校验、deadline和默认契约字段 |
| `mcps/zynq_mcp/control/operation_service.py` | `op_observe()`原子入口、公开Operation视图、CHANNEL_BUSY详情、heartbeat/observation分离 |
| `mcps/zynq_mcp/control/operation_registry.py` | 内存缓存恢复并保留O1字段 |
| `mcps/zynq_mcp/control/execution_gate.py` | command admission写入O1字段 |
| `mcps/zynq_mcp/control/domain_runner.py` | domain admission写入O1字段，busy/dedup返回兼容扩展 |
| `mcps/zynq_mcp/dispatcher.py` | get/wait只读Ledger真值并返回冻结字段；timeout不再伪造Operation状态 |

测试新增/增强：

- 新增 `mcps/zynq_mcp/tests/test_o1_ledger_v2.py`；
- 增强 `test_r1_wait_operation.py` 的真实状态与wait timeout断言；
- 增强 `test_r3_runner.py` 的持久化deadline断言；
- 未删除、跳过或弱化历史测试。

## 3. Ledger v2与迁移契约

### 3.1 新Operation字段

- `deadline_at`
- `artifact_state`
- `recommended_action`
- `observation`

`observation`强制包含冻结契约中的16个字段：status source、backend、observed state、vendor status、current step、progress、worker health、PID五字段、controller heartbeat、observed time、last output和detail。

O1默认值只表达本地接单事实：`status_source=LOCAL`、`backend=NONE`、`observed_state=NOT_STARTED`、`artifact_state=NOT_APPLICABLE`、`observed_at=null`。它不伪装成真实EDA观测。

### 3.2 旧Ledger迁移

- shared query对schema `1.0`执行纯内存迁移，不写文件、不增加sequence；
- 第一次`ledger_transaction`原子持久化schema `2.0`，sequence只增加一次；
- session、context、active/previous operation和dedup registry不丢失；
- 旧heartbeat只进入`controller_heartbeat_at`，不冒充`observed_at`；
- 未知schema、损坏JSON或非法v2 observation均fail-closed。

## 4. 原子观测与公开响应

`op_observe()`在单个Ledger事务中更新observation及可选artifact/action字段：

- 真实观测更新必须带新的`observed_at`；
- 只更新controller heartbeat时不改`observed_at`；
- observation更新不得移动Operation状态或execution lane；
- 终态Operation拒绝继续写观测；
- 写入失败保留原Ledger字节。

`get_operation_status`、`wait_operation`和active-operation `CHANNEL_BUSY`均返回统一字段，包括：

- `status_source`、`backend`、`observed_state`、`vendor_status`、`current_step`、`progress_pct`；
- `worker_health`、`pid`、`observed_at`、`controller_heartbeat_at`；
- `deadline_at`、`deadline_remaining_s`、`artifact_state`、`recommended_action`。

`wait_operation`超时返回真实Operation状态（例如`RUNNING`）并增加`wait_timed_out=true`，不再返回不属于Operation状态机的`still_running`伪状态。公开查询不回退到可能过期的内存缓存，也不增加Ledger sequence。

## 5. O1专项测试

机械收集：**24 tests**，全部通过。

覆盖O101–O119及补充分支：

- 新v2 Ledger、v1只读迁移、首次事务持久化；
- 未知schema、缺失/损坏observation fail-closed；
- admission字段、deadline、原子`op_observe()`；
- heartbeat与observed_at分离、PID身份和progress校验；
- 终态保留真实观测、终态观测拒绝；
- 写失败字节不变；
- get/wait/busy公开字段、cache真值隔离、query sequence不变；
- OperationRegistry重启恢复O1字段。

测试质量机械检查：21个pytest函数（参数化后24 cases），`assert True=0`，空测试=0，RuntimeWarning=0。

## 6. 回归证据

### 6.1 最终非硬件回归

```text
1225 passed, 1 skipped, 38 deselected in 170.48s
```

命令排除了`host_live`、`device_live`以及5个会重复启动真实旧Vivado MCP的R2测试。唯一skip为B02既有POSIX-only `test_posix_link_no_overwrite`。

### 6.2 本轮另行真实入口验证

| 层 | 结果 |
|---|---|
| R2真实旧Vivado MCP启动/PID/崩溃/握手/并发 | 5 passed |
| B05 public快速路径 | 5 passed |
| B05真实Vivado Platform生成/XSA路径 | 2 passed |

分层合计：`mcps`共1264 collected；本轮覆盖1237 passed + 1 skipped；剩余26项为与O1无关、具有JTAG/UART/XSCT或其他设备副作用的host/device-live，未伪报为通过。

最后一处仅日志卫生修订后，受影响的O1/R1/R3专项再次执行：**99 passed**。Python `py_compile`通过。

## 7. 冻结资产不变

O1冻结文件SHA256：

| 文件 | SHA256 |
|---|---|
| `execution_ledger.py` | `842eb633abb93d95a28d99196b72ecab276b98a0c234b00a65a417e5219c0b44` |
| `operation_service.py` | `0376226d096b3e84aa3c7704e5fb01cb7608365a533a6aa02ff3425af7cfc509` |
| `operation_registry.py` | `69f193702def792a52f51cc8786ddf0e99fdc32ea18bd50b46e84203043638d5` |
| `execution_gate.py` | `83d8f7271943faf25869a4b6ceb2b8ed436ca7f650e6a7171ae3ac1ae7b2233b` |
| `domain_runner.py` | `f8e94faeefc061f3dc40d31d13cefcc73b0c4c145f9695dbfabaf2e5173ed855` |
| `dispatcher.py` | `3845abe6c5ab94498dda811045221e7a691cacb6d1e50643b1dedb11c4a9f3ed` |
| `test_o1_ledger_v2.py` | `99c3a7807316a86eab66bcdade55bde926d7c01722042fa478238810982704af` |
| `test_r1_wait_operation.py` | `b133ce6bd9b3770d1710a7db2550c93b288f5bdb8e1f8611fe25293394532dbf` |
| `test_r3_runner.py` | `e149294f39233f5970b833fccf17debfcbf98dd236e30edd27efed33fa0b65fa` |

| 资产 | SHA256 |
|---|---|
| `.mcp.json` | `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02` |
| `Xilinx_Vivado_MCP/server.py` | `9fa66a0ca56389b73fb49cd17492306bf470f3d0b0964eb7fac0724c27b7d47b` |
| `CLAUDE.md` | `b03a060f8afde582ad91ff8d57b8ffd44c763d7ef2b5ce1853311aefee6cdee4` |
| locked Board Package manifest | `ca931987a5843a0bbc627faa40d8842c15e774662dc51e945dafaf03999c97fb` |

系统中存在一个2026-08-09启动的既有`hw_server.exe`（PID 19880），早于本轮O1；本轮未触碰或将其认作O1进程。

## 8. 明确边界

- O1没有实现真实EDA进程所有权或轮询；这是O2/O3/O4。
- O1没有实现JTAG/UART资源观测；这是O5。
- O1没有修改Skill或删除手工逃生通道；这是O6后置门禁。
- O1没有重新运行Agent2黑盒B09；这是O7。
- O1没有进入B10。

**最终状态：O1 COMPLETE / FROZEN。O2进入现状审计；尚未完成实现。**
