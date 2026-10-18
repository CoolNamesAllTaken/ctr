/*
 * RC522 -> USB bridge, presenting as a PN532 UART reader.
 *
 * USB uses the stock pico_stdio_usb setup: one CDC port plus the picotool
 * reset interface, with the 1200-baud magic reset into BOOTSEL. The CDC port
 * carries the PN532 binary protocol, so USB is disabled as a printf sink and
 * debug output goes to the UART (GPIO0 TX) instead. The main loop pumps bytes
 * from the CDC port through the frame parser into the command dispatcher and
 * writes responses back.
 *
 * The stdio_usb background task is turned off (see platformio.ini) so that
 * tud_task() only ever runs here, never concurrently with our CDC calls.
 */

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/stdio_usb.h"
#include "tusb.h"

#include "pins.h"
#include "mfrc522.h"
#include "pn532_frame.h"
#include "pn532_cmd.h"

#define PN532_CDC_ITF 0

/* A frame that stops arriving partway is dropped after this long, so the next
 * command isn't swallowed into it. */
#define PN532_RX_RESYNC_MS 100

static pn532_parser_t g_parser;
static absolute_time_t g_last_rx;
static bool g_connected;     /* host has the port open (DTR) */
static bool g_resync;        /* drop any half-received frame */

/* Last response frame, resent when the host NACKs. */
static uint8_t g_last_frame[PN532_MAX_DATA + 16];
static size_t g_last_frame_len;

static void pn532_write_all(const uint8_t *buf, size_t len) {
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

/* Note the host opening or closing the port. Either way a half-received frame
 * belongs to the old session, so ask the main loop to drop it. */
static bool port_changed(void) {
    bool connected = tud_cdc_n_connected(PN532_CDC_ITF);
    if (connected == g_connected) return false;
    g_connected = connected;
    g_resync = true;
    return true;
}

bool pn532_host_abort(void) {
    tud_task();
    if (port_changed()) return true;

    /* 0x00 bytes are frame padding, e.g. the postamble of the very command
     * being handled (the parser reports a frame at its checksum). Feed them to
     * the parser, which only moves its state and leaves the command buffer
     * alone. Any other byte is the host talking to us, which aborts. */
    uint8_t b;
    while (tud_cdc_n_peek(PN532_CDC_ITF, &b)) {
        if (b != 0x00) return true;
        tud_cdc_n_read(PN532_CDC_ITF, &b, 1);
        const uint8_t *cmd;
        uint16_t cmd_len;
        pn532_parser_push(&g_parser, b, &cmd, &cmd_len);
        g_last_rx = get_absolute_time();
    }
    return false;
}

static void handle_frame(const uint8_t *cmd, uint16_t cmd_len) {
    uint8_t ack[6];
    size_t ack_len = pn532_build_ack(ack);
    pn532_write_all(ack, ack_len);

    uint8_t code = cmd[0];
    uint8_t resp_data[PN532_MAX_DATA];
    uint8_t resp_cmd = 0;
    int n = pn532_cmd_handle(cmd, cmd_len, resp_data, &resp_cmd);
    if (n == PN532_NO_RESPONSE) {
        g_last_frame_len = 0;
        return;
    }
    if (n == PN532_SYNTAX_ERROR) {
        g_last_frame_len = pn532_build_error(g_last_frame);
    } else {
        g_last_frame_len = pn532_build_response(g_last_frame, resp_cmd, resp_data, (uint16_t)n);
    }
    pn532_write_all(g_last_frame, g_last_frame_len);

#if !PICO_NO_HARDWARE
    printf("[pn532] cmd=0x%02X len=%u -> resp=0x%02X dlen=%d\n",
           code, cmd_len, resp_cmd, n);
#endif
}

static void pn532_poll(void) {
    if (port_changed() || g_resync ||
        (!pn532_parser_idle(&g_parser) &&
         absolute_time_diff_us(g_last_rx, get_absolute_time()) > PN532_RX_RESYNC_MS * 1000)) {
        pn532_parser_reset(&g_parser);
        g_resync = false;
    }

    while (tud_cdc_n_available(PN532_CDC_ITF)) {
        uint8_t b;
        if (tud_cdc_n_read(PN532_CDC_ITF, &b, 1) != 1) break;
        g_last_rx = get_absolute_time();

        const uint8_t *cmd;
        uint16_t cmd_len;
        pn532_rx_result_t r = pn532_parser_push(&g_parser, b, &cmd, &cmd_len);
        switch (r) {
        case PN532_RX_FRAME:
            handle_frame(cmd, cmd_len);
            break;
        case PN532_RX_NACK:
            /* Host wants the last response resent. */
            pn532_write_all(g_last_frame, g_last_frame_len);
            break;
        case PN532_RX_ERROR:
            /* Bad checksum/format: like the PN532, ignore the frame and let the
             * host time out and retry. */
        case PN532_RX_ACK:
        case PN532_RX_INCOMPLETE:
        default:
            break;
        }
        if (g_resync) break; /* port closed/reopened during a command */
    }
}

int main(void) {
    stdio_init_all(); /* brings up TinyUSB + the UART debug console */
    stdio_set_driver_enabled(&stdio_usb, false); /* keep printf off the PN532 port */

    uint8_t ver = mfrc522_init();
    printf("\n[boot] RC522 PN532 bridge\n");
    printf("[boot] MFRC522 VersionReg = 0x%02X %s\n", ver,
           (ver == 0x91 || ver == 0x92) ? "(ok)" : "(unexpected!)");

    pn532_parser_reset(&g_parser);

    while (true) {
        tud_task();
        pn532_poll();
        tight_loop_contents();
    }
    return 0;
}
