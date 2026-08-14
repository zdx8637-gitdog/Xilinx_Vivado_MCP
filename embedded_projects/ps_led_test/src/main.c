/*
 * G11 — ARM Validation (direct register writes, no BSP dependency)
 */

#include "xil_io.h"

#define LED_BASE     0x41200000
#define UART1_BASE   0xE0001000

/* UART register offsets */
#define UART_CR       0x00   /* Control */
#define UART_MR       0x04   /* Mode */
#define UART_BAUDGEN  0x18   /* Baud Rate Generator */
#define UART_BDIV     0x34   /* Baud Rate Divider */

static void uart_send(const char *s)
{
    while (*s) {
        /* Wait TX FIFO not full (SR bit 4) */
        while (Xil_In32(UART1_BASE + 0x2C) & (1 << 4));
        Xil_Out32(UART1_BASE + 0x30, *s++);
    }
}

static void delay(void)
{
    volatile int i;
    for (i = 0; i < 600000000; i++) asm volatile("");
}

int main(void)
{
    /* Reconfigure UART1: 115200 @ ~90.9MHz
     * UART_REF = IO_PLL / (DIVISOR0+1) = 1000MHz / 11 ≈ 90.9 MHz
     * Baud = 90.9M/(49*16) = 115,944 bps (0.64% error) */
    Xil_Out32(UART1_BASE + UART_CR,       0x00000000);  /* disable */
    Xil_Out32(UART1_BASE + UART_MR,       0x00000020);  /* 8N1 */
    Xil_Out32(UART1_BASE + UART_BAUDGEN, 49);           /* CD=49 */
    Xil_Out32(UART1_BASE + UART_BDIV,     16);          /* BDIV=16 */
    Xil_Out32(UART1_BASE + UART_CR,       0x00000014);  /* TXEN+RXEN */

    /* GPIO direction: output */
    Xil_Out32(LED_BASE + 0x04, 0x0);

    int count = 0;
    unsigned int pattern = 0xA;

    uart_send("\r\n=== AX7020 ARM Test G11 ===\r\n");
    uart_send("UART: direct register write\r\n");
    uart_send("LED: 1010 <-> 0101 @ ~1s\r\n\r\n");

    while (1) {
        Xil_Out32(LED_BASE, ~pattern & 0xF);

        if (pattern == 0xA) {
            uart_send("1010\r\n");
            pattern = 0x5;
        } else {
            uart_send("0101\r\n");
            pattern = 0xA;
        }
        count++;
        delay();
    }
    return 0;
}
