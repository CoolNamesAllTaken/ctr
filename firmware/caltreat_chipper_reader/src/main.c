/*
 * "Chipper" fare reader: a Clipper-style (next-generation validator) emulator for the Elecrow CrowPanel Advanced 5" ESP32-P4.
 *
 * Taps come from an RFID-RC522 module (nfc.c; each card's UID is its account,
 * see reader.c), the on-screen debug sheet (gear icon, bottom right) or the
 * serial console (serial_cmd.c).
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "audio.h"
#include "clock.h"
#include "board.h"
#include "nfc.h"
#include "reader.h"
#include "serial_cmd.h"
#include "settings.h"
#include "ui.h"

static const char *TAG = "main";

app_settings_t g_settings = {
    .fare_cents = 345,          /* Caltrain 1-zone Clipper adult fare (fixed for now) */
    .low_balance_cents = 200,   /* Clipper warns (yellow, two beeps) under $2 */
    .discount = false,
    .tag_on_off = true,
    .volume = 20,
    .locator_tone = true,
    .brightness = 100,
    .result_ms = 2500,
    .flip = false,
};

void app_main(void)
{
    clock_init();
    ESP_ERROR_CHECK(board_init());
    board_set_flipped(g_settings.flip);
    ui_init();
    ESP_ERROR_CHECK(board_set_backlight(g_settings.brightness));
    ESP_ERROR_CHECK(audio_init());
    ESP_ERROR_CHECK(reader_init());
    ESP_ERROR_CHECK(serial_cmd_init());
    /* The reader keeps working without the NFC module (debug sheet, serial). */
    if (nfc_init() != ESP_OK) {
        ESP_LOGE(TAG, "RC522 not found: card reading disabled");
    }
    ESP_LOGI(TAG, "reader ready");
}
