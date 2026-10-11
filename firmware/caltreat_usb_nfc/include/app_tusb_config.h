/*
 * TinyUSB configuration for the RC522->USB PN532 bridge.
 *
 * Selected via -D CFG_TUSB_CONFIG_FILE=\"app_tusb_config.h\" in platformio.ini so
 * TinyUSB includes this instead of the pico_stdio_usb default (which is a single
 * CDC interface). We expose TWO CDC interfaces:
 *   CDC0 -> debug console, used by pico stdio (printf)
 *   CDC1 -> PN532 binary protocol port
 */

#ifndef APP_TUSB_CONFIG_H
#define APP_TUSB_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Common ---- */
#ifndef CFG_TUSB_MCU
#define CFG_TUSB_MCU            OPT_MCU_RP2040
#endif
#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS             OPT_OS_PICO
#endif
#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG          0
#endif

/* Device mode on root hub port 0 */
#define CFG_TUSB_RHPORT0_MODE   OPT_MODE_DEVICE

/* Memory alignment / section attributes for the RP2040 USB DPRAM */
#ifndef CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_SECTION
#endif
#ifndef CFG_TUSB_MEM_ALIGN
#define CFG_TUSB_MEM_ALIGN      __attribute__((aligned(4)))
#endif

/* ---- Device ---- */
#define CFG_TUD_ENABLED         1

#ifndef CFG_TUD_ENDPOINT0_SIZE
#define CFG_TUD_ENDPOINT0_SIZE  64
#endif

/* Two CDC interfaces, no other classes. We provide our own descriptors, so the
 * pico vendor reset interface is disabled (see platformio.ini). */
#define CFG_TUD_CDC             2
#define CFG_TUD_MSC             0
#define CFG_TUD_HID             0
#define CFG_TUD_MIDI            0
#define CFG_TUD_VENDOR          0

/* FIFO sizes. The PN532 port (CDC1) needs room for a full extended frame plus a
 * 16-byte MIFARE block; 256 bytes is comfortable. */
#define CFG_TUD_CDC_RX_BUFSIZE  256
#define CFG_TUD_CDC_TX_BUFSIZE  256
#define CFG_TUD_CDC_EP_BUFSIZE  64

#ifdef __cplusplus
}
#endif

#endif /* APP_TUSB_CONFIG_H */
