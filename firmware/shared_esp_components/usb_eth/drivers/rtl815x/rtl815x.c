/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * usb_eth driver for the Realtek RTL815x family (vendor configuration).
 *
 * The chip initialisation sequences follow FreeBSD sys/dev/usb/net/if_ure.c:
 * RTL8152: chip_init, chip_nic_reset, chip_disable_aldps (ure_rtl8152_init,
 * ure_rtl8152_nic_reset, ure_enable_aldps); RTL8153B: r8153b_init,
 * r8153b_nic_reset, r8153b_rx_agg (ure_rtl8153b_init, ure_rtl8153b_nic_reset,
 * the 8153B part of ure_init); both: rtl815x_up (ure_init), chip_reset
 * (ure_reset), chip_disable_teredo (ure_disable_teredo), the register access
 * helpers (ure_read_N/ure_write_N, ure_ocp_reg_read/write). Where FreeBSD and
 * OpenBSD sys/dev/usb/if_ure.c (also Kevin Lo's, same licence) differ on the
 * RTL8153B, the choice is commented at the spot; most are FreeBSD accessing a
 * PLA register with the USB MCU type.
 *
 * Copyright (c) 2015-2016 Kevin Lo <kevlo@FreeBSD.org>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 *
 * RTL8153B cross-reference, OpenBSD sys/dev/usb/if_ure.c / if_urereg.h:
 *
 * Copyright (c) 2015, 2016, 2019 Kevin Lo <kevlo@openbsd.org>
 * Copyright (c) 2020 Jonathon Fletcher <jonathon.fletcher@gmail.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
 * ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
 * ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
 * OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
 * OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
 * SUCH DAMAGE.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_log.h"
#include "usb_eth_driver.h"
#include "rtl815x_proto.h"
#include "rtl815x_regs.h"

static const char *TAG = "rtl815x";

#define POLL_DELAY_MS           10

typedef struct {
    uint16_t chip_ver;                      /* PLA_TCR1 version bits */
    const rtl815x_chip_t *chip;
    size_t rx_size;                         /* bulk-IN transfer size (sizing) */
    rtl815x_rx_parser_t parser;
    uint8_t carry[RTL815X_RX_MAX_FRAME];    /* frames split across bulk transfers */
} rtl815x_priv_t;

static inline rtl815x_priv_t *priv(usb_eth_dev_t *d)
{
    return usb_eth_priv(d);
}

/* ===========================================================================
 * Vendor control transfers and register access (control ops only)
 * ======================================================================== */

static esp_err_t vendor_xfer(usb_eth_dev_t *d, bool in, uint16_t value, uint16_t index, void *data, uint16_t len)
{
    uint8_t type = (in ? USB_BM_REQUEST_TYPE_DIR_IN : USB_BM_REQUEST_TYPE_DIR_OUT) |
                   USB_BM_REQUEST_TYPE_TYPE_VENDOR | USB_BM_REQUEST_TYPE_RECIP_DEVICE;
    return usb_eth_ctrl(d, type, RTL815X_REQ, value, index, data, len);
}

static esp_err_t reg_read(usb_eth_dev_t *d, uint16_t reg, uint16_t type, unsigned width, uint32_t *val)
{
    uint8_t raw[4];
    esp_err_t ret = vendor_xfer(d, true, rtl815x_reg_read_addr(reg), type, raw, sizeof(raw));
    if (ret == ESP_OK) {
        *val = rtl815x_reg_decode_read(reg, width, raw);
    }
    return ret;
}

static esp_err_t reg_write(usb_eth_dev_t *d, uint16_t reg, uint16_t type, unsigned width, uint32_t val)
{
    rtl815x_reg_write_t w = rtl815x_reg_encode_write(reg, width, val);
    uint8_t raw[4];
    usb_eth_put_le32(raw, w.data);
    return vendor_xfer(d, false, w.addr, (uint16_t)(type | w.byte_en), raw, sizeof(raw));
}

static esp_err_t reg_modify(usb_eth_dev_t *d, uint16_t reg, uint16_t type, unsigned width,
                            uint32_t clear, uint32_t set)
{
    uint32_t v;
    ESP_RETURN_ON_ERROR(reg_read(d, reg, type, width, &v), TAG, "read 0x%04x failed", reg);
    return reg_write(d, reg, type, width, (v & ~clear) | set);
}

static esp_err_t ocp_read(usb_eth_dev_t *d, uint16_t addr, uint16_t *val)
{
    uint32_t v;
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_OCP_GPHY_BASE, RTL815X_MCU_TYPE_PLA, 2, rtl815x_ocp_base(addr)),
                        TAG, "OCP base write failed");
    ESP_RETURN_ON_ERROR(reg_read(d, rtl815x_ocp_window_reg(addr), RTL815X_MCU_TYPE_PLA, 2, &v),
                        TAG, "OCP read 0x%04x failed", addr);
    *val = (uint16_t)v;
    return ESP_OK;
}

static esp_err_t ocp_write(usb_eth_dev_t *d, uint16_t addr, uint16_t val)
{
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_OCP_GPHY_BASE, RTL815X_MCU_TYPE_PLA, 2, rtl815x_ocp_base(addr)),
                        TAG, "OCP base write failed");
    return reg_write(d, rtl815x_ocp_window_reg(addr), RTL815X_MCU_TYPE_PLA, 2, val);
}

/* Poll until (reg & mask) is non-zero (want_set) or zero (!want_set). */
static esp_err_t reg_wait(usb_eth_dev_t *d, uint16_t reg, uint16_t type, unsigned width,
                          uint32_t mask, bool want_set, const char *what)
{
    for (int i = 0; i < RTL815X_TIMEOUT_POLLS; i++) {
        uint32_t v;
        ESP_RETURN_ON_ERROR(reg_read(d, reg, type, width, &v), TAG, "poll %s failed", what);
        if (((v & mask) != 0) == want_set) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
    }
    ESP_LOGE(TAG, "timeout waiting for %s", what);
    return ESP_ERR_TIMEOUT;
}

/* ===========================================================================
 * Chip initialisation (after FreeBSD if_ure.c, RTL8152 path)
 * ======================================================================== */

static esp_err_t chip_disable_aldps(usb_eth_dev_t *d)
{
    ESP_RETURN_ON_ERROR(ocp_write(d, RTL815X_OCP_ALDPS_CONFIG,
                                  RTL815X_ENPDNPS | RTL815X_LINKENA | RTL815X_DIS_SDSAVE), TAG, "ALDPS off failed");
    for (int i = 0; i < 20; i++) {
        uint16_t v;
        vTaskDelay(1);
        ESP_RETURN_ON_ERROR(ocp_read(d, RTL815X_OCP_ALDPS_STATUS, &v), TAG, "ALDPS status failed");
        if (v & 0x0100) {
            break;
        }
    }
    return ESP_OK;
}

static esp_err_t chip_reset(usb_eth_dev_t *d)
{
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CR, RTL815X_MCU_TYPE_PLA, 1, RTL815X_CR_RST), TAG, "CR reset failed");
    return reg_wait(d, RTL815X_PLA_CR, RTL815X_MCU_TYPE_PLA, 1, RTL815X_CR_RST, false, "MAC reset");
}

static esp_err_t chip_disable_teredo(usb_eth_dev_t *d)
{
    if (priv(d)->chip->family == RTL815X_FAMILY_8153B) {
        ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_TEREDO_CFG, RTL815X_MCU_TYPE_PLA, 1, 0xff), TAG,
                            "teredo cfg failed");
    } else {
        ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_TEREDO_CFG, RTL815X_MCU_TYPE_PLA, 2,
                                       RTL815X_TEREDO_SEL | RTL815X_TEREDO_RS_EVENT_MASK | RTL815X_OOB_TEREDO_EN, 0),
                            TAG, "teredo cfg failed");
    }
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_WDT6_CTRL, RTL815X_MCU_TYPE_PLA, 2, RTL815X_WDT6_SET_MODE),
                        TAG, "WDT6 failed");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_REALWOW_TIMER, RTL815X_MCU_TYPE_PLA, 2, 0), TAG, "realwow failed");
    return reg_write(d, RTL815X_PLA_TEREDO_TIMER, RTL815X_MCU_TYPE_PLA, 4, 0);
}

static esp_err_t chip_nic_reset(usb_eth_dev_t *d)
{
    const uint16_t pla = RTL815X_MCU_TYPE_PLA;

    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MISC_1, pla, 2, 0, RTL815X_RXDY_GATED_EN), TAG, "MISC_1");
    ESP_RETURN_ON_ERROR(chip_disable_teredo(d), TAG, "teredo");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_RCR, pla, 4, RTL815X_RCR_ACPT_ALL, 0), TAG, "RCR");
    ESP_RETURN_ON_ERROR(chip_reset(d), TAG, "reset");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CR, pla, 1, 0), TAG, "CR");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_NOW_IS_OOB, 0), TAG, "OOB_CTRL");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_SFF_STS_7, pla, 2, RTL815X_MCU_BORW_EN, 0), TAG, "SFF_STS_7");
    ESP_RETURN_ON_ERROR(reg_wait(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_LINK_LIST_READY, true, "link list"),
                        TAG, "link list");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_SFF_STS_7, pla, 2, 0, RTL815X_RE_INIT_LL), TAG, "RE_INIT_LL");
    ESP_RETURN_ON_ERROR(reg_wait(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_LINK_LIST_READY, true, "link list re-init"),
                        TAG, "link list re-init");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_CPCR, pla, 2, RTL815X_CPCR_RX_VLAN, 0), TAG, "CPCR");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_TCR0, pla, 2, 0, RTL815X_TCR0_AUTO_FIFO), TAG, "TCR0");

    uint32_t fifo1, fifo2;
    rtl815x_rx_fifo_thresholds(usb_eth_speed(d) != USB_SPEED_HIGH, &fifo1, &fifo2);
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL0, pla, 4, RTL815X_RXFIFO_THR1_NORMAL), TAG, "RXFIFO0");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL1, pla, 4, fifo1), TAG, "RXFIFO1");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL2, pla, 4, fifo2), TAG, "RXFIFO2");
    return reg_write(d, RTL815X_PLA_TXFIFO_CTRL, pla, 4, RTL815X_TXFIFO_THR_NORMAL);
}

static esp_err_t chip_init(usb_eth_dev_t *d)
{
    const uint16_t pla = RTL815X_MCU_TYPE_PLA;
    const uint16_t usb = RTL815X_MCU_TYPE_USB;

    ESP_RETURN_ON_ERROR(chip_disable_aldps(d), TAG, "ALDPS");
    if (priv(d)->chip_ver == RTL815X_VER_4C00) {
        ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_LED_FEATURE, pla, 2, RTL815X_LED_MODE_MASK, 0), TAG, "LED");
    }
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_UPS_CTRL, usb, 2, RTL815X_POWER_CUT, 0), TAG, "UPS_CTRL");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_PM_CTRL_STATUS, usb, 2, RTL815X_RESUME_INDICATE, 0), TAG, "PM_CTRL");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_PHY_PWR, pla, 2, 0,
                                   RTL815X_TX_10M_IDLE_EN | RTL815X_PFM_PWM_SWITCH), TAG, "PHY_PWR");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MAC_PWR_CTRL, pla, 4, RTL815X_MCU_CLK_RATIO_MASK,
                                   RTL815X_MCU_CLK_RATIO | RTL815X_D3_CLK_GATED_EN), TAG, "MAC_PWR_CTRL");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_GPHY_INTR_IMR, pla, 2,
                                  RTL815X_GPHY_STS_MSK | RTL815X_SPEED_DOWN_MSK |
                                  RTL815X_SPDWN_RXDV_MSK | RTL815X_SPDWN_LINKCHG_MSK), TAG, "GPHY_INTR_IMR");
    /* RX aggregation on: several frames per bulk-IN transfer. */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_USB_CTRL, usb, 2,
                                   RTL815X_RX_AGG_DISABLE | RTL815X_RX_ZERO_EN, 0), TAG, "USB_CTRL");
    ESP_RETURN_ON_ERROR(chip_disable_aldps(d), TAG, "ALDPS");
    ESP_RETURN_ON_ERROR(chip_nic_reset(d), TAG, "NIC reset");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_TX_AGG, usb, 1, RTL815X_TX_AGG_MAX_THRESHOLD), TAG, "TX_AGG");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_RX_BUF_TH, usb, 4, RTL815X_RX_THR_HIGH), TAG, "RX_BUF_TH");
    return reg_write(d, RTL815X_USB_TX_DMA, usb, 4, RTL815X_TEST_MODE_DISABLE | RTL815X_TX_SIZE_ADJUST1);
}

/* ===========================================================================
 * RTL8153B (after FreeBSD ure_rtl8153b_init / ure_rtl8153b_nic_reset / ure_init)
 * ======================================================================== */

static esp_err_t ocp_modify(usb_eth_dev_t *d, uint16_t addr, uint16_t clear, uint16_t set)
{
    uint16_t v;
    ESP_RETURN_ON_ERROR(ocp_read(d, addr, &v), TAG, "OCP read 0x%04x failed", addr);
    return ocp_write(d, addr, (uint16_t)((v & ~clear) | set));
}

/* ALDPS on the RTL8153 family is OCP_POWER_CFG.EN_ALDPS (FreeBSD's
 * ure_enable_aldps(false) writes the RTL8152 ALDPS register here; OpenBSD
 * clears EN_ALDPS and then polls PLA 0xe000 bit 8, as done here). */
static esp_err_t r8153b_aldps(usb_eth_dev_t *d, bool enable)
{
    ESP_RETURN_ON_ERROR(ocp_modify(d, RTL815X_OCP_POWER_CFG, enable ? 0 : RTL815X_EN_ALDPS,
                                   enable ? RTL815X_EN_ALDPS : 0), TAG, "ALDPS");
    if (!enable) {
        for (int i = 0; i < 20; i++) {
            uint32_t v;
            vTaskDelay(1);
            ESP_RETURN_ON_ERROR(reg_read(d, RTL815X_OCP_ALDPS_STATUS, RTL815X_MCU_TYPE_PLA, 2, &v), TAG,
                                "ALDPS status");
            if (v & 0x0100) {
                break;
            }
        }
    }
    return ESP_OK;
}

/* Wait for the PHY state machine (ure_phy_status): @p desired, or with 0 any
 * settled state. Like both BSDs, a timeout is only logged. */
static esp_err_t r8153b_phy_status(usb_eth_dev_t *d, uint16_t desired)
{
    uint16_t v = 0;
    for (int i = 0; i < RTL815X_TIMEOUT_POLLS; i++) {
        ESP_RETURN_ON_ERROR(ocp_read(d, RTL815X_OCP_PHY_STATUS, &v), TAG, "PHY status");
        v &= RTL815X_PHY_STAT_MASK;
        if (desired ? v == desired : (v == RTL815X_PHY_STAT_LAN_ON || v == RTL815X_PHY_STAT_PWRDN ||
                                      v == RTL815X_PHY_STAT_EXT_INIT)) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_DELAY_MS));
    }
    ESP_LOGW(TAG, "timeout waiting for the PHY (status %u, wanted %u)", v, desired);
    return ESP_OK;
}

/* Bind-time init (ure_rtl8153b_init). */
static esp_err_t r8153b_init(usb_eth_dev_t *d)
{
    const uint16_t pla = RTL815X_MCU_TYPE_PLA;
    const uint16_t usb = RTL815X_MCU_TYPE_USB;

    ESP_RETURN_ON_ERROR(r8153b_aldps(d, false), TAG, "ALDPS");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_LPM_CONFIG, usb, 2, RTL815X_LPM_U1U2_EN, 0), TAG, "LPM");
    ESP_RETURN_ON_ERROR(reg_wait(d, RTL815X_PLA_BOOT_CTRL, pla, 2, RTL815X_AUTOLOAD_DONE, true, "autoload"),
                        TAG, "autoload");
    ESP_RETURN_ON_ERROR(r8153b_phy_status(d, 0), TAG, "PHY status");
    uint16_t bmcr;
    ESP_RETURN_ON_ERROR(ocp_read(d, rtl815x_ocp_mii_addr(RTL815X_MII_BMCR), &bmcr), TAG, "BMCR");
    if (bmcr & RTL815X_BMCR_PDOWN) {
        ESP_RETURN_ON_ERROR(ocp_write(d, rtl815x_ocp_mii_addr(RTL815X_MII_BMCR), bmcr & ~RTL815X_BMCR_PDOWN), TAG,
                            "BMCR");
    }
    ESP_RETURN_ON_ERROR(r8153b_phy_status(d, RTL815X_PHY_STAT_LAN_ON), TAG, "PHY status");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_U2P3_CTRL, usb, 2, RTL815X_U2P3_ENABLE, 0), TAG, "U2P3");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_MSC_TIMER, usb, 2, RTL815X_MSC_TIMER_32760MS), TAG, "MSC timer");
    /* FreeBSD also sets the U1/U2 idle timer (USB 3 link states); OpenBSD
     * leaves it alone on the 8153B, and the ESP only runs USB 2.0. */
    /* Power cut and UPS off */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_POWER_CUT, usb, 2, RTL815X_PWR_EN, 0), TAG, "power cut");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_MISC_0, usb, 2, RTL815X_PCUT_STATUS, 0), TAG, "MISC_0");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_POWER_CUT, usb, 1, RTL815X_UPS_EN | RTL815X_USP_PREWAKE, 0), TAG,
                        "UPS");
    /* OpenBSD's names and bits; FreeBSD clears only bit 0 of 0xcfff */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_MISC_2, usb, 1, RTL815X_UPS_FORCE_PWR_DOWN | RTL815X_UPS_NO_UPS, 0),
                        TAG, "MISC_2");
    /* No queue wake / runtime suspend. PLA registers: FreeBSD uses the USB
     * MCU type for these, OpenBSD PLA. */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_INDICATE_FLAG, pla, 1, RTL815X_UPCOMING_RUNTIME_D3, 0), TAG,
                        "indicate flag");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_SUSPEND_FLAG, pla, 1, RTL815X_LINK_CHG_EVENT, 0), TAG,
                        "suspend flag");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_EXTRA_STATUS, pla, 2, RTL815X_LINK_CHANGE_FLAG, 0), TAG,
                        "extra status");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CRWECR, pla, 1, RTL815X_CRWECR_CONFIG), TAG, "CRWECR config");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_CONFIG34, pla, 2, RTL815X_LINK_OFF_WAKE_EN, 0), TAG, "CONFIG34");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CRWECR, pla, 1, RTL815X_CRWECR_NORMAL), TAG, "CRWECR normal");
    /* Seed the link-change poll with the current link state */
    uint32_t phystatus;
    ESP_RETURN_ON_ERROR(reg_read(d, RTL815X_PLA_PHYSTATUS, pla, 2, &phystatus), TAG, "PHYSTATUS");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_EXTRA_STATUS, pla, 2, RTL815X_CUR_LINK_OK,
                                   ((phystatus & RTL815X_PHYSTATUS_LINK) ? RTL815X_CUR_LINK_OK : 0) |
                                   RTL815X_POLL_LINK_CHG), TAG, "extra status");
    /* MAC clock speed-down on, MCU speed-down off (PLA_MAC_PWR_CTRL2: FreeBSD
     * again with the USB MCU type) */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MAC_PWR_CTRL2, pla, 2, 0, RTL815X_MAC_CLK_SPDWN_EN), TAG,
                        "MAC_PWR_CTRL2");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MAC_PWR_CTRL3, pla, 2, RTL815X_PLA_MCU_SPDWN_EN, 0), TAG,
                        "MAC_PWR_CTRL3");
    /* RX aggregation on */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_USB_CTRL, usb, 2, RTL815X_RX_AGG_DISABLE | RTL815X_RX_ZERO_EN, 0),
                        TAG, "USB_CTRL");
    return reg_modify(d, RTL815X_PLA_RSTTALLY, pla, 2, 0, RTL815X_TALLY_RESET);
}

/* First step of rtl815x_up (ure_rtl8153b_nic_reset). */
static esp_err_t r8153b_nic_reset(usb_eth_dev_t *d)
{
    const uint16_t pla = RTL815X_MCU_TYPE_PLA;
    const uint16_t usb = RTL815X_MCU_TYPE_USB;

    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_LPM_CONFIG, usb, 2, RTL815X_LPM_U1U2_EN, 0), TAG, "LPM");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_U2P3_CTRL, usb, 2, RTL815X_U2P3_ENABLE, 0), TAG, "U2P3");
    ESP_RETURN_ON_ERROR(r8153b_aldps(d, false), TAG, "ALDPS");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MISC_1, pla, 2, 0, RTL815X_RXDY_GATED_EN), TAG, "MISC_1");
    ESP_RETURN_ON_ERROR(chip_disable_teredo(d), TAG, "teredo");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_RCR, pla, 4, RTL815X_RCR_ACPT_ALL, 0), TAG, "RCR");
    ESP_RETURN_ON_ERROR(chip_reset(d), TAG, "reset");
    /* Reset the buffer management unit of both endpoints */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_BMU_RESET, usb, 1, RTL815X_BMU_RESET_EP_IN | RTL815X_BMU_RESET_EP_OUT,
                                   0), TAG, "BMU");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_USB_BMU_RESET, usb, 1, 0,
                                   RTL815X_BMU_RESET_EP_IN | RTL815X_BMU_RESET_EP_OUT), TAG, "BMU");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_NOW_IS_OOB, 0), TAG, "OOB_CTRL");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_SFF_STS_7, pla, 2, RTL815X_MCU_BORW_EN, 0), TAG, "SFF_STS_7");
    ESP_RETURN_ON_ERROR(reg_wait(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_LINK_LIST_READY, true, "link list"),
                        TAG, "link list");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_SFF_STS_7, pla, 2, 0, RTL815X_RE_INIT_LL), TAG, "RE_INIT_LL");
    ESP_RETURN_ON_ERROR(reg_wait(d, RTL815X_PLA_OOB_CTRL, pla, 1, RTL815X_LINK_LIST_READY, true, "link list re-init"),
                        TAG, "link list re-init");
    /* No VLAN tag stripping: CPCR.RX_VLAN as OpenBSD's ure_rxvlan does for the
     * 8153B (FreeBSD clears 0xc012 bits 7:6, the 8156 RCR1 layout; rtl815x_up
     * clears CPCR.RX_VLAN for every chip anyway). */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_CPCR, pla, 2, RTL815X_CPCR_RX_VLAN, 0), TAG, "CPCR");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RMS, pla, 2, RTL815X_RX_MAX_FRAME), TAG, "RMS");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_MTPS, pla, 1, RTL815X_MTPS_JUMBO), TAG, "MTPS");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_TCR0, pla, 2, 0, RTL815X_TCR0_AUTO_FIFO), TAG, "TCR0");
    ESP_RETURN_ON_ERROR(chip_reset(d), TAG, "reset");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL0, pla, 4, RTL815X_RXFIFO_THR1_NORMAL), TAG, "RXFIFO0");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL1, pla, 2, RTL815X_RXFIFO_THR2_NORMAL), TAG, "RXFIFO1");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_RXFIFO_CTRL2, pla, 2, RTL815X_RXFIFO_THR3_NORMAL), TAG, "RXFIFO2");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_RX_BUF_TH, usb, 4, RTL815X_RX_THR_B), TAG, "RX_BUF_TH");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_TXFIFO_CTRL, pla, 4, RTL815X_TXFIFO_THR_NORMAL2), TAG, "TXFIFO");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MAC_PWR_CTRL3, pla, 2, RTL815X_PLA_MCU_SPDWN_EN, 0), TAG,
                        "MAC_PWR_CTRL3");
    return r8153b_aldps(d, true);
}

/* RX aggregation, after the station address is written (8153B part of ure_init).
 * The early size follows this instance's transfer size, not FreeBSD's fixed
 * 32 KiB, so an aggregate is closed before it overflows one transfer. */
static esp_err_t r8153b_rx_agg(usb_eth_dev_t *d)
{
    const uint16_t usb = RTL815X_MCU_TYPE_USB;
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_RX_EARLY_AGG, usb, 2, RTL815X_RX_EARLY_AGG_8153B), TAG, "early agg");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_RX_EXTRA_AGG_TMR, usb, 2, RTL815X_RX_EXTRA_AGG_8153B), TAG,
                        "extra agg timer");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_USB_RX_EARLY_SIZE, usb, 2, rtl815x_rx_early_size(priv(d)->rx_size)),
                        TAG, "early size");
    return reg_write(d, RTL815X_USB_UPT_RXDMA_OWN, usb, 1, RTL815X_OWN_UPDATE | RTL815X_OWN_CLEAR);
}

/* ===========================================================================
 * Chip families
 * ======================================================================== */

typedef struct {
    esp_err_t (*init)(usb_eth_dev_t *d);    /* bind */
    esp_err_t (*reset)(usb_eth_dev_t *d);   /* first step of rtl815x_up */
    esp_err_t (*rx_agg)(usb_eth_dev_t *d);  /* after the address in rtl815x_up, NULL = none */
    size_t rx_size_hs;                      /* default bulk-IN transfer size at high speed */
} family_ops_t;

static const family_ops_t s_family_8152 = {
    .init = chip_init,          /* includes ure_rtl8152_nic_reset */
    .reset = chip_reset,
    .rx_agg = NULL,
    .rx_size_hs = CONFIG_USB_ETH_RTL815X_RX_SIZE_8152_HS,
};

static const family_ops_t s_family_8153b = {
    .init = r8153b_init,
    .reset = r8153b_nic_reset,
    .rx_agg = r8153b_rx_agg,
    .rx_size_hs = CONFIG_USB_ETH_RTL815X_RX_SIZE_8153B_HS,
};

static const family_ops_t *family(usb_eth_dev_t *d)
{
    return priv(d)->chip->family == RTL815X_FAMILY_8153B ? &s_family_8153b : &s_family_8152;
}

/* The dongle's own address: PLA_BACKUP (factory copy) on chips after 0x4c00,
 * else PLA_IDR, see rtl815x_pick_mac(). ESP_ERR_NOT_FOUND if neither is valid. */
static esp_err_t rtl815x_read_mac(usb_eth_dev_t *d, uint8_t mac[USB_ETH_ADDR_LEN])
{
    uint8_t idr[8], backup[8];
    bool have_backup = false;
    ESP_RETURN_ON_ERROR(vendor_xfer(d, true, RTL815X_PLA_IDR, RTL815X_MCU_TYPE_PLA, idr, sizeof(idr)),
                        TAG, "read IDR failed");
    if (priv(d)->chip_ver != RTL815X_VER_4C00) {
        have_backup = vendor_xfer(d, true, RTL815X_PLA_BACKUP, RTL815X_MCU_TYPE_PLA, backup, sizeof(backup)) == ESP_OK;
        if (!have_backup) {
            ESP_LOGW(TAG, "read BACKUP failed, using IDR");
        }
    }
    rtl815x_mac_src_t src = rtl815x_pick_mac(priv(d)->chip_ver, idr, have_backup ? backup : NULL, mac);
    if (src == RTL815X_MAC_SRC_NONE) {
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGD(TAG, "adapter MAC from %s", src == RTL815X_MAC_SRC_BACKUP ? "PLA_BACKUP" : "PLA_IDR");
    return ESP_OK;
}

static esp_err_t rtl815x_set_mac(usb_eth_dev_t *d, const uint8_t mac[USB_ETH_ADDR_LEN])
{
    uint8_t buf[8] = {0};
    memcpy(buf, mac, USB_ETH_ADDR_LEN);
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CRWECR, RTL815X_MCU_TYPE_PLA, 1, RTL815X_CRWECR_CONFIG),
                        TAG, "CRWECR config");
    esp_err_t ret = vendor_xfer(d, false, RTL815X_PLA_IDR, RTL815X_MCU_TYPE_PLA | RTL815X_BYTE_EN_SIX_BYTES,
                              buf, sizeof(buf));
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_CRWECR, RTL815X_MCU_TYPE_PLA, 1, RTL815X_CRWECR_NORMAL),
                        TAG, "CRWECR normal");
    return ret;
}

static esp_err_t rtl815x_set_filter(usb_eth_dev_t *d, const usb_eth_filter_t *f)
{
    uint32_t hashes[2] = {0, 0};
    if (f->promisc || f->all_multi) {
        hashes[0] = hashes[1] = 0xffffffffu;
    } else {
        for (int i = 0; i < f->n_mcast; i++) {
            unsigned bit = rtl815x_mcast_hash_bit(f->mcast[i]);
            hashes[bit >> 5] |= 1u << (bit & 31);
        }
    }
    uint32_t mar0, mar4, rcr;
    rtl815x_mcast_mar_values(hashes, &mar0, &mar4);
    ESP_RETURN_ON_ERROR(reg_read(d, RTL815X_PLA_RCR, RTL815X_MCU_TYPE_PLA, 4, &rcr), TAG, "RCR read");
    rcr = (rcr & ~RTL815X_RCR_ACPT_ALL) | rtl815x_rcr_bits(f->promisc, true);
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_MAR0, RTL815X_MCU_TYPE_PLA, 4, mar0), TAG, "MAR0");
    ESP_RETURN_ON_ERROR(reg_write(d, RTL815X_PLA_MAR4, RTL815X_MCU_TYPE_PLA, 4, mar4), TAG, "MAR4");
    return reg_write(d, RTL815X_PLA_RCR, RTL815X_MCU_TYPE_PLA, 4, rcr);
}

/* Bring the MAC up (FreeBSD ure_init): reset, program address and filter,
 * enable RX and TX. */
static esp_err_t rtl815x_up(usb_eth_dev_t *d, const uint8_t mac[USB_ETH_ADDR_LEN], const usb_eth_filter_t *f)
{
    const uint16_t pla = RTL815X_MCU_TYPE_PLA;

    ESP_RETURN_ON_ERROR(family(d)->reset(d), TAG, "reset");
    ESP_RETURN_ON_ERROR(rtl815x_set_mac(d, mac), TAG, "MAC address");
    if (family(d)->rx_agg) {
        ESP_RETURN_ON_ERROR(family(d)->rx_agg(d), TAG, "RX aggregation");
    }
    /* Reset the packet filter */
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_FMC, pla, 2, RTL815X_FMC_FCR_MCU_EN, 0), TAG, "FMC");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_FMC, pla, 2, 0, RTL815X_FMC_FCR_MCU_EN), TAG, "FMC");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_CPCR, pla, 2, RTL815X_CPCR_RX_VLAN, 0), TAG, "CPCR");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_CR, pla, 1, 0, RTL815X_CR_RE | RTL815X_CR_TE), TAG, "CR");
    ESP_RETURN_ON_ERROR(reg_modify(d, RTL815X_PLA_MISC_1, pla, 2, RTL815X_RXDY_GATED_EN, 0), TAG, "MISC_1");
    return rtl815x_set_filter(d, f);
}

/* ===========================================================================
 * usb_eth driver ops
 * ======================================================================== */

static void rx_frame_cb(void *ctx, const uint8_t *frame, size_t len, const uint8_t *desc)
{
    (void)desc;
    usb_eth_rx_deliver(ctx, frame, len);
}

static esp_err_t rtl815x_priv_init(usb_eth_dev_t *d, void *p)
{
    rtl815x_priv_t *pv = p;
    return rtl815x_rx_parser_init(&pv->parser, pv->carry, sizeof(pv->carry), rx_frame_cb, d) ? ESP_OK
           : ESP_ERR_INVALID_ARG;
}

static void rtl815x_sizing(usb_eth_dev_t *d, const usb_eth_eps_t *eps, usb_speed_t speed, size_t rx_request,
                           usb_eth_sizing_t *out)
{
    if (rx_request == 0) {
        rx_request = speed == USB_SPEED_HIGH ? family(d)->rx_size_hs : CONFIG_USB_ETH_RTL815X_RX_SIZE_FS;
    }
    out->rx_size = rtl815x_rx_xfer_size(rx_request, eps->mps_in);
    out->tx_size = rtl815x_tx_xfer_size(eps->mps_out);
    priv(d)->rx_size = out->rx_size;
}

static esp_err_t rtl815x_bind(usb_eth_dev_t *d, const usb_eth_id_t *id)
{
    (void)id;
    uint32_t ver;
    ESP_RETURN_ON_ERROR(reg_read(d, RTL815X_PLA_TCR1, RTL815X_MCU_TYPE_PLA, 2, &ver), TAG, "version read");
    priv(d)->chip_ver = (uint16_t)(ver & RTL815X_VERSION_MASK);
    priv(d)->chip = rtl815x_chip_lookup(priv(d)->chip_ver);
    if (priv(d)->chip == NULL) {
        ESP_LOGE(TAG, "unsupported chip version 0x%04x (supported: RTL8152 0x4c00/0x4c10, RTL8153B 0x6000/0x6010)",
                 priv(d)->chip_ver);
        return ESP_ERR_NOT_SUPPORTED;
    }
    usb_eth_set_chip(d, priv(d)->chip->name, priv(d)->chip_ver);
    /* The RTL8153B PHY advertises 1000BASE-T full duplex by itself (GBCR
     * 0x0200, also after the BMCR reset of the esp_eth 802.3 PHY driver, which
     * only writes BMCR/ANAR). */
    return family(d)->init(d);
}

static esp_err_t rtl815x_mdio_read(usb_eth_dev_t *d, uint32_t reg, uint16_t *val)
{
    return ocp_read(d, rtl815x_ocp_mii_addr(reg), val);
}

static esp_err_t rtl815x_mdio_write(usb_eth_dev_t *d, uint32_t reg, uint16_t val)
{
    return ocp_write(d, rtl815x_ocp_mii_addr(reg), val);
}

static void rtl815x_rx_reset(usb_eth_dev_t *d, bool fresh)
{
    /* The chip keeps its link (and keeps receiving) while the host side is
     * re-attached, e.g. across a driver reinstall, and its first bulk-IN data can
     * then be the tail of an aggregate cut at the previous teardown. Seen at full
     * speed (ESP32-S3) with the RTL8153B in about half and the RTL8152 in 2 of 5
     * reinstalls: an 80-byte transfer starting inside an IPv6 header. So a fresh
     * attach is treated like a restart: the first transfer is used only if it is
     * provably a whole burst, else discarded to the burst boundary. */
    (void)fresh;
    rtl815x_rx_parser_restart(&priv(d)->parser);
}

static void rtl815x_rx_feed(usb_eth_dev_t *d, const uint8_t *data, size_t len, bool end_of_burst)
{
    rtl815x_rx_parser_t *p = &priv(d)->parser;
    const rtl815x_rx_stats_t before = p->stats;
    rtl815x_rx_parser_feed(p, data, len, end_of_burst);
    uint32_t runts = p->stats.runts - before.runts;
    uint32_t desync = p->stats.desync - before.desync;
    uint32_t truncated = p->stats.truncated - before.truncated;
    if (runts | desync | truncated) {
        /* Diagnostics only: which check dropped what, and how the transfer started. */
        ESP_LOGD(TAG, "RX dropped %u runt, %u desync, %u truncated in a %u-byte transfer%s (frames so far %u)",
                 (unsigned)runts, (unsigned)desync, (unsigned)truncated, (unsigned)len,
                 end_of_burst ? " (end of burst)" : "", (unsigned)p->stats.frames);
        ESP_LOG_BUFFER_HEX_LEVEL(TAG, data, len < 48 ? len : 48, ESP_LOG_DEBUG);
    }
}

static void rtl815x_rx_discard(usb_eth_dev_t *d, bool end_of_burst)
{
    rtl815x_rx_parser_discard_burst(&priv(d)->parser, end_of_burst);
}

static size_t drv_tx_pack(usb_eth_dev_t *d, uint8_t *dst, size_t cap, const usb_eth_iov_t *iov, size_t n)
{
    (void)d;
    return rtl815x_tx_pack(dst, cap, iov, n);
}

static void rtl815x_get_stats(usb_eth_dev_t *d, usb_eth_drv_stats_t *s)
{
    const rtl815x_rx_stats_t *st = &priv(d)->parser.stats;
    s->rx_dropped = st->runts + st->desync + st->truncated;
    s->rx_reassembled = st->reassembled;
}

/* Vendor-specific interface (class 0xff) of these IDs; the CDC-ECM
 * configuration some of them also offer is left to a class driver. The chip
 * itself is identified by its PLA_TCR1 version in bind. */
static const usb_eth_id_t rtl815x_ids[] = {
    { .match = USB_ETH_MATCH_VIDPID, .vid = 0x0bda, .pid = 0x8152 },
    { .match = USB_ETH_MATCH_VIDPID, .vid = 0x0bda, .pid = 0x8153 },
};

const usb_eth_driver_t usb_eth_drv_rtl815x = {
    .name = "rtl815x",
    .ids = rtl815x_ids,
    .n_ids = sizeof(rtl815x_ids) / sizeof(rtl815x_ids[0]),
    .priv_size = sizeof(rtl815x_priv_t),
    .priv_init = rtl815x_priv_init,
    .sizing = rtl815x_sizing,
    .bind = rtl815x_bind,
    .read_mac = rtl815x_read_mac,
    .up = rtl815x_up,
    .set_mac = rtl815x_set_mac,
    .set_filter = rtl815x_set_filter,
    .mdio_read = rtl815x_mdio_read,
    .mdio_write = rtl815x_mdio_write,
    .rx_reset = rtl815x_rx_reset,
    .rx_feed = rtl815x_rx_feed,
    .rx_discard = rtl815x_rx_discard,
    .tx_pack = drv_tx_pack,
    .get_stats = rtl815x_get_stats,
};
