#pragma once

#include "esp_err.h"

/* RFID-RC522 wiring (SPI; the module's "SDA" pin is the SPI chip select). */
#define NFC_GPIO_RST   26
#define NFC_GPIO_IRQ   47   /* wired, but unused: the reader is polled */
#define NFC_GPIO_MISO  48
#define NFC_GPIO_MOSI  29
#define NFC_GPIO_SCK   30
#define NFC_GPIO_CS    31

/* Start polling the RC522; each new card in the field becomes one tap. */
esp_err_t nfc_init(void);
