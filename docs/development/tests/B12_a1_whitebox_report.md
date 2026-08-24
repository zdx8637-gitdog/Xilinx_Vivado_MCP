# B12-A1 白盒（Agent1）报告 — P0 BLOCKED（板卡包 drift 阻断 create_session）

> 日期：2026-08-24（`Get-Date` 实测 2026-08-24 22:15 +08:00）
> 角色：Agent1（白盒）| 执行面：仅公开 `zynq_mcp`（103 工具，mcp SDK `stdio_client +
> ClientSession`）+ 允许的工作区写 | 全程零 shell 逃生
> 工作区：`workspaces/b12_a1_agent1_20260824/`
> 状态：**P0 BLOCKED** — 在 S0/S1 首步 `create_session` 即被板卡配置包校验失败阻断，
> 未进入任何 EDA/构建/Manifest/部署/观测动作。

## 1. 结论（一句话）

公开 MCP 已按 B11 套路正常启动（103 工具，`get_capabilities`/`list_tools` 正常），
但 `create_session` 返回 `BOARD_INVALID: Package validation failed: EXTRA_FILE_IN_DIR`：
板卡包 `boards/ALINX_AX7020_v1.0/` 内新增了 ADC 事实资产（commit `12cec8f`），而冻结
的 `package_manifest.json` 未同步更新，导致公开契约的「包完整性校验」fail-closed。
此漂移属 `boards/` 冻结资产，**超出白盒 Agent1 的修改边界**（铁律：不改 `boards/`），
且无公开契约绕行路径 → 按铁律输出 **P0 BLOCKED**，等待用户/管理者修复后重跑。

## 2. 环境预检结果（S0，全部通过）

| 检查项 | 结果 |
|---|---|
| 时间 | 2026-08-24 22:15:15 +08:00（`Get-Date`） |
| Python | 3.12.9 |
| `mcp` 包 | 1.28.1（`importlib.metadata.version('mcp')`） |
| Vivado 2023.1 | `D:\Xilinx\Vivado\2023.1\bin\vivado.bat` 存在 |
| Vitis/XSCT 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\xsct.bat` 存在 |
| hw_server | **未运行**（PID 19880 已不在；无 `hw_server` 进程；3121 端口无监听）——记录见 §5，属 S7 前置、与本次 P0 无关 |
| 公开 MCP 启动 | `python -m mcps.zynq_mcp.server` 经 SDK `stdio_client`+`ClientSession` 启动成功，`INIT ok; tools=103` |

## 3. P0 阻断证据（机器可复核）

### 3.1 `create_session` 公开响应（原样）

```json
{"tool": "create_session",
 "result": {"status": "error",
   "error": {"code": "INTERNAL_ERROR",
     "message": "BOARD_INVALID: Package validation failed: EXTRA_FILE_IN_DIR",
     "recoverable": false, "details": null}}}
```

调用参数：`{"board_id": "ALINX_AX7020_v1.0",
"project_path": "D:\\fpgaproject\\workspaces\\b12_a1_agent1_20260824\\project"}`。

### 3.2 板卡包目录实际内容（`boards/ALINX_AX7020_v1.0/`）

| 条目 | 类型 | 是否在冻结 manifest 中 |
|---|---|---|
| `package_manifest.json` | 文件 | manifest 自身（豁免） |
| `board_profile_ALINX_AX7020_v1.0.json` | 文件 | ✅（primary_data_source） |
| `ps7_preset.tcl` | 文件 | ✅（ps7_hardware_preset） |
| `board.xdc` | 文件 | ✅（pl_pin_constraints） |
| `SOURCES.md` | 文件 | ✅（provenance_record） |
| `README.md` | 文件 | ✅（human_description） |
| **`adc/`**（含 `ADI-ad7606c-16_cn_Rev.0.pdf`、`ad7606c_module_facts.md`） | 目录 | ❌ **EXTRA_FILE_IN_DIR** |
| **`adc_ad7606c_pinmap.json`** | 文件 | ❌ **EXTRA_FILE_IN_DIR** |

### 3.3 冻结 manifest 的 files 清单（`package_manifest.json`，status=locked）

仅 5 项：`board_profile_ALINX_AX7020_v1.0.json`、`ps7_preset.tcl`、`board.xdc`、
`SOURCES.md`、`README.md`。`manifest_revision =
sha256:72191212a1bb3359d1d55096417f0d41ed772fd6b04e5fd85b2b512a7431e4d7`
（与 B11 记录一致，证明 manifest 自 B11 以来未变）；`board_profile_sha256 =
sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18`。

### 3.4 根因定位（git）

`git log --oneline -- boards/`：

```
12cec8f boards: AD7606C-16 physical-facts assets (pinmap JSON + module facts card
        + ADI datasheet in board package; vendor docs incl. example code marked
        white-box-only/black-box-forbidden; rar archives gitignored)
4e0d148 Initial commit: ... core baseline ...
```

`git show 12cec8f --name-only -- boards/ALINX_AX7020_v1.0/package_manifest.json`
**输出为空** ⇒ commit `12cec8f` 向板卡包目录新增了
`adc/ADI-ad7606c-16_cn_Rev.0.pdf`、`adc/ad7606c_module_facts.md`、
`adc_ad7606c_pinmap.json`，但**未同步更新 `package_manifest.json`**。当前 HEAD =
`b180278`（"docs: B12 approved - A1 DMA loopback validation started"），工作树对
`boards/` 无未提交改动（`git status --short -- boards/` 为空）。

### 3.5 校验逻辑（生产代码，只读确认，未修改）

`mcps/common/board_profile.py::board_profile_load` → `validate_package_full`
（`board_package.py` L1053-1063）对 `os.listdir(package_dir)` 逐项比对 manifest
`files` 清单，未列出的顶层条目报 `EXTRA_FILE_IN_DIR`。加载路径（`board_profile_load`）
**无任何 env/排除开关**；`exclude_from_extra_files` 仅存在于 freeze 路径
（`_validate_package_except`），不作用于加载路径。⇒ 无公开契约绕行。

## 4. 为什么必须停（无法按公开契约绕行）

1. `create_session` 是 S0→S8 全流程的第一步，所有 Platform/PL/PS/JTAG/UART 原子
   都绑定 session 上下文；session 创建失败 ⇒ 全流程无法推进。
2. 修复路径只有两条，**都在白盒 Agent1 禁止区**：
   - 更新 `boards/ALINX_AX7020_v1.0/package_manifest.json` 把 3 个 ADC 资产纳入并
     re-freeze（或按 freeze 语义新建 `ALINX_AX7020_v1.1/`）——直接改动 `boards/` 冻结
     资产，铁律明令禁止；
   - 移动/删除 `adc/`、`adc_ad7606c_pinmap.json`——同样改动 `boards/`，禁止。
3. 伪造一份「干净」板卡包副本并改 `ZYNQ_BOARD_PROFILE_DIRS` 指向它：会制造与
   冻结唯一数据源不一致的 `board_profile_sha256`，破坏 `verify_consistency` 的
   板卡一致性契约，属「绕过冻结资产」，同样禁止。
4. 此非「MCP 能力缺口」（MCP fail-closed 行为正确），而是**板卡包冻结后又被 commit
   `12cec8f` 追加资产但未同步 manifest 的配置漂移**，属 P0（阻断当前步骤），修复
   责任在用户/管理者，不在白盒 Agent1。

## 5. 附带发现（非本次 P0 根因，记录备查）

- **hw_server 未运行**：任务假设「可能仍有旧 PID 19880 在 127.0.0.1:3121」不复存在。
  实测无 `hw_server` 进程、3121 无监听（`Get-Process -Name hw_server` 空、
  `Get-NetTCPConnection -LocalPort 3121` 空）。若走到 S7 JTAG 部署，需用户先启动
  `hw_server`（公开 MCP 无「启动 hw_server」工具，`ps_connect_hw_server` 仅连接已
  运行实例；`find_*` 只覆盖 xsct/xsdb/vivado，无 `find_hw_server`）。属 S7 前置环境
  缺口，本轮因 P0 更早阻断未触及。

## 6. 本轮已完成 / 未触及 / 未改

- 已完成：环境预检；公开 MCP 启动并验证 103 工具；必读输入全部读取；白盒参考
  （`pl_config.tcl`/`ps_config.tcl`/`dma_intr.c`）与生产代码（`platform_atoms.py`/
  `ps_bsp.py`/`build_manifest.py`/`consistency_check.py`/`domain_runner.py`）只读梳理；
  工作区 driver（`mcp_client.py`/`send_cmd.py`）与自写固件 `src/main.c` 已就绪
  （待 board 修复后即可进入 S5）。
- 未触及：**零** EDA/构建/Manifest/部署/观测动作（`create_session` 即被拒，未产生
  任何 Vivado/XSCT/XSDB 进程）。
- 未改：`mcps/`、`skills/`、`boards/`、冻结文档、三个 legacy 目录；仅写了工作区文件
  与本报告。

## 7. 解除阻断所需的用户/管理者动作（二选一）

1. **更新板卡包 manifest**（推荐，需用户授权）：把 `adc/ADI-ad7606c-16_cn_Rev.0.pdf`、
   `adc/ad7606c_module_facts.md`、`adc_ad7606c_pinmap.json` 纳入 `package_manifest.json`
   的 `files`（含 SHA256/role），重新 freeze（或按 `ALINX_AX7020_v1.1/` 语义新建版本）；
2. **回退 ADC 资产**：若 B12-A1 本切片（DMA）确不需 ADC 资产，可将 `adc/` 与
   `adc_ad7606c_pinmap.json` 移出板卡包目录（由管理者执行，非 Agent1）。

修复后重跑：`create_session` 即应 SUCCEEDED，随后 S0–S8 按既有方案推进
（S3 选型记录与 main.c 已备好，可直接复用）。
