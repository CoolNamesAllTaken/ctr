/*
 * "Chipper" fare reader: a Clipper-style (next-generation validator) emulator for the Elecrow CrowPanel Advanced 5" ESP32-P4.
 *
 * Taps come from an RFID-RC522 module (nfc.c; Chipper cards carry their rides,
 * see card.c), the on-screen debug sheet (gear icon, bottom right) or the
 * serial console (serial_cmd.c). Taps are reported to the backend best-effort
 * (tap_report.c).
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "audio.h"
#include "clock.h"
#include "board.h"
#include "leds.h"
#include "net.h"
#include "net_ota.h"
#include "nfc.h"
#include "reader.h"
#include "serial_cmd.h"
#include "tap_report.h"
#include "settings.h"
#include "ui.h"

static const char *TAG = "main";

app_settings_t g_settings = {
    .fare_cents = 345,          /* Caltrain 1-zone Clipper adult fare (fixed for now) */
    .low_balance_cents = 200,   /* Clipper warns (yellow, two beeps) under $2 */
    .discount = false,
    .tag_on_off = true,
    .volume = 50,
    .locator_tone = true,
    .brightness = 100,
    .result_ms = 2500,
    .flip = false,
};

static void on_ota_begin(void)
{
    ui_show_updating(true);
}

static void on_ota_end(bool ok)
{
    if (!ok) {
        ui_show_updating(false);   /* on success the restart follows */
    }
}

/* Self-test of a freshly updated image: it must get back on the network, so
 * the next update can reach it. Never depends on the backend. */
static void ota_confirm_task(void *arg)
{
    if (net_wait_for_ip(portMAX_DELAY)) {
        net_ota_mark_valid();
    }
    vTaskDelete(NULL);
}

static void start_network(void)
{
    if (net_init() != ESP_OK) {
        ESP_LOGE(TAG, "network init failed: no updates or backend this boot");
        return;
    }
    if (tap_report_init() != ESP_OK) {
        ESP_LOGE(TAG, "tap reports disabled this boot");
    }
    net_ota_config_t ota = NET_OTA_DEFAULT_CONFIG();
    ota.can_update = reader_is_idle;
    ota.on_begin = on_ota_begin;
    ota.on_end = on_ota_end;
    if (net_ota_start(&ota) != ESP_OK) {
        ESP_LOGE(TAG, "update server failed to start");
        return;
    }
    xTaskCreate(ota_confirm_task, "ota_confirm", 2048, NULL, 2, NULL);
}

void app_main(void)
{
    clock_init();
    /* Corner light pipes: idle glow as soon as possible. Not fatal if absent. */
    if (leds_init() != ESP_OK) {
        ESP_LOGE(TAG, "LED string init failed");
    }
    ESP_ERROR_CHECK(board_init());
    board_set_flipped(g_settings.flip);
    ui_init();
    ESP_ERROR_CHECK(board_set_backlight(g_settings.brightness));
    ESP_ERROR_CHECK(audio_init());
    ESP_ERROR_CHECK(reader_init());
    ESP_ERROR_CHECK(serial_cmd_init());
    /* USB Ethernet comes up in the background; the reader works without it. */
    start_network();
    /* The reader keeps working without the NFC module (debug sheet, serial). */
    if (nfc_init() != ESP_OK) {
        ESP_LOGE(TAG, "RC522 not found: card reading disabled");
    }
    ESP_LOGI(TAG, "reader ready");
}
