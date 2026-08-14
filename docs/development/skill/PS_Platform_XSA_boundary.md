# Platform XSA 归属与消费 — 跨域契约

> 日期: 2026-08-09
> 状态: FROZEN — Platform Domain 与 PS Domain 共享 XSA 的正式边界
> 覆盖: Platform XSA vs System XSA 的架构定义、读写归属、时序顺序、校验链

---

## 1. XSA 的两种类型

架构文档 §5.3 定义了两种 XSA，归属明确：

| | Platform XSA | System XSA |
|---|-------------|------------|
| **何时导出** | Platform MCP 导出 BD 后 | PL MCP 生成最终 bitstream 后 |
| **包含内容** | PS7 配置 + 地址映射 + 中断映射 + BD 中的 IP 的硬件描述 | 最终硬件描述 (含 PL 逻辑实例化) + 可选 embedded bitstream |
| **用途** | PS BSP / xparameters.h 生成 | 未来 BOOT.BIN 流程、完整设计归档 |
| **写所有者** | **Platform MCP** | **PL MCP** |
| **文件名示例** | `ax7020_adc_platform.xsa` | `ax7020_adc_system.xsa` |

**PS MCP 的 `ps_import_hardware()` 使用 Platform XSA**（BSP 只需要地址映射和 PS7 配置，不需要完整的 implemented design）。System XSA 是未来 boot/deployment 阶段的产物，当前 JTAG-only 开发模式下不生成。

## 2. 三方角色与边界

```
Platform Domain                  PL Domain                   PS Domain
─────────────────               ──────────                  ──────────
W 所有者：Platform XSA           只读：Platform XSA          R 消费者：Platform XSA
                                 (用于接口对齐)              (用于 BSP/xparams 生成)
                                 
W 所有者：Platform Manifest      R 消费者：读取 address_map  R 消费者：读取 address_map
                                 ├── 对齐 RTL 接口           ├── 生成 xparameters.h
                                 ├── 综合/实现               ├── 确认基地址
                                 └── 生成 bitstream           └── 生成 linker script

                                 W 所有者：System XSA
                                 (含 PL 逻辑 + bitstream)
```

## 3. 时序顺序

正确的 XSA 消费顺序是串行的（与三域架构一致）：

```
Phase 1: Platform Domain
  ┌─────────────────────────────────────────────────────┐
  │ platform.generate()                                 │
  │ → 创建 BD (PS7 + AXI GPIO + SmartConnect)          │
  │ → 分配地址 (GPIO = 0x41200000)                     │
  │ → 导出 Platform XSA + Platform Manifest             │
  │   - XSA: platforms/<name>.xsa                       │
  │   - Manifest: manifests/platform/<revision>.json    │
  │   - Manifest 含: address_map, clock_tree,           │
  │     pl_interfaces, platform_revision                 │
  └─────────────────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ↓                                 ↓
Phase 2a: PL Domain              Phase 2b: PS Domain
  ┌──────────────────────┐       ┌──────────────────────────┐
  │ 读取 Platform Manifest│       │ ps_import_hardware(       │
  │ 对齐 RTL 接口         │       │   xsa_path=platform.xsa) │
  │ system_top.v          │       │ → 生成 xparameters.h     │
  │ synthesize → bitstream│       │ → BSP + app + compile    │
  │                       │       │ → ELF                    │
  └──────────────────────┘       └──────────────────────────┘
          │                                 │
          └────────────────┬────────────────┘
                           ↓
Phase 4: Workflow 部署
  ┌─────────────────────────────────────────────────────┐
  │ pl.program(bitstream) → ps.download(elf) → ps.run() │
  │ → ps.read_uart() → PASS/FAIL                        │
  └─────────────────────────────────────────────────────┘
```

## 4. 校验链

PS Domain 消费 Platform XSA 时，必须校验以下链：

```
ps_import_hardware(xsa_path)
  → 读取 XSA 文件的 SHA256
  → 与 Platform Manifest 中的 xsa_sha256 比对
  → Manifest 的 platform_revision 与 session context 一致
  → board_profile_sha256 一致
  → 不一致 → 拒绝 (STALE_XSA / REVISION_MISMATCH)
```

**注意**：当前 B06 库阶段 `ps_import_hardware` 尚未做 Manifest 校验——这属于集成阶段的完善项（P2）。Workflow (B07) 应在调用 PS import 之前先验证 revision 一致性。

## 5. Skill 层的指导意见

### Platform Skill

- 输出 Platform XSA + Manifest
- 保留 XSA 导出路径在 manifest 中（相对路径）
- **不修改已导出的 XSA**（锁定后不可变）

### PL Skill

- 从 Manifest 读取 PL 接口信息，不直接读 XSA
- 最终产出 System XSA（含 PL 逻辑）
- **不修改 Platform XSA**

### PS Skill

- 使用 **Platform XSA**（不是 System XSA）
- 调用 `ps_import_hardware(xsa_path)` → 自动生成 xparameters.h
- BSP 配置由 Manifest 的 clock_tree/address_map 驱动
- **不拥有 XSA，不修改 XSA**

## 6. 为什么 PS 用 Platform XSA 而不是 System XSA

| | Platform XSA | System XSA |
|---|:--:|:--:|
| 可用于 BSP 生成 | ✅ | 技术上可行但不必要 |
| 包含 PL 逻辑 | ❌ | ✅ |
| 大小 | 小（仅 BD） | 大（含 implemented design） |
| 生成时机 | B05 完成 | B04 R3.3+ |
| 是否需要 PL 完成 | 否 | 是 |

Platform XSA 让 PS 软件开发可以**与 PL 综合/布线并行**进行——这正是三域架构的并行优势。如果 PS 必须等 System XSA，就浪费了 PL 综合的 5-10 分钟。

## 7. 当前文件位置

```
XSA 来源:
  B05 生成: <project>/platform/platform.xsa          (新 MCP 产出)
  已有参考: zynq_platforms/xsa/ax7020_base.xsa       (旧平台工程，用于测试)
  
B06 测试:
  黑盒: 使用 ax7020_base.xsa 或 B05 新生成的 XSA
  Unit: 使用 FakeXsdbBridge（不走真实 XSA）
```

**Agent3 黑盒项目**：`validation_projects/phase_blackbox/b06_ps_domain/` 应使用 `zynq_platforms/xsa/ax7020_base.xsa` 作为测试夹具——这是一个已知好的、已验证的 XSA。B07 端到端使用 B05 新生成的 XSA。
