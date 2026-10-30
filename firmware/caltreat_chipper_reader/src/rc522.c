#include "rc522.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "rc522";

/* registers (MFRC522 datasheet, section 9) */
#define REG_COMMAND     0x01
#define REG_COM_IRQ     0x04
#define REG_ERROR       0x06
#define REG_STATUS2     0x08
#define REG_FIFO_DATA   0x09
#define REG_FIFO_LEVEL  0x0A
#define REG_CONTROL     0x0C
#define REG_BIT_FRAMING 0x0D
#define REG_COLL        0x0E
#define REG_MODE        0x11
#define REG_TX_MODE     0x12
#define REG_RX_MODE     0x13
#define REG_TX_CONTROL  0x14
#define REG_TX_ASK      0x15
#define REG_MOD_WIDTH   0x24
#define REG_T_MODE      0x2A
#define REG_T_PRESCALER 0x2B
#define REG_T_RELOAD_H  0x2C
#define REG_T_RELOAD_L  0x2D
#define REG_VERSION     0x37

/* commands */
#define CMD_IDLE        0x00
#define CMD_TRANSCEIVE  0x0C
#define CMD_SOFT_RESET  0x0F

/* ComIrqReg bits */
#define IRQ_TIMER       0x01
#define IRQ_IDLE        0x10
#define IRQ_RX          0x20

/* ISO 14443-3 type A */
#define PICC_WUPA       0x52
#define PICC_SEL_CL1    0x93
#define PICC_SEL_CL2    0x95
#define PICC_SEL_CL3    0x97
#define PICC_HLTA       0x50
#define PICC_CASCADE_TAG 0x88

static spi_device_handle_t s_dev;

static void write_reg(uint8_t reg, uint8_t val)
{
    spi_transaction_t t = {
        .length = 16,
        .flags = SPI_TRANS_USE_TXDATA,
        .tx_data = {(reg << 1) & 0x7E, val},
    };
    spi_device_polling_transmit(s_dev, &t);
}

static uint8_t read_reg(uint8_t reg)
{
    spi_transaction_t t = {
        .length = 16,
        .flags = SPI_TRANS_USE_TXDATA | SPI_TRANS_USE_RXDATA,
        .tx_data = {((reg << 1) & 0x7E) | 0x80, 0},
    };
    spi_device_polling_transmit(s_dev, &t);
    return t.rx_data[1];
}

static void set_bits(uint8_t reg, uint8_t mask)
{
    write_reg(reg, read_reg(reg) | mask);
}

static void clear_bits(uint8_t reg, uint8_t mask)
{
    write_reg(reg, read_reg(reg) & ~mask);
}

/* ISO 14443A CRC_A (initial value 0x6363), appended little-endian. */
static void crc_a(const uint8_t *data, size_t len, uint8_t out[2])
{
    uint16_t crc = 0x6363;
    for (size_t i = 0; i < len; i++) {
        uint8_t b = data[i] ^ (crc & 0xFF);
        b ^= b << 4;
        crc = (crc >> 8) ^ ((uint16_t)b << 8) ^ ((uint16_t)b << 3) ^ (b >> 4);
    }
    out[0] = crc & 0xFF;
    out[1] = crc >> 8;
}

typedef enum { XFER_OK, XFER_TIMEOUT, XFER_COLLISION, XFER_ERROR } xfer_t;

/*
 * Send `tx` and receive into `rx`. `tx_last_bits` = number of valid bits in the
 * last sent byte (0 = all 8). Returns received length in bytes via rx_len.
 */
static xfer_t transceive(const uint8_t *tx, size_t tx_len, uint8_t tx_last_bits,
                         uint8_t *rx, size_t rx_max, size_t *rx_len)
{
    write_reg(REG_COMMAND, CMD_IDLE);
    write_reg(REG_COM_IRQ, 0x7F);           /* clear all IRQ flags */
    write_reg(REG_FIFO_LEVEL, 0x80);        /* flush FIFO */
    for (size_t i = 0; i < tx_len; i++) {
        write_reg(REG_FIFO_DATA, tx[i]);
    }
    write_reg(REG_BIT_FRAMING, tx_last_bits & 0x07);
    write_reg(REG_COMMAND, CMD_TRANSCEIVE);
    set_bits(REG_BIT_FRAMING, 0x80);        /* StartSend */

    /* The chip's own timer (25 ms, set in init) raises TimerIRq on no reply. */
    const int64_t deadline = esp_timer_get_time() + 40000;
    uint8_t irq;
    while (true) {
        irq = read_reg(REG_COM_IRQ);
        if (irq & (IRQ_RX | IRQ_IDLE)) {
            break;
        }
        if ((irq & IRQ_TIMER) || esp_timer_get_time() > deadline) {
            clear_bits(REG_BIT_FRAMING, 0x80);
            return XFER_TIMEOUT;
        }
    }
    clear_bits(REG_BIT_FRAMING, 0x80);

    uint8_t err = read_reg(REG_ERROR);
    if (err & 0x08) {
        return XFER_COLLISION;              /* CollErr */
    }
    if (err & 0x13) {
        return XFER_ERROR;                  /* BufferOvfl, ParityErr, ProtocolErr */
    }
    size_t n = read_reg(REG_FIFO_LEVEL);
    if (n > rx_max) {
        return XFER_ERROR;
    }
    for (size_t i = 0; i < n; i++) {
        rx[i] = read_reg(REG_FIFO_DATA);
    }
    *rx_len = n;
    return XFER_OK;
}

static rc522_status_t status_of(xfer_t x)
{
    return x == XFER_COLLISION ? RC522_COLLISION : RC522_ERROR;
}

rc522_status_t rc522_read_card(rc522_card_t *card)
{
    uint8_t rx[16];
    size_t n = 0;
    memset(card, 0, sizeof(*card));

    /* WUPA (7-bit frame) also wakes cards we halted on a previous read, so a
     * card left on the reader keeps being seen; the caller de-duplicates. */
    write_reg(REG_COLL, 0x80);              /* clear collision-bit handling */
    const uint8_t wupa = PICC_WUPA;
    xfer_t x = transceive(&wupa, 1, 7, rx, sizeof(rx), &n);
    if (x == XFER_TIMEOUT) {
        return RC522_NO_CARD;
    }
    if (x != XFER_OK || n != 2) {
        return status_of(x);
    }
    card->atqa = rx[0] | (rx[1] << 8);

    static const uint8_t sel_cmd[3] = {PICC_SEL_CL1, PICC_SEL_CL2, PICC_SEL_CL3};
    for (int level = 0; level < 3; level++) {
        /* anticollision: get this cascade level's 4 UID bytes + BCC */
        uint8_t ac[2] = {sel_cmd[level], 0x20};
        x = transceive(ac, 2, 0, rx, sizeof(rx), &n);
        if (x != XFER_OK || n != 5) {
            return x == XFER_OK ? RC522_ERROR : status_of(x);
        }
        if ((rx[0] ^ rx[1] ^ rx[2] ^ rx[3]) != rx[4]) {
            return RC522_ERROR;             /* BCC mismatch */
        }
        uint8_t uid_part[5];
        memcpy(uid_part, rx, 5);

        /* select: SEL, NVB=0x70, 4 UID bytes, BCC, CRC_A -> SAK + CRC_A */
        uint8_t sel[9] = {sel_cmd[level], 0x70};
        memcpy(&sel[2], uid_part, 5);
        crc_a(sel, 7, &sel[7]);
        x = transceive(sel, 9, 0, rx, sizeof(rx), &n);
        if (x != XFER_OK || n != 3) {
            return x == XFER_OK ? RC522_ERROR : status_of(x);
        }
        uint8_t crc[2];
        crc_a(rx, 1, crc);
        if (crc[0] != rx[1] || crc[1] != rx[2]) {
            return RC522_ERROR;
        }
        card->sak = rx[0];

        /* a cascade tag means the UID continues at the next level */
        bool more = card->sak & 0x04;
        if (more && uid_part[0] == PICC_CASCADE_TAG) {
            memcpy(&card->uid[card->uid_len], &uid_part[1], 3);
            card->uid_len += 3;
        } else {
            memcpy(&card->uid[card->uid_len], uid_part, 4);
            card->uid_len += 4;
        }
        if (!more) {
            break;
        }
    }

    /* HLTA: the card stops answering REQA until it leaves the field (no reply expected). */
    uint8_t hlta[4] = {PICC_HLTA, 0x00};
    crc_a(hlta, 2, &hlta[2]);
    transceive(hlta, 4, 0, rx, sizeof(rx), &n);
    return RC522_OK;
}

esp_err_t rc522_init(const rc522_config_t *cfg)
{
    const spi_bus_config_t bus = {
        .mosi_io_num = cfg->gpio_mosi,
        .miso_io_num = cfg->gpio_miso,
        .sclk_io_num = cfg->gpio_sck,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 64,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(cfg->spi_host, &bus, SPI_DMA_DISABLED), TAG, "spi bus");
    const spi_device_interface_config_t dev = {
        .clock_speed_hz = cfg->clock_hz,
        .mode = 0,
        .spics_io_num = cfg->gpio_cs,
        .queue_size = 1,
    };
    ESP_RETURN_ON_ERROR(spi_bus_add_device(cfg->spi_host, &dev, &s_dev), TAG, "spi device");

    /* hard reset via NRSTPD */
    const gpio_config_t rst = {
        .pin_bit_mask = 1ULL << cfg->gpio_rst,
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&rst), TAG, "rst gpio");
    gpio_set_level(cfg->gpio_rst, 0);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(cfg->gpio_rst, 1);
    vTaskDelay(pdMS_TO_TICKS(50));

    write_reg(REG_COMMAND, CMD_SOFT_RESET);
    vTaskDelay(pdMS_TO_TICKS(50));

    uint8_t version = read_reg(REG_VERSION);
    ESP_LOGI(TAG, "MFRC522 version 0x%02x", version);
    if (version == 0x00 || version == 0xFF) {
        ESP_LOGE(TAG, "no answer on SPI: check wiring/power");
        return ESP_ERR_NOT_FOUND;
    }

    write_reg(REG_T_MODE, 0x80);            /* timer starts automatically after sending */
    write_reg(REG_T_PRESCALER, 0xA9);       /* 13.56 MHz / (2*169+1) ~ 40 kHz -> 25 us ticks */
    write_reg(REG_T_RELOAD_H, 0x03);        /* 1000 ticks = 25 ms reply timeout */
    write_reg(REG_T_RELOAD_L, 0xE8);
    write_reg(REG_TX_ASK, 0x40);            /* 100% ASK modulation */
    write_reg(REG_MODE, 0x3D);              /* CRC preset 0x6363 (ISO 14443A) */
    write_reg(REG_TX_MODE, 0x00);           /* 106 kbit/s, no hardware CRC */
    write_reg(REG_RX_MODE, 0x00);
    write_reg(REG_MOD_WIDTH, 0x26);
    set_bits(REG_TX_CONTROL, 0x03);         /* antenna on (TX1, TX2) */
    return ESP_OK;
}
