/*
 * Board support for the Elecrow CrowPanel Advanced 5" ESP32-P4 (800x480 RGB, GT911 touch).
 *
 * Pin map and peripheral wiring taken from Elecrow's reference firmware:
 *   I2C bus      SDA=45  SCL=46   (shared: GT911 touch, STC8H1K co-processor)
 *   GT911        RST=36  INT=42
 *   RGB LCD      16-bit RGB565, PCLK=3 DE=2 HSYNC=40 VSYNC=41
 *   I2S speaker  BCLK=22 WS=21 DOUT=23 (mono class-D amp, enable via STC8)
 *   Backlight    STC8 PWM channel 0 + STC8 power GPIO
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "driver/i2c_master.h"

/* Native panel geometry (landscape). The UI runs portrait on top of it. */
#define BOARD_LCD_H_RES 800
#define BOARD_LCD_V_RES 480
#define BOARD_UI_H_RES  480
#define BOARD_UI_V_RES  800

#define BOARD_I2S_GPIO_BCLK 22
#define BOARD_I2S_GPIO_WS   21
#define BOARD_I2S_GPIO_DOUT 23

/* Bring up I2C, STC8, touch, LCD and LVGL (esp_lvgl_port task). */
esp_err_t board_init(void);

/* Portrait orientation; flipped = rotated 180 degrees. Takes the LVGL lock. */
void board_set_flipped(bool flipped);

/* The frame buffer currently being scanned out (native landscape RGB565), for
 * debugging what is really on the panel. */
const uint16_t *board_front_framebuffer(void);

/* Backlight brightness 0..100 (0 also cuts backlight power). */
esp_err_t board_set_backlight(uint8_t percent);

/* Speaker amplifier on/off. */
esp_err_t board_set_amp(bool on);

/* Shared I2C bus, e.g. for an I2C NFC front-end (PN532 etc.) later on. */
i2c_master_bus_handle_t board_i2c_bus(void);
