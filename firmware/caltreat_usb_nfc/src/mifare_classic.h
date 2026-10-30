/*
 * MIFARE Classic block operations: authenticate (via the MFRC522 crypto1
 * engine), read a 16-byte block, write a 16-byte block. The tag must already be
 * selected (see iso14443a_select) and its 4-byte UID passed to mfc_auth.
 */

#ifndef MIFARE_CLASSIC_H
#define MIFARE_CLASSIC_H

#include <stdint.h>

#include "mfrc522.h"

#define MFC_KEY_A 0x60
#define MFC_KEY_B 0x61

/* Value-block operations (operate on the card's internal transfer buffer;
 * MFC_CMD_TRANSFER commits it to a block). */
#define MFC_CMD_DECREMENT 0xC0
#define MFC_CMD_INCREMENT 0xC1
#define MFC_CMD_RESTORE   0xC2
#define MFC_CMD_TRANSFER  0xB0

/* Authenticate for `block` using key_cmd (MFC_KEY_A/MFC_KEY_B). Auth stays valid
 * for the sector until crypto is stopped or another auth runs. */
mfrc522_status_t mfc_auth(uint8_t key_cmd, uint8_t block,
                          const uint8_t key[6], const uint8_t uid[4]);

/* Read 16 bytes from an authenticated block into out16 (CRC is checked). */
mfrc522_status_t mfc_read(uint8_t block, uint8_t out16[16]);

/* Write 16 bytes to an authenticated block from in16. */
mfrc522_status_t mfc_write(uint8_t block, const uint8_t in16[16]);

/* Increment / decrement / restore (op = MFC_CMD_*) of a value block into the
 * transfer buffer. value is the 4-byte little-endian operand (ignored by the
 * card for restore). */
mfrc522_status_t mfc_value(uint8_t op, uint8_t block, const uint8_t value[4]);

/* Write the transfer buffer to block. */
mfrc522_status_t mfc_transfer(uint8_t block);

#endif /* MIFARE_CLASSIC_H */
