/*
 * RC522 -> USB bridge, presenting as a PN532 UART reader.
 *
 * CDC0 is the pico stdio debug console (printf). CDC1 carries the PN532 binary
 * protocol; the main loop pumps bytes from CDC1 through the frame parser into
 * the command dispatcher and writes responses back.
 *
 * TinyUSB's device task runs from the pico_stdio_usb background IRQ, so we only
 * need to service the CDC1 FIFOs here.
 */

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "tusb.h"

#include "pins.h"
#include "mfrc522.h"
#include "pn532_frame.h"
#include "pn532_cmd.h"

#define PN532_CDC_ITF 1 /* CDC1 */

static pn532_parser_t g_parser;

static void cdc1_write_all(const uint8_t *buf, size_t len) {
    size_t sent = 0;
    uint32_t guard = 0;
    while (sent < len) {
        uint32_t n = tud_cdc_n_write(PN532_CDC_ITF, buf + sent, (uint32_t)(len - sent));
        sent += n;
        tud_cdc_n_write_flush(PN532_CDC_ITF);
        if (n == 0) {
            /* FIFO full; let the device task drain it. Bail out after a while so
             * a disconnected host can't wedge the loop. */
            tud_task();
            if (++guard > 100000) break;
        } else {
            guard = 0;
        }
    }
    tud_cdc_n_write_flush(PN532_CDC_ITF);
}

static void handle_frame(const uint8_t *cmd, uint16_t cmd_len) {
    uint8_t ack[6];
    size_t ack_len = pn532_build_ack(ack);
    cdc1_write_all(ack, ack_len);

    uint8_t resp_data[PN532_MAX_DATA];
    uint8_t resp_cmd = 0;
    int n = pn532_cmd_handle(cmd, cmd_len, resp_data, &resp_cmd);
    if (n < 0) return; /* no response */

    uint8_t frame[PN532_MAX_DATA + 16];
    size_t flen = pn532_build_response(frame, resp_cmd, resp_data, (uint16_t)n);
    cdc1_write_all(frame, flen);

#if !PICO_NO_HARDWARE
    printf("[pn532] cmd=0x%02X len=%u -> resp=0x%02X dlen=%d\n",
           cmd[0], cmd_len, resp_cmd, n);
#endif
}

static void pn532_poll(void) {
    while (tud_cdc_n_available(PN532_CDC_ITF)) {
        uint8_t b;
        if (tud_cdc_n_read(PN532_CDC_ITF, &b, 1) != 1) break;

        const uint8_t *cmd;
        uint16_t cmd_len;
        pn532_rx_result_t r = pn532_parser_push(&g_parser, b, &cmd, &cmd_len);
        switch (r) {
        case PN532_RX_FRAME:
            handle_frame(cmd, cmd_len);
            break;
        case PN532_RX_NACK: {
            /* Host wants the last response resent. We don't buffer it; the host
             * will time out and retry the command, which is acceptable. */
            break;
        }
        case PN532_RX_ERROR: {
            uint8_t nack[6];
            size_t nl = pn532_build_nack(nack);
            cdc1_write_all(nack, nl);
            break;
        }
        case PN532_RX_ACK:
        case PN532_RX_INCOMPLETE:
        default:
            break;
        }
    }
}

int main(void) {
    stdio_init_all(); /* brings up TinyUSB + CDC0 debug console */

    /* Give the host a moment to enumerate so early logs aren't lost. */
    sleep_ms(200);

    uint8_t ver = mfrc522_init();
    printf("\n[boot] RC522 PN532 bridge\n");
    printf("[boot] MFRC522 VersionReg = 0x%02X %s\n", ver,
           (ver == 0x91 || ver == 0x92) ? "(ok)" : "(unexpected!)");

    pn532_parser_reset(&g_parser);

    while (true) {
        tud_task();   /* in case the background IRQ task is not servicing fast enough */
        pn532_poll();
        tight_loop_contents();
    }
    return 0;
}
