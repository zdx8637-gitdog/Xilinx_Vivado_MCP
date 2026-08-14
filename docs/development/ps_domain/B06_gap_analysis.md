# B06 集成阶段 — 全架构差距分析

> 2026-08-08 | 冻结架构 v2.3.1 vs 当前代码 vs 旧 Vivado MCP vs SynthPilot 参考

## 数据源

| 源 | 位置 | 状态 |
|---|------|------|
| 冻结架构 | `docs/architecture_ai_zynq7020.md` §4.2–§4.4 | ~128 APIs 定义 |
| 旧 Vivado MCP | `Xilinx_Vivado_MCP/server.py` | 27 tools，全注册到 `.mcp.json` |
| 新 zynq_mcp | `mcps/zynq_mcp/` | **33 tools**，未注册到 `.mcp.json` |
| B06 集成中 | capabilities.py + dispatcher.py | 22 PS tools 刚集成 |

## 概览

```
架构定义 ~128 APIs
  ├── PS domain  ~41  → 实现 22 + 库阶段有但未注册 6 = 28/41 (68%)
  ├── PL domain   ~41  → 旧 MCP 有 24/41, 新 zynq_mcp 只桥接了 1/41 (2%)
  └── Platform    ~46  → 实现 1 (platform_generate 硬编码), 原子 APIs 全部未实现
```

---

## 一、PL Domain 差距（最大缺口）

### 🔴 P0：旧 MCP 的 24 个 tools 未桥接到新 zynq_mcp

这些 tools 存在于 `Xilinx_Vivado_MCP/` 但**必须通过 `.mcp.json` 的 `vivado` server 调用**，不能通过统一的 `zynq_mcp` 入口。

| 旧 tool | 架构对应 API | 类别 | Brick |
|---------|-------------|------|-------|
| `create_project` | `pl.create_project()` | 工程 | B04 R3.2 |
| `open_checkpoint` | `pl.open_checkpoint()` | 工程 | B04 R3.2 |
| `close_design` | `pl.close_project()` | 工程 | B04 R3.2 |
| `synth_design` | `pl.synthesize()` | 综合 | B04 R3.2 |
| `place_design` | `pl.place()` | 布局 | B04 R3.2 |
| `route_design` | `pl.route()` | 布线 | B04 R3.2 |
| `write_bitstream` | `pl.generate_bitstream()` | 比特流 | B04 R3.3 |
| `report_timing_summary` | `pl.analyze_timing()` | 时序 | B04 R3.2 |
| `report_utilization` | `pl.analyze_utilization()` | 资源 | B04 R3.2 |
| `get_cells` | `pl.query_cells()` | 查询 | B04 R3.2 |
| `get_nets` | `pl.query_nets()` | 查询 | B04 R3.2 |
| `get_clocks` | `pl.query_clocks()` | 查询 | B04 R3.2 |
| `get_ports` | `pl.query_ports()` | 查询 | B04 R3.2 |
| `get_property` | `pl.get_property()` | 查询 | B04 R3.2 |
| `validate_design` | `pl.validate()` | 验证 | B04 R3.2 |
| `get_vivado_info` | `pl.get_vivado_info()` | 信息 | B04 R3.2 |
| `compile_sim` | `pl.simulate()` 子步骤 | 仿真 | B04 R3.2 |
| `elaborate_sim` | `pl.simulate()` 子步骤 | 仿真 | B04 R3.2 |
| `run_simulation` | `pl.simulate()` 子步骤 | 仿真 | B04 R3.2 |
| `parse_sim_log` | `pl.simulate()` 子步骤 | 仿真 | B04 R3.2 |
| `connect_hw_server` | `pl.connect_hw_server()` | JTAG | B04 R3.4 |
| `get_device_status` | `pl.get_device_status()` | JTAG | B04 R3.4 |
| `program_device` | `pl.program()` | JTAG | B04 R3.4 |
| `read_uart` | → 已移到 PS domain | UART | ✅ B06 |
| `list_serial_ports` | → 已移到 PS domain | UART | ✅ B06 |
| `run_tcl` | `pl.run_tcl()` [ADMIN] | 管理 | 待定 |

**问题**：B07 GPIO Workflow 需要 `直接调用统一的 zynq_mcp` 完成 platform_generate → pl.synthesize → ps.compile → deploy。但现在 PL 综合链路走的是旧 `.mcp.json` 的 vivado server，不走统一入口。

### 🟡 P1：架构定义但旧 MCP 也没有的 PL APIs

| API | 说明 |
|-----|------|
| `pl.open_project(path)` | 打开已有工程（旧 MCP 只有 create_project） |
| `pl.add_sources(files, type?)` | 添加源文件 |
| `pl.remove_sources(files)` | 移除源文件 |
| `pl.set_top(module)` | 设置顶层 |
| `pl.update_compile_order()` | 更新编译顺序 |
| `pl.get_project_status()` | 工程状态查询 |
| `pl.simulate()` | 高层仿真封装（非 4 个独立步骤） |
| `pl.place_and_route()` | 一键布局布线 |
| `pl.run_drc()` | DRC 检查 |
| `pl.get_build_status()` | 构建状态 |
| `pl.get_wns()` / `pl.get_tns()` | 窄查询（非完整报告） |
| `pl.get_power()` | 功耗 |
| `pl.query_timing_paths()` | 时序路径查询 |
| `pl.open_hw_target()` / `pl.close_hw_target()` | 硬件目标管理 |
| `pl.list_devices()` / `pl.select_device(id)` | 设备选择 |
| `pl.verify()` | 后综合验证 |

### 🔵 P2：ILA 调试 7 APIs

架构明确要求但 Brick 规划中列为 B10 后候选。当前 GPIO 切片不需要。

---

## 二、Platform Domain 差距

### 🔴 P0：架构定义的 46 个原子 API 只实现了 1 个

B05 `platform_generate` 是硬编码流程（PS7 + GPIO + SmartConnect），把所有步骤打包成一个 tool。下面 46 个原子 API **一个都没暴露**：

| 类别 | APIs | 状态 |
|------|------|:--:|
| Design 生命周期 | `create_design`, `open_design`, `save_design`, `close_design`, `get_status` | ❌ 5/5 未实现 |
| PS7 配置 | `add_ps7`, `configure_ps7` | ❌ 2/2（硬编码在 platform_generate 内部） |
| IP 管理 | `add_ip`, `set_ip_properties`, `remove_ip`, `list_ips` | ❌ 4/4 未实现 |
| IP 快捷 | `add_axi_dma`, `add_axi_gpio`, `add_axi_intc`, `add_processor_reset`, `add_smartconnect` | ❌ 5/5（硬编码在 Tcl 中） |
| 自定义 RTL | `add_module_reference`, `refresh_module`, `list_module_interfaces`, `create_interface_port`, `create_signal_port` | ❌ 5/5 未实现 |
| 连线 | `connect_interface`, `connect_signal`, `disconnect`, `make_external`, `query_connections` | ❌ 5/5 未实现 |
| 时钟/复位/中断 | `connect_clock`, `connect_reset`, `connect_interrupt`, `query_clock_tree`, `query_reset_tree`, `query_interrupt_map` | ❌ 6/6 未实现 |
| 地址空间 | `assign_addresses`, `set_address`, `get_address_space`, `exclude_range`, `check_address_conflicts`, `query_address_map` | ❌ 6/6 未实现 |
| 验证 | `validate`, `check_unconnected`, `query_topology` | ❌ 3/3 未实现 |
| 导出 | `generate_wrapper`, `generate_outputs`, `export_hardware`, `export_manifest` | ❌ 4/4（硬编码在 platform_generate 内部） |
| **合计** | | **0/46** |

**另外**：IP Catalog 查询（`platform.catalog_search(filter?)` / `platform.catalog_describe(vlnv?)`）连架构文档里都没有定义，属于缺失的设计规范。

---

## 三、PS Domain 差距

### ✅ 已实现（B06 第一批集成，22 tools）

| 类别 | Tools | 数量 |
|------|-------|:--:|
| JTAG 连接 | `ps_connect/disconnect_hw_server`, `ps_list/select_targets`, `ps_get_target_status/device_info` | 6 |
| 目标控制 | `ps_reset_target`, `ps_initialize_ps`, `ps_run/halt/step_target`, `ps_wait_for_state` | 7 |
| 内存/寄存器 | `ps_reg_read/write`, `ps_mem_read/write` | 4 |
| 恢复 | `ps_recover/reconnect_target`, `ps_clear_debug_session`, `ps_diagnose_dap` | 4 |
| UART | `ps_read_uart`, `ps_list_serial_ports` | 2 |
| **合计** | | **22** |

### 🟡 需要 XSA/BSP（第二批，等 B05 Platform XSA）

| 类别 | APIs | 数量 |
|------|------|:--:|
| BSP | `ps_import_hardware`, `ps_create_platform`, `ps_create_bsp`, `ps_update_hardware`, `ps_get_bsp_status` | 5 |
| 编译 | `ps_create_app`, `ps_add_sources`, `ps_set_compiler_options`, `ps_compile`, `ps_get_build_status`, `ps_read_elf_info` | 6 |
| **合计** | | **11** |

### 🟡 需要 JTAG + ELF（第三批）

| 类别 | APIs | 数量 |
|------|------|:--:|
| 下载 | `ps_download_elf` | 1 |
| 调试 | `ps_debug_start/close`, `ps_breakpoint_add/remove`, `ps_read/write_register`, `ps_stack_trace` | 7 |
| **合计** | | **8** |

### 🟢 库阶段已有但未注册为 MCP tool

| Tools | 在库阶段的函数 | 备注 |
|-------|-------------|------|
| `ps_write_uart` | SerialAdapter.write() | 库已有，未注册 |

---

## 四、按 Brick 规划的覆盖状态

| Brick | 目标 | 架构要求 | 实现 | 差距 |
|-------|------|:--:|:--:|------|
| B04 R3.2 | PL Build Pipeline | ~18 APIs | 0 | **全部未实现** |
| B04 R3.3 | Bitstream + Manifest | ~3 APIs | 0 | 未实现 |
| B04 R3.4 | JTAG/Hardware | ~8 APIs | 旧 MCP 有 | 未桥接 |
| B04 R3.5 | Integration Gate | 全量 list_tools=21 | 现在 list_tools=33 | PL tools 全缺 |
| **B05** | Platform Domain | 46 APIs | 1 | **45/46 未实现** |
| **B06** | PS Domain | 41 APIs | 22+库6 | **13/41 未实现**（BSP/Debug） |

---

## 五、优先级排序

### 🔴 阻塞 B07 GPIO Workflow 的

| # | 缺口 | 借什么 |
|---|------|--------|
| 1 | PL synth/place/route/bitstream 链路 | 桥接旧 Vivado MCP 到 zynq_mcp PL domain |
| 2 | Platform 原子 APIs（至少 add_ip/connect/assign_address） | 先做最小集，后续扩展 |
| 3 | PS download_elf（JTAG 下载 ELF 到 DDR） | B06 第三批 |
| 4 | PS BSP/Build（编译 C → ELF） | B06 第二批（需 B05 XSA） |

### 🟡 不阻塞但架构有要求的

| # | 缺口 | 说明 |
|---|------|------|
| 5 | `platform.list_ips()` | 架构 §4.3.3 定义但未实现 |
| 6 | `platform.catalog_search/describe()` | 架构未定义，需要先补充设计规范 |
| 7 | ILA 7 APIs | 架构 §4.4 定义，Brick 规划为 B10 后 |
| 8 | `pl.query_timing_paths/get_power/get_wns/get_tns` | 细粒度查询，架构定义但非 GPIO 必须 |
| 9 | `ps_write_uart` | 库已有，简单注册 |

### 🟢 可延后的

| # | 缺口 |
|---|------|
| 10 | Platform 快捷 APIs（add_axi_dma, add_axi_intc 等）— 等 DMA 切片 |
| 11 | Platform 自定义 RTL 接入（add_module_reference 等）— 等复杂 PL 设计 |
| 12 | PS Debug Session（7 APIs）— 库已有，注册即可 |
