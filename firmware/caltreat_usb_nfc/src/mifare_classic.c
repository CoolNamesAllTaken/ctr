#include "mifare_classic.h"

#include <string.h>

#define MFC_CMD_READ   0x30
#define MFC_CMD_WRITE  0xA0
#define MFC_ACK        0x0A /* 4-bit ACK nibble */

mfrc522_status_t mfc_auth(uint8_t key_cmd, uint8_t block,
                          const uint8_t key[6], const uint8_t uid[4]) {
    return mfrc522_mifare_auth(key_cmd, block, key, uid);
}

mfrc522_status_t mfc_read(uint8_t block, uint8_t out16[16]) {
    uint8_t cmd[4] = { MFC_CMD_READ, block, 0, 0 };
    uint8_t crc[2];
    mfrc522_status_t st = mfrc522_calc_crc(cmd, 2, crc);
    if (st != MFRC522_OK) return st;
    cmd[2] = crc[0];
    cmd[3] = crc[1];

    uint8_t rx[18]; /* 16 data + 2 CRC */
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    st = mfrc522_transceive(cmd, 4, 0, rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) return st;
    if (rx_len != 18) return MFRC522_ERR_COMM;

    /* Verify the card's CRC_A over the 16 data bytes. */
    uint8_t want[2];
    st = mfrc522_calc_crc(rx, 16, want);
    if (st != MFRC522_OK) return st;
    if (want[0] != rx[16] || want[1] != rx[17]) return MFRC522_ERR_CRC;

    memcpy(out16, rx, 16);
    return MFRC522_OK;
}

/* A MIFARE write is two phases, each answered with a 4-bit ACK/NAK nibble. */
static mfrc522_status_t expect_ack(const uint8_t *frame, uint8_t len) {
    uint8_t rx[1];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    mfrc522_status_t st = mfrc522_transceive(frame, len, 0, rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) return st;
    /* Response is a single 4-bit nibble. */
    if (rx_len != 1 || rx_bits != 4) return MFRC522_ERR_COMM;
    if ((rx[0] & 0x0F) != MFC_ACK) return MFRC522_ERR_COMM; /* NAK */
    return MFRC522_OK;
}

mfrc522_status_t mfc_write(uint8_t block, const uint8_t in16[16]) {
    /* Phase 1: WRITE command + block, expect ACK. */
    uint8_t cmd[4] = { MFC_CMD_WRITE, block, 0, 0 };
    uint8_t crc[2];
    mfrc522_status_t st = mfrc522_calc_crc(cmd, 2, crc);
    if (st != MFRC522_OK) return st;
    cmd[2] = crc[0];
    cmd[3] = crc[1];
    st = expect_ack(cmd, 4);
    if (st != MFRC522_OK) return st;

    /* Phase 2: 16 data bytes + CRC_A, expect ACK. */
    uint8_t data[18];
    memcpy(data, in16, 16);
    st = mfrc522_calc_crc(data, 16, crc);
    if (st != MFRC522_OK) return st;
    data[16] = crc[0];
    data[17] = crc[1];
    return expect_ack(data, 18);
}
