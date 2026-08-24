# B12-B03 合同简化勘误 — 退役运行时封条，保留小票与对账

> 日期：2026-08-24 ｜ 类型：Erratum（合同勘误）｜ 触发证据：
> [B12_a1_whitebox_report.md](../tests/B12_a1_whitebox_report.md)（P0 BLOCKED：`EXTRA_FILE_IN_DIR`）
> 状态：**已实施，非硬件回归通过（基线不下降）**

## 1. 用户授权摘录与理由

用户裁决：**运行时封条退役，保留「小票与对账」**：

1. 加载并解析板卡事实；
2. 记录 `board_profile_sha256` 进 session/Ledger（证据）；
3. 产物间一致性 `verify_consistency` 照旧；
4. 冻结纪律降为文档级（SHA 表记在冻结基线文档，不进热路径）；
5. 用户对自己提供的板卡信息负责，框架不审判用户输入。

**理由**：B12-A1 白盒在 `create_session` 被板卡包运行时封条 `EXTRA_FILE_IN_DIR` 阻断。根因是
commit `12cec8f` 向 `boards/ALINX_AX7020_v1.0/` 新增 3 个 ADC 事实资产（`adc/` 目录 + `adc_ad7606c_pinmap.json`），
而冻结的 `package_manifest.json` 未同步、加载路径无排除开关。封条把「合法演进」（新增事实资产）
误判为「配置漂移」，fail-closed 却误伤了合法演进——这正是要退役它的原因。信任用户输入 + 用小票与
对账提供审计线索，比运行时硬封条更符合「板卡事实是用户唯一数据源」的定位。

## 2. 变更点（运行时门禁移除位置与保留路径）

### 2.1 退役（从运行时热路径移除）

| 校验 | 原触发点 | 新合同 |
|---|---|---|
| `EXTRA_FILE_IN_DIR`（目录含 manifest 未列文件） | `board_package.py::validate_package_full` 目录扫描 | 不再触发；审计脚本保留 |
| `EXTRA_FILE_IN_MANIFEST` / `MISSING_FILE_IN_MANIFEST`（精确文件集） | `_check_exact_file_set` | 不再触发；审计脚本保留 |
| SHA 交叉引用漂移（`SHA256_MISMATCH`/`SHA_CROSS_REF_MISMATCH`/`PROFILE_SHA256_MISMATCH`/`PRESET_SHA256_MISMATCH`/`XDC_SHA256_MISMATCH`） | `_validate_sha_cross_refs` | 不再触发（④ SHA 表 → 文档级） |
| `BAD_REVISION`（manifest_revision ≠ compute_revision） | `validate_package_manifest` 内 | 不再触发（④） |
| manifest 文件清单缺文件（`PATH_NOT_FOUND`） | `validate_package_full` 存在性检查 | 不再触发（board_profile 除外，见 §2.2） |
| 语义交叉校验（`DDR_CAPACITY_INCONSISTENT`/`QSPI_WINDOW_INCONSISTENT`/`LED_COUNT_XDC_MISMATCH`/`CLOCK_FREQ_XDC_MISMATCH`） | `_validate_ddr/qspi/led_xdc/clock_xdc_consistency` | 不再触发（⑤ 框架不审判用户输入） |
| manifest 结构校验（`MANIFEST_SELF_REFERENCE`/`DUPLICATE_PATH`/manifest 文件路径 `ABSOLUTE_PATH_FORBIDDEN`） | `validate_package_manifest` | 不再触发（manifest 仅读证据） |

### 2.2 保留（fail-closed 保留该保的）

| 校验 | 位置 | 说明 |
|---|---|---|
| `board_profile` 文件必须存在 | `board_profile.py::_resolve_profile_path` | 缺失 → `FileNotFoundError` → `BOARD_INVALID` |
| `board_profile` JSON 可解析 | `board_profile.py` | 损坏 → `INVALID_JSON` |
| `board_id` 一致 | `board_profile.py` + `validate_package_runtime` | 仍拒绝（身份，非审判） |
| `board_profile` schema（必需字段/类型/SHA 格式） | `validate_board_profile` | 「可解析」= 结构有效（①） |
| `board_profile_sha256` 计算与写入 | `board_profile.py` + `session.py` | 小票（②），**零改动** |
| manifest 生命周期门（`INVALID_JSON`/`MISSING_MANIFEST`(prod)/`PACKAGE_STATE_CONFLICT`/`PACKAGE_NOT_LOCKED`） | `find_manifest_status` | 仍拒绝（manifest 状态，非封条） |
| 绝对/个人路径封条（profile 内 `ABSOLUTE_PATH_FORBIDDEN`） | `validate_package_runtime` → `_validate_profile_paths` | 路径安全 fail-closed，保留 |
| `verify_consistency` + 三 Manifest 链 | `domains/verification/consistency_check.py` | **零改动** |
| `freeze_package()` 完整校验 | `board_package.py::freeze_package` → `validate_package_full` | 开发期冻结仍全量校验 |

### 2.3 实现

- 新增 `mcps/common/board_package.py::validate_package_runtime()`：运行时只校验 `board_id` 一致 +
  路径安全（`_validate_profile_paths`），manifest 仅读证据。
- `mcps/common/board_profile.py`（非 fixture 分支）：`validate_package_full` → `validate_package_runtime`；
  原 `ARTIFACT_STALE` 分支移除（运行时不再产出 SHA 类 reason_code，统一 `CONTEXT_INVALID`）。
- `validate_package_full()` **未改动**，仍服务于 `freeze_package()` 与审计工具。

## 3. 测试映射表（旧 → 新，数量对照）

重映射原则：所有断言「额外文件 / 漂移 / 语义不一致 / manifest 结构 → 拒绝」的测试，改为
新合同语义——(a) 额外文件不再拒绝、(b) board_profile 缺失/损坏仍拒绝（保留未动）、(c) 篡改已加载
文件 → 指纹变化被记录（不拒绝）。

| # | 旧测试（断言拒绝） | 新测试（断言接受/指纹记录） | 语义 |
|---|---|---|---|
| 1 | `test_ps7_preset_missing_rejected` | `test_ps7_preset_missing_accepted` | 使用点自然校验 |
| 2 | `test_board_xdc_missing_rejected` | `test_board_xdc_missing_accepted` | 使用点自然校验 |
| 3 | `test_files_missing_entry` | `test_files_missing_entry_accepted` | 封条退役 |
| 4 | `test_files_extra_entry` | `test_files_extra_entry_accepted` | 封条退役 |
| 5 | `test_files_sha_vs_revision_inputs_mismatch` | `test_files_sha_vs_revision_inputs_mismatch_accepted` | SHA 表 → 文档级 |
| 6 | `test_revision_inputs_vs_disk_mismatch` | `test_revision_inputs_vs_disk_mismatch_accepted` | SHA 表 → 文档级 |
| 7 | `test_profile_preset_sha_vs_disk_mismatch` | `test_profile_preset_sha_vs_disk_mismatch_accepted` | 使用点自然校验 |
| 8 | `test_cache_invalidates_on_preset_change` | （同名）改为断言加载成功 | 缓存仍失效，不拒绝 |
| 9 | `test_cache_invalidates_on_xdc_change` | （同名）改为断言加载成功 | 同上 |
| 10 | `test_cache_invalidates_on_manifest_change` | （同名）改为断言加载成功 | 同上 |
| 11 | `test_cache_invalidates_on_extra_file` | （同名）改为断言加载成功 | 封条退役 |
| 12 | `test_cache_invalidates_on_second_profile` | （同名）改为断言加载成功 | 封条退役 |
| 13 | `test_path_backslash_rejected` | `test_path_backslash_accepted` | manifest 路径不再运行时校验 |
| 14 | `test_path_absolute_drive_rejected` | `test_path_absolute_drive_accepted` | 同上 |
| 15 | `test_path_drive_relative_rejected` | `test_path_drive_relative_accepted` | 同上 |
| 16 | `test_path_dotdot_rejected` | `test_path_dotdot_accepted` | 同上 |
| 17 | `test_path_duplicate_rejected` | `test_path_duplicate_accepted` | 同上 |
| 18 | `test_preset_sha_reason_code` | `test_preset_sha_tamper_accepted` | 使用点自然校验 |
| 19 | `test_extra_file_reason_code` | `test_extra_file_manifest_entry_accepted` | 封条退役 |
| 20 | `test_bad_revision_reason_code` | `test_bad_revision_accepted` | 修订漂移 → 证据 |
| 21 | `test_profile_sha_drift_detected` | `test_profile_sha_drift_recorded` | (c) 指纹变化被记录 |
| 22 | `test_ddr_capacity_inconsistency_rejected` | `test_ddr_capacity_inconsistency_accepted` | 不审判用户输入 |
| 23 | `test_qspi_window_inconsistency_rejected` | `test_qspi_window_inconsistency_accepted` | 不审判用户输入 |
| 24 | `test_led_count_xdc_mismatch_rejected` | `test_led_count_xdc_mismatch_accepted` | 不审判用户输入 |
| 25 | `test_clock_freq_xdc_mismatch_rejected` | `test_clock_freq_xdc_mismatch_accepted` | 不审判用户输入 |
| 26 | `test_sources_md_tamper_unsealed` | `test_sources_md_tamper_accepted` | 使用点自然校验 |
| 27 | `test_readme_md_tamper_unsealed` | `test_readme_md_tamper_accepted` | 使用点自然校验 |
| 28 | `test_manifest_revision_wrong_value` | `test_manifest_revision_wrong_value_accepted` | 修订漂移 → 证据 |
| 29 | `test_preset_sha_field_wrong` | `test_preset_sha_field_wrong_accepted` | SHA 表 → 文档级 |
| 30 | `test_xdc_sha_field_wrong` | `test_xdc_sha_field_wrong_accepted` | SHA 表 → 文档级 |
| 31 | `test_revision_inputs_sha_wrong` | `test_revision_inputs_sha_wrong_accepted` | SHA 表 → 文档级 |
| 32 | `test_files_sha_wrong_in_manifest` | `test_files_sha_wrong_in_manifest_accepted` | SHA 表 → 文档级 |
| 33 | `test_profile_extra_file_on_disk` | `test_profile_extra_file_on_disk_accepted` | 封条退役 |
| 34 | `test_recovery_after_tamper` | （同名）改为断言篡改后仍加载 | (c) 不拒绝 |
| 35 | `test_preset_sha_mismatch_at_load` | `test_preset_sha_tamper_accepted_at_load` | 使用点自然校验 |
| 36 | `test_xdc_sha_mismatch_at_load` | `test_xdc_sha_tamper_accepted_at_load` | 使用点自然校验 |
| 37 | `test_cache_invalidates_on_file_change` | （同名）改为断言指纹变化被记录 | (c) 指纹被记录 |

**数量对照**：37 个「拒绝」测试 → 37 个「接受/记录」测试，**净减 0**。

### 新增回归测试

| 新测试 | 位置 | 断言 |
|---|---|---|
| `test_create_session_with_extra_file_succeeds` | `mcps/zynq_mcp/tests/test_r1_session.py::TestSession` | 复制真实包 + 放置额外文件后 `create_session` **SUCCEEDED** 且 `board_profile_sha256` == `sha256:a7cb97a56930d1a7903ee64e026db2f4a8a5d56e4443566e2274cb1fc8c7bc18`（今天事故的直接回归） |

### 解除阻断（原本因封条失败，现恢复通过）

| 测试 | 说明 |
|---|---|
| `test_load_real_profile_allow_draft` | 真实包（含 ADC 资产）加载成功 |
| `test_locked_loads_without_allow_draft` | 真实包（含 ADC 资产）锁定加载成功 |
| `test_create_returns_real_session_id` | 真实包 create_session 成功 |
| `test_e005_create_session_includes_profile_sha` 等 E005 | 真实包 E005 证据成功 |
| `test_tool_count_matches_capabilities`（MCP SDK） | 真实 MCP create_session 成功 |

## 4. 回归数字（对照基线）

- 基线（CLAUDE.md）：`1426 collected / 1385 passed / 1 skipped / 40 deselected / 0 failed`
- 本轮：`1427 collected / 1386 passed / 1 skipped / 40 deselected / 0 failed`
  （37 重映射 + 1 新增回归；collected +1、passed +1，无失败、无净减）
- 机械核对：`1386 passed + 1 skipped + 40 deselected = 1427 collected` ✓

## 5. 审计脚本

- 路径：`tools/audit/b03_package_audit.py`
- 用法：`python tools/audit/b03_package_audit.py [package_dir]`（缺省 `boards/ALINX_AX7020_v1.0/`）
- 输出：完整包校验（目录 vs manifest + SHA 交叉引用 + 语义）的漂移/多余/缺失清单。
- 退出码：`0` 干净、`1` 有 issue、`2` 用法/IO 错误。
- 实测（真实包）：报 `EXTRA_FILE_IN_DIR` × 2（`adc/`、`adc_ad7606c_pinmap.json`），退出码 1 ——
  证明运行时已容忍的漂移仍可被开发期审计捕获。

## 6. 风险说明（密封退役后）

- 运行时不再拒绝「目录 ≠ manifest」与 SHA 漂移；漂移靠**文档级冻结基线**（B03 completion report 的
  SHA 表）+ **指纹记录审计**（`board_profile_sha256` 进 session/Ledger）+ 开发期审计脚本兜底。
- `verify_consistency`（对账）与 `freeze_package`（冻结）仍全量校验，故「产物间一致性」与「发布
  时完整性」不受影响。
- 用户对板卡信息负责：若板卡事实自相矛盾（如 DDR 超限），框架不再在 `create_session` 拦截，
  而是在实际使用点（工具读 `ps7_preset`/`board.xdc`/参数时）或 `verify_consistency` 阶段暴露。

## 7. 禁区零触碰声明

- `boards/`（3 个 ADC 资产保留原位，未改）、`skills/`、架构文档、README、CLAUDE.md、
  `legacy/`（Xilinx_Vivado_MCP 等）、`validation_projects/` —— **均未改动**。
- `.mcp.json` 未改（SHA256 保持 `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`）。
- `verify_consistency` / 三 Manifest 链（`consistency_check.py`）零改动。
