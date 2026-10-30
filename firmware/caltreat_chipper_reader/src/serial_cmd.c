/*
 * Line-based serial commands on the console UART, mirroring the debug overlay
 * so the reader can be driven from a script / host-side NFC bridge:
 *
 *   tap              tap the virtual card
 *   ok | low | tagoff | insufficient | error    force a result
 *   balance <cents>  set the virtual card balance
 *   card <balance_cents> [discount] [on]  present an external card (NFC path);
 *                    "on" = card already tagged on
 *   time <unix_seconds>                set the on-screen clock (e.g. `time $(date +%s)`)
 *   snap [top|fb]    dump a screenshot (decode with tools/snap.py); "top" = overlay
 *                    layer, "fb" = the frame buffer actually on the panel (landscape)
 *   sheet            open/close the debug sheet
 *   cards            list the card accounts (UID, balance, trip state)
 */
#include "serial_cmd.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "mbedtls/base64.h"

#include "clock.h"
#include "board.h"
#include "reader.h"
#include "ui.h"

static const char *TAG = "cmd";

#define CMD_UART  CONFIG_ESP_CONSOLE_UART_NUM
#define CMD_LINE_LEN  64

/*
 * Screenshot as run-length encoded RGB565 (little-endian u16 count, u16 pixel),
 * base64 in 76-char lines between SNAP-BEGIN/SNAP-END markers.
 */
static void out(const char *fmt, ...)
{
    /* Through the UART driver (blocking, yields) rather than printf, which
     * busy-waits on the FIFO and starves the idle task during a long dump. */
    char buf[96];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    uart_write_bytes(CMD_UART, buf, n);
}

static void dump_rgb565(const uint8_t *data, uint32_t w, uint32_t h, uint32_t stride);

static void dump_screenshot(const char *what)
{
    if (what && !strcmp(what, "fb")) {
        /* Hold the LVGL lock so no buffer swap happens mid-dump. */
        lvgl_port_lock(0);
        dump_rgb565((const uint8_t *)board_front_framebuffer(), BOARD_LCD_H_RES, BOARD_LCD_V_RES,
                    BOARD_LCD_H_RES * 2);
        lvgl_port_unlock();
        return;
    }
    bool top = what && !strcmp(what, "top");
    lvgl_port_lock(0);
    lv_draw_buf_t *snap = lv_snapshot_take(top ? lv_layer_top() : lv_screen_active(), LV_COLOR_FORMAT_RGB565);
    lvgl_port_unlock();
    if (!snap) {
        ESP_LOGE(TAG, "snapshot failed");
        return;
    }
    dump_rgb565(snap->data, snap->header.w, snap->header.h, snap->header.stride);
    lv_draw_buf_destroy(snap);
}

static void dump_rgb565(const uint8_t *data, uint32_t w, uint32_t h, uint32_t stride)
{
    out("SNAP-BEGIN %lu %lu\n", (unsigned long)w, (unsigned long)h);

    uint8_t raw[57];   /* 57 bytes -> one 76-char base64 line */
    size_t raw_len = 0;
    unsigned char b64[80];
    size_t b64_len;

    for (uint32_t y = 0; y < h; y++) {
        const uint16_t *row = (const uint16_t *)(data + y * stride);
        for (uint32_t x = 0; x < w;) {
            uint16_t px = row[x], n = 0;
            while (x < w && row[x] == px && n < 0xFFFF) {
                x++;
                n++;
            }
            const uint8_t run[4] = {n & 0xFF, n >> 8, px & 0xFF, px >> 8};
            for (int i = 0; i < 4; i++) {
                raw[raw_len++] = run[i];
                if (raw_len == sizeof(raw)) {
                    mbedtls_base64_encode(b64, sizeof(b64), &b64_len, raw, raw_len);
                    out("%.*s\n", (int)b64_len, b64);
                    raw_len = 0;
                }
            }
        }
    }
    if (raw_len) {
        mbedtls_base64_encode(b64, sizeof(b64), &b64_len, raw, raw_len);
        out("%.*s\n", (int)b64_len, b64);
    }
    out("SNAP-END\n");
}

static void run(char *line)
{
    char *cmd = strtok(line, " \t");
    char *arg = strtok(NULL, " \t");
    if (!cmd) {
        return;
    }

    if (!strcmp(cmd, "tap")) {
        reader_tap_virtual_card();
    } else if (!strcmp(cmd, "ok")) {
        reader_force_result(READER_RESULT_OK);
    } else if (!strcmp(cmd, "low")) {
        reader_force_result(READER_RESULT_LOW_BALANCE);
    } else if (!strcmp(cmd, "tagoff")) {
        reader_force_result(READER_RESULT_TAG_OFF);
    } else if (!strcmp(cmd, "insufficient")) {
        reader_force_result(READER_RESULT_INSUFFICIENT);
    } else if (!strcmp(cmd, "error")) {
        reader_force_result(READER_RESULT_CARD_ERROR);
    } else if (!strcmp(cmd, "balance") && arg) {
        reader_set_virtual_balance(atoi(arg));
        ESP_LOGI(TAG, "virtual balance = %ld", (long)reader_virtual_balance());
    } else if (!strcmp(cmd, "card") && arg) {
        reader_card_t card = {.balance_cents = atoi(arg)};
        for (char *opt; (opt = strtok(NULL, " \t"));) {
            card.discount |= !strcmp(opt, "discount");
            card.tagged_on |= !strcmp(opt, "on");
        }
        reader_present_card(&card);
    } else if (!strcmp(cmd, "snap")) {
        dump_screenshot(arg);
    } else if (!strcmp(cmd, "cards")) {
        reader_account_t acc[MAX_ACCOUNTS];
        int n = reader_accounts(acc, MAX_ACCOUNTS);
        for (int i = 0; i < n; i++) {
            char hex[31] = "";
            for (int j = 0; j < acc[i].uid_len; j++) {
                sprintf(hex + (j ? 3 * j - 1 : 0), j ? ":%02X" : "%02X", acc[i].uid[j]);
            }
            ESP_LOGI(TAG, "card %s  balance %ld.%02ld  %s", hex, (long)(acc[i].balance_cents / 100),
                     (long)(acc[i].balance_cents % 100), acc[i].tagged_on ? "tagged on" : "");
        }
        if (n == 0) {
            ESP_LOGI(TAG, "no cards seen yet");
        }
    } else if (!strcmp(cmd, "sheet")) {
        ui_toggle_debug();
    } else if (!strcmp(cmd, "time") && arg) {
        clock_set((time_t)atoll(arg));
    } else {
        ESP_LOGW(TAG, "unknown command '%s' (tap|ok|low|tagoff|insufficient|error|balance N|card N [discount] [on]|time T|snap [top|fb]|sheet|cards)", cmd);
    }
}

static void cmd_task(void *arg)
{
    char line[CMD_LINE_LEN];
    size_t len = 0;
    uint8_t c;

    while (true) {
        if (uart_read_bytes(CMD_UART, &c, 1, portMAX_DELAY) != 1) {
            continue;
        }
        if (c == '\r' || c == '\n') {
            line[len] = '\0';
            run(line);
            len = 0;
        } else if (c >= 0x20 && c < 0x7F && len < CMD_LINE_LEN - 1) {
            line[len++] = (char)c;
        }
    }
}

esp_err_t serial_cmd_init(void)
{
    ESP_RETURN_ON_ERROR(uart_driver_install(CMD_UART, 256, 2048, 0, NULL, 0), TAG, "uart");
    BaseType_t ok = xTaskCreate(cmd_task, "cmd", 6144, NULL, 3, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}
