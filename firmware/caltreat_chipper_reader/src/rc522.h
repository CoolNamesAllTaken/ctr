/*
 * Minimal MFRC522 (RFID-RC522 module) driver over SPI: reads the UID of an
 * ISO 14443A card (MIFARE Classic / Ultralight / DESFire, phones in card
 * emulation, ...), plus MIFARE Classic key A authentication and 16-byte block
 * read/write.
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

/* Like rc522_read_card() but leaves the card selected for block access.
 * Finish with rc522_halt(). */
rc522_status_t rc522_select(rc522_card_t *card);

/* Halt the selected card and switch the crypto engine off. */
void rc522_halt(void);

/* MIFARE Classic, on a selected card. RC522_NO_CARD = no answer (card gone or,
 * for auth, wrong key: the card stays silent). */
rc522_status_t rc522_mfc_auth_a(uint8_t block, const uint8_t key[6], const rc522_card_t *card);
rc522_status_t rc522_mfc_read(uint8_t block, uint8_t out16[16]);
rc522_status_t rc522_mfc_write(uint8_t block, const uint8_t in16[16]);
void rc522_mfc_stop_crypto(void);
