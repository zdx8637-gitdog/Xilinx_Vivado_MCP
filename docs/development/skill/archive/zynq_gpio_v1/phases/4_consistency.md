# Phase 4 — Consistency Check

> 输入: Platform Manifest + 所有产物文件 | 输出: 校验通过/失败报告

## Skill 决策

- 部署前**必须**通过一致性校验。任何一个 check 失败 = 拒绝部署
- Phase 4 是纯 query——不产生新文件，不推进 stage

## 执行序列

### 4a. 收集 Manifest 路径

**主动检查 manifest 目录，不要假设它们不存在：**

| Manifest | 路径 glob | 何时存在 |
|----------|----------|---------|
| Platform | `manifests/platform/sha256_*.json` | Phase 1 完成后 |
| PL Build | `manifests/pl/sha256_*.json` | Phase 2 完成后 |
| PS Build | `manifests/ps/sha256_*.json` | `ps_compile` 成功终态自动发布 |

**规则**：列出每个目录下的文件，找到的传全部，找不到的记为不存在。不要只照抄示例。

### 4b. 执行校验

| 步骤 | MCP Tool | 参数 | 验证 |
|------|----------|------|------|
| 1 | `verify_consistency` | `{"platform_manifest_path": "<P1 manifest>", "pl_build_manifest_path": "<P2 manifest 或 ''>", "ps_build_manifest_path": "<P3 manifest 或 ''>", "board_profile_sha256": "sha256:a7cb97..."}` | `errors` 为空 |

**完整参数说明**：

```json
{
  "platform_manifest_path": "manifests/platform/sha256_xxx.json",
  "pl_build_manifest_path":  "manifests/pl/sha256_xxx.json",
  "ps_build_manifest_path":  "manifests/ps/sha256_xxx.json",
  "board_profile_sha256":    "sha256:a7cb97..."
}
```

GPIO v1 正式契约要求三类 Manifest 全部存在。任何目录 0 个或多个当前候选都先
停止并报告；不得用空字符串、省略参数或手工新建 Manifest 来得到部分校验。

## 7 条校验规则

| # | 规则 | 说明 |
|---|------|------|
| 1 | `pl_build.built_from_platform_revision == platform.platform_revision` | PL 产物必须与 Platform 版本一致 |
| 2 | `ps_build.built_from_platform_revision == platform.platform_revision` | PS 产物必须与 Platform 版本一致 |
| 3 | `ps_build.platform_xsa_sha256 == platform.xsa_sha256` | PS 用的 XSA 必须是 Platform 产出的那个 |
| 4 | `ps_build.xparameters_addrs == platform.address_map` | 地址映射逐字段一致 |
| 5 | `ps_build.board_profile_sha256 == board_profile_sha256` | PS 板卡配置一致 |
| 6 | `pl_build.board_profile_sha256 == board_profile_sha256` | PL 板卡配置一致 |
| 7 | 所有 artifact 文件存在 + SHA256 匹配 | 文件完整性和完整性 |

## 产物

- 校验报告（从 tool 返回）。只有 `all_passed == true`、`errors == []`、
  `summary.failed == 0` 且 `summary.skipped == 0` 才能继续。

## 失败恢复

| 症状 | 动作 |
|------|------|
| `failed` 非空 | 列出所有失败规则。最常见：Phase 1 的产物文件被移动或修改了。重新检查文件路径 |
| manifest 不存在 | 对应 Phase 未满足终态门禁；从该 Phase 的公开 MCP 操作重跑，禁止手工发布 |
| board_profile 不匹配 | 确认 board_id 为 `ALINX_AX7020_v1.0`，profile 文件未被篡改 |

## 最终判定

```
all_passed=true 且 failed=0 且 skipped=0 → 继续 Phase 5
其他任何结果                              → 拒绝部署并保留完整校验报告
```
