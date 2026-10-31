// HUB75 pin map of the Waveshare ESP32-S3-RGB-Matrix (SKU 34422).
// CONFIRMED by three Waveshare sources that agree:
//   - schematic GPIO table (hardware/schematics/ESP32-S3-RGB-Matrix-Schematics.pdf, "Lattice" column)
//   - ESP-IDF example sdkconfig.defaults (CONFIG_HUB75_PIN_*)
//   - the library copy bundled with the Arduino examples (platforms/esp32s3/esp32s3-default-pins.hpp)
// The signals pass through two SN74HC245 buffers (5 V) to the 2x8 HUB75 header J1.
#pragma once
#define PIN_R1  4
#define PIN_G1  5
#define PIN_B1  6
#define PIN_R2  7
#define PIN_G2  15
#define PIN_B2  16
#define PIN_A   18
#define PIN_B   8
#define PIN_C   3
#define PIN_D   42
#define PIN_E   9   // 64x32 (1/16 scan) never sets E, but keep it driven: an undriven GPIO9 would leave
                    // the HC245 input floating and HUB75 pin 8 (E, or GND on some panels) undefined.
#define PIN_LAT 40
#define PIN_OE  2
#define PIN_CLK 41

// Other on-board I/O (from the schematic / BSP config.h), for optional features:
#define PIN_BOOT_BUTTON 0     // BOOT key (active low); used here to step brightness
#define PIN_I2C_SDA     47    // PCF85063 RTC, QMI8658 IMU, SHTC3 temp/humidity, ES8311/ES7210 codecs
#define PIN_I2C_SCL     48
#define PIN_RTC_INT     10
#define PIN_PA_CTRL     11    // speaker amplifier enable (NS4150B)
