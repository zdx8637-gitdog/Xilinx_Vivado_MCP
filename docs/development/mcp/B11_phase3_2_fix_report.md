# B11 阶段③.2 小整改轮报告（D10 / D11 / PS-LED 根因与需求变更）

> 日期：2026-08-15（`Get-Date` 实测）
> 状态：本轮为阶段③后续小整改轮（用户实板观察驱动）；修复落点为代码 / 文档 /
> Skill / workspace 固件设计稿。阶段④/⑥ 全新会话黑盒重做由后续轮次执行。
> 基线：`1411 collected / 1371 passed / 1 skipped / 39 deselected / 0 failed`
> （commit `2d258ef` 记录，与 CLAUDE.md 一致）。

## 0. 触发背景

阶段③真板重跑 UART 判 PASS（`docs/development/tests/B11_phase3_rerun_report.md`），
但用户实板观察发现两处问题：

1. **PS 两个 LED（MIO0/MIO13，active-low）物理不亮**——UART 16 轮读回自洽通过，
   怀疑读回的是写镜像而非引脚真实状态，或 PS 输出方向/输出使能未配置；
2. **LED 效果跑完 8 轮即停**——用户希望 PASS 打印后继续 1s 亮暗 while 循环。

另有已记录债务 D10（P1，defines 未传 ps_compile）与 D11（P2，verify_consistency
Manifest 路径语义）。

---

## 1. D10（P1）：ps_set_compiler_options 的 defines 不传 ps_compile

### 现象
- 设 `defines=FAULT_INJECT` 后编译，`led6_app/Debug/makefile` 等构建产物 grep
  `FAULT_INJECT` 0 命中；上板输出全部 READ==WROTE（宏未生效）。
- 证据：`B11_phase3_rerun_report.md` §9 表 D10 行。

### 根因（带证据行号）
- `domains/ps/ps_bsp.py`：`_WS_DEFINES` 模块级字典**只写不读**——grep 仅 3 处：
  声明（L83）、写入（L643）、删除（L645）；`compile_app`（L650 起）只调用
  `templates.app_build(name)`，从不读取 `_WS_DEFINES`。
- `adapters/xsct/templates.py`：`app_build_defines(name, defines)`（L234–236 旧版，
  产出 `app build -name <n> -defines {<defines>}`）存在但从未被调用。
- **机制勘误（host_live 实测发现）**：`app build -defines` 在本 XSCT 版本被拒绝
  ——真机错误 `bad option '-defines': -name -all -help`（`app build` 仅支持
  `-name/-all/-help`）。Vitis 2023.1 XSCT 的正确 API 是 `app config -name <app>
  -add define-compiler-symbols <sym>`（每符号一条，追加 `-D<sym>` 到编译选项；
  来源：Vitis 2023.1 安装 `scripts/xsct/xsdb/sdk.tcl` `app config` 命令参考
  L1234–1319，示例即 `app config -name test define-compiler-symbols FSBL_DEBUG_INFO`）。

### 修复位置
- `mcps/zynq_mcp/adapters/xsct/templates.py`：移除错误的 `app_build_defines`，
  新增 `app_config_define_symbol(app, symbol)` → `app config -name <app>
  -add define-compiler-symbols {<sym>}`（花括号保证单 Tcl 词）。
- `mcps/zynq_mcp/domains/ps/ps_bsp.py`（`compile_app`，Step 1 前置）：读取
  `_WS_DEFINES.get(ws, "")`，非空时**按空白拆分为符号**，逐符号执行一次
  `app config -add define-compiler-symbols`（任一失败 → BUILD_FAILED，
  fail-closed，不继续构建），随后照常 `app build -name <n>`。
- make 回退路径（Step 3 注释）：`app config` 写入 app 构建配置（持久化于 app
  设置），XSCT 重生成的 Debug makefile 含 `-D` 符号，回退 `exec make` 同宏构建。
- 行为不变（fail-closed 不变）：无 defines 时 Tcl 序列与基线完全一致。

### 测试
| 测试 | 文件 | 说明 |
|---|---|---|
| `test_compile_app_passes_defines_to_app_build` | `tests/test_ps_bsp_domain.py`（新增） | 组件：`set_compiler_options(defines=…)` → `compile_app` 先发两条 `app config -name myapp -add define-compiler-symbols {…}` 再发 `app build -name myapp`（Tcl 序列断言） |
| `test_compile_app_defines_are_workspace_scoped` | 同上（新增） | 组件：A workspace 设 defines 不影响 B workspace 的 plain build（按 ws 键隔离） |
| `test_compile_app_define_config_failure_is_fail_closed` | 同上（新增） | 组件：define 配置失败 → BUILD_FAILED 且不再执行 app build |
| `test_compile_app_skips_make_when_elf_exists` 等既有 4 个 | 同上（保留） | 无 defines 路径 Tcl 不变（`app build -name myapp`） |
| `test_ps_compile_defines_reach_compiler_real` | `tests/test_b06_ps_bsp_public.py`（新增，`host_live`） | 真 XSCT：`#ifdef B11_PROBE_DEFINE` 双分支探针字符串，编译后 ELF 含 `B11_DEFINE_ACTIVE` 且不含 `B11_DEFINE_INACTIVE`（宏真实生效）；首跑即以真实错误证伪 `app build -defines`，机制修正后通过 |

### 状态
**DONE**（组件测试全绿；host_live 测试见 §5 运行结果）。

---

## 2. D11（P2）：verify_consistency Manifest 路径

### 现象
- 按 Skill 模板相对路径调用 `verify_consistency` → 12 条规则全 skipped + 3 条
  NOT FOUND（`_load_manifest` 用 `os.path.isfile(path)` 对进程 CWD 解析相对路径）。
- 证据：`B11_phase3_rerun_report.md` §6 用法备注。

### 根因（带证据行号）
- `domains/verification/consistency_check.py`：`_load_manifest`（L72–107 旧版）直接
  `os.path.isfile(path)`；`verify_consistency`（L270–273 旧版）不向 `_load_manifest`
  传 `resolve_root`（该参数只用于规则 7 产物路径解析，见 `_check_artifact_files`
  L219–221）。

### 修复位置
- `mcps/zynq_mcp/domains/verification/consistency_check.py`：
  - `_load_manifest` 增加 `resolve_root` 参数：相对路径 + 有 `resolve_root` → 按
    `os.path.join(resolve_root, path)` 解析；相对路径 + 无 `resolve_root` →
    **显式 `INVALID_ARGUMENT` 错误**（消息含该词）而非静默 NOT FOUND；
  - `verify_consistency` 向三条 Manifest 的加载调用传 `resolve_root`。
- 触发条件为「任一提供的 Manifest 路径相对且未给 resolve_root」即报错（比用户
  字面「三条全相对」更严格，fail-closed 更彻底；三条全相对场景必然命中）。
- `skills/zynq_dev/appendix_mechanics.md` §2.2 补路径规则句（绝对路径 或 相对路径
  + `resolve_root`；两者皆缺 → INVALID_ARGUMENT，绝不静默）。

### 测试
| 测试 | 文件 | 说明 |
|---|---|---|
| `test_relative_manifest_paths_with_resolve_root_pass` | `tests/test_consistency_check.py`（新增） | 相对路径 + resolve_root → 12/12 通过、0 skipped |
| `test_relative_manifest_paths_without_resolve_root_error` | 同上（新增） | 相对路径 + 无 resolve_root → 三条 INVALID_ARGUMENT 错误、全 skipped、all_passed=false |
| 既有 19 个测试 | 同上（保留） | 绝对路径行为不变 |

### 状态
**DONE**。

---

## 3. PS LED 不亮：根因调查（带证据）与修复

### 3.1 现场只读证据

**A. 上一轮实际烧写固件**（`workspaces/b11_p3_agent1_rerun_20260814/project_r3/
src/main.c` 与 `led6_app/src/main.c`，两份相同，L34–38 寄存器常量、L90–94 初始化、
L107–112 写读）：

```c
#define MIO_GPIO_BASE  0xE000A000
#define MIO_DATA_OFF   0x000      /* ← 问题①：写/读的是 0x000（DATA_LSW 掩码写、WO） */
#define MIO_DIRM_OFF   0x204
#define MIO_OEN_OFF    0x208
...
Xil_Out32(MIO_GPIO_BASE + MIO_DIRM_OFF, ... | MIO_MASK);   /* 方向 1=输出：正确 */
Xil_Out32(MIO_GPIO_BASE + MIO_OEN_OFF,  ... & ~MIO_MASK);  /* ← 问题②：清 OUTEN 位 */
...
Xil_Out32(MIO_GPIO_BASE + MIO_DATA_OFF, ps_wr);            /* 写 0x000 掩码寄存器 */
ps_rd = Xil_In32(MIO_GPIO_BASE + MIO_DATA_OFF);            /* ← 问题③：读回 0x000 写镜像 */
```

**B. BSP 驱动源码**（workspace BSP `gpiops_v3_11`，即板卡实际使用的驱动版本，
对软件可见寄存器语义具有权威性）：

- `xgpiops_hw.h` L50–55 寄存器偏移：
  `XGPIOPS_DATA_LSW_OFFSET=0x000`（Mask and Data Register **LSW, WO**）、
  `XGPIOPS_DATA_OFFSET=0x040`（Data Register, **RW**）、
  `XGPIOPS_DATA_RO_OFFSET=0x060`（Data Register **- Input, RO**）、
  `XGPIOPS_DIRM_OFFSET=0x204`、`XGPIOPS_OUTEN_OFFSET=0x208`。
- `xgpiops.c` `XGpioPs_ReadPin`（L233–252）读的是 `DATA_RO`（L248–250）——
  **引脚真实状态读回唯一正确来源**；`XGpioPs_Write`（L269–285）写的是 `DATA`
  （0x040，L282–284）；`XGpioPs_SetOutputEnablePin`（L547–578）：`OpEnable=1` →
  `OUTEN |= 位`（L569–570）= **输出使能**，`OpEnable=0` → 清位 = **禁用输出**
  （L571–572）；`XGpioPs_SetDirectionPin`（L393–423）：`Direction=1` → `DIRM |= 位`
  （L414–415）= 输出方向。
- 结论（对照 r3 固件）：
  1. **OUTEN 极性写反（致命）**：r3 固件 `& ~MIO_MASK` 清 OUTEN 位 = **输出驱动
     禁用**（高阻）。板卡 MIO0/MIO13 上拉使能（ps7_preset.tcl `PULLUP enabled`），
     引脚被拉高 → active-low PS LED 恒灭。**此即「物理不亮」主因**。
  2. **读回寄存器选错（自洽假象）**：r3 固件读 0x000（DATA_LSW，写侧/掩码寄存器）
     = 读写镜像，恒等于刚写入值 → 16 轮 UART 自洽 PASS 与引脚真实状态无关。
     真实状态必须读 `DATA_RO`（**0x060 = 0xE000A060**）。
  3. **数据写入寄存器选错**：r3 固件写 0x000（DATA_LSW 掩码写）而非 `DATA`
     （0x040, RW）——掩码位全 0 时数据不更新（或按掩码语义全更新），输出数据
     不可靠。正确写法：写 `DATA`（0x040, RW），与驱动 `XGpioPs_Write` 一致。
- **寄存器地址与用户提示的差异说明**：用户提示「DATA_RO_0=0xE000A040」——
  按本板实际驱动（`xgpiops_hw.h`）与 Zynq-7000 TRM（UG585，GPIO 寄存器图），
  `0xE000A040` 是 **DATA（RW）**，`0xE000A060` 才是 **DATA_RO（RO 引脚状态）**；
  以驱动源码为准（本报告引用处即来源），差异已在参考固件注释中说明。
- 不确定性与来源：以上偏移来自本 workspace BSP `gpiops_v3_11` 头文件/驱动源码
  （软件可见接口的第一手来源）；与 Zynq-7000 TRM UG585 GPIO 章节一致。
  （补充来源：[Zynq-7000 GPIO 寄存器解析](https://blog.csdn.net/weixin_42593701/article/details/157877787)、
  [EmbeddedSW gpiops 文档](https://xilinx.github.io/embeddedsw.github.io/gpiops/doc/html/api/xgpiops__hw_8h.html)，
  均为二手佐证，结论以本机驱动源码为准。）

**C. 板卡 MIO 复用（ps7_preset.tcl，排除 mux 因素）**：

- `PCW_EN_GPIO {1}`（L135）、`PCW_GPIO_MIO_GPIO_ENABLE {1}`（L180）；
- `PCW_MIO_TREE_PERIPHERALS`（L345–346）首项 `GPIO`、`PCW_MIO_TREE_SIGNALS`
  （L348）`gpio[0]#…`（MIO0）与 `…gpio[13]…`（MIO13）→ **MIO0/MIO13 已复用为
  GPIO**，非外设复用问题。
- 部署用 `ps7_init.tcl`（project_r3/ax7020_platform/hw）写入 MIO 复用寄存器
  `0xF8000700`/`0xF800070C`（L144/147），与 preset 一致。

**D. 现场 JTAG 实测（2026-08-15，只读）**：真板经公开 MCP 连接成功（hw_server
127.0.0.1:3121，XSDB CONNECTED，ARM#0 状态 Running——r3 固件仍在运行）。但
`ps_mem_read` 对 `0xE000A204/0xE000A208/0xE000A040/0xE000A060/0xE000A000/
0xF8000700/0xF800070C`（含停核后重试与 DDR `0x100000` 对照）均返回
`words: []`（SUCCESS 但无可解析字）——公开工具 mrd 输出解析与真实 XSDB 2023.1
输出格式存在 gap（**新增观察项，见 §4**），故**未能取得寄存器现场快照**；
根因结论以 B（驱动源码）为准，证据等级 STATIC_REVIEW（驱动源码+预设+init 文件），
未作任何板卡写入/状态修改（halt 后已 resume、disconnect）。

### 3.2 修复

1. **固件设计稿**（workspace 新参考版，禁改清单允许的固件设计稿修正）：
   `workspaces/b11_p3_agent1_rerun_20260814/project_r3/src/main_r3p2_fixed.c`
   （SHA256 见 §6；原 r3 固件文件保持原样作证据）：
   - `OUTEN`：`|= MIO_MASK`（位 1 = 输出使能）；
   - 数据写 `DATA`（0x040, RW）；读回 `DATA_RO`（0x060, RO，引脚真实状态）；
   - 行为（用户明确要求）：每轮仍输出 `WROTE:0x%X READ:0x%X`；8 轮全对后打印一次
     `LED_E2E_PASS` 并**继续无限 1s 交替**（直到复位/断电）；任一不一致打印
     `LED_E2E_FAIL` 并停止。
2. **Skill 通用知识增强**（`skills/zynq_dev/appendix_mechanics.md` §5.1，零外设
   字样）：PS 端并行输出引脚驱动要点——方向寄存器与输出使能寄存器都必须显式
   配置（使能位 1=使能，与 PL 三态语义相反，按驱动源码核对极性）；数据写入用
   数据寄存器；读回必须读引脚状态读回寄存器（DATA_RO 类），禁止读写镜像；
   极性由需求文档/板卡物理事实定义；配置顺序方向→使能→数据→读回。
3. **需求文档同步**（`docs/development/tests/B11_blackbox_requirement_draft.md`）：
   - §3.1：行为改为「持续循环不停止：满 8 轮且全部一致后打印一次
     `LED_E2E_PASS`，随后继续无限交替（约 1s/模式）直到复位或断电；任一不一致
     `LED_E2E_FAIL` 并停止」；
   - §3.2：补硬约束「读回必须来自引脚/外设真实状态寄存器（如 PS 端 DATA_RO
     类），不得读写入镜像/写侧寄存器（自洽假象）」；
   - §3.3：PASS 判据行注明「打印一次；程序继续无限交替」；超时建议注明
     「PASS 后程序不退出，观测端捕获到 PASS 即可停止捕获」；
   - §2 板卡物理事实**未变**；
   - §6 补 B09 区别句：「物理确认由用户阶段⑤执行，6 灯含 PS 2 灯都要亮」。

### 3.3 测试
- 本轮固件改动为设计稿/文档级（workspace + 需求文档 + Skill），生产代码零改动
  （PS-LED 相关），因此无新增 pytest；行为验收由阶段④/⑥ 全新会话白盒/黑盒重做
  执行（用户指定）。

### 状态
**DONE**（根因调查完成 + 设计稿/文档/Skill 修正落地）。

---

## 4. 新增观察项（P2，记录不修）

| # | 现象 | 证据 | 建议 |
|---|---|---|---|
| N1 | `ps_mem_read` 对真实 XSDB 2023.1（板卡在线、CPU 运行或停核）返回 SUCCESS 但 `words: []`——mrd 输出格式与 `_parse_mrd_words`（`memory_access.py` L311–323）期望的 `地址: 值` 行不匹配；`0x100000` DDR 对照同样为空 | 本会话 3 轮实测（probe v1/v2/v3，均经公开 MCP） | 下一轮补 host_live mrd 解析测试或改用 `ps_reg_read` 类路径；不影响本轮根因结论（驱动源码已定论） |

---

## 5. 回归与门禁

### 5.1 测试统计（机械实测，项目根目录）

- 基线：`1411 collected / 1371 passed / 1 skipped / 39 deselected / 0 failed`。
- 本轮新增测试 **6** 个（`test_ps_bsp_domain.py` +3、`test_consistency_check.py` +2、
  `test_b06_ps_bsp_public.py` +1 host_live）；**0 删除、0 替换**。
- 收集：`1417 collected`（`--collect-only -q`）。
- 完整非硬件回归（`python -m pytest mcps -m "not host_live and not device_live" -q`）：
  **见 §5.2 实测数字**（1377 运行 = 1417 − 40 deselected；40 = 36 host_live + 4
  device_live）。
- host_live 专项（真实 XSCT，D10 宏生效验证）：**见 §5.3 运行结果**。

### 5.2 完整非硬件回归结果（机械实测）

```
python -m pytest mcps -m "not host_live and not device_live" -q
1417 collected / 1376 passed / 1 skipped / 40 deselected / 0 failed   (205.86s)
（1377 运行 = 1417 − 40 deselected；40 = 36 host_live + 4 device_live）
```

- 对照基线：collected 1411 → **1417**（+6 新测试）；passed 1371 → **1376**；
  skipped 1 → 1；deselected 39 → 40（+1 host_live）；failed 0 → **0**。无下降。
- 首轮回归曾现 1 个失败：`test_project_lock.py::test_heartbeat`（时间戳单调
  断言，`_now_utc` 的 `strftime` 与 `time.time()` 分开取时，机器高负载下时钟
  刻度变粗可返回相同时间戳）——与本次改动无关（project_lock 零改动），
  孤立重跑 6/6 通过，最终干净重跑 0 失败。属**既有偶发 flaky**，记录不修。

### 5.3 host_live 专项结果（真实 XSCT，D10）

```
python -m pytest mcps/zynq_mcp/tests/test_b06_ps_bsp_public.py::TestBspRealXsct::test_ps_compile_defines_reach_compiler_real -q -m host_live
1 passed in 44.78s
```

- 首跑（`app build -defines` 机制）FAILED：真机错误 `bad option '-defines':
  -name -all -help` → 证伪旧机制并改为 `app config -add define-compiler-symbols`
  （见 §1）；修正后通过。
- 断言：真 XSCT 全流程（import → platform → bsp → app → add 源码 →
  `ps_set_compiler_options(defines=B11_PROBE_DEFINE)` → `ps_compile`）后，ELF
  字节含 `B11_DEFINE_ACTIVE`（`#ifdef` 使能分支）且不含 `B11_DEFINE_INACTIVE`
  （禁用分支）——宏真实生效，D10 闭环。

### 5.4 机械门禁

- 新/改测试无空 pass、无 `except Exception: pass`、无裸 `except: pass`（扫描 0 命中）。
- **Skill 零字样门禁**（`skills/zynq_dev/` 全 11 文件机械扫描）：
  `gpio`（大小写不敏感）/ `0x41200000` / `LED`（整词）/ `breath|blink` **0 命中**。
  重点自查：新增 §5.1 与 D11 句未出现任何 gpio 大小写变体（含 `XGpioPs` 类词，
  改用「PS 输出引脚控制模块/方向寄存器/输出使能/引脚状态读回寄存器/DATA_RO」）。
- `.mcp.json` SHA256 = `d8e397af03b5b032f21d0aa967086f0c78b33c87b76f2e9898ae0a144df7de02`
  （不变，与 O1–O6 冻结记录一致）。

---

## 6. 变更文件清单与 SHA256

### 生产代码（3）
| 文件 | SHA256 |
|---|---|
| `mcps/zynq_mcp/domains/ps/ps_bsp.py` | `576ca6cdbacb30c607aac2953b60f7c04a87adafeef8538e1285b641b10bc5db` |
| `mcps/zynq_mcp/adapters/xsct/templates.py` | `0568eb79be30541444afa1f36de79ff7c3e4ff4296755ae490d4a883140bdb09` |
| `mcps/zynq_mcp/domains/verification/consistency_check.py` | `8bc59fad4e5790d0242099bc7fa464d0801610d9f69b97e6f9040d49d9cd64ec` |

### 测试（3）
| 文件 | SHA256 |
|---|---|
| `mcps/zynq_mcp/tests/test_ps_bsp_domain.py` | `74e02a99ec2566ec88f56f2f779eec89c6deb08084a8a31a30c653b08d343f68` |
| `mcps/zynq_mcp/tests/test_consistency_check.py` | `501c24812a7736aa98df2d19983370b28e060225a5dffe30aa94de51fac2e51a` |
| `mcps/zynq_mcp/tests/test_b06_ps_bsp_public.py` | `2826261bb80aa7ea4c34727c45aa391b824f10916cc8bab2b3fe9d298ca1da03` |

### Skill / 文档（4）
| 文件 | SHA256 |
|---|---|
| `skills/zynq_dev/appendix_mechanics.md` | `e8ef4adcb4db1daf1a4151bc0033de72d69b7c371403c4f052938a79d0f15613` |
| `docs/development/tests/B11_blackbox_requirement_draft.md` | `39e65e01e09cc4dfb39b8506b3a649ecd4459dea6141294f5f82f91d076a25a2` |
| `docs/development/mcp/B11_plan.md`（追加「阶段③.2 记录」） | `28f381af415b51ec4d08ce280bbb852d6c6470b3d4fc3266cb00218b0df4ac8e` |
| `docs/development/mcp/B11_phase3_2_fix_report.md`（本文档，新建，自引用） | 见 Git 段注（提交后校验值见交付汇报） |

### workspace 固件设计稿（证据，不入库）
| 文件 | SHA256 |
|---|---|
| `workspaces/b11_p3_agent1_rerun_20260814/project_r3/src/main_r3p2_fixed.c` | `5789888d9f5bea1a85fd29da2dca2ef79da787315ad4e5732de2c4ce7c43f0a8` |

### Git
- 提交信息：`B11 phase 3.2: D10 defines pass-through fix, D11 consistency path guidance, PS output-pin root-cause fix (physical-state readback, continuous loop requirement)`
- 提交 hash 与推送结果：**见交付汇报**（先例：`B11_remediation_round_report.md` 同此处理，报告内不记录自身提交 hash）。

> 注：本文档为自引用文件，自身 SHA256 无法写入自身内容（先例同此处理：`B11_remediation_round_report.md` §6 记 `NEW → E615B9DF…`）；提交后校验值见交付汇报。

---

## 7. 禁区零触碰声明

- `boards/`、架构文档（`docs/architecture_ai_zynq7020.md`）、`docs/brick_development_plan.md`、
  README、CLAUDE.md、三个 legacy 目录（`Xilinx_Vivado_MCP/`、`Xilinx_Vitis_MCP/`、
  `zynq_platforms/`）、`validation_projects/`：**零改动**。
- `workspaces/`：仅新增固件设计稿 `project_r3/src/main_r3p2_fixed.c`（用户授权的
  固件设计稿修正）；上一轮证据文件（main.c、manifest、uart_*.txt 等）**零改动**。
- 未运行任何写板操作；现场探测为纯读（停核后已恢复运行并断开）。
- 未自行冻结任何 Brick、未越级进入阶段④/⑥。
