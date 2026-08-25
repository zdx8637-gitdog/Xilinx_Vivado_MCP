# B12-A2 白盒 v2（Agent1c）报告 — 状态：平台 + PL + bitstream 全部成功；S5-PS 被框架级限制阻塞

> 日期：2026-08-25 ｜ 工作区：`workspaces/b12_a2_agent1c_20260825/`（本轮使用全新 `project_f`）
> 状态：**S0-S4 + S5 平台 + S5 PL + PL manifest 全部成功**；**S5-PS 被框架级限制阻塞**（按指示如实报告，不再做新的重置）。
> 关键产出位（全新 project_f，无 manifest 冲突）：bitstream/三 Manifest 已发布。

---

## 0. 结论速览

| 阶段 | 状态 |
|---|---|
| S0–S4 设计文档 | ✅ |
| S5 平台（外部化主接口 + 自定义从机） | ✅ BD 合成、XSA、**platform manifest（address_map={} → Rule 4 通过）** |
| S5 PL（自定义 AXI3 从机 + ADC FSM）| ✅ synth→place→route→timing(**met**)→**bitstream(DRC 0 errors)** |
| PL manifest | ✅ **已发布**（artiface_state=PUBLISHED，stage→PS_BUILD） |
| S5 PS 裸机 | ⛔ **ps_compile 失败**：BSP 库不完整（缺 XUartPs_Initialize/_exit）+ D1 worker 身份校验不一致 |
| S6 verify / S7 部署 / S8 判定 | 未到达（被 S5-PS 阻塞） |

## 1. 本轮一次性路径成功要点（全新 project_f，规避了前几轮所有设计/构建问题）

本轮按「全新 project_path + 严格按序」一口气跑通 **平台 + PL + bitstream**：
- **全新 `project_f`**（无旧 manifest）→ 规避了之前「相同 BD 重跑导致 platform manifest 不可恢复冲突（C）」。
- **`adc_top` 顶层模块**（放 `project_f\rtl\adc_top.v`）→ 规避 `pl_generate_system_top` 覆盖 `rtl/system_top.v` 的顶层命名冲突（E）。
- **RTL/XDC 全部 stage 到 `project_f\rtl`、`project_f\xdc`** → 满足 build_manifest 只在 `project/**` 下发现的约束（G）。
- **XDC 注释独占行**（`project_f\xdc\adc7606c.xdc`）→ 规避 Vivado 误解析行内 `#`（F）。
- 全部步骤 **SUCCEEDED 连续**（避免中途失败触发 reset_run/stage 回退（A/B））。

### 1.1 关键产物（project_f 相对路径）
| 产物 | 路径 | revision/hash |
|---|---|---|
| Platform Manifest | `manifests/platform/sha256_c77b882451f1f796166b62db406f29bcf99ce43f41422bc3f8747340a33ce6e6.json` | `sha256:c77b8824…`（address_map={}，ip_list=[processing_system7_0]） |
| PL Manifest | `manifests/pl/sha256_9ec07154a5b697b28a6e67e2cd1d17c7e82b0f072eeea2276f2e874f3d279bf3.json` | `sha256:9ec07154…`（bitstream PUBLISHED） |
| Bitstream | `bitstream/system_top.bit` | ~4.0 MB，DRC 0 errors，timing met |
| XSA | `platform.xsa`（staged `inputs/platform.xsa`） | — |
| 自定义 RTL | `rtl/adc_top.v`、`rtl/adc_ringbuf_top.v` | 可综合 / 时序满足 |
| XDC | `xdc/adc7606c.xdc`（BANK35 LVCMOS33） | 已修复（注释独占行） |
| PS 源 | `ps_src/main.c`（READY + UPLOAD + 读快照 + ASCII-hex + sum16 + DONE + A2_PASS） | — |

## 2. S5-PS 阻塞（框架级）

### 2.1 `ps_compile` FAILED → 定位：BSP 库不完整
- make link 报：`undefined reference to XUartPs_Initialize`（main.c:160）、`undefined reference to _exit`。
- `nm libxil.a` / `nm *.o` 证实：**libxil.a 缺少 XUartPs_Initialize 的定义；`_exit.o` 为空对象**（无 T/U 符号）。
  - 说明 standalone BSP 库 `libxil.a` **构建不完整**：XUartPs 驱动对象有 `XUartPs_SetBaudRate`（T），但缺 `XUartPs_Initialize`；
    运行时对象 `_exit.o` 为空。→ 任何 PS app 都因此无法链接（不是设计错误，是 BSP 库产物不完整）。
- 因 D-C（ps_compile 的 MAKE_FALLBACK 只回传 `no ELF produced`）**抑制了详细编译/链接错误**，只能用工具链 `nm` 定位。

### 2.2 ps_compile 重试 → `OUTCOME_UNKNOWN`/`BACKEND_IDENTITY_MISMATCH`（D1 worker 身份校验）
- 重跑 ps_compile 时 XSCT 后端 worker 身份校验不一致（`worker_health=IDENTITY_MISMATCH`，pid 23148，generation 9）→
  `OUTCOME_UNKNOWN` → lane `RECOVERY_REQUIRED`（D1 类残留，属 P2）。
- `ps_get_bsp_status` 也命中 schema 拒绝（`platform_name`/`project_path` 不是受支持参数 → TypeError → OUTCOME_UNKNOWN，D-B 类）。

## 3. 上一步的框架限制（已在"一次性路径"中规避，非本轮设计问题）

前几轮排查并规避的框架级限制（A-G，详见上一版报告 §2）：无 reset_run（A）、无 stage 回退（B）、platform manifest 不可恢复冲突（C）、add_ip 回读假阴性（D）、顶层命名冲突（E，已改用 adc_top）、XDC 行内 #（F，已修）、build_manifest 只在 project/** 下发现（G，已 stage）。

## 4. 建议（P2 技术债，排除本轮）
1. **BSP 库构建完整性**：`ps_create_bsp`/standalone 库需保证所有对象（驱动 + 运行时 `_exit`）完整编译，`libxil.a` 不得含空对象；`ps_compile` 失败应回传**完整 make/链接输出**（D-C 已记录，但 BSP 库不完整是独立问题）。
2. **D1 worker 身份**：XSCT 后端 generation/identity 校验在恢复后仍不一致 → 需接管/重连机制（P2）。
3. `ps_get_bsp_status` schema（D-B）补齐 `platform_name`/`project_path` 支持。

## 5. 结论
- **设计 + 平台 + PL + bitstream + 三 Manifest（platform/pl）全部成功**；盲测所需的中止条件（设计可综合、时序满足、位流生成）已满足。
- **S5-PS 及 S6-S8 未完成**：被「BSP 库不完整（缺 XUartPs_Initialize/_exit）+ D1 worker 身份不一致 + D-C 抑制错误」这些**框架级**问题阻塞（非设计错误）。
- 诚实话：已到「框架级阻塞即停」边界，按指示停止并报告，不进行新的重置尝试；上述 P2 项由主代理安排黑盒前的修复轮处理。

## 6. 声明
- 盲测通道/频率**未臆造**（未采集；文档无臆造值）。
- 只写工作区与报告；未改 mcps/skills/boards/CLAUDE.md/冻结文档/legacy；未执行 git 写操作；未自行冻结/越级。
