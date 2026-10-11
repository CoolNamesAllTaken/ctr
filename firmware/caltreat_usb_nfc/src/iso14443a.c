#include "iso14443a.h"

#include <string.h>

/* PICC commands */
#define PICC_REQA   0x26
#define PICC_WUPA   0x52
#define PICC_HLTA   0x50
#define PICC_CT     0x88 /* cascade tag */

static const uint8_t sel_cmd[3] = { 0x93, 0x95, 0x97 }; /* cascade levels 1..3 */

/* REQA/WUPA: a 7-bit short frame, ATQA is 2 bytes. */
static mfrc522_status_t reqa_wupa(bool use_wupa, uint8_t atqa[2]) {
    uint8_t cmd = use_wupa ? PICC_WUPA : PICC_REQA;
    uint8_t rx[2];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;

    mfrc522_stop_crypto1();
    mfrc522_status_t st = mfrc522_transceive(&cmd, 1, 7 /* 7 valid bits */,
                                             rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) return st;
    if (rx_len != 2) return MFRC522_ERR_COMM;
    atqa[0] = rx[0];
    atqa[1] = rx[1];
    return MFRC522_OK;
}

/*
 * Anticollision + SELECT for one cascade level. On success, writes the 4 UID
 * bytes carried at this level into uid4 and the level's SAK into *sak.
 * *cascade is set true when SAK says another level follows.
 *
 * We assume a single tag in the field (no bit-level collision resolution), which
 * matches the RC522's usual single-card use. A collision returns an error.
 */
static mfrc522_status_t anticoll_select(uint8_t level, uint8_t uid4[4],
                                        uint8_t *sak, bool *cascade) {
    uint8_t buf[9];
    buf[0] = sel_cmd[level];
    buf[1] = 0x20; /* NVB: 2 bytes valid (SEL + NVB), anticollision phase */

    uint8_t rx[5];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;

    /* Anticollision: ask for the 4 UID bytes + BCC. */
    mfrc522_status_t st = mfrc522_transceive(buf, 2, 0, rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) return st;
    if (rx_len != 5) return MFRC522_ERR_COMM;

    /* BCC check: XOR of the 4 UID bytes must equal the 5th. */
    if ((rx[0] ^ rx[1] ^ rx[2] ^ rx[3]) != rx[4]) return MFRC522_ERR_COMM;

    memcpy(uid4, rx, 4);

    /* SELECT: SEL, NVB=0x70 (7 bytes), the 4 UID bytes, BCC, then CRC_A. */
    buf[0] = sel_cmd[level];
    buf[1] = 0x70;
    memcpy(&buf[2], rx, 5); /* UID0..3 + BCC */
    uint8_t crc[2];
    st = mfrc522_calc_crc(buf, 7, crc);
    if (st != MFRC522_OK) return st;
    buf[7] = crc[0];
    buf[8] = crc[1];

    uint8_t sak_rx[3]; /* SAK (1) + CRC_A (2) */
    uint8_t sak_len = sizeof(sak_rx);
    st = mfrc522_transceive(buf, 9, 0, sak_rx, &sak_len, &rx_bits);
    if (st != MFRC522_OK) return st;
    if (sak_len != 3) return MFRC522_ERR_COMM;

    *sak = sak_rx[0];
    *cascade = (sak_rx[0] & 0x04) != 0; /* bit 3 set => UID not complete */
    return MFRC522_OK;
}

mfrc522_status_t iso14443a_select(iso14443a_target_t *t, bool use_wupa) {
    memset(t, 0, sizeof(*t));

    mfrc522_status_t st = reqa_wupa(use_wupa, t->atqa);
    if (st != MFRC522_OK) {
        /* A card selected by a previous call is still ACTIVE and ignores
         * REQA/WUPA, so the first poll after a successful select fails. Halt it
         * (ACTIVE -> HALT) and retry: a halted card answers WUPA reliably. This
         * is why we require WUPA, not REQA, for the retry. */
        iso14443a_halt();
        st = reqa_wupa(true, t->atqa);
        if (st != MFRC522_OK) return st;
    }

    uint8_t uid_idx = 0;
    for (uint8_t level = 0; level < 3; level++) {
        uint8_t uid4[4];
        uint8_t sak;
        bool cascade;
        st = anticoll_select(level, uid4, &sak, &cascade);
        if (st != MFRC522_OK) return st;

        if (cascade) {
            /* uid4[0] is the cascade tag 0x88; the real UID is bytes 1..3. */
            t->uid[uid_idx++] = uid4[1];
            t->uid[uid_idx++] = uid4[2];
            t->uid[uid_idx++] = uid4[3];
        } else {
            t->uid[uid_idx++] = uid4[0];
            t->uid[uid_idx++] = uid4[1];
            t->uid[uid_idx++] = uid4[2];
            t->uid[uid_idx++] = uid4[3];
            t->sak = sak;
            t->uid_len = uid_idx;
            return MFRC522_OK;
        }
    }
    return MFRC522_ERR_COMM; /* more than 3 cascade levels is invalid */
}

void iso14443a_halt(void) {
    uint8_t buf[4] = { PICC_HLTA, 0x00, 0, 0 };
    uint8_t crc[2];
    if (mfrc522_calc_crc(buf, 2, crc) != MFRC522_OK) return;
    buf[2] = crc[0];
    buf[3] = crc[1];
    /* HLTA gets no reply; a timeout here is the expected success case. */
    uint8_t rx[1];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    mfrc522_transceive(buf, 4, 0, rx, &rx_len, &rx_bits);
    mfrc522_stop_crypto1();
}
