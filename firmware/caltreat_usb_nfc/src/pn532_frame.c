#include "pn532_frame.h"

#include <string.h>

/*
 * Parser states. After the 00 00 FF start code we peek one byte to classify the
 * frame:
 *   0x00 -> next must be 0xFF => ACK   (00 00 FF 00 FF 00)
 *   0xFF -> next 0x00 => NACK           (00 00 FF FF 00 00)
 *           next 0xFF => extended frame (00 00 FF FF FF LENH LENL LCS ...)
 *           next 0x01 => normal frame with LEN=0xFF, LCS=0x01
 *   else -> normal LEN byte
 */
enum {
    S_PREAMBLE = 0, /* skipping noise, waiting for a 0x00 */
    S_START_FF,     /* saw 0x00(s), waiting for 0xFF */
    S_CLASSIFY,     /* first byte after 0xFF */
    S_ACK_FF,       /* ACK: expect 0xFF after the 0x00 */
    S_EXT_OR_NACK,  /* saw FF after the start code: NACK, extended or LEN=FF */
    S_EXT_LENH,     /* extended length high */
    S_EXT_LENL,     /* extended length low */
    S_EXT_LCS,      /* extended length checksum */
    S_LCS,          /* normal length checksum */
    S_PAYLOAD,      /* TFI + data */
    S_DCS,          /* data checksum */
    S_DONE_ACK,     /* consume postamble, then report ACK */
};

void pn532_parser_reset(pn532_parser_t *p) {
    memset(p, 0, sizeof(*p));
    p->state = S_PREAMBLE;
}

bool pn532_parser_idle(const pn532_parser_t *p) {
    return p->state == S_PREAMBLE;
}

pn532_rx_result_t pn532_parser_push(pn532_parser_t *p, uint8_t b,
                                    const uint8_t **out_cmd, uint16_t *out_len) {
    switch (p->state) {
    case S_PREAMBLE:
        if (b == 0x00) p->state = S_START_FF;
        return PN532_RX_INCOMPLETE;

    case S_START_FF:
        if (b == 0x00) return PN532_RX_INCOMPLETE; /* run of 0x00 */
        if (b == 0xFF) { p->state = S_CLASSIFY; return PN532_RX_INCOMPLETE; }
        p->state = S_PREAMBLE; /* not a start code */
        return PN532_RX_INCOMPLETE;

    case S_CLASSIFY:
        if (b == 0x00) {           /* possible ACK: 00 00 FF 00 FF 00 */
            p->state = S_ACK_FF;
            return PN532_RX_INCOMPLETE;
        }
        if (b == 0xFF) {           /* NACK or extended frame */
            p->state = S_EXT_OR_NACK;
            return PN532_RX_INCOMPLETE;
        }
        /* normal frame: this byte is LEN */
        p->len = b;
        p->extended = false;
        p->state = S_LCS;
        return PN532_RX_INCOMPLETE;

    case S_ACK_FF:
        if (b == 0xFF) {
            p->state = S_DONE_ACK; /* valid ACK; swallow trailing 0x00 */
            return PN532_RX_INCOMPLETE;
        }
        pn532_parser_reset(p);
        return PN532_RX_ERROR;

    case S_DONE_ACK:
        pn532_parser_reset(p);
        return PN532_RX_ACK;

    case S_EXT_OR_NACK:
        if (b == 0x00) {
            /* NACK: 00 00 FF FF 00 00 — one more 0x00 postamble follows but we
             * can report now; the stray 0x00 is swallowed by S_PREAMBLE. */
            pn532_parser_reset(p);
            return PN532_RX_NACK;
        }
        if (b == 0xFF) {           /* second FF of the extended-frame marker */
            p->state = S_EXT_LENH;
            return PN532_RX_INCOMPLETE;
        }
        if (b == 0x01) {           /* normal frame: LEN=0xFF, LCS=0x01 */
            p->len = 0xFF;
            p->extended = false;
            p->idx = 0;
            p->sum = 0;
            p->state = S_PAYLOAD;
            return PN532_RX_INCOMPLETE;
        }
        pn532_parser_reset(p);
        return PN532_RX_ERROR;

    case S_EXT_LENH:
        p->ext_lenh = b;
        p->state = S_EXT_LENL;
        return PN532_RX_INCOMPLETE;

    case S_EXT_LENL:
        p->ext_lenl = b;
        p->len = (uint16_t)((p->ext_lenh << 8) | b);
        p->state = S_EXT_LCS;
        return PN532_RX_INCOMPLETE;

    case S_EXT_LCS:
        if (((p->ext_lenh + p->ext_lenl + b) & 0xFF) != 0 ||
            p->len == 0 || p->len > PN532_MAX_DATA) {
            pn532_parser_reset(p);
            return PN532_RX_ERROR;
        }
        p->extended = true;
        p->idx = 0;
        p->sum = 0;
        p->state = S_PAYLOAD;
        return PN532_RX_INCOMPLETE;

    case S_LCS:
        if (((p->len + b) & 0xFF) != 0 || p->len == 0 || p->len > PN532_MAX_DATA) {
            pn532_parser_reset(p);
            return PN532_RX_ERROR;
        }
        p->idx = 0;
        p->sum = 0;
        p->state = S_PAYLOAD;
        return PN532_RX_INCOMPLETE;

    case S_PAYLOAD:
        if (p->idx < PN532_MAX_DATA) p->buf[p->idx] = b;
        p->sum = (uint8_t)(p->sum + b);
        p->idx++;
        if (p->idx >= p->len) p->state = S_DCS;
        return PN532_RX_INCOMPLETE;

    case S_DCS:
        if (((p->sum + b) & 0xFF) != 0) {
            pn532_parser_reset(p);
            return PN532_RX_ERROR;
        }
        /* Full frame received. buf[0] is TFI (0xD4). Don't wait for postamble;
         * report now and let any trailing 0x00 be skipped as preamble. */
        if (p->len < 1 || p->buf[0] != PN532_HOST_TFI) {
            pn532_parser_reset(p);
            return PN532_RX_ERROR;
        }
        *out_cmd = &p->buf[1];
        *out_len = (uint16_t)(p->len - 1);
        /* Keep buf valid for the caller: reset state but not the buffer. */
        p->state = S_PREAMBLE;
        p->idx = 0;
        p->sum = 0;
        return PN532_RX_FRAME;

    default:
        pn532_parser_reset(p);
        return PN532_RX_ERROR;
    }
}

size_t pn532_build_ack(uint8_t *out) {
    static const uint8_t ack[6] = { 0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00 };
    memcpy(out, ack, sizeof(ack));
    return sizeof(ack);
}

size_t pn532_build_error(uint8_t *out) {
    static const uint8_t err[8] = { 0x00, 0x00, 0xFF, 0x01, 0xFF, 0x7F, 0x81, 0x00 };
    memcpy(out, err, sizeof(err));
    return sizeof(err);
}

size_t pn532_build_response(uint8_t *out, uint8_t cmd,
                            const uint8_t *data, uint16_t data_len) {
    size_t i = 0;
    uint16_t len = (uint16_t)(data_len + 2); /* TFI + cmd + data */

    out[i++] = 0x00;
    out[i++] = 0x00;
    out[i++] = 0xFF;

    if (len > 255) {
        out[i++] = 0xFF;
        out[i++] = 0xFF;
        uint8_t lh = (uint8_t)(len >> 8);
        uint8_t ll = (uint8_t)(len & 0xFF);
        out[i++] = lh;
        out[i++] = ll;
        out[i++] = (uint8_t)(0u - (lh + ll));
    } else {
        out[i++] = (uint8_t)len;
        out[i++] = (uint8_t)(0u - len);
    }

    uint8_t sum = 0;
    out[i++] = PN532_PN_TFI; sum = (uint8_t)(sum + PN532_PN_TFI);
    out[i++] = cmd;          sum = (uint8_t)(sum + cmd);
    for (uint16_t k = 0; k < data_len; k++) {
        out[i++] = data[k];
        sum = (uint8_t)(sum + data[k]);
    }
    out[i++] = (uint8_t)(0u - sum); /* DCS */
    out[i++] = 0x00;                /* postamble */
    return i;
}
