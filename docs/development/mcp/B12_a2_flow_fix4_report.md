# B12-A2 开发流程修复轮 #4（Agent1 白盒）报告

> 日期：2026-08-26（`Get-Date` 实测；UTC+8）｜角色：Agent1（白盒实现）
> 范围：黑盒前最后一轮，只修会影响黑盒的两个 P2：①`pl_generate_bitstream` 输出目标=impl_1 运行目录时 file copy 自复制失败；②`pl_reset_run` 把非法 `-force` 转发到 `reset_run`。
> 纪律：改动前建 `.bak`，收尾已删除（全仓 `.bak` = 0）。只跑非硬件回归。未执行任何 git 写操作。未修改 CLAUDE.md、docs 冻结文档、boards/、legacy 目录、workspaces/、.mcp.json。未运行任何 EDA/host_live/device_live。

---

## 0. 修复总览

| # | 级别 | 生产入口 | 修复性质 | 证据等级 |
|---|---|---|---|---|
| 1 | P2 | `domains/pl/pl_bridge_tools.py`（`pl_generate_bitstream`） | 自复制修复：Tcl 用 `file normalize` 判源==目标，相同则跳过拷贝（不报错）、仅校验文件存在；不同则正常拷贝。fail-closed。 | `IMPLEMENTED_AND_TESTED`（mock，+2 测：源==目标 / 源≠目标） |
| 2 | P2 | `domains/pl/pl_bridge_tools.py`（`pl_reset_run`） | `reset_run` 无 `-force` 选项（UG835 Tcl Command Reference 仅 `reset_run <run>`）；转发 `-force` 报 "Unknown option '-force'"。已去掉非法转发；`force` 参数保留但绝不转发（文档化）。 | `IMPLEMENTED_AND_TESTED`（mock，force test 改为断 `-force` 不存在） |

---

## 1. 项 #1（P2）：`pl_generate_bitstream` 输出目标=impl_1 运行目录时 file copy 自复制失败

### 根因
`pl_generate_bitstream` 走 `run_vivado_run`（观察者路径）后，用 `copy_tcl` 执行
`file copy $__o3_bit {output_path}`（`$__o3_bit` = impl_1 运行目录里 Vivado 写出的
`<top>.bit`）。当调用方把 `path` 设为**该运行目录下的同一个 bit 文件**（源==目标）时，
Windows 无法把一个文件复制到它自身 → `file copy X X` 失败 → `__O3_BIT_COPY_FAILED`
→ 上层判断 BIT_DONE 缺失/文件缺失 → 报 `BITSTREAM_NOT_FOUND`。白盒实测：输出到
`project_h/bitstream/` 独立路径可绕；输出=impl_1 运行目录则失败。

### 修复（`domains/pl/pl_bridge_tools.py`）
`copy_tcl` 在拷贝前用 `file normalize` 比较源与目标：
```
if {[string equal [file normalize $__o3_bit] [file normalize {<output>}]]} {
  puts BIT_DONE          # 源 == 目标：已发布，跳过拷贝
} elseif {[catch {file copy ...} __o3_copy_err]} {
  puts __O3_BIT_COPY_FAILED
} else {
  puts BIT_DONE
}
```
- `$__o3_bit` 存在性校验仍保留在前（`if {![file exists $__o3_bit]} {error "BITSTREAM_NOT_FOUND"}`）——fail-closed：目标不存在仍报错。
- 源==目标：跳过拷贝，仅确认源文件存在后报 BIT_DONE（源本就是发布路径）。
- 源≠目标：正常拷贝（原逻辑不变）。

### 风险
低。仅在同路径时跳过拷贝；不同路径行为完全不变。错误消息/出错码未改。mock 测试已覆盖两路径。

### 测试（`test_pl_bridge.py`，+2）
- `test_bitstream_observer_skips_copy_when_source_equals_target` — `_ObserverBridge` 返回 BIT_DONE，目标文件在盘上 → `pl_generate_bitstream` 成功；断言 copy_tcl 含 `file normalize` 与 `[file normalize $__o3_bit]` 判等分支。
- `test_bitstream_observer_copies_distinct_source` — 目标文件不在盘上（源≠目标、mock 不执行真拷贝）→ fail-closed `BITSTREAM_NOT_FOUND`；断言 copy_tcl 仍含 `file normalize` 守卫（无回归）。

---

## 2. 项 #2（P2）：`pl_reset_run` 的 `force` 转发 bug

### 根因
`pl_reset_run` 原实现在 `force=True` 时把 `-force` 拼进 `reset_run` Tcl：
`reset_run -force <run>`。但 Vivado `reset_run` **没有 `-force` 选项**（UG835 Tcl
Command Reference：`reset_run <run>`），因此 Vivado 报 `Unknown option '-force'`
且 reset 根本未执行。（web 检索的 UG835 参考印证：`reset_run` 无 `-force`。）

### 修复（`domains/pl/pl_bridge_tools.py`）
- 去掉 `-force` 转发：Tcl 改为纯 `reset_run {<name>}`（带存在性守卫 + catch + RESET_STATUS 报告）。
- `force` 参数仍接受（schema 调用方可能传），但**绝不转发**到 Tcl 行（文档化：reset_run 无条件合法 reset；force 为兼容参数，no-op-if-true）。
- 保留既有守卫：`run_name` 必须 ∈ {synth_1, impl_1}，否则 INVALID_ARGUMENT；bridge 错误 / 非 Complete reset 输出 → 报错（fail-closed）。

### 风险
低。仅移除一个非法选项；reset 行为不变（reset_run 本身即重置）；`force=True` 与 `force=False` 行为一致（都发合法 `reset_run`，不再有差异）。

### 测试（`test_pl_bridge.py`，更新 1 个、其余保持）
- `test_reset_run_force_flag`（等价更新）：断言 `-force` **不出现**在 Tcl 中、`reset_run {impl_1}` 存在（原来断言 `reset_run -force impl_1`，即 bug 本身）。
- `test_reset_run_success`（等价更新）：断言 `reset_run {synth_1}`（改为 braced 形式）。
- 其余 reset_run 测试（reject_unknown / fail_closed / missing_run）不变，仍通过。

---

## 3. 回归机械统计（前后对照，数字来自命令输出）

```bash
python -m pytest mcps -m "not host_live and not device_live"   （仓库根）
```
| 指标 | 基线（fix #3 后） | 修复后 | 变化 |
|---|---|---|---|
| collected | 1467 | **1469** | +2（新增测试） |
| passed | 1425 | **1427** | +2 |
| skipped | 1 | 1 | 0 |
| deselected | 41 | 41 | 0 |
| failed | 0 | **0** | 0 |

- 修复后 passed（1427）≥ 基线（1425），failed = 0，无测试净减。
- collected 基线（1467）为 fix #3 后 `--collect-only` 输出；修复后 **1469 tests collected**。

### 新增/修改测试映射

新增 2 个测试函数（非硬件）：

| 文件 | 函数 | 归属项 |
|---|---|---|
| `test_pl_bridge.py` | `test_bitstream_observer_skips_copy_when_source_equals_target` | #1 |
| `test_pl_bridge.py` | `test_bitstream_observer_copies_distinct_source` | #1 |

等价修订（语义一致，适配修复）：
- `test_pl_bridge.py::test_reset_run_success` — `reset_run synth_1`→`reset_run {synth_1}`
- `test_pl_bridge.py::test_reset_run_force_flag` — 断言 `-force` 不存在（原断言`reset_run -force impl_1` 即 bug）

删除/重命名测试：**无**。

---

## 4. 修改文件清单

生产代码（1）：
- `mcps/zynq_mcp/domains/pl/pl_bridge_tools.py` — `pl_generate_bitstream` 自复制守卫 + `pl_reset_run` 去非法 `-force`

测试（1）：
- `mcps/zynq_mcp/tests/test_pl_bridge.py`

（曾为两个文件建 `.bak`，收尾删除；全仓 `.bak` = 0。）

---

## 5. 未改动/未实现声明

- **未修改**：CLAUDE.md、docs 冻结文档、boards/、legacy 目录、workspaces/、.mcp.json。
- **未执行**：任何 git 写操作；任何 EDA/host_live/device_live 工具。
- **未自行冻结 Brick、未越级进入下一步骤；未调用 Agent2。**

## 6. 需主代理注意（如实）

1. 两项均为 `MOCK_ONLY`（mock 测试；未跑真 Vivado host_live，属本轮非硬件纪律）。真实工具门禁需另行一轮 host_live。
2. `pl_generate_bitstream` 的自复制守卫基于 `file normalize` 判等（Tcl 层）。若 Vivado 对同路径返回非规范化字符串（极少见），守卫可能未命中而回退到拷贝路径——fail-closed 仍会报 BITSTREAM_NOT_FOUND 而非静默成功，可接受；真实主机测试可进一步验证。
3. `reset_run` 的 `force` 参数现为兼容 no-op；若未来确需"无强制重置"语义，应改为触发 Vivado 支持的选项（本轮无，保留纯 `reset_run`）。
