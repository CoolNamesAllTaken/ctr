/*
 * Pin and SPI assignments for the RC522 <-> RP2040-Zero wiring.
 *
 *   GPIO9  SDA/NSS  -> manual chip-select (SIO output)
 *   GPIO10 SCK      -> SPI1 SCK
 *   GPIO11 MOSI     -> SPI1 TX
 *   GPIO12 MISO     -> SPI1 RX
 *   GPIO13 IRQ      -> input (unused for now; polling)
 *   GPIO14 RST      -> active-low reset (output)
 *
 * GPIO10-13 are the SPI1 function pins on the RP2040, so the RC522 lives on the
 * SPI1 instance. SDA is driven by hand because the MFRC522 wants CS asserted for
 * the whole multi-byte register transaction.
 */

#ifndef PINS_H
#define PINS_H

#include "hardware/spi.h"

#define RC522_SPI_PORT   spi1
#define RC522_SPI_BAUD   (4u * 1000u * 1000u) /* 4 MHz; MFRC522 is good to 10 MHz */

#define PIN_RC522_CS     9
#define PIN_RC522_SCK    10
#define PIN_RC522_MOSI   11
#define PIN_RC522_MISO   12
#define PIN_RC522_IRQ    13
#define PIN_RC522_RST    14

/* Onboard WS2812 on the Waveshare RP2040-Zero (status LED, optional). */
#define PIN_WS2812       16

#endif /* PINS_H */
