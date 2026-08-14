# B09 Execution Observation O5 完成报告

> 日期：2026-08-13  
> 状态：**COMPLETE / FROZEN**  
> 范围：O5 — PS JTAG、UART 与资源级真实观测  
> 后续：O6 **NOT STARTED**；Agent2未调用；B10保持BLOCKED

## 1. 结论

O5已经把JTAG连接和UART capture从进程内辅助状态提升为Execution Ledger中的持久化资源真值。正式统一Server不再直接构造独立XSDB bridge；XSDB只可由`ToolProcessController`启动，并与Vivado/XSCT共享单一EDA后端通道。UART作为独立资源可在CPU/JTAG命令之间持续采集，但不会创建第二个Command Operation。

真实SDK-only设备门禁通过：统一`zynq_mcp`完成JTAG连接、目标选择、PS初始化、FPGA编程、XSA加载、ELF下载、CPU运行和COM4 capture，最终捕获`GPIO_E2E_PASS`。该测试没有导入内部bridge、没有直接打开串口、没有直接调用XSDB，也没有手工修改Ledger。

## 2. 生产变更

| 文件 | O5变更 |
|---|---|
| `control/resource_registry.py` | 新增持久化JTAG lease、UART capture、公开resource view与真实RESOURCE observation |
| `control/xsdb_execution_observer.py` | 新增Controller-owned XSDB执行facade；PROCESS预观测与RESOURCE结果观测分离 |
| `control/execution_ledger.py` | Worker默认记录增加`jtag_lease`与`uart_capture` |
| `control/tool_process_controller.py` | 后端切换保留UART资源；XSDB关闭保留已断开的JTAG审计记录；PID清理仍由Controller证明 |
| `control/domain_runner.py` | PS JTAG/PL program统一走Controller；JTAG/UART owner-aware P9；direct UART也写RESOURCE observation |
| `control/recovery.py` | recovery把旧JTAG标为ORPHANED、旧UART标为INTERRUPTED，禁止伪连接/伪运行 |
| `dispatcher.py` | PS/PL资源要求接线；close_session依次清理UART与受控后端；公开状态返回resources |
| `server.py` | 注入单一JTAG/UART registry；启动reconcile使旧资源失效；finalizer清理UART |
| `domains/ps/uart_capture.py` | 正式路径委托持久化facade；旧内存registry仅保留历史组件兼容 |

## 3. JTAG资源契约

JTAG lease持久化以下关键字段：`lease_id`、`owner_session_id`、`hw_server_url/lock_key`、`status/connected`、`target_id/target_name`、`acquired_at/last_observed_at/heartbeat_at/ttl_s`、`worker_generation`和`instance_id`。

- connect成功后记录`CONNECTED`，并绑定真实XSDB PID、generation和instance；
- select更新目标ID与名称；reset/init/program/loadhw/download/run等步骤写稳定`current_step`；
- foreign owner、错误generation、错误instance和过期lease均在外部执行前拒绝；
- disconnect先记录`DISCONNECTED`，再关闭Controller后端并验证真实PID消失，之后才允许Operation SUCCEEDED；
- MCP重启把旧连接标为`ORPHANED`，不能跨实例静默复用；
- recover后旧lease保持审计记录但`connected=false/held=false`，可显式新建连接。

## 4. UART资源契约

UART capture持久化：`capture_id`、`session_id`、`port`、`baudrate`、`status`、`started_at`、`last_rx_at`、`bytes_received`、`markers_found`、`deadline_at`、`finished_at`和`instance_id`。

- 同端口已有owner时第二次capture在打开串口前拒绝；
- `last_rx_at`和`bytes_received`只由真实serial read更新；
- marker命中记录`MATCHED`和`UART_MARKER_MATCH`；
- timeout/partial/disconnect均产生机器可判定状态；
- stop关闭真实handle、清除owner并保留最终文本/字节/marker审计记录；
- 一次性read/write/list同样使用`status_source=RESOURCE`；
- restart/recovery把遗留活动capture改为`INTERRUPTED`，不再声称RUNNING。

## 5. 公开状态

`get_execution_state`和`diagnose_execution`现在都返回`resources`：

- `resources.jtag`：held/connected/status/owner、完整lease、worker PID/generation/instance；
- `resources.uart`：serial_owner、完整capture、active。

公开Operation状态中的JTAG/UART结果使用`status_source=RESOURCE`，并保留`backend`、`current_step`、`observed_at`和resource detail。Query仍只读Ledger，不增加sequence。

## 6. O5专项测试

### 6.1 Component / contract（8 passed）

`test_o5_resource_observation.py`覆盖：

1. 正式Server无standalone XSDB构造；
2. 真实受控子进程PID、JTAG lease、target与disconnect PID消失；
3. foreign/stale JTAG lease在executor调用前拒绝；
4. UART marker、bytes、last_rx与stop持久化；
5. 同端口第二capture在serial factory调用前拒绝；
6. 设备断开形成`DISCONNECTED/UART_DISCONNECTED`；
7. restart使JTAG/UART旧记录失效；
8. 一次性UART read写入RESOURCE observation。

### 6.2 Host-live（1 passed）

`test_o5_public_jtag_resource_truth`完全通过MCP SDK验证：

- 实际连接`localhost:3121`；
- `status_source=RESOURCE`、`backend=XSDB`、真实PID大于0；
- Ledger公开lease的owner/generation/instance与Worker一致；
- 动态选择真实`ARM Cortex-A9 MPCore #0`并持久化target；
- disconnect后公开状态为`DISCONNECTED`且Worker PID为空。

### 6.3 Device-live（1 passed）

`test_o5_public_gpio_uart_marker_resource_truth`完全通过MCP SDK执行：

```text
connect -> UART capture(COM4/115200) -> list/select ARM #0
-> halt -> system reset -> ps7_init -> program FPGA
-> loadhw -> download ELF -> run
-> wait marker(GPIO_E2E_PASS) -> stop capture -> disconnect
```

结果：marker精确命中；`GPIO_E2E_FAIL`不存在；`bytes_received > 0`；`last_rx_at`非空；Operation observation为`RESOURCE/UART/UART_MARKER_MATCH`。测试结束只通过公开MCP执行stop/disconnect。

## 7. 回归与机械统计

| Gate | 结果 |
|---|---|
| O5专项 | **10 passed**（8 component/contract + 1 host-live + 1 device-live） |
| `mcps` collect | **1332 collected** |
| 非硬件全量 | **1294 passed, 1 skipped, 37 deselected** |
| 唯一skip | B02既有POSIX-only `test_posix_link_no_overwrite` |
| RuntimeWarning | 0（`-W error::RuntimeWarning`） |
| O5残留XSDB/XSCT/Vivado子进程 | 0 |

系统中既有`hw_server.exe` PID 19880及其`cmd.exe`父进程早于O5，本轮只连接、不终止、不认领。禁止按进程名清理的规则保持不变。

## 8. O5冻结SHA256

| 文件 | SHA256 |
|---|---|
| `resource_registry.py` | `c540cd00bfa081d437881bda74e37d704252d97aa99cefae9460dc4a1eab945b` |
| `xsdb_execution_observer.py` | `f1afc7afa8abded1072f1c41c851780e6b8ff6ebb2e51640b847cdbc17b55faa` |
| `execution_ledger.py` | `dd5679bb9afac06d1d8fc4d109316b5b9f29819e55463047ef5b3de688147d5d` |
| `tool_process_controller.py` | `3b7a1540c16bc8cba28034ba25daf0fcf7737f627a07c95b2a30eac3e36b3e00` |
| `domain_runner.py` | `bc0c77ebeb79a676180e263bc22fd067ca4c604137a1347efe5505e38943ac30` |
| `recovery.py` | `68fb850232f2f4a9b3f99820b05e9cc6ee8d50b294ae35a86bb6015afe4fe8e2` |
| `dispatcher.py` | `e056d9c9a0565b9736a68579ad2b633dcdff7d635613f3ec467f861524a7503b` |
| `server.py` | `b57942a319d25bd26594940485cb8ddef71bad8d75808262ce8bb6d3ec882847` |
| `uart_capture.py` | `8245f8b42000f9b26557b0b38f3677cf483be3c51ca58b03b2a800d5fa8bb85f` |
| `test_o5_resource_observation.py` | `048587fe97210ccee5f362e11f1484c8b9880de06da74d4a9b7bbb19a04305c7` |
| `test_o5_public_resource_live.py` | `2d722a3df948463ead1986f337f921ea1d55066e111c12e1f97eb47b7ce9620c` |

## 9. 冻结资产不变

- `.mcp.json`: `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`
- `CLAUDE.md`: `b03a060f8afde582ad91ff8d57b8ffd44c763d7ef2b5ce1853311aefee6cdee4`
- `Xilinx_Vivado_MCP/server.py`: `9fa66a0ca56389b73fb49cd17492306bf470f3d0b0964eb7fac0724c27b7d47b`
- `mcps/common/context.py`: `37bb0d1ad7ec85385f2cd753dc5e0bb09b9a8edd4b0516b3418624e6e373833c`
- 锁定Board Package六文件SHA256全部保持B03冻结值；`manifest_revision=sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`。

## 10. 明确声明

- O5：**COMPLETE / FROZEN**；
- O6：**NOT STARTED**；Skill逃生通道尚未修改；
- Agent2：未调用；B09纯黑盒最终重验尚未开始；
- B10：继续BLOCKED；
- O1–O4冻结结论不变。
