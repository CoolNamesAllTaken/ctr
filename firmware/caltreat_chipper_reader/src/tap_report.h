/*
 * Best-effort tap reports to the backend (POST /api/taps/). The card is the
 * source of truth; these only tell the backend that a tap happened. Reports
 * wait in a RAM queue in PSRAM (16384, oldest dropped when full) and are
 * retried while the backend is unreachable; they don't survive a restart.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "card.h"

typedef struct {
    uint32_t queued;            /* waiting now */
    uint32_t sent;
    uint32_t dropped_full;      /* overwritten while the queue was full */
    uint32_t dropped_rejected;  /* refused by the backend (4xx other than 401) */
    int last_status;            /* HTTP status of the last attempt, 0 = no response */
    char last_detail[96];       /* backend's reply to the last refused report */
} tap_report_stats_t;

/* Start the sender task (needs net_init() first). */
esp_err_t tap_report_init(void);

/* Queue a report of a CARD_TAP_ON / CARD_TAP_OFF / CARD_NO_RIDES tap; never
 * blocks. Dropped if tap_report_init() hasn't run. */
void tap_report_add(const uint8_t *uid, uint8_t uid_len, card_status_t result, uint8_t half_rides_left);

void tap_report_stats(tap_report_stats_t *out);
