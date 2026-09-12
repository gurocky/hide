#include "crc8.h"

uint8_t crc8(const uint8_t *data, uint8_t len)
{
    uint8_t crc = 0;
    uint8_t i, j;

    for (i = 0; i < len; i++) {
        crc ^= data[i];
        for (j = 0; j < 8; j++) {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}
