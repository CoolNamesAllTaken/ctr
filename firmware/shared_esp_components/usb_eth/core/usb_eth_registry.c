/*
 * SPDX-License-Identifier: BSD-2-Clause
 *
 * Drivers compiled in (menuconfig, "USB Ethernet (usb_eth)" > Drivers), in
 * match priority order: vendor drivers before class drivers, so an adapter
 * that offers both a vendor and a CDC configuration binds to its vendor driver.
 */
#include "sdkconfig.h"
#include "usb_eth_driver.h"

#if CONFIG_USB_ETH_DRV_RTL815X
extern const usb_eth_driver_t usb_eth_drv_rtl815x;
#endif

const usb_eth_driver_t *const usb_eth_builtin_drivers[] = {
#if CONFIG_USB_ETH_DRV_RTL815X
    &usb_eth_drv_rtl815x,
#endif
};

const size_t usb_eth_builtin_driver_count = sizeof(usb_eth_builtin_drivers) / sizeof(usb_eth_builtin_drivers[0]);

_Static_assert(sizeof(usb_eth_builtin_drivers) > 0, "usb_eth: enable at least one driver in menuconfig");
