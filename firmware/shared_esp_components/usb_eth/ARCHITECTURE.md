# usb_eth architecture

`usb_eth` is one ESP-IDF component for USB Ethernet adapters: a chip-independent
**core** plus **drivers** behind an ops table. It grew out of the validated RTL8152
driver; the design started from a proposal written for that migration (worker-2,
t-0108) and this file records what was built, including where it deviates from the
proposal and why. Today one driver exists, `rtl815x` (RTL8152, RTL8153B).

## Layout

```
usb_eth/
  CMakeLists.txt        core sources + each driver's sources if CONFIG_USB_ETH_DRV_<X>
  Kconfig               core options; menu "Drivers" sources drivers/*/Kconfig
  include/usb_eth.h     public API
  private_include/
    usb_eth_driver.h    the core <-> driver contract
    usb_eth_util.h      IDF-free helpers (host tested)
  core/
    usb_eth_core.c      USB side: host client, attach/detach/zombie, bulk transfer pools,
                        RX gate, endpoint + STALL recovery, worker/client tasks, EP0 control
    usb_eth_mac.c       esp_eth side: esp_eth_mac_t ops, PHY register access, filter state,
                        ioctls, constructors (usb_eth_mac_new/new/install/uninstall)
    usb_eth_priv.h      the instance struct shared by those two files (drivers never see it)
    usb_eth_phy.c       usb_eth_phy_new(): the IEEE 802.3 PHY driver, address 0, no reset pin
    usb_eth_registry.c  compiled-in drivers, priority order
    usb_eth_util.c      round_up, ep_mps, ZLP rule, le32, iov, MAC check, recovery limiter,
                        USB address set, synthetic MII registers
  drivers/rtl815x/
    Kconfig             USB_ETH_DRV_RTL815X + its transfer sizes
    rtl815x.c           ops: register/OCP access, per-family init, MAC, filter, up, parser glue
    rtl815x_proto.c/.h  IDF-free: wire format, RX parser, register encoding, chip table,
                        filter math, MAC source, early-aggregation size (host tested)
    rtl815x_regs.h      register map (FreeBSD/OpenBSD if_urereg.h)
  test/host/            Makefile: one binary per suite (core, rtl815x), `make check`
```

## Public API (`usb_eth.h`)

```c
usb_eth_config_t cfg = USB_ETH_DEFAULT_CONFIG();   /* drivers = NULL: all compiled in */
esp_eth_handle_t eth;
usb_eth_install(&cfg, &eth);        /* usb_eth_new() + esp_eth_driver_install() */
...
usb_eth_uninstall(eth);             /* esp_eth_driver_uninstall() + mac/phy del */
```

`usb_eth_new()` returns the MAC + PHY pair for applications that build the
`esp_eth_config_t` themselves. The adapter is detected when it attaches (and on every
re-plug) by matching the compiled-in drivers' ID tables; `cfg.drivers` restricts or
reorders them, `cfg.bind_filter` can veto a device. `USB_ETH_CMD_ADD_MCAST`,
`_DEL_MCAST`, `_S_ALL_MULTICAST` and `_G_INFO` (`usb_eth_info_t`: VID:PID, driver,
chip name and id, counters) are the custom ioctls. There is no RTL8152 compatibility
layer (the old API never shipped).

## The contract (`usb_eth_driver.h`)

A driver is a `const usb_eth_driver_t` with:

| Field / op | Purpose |
|---|---|
| `ids` | `USB_ETH_MATCH_VIDPID` and/or `USB_ETH_MATCH_IFCLASS` (any interface of the active configuration) |
| `priv_size`, `priv_init`, `priv_deinit` | Per-instance private state, from the driver's first bind until another driver binds or the instance is deleted |
| `select_eps` | Data interface, **alternate setting** and bulk pair. NULL: first vendor-class interface, alt 0 |
| `sizing` | Bulk-IN / bulk-OUT transfer sizes for this device and speed (the driver rounds) |
| `bind` | Identify the chip and initialise it; `ESP_ERR_NOT_SUPPORTED` rejects the device |
| `read_mac`, `up`, `set_mac`, `set_filter` | Station address and RX filter; `set_mac` NULL = fixed address |
| `mdio_read`, `mdio_write` | The adapter's MII PHY; NULL for adapters without one |
| `rx_reset`, `rx_feed`, `rx_discard`, `tx_pack` | Wire format, called from the data path |
| `get_stats` | Driver-side RX drops/reassembly for `usb_eth_info_t` |

Core services: `usb_eth_priv`, `usb_eth_speed`, `usb_eth_eps`, `usb_eth_ctrl` (any EP0
request, vendor or class), `usb_eth_set_chip`, `usb_eth_rx_deliver`, and
`usb_eth_report_link` for drivers without MDIO: the core then answers the PHY driver's
polls from a synthetic MII register set (`usb_eth_mii_*`), so esp_eth sees link,
speed (up to 100) and duplex. That is how a class driver (CDC-ECM/NCM) with no vendor
registers and no PHY fits the same table: `mdio_*` NULL, `set_mac` NULL, `select_eps`
picks the CDC data interface with alt 1.

Attach sequence (worker task, `reg_lock` held): match → `select_eps` → claim
(iface, alt) → private state → `bind` → `sizing` + transfer allocation → MAC policy
(`read_mac`, else the ESP's Ethernet MAC; the address is kept for the instance's
lifetime) → `up` → RX starts when esp_eth reports link up. Everything that was
validated in the RTL8152 driver stays in the core and applies to every driver: one
client per instance, NEW_DEV/DEV_GONE plus NO_DEVICE unplug detection, the skip set,
zombie close with the 20 ms / lib-task wait, the stale DEV_GONE drain, `rx_gate`
(no frame reaches the stack after `esp_eth_stop()` returns), lock order `reg_lock` →
`xfer_lock`, the 5-strike recovery limiter, and STALL recovery by re-claiming the
interface (with the same alternate setting) for DATA0.

## rtl815x: chip variants

The chip is identified in `bind` by `PLA_TCR1 & 0x7cf0` through a table
(`rtl815x_chip_lookup`: version → name, family). Family-specific steps sit behind a
four-entry ops table in `rtl815x.c` (`init`, `reset` at bring-up, `rx_agg`, default
high-speed transfer size), so adding a family (RTL8153A, RTL8156) means a table row,
an ops struct and its sequences, not version checks across the file.

| Family | Versions | init | reset in `up` | RX aggregation | Default RX transfer (HS / FS) |
|---|---|---|---|---|---|
| RTL8152 | 0x4c00, 0x4c10 | `ure_rtl8152_init` + `ure_rtl8152_nic_reset` | `ure_reset` | defaults | 16 KiB / 4 KiB |
| RTL8153B | 0x6000, 0x6010 | `ure_rtl8153b_init` | `ure_rtl8153b_nic_reset` | early agg 158, extra timer 1875, early size from the transfer size | 16 KiB / 4 KiB |

## Deviations from the proposal

- **Core files.** Two core source files (USB side, esp_eth side) sharing
  `usb_eth_priv.h`, not six. The split by thread/role is the one that matters;
  `usb_eth_ctrl` and the transfer pool are small enough to live with attach/detach.
- **No capability flags.** The proposal's `CAP_MII`/`CAP_FIXED_MAC`/`CAP_RX_STREAM`
  are expressed by the ops themselves (`mdio_read == NULL`, `set_mac == NULL`), and
  stream semantics are the driver's business inside `rx_feed`/`rx_discard`.
- **No `probe` score.** First match in priority order is enough with one driver;
  a score can be added when two drivers really compete for one device.
- **`bind` before `sizing`.** The transfer size may depend on the chip (each rtl815x
  family has its own default), which is known only after `bind` reads the version
  register.
- **`sizing` returns final sizes.** The driver applies its own rounding (the RTL
  parser needs multiples of lcm(MPS, 8)); the core only checks that the IN size is a
  multiple of MPS.
- **Private state** is one arena sized for the largest driver, allocated with the
  instance, re-initialised (`priv_init`) when a different driver binds.
- **CRC/hash** (`mcast_hash_bit`) stayed in rtl815x_proto: no second user yet.
- **No compatibility layer** (`rtl8152.h`, `sdkconfig.rename`, shim component), by
  decision: the old API never shipped.
- **Kconfig names**: `USB_ETH_*` for the core, `USB_ETH_DRV_<X>` and
  `USB_ETH_<X>_*` per driver; `USB_ETH_RX_XFER_SIZE = 0` means driver default.

## Not implemented yet (needed by the next drivers)

- Interrupt-endpoint link notifications (`intr_in`, `link_poll`, `WORK_LINK_*`):
  RTL815x polls the PHY like before; CDC-ECM/NCM and ASIX will want them.
- `link_changed` op (ASIX medium mode).
- Enumeration-filter helper for picking a configuration (`usb_eth_enum.h`), for
  adapters whose ECM/NCM function is not configuration 1.
- Software unicast filter for fixed-MAC (CDC) adapters with a foreign address.
- The fake-USB state-machine harness and the capture-based conformance suite
  (proposal §5); host tests today cover the util and rtl815x pure logic.

## Licensing

BSD-2-Clause. The rtl815x driver derives from FreeBSD `ure(4)`; its RTL8153B parts
were cross-checked against OpenBSD `ure(4)` (same author, same licence), and the
choices where they differ are commented in the code. Both notices are in `LICENSE`
and in the rtl815x source headers. Linux `r8152.c` (GPL) was not used.
