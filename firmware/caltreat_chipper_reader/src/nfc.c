/*
 * NFC front-end: polls the RC522 and turns "a card arrived in the field" into
 * one reader tap. Read-only: only the card's UID is used, like the
 * account-based next-generation Clipper, where the card is just an identifier
 * and balance/trip state live in the back office (here: reader.c).
 */
#include "nfc.h"

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "rc522.h"
#include "reader.h"

static const char *TAG = "nfc";

#define POLL_MS        50
#define GONE_MS        600   /* card counts as removed after this long unseen */

static void nfc_task(void *arg)
{
    rc522_card_t last = {0};
    int64_t last_seen = 0;

    while (true) {
        rc522_card_t card;
        rc522_status_t st = rc522_read_card(&card);
        int64_t now = esp_timer_get_time();

        if (st == RC522_OK) {
            bool same = card.uid_len == last.uid_len && !memcmp(card.uid, last.uid, card.uid_len);
            bool still_there = same && (now - last_seen) < GONE_MS * 1000LL;
            last = card;
            last_seen = now;
            if (!still_there) {
                char hex[3 * RC522_UID_MAX + 1] = "";
                for (int i = 0; i < card.uid_len; i++) {
                    sprintf(hex + (i ? 3 * i - 1 : 0), i ? ":%02X" : "%02X", card.uid[i]);
                }
                ESP_LOGI(TAG, "card %s (ATQA %04X SAK %02X)", hex, card.atqa, card.sak);

                reader_card_t rc = {.uid_len = card.uid_len};
                memcpy(rc.uid, card.uid, card.uid_len);
                reader_present_card(&rc);
            }
        } else if (st == RC522_COLLISION) {
            ESP_LOGW(TAG, "more than one card in the field");
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
}

esp_err_t nfc_init(void)
{
    const rc522_config_t cfg = {
        .spi_host = SPI2_HOST,
        .gpio_mosi = NFC_GPIO_MOSI,
        .gpio_miso = NFC_GPIO_MISO,
        .gpio_sck = NFC_GPIO_SCK,
        .gpio_cs = NFC_GPIO_CS,
        .gpio_rst = NFC_GPIO_RST,
        .clock_hz = 5 * 1000 * 1000,
    };
    ESP_RETURN_ON_ERROR(rc522_init(&cfg), TAG, "rc522");
    BaseType_t ok = xTaskCreate(nfc_task, "nfc", 4096, NULL, 5, NULL);
    ESP_RETURN_ON_FALSE(ok == pdPASS, ESP_ERR_NO_MEM, TAG, "task");
    return ESP_OK;
}
