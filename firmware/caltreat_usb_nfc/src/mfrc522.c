#include "mfrc522.h"

#include "pins.h"
#include "hardware/gpio.h"
#include "hardware/spi.h"
#include "pico/stdlib.h"

/* ---- SPI register access -------------------------------------------------- */
/* MFRC522 SPI address byte: bit0 is always 0, bits6..1 are the register,
 * bit7 selects read(1)/write(0). (datasheet 8.1.2.3) */
static inline uint8_t addr_read(uint8_t reg)  { return (uint8_t)(0x80 | ((reg << 1) & 0x7E)); }
static inline uint8_t addr_write(uint8_t reg) { return (uint8_t)(((reg << 1) & 0x7E)); }

static inline void cs_select(void)   { gpio_put(PIN_RC522_CS, 0); }
static inline void cs_deselect(void) { gpio_put(PIN_RC522_CS, 1); }

uint8_t mfrc522_read_reg(uint8_t reg) {
    uint8_t tx[2] = { addr_read(reg), 0x00 };
    uint8_t rx[2] = { 0, 0 };
    cs_select();
    spi_write_read_blocking(RC522_SPI_PORT, tx, rx, 2);
    cs_deselect();
    return rx[1];
}

void mfrc522_write_reg(uint8_t reg, uint8_t val) {
    uint8_t tx[2] = { addr_write(reg), val };
    cs_select();
    spi_write_blocking(RC522_SPI_PORT, tx, 2);
    cs_deselect();
}

static void set_bits(uint8_t reg, uint8_t mask) {
    mfrc522_write_reg(reg, (uint8_t)(mfrc522_read_reg(reg) | mask));
}
static void clear_bits(uint8_t reg, uint8_t mask) {
    mfrc522_write_reg(reg, (uint8_t)(mfrc522_read_reg(reg) & (uint8_t)~mask));
}

/* ---- Antenna / crypto ----------------------------------------------------- */
void mfrc522_antenna_on(void) {
    if ((mfrc522_read_reg(TxControlReg) & 0x03) != 0x03) {
        set_bits(TxControlReg, 0x03); /* Tx1RFEn | Tx2RFEn */
    }
}
void mfrc522_antenna_off(void) {
    clear_bits(TxControlReg, 0x03);
}
void mfrc522_stop_crypto1(void) {
    clear_bits(Status2Reg, 0x08); /* MFCrypto1On */
}

/* ---- Init ----------------------------------------------------------------- */
static void hard_reset(void) {
    gpio_put(PIN_RC522_RST, 0);
    sleep_ms(2);
    gpio_put(PIN_RC522_RST, 1);
    sleep_ms(50); /* oscillator start-up */
}

uint8_t mfrc522_init(void) {
    /* SPI pins */
    spi_init(RC522_SPI_PORT, RC522_SPI_BAUD);
    spi_set_format(RC522_SPI_PORT, 8, SPI_CPOL_0, SPI_CPHA_0, SPI_MSB_FIRST);
    gpio_set_function(PIN_RC522_SCK, GPIO_FUNC_SPI);
    gpio_set_function(PIN_RC522_MOSI, GPIO_FUNC_SPI);
    gpio_set_function(PIN_RC522_MISO, GPIO_FUNC_SPI);

    /* Manual CS */
    gpio_init(PIN_RC522_CS);
    gpio_set_dir(PIN_RC522_CS, GPIO_OUT);
    gpio_put(PIN_RC522_CS, 1);

    /* Reset line */
    gpio_init(PIN_RC522_RST);
    gpio_set_dir(PIN_RC522_RST, GPIO_OUT);
    gpio_put(PIN_RC522_RST, 1);

    /* IRQ input (unused for now, but park it defined) */
    gpio_init(PIN_RC522_IRQ);
    gpio_set_dir(PIN_RC522_IRQ, GPIO_IN);
    gpio_pull_up(PIN_RC522_IRQ);

    hard_reset();

    /* Soft reset; wait for the PowerDown bit to clear. */
    mfrc522_write_reg(CommandReg, PCD_SoftReset);
    for (int i = 0; i < 50; i++) {
        if (!(mfrc522_read_reg(CommandReg) & 0x10)) break;
        sleep_ms(1);
    }

    /* Timer: TAuto, 40 kHz => ~25 ms timeout at TReload=1000.
     * f_timer = 13.56MHz / (2*TPrescaler+1); TPrescaler=0xA9(169) -> ~40kHz. */
    mfrc522_write_reg(TModeReg, 0x80);       /* TAuto=1 */
    mfrc522_write_reg(TPrescalerReg, 0xA9);
    mfrc522_write_reg(TReloadRegH, 0x03);
    mfrc522_write_reg(TReloadRegL, 0xE8);    /* 0x03E8 = 1000 */

    mfrc522_write_reg(TxASKReg, 0x40);       /* Force100ASK */
    mfrc522_write_reg(ModeReg, 0x3D);        /* CRC preset 0x6363, TxWaitRF, polarity */

    mfrc522_antenna_on();

    return mfrc522_read_reg(VersionReg);
}

/* ---- FIFO transceive ------------------------------------------------------ */
/*
 * Generic command runner for Transceive/Authent. Loads tx into the FIFO, kicks
 * the command, and (for Transceive) sets StartSend. Waits on ComIrqReg for the
 * command to finish or the timer to expire.
 */
static mfrc522_status_t command_transceive(uint8_t command,
                                           const uint8_t *tx, uint8_t tx_len,
                                           uint8_t tx_last_bits,
                                           uint8_t *rx, uint8_t *rx_len,
                                           uint8_t *rx_last_bits,
                                           bool check_crc_collision) {
    /* waitIRq: for Transceive the RxIRq(0x20) or IdleIRq(0x10). */
    uint8_t wait_irq = (command == PCD_Transceive) ? 0x30 : 0x10;

    mfrc522_write_reg(ComIEnReg, 0x77 | 0x80); /* enable IRQ propagation */
    clear_bits(ComIrqReg, 0x80);               /* clear all IRQ bits (Set1=0) */
    set_bits(FIFOLevelReg, 0x80);              /* FlushBuffer */

    mfrc522_write_reg(CommandReg, PCD_Idle);
    for (uint8_t i = 0; i < tx_len; i++) {
        mfrc522_write_reg(FIFODataReg, tx[i]);
    }

    mfrc522_write_reg(BitFramingReg, (uint8_t)(tx_last_bits & 0x07));
    mfrc522_write_reg(CommandReg, command);
    if (command == PCD_Transceive) {
        set_bits(BitFramingReg, 0x80); /* StartSend */
    }

    /* Poll ComIrqReg. The MFRC522 timer bounds this (~25 ms); cap iterations as
     * a hard backstop so a dead chip can't wedge the loop. */
    uint16_t guard = 2000;
    uint8_t irq;
    do {
        irq = mfrc522_read_reg(ComIrqReg);
        if (irq & wait_irq) break;      /* command finished */
        if (irq & 0x01) {               /* TimerIRq -> timeout */
            clear_bits(BitFramingReg, 0x80);
            return MFRC522_ERR_TIMEOUT;
        }
    } while (--guard);

    clear_bits(BitFramingReg, 0x80); /* stop StartSend */

    if (guard == 0) return MFRC522_ERR_TIMEOUT;

    uint8_t err = mfrc522_read_reg(ErrorReg);
    if (err & 0x13) { /* BufferOvfl | ParityErr | ProtocolErr */
        return MFRC522_ERR_COMM;
    }
    if (check_crc_collision && (err & 0x08)) { /* CollErr */
        return MFRC522_ERR_COLLISION;
    }

    if (command != PCD_Transceive) {
        return MFRC522_OK;
    }

    uint8_t n = mfrc522_read_reg(FIFOLevelReg);
    uint8_t last = (uint8_t)(mfrc522_read_reg(ControlReg) & 0x07);

    if (rx && rx_len) {
        if (n > *rx_len) return MFRC522_ERR_COMM; /* won't fit */
        for (uint8_t i = 0; i < n; i++) rx[i] = mfrc522_read_reg(FIFODataReg);
        *rx_len = n;
        if (rx_last_bits) *rx_last_bits = last;
    }
    return MFRC522_OK;
}

mfrc522_status_t mfrc522_transceive(const uint8_t *tx, uint8_t tx_len,
                                    uint8_t tx_last_bits,
                                    uint8_t *rx, uint8_t *rx_len,
                                    uint8_t *rx_last_bits) {
    return command_transceive(PCD_Transceive, tx, tx_len, tx_last_bits,
                              rx, rx_len, rx_last_bits, true);
}

/* ---- CRC coprocessor ------------------------------------------------------ */
mfrc522_status_t mfrc522_calc_crc(const uint8_t *data, uint8_t len, uint8_t out[2]) {
    mfrc522_write_reg(CommandReg, PCD_Idle);
    clear_bits(DivIrqReg, 0x04);   /* clear CRCIRq */
    set_bits(FIFOLevelReg, 0x80);  /* flush FIFO */
    for (uint8_t i = 0; i < len; i++) mfrc522_write_reg(FIFODataReg, data[i]);
    mfrc522_write_reg(CommandReg, PCD_CalcCRC);

    uint16_t guard = 5000;
    while (--guard) {
        if (mfrc522_read_reg(DivIrqReg) & 0x04) { /* CRCIRq */
            mfrc522_write_reg(CommandReg, PCD_Idle);
            out[0] = mfrc522_read_reg(CRCResultRegL);
            out[1] = mfrc522_read_reg(CRCResultRegH);
            return MFRC522_OK;
        }
    }
    return MFRC522_ERR_TIMEOUT;
}

/* ---- MIFARE auth ---------------------------------------------------------- */
mfrc522_status_t mfrc522_mifare_auth(uint8_t key_cmd, uint8_t block,
                                     const uint8_t key[6], const uint8_t uid[4]) {
    uint8_t buf[12];
    buf[0] = key_cmd;           /* 0x60 (A) or 0x61 (B) */
    buf[1] = block;
    for (int i = 0; i < 6; i++) buf[2 + i] = key[i];
    for (int i = 0; i < 4; i++) buf[8 + i] = uid[i];

    mfrc522_status_t st = command_transceive(PCD_MFAuthent, buf, sizeof(buf), 0,
                                              NULL, NULL, NULL, false);
    if (st != MFRC522_OK) return st;

    /* Crypto1 engine must now be on. */
    if (!(mfrc522_read_reg(Status2Reg) & 0x08)) return MFRC522_ERR_COMM;
    return MFRC522_OK;
}
