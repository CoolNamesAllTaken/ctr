#include "pn532_cmd.h"

#include <string.h>

#include "pico/time.h"

#include "pn532_frame.h"
#include "mfrc522.h"
#include "iso14443a.h"
#include "mifare_classic.h"

/* PN532 host command codes we handle. */
enum {
    CMD_DIAGNOSE            = 0x00,
    CMD_GETFIRMWAREVERSION  = 0x02,
    CMD_GETGENERALSTATUS    = 0x04,
    CMD_READREGISTER        = 0x06,
    CMD_WRITEREGISTER       = 0x08,
    CMD_READGPIO            = 0x0C,
    CMD_WRITEGPIO           = 0x0E,
    CMD_SETSERIALBAUDRATE   = 0x10,
    CMD_SETPARAMETERS       = 0x12,
    CMD_SAMCONFIGURATION    = 0x14,
    CMD_POWERDOWN           = 0x16,
    CMD_RFCONFIGURATION     = 0x32,
    CMD_INDATAEXCHANGE      = 0x40,
    CMD_INCOMMUNICATETHRU   = 0x42,
    CMD_INDESELECT          = 0x44,
    CMD_INLISTPASSIVETARGET = 0x4A,
    CMD_INRELEASE           = 0x52,
    CMD_INSELECT            = 0x54,
    CMD_INAUTOPOLL          = 0x60,
};

/* PN532 error status bytes (subset, matching libnfc expectations). */
#define PN_ERR_NONE        0x00
#define PN_ERR_TIMEOUT     0x01
#define PN_ERR_CRC         0x02
#define PN_ERR_COLLISION   0x06
#define PN_ERR_PARAM       0x10
#define PN_ERR_AUTH        0x14 /* MIFARE auth error; also used for a NAK */
#define PN_ERR_CONTEXT     0x27 /* command not acceptable in this context */

/* SetParameters flags. */
#define PARAM_AUTO_RATS    0x10

/*
 * PN532 register shadow. Hosts read and write CIU registers (0x6301-0x633F) and
 * SFRs (0xFF00-0xFFFF); we keep them here and honour the few that change how
 * raw frames are sent (see handle_incommunicatethru). Other addresses read as
 * 0x00 and ignore writes.
 */
#define REG_CIU_TXMODE      0x6302 /* bit 7: TxCRCEn */
#define REG_CIU_RXMODE      0x6303 /* bit 7: RxCRCEn */
#define REG_CIU_CONTROL     0x630C /* bits 0-2: RxLastBits */
#define REG_CIU_STATUS2     0x6338 /* bit 3: MFCrypto1On */
#define REG_CIU_BITFRAMING  0x633D /* bits 0-2: TxLastBits */
#define REG_SFR_P3          0xFFB0
#define REG_SFR_P7          0xFFF7

/* CRC on in both directions, as libnfc and pn532.js configure it at init. */
static uint8_t ciu_shadow[0x40] = { [0x02] = 0x80, [0x03] = 0x80 };
static uint8_t sfr_shadow[0x100] = { [0xB0] = 0x3F, [0xF7] = 0x06 };

static uint8_t g_params = 0x14;           /* fAutomaticATR_RES | fAutomaticRATS */
static uint8_t g_mx_rty_passive = 0xFF;   /* RFConfiguration MxRtyPassiveActivation */
static uint8_t g_last_err;                /* for GetGeneralStatus */

/* The target found by InListPassiveTarget / InAutoPoll. "Active" means
 * selected; InDeselect halts it but keeps it listed so InSelect can wake it. */
static iso14443a_target_t g_target;
static uint8_t g_ats[64];
static uint8_t g_ats_len;
static bool g_have_target;
static bool g_target_active;

static uint8_t *reg_slot(uint16_t addr) {
    if (addr >= 0x6300 && addr < 0x6340) return &ciu_shadow[addr - 0x6300];
    if (addr >= 0xFF00) return &sfr_shadow[addr - 0xFF00];
    return NULL;
}

static uint8_t reg_read(uint16_t addr) {
    if (addr == REG_CIU_STATUS2) {
        /* Report the real crypto1 state. */
        return (uint8_t)((ciu_shadow[0x38] & ~0x08) | (mfrc522_read_reg(Status2Reg) & 0x08));
    }
    uint8_t *slot = reg_slot(addr);
    return slot ? *slot : 0x00;
}

static void reg_write(uint16_t addr, uint8_t val) {
    if (addr == REG_CIU_STATUS2 && !(val & 0x08)) mfrc522_stop_crypto1();
    uint8_t *slot = reg_slot(addr);
    if (slot) *slot = val;
}

static uint8_t map_status(mfrc522_status_t st) {
    switch (st) {
    case MFRC522_OK:            return PN_ERR_NONE;
    case MFRC522_ERR_TIMEOUT:   return PN_ERR_TIMEOUT;
    case MFRC522_ERR_CRC:       return PN_ERR_CRC;
    case MFRC522_ERR_COLLISION: return PN_ERR_COLLISION;
    case MFRC522_ERR_PARAM:     return PN_ERR_PARAM;
    case MFRC522_ERR_COMM:      return PN_ERR_AUTH; /* most commonly an auth/NAK */
    default:                    return PN_ERR_TIMEOUT;
    }
}

/* Halt the current card, using S(DESELECT) first if it's in ISO14443-4 mode. */
static void halt_target(void) {
    if (g_target_active && g_ats_len) iso14443a_deselect_l4();
    iso14443a_halt();
    g_target_active = false;
}

/*
 * One 106 kbps type A activation attempt. When want_len is non-zero only a card
 * with that UID counts (InListPassiveTarget InitiatorData, InSelect). Sends RATS
 * to ISO14443-4 cards when automatic RATS is enabled, like the PN532.
 */
static bool activate_target(const uint8_t *want_uid, uint8_t want_len) {
    iso14443a_target_t t;
    mfrc522_antenna_on(); /* the PN532 switches the field on as needed */
    if (iso14443a_select(&t, true) != MFRC522_OK) return false;
    if (want_len && (want_len != t.uid_len || memcmp(want_uid, t.uid, want_len) != 0)) {
        iso14443a_halt();
        return false;
    }

    uint8_t ats[sizeof(g_ats)];
    uint8_t ats_len = 0;
    if ((t.sak & 0x20) && (g_params & PARAM_AUTO_RATS)) {
        if (iso14443a_rats(ats, sizeof(ats), &ats_len) != MFRC522_OK) {
            iso14443a_halt();
            return false;
        }
    }

    g_target = t;
    memcpy(g_ats, ats, ats_len);
    g_ats_len = ats_len;
    g_have_target = true;
    g_target_active = true;
    return true;
}

/* TargetData for 106 kbps type A (InListPassiveTarget / InAutoPoll):
 * Tg, SENS_RES (2), SEL_RES, NFCIDLength, NFCID, [ATS incl. its TL byte]. */
static int write_target_data(uint8_t *out) {
    int i = 0;
    out[i++] = 0x01;              /* Tg */
    out[i++] = g_target.atqa[1];  /* the PN532 reports SENS_RES MSB first */
    out[i++] = g_target.atqa[0];
    out[i++] = g_target.sak;
    out[i++] = g_target.uid_len;
    memcpy(&out[i], g_target.uid, g_target.uid_len);
    i += g_target.uid_len;
    memcpy(&out[i], g_ats, g_ats_len);
    i += g_ats_len;
    return i;
}

/* Wait ms while keeping USB serviced. Returns false if the host aborted. */
static bool wait_ms_or_abort(uint32_t ms) {
    absolute_time_t until = make_timeout_time_ms(ms);
    while (!time_reached(until)) {
        if (pn532_host_abort()) return false;
    }
    return true;
}

/*
 * Turn a card's reply into [status][data] at resp. A 4-bit reply is a MIFARE
 * ACK/NAK nibble: with nibble_status it becomes the status (ACK -> OK with no
 * data, NAK -> error), otherwise it is returned as data. Full-byte replies have
 * their CRC_A checked and stripped when crc is set.
 */
static int card_reply(uint8_t *resp, const uint8_t *rx, uint8_t rx_len,
                      uint8_t rx_bits, bool crc, bool nibble_status) {
    if (rx_len == 1 && rx_bits == 4) {
        if (!nibble_status) {
            resp[0] = PN_ERR_NONE;
            resp[1] = rx[0] & 0x0F;
            return 2;
        }
        resp[0] = ((rx[0] & 0x0F) == 0x0A) ? PN_ERR_NONE : PN_ERR_AUTH;
        return 1;
    }
    uint8_t n = rx_len;
    if (crc) {
        if (mfrc522_check_crc(rx, rx_len) != MFRC522_OK) {
            resp[0] = PN_ERR_CRC;
            return 1;
        }
        n = (uint8_t)(rx_len - 2);
    }
    resp[0] = PN_ERR_NONE;
    memcpy(&resp[1], rx, n);
    return n + 1;
}

static int handle_firmware(uint8_t *resp) {
    /* IC=0x32 (PN532), Ver=1, Rev=6, Support=0x01 (ISO14443A only: the RC522
     * can't do 14443B or ISO18092). */
    resp[0] = 0x32;
    resp[1] = 0x01;
    resp[2] = 0x06;
    resp[3] = 0x01;
    return 4;
}

static int handle_diagnose(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* Communication-line test (test 0x00) echoes NumTst + data back. Other
     * tests (ROM/RAM checks etc.) report success. */
    if (cmd_len < 2) return PN532_SYNTAX_ERROR;
    if (cmd[1] == 0x00) {
        uint16_t n = (uint16_t)(cmd_len - 1);
        memcpy(resp, &cmd[1], n);
        return (int)n;
    }
    resp[0] = 0x00;
    return 1;
}

static int handle_getgeneralstatus(uint8_t *resp) {
    /* Err, Field (external field detected: never, we're initiator only), NbTg,
     * [Tg, BrRx, BrTx, Type] per target, SAM status. */
    int i = 0;
    resp[i++] = g_last_err;
    resp[i++] = 0x00;
    resp[i++] = g_target_active ? 1 : 0;
    if (g_target_active) {
        resp[i++] = 0x01; /* Tg */
        resp[i++] = 0x00; /* 106 kbps */
        resp[i++] = 0x00;
        resp[i++] = 0x00; /* MIFARE / ISO14443-3 type A */
    }
    resp[i++] = 0x00;     /* SAM status */
    return i;
}

static int handle_readregister(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: [addrH addrL]*. Reply: one value per address. */
    uint16_t pairs = (uint16_t)((cmd_len - 1) / 2);
    if (pairs == 0) return PN532_SYNTAX_ERROR;
    for (uint16_t i = 0; i < pairs; i++) {
        uint16_t addr = (uint16_t)((cmd[1 + i * 2] << 8) | cmd[2 + i * 2]);
        resp[i] = reg_read(addr);
    }
    return (int)pairs;
}

static int handle_writeregister(const uint8_t *cmd, uint16_t cmd_len) {
    /* cmd: [addrH addrL value]*. */
    uint16_t triples = (uint16_t)((cmd_len - 1) / 3);
    if (triples == 0) return PN532_SYNTAX_ERROR;
    for (uint16_t i = 0; i < triples; i++) {
        uint16_t addr = (uint16_t)((cmd[1 + i * 3] << 8) | cmd[2 + i * 3]);
        reg_write(addr, cmd[3 + i * 3]);
    }
    return 0; /* WriteRegister response carries no data */
}

static int handle_readgpio(uint8_t *resp) {
    resp[0] = sfr_shadow[REG_SFR_P3 & 0xFF];
    resp[1] = sfr_shadow[REG_SFR_P7 & 0xFF];
    resp[2] = 0x00; /* I0I1 */
    return 3;
}

static int handle_writegpio(const uint8_t *cmd, uint16_t cmd_len) {
    /* Bit 7 of each byte says whether that port's value is valid. */
    if (cmd_len >= 2 && (cmd[1] & 0x80)) sfr_shadow[REG_SFR_P3 & 0xFF] = cmd[1] & 0x3F;
    if (cmd_len >= 3 && (cmd[2] & 0x80)) sfr_shadow[REG_SFR_P7 & 0xFF] = cmd[2] & 0x06;
    return 0;
}

static int handle_rfconfiguration(const uint8_t *cmd, uint16_t cmd_len) {
    /* cmd[1] = CfgItem. 0x01: RF field on/off. 0x05: MaxRetries
     * (MxRtyATR, MxRtyPSL, MxRtyPassiveActivation). Others are accepted. */
    if (cmd_len < 2) return PN532_SYNTAX_ERROR;
    if (cmd[1] == 0x01 && cmd_len >= 3) {
        if (cmd[2] & 0x01) mfrc522_antenna_on();
        else               mfrc522_antenna_off();
    } else if (cmd[1] == 0x05 && cmd_len >= 5) {
        g_mx_rty_passive = cmd[4];
    }
    return 0;
}

/* InitiatorData for type A is the UID as sent over the air, so 7- and 10-byte
 * UIDs carry a 0x88 cascade tag before each incomplete level (libnfc and
 * pn532.js send it that way). Strip the tags to get the plain UID. */
static uint8_t strip_cascade_tags(const uint8_t *in, uint8_t len, uint8_t out[10]) {
    if (len == 8 && in[0] == 0x88) {
        memcpy(out, &in[1], 7);
        return 7;
    }
    if (len == 12 && in[0] == 0x88 && in[4] == 0x88) {
        memcpy(out, &in[1], 3);
        memcpy(&out[3], &in[5], 7);
        return 10;
    }
    if (len > 10) return 0xFF; /* not a valid UID; caller rejects it */
    memcpy(out, in, len);
    return len;
}

static int handle_inlistpassivetarget(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: MaxTg, BrTy, [InitiatorData]. The RC522 only does BrTy 0x00 (106
     * kbps type A); other types behave as if no such card is in the field.
     * InitiatorData for type A is a UID to look for. */
    if (cmd_len < 3 || cmd[1] < 1 || cmd[1] > 2) return PN532_SYNTAX_ERROR;
    uint8_t br_ty = cmd[2];
    uint16_t init_len = (uint16_t)(cmd_len - 3);
    if (init_len > 12) return PN532_SYNTAX_ERROR;
    uint8_t want_uid[10];
    uint8_t want_len = strip_cascade_tags(&cmd[3], (uint8_t)init_len, want_uid);
    if (want_len > sizeof(want_uid)) return PN532_SYNTAX_ERROR;

    if (g_target_active) halt_target();
    g_have_target = false;

    /* MxRtyPassiveActivation: 0xFF searches until a card appears, n makes n+1
     * attempts. Any new data from the host aborts the search with no reply. */
    for (uint16_t attempt = 0;; attempt++) {
        if (br_ty == 0x00 && activate_target(want_uid, want_len)) {
            resp[0] = 0x01; /* NbTg */
            return 1 + write_target_data(&resp[1]);
        }
        if (g_mx_rty_passive != 0xFF && attempt >= g_mx_rty_passive) break;
        if (pn532_host_abort()) return PN532_NO_RESPONSE;
    }
    resp[0] = 0x00; /* NbTg: nothing found */
    return 1;
}

/* InAutoPoll target type to report for the activated card, or -1 if none of
 * the requested types fits. 0x00 is generic 106 kbps passive, 0x10 MIFARE,
 * 0x20 ISO14443-4A. */
static int autopoll_type(const uint8_t *types, uint16_t n_types) {
    for (uint16_t i = 0; i < n_types; i++) {
        if (types[i] == 0x00 || types[i] == 0x10) return types[i];
        if (types[i] == 0x20 && (g_target.sak & 0x20)) return 0x20;
    }
    return -1;
}

static int handle_inautopoll(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: PollNr (0xFF = forever), Period (x150 ms), Type1..TypeN.
     * Reply: NbTg, then Type, TargetDataLength, TargetData per target. */
    if (cmd_len < 4 || cmd_len > 18 || cmd[1] == 0x00 || cmd[2] == 0x00) {
        return PN532_SYNTAX_ERROR;
    }
    uint8_t poll_nr = cmd[1];
    uint32_t period_ms = cmd[2] * 150u;
    const uint8_t *types = &cmd[3];
    uint16_t n_types = (uint16_t)(cmd_len - 3);

    if (g_target_active) halt_target();
    g_have_target = false;

    for (uint16_t round = 0; poll_nr == 0xFF || round < poll_nr; round++) {
        if (activate_target(NULL, 0)) {
            int type = autopoll_type(types, n_types);
            if (type >= 0) {
                resp[0] = 0x01;
                resp[1] = (uint8_t)type;
                int n = write_target_data(&resp[3]);
                resp[2] = (uint8_t)n;
                return 3 + n;
            }
            halt_target();
            g_have_target = false;
        }
        if (!wait_ms_or_abort(period_ms)) return PN532_NO_RESPONSE;
    }
    resp[0] = 0x00;
    return 1;
}

static int handle_indataexchange(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* cmd: Tg, DataOut. Tg must be the listed target (1). */
    if (cmd_len < 3) return PN532_SYNTAX_ERROR;
    if (!g_have_target || (cmd[1] & 0x3F) != 0x01) {
        resp[0] = PN_ERR_CONTEXT;
        return 1;
    }
    /* A target halted by InDeselect is woken up again first. */
    if (!g_target_active && !activate_target(g_target.uid, g_target.uid_len)) {
        resp[0] = PN_ERR_TIMEOUT;
        return 1;
    }

    const uint8_t *d = &cmd[2];          /* after cmd code + Tg */
    uint16_t dlen = (uint16_t)(cmd_len - 2);
    uint8_t op = d[0];

    if (op == MFC_KEY_A || op == MFC_KEY_B) {
        /* AUTH: op, block, key[6], uid. Auth uses the last 4 UID bytes (for a
         * 7-byte UID, per NXP AN10927), whether the host passes 4 bytes, the
         * whole UID, or nothing, in which case the listed target's is used. */
        if (dlen < 8) return PN532_SYNTAX_ERROR;
        const uint8_t *uid;
        if (dlen >= 12) {
            uid = &d[dlen - 4];
        } else if (g_target.uid_len >= 4) {
            uid = &g_target.uid[g_target.uid_len - 4];
        } else {
            resp[0] = PN_ERR_AUTH;
            return 1;
        }
        resp[0] = map_status(mfc_auth(op, d[1], &d[2], uid));
        return 1;
    }

    if (op == 0x30) { /* READ */
        if (dlen < 2) return PN532_SYNTAX_ERROR;
        mfrc522_status_t st = mfc_read(d[1], &resp[1]);
        resp[0] = map_status(st);
        return (st == MFRC522_OK) ? 17 : 1;
    }

    if (op == 0xA0) { /* WRITE (16 bytes) */
        if (dlen < 18) return PN532_SYNTAX_ERROR;
        resp[0] = map_status(mfc_write(d[1], &d[2]));
        return 1;
    }

    if (op == MFC_CMD_INCREMENT || op == MFC_CMD_DECREMENT || op == MFC_CMD_RESTORE) {
        /* op, block, value[4] (restore's operand is a don't-care). */
        if (dlen < 2) return PN532_SYNTAX_ERROR;
        static const uint8_t zero[4] = { 0 };
        const uint8_t *value = (dlen >= 6) ? &d[2] : zero;
        resp[0] = map_status(mfc_value(op, d[1], value));
        return 1;
    }

    if (op == MFC_CMD_TRANSFER) {
        if (dlen < 2) return PN532_SYNTAX_ERROR;
        resp[0] = map_status(mfc_transfer(d[1]));
        return 1;
    }

    /* ISO14443-4 targets need I-block framing, which isn't implemented. */
    if (g_ats_len) {
        resp[0] = PN_ERR_CONTEXT;
        return 1;
    }

    /* Anything else (Ultralight/NTAG commands etc.) is sent as-is with CRC_A,
     * and the reply's CRC is checked and stripped. The MFRC522 FIFO (64 bytes)
     * bounds both directions. */
    uint8_t tx[64];
    if (dlen + 2 > sizeof(tx)) { resp[0] = PN_ERR_PARAM; return 1; }
    memcpy(tx, d, dlen);
    mfrc522_status_t st = mfrc522_append_crc(tx, (uint8_t)dlen);
    if (st != MFRC522_OK) { resp[0] = map_status(st); return 1; }

    uint8_t rx[64];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    st = mfrc522_transceive(tx, (uint8_t)(dlen + 2), 0, rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) { resp[0] = map_status(st); return 1; }
    return card_reply(resp, rx, rx_len, rx_bits, true, true);
}

static int handle_incommunicatethru(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    /* Raw transceive, shaped by the CIU registers the host has written:
     * TxMode/RxMode bit 7 turn CRC_A generation/checking on or off, and
     * BitFraming bits 0-2 send a short final byte (e.g. 7-bit frames). The
     * reply's valid bits in its last byte are left in CIU_Control for the host
     * to read, as on a real PN532. Parity can't be turned off. */
    const uint8_t *d = &cmd[1];
    uint16_t dlen = (uint16_t)(cmd_len - 1);
    if (dlen == 0) return PN532_SYNTAX_ERROR;

    bool tx_crc = reg_read(REG_CIU_TXMODE) & 0x80;
    bool rx_crc = reg_read(REG_CIU_RXMODE) & 0x80;
    uint8_t tx_last_bits = reg_read(REG_CIU_BITFRAMING) & 0x07;

    uint8_t tx[64];
    uint16_t tx_len = (uint16_t)(dlen + (tx_crc ? 2 : 0));
    if (tx_len > sizeof(tx)) { resp[0] = PN_ERR_PARAM; return 1; }
    memcpy(tx, d, dlen);
    if (tx_crc) {
        mfrc522_status_t st = mfrc522_append_crc(tx, (uint8_t)dlen);
        if (st != MFRC522_OK) { resp[0] = map_status(st); return 1; }
    }

    uint8_t rx[64];
    uint8_t rx_len = sizeof(rx);
    uint8_t rx_bits = 0;
    mfrc522_status_t st = mfrc522_transceive(tx, (uint8_t)tx_len, tx_last_bits,
                                             rx, &rx_len, &rx_bits);
    if (st != MFRC522_OK) { resp[0] = map_status(st); return 1; }

    reg_write(REG_CIU_CONTROL, (uint8_t)((ciu_shadow[0x0C] & ~0x07) | (rx_bits & 0x07)));
    return card_reply(resp, rx, rx_len, rx_bits, rx_crc && rx_bits == 0, false);
}

static int handle_inselect(const uint8_t *cmd, uint16_t cmd_len, uint8_t *resp) {
    if (cmd_len < 2) return PN532_SYNTAX_ERROR;
    if (!g_have_target || (cmd[1] & 0x3F) != 0x01) {
        resp[0] = PN_ERR_CONTEXT;
        return 1;
    }
    if (!g_target_active && !activate_target(g_target.uid, g_target.uid_len)) {
        resp[0] = PN_ERR_TIMEOUT;
        return 1;
    }
    resp[0] = PN_ERR_NONE;
    return 1;
}

int pn532_cmd_handle(const uint8_t *cmd, uint16_t cmd_len,
                     uint8_t *resp, uint8_t *resp_cmd) {
    if (cmd_len < 1) return PN532_SYNTAX_ERROR;
    uint8_t code = cmd[0];
    *resp_cmd = (uint8_t)(code + 1);

    int n;
    switch (code) {
    case CMD_GETFIRMWAREVERSION: return handle_firmware(resp);
    case CMD_DIAGNOSE:           return handle_diagnose(cmd, cmd_len, resp);
    case CMD_GETGENERALSTATUS:   return handle_getgeneralstatus(resp);
    case CMD_SAMCONFIGURATION:   return 0; /* no data */
    case CMD_SETSERIALBAUDRATE:  return 0; /* meaningless over USB CDC */
    case CMD_SETPARAMETERS:
        if (cmd_len < 2) return PN532_SYNTAX_ERROR;
        g_params = cmd[1];
        return 0;
    case CMD_POWERDOWN:
        /* Nothing to power down; the next frame (with its wakeup preamble) is
         * handled as usual. */
        resp[0] = PN_ERR_NONE;
        return 1;
    case CMD_RFCONFIGURATION:    return handle_rfconfiguration(cmd, cmd_len);
    case CMD_READREGISTER:       return handle_readregister(cmd, cmd_len, resp);
    case CMD_WRITEREGISTER:      return handle_writeregister(cmd, cmd_len);
    case CMD_READGPIO:           return handle_readgpio(resp);
    case CMD_WRITEGPIO:          return handle_writegpio(cmd, cmd_len);
    case CMD_INLISTPASSIVETARGET:return handle_inlistpassivetarget(cmd, cmd_len, resp);
    case CMD_INAUTOPOLL:         return handle_inautopoll(cmd, cmd_len, resp);
    case CMD_INSELECT:           return handle_inselect(cmd, cmd_len, resp);
    case CMD_INDATAEXCHANGE:
        n = handle_indataexchange(cmd, cmd_len, resp);
        if (n > 0) g_last_err = resp[0];
        return n;
    case CMD_INCOMMUNICATETHRU:
        n = handle_incommunicatethru(cmd, cmd_len, resp);
        if (n > 0) g_last_err = resp[0];
        return n;
    case CMD_INDESELECT:
    case CMD_INRELEASE:
        if (cmd_len < 2) return PN532_SYNTAX_ERROR;
        if (g_target_active) halt_target();
        if (code == CMD_INRELEASE) g_have_target = false;
        resp[0] = PN_ERR_NONE;
        return 1;
    default:
        /* Target mode, DEP, etc.: not supported. A real PN532 answers commands
         * it doesn't know with the syntax error frame. */
        return PN532_SYNTAX_ERROR;
    }
}
