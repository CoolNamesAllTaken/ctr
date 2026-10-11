/*
 * USB descriptors for the 2x CDC composite device.
 *
 *   CDC0 (itf 0/1): debug console, used by pico stdio (printf)
 *   CDC1 (itf 2/3): PN532 protocol port (binary)
 *
 * These replace the pico_stdio_usb default descriptors (disabled via
 * PICO_STDIO_USB_USE_DEFAULT_DESCRIPTORS=0). stdio writes to CDC interface 0.
 *
 * VID/PID: use the Raspberry Pi VID with a distinct PID. Note that libnfc finds
 * a pn532_uart device by the serial port path, not by USB id, so the id only
 * affects enumeration/driver binding (CDC-ACM is class-driver on all 3 OSes).
 */

#include <string.h>

#include "tusb.h"
#include "pico/unique_id.h"

#define USBD_VID            0x2E8A /* Raspberry Pi */
#define USBD_PID            0x000B /* distinct from the SDK CDC pids (0x000a/9) */

#define EPNUM_CDC0_NOTIF    0x81
#define EPNUM_CDC0_OUT      0x02
#define EPNUM_CDC0_IN       0x82
#define EPNUM_CDC1_NOTIF    0x83
#define EPNUM_CDC1_OUT      0x04
#define EPNUM_CDC1_IN       0x84

enum {
    ITF_NUM_CDC0 = 0,
    ITF_NUM_CDC0_DATA,
    ITF_NUM_CDC1,
    ITF_NUM_CDC1_DATA,
    ITF_NUM_TOTAL
};

/* ---- Device descriptor ---- */
static const tusb_desc_device_t desc_device = {
    .bLength            = sizeof(tusb_desc_device_t),
    .bDescriptorType    = TUSB_DESC_DEVICE,
    .bcdUSB             = 0x0200,
    /* Composite: IAD associates the CDC interface pairs. */
    .bDeviceClass       = TUSB_CLASS_MISC,
    .bDeviceSubClass    = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol    = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0    = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor           = USBD_VID,
    .idProduct          = USBD_PID,
    .bcdDevice          = 0x0100,
    .iManufacturer      = 0x01,
    .iProduct           = 0x02,
    .iSerialNumber      = 0x03,
    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void) {
    return (uint8_t const *)&desc_device;
}

/* ---- Configuration descriptor ---- */
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + 2 * TUD_CDC_DESC_LEN)

static const uint8_t desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 250),

    /* CDC0 — debug console. String index 4. */
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC0, 4, EPNUM_CDC0_NOTIF, 8,
                       EPNUM_CDC0_OUT, EPNUM_CDC0_IN, 64),

    /* CDC1 — PN532 protocol. String index 5. */
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC1, 5, EPNUM_CDC1_NOTIF, 8,
                       EPNUM_CDC1_OUT, EPNUM_CDC1_IN, 64),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return desc_configuration;
}

/* ---- String descriptors ---- */
static const char *const string_desc_arr[] = {
    (const char[]){ 0x09, 0x04 }, /* 0: language = English (US) */
    "Raspberry Pi",               /* 1: Manufacturer */
    "RC522 PN532 Bridge",         /* 2: Product */
    NULL,                         /* 3: Serial — filled from the chip id */
    "RC522 Debug",                /* 4: CDC0 interface */
    "PN532 Emulation",            /* 5: CDC1 interface */
};

static uint16_t desc_str[32];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;
    size_t count = 0;

    if (index == 0) {
        memcpy(&desc_str[1], string_desc_arr[0], 2);
        count = 1;
    } else if (index == 3) {
        /* Unique serial from the flash/chip id, as hex. */
        char serial[2 * PICO_UNIQUE_BOARD_ID_SIZE_BYTES + 1];
        pico_get_unique_board_id_string(serial, sizeof(serial));
        count = strlen(serial);
        if (count > 31) count = 31;
        for (size_t i = 0; i < count; i++) desc_str[1 + i] = (uint16_t)serial[i];
    } else {
        if (index >= sizeof(string_desc_arr) / sizeof(string_desc_arr[0])) return NULL;
        const char *str = string_desc_arr[index];
        if (!str) return NULL;
        count = strlen(str);
        if (count > 31) count = 31;
        for (size_t i = 0; i < count; i++) desc_str[1 + i] = (uint16_t)str[i];
    }

    /* First word: length (bytes, incl. header) and descriptor type. */
    desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * count + 2));
    return desc_str;
}
