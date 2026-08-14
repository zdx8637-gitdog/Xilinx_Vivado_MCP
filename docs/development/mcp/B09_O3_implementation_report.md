# B09 Execution Observation O3 完成报告

> 日期：2026-08-12  
> 状态：**COMPLETE / FROZEN**  
> 上位契约：[B09_execution_observation_contract.md](B09_execution_observation_contract.md) v1.0 FROZEN

## 1. 审计结论

O3审计确认旧正式路径存在三项契约缺口：长任务依赖阻塞`wait_on_run`、Operation定时heartbeat不等于Vivado真实run状态、PL Manifest发布失败仍可能进入SUCCEEDED。三项均已关闭。

## 2. 产品行为

- 正式server不构造独立`VivadoTclBridge`；唯一入口为`ToolProcessController`；
- `launch_runs`短命令返回后轮询真实`get_property STATUS [get_runs ...]`；
- 原始STATUS规范化为STARTING/RUNNING/COMPLETE/FAILED/UNKNOWN；
- 无可信进度时`progress_pct=null`，不伪造百分比；
- synthesis/place/route/bitstream写入`VENDOR_RUN` observation；
- PID死亡、身份不符、observer超时和cleanup不确定均不得报告SUCCEEDED；
- bitstream、XDC、PL Manifest发布与回读全部成功后才可进入PS_BUILD；
- bitstream终态提交前关闭Vivado并确认PID消失；
- 运行期虚假Operation heartbeat已移除。

## 3. 真实入口证据

O3 host-live以真实Vivado 2023.1执行最小synthesis：

- `launch_runs synth_1`；
- 原始STATUS=`synth_design Complete!`；
- `status_source=VENDOR_RUN`、`observed_state=COMPLETE`；
- 记录真实Vivado PID与五字段身份；
- 正式路径中无`wait_on_run`；
- 最终复验结果：`1 passed, 20 deselected in 45.51s`。

## 4. 测试与回归

- O3专项：21 collected（20 component/contract + 1 host-live）；
- O3/O4专项合计：27 collected；
- 最终非硬件全量：`1286 passed, 1 skipped, 35 deselected`；
- 总收集：`1322 collected`；
- 唯一skip为B02 POSIX-only `test_posix_link_no_overwrite`；
- 曾出现一次B02 `test_heartbeat`同微秒flake，单项立即复验PASS；最终全量无失败。

## 5. 冻结SHA256

| 文件 | SHA256 |
|---|---|
| `control/vivado_execution_observer.py` | `d76c78fe880437dd18b4bf9836f39d19904dc9109cf1a6debc795d943df4160c` |
| `domains/platform/platform_domain.py` | `c6ccca611816cfe4ae4d1e4f5ecbbe315083b9f52cdc1b136e20f0f0aa4661a0` |
| `domains/pl/pl_bridge_tools.py` | `0f3d690d25390276b32f69e4b9b795e4d75f4e46979b47cdabf688465d7d9a15` |
| `tests/test_o3_vivado_observer.py` | `a1ed36fe0cd64573148df22736fdc43256bf5655eed1f10a083a9077cf772ec8` |

O3冻结；任何改变上述真实STATUS、单进程所有权或Manifest成功门禁的行为必须单独审计。
