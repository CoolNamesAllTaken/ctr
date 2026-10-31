/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * PHY object for every usb_eth adapter. Its registers are the standard IEEE
 * 802.3 MII set, reached by esp_eth_mac_t::read_phy_reg / write_phy_reg: the
 * adapter's PHY through the driver's mdio ops, or the set the core synthesises
 * (no adapter, or a driver without an MDIO PHY). So the IEEE 802.3 base
 * driver does all the work. (esp_eth_phy_new_generic() is not used because ESP-IDF
 * only builds it on chips with an internal EMAC, which the ESP32-S3 lacks.)
 */
#include <stdlib.h>
#include "esp_check.h"
#include "esp_eth_phy_802_3.h"
#include "usb_eth.h"

static const char *TAG = "usb_eth_phy";

esp_eth_phy_t *usb_eth_phy_new(const eth_phy_config_t *config)
{
    ESP_RETURN_ON_FALSE(config, NULL, TAG, "invalid argument");
    eth_phy_config_t cfg = *config;
    cfg.phy_addr = 0;           /* the MAC object ignores the address; avoid the bus scan */
    cfg.reset_gpio_num = -1;    /* no reset line */

    /* Owned by the caller; freed by phy->del() (esp_eth_phy_802_3_del). */
    phy_802_3_t *phy = calloc(1, sizeof(phy_802_3_t));
    ESP_RETURN_ON_FALSE(phy, NULL, TAG, "no mem for PHY instance");
    if (esp_eth_phy_802_3_obj_config_init(phy, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "PHY 802.3 init failed");
        free(phy);
        return NULL;
    }
    return &phy->parent;
}
