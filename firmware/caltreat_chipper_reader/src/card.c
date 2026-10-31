#include "card.h"

#include <string.h>
#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"

static const char *TAG = "card";

#define RIDE_BLOCK   4
#define VERSION      2
#define OFF_VERSION  4
#define OFF_RIDES    5
#define OFF_CRC      14
#define SEEN_MAX     1024   /* 500 cards issued; oldest overwritten when full */
#define IGNORE_US    ((int64_t)CONFIG_CHIPPER_SAME_READER_IGNORE_MIN * 60 * 1000000)

static const uint8_t MAGIC[4] = {'C', 'H', 'P', 'R'};
static const uint8_t KEY_A[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};   /* factory key, no secrets */

/* Cards that tapped on at this reader: a tap here soon after is ignored
 * instead of counting as the tap off. */
static struct {
    uint8_t uid[RC522_UID_MAX];
    uint8_t len;
    int64_t at_us;
} s_seen[SEEN_MAX];
static int s_seen_count;
static int s_seen_next;

uint16_t card_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0x6363;
    for (size_t i = 0; i < len; i++) {
        uint8_t b = data[i] ^ (crc & 0xFF);
        b ^= b << 4;
        crc = (crc >> 8) ^ ((uint16_t)b << 8) ^ ((uint16_t)b << 3) ^ (b >> 4);
    }
    return crc;
}

static void set_crc(uint8_t b[16])
{
    uint16_t crc = card_crc16(b, OFF_CRC);
    b[OFF_CRC] = crc & 0xFF;
    b[OFF_CRC + 1] = crc >> 8;
}

void card_make_block(uint8_t half_rides, uint8_t out16[16])
{
    memset(out16, 0, 16);
    memcpy(out16, MAGIC, sizeof(MAGIC));
    out16[OFF_VERSION] = VERSION;
    out16[OFF_RIDES] = half_rides;
    set_crc(out16);
}

static card_status_t check_block(const uint8_t b[16], uint8_t *half_rides)
{
    if (memcmp(b, MAGIC, sizeof(MAGIC)) != 0 || (b[OFF_VERSION] != 1 && b[OFF_VERSION] != VERSION)) {
        return CARD_NOT_CHIPPER;
    }
    uint16_t crc = card_crc16(b, OFF_CRC);
    if (b[OFF_CRC] != (crc & 0xFF) || b[OFF_CRC + 1] != (crc >> 8)) {
        return CARD_DAMAGED;
    }
    if (b[OFF_VERSION] == 1) {
        /* v1: whole rides, never mid-trip. 128+ rides can't be expressed in halves. */
        *half_rides = b[OFF_RIDES] > 127 ? 254 : b[OFF_RIDES] * 2;
    } else {
        *half_rides = b[OFF_RIDES];
    }
    return CARD_VALID;
}

static int seen_index(const rc522_card_t *card)
{
    for (int i = 0; i < s_seen_count; i++) {
        if (s_seen[i].len == card->uid_len && !memcmp(s_seen[i].uid, card->uid, card->uid_len)) {
            return i;
        }
    }
    return -1;
}

static void remember_tap_on(const rc522_card_t *card)
{
    int i = seen_index(card);
    if (i < 0) {
        i = s_seen_next;
        s_seen_next = (s_seen_next + 1) % SEEN_MAX;
        if (s_seen_count < SEEN_MAX) {
            s_seen_count++;
        }
        memcpy(s_seen[i].uid, card->uid, card->uid_len);
        s_seen[i].len = card->uid_len;
    }
    s_seen[i].at_us = esp_timer_get_time();
}

static bool tapped_on_here_recently(const rc522_card_t *card)
{
    int i = seen_index(card);
    return i >= 0 && esp_timer_get_time() - s_seen[i].at_us < IGNORE_US;
}

/* A wrong key and a card that left both look like silence. Re-select to tell them apart. */
static card_status_t auth_failed(const rc522_card_t *card)
{
    rc522_card_t again;
    bool present = rc522_select(&again) == RC522_OK && again.uid_len == card->uid_len &&
                   !memcmp(again.uid, card->uid, card->uid_len);
    return present ? CARD_NOT_CHIPPER : CARD_RF_ERROR;
}

card_status_t card_read(const rc522_card_t *card, uint8_t *half_rides, uint8_t *version, uint8_t raw[16])
{
    uint8_t b[16];
    if (rc522_mfc_auth_a(RIDE_BLOCK, KEY_A, card) != RC522_OK) {
        return auth_failed(card);
    }
    if (rc522_mfc_read(RIDE_BLOCK, b) != RC522_OK) {
        return CARD_RF_ERROR;
    }
    if (raw) {
        memcpy(raw, b, 16);
    }
    if (version) {
        *version = b[OFF_VERSION];
    }
    return check_block(b, half_rides);
}

/* Write the new half-ride count (always as v2, reserved bytes kept) and read it back. */
static card_status_t write_half_rides(const uint8_t old[16], uint8_t half_rides)
{
    uint8_t next[16];
    memcpy(next, old, 16);
    next[OFF_VERSION] = VERSION;
    next[OFF_RIDES] = half_rides;
    set_crc(next);

    /* A failed write may still have landed, so always check by reading back. */
    if (rc522_mfc_write(RIDE_BLOCK, next) != RC522_OK) {
        ESP_LOGW(TAG, "write not acknowledged");
    }
    uint8_t back[16];
    if (rc522_mfc_read(RIDE_BLOCK, back) != RC522_OK) {
        ESP_LOGW(TAG, "read-back failed: the card may or may not be updated");
        return CARD_RF_ERROR;
    }
    if (memcmp(back, next, 16) != 0) {
        ESP_LOGW(TAG, "read-back differs: card %s updated", memcmp(back, old, 16) ? "maybe" : "not");
        return CARD_RF_ERROR;
    }
    return CARD_VALID;
}

card_status_t card_tap(const rc522_card_t *card, uint8_t *half_rides_left)
{
    uint8_t half, old[16];
    card_status_t st = card_read(card, &half, NULL, old);
    *half_rides_left = st == CARD_VALID ? half : 0;
    if (st != CARD_VALID) {
        return st;
    }

    const bool on_trip = half & 1;
    if (on_trip && tapped_on_here_recently(card)) {
        return CARD_ALREADY_TAPPED;
    }
    if (!on_trip && half == 0) {
        return CARD_NO_RIDES;
    }
    /* Odd: pay the trip's second half (tap off). Even, >= 2: pay the first (tap on). */
    st = write_half_rides(old, half - 1);
    if (st != CARD_VALID) {
        return st;
    }
    *half_rides_left = half - 1;
    if (on_trip) {
        return CARD_TAP_OFF;
    }
    remember_tap_on(card);
    return CARD_TAP_ON;
}

const char *card_status_name(card_status_t st)
{
    static const char *const names[] = {"tap_on",  "tap_off",  "no_rides", "already_tapped",
                                        "not_chipper", "damaged", "rf_error", "valid"};
    return st < sizeof(names) / sizeof(names[0]) ? names[st] : "?";
}
