# B09 Execution Observation O4 完成报告

> 日期：2026-08-12  
> 状态：**COMPLETE / FROZEN**  
> O5：**NOT STARTED**

## 1. 审计结论

O4审计发现正式server仍会构造独立`XsctBridge`，`ps_compile`只在成功后best-effort发布PS Manifest，且APP_BUILD/make fallback/ELF验证没有成为Ledger真实步骤。上述缺口均已关闭。

## 2. 产品行为

- 正式server不再构造独立`XsctBridge`；XSCT只能由`ToolProcessController`创建；
- XSCT实际PID、process_start_time、executable_path、generation和instance_id进入Worker/observation；
- 同步XSCT命令只报告真实`PROCESS`状态，不伪造vendor run或进度百分比；
- `ps_compile`稳定步骤为`APP_BUILD`、可选`MAKE_FALLBACK`、`ELF_VERIFY`、`MANIFEST_PUBLISH`；
- make fallback仅为MCP内部实现，Agent无需且不得直接调用shell；
- ELF必须位于project内、是ELFCLASS32 little-endian、`EM_ARM=40`；
- Platform/XSA交叉引用、PS Manifest发布和磁盘回读是SUCCEEDED硬门禁；
- 终态前关闭XSCT并确认PID消失，为后续XSDB切换释放唯一EDA通道；
- timeout精确清理本轮XSCT PID树并进入TIMED_OUT/RECOVERY_REQUIRED；不自动重试。

## 3. 真实入口证据

公开MCP SDK host-live完成真实流程：import hardware → create platform/BSP/app → `ps_add_sources(main.c)` → `ps_compile`。

运行期证据：

- `status_source=PROCESS`；
- `backend=XSCT`；
- 真实XSCT PID非空；
- `current_step`出现在APP_BUILD/MAKE_FALLBACK/ELF_VERIFY；
- `progress_pct=null`。

终态证据：

- ARM ELF存在且架构校验通过；
- `artifact_state=PUBLISHED`；
- `current_step=MANIFEST_PUBLISH`；
- PS Manifest路径存在并完成回读；
- Worker=ABSENT、PID=null；
- 结果：`1 passed, 11 deselected in 43.54s`。

## 4. 失败注入

- 无效/错误架构ELF：FAILED + `ELF_VERIFY_FAILED`；
- Platform Manifest缺失：FAILED + `MANIFEST_PUBLISH_FAILED`，stage不推进；
- app build无ELF：进入内部`MAKE_FALLBACK`且Ledger可见；
- timeout：精确清理owned XSCT PID并持久化TIMED_OUT；
- 独立`XsctBridge()` server源码扫描为0。

## 5. 测试与回归

- O4专项：6 collected / 6 passed；
- 最终PS/Manifest/Runner定向回归：80 passed / 5 deselected；
- 最终非硬件全量：`1286 passed, 1 skipped, 35 deselected`；
- 总收集：`1322 collected`。

## 6. 冻结SHA256

| 文件 | SHA256 |
|---|---|
| `control/xsct_execution_observer.py` | `55d3c88abf7c2b34a3d27b652980c1ebda8cbde9cef6d6ef319545b8b1f3d3a5` |
| `domains/ps/ps_bsp.py` | `5206b809c406063bc88d22158b0a8922a22941c63ffe7c38fc0eec0f4301a26d` |
| `tests/test_o4_xsct_observer.py` | `5e0d03ec3c169ce8135aa3cc06b5abd5aff9984ce475166125dde120cdfd995c` |

共享文件`control/domain_runner.py`和`domains/verification/build_manifest.py`同时承载O3/O4终态门禁，其最终SHA记录在本轮机械审计输出中。O4冻结；O5未开始，JTAG/XSDB/UART资源迁移不在本轮范围。
