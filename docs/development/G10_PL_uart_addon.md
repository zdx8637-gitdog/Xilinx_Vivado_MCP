# Add PL UART to G10 Zynq Platform

> 目标: 在 AX7020 Base Platform 增加 FPGA PL 软 UART (F17)
> 状态: 方案设计，待实施

## Hardware

| 信号 | 引脚 | 位置 | 参数 |
|------|------|------|------|
| uart_tx | F17 | J11 Pin3 | 115200 8N1 |

## Architecture

```
top_wrapper.v                         ← 新建: 例化 BD + uart_tx
├── ax7020_base_wrapper (BD)
│   └── PS7 + AXI GPIO LED + AXI GPIO Status
└── uart_tx (Verilog module)          ← 复用 hello_fpga/rtl/uart_tx.v
    └── 每 500ms 发送 "G10_ARM_OK\r\n"
```

## Files Needed

| 文件 | 位置 | 说明 |
|------|------|------|
| `uart_tx.v` | 从 `hello_fpga/rtl/` 复制 | 已有, 115200 8N1, 50MHz |
| `top_wrapper.v` | `zynq_platforms/ax7020_base/rtl/` | 新建顶层, 例化 BD + uart_tx |
| `led_pins.xdc` | 已有 | 加 F17 约束 |

## Steps

### 1. 复制 uart_tx.v

```
cp hello_fpga/rtl/uart_tx.v → zynq_platforms/ax7020_base/rtl/
```

### 2. 创建 top_wrapper.v

```verilog
module top_wrapper(
    input  wire       sys_clk,      // FCLK_CLK0 from PS7 (50MHz)
    output wire       uart_tx,      // F17
    // BD ports are auto-connected by Vivado
    inout  wire [14:0] DDR_addr,
    ...
);

    // BD wrapper
    ax7020_base_wrapper u_bd (...);

    // UART debug
    wire uart_busy;
    reg  [7:0] uart_data;
    reg        uart_send;

    uart_tx u_debug (.clk(sys_clk), .rst_n(1'b1), .data(uart_data), .send(uart_send), .tx(uart_tx), .busy(uart_busy));
endmodule
```

### 3. 更新 XDC

```
set_property PACKAGE_PIN F17 [get_ports uart_tx]
set_property IOSTANDARD LVCMOS33 [get_ports uart_tx]
```

### 4. 构建

```
add_files rtl/uart_tx.v rtl/top_wrapper.v
set_property top top_wrapper [current_fileset]
synth_design → place_design → route_design → write_bitstream
```

## 验证

| 步骤 | 方法 |
|------|------|
| 1 | 构建 bitstream |
| 2 | JTAG: `fpga -f` + ARM `dow` |
| 3 | USB-UART 接 J11 Pin1(GND) + Pin3(F17) |
| 4 | COM5 115200 8N1 → 应收到数据 |
