/*
 * RTL815x register map.
 *
 * Derived from FreeBSD sys/dev/usb/net/if_urereg.h (ure(4)), trimmed to the
 * registers the RTL8152 and RTL8153B paths use and renamed to the RTL815X_
 * prefix. The RTL8153B values were cross-checked against OpenBSD
 * sys/dev/usb/if_urereg.h (same author and licence, below); USB_MISC_2 and
 * its bits are only named there.
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
#pragma once

/* Vendor control request: bRequest is always 0x05, wValue = register
 * address, wIndex = MCU type | byte enables, data = 4 bytes little-endian. */
#define RTL815X_REQ                 0x05

#define RTL815X_MCU_TYPE_PLA        0x0100
#define RTL815X_MCU_TYPE_USB        0x0000

#define RTL815X_BYTE_EN_DWORD       0xff
#define RTL815X_BYTE_EN_WORD        0x33
#define RTL815X_BYTE_EN_BYTE        0x11
#define RTL815X_BYTE_EN_SIX_BYTES   0x3f

#define RTL815X_TIMEOUT_POLLS       1000    /* polls of ~10 ms each */

/* PLA registers */
#define RTL815X_PLA_IDR             0xc000
#define RTL815X_PLA_RCR             0xc010
#define RTL815X_PLA_RXFIFO_CTRL0    0xc0a0
#define RTL815X_PLA_RXFIFO_CTRL1    0xc0a4
#define RTL815X_PLA_RXFIFO_CTRL2    0xc0a8
#define RTL815X_PLA_FMC             0xc0b4
#define RTL815X_PLA_TEREDO_CFG      0xc0bc
#define RTL815X_PLA_MAR0            0xcd00
#define RTL815X_PLA_MAR4            0xcd04
#define RTL815X_PLA_BACKUP          0xd000
#define RTL815X_PLA_TEREDO_TIMER    0xd2cc
#define RTL815X_PLA_REALWOW_TIMER   0xd2e8
#define RTL815X_PLA_LED_FEATURE     0xdd92
#define RTL815X_PLA_GPHY_INTR_IMR   0xe022
#define RTL815X_PLA_MAC_PWR_CTRL    0xe0c0
#define RTL815X_PLA_WDT6_CTRL       0xe428
#define RTL815X_PLA_TCR0            0xe610
#define RTL815X_PLA_TCR1            0xe612
#define RTL815X_PLA_TXFIFO_CTRL     0xe618
#define RTL815X_PLA_CR              0xe813
#define RTL815X_PLA_CRWECR          0xe81c
#define RTL815X_PLA_PHY_PWR         0xe84c
#define RTL815X_PLA_OOB_CTRL        0xe84f
#define RTL815X_PLA_CPCR            0xe854
#define RTL815X_PLA_MISC_1          0xe85a
#define RTL815X_PLA_OCP_GPHY_BASE   0xe86c
#define RTL815X_PLA_SFF_STS_7       0xe8de
#define RTL815X_PLA_PHYSTATUS       0xe908

/* USB registers */
#define RTL815X_USB_USB_CTRL        0xd406
#define RTL815X_USB_TX_AGG          0xd40a
#define RTL815X_USB_RX_BUF_TH       0xd40c
#define RTL815X_USB_PM_CTRL_STATUS  0xd432
#define RTL815X_USB_TX_DMA          0xd434
#define RTL815X_USB_UPS_CTRL        0xd800

/* OCP (internal PHY) registers */
#define RTL815X_OCP_ALDPS_CONFIG    0x2010
#define RTL815X_OCP_BASE_MII        0xa400
#define RTL815X_OCP_POWER_CFG       0xa430
#define RTL815X_OCP_ALDPS_STATUS    0xe000  /* bit 0x0100: ALDPS off */

/* PLA_RCR */
#define RTL815X_RCR_AAP             0x00000001
#define RTL815X_RCR_APM             0x00000002
#define RTL815X_RCR_AM              0x00000004
#define RTL815X_RCR_AB              0x00000008
#define RTL815X_RCR_ACPT_ALL        (RTL815X_RCR_AAP | RTL815X_RCR_APM | RTL815X_RCR_AM | RTL815X_RCR_AB)

/* PLA_RXFIFO_CTRL0..2 */
#define RTL815X_RXFIFO_THR1_NORMAL  0x00080002
#define RTL815X_RXFIFO_THR2_FULL    0x00000060
#define RTL815X_RXFIFO_THR2_HIGH    0x00000038
#define RTL815X_RXFIFO_THR3_FULL    0x00000078
#define RTL815X_RXFIFO_THR3_HIGH    0x00000048

/* PLA_TXFIFO_CTRL */
#define RTL815X_TXFIFO_THR_NORMAL   0x00400008

/* PLA_FMC */
#define RTL815X_FMC_FCR_MCU_EN      0x0001

/* PLA_WDT6_CTRL */
#define RTL815X_WDT6_SET_MODE       0x0010

/* PLA_TCR0 */
#define RTL815X_TCR0_AUTO_FIFO      0x0080

/* PLA_TCR1 */
#define RTL815X_VERSION_MASK        0x7cf0
#define RTL815X_VER_4C00            0x4c00
#define RTL815X_VER_4C10            0x4c10

/* PLA_CR */
#define RTL815X_CR_RST              0x10
#define RTL815X_CR_RE               0x08
#define RTL815X_CR_TE               0x04

/* PLA_CRWECR */
#define RTL815X_CRWECR_NORMAL       0x00
#define RTL815X_CRWECR_CONFIG       0xc0

/* PLA_OOB_CTRL */
#define RTL815X_NOW_IS_OOB          0x80
#define RTL815X_LINK_LIST_READY     0x02

/* PLA_MISC_1 */
#define RTL815X_RXDY_GATED_EN       0x0008

/* PLA_SFF_STS_7 */
#define RTL815X_RE_INIT_LL          0x8000
#define RTL815X_MCU_BORW_EN         0x4000

/* PLA_CPCR */
#define RTL815X_CPCR_RX_VLAN        0x0040

/* PLA_TEREDO_CFG */
#define RTL815X_TEREDO_SEL          0x8000
#define RTL815X_TEREDO_RS_EVENT_MASK 0x00fe
#define RTL815X_OOB_TEREDO_EN       0x0001

/* PLA_LED_FEATURE */
#define RTL815X_LED_MODE_MASK       0x0700

/* PLA_PHY_PWR */
#define RTL815X_TX_10M_IDLE_EN      0x0080
#define RTL815X_PFM_PWM_SWITCH      0x0040

/* PLA_MAC_PWR_CTRL */
#define RTL815X_D3_CLK_GATED_EN     0x00004000
#define RTL815X_MCU_CLK_RATIO       0x07010f07
#define RTL815X_MCU_CLK_RATIO_MASK  0x0f0f0f0f

/* PLA_GPHY_INTR_IMR */
#define RTL815X_GPHY_STS_MSK        0x0001
#define RTL815X_SPEED_DOWN_MSK      0x0002
#define RTL815X_SPDWN_RXDV_MSK      0x0004
#define RTL815X_SPDWN_LINKCHG_MSK   0x0008

/* PLA_PHYSTATUS */
#define RTL815X_PHYSTATUS_FDX       0x0001
#define RTL815X_PHYSTATUS_LINK      0x0002
#define RTL815X_PHYSTATUS_10MBPS    0x0004
#define RTL815X_PHYSTATUS_100MBPS   0x0008

/* USB_USB_CTRL */
#define RTL815X_RX_AGG_DISABLE      0x0010
#define RTL815X_RX_ZERO_EN          0x0080

/* USB_TX_AGG */
#define RTL815X_TX_AGG_MAX_THRESHOLD 0x03

/* USB_RX_BUF_TH */
#define RTL815X_RX_THR_HIGH         0x7a120180

/* USB_TX_DMA */
#define RTL815X_TEST_MODE_DISABLE   0x00000001
#define RTL815X_TX_SIZE_ADJUST1     0x00000100

/* USB_UPS_CTRL */
#define RTL815X_POWER_CUT           0x0100

/* USB_PM_CTRL_STATUS */
#define RTL815X_RESUME_INDICATE     0x0001

/* OCP_ALDPS_CONFIG */
#define RTL815X_ENPDNPS             0x0200
#define RTL815X_LINKENA             0x0100
#define RTL815X_DIS_SDSAVE          0x0010

/* OCP_POWER_CFG */
#define RTL815X_EN_ALDPS            0x0004

/* ---------------------------------------------------------------------------
 * RTL8153B (PLA_TCR1 version 0x6000 / 0x6010)
 * ------------------------------------------------------------------------- */

#define RTL815X_VER_6000            0x6000
#define RTL815X_VER_6010            0x6010

/* PLA */
#define RTL815X_PLA_RMS             0xc016  /* RX max frame size */
#define RTL815X_PLA_SUSPEND_FLAG    0xd38a
#define RTL815X_PLA_INDICATE_FLAG   0xd38c  /* "INDICATE_FALG" in the BSD headers */
#define RTL815X_PLA_EXTRA_STATUS    0xd398
#define RTL815X_PLA_BOOT_CTRL       0xe004
#define RTL815X_PLA_MAC_PWR_CTRL2   0xe0ca
#define RTL815X_PLA_MAC_PWR_CTRL3   0xe0cc
#define RTL815X_PLA_MTPS            0xe615  /* TX max packet size */
#define RTL815X_PLA_RSTTALLY        0xe800
#define RTL815X_PLA_CONFIG34        0xe820

/* USB */
#define RTL815X_USB_U2P3_CTRL       0xb460
#define RTL815X_USB_MSC_TIMER       0xcbfc
#define RTL815X_USB_LPM_CONFIG      0xcfd8
#define RTL815X_USB_MISC_2          0xcfff
#define RTL815X_USB_RX_EARLY_AGG    0xd42c
#define RTL815X_USB_RX_EARLY_SIZE   0xd42e
#define RTL815X_USB_RX_EXTRA_AGG_TMR 0xd432
#define RTL815X_USB_UPT_RXDMA_OWN   0xd437
#define RTL815X_USB_BMU_RESET       0xd4b0
#define RTL815X_USB_POWER_CUT       0xd80a
#define RTL815X_USB_MISC_0          0xd81a

/* OCP (PHY side) */
#define RTL815X_OCP_PHY_STATUS      0xa420
#define RTL815X_OCP_POWER_CFG       0xa430

/* Values and bits */
#define RTL815X_RXFIFO_THR2_NORMAL  0x00a0
#define RTL815X_RXFIFO_THR3_NORMAL  0x0110
#define RTL815X_TXFIFO_THR_NORMAL2  0x01000008
#define RTL815X_RX_THR_B            0x00010001
#define RTL815X_MTPS_JUMBO          192
#define RTL815X_RX_EARLY_AGG_8153B  158     /* USB_RX_EARLY_AGG (aggregation timeout) */
#define RTL815X_RX_EXTRA_AGG_8153B  1875    /* USB_RX_EXTRA_AGG_TMR */
#define RTL815X_MSC_TIMER_32760MS   0x0fff
#define RTL815X_AUTOLOAD_DONE       0x0002  /* PLA_BOOT_CTRL */
#define RTL815X_PHY_STAT_MASK       0x0007  /* OCP_PHY_STATUS */
#define RTL815X_PHY_STAT_EXT_INIT   2
#define RTL815X_PHY_STAT_LAN_ON     3
#define RTL815X_PHY_STAT_PWRDN      5
#define RTL815X_EN_ALDPS            0x0004  /* OCP_POWER_CFG */
#define RTL815X_U2P3_ENABLE         0x0001  /* USB_U2P3_CTRL */
#define RTL815X_LPM_U1U2_EN         0x0001  /* USB_LPM_CONFIG */
#define RTL815X_PWR_EN              0x0001  /* USB_POWER_CUT */
#define RTL815X_UPS_EN              0x0010
#define RTL815X_USP_PREWAKE         0x0020
#define RTL815X_PCUT_STATUS         0x0001  /* USB_MISC_0 */
#define RTL815X_UPS_FORCE_PWR_DOWN  0x01    /* USB_MISC_2 */
#define RTL815X_UPS_NO_UPS          0x80
#define RTL815X_UPCOMING_RUNTIME_D3 0x01    /* PLA_INDICATE_FLAG */
#define RTL815X_LINK_CHG_EVENT      0x01    /* PLA_SUSPEND_FLAG */
#define RTL815X_POLL_LINK_CHG       0x0001  /* PLA_EXTRA_STATUS */
#define RTL815X_LINK_CHANGE_FLAG    0x0100
#define RTL815X_CUR_LINK_OK         0x8000
#define RTL815X_LINK_OFF_WAKE_EN    0x0008  /* PLA_CONFIG34 */
#define RTL815X_MAC_CLK_SPDWN_EN    0x8000  /* PLA_MAC_PWR_CTRL2 */
#define RTL815X_PLA_MCU_SPDWN_EN    0x4000  /* PLA_MAC_PWR_CTRL3 */
#define RTL815X_TALLY_RESET         0x0001  /* PLA_RSTTALLY */
#define RTL815X_BMU_RESET_EP_IN     0x01    /* USB_BMU_RESET */
#define RTL815X_BMU_RESET_EP_OUT    0x02
#define RTL815X_OWN_UPDATE          0x01    /* USB_UPT_RXDMA_OWN */
#define RTL815X_OWN_CLEAR           0x02
#define RTL815X_PHYSTATUS_1000MBPS  0x0010  /* PLA_PHYSTATUS */

/* Standard MII register used by the 8153B path (IEEE 802.3 clause 22) */
#define RTL815X_MII_BMCR            0
#define RTL815X_BMCR_PDOWN          0x0800
