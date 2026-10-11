/*
 * Minimal MFRC522 (RFID-RC522 module) driver over SPI: enough to read the UID
 * of an ISO 14443A card (MIFARE Classic / Ultralight / DESFire, phones in
 * card emulation, ...). Read-only: no authentication, no block access.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#define RC522_UID_MAX 10

typedef struct {
    uint8_t uid[RC522_UID_MAX];
    uint8_t uid_len;        /* 4, 7 or 10 */
    uint8_t sak;            /* card type hint, e.g. 0x08 = MIFARE Classic 1K */
    uint16_t atqa;
} rc522_card_t;

typedef enum {
    RC522_NO_CARD,          /* nothing answered */
    RC522_OK,               /* card selected, UID read */
    RC522_COLLISION,        /* more than one card in the field */
    RC522_ERROR,            /* card answered but the exchange failed (e.g. moved away) */
} rc522_status_t;

typedef struct {
    int spi_host;           /* spi_host_device_t */
    int gpio_mosi, gpio_miso, gpio_sck, gpio_cs, gpio_rst;
    int clock_hz;
} rc522_config_t;

/* Bring up SPI, hard-reset the chip, configure it and switch the antenna on.
 * Logs the chip version (0x91/0x92 genuine, 0x88/0x12 common clones). */
esp_err_t rc522_init(const rc522_config_t *cfg);

/* Look for a card (including one already halted) and read its UID, then halt
 * it. One call is one complete attempt; poll it. */
rc522_status_t rc522_read_card(rc522_card_t *card);
