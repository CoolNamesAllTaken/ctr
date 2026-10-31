/*
 * Chipper card rides, stored on a MIFARE Classic 1K card (spec:
 * firmware/docs/chipper_card.md). Sector 1, block 4, factory keys:
 *
 *   0-3 "CHPR"  4 version (2)  5 half rides  6-13 zero  14-15 CRC_A of 0-13 (LE)
 *
 * Rides are counted in halves: tap on takes one half, tap off the other. An
 * even count = whole rides, not on a trip; odd = tapped on. Version 1 cards
 * (byte 5 = whole rides) are read as 2 x rides and rewritten as version 2.
 *
 * The card is the source of truth. All functions run on a card selected with
 * rc522_select(), from the NFC task only.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>
#include "rc522.h"

typedef enum {
    CARD_TAP_ON,          /* half a ride taken, rider is now on a trip */
    CARD_TAP_OFF,         /* the trip's second half taken */
    CARD_NO_RIDES,        /* 0 half rides; nothing written */
    CARD_ALREADY_TAPPED,  /* tapped on at this reader moments ago; not touched */
    CARD_NOT_CHIPPER,     /* other keys, blank card, wrong magic or version */
    CARD_DAMAGED,         /* magic ok, CRC wrong (torn or edited write); nothing written */
    CARD_RF_ERROR,        /* card left or the exchange failed; re-tap reads the real state */
    CARD_VALID,           /* card_read() only: a good ride block */
} card_status_t;

/* Tap on or off according to the card's half rides (see the spec's table). */
card_status_t card_tap(const rc522_card_t *card, uint8_t *half_rides_left);

/* Read and check the ride block without changing it. CARD_VALID on success.
 * version and raw (both optional) get the stored version and the block. */
card_status_t card_read(const rc522_card_t *card, uint8_t *half_rides, uint8_t *version, uint8_t raw[16]);

/* CRC-16/ISO-IEC-14443-3-A (CRC_A); "123456789" -> 0xBF05. */
uint16_t card_crc16(const uint8_t *data, size_t len);

/* Build a valid version 2 ride block. */
void card_make_block(uint8_t half_rides, uint8_t out16[16]);

const char *card_status_name(card_status_t st);
