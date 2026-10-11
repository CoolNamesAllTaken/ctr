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

/* Authenticate for `block` using key_cmd (MFC_KEY_A/MFC_KEY_B). Auth stays valid
 * for the sector until crypto is stopped or another auth runs. */
mfrc522_status_t mfc_auth(uint8_t key_cmd, uint8_t block,
                          const uint8_t key[6], const uint8_t uid[4]);

/* Read 16 bytes from an authenticated block into out16 (CRC is checked). */
mfrc522_status_t mfc_read(uint8_t block, uint8_t out16[16]);

/* Write 16 bytes to an authenticated block from in16. */
mfrc522_status_t mfc_write(uint8_t block, const uint8_t in16[16]);

#endif /* MIFARE_CLASSIC_H */
