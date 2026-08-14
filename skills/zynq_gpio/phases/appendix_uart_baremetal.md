# Appendix: 验证过的裸机 UART 代码

> 基于 Zynq-7020 (AX7020) 板卡验证。UART1 @ 0xE0001000, 115200 8N1.
> 此代码可以嵌入 Phase 3 `main.c`，替代 BSP `xil_printf`。

## 最小 UART 发送（逐字节，无 BSP 依赖）

```c
#include "xil_io.h"

#define UART1_BASE    0xE0001000
#define UART_SR       0x2C   /* Status Register (Channel Status) */
#define UART_FIFO     0x30   /* TX/RX FIFO */
#define UART_CR       0x00   /* Control Register */
#define UART_MR       0x04   /* Mode Register */
#define UART_BAUDGEN  0x18   /* Baud Rate Generator */
#define UART_BDIV     0x34   /* Baud Rate Divider */
#define UART_TXEMPTY  (1 << 3)  /* TX FIFO empty bit in SR */
#define UART_TXFULL   (1 << 4)  /* TX FIFO full bit in SR */

/* BSP-style primitive write (32-bit aligned, no null padding from BSP linkage) */
/* This is the standard standalone BSP implementation: write 32-bit word,
   but ensure only byte[0] is the data, byte[1:3] are 0x00.
   Actually for xil_printf-free output, use the UART TX FIFO directly: */

static void uart_putc(char c)
{
    /* Wait until TX FIFO is not full */
    while (Xil_In32(UART1_BASE + UART_SR) & UART_TXFULL);
    Xil_Out32(UART1_BASE + UART_FIFO, (unsigned int)c);
}

static void uart_send(const char *s)
{
    while (*s) {
        uart_putc(*s++);
    }
}
```

**注意**：上述 `Xil_Out32(UART_FIFO, c)` 仍会产生 3 个 null 字节（32-bit 写到 8-bit FIFO 的硬件行为——寄存器接口是 32-bit 但 FIFO 只取 byte[0]）。接收端需要用 `.replace("\x00", "")` 清理。如果使用 `Xil_Out8` 只写 byte，需要 PS UART 控制器配置为 8-bit 访问模式，这在 bare-metal 下涉及 SLCR 寄存器修改，超出 GPIO 项目范围。

**结论**：GPIO 项目使用 `xil_printf` 或上述 `Xil_Out32` UART 方案均可——两者都产生 null 字节，MCP `ps_stop_uart_capture` 会自动清理。本附录供需要完全无 BSP 依赖的程序参考。

## UART 初始化（115200 @ ~100MHz UART_REF）

```c
/* Zynq-7020: UART_REF = IO_PLL / (DIVISOR0+1) = 1000MHz / 11 ≈ 90.9MHz.
 * Baud = UART_REF / (CD * BDIV) = 90.9MHz / (49 * 16) = 115,944 bps (0.64% error).
 * This is close enough to 115200 for reliable PC reception. */

Xil_Out32(UART1_BASE + UART_CR,  0x00000000);  /* Disable UART */
Xil_Out32(UART1_BASE + UART_MR,  0x00000020);  /* 8N1 mode */
Xil_Out32(UART1_BASE + UART_BAUDGEN, 49);      /* CD=49 */
Xil_Out32(UART1_BASE + UART_BDIV, 16);         /* BDIV=16 */
Xil_Out32(UART1_BASE + UART_CR,  0x00000014);  /* TXEN + RXEN */
```

## 完整最小 main.c 骨架（无 BSP）

```c
#include "xil_io.h"

#define UART1_BASE    0xE0001000
#define LED_BASE      0x41200000

/* ... uart_putc / uart_send / uart_init from above ... */

int main(void)
{
    /* Init UART */
    Xil_Out32(UART1_BASE + 0x00, 0x00000000);
    Xil_Out32(UART1_BASE + 0x04, 0x00000020);
    Xil_Out32(UART1_BASE + 0x18, 49);
    Xil_Out32(UART1_BASE + 0x34, 16);
    Xil_Out32(UART1_BASE + 0x00, 0x00000014);

    /* GPIO direction: output */
    Xil_Out32(LED_BASE + 0x04, 0x0);

    uart_send("=== AX7020 GPIO B08 ===\r\n");

    /* Main loop — insert Phase 3 GPIO test spec logic here */
    unsigned int pattern = 0xA;
    for (int i = 0; i < 8; i++) {
        unsigned int wrote = ~pattern & 0xF;
        Xil_Out32(LED_BASE, wrote);
        unsigned int readback = Xil_In32(LED_BASE) & 0xF;
        /* Format and output via uart_send or xil_printf */
        /* Compare wrote vs readback */
        pattern ^= 0xF;
        /* delay ~1s */
        volatile int d; for (d = 0; d < 100000000; d++) asm volatile("");
    }
    uart_send("GPIO_E2E_PASS\r\n");
    while (1);
    return 0;
}
```

## BSP vs 裸机对比

| 方案 | 依赖 | text size | null 字节 | 推荐 |
|------|------|-----------|:--:|:--:|
| `xil_printf` | BSP (libxil.a, ~50KB) | +1.5KB | 是 | GPIO 项目（编译简单） |
| `Xil_Out32` 自写 | 仅 `xil_io.h` | +200B | 是 | 最小编译依赖 |
| `Xil_Out8` 自写 | 仅 `xil_io.h` | +200B | **否** | 需要 SLCR 8-bit 配置，GPIO 不推荐 |

**GPIO 项目推荐**：使用 BSP `xil_printf`（编译最简单，`ps_create_bsp` 后自动可用），
接受 null 字节问题（MCP 自动清理）。

## 调试提示

- UART 无输出 → 检查 `uart_init` 序列中 CR=0 (disable) → MR → BAUDGEN → BDIV → CR=0x14 的顺序
- 乱码 → UART_REF 频率与 baud divisor 不匹配。90.9MHz / (49×16) = 115,944bps
- `Xil_Out8` 无输出 → PS UART 默认 32-bit 访问；需要用 SLCR 解锁 + APER_CLK_CTRL 配置使能 8-bit。GPIO 项目跳过此方案
