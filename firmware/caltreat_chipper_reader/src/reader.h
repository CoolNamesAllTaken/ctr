/*
 * Fare-reader logic: turns "a card was presented" into a result, then
 * drives the screen and audio cues. Runs in its own task; every public
 * function only posts an event, so it is safe to call from any task
 * (LVGL callbacks today, an NFC polling task later).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef enum {
    READER_RESULT_OK,           /* tag on / fare paid: one beep */
    READER_RESULT_LOW_BALANCE,  /* paid, balance under threshold: two beeps */
    READER_RESULT_TAG_OFF,      /* Caltrain tag off: rising two-tone */
    READER_RESULT_INSUFFICIENT, /* not enough value: three beeps */
    READER_RESULT_CARD_ERROR,   /* unreadable, tap again: warning tone */
} reader_result_kind_t;

typedef struct {
    reader_result_kind_t kind;
    int32_t fare_cents;         /* fare charged (or that would have been) */
    int32_t balance_cents;      /* remaining balance after the tap */
    bool discount;
    bool tagged_on;             /* card's trip state after the tap */
    uint8_t uid[10];            /* physical card that was tapped (uid_len 0 = virtual/forced) */
    uint8_t uid_len;
} reader_result_t;

/*
 * A card presented to the reader. With a UID (uid_len > 0) the card is just an
 * identifier, as with the account-based next-generation Clipper: its balance
 * and trip state come from the reader's account table (a new UID is enrolled
 * with ACCOUNT_START_CENTS). Without a UID, the fields below are used as-is.
 */
typedef struct {
    uint8_t uid[10];
    uint8_t uid_len;
    int32_t balance_cents;
    bool discount;
    bool tagged_on;             /* card currently has an open Caltrain trip */
} reader_card_t;

#define ACCOUNT_START_CENTS 2000
#define MAX_ACCOUNTS        16

typedef struct {
    uint8_t uid[10];
    uint8_t uid_len;
    int32_t balance_cents;
    bool tagged_on;
} reader_account_t;

/* Snapshot of the account table; returns the number of accounts copied. */
int reader_accounts(reader_account_t *out, int max);

esp_err_t reader_init(void);

/* Real path: a card was read. The result's balance is what should be written back. */
void reader_present_card(const reader_card_t *card);

/* Debug: tap the built-in virtual card (balance editable from the debug panel). */
void reader_tap_virtual_card(void);

/* Debug: show a given result without touching any balance. */
void reader_force_result(reader_result_kind_t kind);

/* Virtual card balance. */
int32_t reader_virtual_balance(void);
void reader_set_virtual_balance(int32_t cents);
