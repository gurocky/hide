/* Bit-banged "SPI" on port A: a module with a file-static function. */
#include "spi.h"
#include "regs.h"

static void write_byte(uint8_t value)
{
    uint8_t bit;

    for (bit = 0; bit < 8; bit++) {
        _PTAD.Bits.PTAD0 = (value & 0x80) ? 1 : 0;
        _PTAD.Bits.PTAD1 = 1;
        _PTAD.Bits.PTAD1 = 0;
        value <<= 1;
    }
}

void spi_send(const uint8_t *buf, uint8_t len)
{
    while (len--) {
        write_byte(*buf++);
    }
}
