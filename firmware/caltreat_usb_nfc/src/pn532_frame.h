/*
 * PN532 host<->controller framing over a byte stream (TinyUSB CDC1).
 *
 * Frame format (UART/HSU variant, used by libnfc's pn532_uart driver and by
 * adafruit-circuitpython-pn532):
 *
 *   Normal:   00 00 FF LEN LCS TFI PD0..PDn DCS 00
 *   Extended: 00 00 FF FF FF LENH LENL LCS TFI PD0..PDn DCS 00
 *   ACK:      00 00 FF 00 FF 00
 *   NACK:     00 00 FF FF 00 00
 *
 * LEN = TFI + data length; LCS = (-(LEN)) & 0xFF;
 * DCS = -(TFI + sum(PD)) & 0xFF. TFI is 0xD4 host->PN532, 0xD5 PN532->host.
 *
 * The parser is fed one byte at a time and returns a complete command payload
 * (TFI stripped) when a valid frame arrives. A leading 0x55 wakeup preamble and
 * stray 0x00 bytes between frames are skipped.
 */

#ifndef PN532_FRAME_H
#define PN532_FRAME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PN532_HOST_TFI  0xD4
#define PN532_PN_TFI    0xD5
#define PN532_MAX_DATA  265 /* generous: covers extended frames we accept */

typedef enum {
    PN532_RX_INCOMPLETE = 0, /* need more bytes */
    PN532_RX_FRAME,          /* a full command payload is ready */
    PN532_RX_ACK,            /* host sent an ACK (ignore) */
    PN532_RX_NACK,           /* host sent a NACK (host wants a resend) */
    PN532_RX_ERROR,          /* checksum/format error; a NACK should be sent */
} pn532_rx_result_t;

typedef struct {
    uint8_t  state;
    uint16_t len;      /* declared payload length (TFI + data) */
    uint16_t idx;      /* bytes of payload collected so far */
    uint8_t  sum;      /* running checksum accumulator for DCS */
    uint8_t  ext_lenh; /* extended-frame length bytes, held for the LCS check */
    uint8_t  ext_lenl;
    uint8_t  buf[PN532_MAX_DATA]; /* payload incl. TFI at buf[0] */
    bool     extended;
} pn532_parser_t;

void pn532_parser_reset(pn532_parser_t *p);

/*
 * Push one received byte. On PN532_RX_FRAME, *out_cmd points at the command
 * byte and the data that follows (TFI already stripped) and *out_len is the
 * number of bytes there (>=1; out_cmd[0] is the command code).
 */
pn532_rx_result_t pn532_parser_push(pn532_parser_t *p, uint8_t byte,
                                    const uint8_t **out_cmd, uint16_t *out_len);

/* Build an ACK frame into out (6 bytes). Returns the length (6). */
size_t pn532_build_ack(uint8_t *out);
/* Build a NACK frame into out (6 bytes). Returns the length (6). */
size_t pn532_build_nack(uint8_t *out);

/*
 * Build a response frame carrying TFI 0xD5 followed by `cmd` and `data`.
 * `cmd` is normally the request command + 1. Writes into out (which must hold
 * at least data_len + 12 bytes) and returns the total frame length.
 * Uses the extended format automatically when data_len+2 > 255.
 */
size_t pn532_build_response(uint8_t *out, uint8_t cmd,
                            const uint8_t *data, uint16_t data_len);

#endif /* PN532_FRAME_H */
