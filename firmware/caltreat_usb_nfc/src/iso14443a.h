/*
 * ISO/IEC 14443-A activation: REQA/WUPA, bit-frame anticollision and SELECT
 * across cascade levels, plus HLTA. Built on the MFRC522 transceive helpers.
 */

#ifndef ISO14443A_H
#define ISO14443A_H

#include <stdbool.h>
#include <stdint.h>

#include "mfrc522.h"

typedef struct {
    uint8_t atqa[2];   /* ATQA as received (atqa[0] = first byte) */
    uint8_t uid[10];   /* 4, 7 or 10 byte UID */
    uint8_t uid_len;   /* 4 / 7 / 10 */
    uint8_t sak;       /* SAK of the final cascade level */
} iso14443a_target_t;

/* REQA (use_wupa=false) or WUPA (use_wupa=true) then full anticollision+select.
 * Fills *t on success. Returns MFRC522_ERR_TIMEOUT when no card is present. */
mfrc522_status_t iso14443a_select(iso14443a_target_t *t, bool use_wupa);

/* HLTA (halt) - card stops responding until the field is re-cycled / WUPA. */
void iso14443a_halt(void);

#endif /* ISO14443A_H */
