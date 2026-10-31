/*
 * Line-based serial commands on the console UART, mirroring the debug overlay
 * so the reader can be driven from a script / host-side NFC bridge:
 *
 *   tap              tap the virtual card
 *   ok | low | tagoff | insufficient | error    force a result
 *   balance <cents>  set the virtual card balance
 *   card             print the ride block of the next Chipper card tapped (no ride taken)
 *   card <balance_cents> [discount] [on]  present a debug card charged like the
 *                    virtual card; "on" = card already tagged on
 *   time <unix_seconds>                set the on-screen clock (e.g. `time $(date +%s)`)
 *   snap [top|fb]    dump a screenshot (decode with tools/snap.py); "top" = overlay
 *                    layer, "fb" = the frame buffer actually on the panel (landscape)
 *   sheet            open/close the debug sheet
 *   reports          tap reports to the backend: queued, sent, dropped, last error
 *   net              network status (adapter, IP, backend, hostname)
 *   ota              running firmware version, partition and update state
 */
#include "serial_cmd.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "mbedtls/base64.h"

#include "clock.h"
#include "board.h"
#include "net.h"
#include "net_ota.h"
#include "nfc.h"
#include "reader.h"
#include "tap_report.h"
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

static void print_net(void)
{
    net_status_t st;
    net_get_status(&st);
    ESP_LOGI(TAG, "nic %s  ip " IPSTR "  reinits %lu", net_nic_state_name(st.nic), IP2STR(&st.ip),
             (unsigned long)st.nic_reinits);
    if (st.backend_last_ok_us) {
        ESP_LOGI(TAG, "backend %s  last ok %lld s ago", net_backend_state_name(st.backend),
                 (esp_timer_get_time() - st.backend_last_ok_us) / 1000000);
    } else {
        ESP_LOGI(TAG, "backend %s  never reached", net_backend_state_name(st.backend));
    }
    ESP_LOGI(TAG, "host %s.local  id %s  backend url %s", net_hostname(), net_device_id(), CONFIG_CHIPPER_BACKEND_URL);
}

static void print_ota(void)
{
    const esp_app_desc_t *d = esp_app_get_description();
    const esp_partition_t *part = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    const char *state = "none";
    if (esp_ota_get_state_partition(part, &st) == ESP_OK) {
        state = st == ESP_OTA_IMG_VALID            ? "valid"
                : st == ESP_OTA_IMG_PENDING_VERIFY ? "pending_verify"
                : st == ESP_OTA_IMG_NEW            ? "new"
                                                   : "other";
    }
    ESP_LOGI(TAG, "%s %s (IDF %s, built %s %s)", d->project_name, d->version, d->idf_ver, d->date, d->time);
    ESP_LOGI(TAG, "running %s @0x%lx, state %s%s", part->label, (unsigned long)part->address, state,
             net_ota_busy() ? ", update in progress" : "");
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
    } else if (!strcmp(cmd, "card")) {
        nfc_inspect_next();
    } else if (!strcmp(cmd, "snap")) {
        dump_screenshot(arg);
    } else if (!strcmp(cmd, "reports")) {
        tap_report_stats_t st;
        tap_report_stats(&st);
        ESP_LOGI(TAG, "reports: %lu queued, %lu sent, %lu dropped (queue full), %lu refused, last HTTP %d",
                 (unsigned long)st.queued, (unsigned long)st.sent, (unsigned long)st.dropped_full,
                 (unsigned long)st.dropped_rejected, st.last_status);
        if (st.last_detail[0]) {
            ESP_LOGI(TAG, "last refusal: %s", st.last_detail);
        }
    } else if (!strcmp(cmd, "sheet")) {
        ui_toggle_debug();
    } else if (!strcmp(cmd, "time") && arg) {
        clock_set((time_t)atoll(arg));
    } else if (!strcmp(cmd, "net")) {
        print_net();
    } else if (!strcmp(cmd, "ota")) {
        print_ota();
    } else {
        ESP_LOGW(TAG, "unknown command '%s' (tap|ok|low|tagoff|insufficient|error|balance N|card|card N [discount] [on]|time T|snap [top|fb]|sheet|reports|net|ota)", cmd);
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
