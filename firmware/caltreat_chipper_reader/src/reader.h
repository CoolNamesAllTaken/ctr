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
#include "card.h"

typedef enum {
    READER_RESULT_OK,           /* tag on / fare paid: one beep */
    READER_RESULT_LOW_BALANCE,  /* paid, balance under threshold: two beeps */
    READER_RESULT_TAG_OFF,      /* Caltrain tag off: rising two-tone */
    READER_RESULT_INSUFFICIENT, /* not enough value: three beeps */
    READER_RESULT_CARD_ERROR,   /* unreadable, tap again: warning tone */
    READER_RESULT_ALREADY_TAPPED, /* ride used at this reader already: neutral, one beep */
    READER_RESULT_NOT_CHIPPER,  /* not a Chipper card: warning tone */
} reader_result_kind_t;

typedef struct {
    reader_result_kind_t kind;
    int32_t fare_cents;         /* fare charged (or that would have been) */
    int32_t balance_cents;      /* remaining balance after the tap */
    bool discount;
    bool tagged_on;             /* card's trip state after the tap */
    uint8_t uid[10];            /* physical card that was tapped (uid_len 0 = virtual/forced) */
    uint8_t uid_len;
    int16_t rides_left;         /* Chipper card whole rides after the tap; -1 = virtual/forced (money screens) */
} reader_result_t;

/* Debug: a card given by its fields (serial "card"), charged with the fare rules
 * of the debug sheet like the virtual card. */
typedef struct {
    int32_t balance_cents;
    bool discount;
    bool tagged_on;             /* card currently has an open Caltrain trip */
} reader_card_t;

esp_err_t reader_init(void);

/* Real path: a Chipper card was tapped and card_tap() returned `st`. */
void reader_card_result(const rc522_card_t *card, card_status_t st, uint8_t half_rides_left);

/* Debug: present a card given by its fields. */
void reader_present_card(const reader_card_t *card);

/* Debug: tap the built-in virtual card (balance editable from the debug panel). */
void reader_tap_virtual_card(void);

/* Debug: show a given result without touching any balance. */
void reader_force_result(reader_result_kind_t kind);

/* No tap being processed and no result on screen (e.g. safe to start an update). */
bool reader_is_idle(void);

/* Virtual card balance. */
int32_t reader_virtual_balance(void);
void reader_set_virtual_balance(int32_t cents);
