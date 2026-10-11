/*
 * Runtime-tunable settings, edited from the debug overlay.
 *
 * Fields are plain word-sized values written from the LVGL task and read
 * from the reader/audio tasks; a torn read is impossible and a stale read
 * only delays a setting change by one event, so no lock is used.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    /* fare rules */
    int32_t fare_cents;           /* adult fare charged per tap */
    int32_t low_balance_cents;    /* below this after paying -> yellow / two beeps */
    bool    discount;             /* discount card: half fare */
    bool    tag_on_off;           /* Caltrain-style: second tap on a card tags off */

    /* audio */
    uint8_t  volume;              /* 0..100 */
    bool     locator_tone;        /* accessibility chirp every 15 s while idle */

    /* display */
    uint8_t  brightness;          /* 0..100 */
    uint16_t result_ms;           /* how long a result stays on screen */
    bool     flip;                /* portrait, rotated 180 degrees (other mounting side) */
} app_settings_t;

extern app_settings_t g_settings;
