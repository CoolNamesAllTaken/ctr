/*
 * Low-level NXP MFRC522 driver over SPI.
 *
 * Covers register access, FIFO transceive, CRC coprocessor use and the MIFARE
 * authentication command. Higher layers (iso14443a, mifare_classic) build on
 * mfrc522_transceive() / mfrc522_calc_crc() / mfrc522_mifare_auth().
 */

#ifndef MFRC522_H
#define MFRC522_H

#include <stdbool.h>
#include <stdint.h>

/* ---- MFRC522 registers (datasheet section 9) ---- */
enum {
    CommandReg      = 0x01,
    ComIEnReg       = 0x02,
    ComIrqReg       = 0x04,
    DivIrqReg       = 0x05,
    ErrorReg        = 0x06,
    Status1Reg      = 0x07,
    Status2Reg      = 0x08,
    FIFODataReg     = 0x09,
    FIFOLevelReg    = 0x0A,
    ControlReg      = 0x0C,
    BitFramingReg   = 0x0D,
    CollReg         = 0x0E,
    ModeReg         = 0x11,
    TxModeReg       = 0x12,
    RxModeReg       = 0x13,
    TxControlReg    = 0x14,
    TxASKReg        = 0x15,
    CRCResultRegH   = 0x21,
    CRCResultRegL   = 0x22,
    TModeReg        = 0x2A,
    TPrescalerReg   = 0x2B,
    TReloadRegH     = 0x2C,
    TReloadRegL     = 0x2D,
    VersionReg      = 0x37,
};

/* ---- MFRC522 commands ---- */
enum {
    PCD_Idle          = 0x00,
    PCD_Mem           = 0x01,
    PCD_CalcCRC       = 0x03,
    PCD_Transmit      = 0x04,
    PCD_NoCmdChange   = 0x07,
    PCD_Receive       = 0x08,
    PCD_Transceive    = 0x0C,
    PCD_MFAuthent     = 0x0E,
    PCD_SoftReset     = 0x0F,
};

/* Status codes returned by the transceive helpers. */
typedef enum {
    MFRC522_OK = 0,
    MFRC522_ERR_TIMEOUT,  /* no tag / no response */
    MFRC522_ERR_COMM,     /* buffer overflow, protocol, FIFO, etc. */
    MFRC522_ERR_COLLISION,
    MFRC522_ERR_CRC,
    MFRC522_ERR_PARAM,
} mfrc522_status_t;

/* Initialise SPI, reset the chip and configure it for ISO14443A at 106 kbps.
 * Returns the VersionReg value (0x91/0x92 on a genuine MFRC522; 0x00/0xFF means
 * the chip is not responding). */
uint8_t mfrc522_init(void);

void mfrc522_antenna_on(void);
void mfrc522_antenna_off(void);

/* Clear Status2Reg.MFCrypto1On so the next select/REQA works after an auth. */
void mfrc522_stop_crypto1(void);

/* Raw register access (exposed so the PN532 register-shadow can be backed by
 * real CIU registers where it helps compatibility). */
uint8_t mfrc522_read_reg(uint8_t reg);
void    mfrc522_write_reg(uint8_t reg, uint8_t val);

/*
 * Run the Transceive command: send tx_len bytes (the last byte sending only
 * tx_last_bits bits when tx_last_bits != 0, for short/anticollision frames),
 * then receive into rx. On entry *rx_len is the capacity of rx; on success it
 * holds the number of received bytes and *rx_last_bits the valid bits in the
 * final byte (0 == all 8).
 */
mfrc522_status_t mfrc522_transceive(const uint8_t *tx, uint8_t tx_len,
                                    uint8_t tx_last_bits,
                                    uint8_t *rx, uint8_t *rx_len,
                                    uint8_t *rx_last_bits);

/* Compute CRC_A over data[0..len) using the MFRC522 coprocessor.
 * out[0] = CRC low byte, out[1] = CRC high byte (ISO14443A transmission order). */
mfrc522_status_t mfrc522_calc_crc(const uint8_t *data, uint8_t len, uint8_t out[2]);

/* Append CRC_A over buf[0..len) as buf[len], buf[len+1]. */
mfrc522_status_t mfrc522_append_crc(uint8_t *buf, uint8_t len);

/* Check that the last two bytes of buf[0..len) are a valid CRC_A over the rest.
 * Returns MFRC522_ERR_CRC on mismatch (or when len < 2). */
mfrc522_status_t mfrc522_check_crc(const uint8_t *buf, uint8_t len);

/*
 * MIFARE Classic authentication. key_cmd is 0x60 (key A) or 0x61 (key B).
 * key is 6 bytes; uid is the 4 UID bytes used for auth (the last 4 of a
 * 7-byte UID). Returns MFRC522_OK
 * when the crypto handshake succeeds (Status2Reg.MFCrypto1On set).
 */
mfrc522_status_t mfrc522_mifare_auth(uint8_t key_cmd, uint8_t block,
                                     const uint8_t key[6], const uint8_t uid[4]);

#endif /* MFRC522_H */
