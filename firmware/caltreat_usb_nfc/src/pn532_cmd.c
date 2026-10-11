#include "pn532_cmd.h"

#include <string.h>

#include "pn532_frame.h"
#include "mfrc522.h"
#include "iso14443a.h"
#include "mifare_classic.h"

/* PN532 host command codes we handle. */
enum {
    CMD_DIAGNOSE            = 0x00,
    CMD_GETFIRMWAREVERSION  = 0x02,
    CMD_READREGISTER        = 0x06,
    CMD_WRITEREGISTER       = 0x08,
    CMD_SETPARAMETERS       = 0x12,
    CMD_SAMCONFIGURATION    = 0x14,
    CMD_RFCONFIGURATION     = 0x32,
    CMD_INDATAEXCHANGE      = 0x40,
    CMD_INCOMMUNICATETHRU   = 0x42,
    CMD_INDESELECT          = 0x44,
    CMD_INLISTPASSIVETARGET = 0x4A,
    CMD_INRELEASE           = 0x52,
};

/* PN532 error status bytes (subset, matching libnfc expectations). */
#define PN_ERR_NONE        0x00
#define PN_ERR_TIMEOUT     0x01
#define PN_ERR_CRC         0x02
#define PN_ERR_AUTH        0x14

/*
 * Fake PN532/CIU register shadow. libnfc's PN53x_UART init reads and writes a
 * handful of CIU registers; returning stored values (defaulting to 0x00) keeps
 * its probe happy without a real register map. Index by low byte of the 16-bit
 * PN532 register address.
 */
static uint8_t reg_shadow[256];

/* Remember the last selected target so InDataExchange can reuse its UID for the
 * MIFARE crypto1 authentication (which needs the first 4 UID bytes). */
static iso14443a_target_t g_target;
static bool g_have_target;

static uint8_t map_status(mfrc522_status_t st) {
    switch (st) {
    case MFRC522_OK:          return PN_ERR_NONE;
    case MFRC522_ERR_TIMEOUT: return PN_ERR_TIMEOUT;
    case MFRC522_ERR_CRC:     return PN_ERR_CRC;
    case MFRC522_ERR_COMM:    return PN_ERR_AUTH; /* most commonly an auth/NAK */
    default:                  return PN_ERR_TIMEOUT;
    }
}

static int handle_firmware(uint8_t *resp) {
    /* IC=0x32 (PN532), Ver=1, Rev=6, Support=0x07 (ISO14443A/B + 18092). */
    resp[0] = 0x32;
    resp[1] = 0x01;
    resp[2] = 0x06;
    resp[3] = 0x07;
    return 4;
}

static int handle_diagnose(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* Communication-line test (test 0x00) echoes the data back. Others: OK. */
    if (cmd_len >= 1 && cmd[1] == 0x00) {
        uint16_t n = (uint16_t)(cmd_len - 1);
        if (n > PN532_MAX_DATA - 1) n = PN532_MAX_DATA - 1;
        memcpy(resp, &cmd[1], n);
        return (int)n;
    }
    resp[0] = 0x00;
    return 1;
}

static int handle_readregister(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: [count*2 address bytes]. Reply: one value per address. */
    uint16_t n_bytes = (uint16_t)(cmd_len - 1); /* bytes after command code */
    uint16_t pairs = n_bytes / 2;
    uint16_t out = 0;
    for (uint16_t i = 0; i < pairs; i++) {
        uint8_t lo = cmd[1 + i * 2 + 1];
        resp[out++] = reg_shadow[lo];
    }
    return (int)out;
}

static int handle_writeregister(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: [addrH addrL value]*. Store into the shadow. */
    uint16_t n_bytes = (uint16_t)(cmd_len - 1);
    uint16_t triples = n_bytes / 3;
    for (uint16_t i = 0; i < triples; i++) {
        uint8_t lo  = cmd[1 + i * 3 + 1];
        uint8_t val = cmd[1 + i * 3 + 2];
        reg_shadow[lo] = val;
    }
    (void)resp;
    return 0; /* WriteRegister response carries no data */
}

static int handle_rfconfiguration(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd[1] = CfgItem. Only RF field on/off (0x01) touches hardware. */
    if (cmd_len >= 2 && cmd[1] == 0x01 && cmd_len >= 3) {
        if (cmd[2] & 0x01) mfrc522_antenna_on();
        else               mfrc522_antenna_off();
    }
    (void)resp;
    return 0;
}

static int handle_inlistpassivetarget(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: MaxTg, BrTy, [InitiatorData]. We support BrTy 0x00 (106 kbps A). */
    uint8_t br_ty = (cmd_len >= 3) ? cmd[2] : 0x00;
    if (br_ty != 0x00) {
        resp[0] = 0x00; /* NbTg = 0 */
        return 1;
    }

    iso14443a_target_t t;
    mfrc522_status_t st = iso14443a_select(&t, true /* WUPA */);
    if (st != MFRC522_OK) {
        resp[0] = 0x00; /* no target found */
        g_have_target = false;
        return 1;
    }

    g_target = t;
    g_have_target = true;

    /* Response layout (one target):
     *   NbTg, Tg(=1), ATQA(2, SENS_RES), SAK(SEL_RES), NFCIDLength, NFCID[...] */
    int i = 0;
    resp[i++] = 0x01;            /* NbTg */
    resp[i++] = 0x01;            /* Tg */
    resp[i++] = t.atqa[0];       /* SENS_RES byte order as libnfc expects */
    resp[i++] = t.atqa[1];
    resp[i++] = t.sak;
    resp[i++] = t.uid_len;
    memcpy(&resp[i], t.uid, t.uid_len);
    i += t.uid_len;
    return i;
}

static int handle_indataexchange(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: Tg, then a MIFARE command. We expect Tg=1. */
    if (cmd_len < 2) { resp[0] = PN_ERR_TIMEOUT; return 1; }
    const uint8_t *d = &cmd[2];          /* after cmd code + Tg */
    uint16_t dlen = (uint16_t)(cmd_len - 2);
    if (dlen < 1) { resp[0] = PN_ERR_TIMEOUT; return 1; }

    uint8_t mif = d[0];

    if (mif == MFC_KEY_A || mif == MFC_KEY_B) {
        /* AUTH: key_cmd, block, key[6], uid[4].
         * libnfc/Adafruit pass block then key then the 4 UID bytes. We fall back
         * to the UID captured at InListPassiveTarget if the frame omits it. */
        if (dlen < 8) { resp[0] = PN_ERR_AUTH; return 1; }
        uint8_t block = d[1];
        const uint8_t *key = &d[2];
        uint8_t uid[4];
        if (dlen >= 12) {
            memcpy(uid, &d[8], 4);
        } else if (g_have_target && g_target.uid_len >= 4) {
            memcpy(uid, g_target.uid, 4);
        } else {
            resp[0] = PN_ERR_AUTH; return 1;
        }
        mfrc522_status_t st = mfc_auth(mif, block, key, uid);
        resp[0] = map_status(st);
        return 1;
    }

    if (mif == 0x30) { /* READ */
        if (dlen < 2) { resp[0] = PN_ERR_TIMEOUT; return 1; }
        uint8_t block = d[1];
        uint8_t out16[16];
        mfrc522_status_t st = mfc_read(block, out16);
        resp[0] = map_status(st);
        if (st != MFRC522_OK) return 1;
        memcpy(&resp[1], out16, 16);
        return 17;
    }

    if (mif == 0xA0) { /* WRITE */
        if (dlen < 18) { resp[0] = PN_ERR_TIMEOUT; return 1; }
        uint8_t block = d[1];
        mfrc522_status_t st = mfc_write(block, &d[2]);
        resp[0] = map_status(st);
        return 1;
    }

    /* Unknown MIFARE command: pass it through raw with CRC and return the data.
     * The MFRC522 FIFO is 64 bytes, so a single transceive can't return more. */
    uint8_t rx[64];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    /* Build CRC over the raw command. */
    uint8_t tx[PN532_MAX_DATA];
    if (dlen + 2 > (int)sizeof(tx)) { resp[0] = PN_ERR_TIMEOUT; return 1; }
    memcpy(tx, d, dlen);
    uint8_t crc[2];
    if (mfrc522_calc_crc(tx, (uint8_t)dlen, crc) != MFRC522_OK) { resp[0] = PN_ERR_TIMEOUT; return 1; }
    tx[dlen] = crc[0];
    tx[dlen + 1] = crc[1];
    mfrc522_status_t st = mfrc522_transceive(tx, (uint8_t)(dlen + 2), 0, &rx[0], &rx_len, &rx_bits);
    resp[0] = map_status(st);
    if (st != MFRC522_OK) return 1;
    memcpy(&resp[1], rx, rx_len);
    return rx_len + 1;
}

static int handle_incommunicatethru(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* Raw transceive: cmd data (after code) is sent with CRC appended; reply is
     * [status][received bytes without CRC]. Used by nfcpy / libnfc for custom
     * frames. */
    const uint8_t *d = &cmd[1];
    uint16_t dlen = (uint16_t)(cmd_len - 1);
    if (dlen == 0 || dlen + 2 > PN532_MAX_DATA) { resp[0] = PN_ERR_TIMEOUT; return 1; }

    uint8_t tx[PN532_MAX_DATA];
    memcpy(tx, d, dlen);
    uint8_t crc[2];
    if (mfrc522_calc_crc(tx, (uint8_t)dlen, crc) != MFRC522_OK) { resp[0] = PN_ERR_TIMEOUT; return 1; }
    tx[dlen] = crc[0];
    tx[dlen + 1] = crc[1];

    uint8_t rx[64]; /* MFRC522 FIFO is 64 bytes */
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    mfrc522_status_t st = mfrc522_transceive(tx, (uint8_t)(dlen + 2), 0, rx, &rx_len, &rx_bits);
    resp[0] = map_status(st);
    if (st != MFRC522_OK) return 1;
    /* Strip the trailing 2 CRC bytes if present. */
    uint16_t n = (rx_len >= 2) ? (uint16_t)(rx_len - 2) : 0;
    memcpy(&resp[1], rx, n);
    return (int)n + 1;
}

int pn532_cmd_handle(const uint8_t *cmd, uint16_t cmd_len,
                     uint8_t *resp, uint8_t *resp_cmd) {
    if (cmd_len < 1) return -1;
    uint8_t code = cmd[0];
    *resp_cmd = (uint8_t)(code + 1);

    switch (code) {
    case CMD_GETFIRMWAREVERSION: return handle_firmware(resp);
    case CMD_DIAGNOSE:           return handle_diagnose(cmd, cmd_len, resp);
    case CMD_SAMCONFIGURATION:   return 0; /* ack, no data */
    case CMD_SETPARAMETERS:      return 0;
    case CMD_RFCONFIGURATION:    return handle_rfconfiguration(cmd, cmd_len, resp);
    case CMD_READREGISTER:       return handle_readregister(cmd, cmd_len, resp);
    case CMD_WRITEREGISTER:      return handle_writeregister(cmd, cmd_len, resp);
    case CMD_INLISTPASSIVETARGET:return handle_inlistpassivetarget(cmd, cmd_len, resp);
    case CMD_INDATAEXCHANGE:     return handle_indataexchange(cmd, cmd_len, resp);
    case CMD_INCOMMUNICATETHRU:  return handle_incommunicatethru(cmd, cmd_len, resp);
    case CMD_INDESELECT:
    case CMD_INRELEASE:
        iso14443a_halt();
        g_have_target = false;
        resp[0] = PN_ERR_NONE;
        return 1;
    default:
        /* Unknown command: respond with a single error status byte so the host
         * doesn't hang. (libnfc treats a 0x7F frame as a syntax error; a normal
         * response frame with an error status is also accepted for most paths.) */
        resp[0] = PN_ERR_TIMEOUT;
        return 1;
    }
}
