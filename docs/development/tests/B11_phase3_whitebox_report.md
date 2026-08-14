# B11 阶段③ Agent1 白盒自测报告（BLOCKED — P1 平台原子能力缺口）

> 日期：2026-08-14（`Get-Date` 实测 2026-08-14 19:56 +08:00）
> 状态：**BLOCKED（P1）** — 6-LED 全流程在 Platform 域即被公开原子能力缺口阻塞，未能到达 PL/PS/部署/观测。本报告如实记录全部证据；未修改任何生产代码 / 测试 / skills / boards / 冻结文档。
> 角色：Agent1（白盒）| 执行面：仅公开 `zynq_mcp`（100 工具，`stdio_client + ClientSession` 驱动）+ 允许的工作区写
> 起点基线（阶段①→② 已冻结数字，本轮不改代码不重跑回归）：`--collect-only`=1376；非硬件回归 1337 passed / 1 skipped / 38 deselected / 0 failed
> 工作区：`workspaces/b11_p3_agent1_20260814/`（runtime 独立于 `.zynq_runtime`；gitignore）
> 配套证据：`workspaces/b11_p3_agent1_20260814/` 下 `mcp_calls.jsonl`（72 次工具调用的完整机读轨迹）、`project/00..04_*.md`（S0–S4 文档）、`project/manifests/`、`project/platform.xsa`、`project/hdl/platform_bd_wrapper.v`、`D:\fpgaproject\vivado.log`（Vivado 原始 Tcl 轨迹）

## 1. 目标与执行概况

目标：仅凭「新泛化 Skill（`skills/zynq_dev/`，零 GPIO 字样）+ 6-LED 需求文档 + 公开 zynq_mcp（100 工具）」在真板上完成 S0–S8 全流程（Platform 原子序列 → PL 构建 → PS 软件 → S6 一致性 → S7 JTAG 部署 + UART → S8 机读判定），并完成故障注入与勘误验证。

执行结果：S0–S4 完成（结构文档齐全）；S5 Platform 原子序列执行 17 个原子操作，**在 `platform_set_address` / `platform_export_hardware` 暴露出三个 P1 级公开能力缺口**，使 BD 无法获得地址映射、PL 引脚无法暴露、XSA 不含 HDF。下游（PL 构建、PS 软件、部署、观测）在公开契约下**不可达** → 按任务铁律「若确实无法继续，输出阻塞报告（P0/P1 级别描述）并停止」。

Operation 统计（全部经 Execution Ledger 真实终态）：25 个 command 操作，19 SUCCEEDED / 6 FAILED（详情见 §4 与 `mcp_calls.jsonl`）。另执行 3 个查询原子与 4 次 `evaluate_observation` 机读判定演示（§5）。

## 2. 环境预检（S1，全部实测）

| 项 | 实测 | 结论 |
|---|---|---|
| Vivado 2023.1 | `D:\Xilinx\Vivado\2023.1\bin\vivado.bat` 存在 | ✅ |
| Vitis/XSCT/XSDB 2023.1 | `D:\Xilinx\Vitis\2023.1\bin\xsct.bat`、`xsdb.bat` 存在 | ✅ |
| hw_server | 既有进程 PID 19880（2026-08-09 08:59:34 启动，`D:\Xilinx\Vitis\2023.1\bin\unwrapped\win64.o\hw_server.exe`），TCP 127.0.0.1:3121 可达 | ✅ 复用既有进程（早于本阶段；不启动不终止；与 B09 记录一致，不在清理范围） |
| USB-UART | COM4 = Silicon Labs CP210x（VID/PID 0x10C4/0xEA60，B09 同端口） | ✅ |
| ZYNQ_BOARD_PROFILE_DIRS | `mcps/common/board_profile.py::_get_search_dirs`：env 目录优先，默认搜索 `_SEARCH_DIRS` = 生产目录 `D:\fpgaproject\boards\ALINX_AX7020_v1.0`。**结论：设为 `D:\fpgaproject\boards`**（与默认等价，显式声明） | ✅ |
| 板包锁定 | `package_manifest.json` status=locked；board_profile_sha256=`sha256:a7cb97a5…c7bc18`（与 create_session 返回一致） | ✅ |

## 3. S3 选型记录（白盒自审）

- **PL 路线：AXI GPIO（`xilinx.com:ip:axi_gpio:2.0`，4-bit，C_ALL_OUTPUTS=1）**，挂 PS7 M_AXI_GP0，地址规划 0x41200000（沿袭 B05/B09 已验证值）。理由：与已移除 B05 快捷路径的 BD 结构一致（勘误 §2/§4 映射），AXI GPIO DATA 寄存器读回即写入值，满足需求 §3.2 读回语义。
- **EMIO 路线否决**：公开 `platform_configure_ps7` 的 config 键集合不含 EMIO GPIO 键（`_PS7_CONFIG_TO_PCW` 仅 m_axi_gp0/1、s_axi_hp0/1、s_axi_acp、irq_f2p、fclk0/1、uart1、ddr），预设 `PCW_EN_EMIO_GPIO=0` → 公开契约下无法使能 EMIO。记为公开能力缺口（D0，P2）。
- **PL LED 引脚连接**：原方案为「自写顶层 `led6_top.v` + Verilog 层次引用读取 BD 内部确定性网络 `platform_bd_i.axi_gpio_led_gpio_io_o`」以绕开缺失的 BD 端口原子（见 D2）。**因 D1/D3 使流程在 PL 域之前即阻塞，该绕行未实施**；实施前提（BD 地址可用 + XSA 可用）不成立。
- PS 软件设计稿已就绪：`project/src/main.c`（XGpioPs 寄存器 API 控制 MIO0/13；AXI GPIO DATA 读回 PL；`WROTE:0x%X READ:0x%X`；8 轮 A/B 交替；`#ifdef FAULT_INJECT` 读回异或 0x1；LED_E2E_PASS/FAIL）——**未编译、未上板**（受阻）。

## 4. S5 Platform 原子序列 — 执行证据与缺口发现

执行环境：MCP 服务器子进程 `python -m mcps.zynq_mcp.server`（cwd=D:\fpgaproject，`ZYNQ_RUNTIME_ROOT`=workspace/runtime，`ZYNQ_BOARD_PROFILE_DIRS`=D:\fpgaproject\boards），经 mcp SDK `stdio_client + ClientSession` 驱动（`mcp_client.py`），全部长任务用 `wait_operation` 等真实终态。

| # | 原子 | operation_id | 终态 | 说明 |
|---|---|---|---|---|
| 1 | platform_create_design | op-ac36a18f577948b988144fa09c904d5a | SUCCEEDED | Vivado 2023.1 后端启动（PID 33156，worker_generation 1） |
| 2 | platform_add_ps7 | op-8e5a77d6817144129fdb7f29e89a3310 | SUCCEEDED | ps7_preset.tcl；UART1/M_AXI_GP0/FCLK0 就绪 |
| 3 | platform_configure_ps7 | op-3ffb80f386f544aa881875b4bb15bcb7 | SUCCEEDED | uart1_enable/io 显式确认 |
| 4 | platform_add_ip（axi_gpio_led） | op-12ec5313cb014aa8a89792f1b3e20e15 | SUCCEEDED | C_GPIO_WIDTH=4,C_ALL_OUTPUTS=1,C_IS_DUAL=0 |
| 5 | platform_add_ip（rst_ps7_50M） | op-c4ae68d4aac84559b0fe6244d1ae257d | SUCCEEDED | proc_sys_reset:5.0 |
| 6 | platform_add_ip（smartconnect_0） | op-23dda998b9ab40ae8bd63d42b3ccf873 | SUCCEEDED | smartconnect:1.0，NUM_SI=1 |
| 7 | platform_connect_interface | op-951de2c768ef49e48029538ba5878e6d | SUCCEEDED | M_AXI_GP0→S00_AXI |
| 8 | platform_connect_interface | op-a5f773183ff8411e8f08f3743dae7b42 | SUCCEEDED | M00_AXI→S_AXI |
| 9 | platform_connect_clock | op-f2c0a04f4d0b4b0fa178c8b84c313206 | SUCCEEDED | FCLK_CLK0→4 目标 |
| 10 | platform_connect_clock | op-cccc733c5516498692cb48e94b3b0e68 | SUCCEEDED | FCLK_RESET0_N→ext_reset_in |
| 11 | platform_connect_reset | op-c6d0d49eaee0437b89047364fb53cf53 | SUCCEEDED | peripheral_aresetn→s_axi_aresetn |
| 12 | platform_connect_reset | op-31f9c2af03f24e5b8060fbfe0411c853 | SUCCEEDED | interconnect_aresetn→aresetn |
| 13a | platform_set_address（`S_AXI`） | op-17ae1ca885554c498e54397f9afbd8b7 | **FAILED** | `[get_bd_addr_segs {axi_gpio_led/S_AXI}]` 空 → **D1/D5** |
| 13b | platform_set_address（`S_AXI/reg0`） | op-378498eb4ce8463282dffcade8359e1f | **FAILED** | 段名仍不匹配（真实名 `S_AXI/Reg`） |
| 13c | platform_set_address（`S_AXI/Reg`） | op-7555aef6f6fe400a842a1ca7f6b2c1ba | SUCCEEDED | **但 Vivado 日志显示 `C_BASEADDR`/`C_HIGHADDR` read-only，地址实际未分配** → **D1** |
| 14a | platform_validate | op-ca3db4e4155a46b8bfa8e1a997bc4837 | **FAILED** | `BD 41-1356` slave segment 未分配进地址空间（真实告警） |
| 14b | platform_validate | op-0eb15f3c99354b0faa5ddcf14834b96d | SUCCEEDED | **假阳性**：`validate_bd_design` 缓存"already validated"掩盖未分配问题 → **D7** |
| 15 | platform_generate_wrapper | op-e0520db4e4b54efea9107f6732e06c3e | SUCCEEDED | `hdl/platform_bd_wrapper.v`（端口仅 DDR/FIXED_IO，**无 LED 端口** → D2） |
| 16 | platform_export_hardware | op-d393ce8a9bef4857bd65b5d2f6dd8d45 | SUCCEEDED | XSA=1500B `pre_synth` 空壳（无 HDF）→ **D3** |
| 17 | platform_export_manifest | op-04e12772c14f42bebf58970db2503b43 | SUCCEEDED | **决策(a)验证通过**：`completion_evidence={stage_advanced_from:PLATFORM_DESIGN, stage_advanced_to:PL_GENERATE}`；`get_execution_state` 确认 current_stage=PL_GENERATE。但 `ip_list=[]`、`address_map={}`、clock_tree 降级 → **D8/D9** |

**Manifest / 产物 SHA256（真实磁盘值）**：

| 产物 | 路径 | SHA256 |
|---|---|---|
| Platform Manifest | `project/manifests/platform/sha256_a7efbefd….json` | 文件 `sha256:52955076f48449de05e8e324cc2c0d0b2a4a0ac6cbecedd69dc9e31b4c0f388f`；platform_revision `sha256:a7efbefdbba4be86fbfec78c93ed7dc4cbdc781873334bfcaabc0961a8d6cbc1` |
| XSA | `project/platform.xsa` | `sha256:9f58dccc1560ed1f56926c3e7d5fb8afd988c3d5f5431146d05f74c97c206407`（1500 B） |
| BD wrapper | `project/hdl/platform_bd_wrapper.v` | `sha256:d2b4b82a11b93abd52268d74b0595677b062f72b4a72ef3108827831cb81ce87` |

## 5. 故障注入门禁的机读部分证据（硬件路径受阻，机读判定已验证）

阶段③门禁要求覆盖读回失败路径（FAIL marker 机读判定）。硬件故障注入（先 FAIL 后 PASS 的两次真板运行）因 D1–D3 无法到达部署阶段，**未能执行**；其机读判定环节已用公开 `evaluate_observation` 显式传 marker 验证（真工具、真输出）：

| 输入文本 | 显式 markers | 机读 verdict |
|---|---|---|
| `WROTE:0x2A READ:0x2B` + `LED_E2E_FAIL`（注入读回不一致的预期输出） | pass=`LED_E2E_PASS` fail=`LED_E2E_FAIL` | **FAIL**（fail_marker_found=true, pass=false） |
| 16 行 `WROTE:0x2A READ:0x2A` / `WROTE:0x15 READ:0x15` + `LED_E2E_PASS` | 同上 | **PASS**（pass_marker_found=true, fail=false） |
| 空文本 | 同上 | **TIMEOUT** |
| 仅 banner | 同上 | **INCOMPLETE** |

固件侧注入路径设计就绪（`project/src/main.c` 的 `#ifdef FAULT_INJECT`：PL 回读异或 0x1 → 第 1 轮即 `LED_E2E_FAIL` 并停机），经 `ps_set_compiler_options defines` 注入；**未编译未上板**。

## 6. Skill / MCP 缺陷清单（不修生产代码，如实记录）

| # | 级别 | 缺陷 | 证据 | 影响 | 建议（最小修复方向） |
|---|---|---|---|---|---|
| D1 | **P1** | **无 BD 地址分配能力**：`platform_set_address` 对地址段设 `CONFIG.C_BASEADDR` 报 `[Common 17-107] read-only`；真实分配机制 `assign_bd_address` 无公开原子 | vivado.log（3 次 set_property 失败/read-only）；BD 文件无 0x41200000；manifest `address_map={}` | 任何含 PL 外设的 BD 无地址映射 → PS 访问外设 abort/不可达 → 6-LED 读回与控制核心需求不可满足；implementation 亦将失败 | 新增 `platform_assign_address` 原子（`assign_bd_address -offset`）或修正 `platform_set_address` 语义 |
| D2 | **P1** | **无 BD 外部端口创建能力**：无 `create_bd_port` / `make_bd_pins_external` 原子（`mcps/` 生产代码 grep 0 命中） | wrapper 端口清单全文（仅 DDR_*/FIXED_IO_*，无 LED 端口）；已移除 B05 的 `gpio_external` 步骤无 1→N 替代 | PL I/O 引脚无法从 BD 暴露 → 任何 PS 控制 PL 引脚的项目（LED/未来 ADC/HDMI）不可行 | 新增 `platform_make_external` 原子（make_bd_pins_external / create_bd_port+connect_bd_net） |
| D3 | **P1** | **XSA 无 HDF（勘误 §4 实证）**：原子路径无合成 → `write_hw_platform` 产出 `pre_synth` 空壳 | XSA 解包仅 xsa.json+xsa.xml（1500B，`<Files/>` 空）；Vivado 日志 `[Vivado_Tcl 4-424] Cannot write hardware definition file…`；`ps_create_platform` FAILED（PLATFORM_CREATE_FAILED，`hsi::current_hw_design`） | PS 域完全不可用 | 原子序列在 `platform_export_hardware` 前增加合成步骤（新增合成原子） |
| D4 | **P1** | **空闲心跳死锁**：后端存活但 idle>120s → 心跳停滞 → 下一条命令 `WORKER_UNRESPONSIVE`；`recover_execution` 因 worker alive 为 no-op（lane=IDLE）；唯一出路 `close_session`（强制杀后端） | `pl_generate_system_top` 被拒（recommended_action=RECOVER）；`recover_execution` 返回 IDLE/READY 未变化；`close_session` 后 Vivado PID 33156 消失 | 长思考间隔后流程卡死，须牺牲后端重启 | 空闲心跳刷新，或 P5 区分「本控制器持有且进程存活」 |
| D5 | P2 | `platform_set_address` 段名文档/示例错误：文档 `'<ip>/S_AXI'`，真实段名 `<ip>/S_AXI/Reg`（Vivado 报 `BD 41-1356 … /axi_gpio_led/S_AXI/Reg`） | capabilities.py:330 示例 + SKILL.md appendix §3 原子 #8 占位符；13a/13b 两次 FAILED | 按文档调用必然失败 | 修正文档/示例；原子可尝试 `Reg` 块名兜底 |
| D6 | P2 | 纯 Tcl 错误映射为误导 reason_code `ADAPTER_NOT_READY`（后端健康） | 13a/13b/14a 及 add_ip 重试等 error.details | 诊断误导 | 区分 Tcl 错误与适配器未就绪 |
| D7 | P2 | `platform_validate` 假阳性：`validate_bd_design` "already validated" 缓存 → 无新 error/critical warning → SUCCEEDED，掩盖地址未分配 | 14a FAILED(真实告警) → 14b SUCCEEDED 但 BD 地址仍为未分配 | 错误地把「未分配地址的 BD」判为验证通过 | 校验 BD 41-1356/41-2909 类告警或强制 re-validate |
| D8 | P2 | `write_hw_platform` 后 BD 查询损坏：`get_bd_cells *`/`get_bd_intf_pins -filter {TYPE == master}`/`current_project` 返回空，但 `create_bd_cell` 报 cell 已存在（幂等 add_ip 失败） | export_manifest `ip_list=[]`/`address_map={}`；platform_get_status `has_project=false`；add_ip 重试 op-97062dc1 FAILED "already exists" | Manifest 数据质量损坏、原子幂等契约破坏、后续平台操作不可用 | 排查 write_hw_platform 对 BD 上下文的影响；查询改用 -of_objects 主接口方式 |
| D9 | P2 | `platform_export_manifest` 时钟树降级：`clock_tree` 记录 pin 短名（`FCLK_CLK0`/`aclk`/…）而非完整路径 | manifest clock_tree 字段 vs B09 旧格式（`processing_system7_0/M_AXI_GP0_ACLK` 等） | Manifest 可读性/跨域一致性降级 | `get_property PATH` 或对象名 |
| D0 | P2 | `platform_configure_ps7` 缺 EMIO GPIO 键 | `_PS7_CONFIG_TO_PCW` 键集合 | EMIO 路线在公开契约下不可行 | 增加 emio_gpio 配置键 |

**Skill 侧**：SKILL.md / appendix 的 `platform_set_address` 段名占位符（`<ip>/S_AXI`）与真实行为不符（D5）；其余 S0–S8 阶段框架与公开工具对应关系在可达范围内验证一致（含决策 (a) 推进、UART capture 顺序、marker 纪律）。

## 7. 勘误 §4 已知差异验证结论（原子路径无顶层合成）

- **已验证**：`platform_export_hardware`（`write_hw_platform -fixed`，无合成）产出的 XSA 为 **1.5KB `pre_synth` 空壳**（仅 xsa.json/xsa.xml，`<Files/>` 为空，无 hwdef/hwh/ps7_init）；对比 B09 旧快捷路径（内部 `launch_runs synth_1`）的 XSA 含 12 文件完整 HDF（hwdef.xml、platform_bd.hwh、ps7_init.c/tcl 等，约 4MB）。
- **ps_import_hardware**：**接受**该 XSA（imported=true，XSCT 拷贝成功）——勘误所述「是否被 ps_import_hardware 正常接受」为**是**（仅文件层）。
- **但 ps_create_platform 必然失败**：`PLATFORM_CREATE_FAILED` / `'hsi::current_hw_design' failed due to earlier errors`（XSA 无 HDF，HSI 无法打开硬件设计）。
- **结论**：勘误 §4 的「6-LED 全链路是否需补合成步骤」——**必须补**：原子路径 XSA 对 PS 域不可用，`write_hw_platform` 前的顶层合成步骤（或等价 HDF 生成）是必需能力（D3）。

## 8. S5-PL / S5-PS / S6 / S7 / S8 状态

- **S5-PL**：未执行（`pl_generate_system_top` 被 D4 心跳死锁拒绝于尝试时；即便通过，D1/D3 使 BD/XSA 不可用，PL 构建无意义）。
- **S5-PS**：`ps_import_hardware`（新会话、正式顺序）成功但 `ps_create_platform` 失败（§7）——PS 域整体不可达。
- **S6**：未执行（需三 Manifest；PL/PS Manifest 不存在——符合「任何目录 0 个候选 → 停止并报告」）。
- **S7/S8**：未执行（无 bitstream/ELF；JTAG/UART 未触碰）。

## 9. 清理证据（PID 前后核对）

| 进程 | 本阶段前 | 本阶段中 | 本阶段后 |
|---|---|---|---|
| vivado.exe（本阶段启动，PID 33156） | 无 | 运行（platform 域） | **已终止**（close_session 的 `direct_backend_shutdown`；Get-Process 无结果） |
| rdi_xsct.exe（本阶段启动，PID 26084） | 无 | 运行（勘误会话） | **已终止**（close_session；Get-Process 无结果） |
| xsct/xsdb 等 | 无 | 无 | 无 |
| hw_server.exe（PID 19880） | 2026-08-09 已存在（早于本阶段） | 未触碰（仅连接复用） | 仍在运行（**不在清理范围**，与 B09 记录一致） |
| python（MCP 驱动 10064 + 服务器 26644） | 无 | 运行 | 本轮收尾时由本报告作者停掉（见 REPO 收尾说明） |

## 10. 未确认项

- LED 物理亮灭现象：未验证（流程受阻未部署）→ 留待阶段⑤。
- `platform_set_address` 在正确段名 + 已分配设计上是否可用：未验证（当前设计分配机制缺失）。
- Vivado 2023.1 `get_bd_cells` 在 `write_hw_platform` 后返回空的根因：未定位（只记录现象，D8）。
- 层次引用顶层（led6_top.v）在真实综合中的可行性：未验证（前置 D1/D3 阻塞）。

## 11. 结论与建议

- **本阶段判定：BLOCKED（P1）**。公开 100 工具在 Platform 域存在三个 P1 能力缺口（D1 地址分配、D2 BD 端口外部化、D3 XSA HDF），使「新泛化 Skill + 需求文档 + 公开 zynq_mcp 完成 6-LED 全流程」在真板上**不可达**；同时存在一个 P1 会话卡死缺陷（D4）。
- 已按铁律：未修改 `mcps/`、`skills/`、`boards/`、docs 冻结文档、三个 legacy 目录；只写了 workspace 工程/驱动/设计稿与本文档。全部 EDA/构建/Manifest/观测动作经公开 MCP；零 shell 逃生通道（本会话仅用 PowerShell 做进程/文件/哈希观察与驱动调用，未启动任何 EDA 工具进程）。
- **建议**：① 按 D1–D3 的最小修复（新增 assign_address / make_external / synthesis 能力）后，重跑阶段③白盒；② 修复 D4 心跳；③ 修复后任何阶段②/③变更按 B11 纪律触发全新会话重验（阶段⑥门禁 6）。
- 基线声明：本阶段不改生产代码，未重跑回归；阶段起点基线 1376 collected / 1337 passed / 1 skipped / 38 deselected（阶段②完成值）。
