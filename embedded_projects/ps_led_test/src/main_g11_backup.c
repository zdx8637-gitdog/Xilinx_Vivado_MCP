/*
 * G11 — ARM Validation (ALINX-compliant XSA)
 * Uses BSP xil_printf for UART — tested with ALINX 537-param PS7 config
 */
#include "xparameters.h"
#include "xil_printf.h"
#include "xil_io.h"

int main(void)
{
    xil_printf("\r\n========================================\r\n");
    xil_printf("  AX7020 ARM JTAG Test — G11\r\n");
    xil_printf("  PS7 UART1 @ 115200, ARM @ 667MHz\r\n");
    xil_printf("========================================\r\n\r\n");

    int count = 0;
    unsigned int pattern = 0xA;  /* LED[1,3] ON (active-low) */

    while (1) {
        /* LED toggle: 1010 <-> 0101 every ~1 second */
        Xil_Out32(0x41200004, 0x0);  /* GPIO direction: output */
        Xil_Out32(0x41200000, ~pattern & 0xF);

        if (pattern == 0xA) {
            xil_printf("[%d] 1010\r\n", count);
            pattern = 0x5;
        } else {
            xil_printf("[%d] 0101\r\n", count);
            pattern = 0xA;
        }
        count++;

        /* Spin ~1 second */
        volatile int i;
        for (i = 0; i < 600000000; i++) asm volatile("");
    }
    return 0;
}
