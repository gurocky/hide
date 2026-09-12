/*
 * Sample firmware for the hcs08-dap tests: a receive buffer, a CRC and a
 * register write, so the debug info has globals, locals, a static function,
 * a pointer, an array and a bit-field register.
 */
#include <stdint.h>
#include "crc8.h"
#include "spi.h"
#include "regs.h"

uint8_t rx_buf[64];
uint8_t rx_len;
uint8_t rx_crc;

void main(void)
{
    rx_len = 4;
    rx_buf[0] = 0x01;
    rx_buf[1] = 0x02;
    rx_buf[2] = 0x03;
    rx_buf[3] = 0x04;
    for (;;) {
        rx_crc = crc8(rx_buf, rx_len);
        spi_send(rx_buf, rx_len);
        PTAD ^= 0x80;
    }
}
