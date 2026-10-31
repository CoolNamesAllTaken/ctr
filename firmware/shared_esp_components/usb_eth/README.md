# usb_eth: USB Ethernet adapters for ESP-IDF

Drives USB Ethernet adapters through the ESP-IDF **USB Host library** and exposes
them as a standard **`esp_eth` MAC + PHY pair**. Applications use it exactly like
any other ESP Ethernet chip: `usb_eth_install()` (or `usb_eth_new()` +
`esp_eth_driver_install()`), `esp_eth_new_netif_glue()`, then DHCP, sockets, etc.
via lwIP. The adapter is detected when it is plugged in and bound to the first
compiled-in driver that supports it; see [ARCHITECTURE.md](ARCHITECTURE.md) for the
core/driver split.

> **Status: tested on hardware**: RTL8152B (0x4c10) on ESP32-P4 (Waveshare
> ESP32-P4-WIFI6-Touch-LCD-3.4C and Elecrow CrowPanel Advanced 5", chip rev v1.3,
> USB high speed) and ESP32-S3 (DevKitC-1, USB full speed); RTL8153B (0x6010) on the
> Elecrow. See [Hardware test results](#hardware-test-results) and
> [What is still unverified](#what-is-still-unverified).

| | |
|---|---|
| Chips | ESP32-S3 (USB OTG full speed), ESP32-P4 (USB OTG high speed) |
| ESP-IDF | v5.4.x and v5.5.x (tested on hardware with v5.4.2 and v5.5.5; on the ESP32-P4 rev < 3.0 use v5.5.5+ or see [Known ESP-IDF issue](#known-esp-idf-issue-usb-host-assert-under-load-esp32-p4-rev--30)) |
| Adapters | Driver `rtl815x`: Realtek RTL8152 (`0bda:8152`, chip versions 0x4c00/0x4c10, 10/100) and RTL8153B (`0bda:8153`, 0x6000/0x6010, 10/100/1000), vendor configuration. RTL8153A and RTL8156 are rejected by their version register. |
| License | BSD-2-Clause (rtl815x register sequences derived from FreeBSD `ure(4)`, RTL8153B cross-checked with OpenBSD `ure(4)`; see [Sources](#sources-and-licensing)) |

## Hardware

### ESP32-S3

The S3 has one USB OTG 1.1 controller (**full speed, 12 Mbit/s**, 64-byte
bulk packets) on fixed pins **GPIO19 (D−)** and **GPIO20 (D+)**, shared with
USB-Serial-JTAG (the USB Host library switches the PHY to OTG).

- Wire the dongle's D−/D+ to GPIO19/GPIO20 (on most dev boards that is the
  connector labelled "USB", not "UART"; use an OTG adapter).
- **Supply VBUS (5 V) to the dongle yourself.** Dev boards generally do not
  power their USB connector in host mode. Use a board with a host power switch,
  a powered OTG Y-cable or a powered hub port, and budget about 200 mA.
- Keep the console on UART0 (the default), since GPIO19/20 are taken.
- Expect at most about 8–9 Mbit/s of Ethernet throughput: the USB link, not
  the 100 Mbit/s PHY, is the bottleneck.

### ESP32-P4

The P4 has two USB OTG controllers. The ESP-IDF 5.4 USB Host library only
uses the **USB 2.0 high-speed OTG controller with its internal UTMI PHY**
(dedicated USB pins, not GPIO-routed; see `usb_host.c`: "We support HS+UTMI
only ATM"). Plug the dongle into the connector wired to that HS port. On the
ESP32-P4-Function-EV-Board that is the USB-A host connector, not the
USB-Serial-JTAG or UART ports. The FS OTG 1.1 controller is not usable with
the 5.4 host library. Make sure that port supplies 5 V VBUS.

At high speed the bulk endpoints use 512-byte packets and rtl815x uses 16 KiB
RX transfers by default.

Board notes (per-board `sdkconfig` defaults live in `examples/basic/boards/`):

- **Waveshare ESP32-P4-WIFI6-Touch-LCD-3.4C**: the USB-A OTG port (J3) is on the
  HS controller, and its VBUS comes from a load switch that is always enabled.
  The dongle works plugged straight in. Opening the CH343 UART port resets
  the P4.
- **Elecrow CrowPanel Advanced 5" (ESP32-P4)**: the HS port is the USB-C J16,
  but its VBUS only feeds *into* the board (through a Schottky diode). The board
  cannot power a device on it, so use a powered OTG adapter. Validated with one
  (see [Hardware test results](#elecrow-crowpanel-advanced-5-esp32-p4)).

### Hubs

`CONFIG_USB_HOST_HUBS_SUPPORTED` is off by default in IDF 5.4, so connect the
dongle directly to the root port unless you enable hub support.

## Usage

`idf_component.yml` of your app (or copy the `usb_eth` folder into `components/`):

```yaml
dependencies:
  usb_eth:
    path: ../path/to/esp-rtl8152/usb_eth
```

```c
#include "usb/usb_host.h"
#include "esp_eth.h"
#include "esp_netif.h"
#include "usb_eth.h"

static void usb_lib_task(void *arg)
{
    for (;;) {
        uint32_t flags;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
    }
}

void app_main(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    // 1. USB Host library: install once and run its event loop
    const usb_host_config_t host_cfg = { .intr_flags = ESP_INTR_FLAG_LEVEL1 };
    ESP_ERROR_CHECK(usb_host_install(&host_cfg));
    TaskHandle_t usb_lib;
    xTaskCreate(usb_lib_task, "usb_lib", 4096, NULL, 20, &usb_lib);   // above the driver's tasks

    // 2. usb_eth MAC + PHY, installed into esp_eth
    usb_eth_config_t cfg = USB_ETH_DEFAULT_CONFIG();
    cfg.usb_lib_task = usb_lib;   // optional, see "Task priorities"
    esp_eth_handle_t eth;
    ESP_ERROR_CHECK(usb_eth_install(&cfg, &eth));

    // 3. lwIP via the standard glue (DHCP client on by default)
    esp_netif_config_t netif_cfg = ESP_NETIF_DEFAULT_ETH();
    esp_netif_t *netif = esp_netif_new(&netif_cfg);
    ESP_ERROR_CHECK(esp_netif_attach(netif, esp_eth_new_netif_glue(eth)));
    ESP_ERROR_CHECK(esp_eth_start(eth));
}
```

To tear down: `esp_eth_stop()`, `esp_eth_del_netif_glue()`, `esp_netif_destroy()`,
then `usb_eth_uninstall(eth)` (uninstalls esp_eth and deletes the MAC and PHY).
`usb_eth_new()` returns the MAC + PHY pair for code that builds its own
`esp_eth_config_t`. `cfg.drivers` restricts or orders the drivers an instance may
bind, and `cfg.bind_filter` can leave a device alone (e.g. to pin an instance to one
VID:PID when several adapters are attached).

`examples/basic` is a complete version: it prints link and IP events, the chip,
the USB speed and packet size, and pings the gateway.

Extra `esp_eth_ioctl()` commands (`usb_eth.h`): `USB_ETH_CMD_ADD_MCAST` /
`USB_ETH_CMD_DEL_MCAST` (multicast filter), `USB_ETH_CMD_S_ALL_MULTICAST`,
`USB_ETH_CMD_G_INFO` (`usb_eth_info_t`: attach state, VID:PID, driver, chip,
USB speed, packet sizes, counters). `ETH_CMD_S_PROMISCUOUS` and
`ETH_CMD_S_MAC_ADDR` work as usual.

### Hot-plug

- `esp_eth_driver_install()` waits up to `CONFIG_USB_ETH_ATTACH_TIMEOUT_MS`
  (default 3 s) for a dongle so that its MAC address can be used. Without one
  it continues and uses the ESP's own Ethernet MAC (`esp_read_mac(ESP_MAC_ETH)`).
  That address stays for the life of the driver instance, also after a dongle is
  plugged in. So the interface MAC depends on whether the dongle enumerated in
  time, the same way IDF picks the ESP's address for its internal EMAC and the
  chip's own address for chips that have one (DM9051). For a fixed address, set it
  after install with `ETH_CMD_S_MAC_ADDR`. The glue (`esp_eth_new_netif_glue`)
  hands lwIP whatever `ETH_CMD_G_MAC_ADDR` returns.
- rtl815x reads the adapter's own address from `PLA_BACKUP` (the copy loaded from
  its efuse/EEPROM at power-up) on 0x4c10 and the RTL8153B, as FreeBSD and Linux
  do, and from `PLA_IDR` on 0x4c00 or if `PLA_BACKUP` is empty. `PLA_IDR` is the live filter address, and
  the driver writes the interface MAC there. It keeps that value until the dongle
  loses power, so after a warm restart of the ESP it would return the previous
  run's address.
- The chip's unicast filter (`PLA_IDR`, RCR `APM`) is always programmed with the
  interface MAC, at every attach and on `ETH_CMD_S_MAC_ADDR`. Unicast RX works
  without promiscuous mode whichever source the address came from.
- Unplug: the driver cancels and frees all transfers and closes the device.
  The PHY registers then read as "link down", so esp_eth's normal link poll
  posts `ETHERNET_EVENT_DISCONNECTED` (within `check_link_period_ms`, 2 s by default).
- Replug (same or another dongle): the chip is re-initialised and programmed
  with the interface's existing MAC address. When the link poll sees the link
  come up, it posts `ETHERNET_EVENT_CONNECTED` and RX restarts. DHCP renews as usual.
- A dongle whose initialisation fails is not retried until it is replugged. A
  device that could not even be opened (e.g. it vanished during the scan) is
  retried on the next scan.
- Unplug is detected from IDF's `DEV_GONE` event, and also from a bulk transfer
  completing with `NO_DEVICE`, in case IDF dropped the event (its client event
  queue, 16 messages here, drops messages when full). That fallback only helps
  while bulk transfers are in flight on a directly connected dongle. Behind an
  external hub, IDF completes them as `CANCELED`, and with the link down no RX
  transfers are queued. A lost `DEV_GONE` in those cases leaves the old device
  open, and a replugged dongle is ignored until the driver is reinstalled.
- One adapter per driver instance. Devices no driver supports are ignored and left
  closed. An RTL8152 can be swapped for an RTL8153B (or back) without a reboot: the
  next attach re-sizes the transfers for the new chip.

### Configuration (`menuconfig` → *Component config* → *USB Ethernet (usb_eth)*)

| Option | Default | Meaning |
|---|---|---|
| `USB_ETH_ATTACH_TIMEOUT_MS` | 3000 | Install-time wait for the first adapter |
| `USB_ETH_RX_XFER_SIZE` | 0 | Bulk-IN transfer size; 0 = the driver's default for the chip and USB speed |
| `USB_ETH_RX_XFER_COUNT` | 3 / 4 (P4) | Bulk-IN transfers in flight |
| `USB_ETH_TX_XFER_COUNT` | 4 / 8 (P4) | Bulk-OUT transfers (TX queue depth) |
| `USB_ETH_TX_TIMEOUT_MS` | 50 | Wait for a free TX transfer before dropping |
| `USB_ETH_CTRL_TIMEOUT_MS` | 1000 | Register access timeout |
| `USB_ETH_ACCEPT_ALL_MULTICAST` | y | Accept all multicast (IDF 5.4's esp_eth has no multicast API; needed for IPv6/mDNS) |
| `USB_ETH_WORKER_TASK_STACK_SIZE` / `_PRIO` | 4096 / 5 | Attach/detach worker task |
| *Drivers* → `USB_ETH_DRV_RTL815X` | y | Realtek RTL8152 / RTL8153B driver |
| `USB_ETH_RTL815X_RX_SIZE_8152_HS` | 16384 | RTL8152 RX transfer at high speed |
| `USB_ETH_RTL815X_RX_SIZE_8153B_HS` | 16384 | RTL8153B RX transfer at high speed (its RX aggregation threshold follows it) |
| `USB_ETH_RTL815X_RX_SIZE_FS` | 4096 | Any chip at full speed (ESP32-S3) |

`eth_mac_config_t.rx_task_stack_size` / `rx_task_prio` size the task that runs
USB client events and RX processing.

#### Task priorities

Run your `usb_host_lib_handle_events()` task at a **higher priority than both
driver tasks**: the client/RX task (`eth_mac_config_t.rx_task_prio`, 15 with
`ETH_MAC_DEFAULT_CONFIG()`) and the worker (`USB_ETH_WORKER_TASK_PRIO`, 5).
The example uses 20. Reason: IDF decrements its in-flight control-transfer
counter in that task *after* our completion callback has run, and
`usb_host_device_close()` asserts that the counter is zero. IDF exposes no
counter, so before closing a device the driver:

1. waits until 20 ms have passed since the last control completion (measured
   with `esp_timer_get_time()`);
2. if you pass the lib task's handle in `usb_eth_config_t.usb_lib_task`
   (recommended; the example does), also waits (max 500 ms) until that task is
   Blocked. It never blocks between dispatching the callback and the decrement,
   so seeing it blocked proves the decrement ran. With the handle set, the
   driver also logs a warning at init if the priority order above is not met.
   **The handle must stay valid until `mac->del()`**: don't delete the lib task
   while the driver is installed. Some IDF examples end their lib loop on
   `USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS` and delete the task; the driver's client
   is registered until `mac->deinit()`/`mac->del()`.
   Also don't call `usb_host_device_free_all()` on `NO_CLIENTS` (as those
   examples do) if the driver may be installed again: in IDF 5.4 freeing the
   dongle disables its port until it is physically replugged, so a reinstalled
   driver finds no device (seen on the ESP32-P4).

With the priority order above, the decrement happens within microseconds of the callback.

#### Stopping and locks

- `esp_eth_stop()` and `mac->stop()` wait for the client task to finish
  delivering the frames of the transfer it is processing (see `rx_gate` below).
  That delivery runs the input path: `esp_netif_receive` → lwIP `tcpip_input`,
  which takes the **TCPIP core lock** with `CONFIG_LWIP_TCPIP_CORE_LOCKING_INPUT`,
  plus L2TAP or any custom `stack_input`. **Do not call `esp_eth_stop()` or
  `mac->stop()` while holding the TCPIP core lock** (e.g. from
  `esp_netif_tcpip_exec()` or inside `LOCK_TCPIP_CORE()`), or any lock your input
  path takes: that deadlocks against the client task.
- **Deleting the netif after stop.** IDF's `esp_eth_stop()` stops the link timer
  with `esp_timer_stop()`, which does not wait for a link-poll callback that is
  already running. If that callback is reporting link up at that moment,
  `esp_eth_stop()` skips the link-down report, and RX keeps delivering frames.
  This affects every IDF MAC that starts in `set_link`. With this driver you can
  close the window: call `mac->stop(mac)` after `esp_eth_stop()` and before
  `esp_eth_del_netif_glue()` / `esp_netif_destroy()`. It is synchronous for
  frame delivery, like link down. Call `mac->start(mac)` before the next
  `esp_eth_start()`.

## Design

```
 lwIP <-> esp_netif <-> esp_eth glue <-> esp_eth driver
                                           |  mac: core/usb_eth_mac.c   phy: IEEE 802.3 base (core/usb_eth_phy.c)
                                           |        |                       | MII regs via read/write_phy_reg
                                           |        v                       v
                                           |  core/usb_eth_core.c: attach/detach, bulk IN/OUT, recovery, EP0
                                           |        |  driver ops (private_include/usb_eth_driver.h)
                                           |        v
                                           |  drivers/rtl815x: register + OCP access, chip init, RX parser, TX pack
                                           v
                                  ESP-IDF USB Host library (client) -> USB OTG -> adapter
```

The rest of this section describes the core together with the rtl815x driver.

- **`rtl815x_proto.c`** has no IDF dependencies: RX descriptor stream parser,
  TX descriptor packing, MPS-based buffer sizing, register byte-enable
  encoding, OCP window mapping, multicast hash, PHY status decoding, the chip
  variant table and the RTL8153B aggregation threshold. **`usb_eth_util.c`** holds
  the chip-independent helpers (recovery limiter, address set, synthetic MII).
  Both are unit tested on the host (`test/host`).
- **Register access**: vendor request `0x05`, `wValue` = register,
  `wIndex` = MCU type (PLA `0x0100` / USB `0x0000`) | byte enables, with 4
  little-endian data bytes. PHY registers live in the OCP space, reached by
  writing the 4 KiB page to `PLA_OCP_GPHY_BASE` and accessing `0xb000 | offset`.
  The internal PHY's MII registers sit at OCP `0xa400 + 2*reg`.
- **PHY**: `usb_eth_phy_new()` is the stock IEEE 802.3 PHY driver
  with address 0 and no reset GPIO, so speed/duplex/link detection and
  autonegotiation come from esp_eth's link poll (BMSR/ANLPAR read through
  `read_phy_reg`, i.e. the driver's `mdio_read`). The interrupt endpoint is not
  used. That driver only knows 10/100: an RTL8153B linked at 1000 Mbit/s would be
  reported as 100M (the MAC itself runs at the negotiated speed). The RTL8153B
  advertises 1000BASE-T full duplex by default (GBCR 0x0200).
- **RX**: several bulk-IN transfers stay in flight. Their size is
  `rtl815x_rx_xfer_size(config, wMaxPacketSize)`: a multiple of both the
  endpoint MPS (64 at FS, 512 at HS) and 8, and at least one maximum frame.
  The chip aggregates frames (24-byte descriptor + frame + FCS, 8-byte aligned).
  The parser handles many frames per transfer, frames and descriptors split
  across transfers (reassembled in a 1522-byte carry buffer), runts, truncated
  bursts, and impossible lengths (it drops the rest of the burst rather than
  guessing, to avoid packet-in-packet injection). Each frame is copied into a
  `malloc`ed buffer and passed to `stack_input`.
- **TX**: one frame per bulk-OUT transfer with the 8-byte descriptor
  (`len | FS | LS`), frames padded to 60 bytes, `USB_TRANSFER_FLAG_ZERO_PACK`
  so a transfer that is an exact multiple of the MPS is terminated.
- **Start/stop**: esp_eth never calls `mac->start`/`mac->stop`; like the
  in-tree IDF MACs, RX follows `mac->set_link()`. Link up (from the PHY poll)
  starts the bulk-IN transfers; link down (PHY poll, or `esp_eth_stop()`)
  stops them. `mac->stop`/`mac->start` additionally let an application pause
  reception.
- **Threads**: a *client task* runs `usb_host_client_handle_events()`; every
  transfer callback (RX parsing, TX completion, control completion) runs
  there and never blocks on a transfer. A *worker task* does attach (open,
  claim, chip init), detach, RX start/stop and endpoint error recovery,
  because control transfers block on a semaphore the client task gives. Work
  reaches the worker as event-group bits, so requests coalesce and cannot be
  lost. `reg_lock` (recursive) serialises register access, `xfer_lock` guards
  transfer ownership, and `rx_gate` makes stopping frame delivery synchronous.
  Lock order: `reg_lock` → `xfer_lock`. `rx_gate` is never held together with
  either: it is taken only by the RX callback, around frame delivery, and
  passed through by link down / `mac->stop`.
- **PHY polls** (`read_phy_reg`, esp_timer task) wait at most 50 ms for
  `reg_lock`. Their control transfers use a 100 ms timeout instead of
  `ctrl_timeout_ms`, so a dongle that stops answering EP0 holds the esp_timer
  task for at most ~250 ms, once. After that the timed-out transfer stays in
  flight (IDF does not implement transfer timeouts), and every register access
  fails fast with `ESP_ERR_TIMEOUT` until it completes. Ioctls and
  `ETH_CMD_S_MAC_ADDR` return that error. PHY polls answer from the last values
  read from the real PHY, as on a lock timeout, so esp_eth keeps the current link
  state. While no dongle is ready they answer from the virtual link-down PHY.
- **Chip init, RTL8152** (FreeBSD `ure_rtl8152_init` + `ure_rtl8152_nic_reset` +
  `ure_init`): ALDPS off, power/clock setup, RX aggregation on, MAC reset,
  OOB/link-list re-init, RX FIFO thresholds chosen by USB speed (full vs high),
  TX FIFO, TX aggregation, MAC address, packet filter reset, RX/TX enable,
  RCR + 64-bit multicast hash.
- **Chip init, RTL8153B** (FreeBSD `ure_rtl8153b_init`, `ure_rtl8153b_nic_reset`
  and the 8153B part of `ure_init`, cross-checked with OpenBSD): ALDPS off, U1/U2
  and U2P3 off, wait for the autoload and the PHY, PHY power-down cleared, power
  cut / UPS / runtime-suspend wake off, MAC clock speed-down, RX aggregation on;
  at bring-up BMU reset, link-list re-init, RX max frame 1522, FIFO thresholds,
  ALDPS on, then RX early aggregation (timeout 158, extra timer 1875, size derived
  from the transfer size so an aggregate never outgrows a transfer). Where the two
  BSDs differ the code comments which was followed (mostly PLA registers that
  FreeBSD accesses with the USB MCU type).

### ESP-IDF 5.4 quirks handled by the component

- esp_eth only builds its core when some controller selects `ETH_ENABLED`, and
  the S3 has no internal EMAC. The component's Kconfig selects it.
- `esp_eth_driver.h` only includes `esp_eth_mac.h` through the EMAC / SPI /
  OpenETH headers, so with none of them enabled esp_eth itself does not
  compile. The Kconfig also selects the SPI Ethernet umbrella option (no SPI
  chip drivers, so no extra code) when neither EMAC nor OpenETH is enabled.
- `esp_eth_phy_new_generic()` is only built on chips with an EMAC, so the PHY
  object is built directly on the 802.3 base (`esp_eth_phy_802_3_obj_config_init`).

## Resource ownership

Every allocation has exactly one owner at any time. Who frees what:

| Resource | Created by | Owner / freed by |
|---|---|---|
| MAC object, `tx_pool` queue, event groups (`events`, `work_ev`), `reg_lock`, `xfer_lock`, `rx_gate`, `ctrl_done`, driver private state (incl. the RX carry buffer) | `usb_eth_mac_new()` (via `usb_eth_new()`/`usb_eth_install()`) | `mac->del()` (also on any failure inside `usb_eth_mac_new`); `usb_eth_uninstall()` calls it |
| PHY object | `usb_eth_phy_new()` | `phy->del()` (802.3 base); `usb_eth_uninstall()` calls it |
| USB host client, control transfer, client task, worker task | `mac->init()` | `mac->deinit()`; also `mac->del()` if deinit was skipped (esp_eth frees its mediator without calling `mac->deinit()` when `phy->init()` fails), and `mac->init()`'s own error path. All go through `usb_eth_usb_shutdown()`, which is idempotent. |
| Device handle, claimed interface | worker on `NEW_DEV` (`handle_new_dev`) | worker: `dev_teardown()` → `dev_release()` on unplug (`DEV_GONE`), on any init failure, and on `mac->deinit()`/`del()` (`WORK_QUIT`). IDF refuses `usb_host_interface_release()` while URBs are in flight and asserts in `usb_host_device_close()` while a control transfer is in flight, so release only happens once every transfer has returned. Until then the device stays open as a *zombie*: data path off, PHY reads as link down. The worker retries every 100 ms, on `DEV_GONE` and before exiting. After every close, the client task drains a `DEV_GONE` that may still be queued for the closed handle (IDF can reuse the handle for the next device), and only then does the worker rescan. A device that does not match or fails init is skipped until a `NEW_DEV` arrives for its address, i.e. a replug. |
| Bulk transfers | `alloc_transfers()` at attach | The slot in `rx_xfer[]`/`tx_xfer[]` owns the transfer. While submitted, the USB stack holds it. `dev_teardown()` → `free_transfers()` frees idle ones. A transfer still held after the 2 s teardown timeout is *abandoned*: its slot is cleared and its completion callback (`rx_xfer_cb`/`tx_release`) frees it when it returns. Freeing it earlier would be a use-after-free. |
| **RX frame buffer** | `usb_eth_rx_deliver()` (`malloc`, one per frame), called from the driver's parser | **The network stack**, from the moment it is passed to `eth->stack_input()`, on success *and* on error (`ethernetif_input` → `esp_netif_free_rx_buffer` / `pbuf_free`; `eth_stack_input` frees it if no input path is set). The driver never touches it again. If you install your own path with `esp_eth_update_input_path()`, your callback must `free()` it. |
| **TX frame buffer** | caller of `esp_eth_transmit()` (lwIP pbuf) | **Caller.** The driver copies the frame into a bulk-OUT transfer before returning, never keeps or frees the caller's buffer. |
| TX transfer during a send | taken from `tx_pool` by `transmit()` | `transmit()` until submitted, then the USB stack, then `tx_xfer_cb()` which puts it back into `tx_pool` (or frees it if abandoned). Every error path calls `tx_release()`. |

Per event:

- **RX**: transfer completes → client task takes `rx_gate`, and only if RX is
  wanted (link up, not stopped) parses it: each frame is `malloc`ed, copied
  and handed to the stack (stack frees) → the transfer is resubmitted under
  `xfer_lock`, or its slot is marked idle (`rx_busy = false`).
- **Link down / `esp_eth_stop()` / `mac->stop`**: `set_link(DOWN)` clears the
  "RX wanted" flag and passes through `rx_gate` before returning, so **no frame
  reaches `stack_input` after `esp_eth_stop()` returns**. Deleting the netif
  glue and the netif afterwards is safe, even though the worker is still
  cancelling the bulk-IN transfers (halt, flush, wait, clear). The transfers
  stay allocated and idle for the next link up. Nothing in this path blocks on
  USB, so the esp_timer task (link poll) is never held up.
- **TX**: `transmit()` takes a pooled transfer, copies the caller's frame and
  submits it under `xfer_lock` after checking the device is ready and TX is not
  paused. Completion returns it to the pool. On failure it is returned immediately.
- **TX error** (transaction error, overflow): the HCD halts and flushes the OUT
  pipe and IDF rejects later submits. `tx_xfer_cb` pauses TX (`transmit()`
  returns `ESP_ERR_INVALID_STATE`), and the worker waits for every TX transfer
  to be back in the pool, clears the host pipe and resumes. Same 5-attempt limit
  as RX. No allocation changes hands.
- **STALL on either bulk endpoint**: `CLEAR_FEATURE(ENDPOINT_HALT)` resets the
  device's data toggle to DATA0, but IDF 5.4 never resets a bulk pipe's host
  toggle. It loads DATA0 only when the pipe is allocated at interface claim
  (`usb_dwc_hal_chan_alloc` → `hctsiz_init`). So the worker pauses both
  directions, halts and flushes both pipes, waits for every transfer, then
  **releases and re-claims the interface** (fresh host pipes at DATA0). It sends
  `CLEAR_FEATURE(ENDPOINT_HALT)` to both endpoints (device toggles at DATA0,
  halted or not) and restarts. The transfers stay allocated, since they only
  name the device and endpoint address. The interface stays owned by the
  worker throughout. If the re-claim fails, the device goes through the normal
  unplug path and is bound again by the next scan (it is not skipped).
- **Unplug**: worker sets `DEV_CLOSING`, passes through `xfer_lock` (so no
  submit can follow), halts and flushes both bulk endpoints, waits for the
  transfers, frees them, then releases the interface and closes the device
  (zombie retry if the USB stack still holds anything, see above). Register
  access in progress finishes first (`reg_lock`). A timed-out control
  transfer must complete before the device is closed; on a real unplug IDF
  completes it with `NO_DEVICE`.
- **RX error** (transaction error, overflow; STALL see above): the HCD halts
  and flushes the IN pipe and every RX transfer returns. The worker waits for
  them, clears the host pipe and restarts RX. After 5 recoveries without a good
  transfer it gives up until replug. No allocation changes hands.
- **Gaps in the RX stream** (tested on the host):
  - When the RX callback drops a completed transfer that did not end a burst
    (RX off), the bytes are certainly missing. The parser discards up to the
    next burst boundary (short transfer) instead of splicing new data onto an old
    partial frame.
  - When RX restarts after a stop or recovery, the IN pipe was halted and its
    transfers cancelled. IDF reports 0 bytes for cancelled transfers, so it is
    unknown whether an aggregate was cut. The first transfer is still parsed if
    it is provably a whole burst: a short transfer that is exactly a chain of
    plausible, aligned RX records. On a quiet link that is the usual case, so
    the first frame after a link-up (often the DHCP or ARP reply) is kept.
    Otherwise everything up to the next burst boundary is dropped: at most the
    first aggregate after a restart under load.
- **Transfers that never return** within 2 s, during a stop or a recovery: the
  pipe is left halted and that direction stays off until replug. The
  transfers are abandoned and freed by teardown as described above.
- **deinit / del**: `WORK_QUIT` → worker runs the unplug path and exits →
  client task exits → control transfer freed → client deregistered. `del`
  then frees the object's own queues, semaphores and buffers.

Known deliberate leak: a control or bulk transfer can *never* complete,
for example when a still-connected dongle NAKs EP0 forever (IDF ignores
`timeout_ms`). The device then stays a zombie until it is unplugged. If that
is still the case at `deinit`, after 5 s the transfer, device handle and USB
client are logged as leaked rather than freed while the USB stack owns them.
Freeing them would be a use-after-free or would trip IDF asserts.

These paths were reviewed against the IDF v5.4.2 sources (`usb_host.c`,
`usbh.c`, `hcd_dwc.c`, `esp_eth.c`, `ethernetif.c`). They can only be
exercised on hardware: see items 9–11 of the checklist.

## Building and testing

```sh
# ESP-IDF v5.4.x or v5.5.x environment (P4 rev < 3.0: prefer v5.5.5+, see below), then:
cd examples/basic
idf.py set-target esp32s3 build      # or esp32p4

# Host unit tests for the IDF-independent logic (gcc only)
cd usb_eth/test/host
make            # plain
make asan       # AddressSanitizer + LeakSanitizer + UBSan, fails on any leak/error
make valgrind   # memcheck, fails on any leak/error (needs glibc debug symbols, libc6-dbg)
make check      # all three; prints "SKIP valgrind" instead of failing if valgrind is missing
```

The component compiles with `-Wall -Wextra -Werror -Wshadow` (set in its
CMakeLists), on top of IDF's default warnings.

## Hardware test checklist

Run `examples/basic` with `idf.py monitor` and the log level at INFO (DEBUG for `usb_eth` and `rtl815x`):

1. **Enumeration**: log shows `RTL8152 (0bda:8152, rtl815x 0x4c10) attached at full speed`
   (S3) or `high speed` (P4), `bulk MPS in/out 64/64` (S3) or `512/512` (P4), and a
   sane MAC (compare with the adapter's label or what a PC reports).
2. **No init timeouts**: no `timeout waiting for ...` or `control transfer ... timed out` messages.
3. **Link**: plug a cable into a 100 Mbit switch, get `link up`. Check that the
   negotiated speed/duplex (esp_eth `ETH_CMD_G_SPEED` / `G_DUPLEX_MODE`) is 100/full. Repeat with a
   10 Mbit/half-duplex peer if one is available.
4. **DHCP + ping**: `got IP` and 5 replies from the gateway. Ping the ESP from a PC
   (`ping -s 1472` for full-size frames, `ping -s 1473` to check IP fragmentation).
5. **RX aggregation / reassembly**: flood ping from a PC (`ping -f`, or
   `iperf -u -b 20M` towards the ESP), then check `USB_ETH_CMD_G_INFO`:
   `rx_frames` growing, `rx_dropped` small, `rx_reassembled` > 0 under load.
6. **Throughput**: iperf TCP both directions. Expect about 6–9 Mbit/s on S3 (FS limit)
   and tens of Mbit/s on P4.
7. **Multicast/broadcast**: ARP works (implied by ping). mDNS or an IPv6 link-local
   ping works with `ACCEPT_ALL_MULTICAST=y`. With it off, add a group via
   `USB_ETH_CMD_ADD_MCAST` and check that only that group arrives. Check
   `ETH_CMD_S_PROMISCUOUS`.
8. **Cable unplug/replug**: `link down`/`link up` events, DHCP renews, RX resumes
   (`rx_frames` grows again). Also `esp_eth_stop()` → `esp_eth_start()`: RX resumes
   after link up, and no `rx_frames` increase between stop and start.
9. **Adapter hot-plug**: unplug, get `RTL8152 detached` (the chip name) and `link down` within ~2 s.
   Replug, get attach, link up, new lease, same MAC. Repeat 50× while watching
   `esp_get_free_heap_size()`: it must return to the same value (leak check on target).
10. **Boot without dongle**: install continues after 3 s (warning), plug in later, works.
11. **stop/start and uninstall**: `esp_eth_stop` / `esp_eth_start` cycles, and
    `esp_eth_driver_uninstall` + `mac->del` + `phy->del`, with heap returning to baseline.
12. **Long run**: hours of iperf. No `RX transfers did not complete`,
    `stalled, re-synchronising`, `transfer error, recovering`, or growing `tx_errors`.
    If a STALL recovery does happen, traffic must continue normally after it (data-toggle check).

## Hardware test results

ESP32-P4 (Waveshare board above, IDF v5.4.2) with an RTL8152B (0x4c10) dongle
attached at high speed (bulk MPS 512), connected to a Linux PC's 100 Mbit
port. The scripts are in `examples/basic/hwtest/`, and the firmware is
`examples/basic` with `sdkconfig.hwtest`.

| Test | Result |
|---|---|
| Enumeration, init, MAC from `PLA_IDR` (before the `PLA_BACKUP` change) | OK on first boot, no init timeouts, MAC matches the label |
| Link / DHCP | 100M full duplex, lease obtained, 5/5 gateway pings |
| Ping from PC | 1472 B with DF: OK. 1473 B and 4000 B (fragmented): OK with `CONFIG_LWIP_IP4_REASSEMBLY=y` (off by default in IDF) |
| Sustained ping, 5 min each way (1400 B, 5/s) | 1500/1500 both directions, heap delta −236 B |
| TCP, IDF default lwIP config | to ESP 18.2 Mbit/s, from ESP 16.3 Mbit/s |
| TCP, `sdkconfig.perf` (64 KB windows) | to ESP 67–76 Mbit/s, from ESP 86–95 Mbit/s |
| UDP to ESP (1400 B) | lossless at 20, 40 and 50 Mbit/s. At 80–95 Mbit/s the application socket drops datagrams, but the driver's `rx_frames` matches what was sent (the loss is in lwIP/app, not the driver) |
| `esp_eth_stop`/`start` | 20/20 cycles, heap flat |
| uninstall + reinstall (driver, glue, netif) | 25/25 cycles, heap back to baseline (415008 vs 415080 B after settling) |
| Cable unplug/replug | link down/up, new lease, traffic resumes |
| Dongle hot-plug (3×) | detach seen immediately; re-attach with same MAC, link and lease; heap back to baseline |
| Boot without dongle | install and `esp_eth_start` return immediately, and a dongle plugged in later works |
| `rx_dropped`, `tx_errors` | 0 throughout all tests |
| Soak, IDF v5.4.2 + v2 patch (4.2 h, a round every 10 min: ping both ways, TCP both ways, UDP 40 Mbit/s, 3 stop/start, every 2nd round 2 reinstalls) | 0 panics. Pings 9050/9050 and 7800/7800, TCP to/from ESP avg 74.7/84.4 Mbit/s, UDP 1392869/1392869 datagrams, stop/start 78/78, reinstall 26/26, heap trend −39 B/h. 3 transfer errors (2 bulk IN, 1 bulk OUT) recovered without re-enumeration |
| Soak, stock IDF v5.5.5 (4 h, same rounds) | 0 panics, 0 transfer errors. Pings 8400/8400 and 7200/7200, TCP to/from ESP avg 66.1/92.9 Mbit/s, UDP 1285727/1285727 datagrams, stop/start 72/72, reinstall 24/24, heap trend −39 B/h |
| Soak, stock IDF | see the next section: about 1 crash per million bulk-IN transfers on v5.4.2, 0 in 12.8M on stock v5.5.5 |

Bugs found on hardware and fixed:

- `esp_eth_start` blocked for 4 s with no dongle attached (the generic PHY
  driver polled for autoneg completion against the virtual PHY). The virtual
  BMSR now reports autoneg complete.
- The example freed all USB devices on `NO_CLIENTS`, which in IDF 5.4 disables
  the port until a physical replug, so a reinstalled driver could not bind the
  dongle (see Task priorities).
- On unplug, RX/TX recovery kept resubmitting into a pipe that could no longer
  be cleared, producing an error burst. A failed pipe clear now re-binds
  (tears the device down). *This fix is not yet re-verified with a physical
  unplug.*

### RTL8153B on the Elecrow CrowPanel Advanced 5" (ESP32-P4)

A PoE-powered USB-C adapter with an RTL8153B (`0bda:8153`, bcdDevice 0x3100, "USB
10/100/1000 LAN", PLA_TCR1 version 0x6010) on the Elecrow's HS port J16, with the
board and settings below, stock ESP-IDF v5.5.5, `sdkconfig.perf`, firmware
`0c73f0f` (32 KiB RX transfers, the default at the time). The link partner is the
lab PC's RTL8152 (100 Mbit/s), so the link is 100M full duplex; the RTL8153B
advertises 1000BASE-T full duplex (GBCR 0x0200) and the partner none (GBSR 0).

| Test | Result |
|---|---|
| Enumeration | `RTL8153B (0bda:8153, rtl815x 0x6010) attached at high speed, bulk MPS in/out 512/512` about 0.8 s after boot; the vendor configuration (1 of 2; the other is CDC-ECM) |
| MAC | from `PLA_BACKUP` (the adapter's factory address) |
| Link / DHCP | 100M full duplex at 3.4 s, lease at 7.9 s, 5/5 gateway pings |
| Ping both ways (1400 B at 5/s, 1000 B at 10/s) | 1400/1400 and 1200/1200, RTT 1.57 ms (2.8 ms with the RTL8152) |
| TCP (`sdkconfig.perf`) | to ESP 59–62 Mbit/s, from ESP 91–95 Mbit/s |
| UDP to ESP, 40 Mbit/s (1400 B) | 214285/214285 datagrams |
| `esp_eth_stop`/`start` | 19/19 |
| uninstall + reinstall (`usb_eth_uninstall`/`usb_eth_install`) | 5/5, re-attach and same MAC each time |
| Soak (25 min, 3 rounds as above) | 0 panics, 0 transfer errors or recoveries, `rx_dropped`/`tx_errors` 0, 416k bulk-IN transfers |
| Heap | 343.8 KB free after boot, 343.13 KB from the second round on (with 4 × 32 KiB RX transfers) |
| RX transfer size | 16 KiB: TCP to ESP 58.7–61.8 Mbit/s (avg 59.0, 16 × 20 s bursts, 1.6M transfers, 0 errors), the same as 32 KiB; free heap 409.1 KB. The default is 16 KiB since. |

TCP towards the ESP is about 10% below the RTL8152 on the same board (65–69
Mbit/s); the transfer size is not the cause. Not investigated further.

### RTL8152 regression after the usb_eth refactor

The same short test as above (functional pass + 25 min mixed soak) with the
RTL8152 (0x4c10) on the usb_eth core, stock ESP-IDF v5.5.5. The lab PC's
adapter is now an RTL8153 instead of the RTL8152, which explains the lower RTT.

| | Elecrow P4, HS (`9014a27`) | Elecrow P4, HS, before (t-0064) | S3-RGB-Matrix, FS (`a4dde64`) | S3 DevKitC, FS, before (t-0062) |
|---|---|---|---|---|
| Attach | HS, MPS 512, RX 16384, MAC from `PLA_BACKUP` | same | FS, MPS 64, RX 4096 | same |
| Pings both ways | 1400/1400, 1200/1200 | 1400/1400, 1200/1200 | 1400/1400, 1200/1200 | 1400/1400, 1200/1200 |
| RTT | 1.6 ms | 2.8 ms | 4.2 ms | 5.5 ms |
| TCP to / from ESP (Mbit/s) | 60–71 / 92–93 | 65–69 / 94.8 | 8.6 / 8.1 | 8.6 / 8.0 |
| UDP | 40 Mbit/s 214288/214288 | 40 Mbit/s lossless | 8 Mbit/s 42860/42860 | 8 Mbit/s lossless |
| stop/start, reinstall | 19/19, 5/5 | 19/19, 5/5 | 19/19, 5/5 | 19/19, 5/5 |
| Errors | 0 panics, 0 recoveries, `rx_dropped` 0 | 0 | 0 panics, 0 recoveries; `rx_dropped` 2 once (fixed in `9014a27`, below) | 0 |
| Free heap, idle | 390.8 KB, flat | 408.7 KB, flat | internal ~255 KB, flat | 263 KB, flat |

The heap difference on the Elecrow is the example's console UART RX buffer
(16 KiB instead of 512 B, room for `uart_ota` frames): with 512 B the same build
shows 407.4 KB, so the usb_eth core itself costs about 1.3 KB. On the S3 the
first bulk-IN transfer after a driver reinstall was in 2 of 5 cases the tail of
a frame cut at the previous teardown, as with the RTL8153B; since `9014a27` every
attach uses the parser's restart rule (0 such drops in the Elecrow run).

### RTL8153B on the Waveshare ESP32-S3-RGB-Matrix (USB full speed)

ESP32-S3-WROOM-2-N32R16V (QFN56 rev v0.2, 32 MB octal flash, 16 MB 1.8 V octal
PSRAM), stock ESP-IDF v5.5.5, `boards/waveshare_s3_rgb_matrix.defaults` +
`sdkconfig.perf`. The same PoE-powered RTL8153B adapter on the native USB-C
(GPIO19/20); the console and all updates on a CP2102N on GPIO45/46
([`extras/uart_ota`](../extras/uart_ota/README.md)). Firmware `49d6fd0`
(functional pass + soak), drop fix verified on `a4dde64`.

| Test | Result |
|---|---|
| Enumeration | `RTL8153B (0bda:8153, rtl815x 0x6010) attached at full speed, bulk MPS in/out 64/64, RX transfer 4096 bytes` about 1.75 s after boot |
| MAC / link / DHCP | from `PLA_BACKUP`; 100M full duplex; lease about 5.8 s after boot |
| Ping both ways (1400 B at 5/s, 1000 B at 10/s) | 1400/1400 and 1200/1200, RTT 4.1 ms |
| TCP (`sdkconfig.perf`) | to ESP 8.8 Mbit/s, from ESP 8.0 Mbit/s (every run) |
| UDP to ESP, 8 Mbit/s (1400 B) | 42860/42860 datagrams |
| `esp_eth_stop`/`start` | 19/19 |
| uninstall + reinstall | 5/5 (+ 40 more while debugging the item below) |
| Soak (25 min, 3 rounds, UDP at 8 Mbit/s) | 0 panics, 0 watchdog, 0 transfer errors or recoveries, `rx_dropped`/`tx_errors` 0 |
| Heap | internal 288.6 KB free after boot, about 255 KB while running (lowest 247.8 KB); PSRAM 16 MB, practically unused. The full-speed defaults (3 x 4 KiB RX, 4 TX) take about 18 KB of internal DMA memory and already reach the FS ceiling |
| Re-attach after a reinstall | before `a4dde64`: in 10 of 20 reinstalls the first bulk-IN transfer was the tail of a frame cut at the previous teardown (the chip keeps its link), counted as `rx_dropped` 2; since then parsed with the restart rule: 0 in 20. The RTL8152 on this board showed the same in 2 of 5 reinstalls, so the rule applies to every chip since the next commit |

### Elecrow CrowPanel Advanced 5" (ESP32-P4)

ESP32-P4 rev v1.3, 16 MB flash (GigaDevice), PSRAM not enabled, stock ESP-IDF
v5.5.5 (no patch, no shim), `sdkconfig.perf`, the board defaults in
`examples/basic/boards/`. The same RTL8152B dongle on the HS USB-C port J16
through a powered USB-C OTG adapter (its VBUS also feeds the board through
D007), cabled to the same Linux PC. The display is not initialised by the example.

| Test | Result |
|---|---|
| Enumeration | `attached at high speed, bulk MPS in/out 512/512, RX transfer 16384 bytes` about 1.15 s after boot, MAC c8:4d:44:35:8f:72 from `PLA_BACKUP` |
| Link / DHCP | 100M full duplex, lease about 4.4 s after boot, 5/5 gateway pings |
| Ping both ways (1400 B at 5/s, 1000 B at 10/s) | 1400/1400 and 1200/1200, RTT 2.8 ms |
| TCP (`sdkconfig.perf`) | to ESP 65–69 Mbit/s, from ESP 94.8 Mbit/s |
| UDP to ESP, 40 Mbit/s (1400 B) | 214288/214288 datagrams |
| `esp_eth_stop`/`start` | 19/19 |
| uninstall + reinstall | 5/5 |
| Soak (25 min, 3 rounds as in the Waveshare soak) | 0 panics, 0 transfer errors or recoveries, `rx_dropped`/`tx_errors` 0, no brown-out or unexpected reset |
| Heap | 409.4 KB free after boot, 408.7 KB from the second round on, as on the Waveshare board |

Results match the Waveshare board on the same IDF version.

### ESP32-S3 (USB full speed)

ESP32-S3-DevKitC-1 (ESP32-S3 QFN56 rev v0.1, 8 MB flash, no PSRAM), stock
ESP-IDF v5.5.5, `sdkconfig.perf`, Kconfig defaults for the S3 (3 × 4096-byte RX
transfers, 4 TX transfers). The same RTL8152B dongle on the native USB port
through a powered OTG adapter, cabled to the same Linux PC.

| Test | Result |
|---|---|
| Enumeration | `attached at full speed, bulk MPS in/out 64/64, RX transfer 4096 bytes` about 1.15 s after boot |
| Link / DHCP | 100M full duplex, lease about 4.1 s after boot, 5/5 gateway pings |
| MAC | from `PLA_BACKUP`: the dongle's own address, also on the first boot after a run that had left the ESP's MAC in `PLA_IDR`, and after a warm ESP reset with the dongle still powered (before that change the stale `PLA_IDR` value was used) |
| Ping both ways (1400 B at 5/s, 1000 B at 10/s) | 1400/1400 and 1200/1200, RTT 5.5 ms (2.8 ms on the P4) |
| TCP (`sdkconfig.perf`) | to ESP 8.6 Mbit/s, from ESP 8.0 Mbit/s (every run) |
| UDP to ESP, 8 Mbit/s (1400 B) | 42860/42860 datagrams |
| `esp_eth_stop`/`start` | 19/19 |
| uninstall + reinstall | 5/5 |
| Soak (25 min, 3 rounds as in the P4 soak, UDP at 8 Mbit/s) | 0 panics, 0 transfer errors or recoveries, `rx_dropped`/`tx_errors` 0 |
| Heap | 263.9 KB free after boot, 263.25 KB from the second round on (unchanged over the last two rounds) |

The ESP-IDF BNA issue below is specific to the P4; the S3 needs no patch or shim.

## Known ESP-IDF issue: USB host assert under load (ESP32-P4 rev < 3.0)

On the ESP32-P4 (rev v1.3) with IDF v5.4.2 the firmware occasionally panics
during sustained TCP traffic towards the ESP:

```
assert failed: usb_dwc_hal_chan_decode_intr /IDF/components/hal/usb_dwc_hal.c:513 (chan_intrs & USB_DWC_LL_INTR_CHAN_CHHLTD)
```

The assert fires in the HCD interrupt handler (`intr_hdlr_main` →
`_intr_hdlr_chan`), with no driver code on the stack. The chip reboots and
recovers in about 1.5 s (re-enumeration, new lease, traffic resumes).

**Rate.** About 1 per million bulk-IN transfers (8 in 6.3M, 0.013 per 10k),
the same with 1, 2 or 4 RX transfers in flight, i.e. one crash every 10–20
minutes of continuous 70 Mbit/s receive. Moving USB DMA memory to PSRAM does not
help: at 200 MHz it crashed 3.5× more often, at 20 MHz throughput drops to
15 Mbit/s.

**Cause: a lost cache write-back on the P4, fixed in IDF v5.5.5 / v6.0.2.**
The USB controller raises BNA (descriptor not active) on a bulk-IN channel.
A diagnostic IDF build that dumps the failing descriptor showed, every time,
the *previous, completed* descriptor in memory (including the old buffer
pointer, which the controller never writes) while the CPU cache held the new
one, written and synced to memory (`esp_cache_msync` C2M) 0.3–1.2 ms earlier.
Adding a second cache operation right after that sync made the error disappear
(0 in 6.29M transfers). Espressif fixed this in esp_rom
([esp-idf MR !48498](https://github.com/espressif/esp-idf/commit/751a75a982):
"avoid critical issue in writeback"): the ROM `Cache_WriteBack_*` functions are
replaced by IRAM versions that run the sync twice. The fix is in **v5.5.5**,
**v6.0.2** and later and on the release/v5.4 branch, but in **no v5.4.x tag**
(v5.4.4 predates it).

BNA does not halt the channel, which is why IDF 5.4/5.5 trip the HAL assert;
with the assert removed (esp-idf master 63b468539f) the HCD `abort()`s in
`_buffer_parse_error()` instead, because it has no case for BNA. Reported
upstream in [espressif/esp-idf#19111](https://github.com/espressif/esp-idf/issues/19111).

**What to do** (in order of preference):

1. **Use ESP-IDF v5.5.5 or later** (or v6.0.2+). No patch needed. The driver
   builds on 5.5 (the board defaults select P4 rev < 3.0, which IDF 5.5
   requires for v1.x chips).
   Verified: 0 crashes in 12.8M bulk-IN transfers (2 h of bursts, where
   v5.4.2 would expect 10–17), and a 4 h mixed soak (table above) with 0
   panics and 0 transfer errors.
2. **On an older IDF, add [`extras/p4_cache_wb_fix`](../extras/p4_cache_wb_fix/README.md)**
   to the project: Espressif's fix as an optional component that redirects the
   ROM calls with `-Wl,--wrap`, without modifying IDF.
   Verified on stock v5.4.2: 0 crashes in 13.1M bulk-IN transfers (2 h of
   bursts, avg 67.8 Mbit/s, where 10–17 would be expected without it).
3. **Patch IDF**: [`patches/esp-idf-v5.4-usb_dwc-bna-error-handling-v2.patch`](../patches/esp-idf-v5.4-usb_dwc-bna-error-handling-v2.patch)
   (`git apply` in the IDF root) does not prevent the fault but makes it
   recoverable. The HAL halts the channel on an error interrupt that arrives
   before the halt and reports the error then (as Linux dwc2 does), and the HCD
   completes a BNA'd transfer with `USB_TRANSFER_STATUS_ERROR`. The driver's
   `rx_recover` / `tx_recover` halts, flushes and clears the pipe and restarts
   it; TCP retransmits what was lost. Verified in the 4.2 h soak above (3
   recoveries, 0 panics) and in 8 BNA recoveries of the diagnostic runs. The
   older assert-only backport (`...-error-without-halt.patch`) is kept for
   reference only. A patched IDF still benefits from 1 or 2.

| Run (P4 rev v1.3, burst mode: back-to-back 20 s TCP-to-ESP bursts) | bulk-IN transfers | crashes / BNA | per 10k | TCP to ESP |
|---|---|---|---|---|
| v5.4.2 stock, 4 RX transfers (2.7 h mixed soak, one burst per round) | – | 2 in 17 bursts | – | 75 Mbit/s |
| v5.4.2 stock, 2 RX transfers | ~3.3M (est.) | 3 | ~0.009 | 69 Mbit/s |
| v5.4.2 stock, 1 RX transfer | ~3.8M (est.) | 3 | ~0.008 | 83 Mbit/s |
| v5.4.2 + v2 patch + descriptor dump | ~6.3M | 8 BNA, all recovered | 0.013 | 68 Mbit/s |
| same + second cache op after the descriptor sync | 6,288,599 | 0 | 0 | 65 Mbit/s |
| v5.4.2 stock, USB DMA in PSRAM 20 MHz | 780,005 | 0 (too few to tell) | 0 | 15 Mbit/s |
| v5.4.2 stock, USB DMA in PSRAM 200 MHz | 645,652 | 3 | 0.046 | 62 Mbit/s |
| **v5.5.5 stock** (no patch, 4 RX transfers, internal RAM) | 12,782,016 | **0** | 0 (95% bound < 0.0023) | 66 Mbit/s |

## What is still unverified

- **ESP32-S3 / USB full speed**: only a short validation (about 35 min, stock
  v5.5.5, see above). Not run: a long soak, IDF v5.4.x, hot-plug, and other
  RX transfer sizes or counts. No PHY-poll timeouts were logged under FS bulk load.
- **Elecrow CrowPanel P4**: only a short validation (about 35 min, see above);
  not run with the display and backlight on, a long soak, or hot-plug.
- **Unplug-burst fix** (re-bind when a pipe clear fails) with a physical unplug.
  The hot-plug tests above ran before it.
- **10 Mbit and half-duplex peers**; only 100/full has been negotiated.
- **RTL8153B**: short validations on the Elecrow (P4, HS) and the
  S3-RGB-Matrix (S3, FS) only (see above). Not run: a gigabit link partner (the lab
  peer is 100M; the PHY advertises 1000BASE-T FD and the partner ability reads 0),
  hot-plug, a long soak, and the RTL8153B on IDF 5.4.x.
- **Multicast filtering**: `USB_ETH_CMD_ADD_MCAST`, `ACCEPT_ALL_MULTICAST=n`
  and `ETH_CMD_S_PROMISCUOUS` (broadcast/ARP and unicast work).
- **STALL recovery**: no STALL occurred during testing, so `stall_recover`
  (re-claim for DATA0) has not been observed. The non-STALL error path
  (halt, flush, clear, resubmit) ran 11 times with the patched IDF (above).
- **Zombie paths**: teardown or RX stop when transfers do not return within 2 s,
  and the leak-on-deinit case. These need a misbehaving device.
- **CDC-ECM-first adapters**: the tested adapters enumerate the vendor
  configuration (IDF logs that they have more than one configuration). Selecting a
  configuration via the enum filter callback is not implemented, and there is no
  class driver yet (see ARCHITECTURE.md).
- Long hot-plug runs (the checklist's 50×); only 3 physical replugs were done.
- Stale `DEV_GONE` drain ordering and the 20 ms settle before
  `usb_host_device_close()` (see Task priorities): reviewed against the IDF
  sources, not provoked.

## Next steps

- The unverified items above.
- Optional interrupt-endpoint link notification instead of polling.
- RX/TX checksum offload and VLAN tags (descriptor bits are known from FreeBSD).
- Enumeration filter helper to force the vendor configuration.
- Batch several TX frames per bulk transfer (the descriptor format allows it).
- A host-side harness with fake FreeRTOS/USB-host shims to unit test the
  ownership state machine in `core/usb_eth_core.c` (attach/unplug/stop races) under ASan.
- More drivers: RTL8153A/RTL8156 in rtl815x (a variant table row and an ops
  struct each), AX88179, CDC-ECM/NCM (see ARCHITECTURE.md for what the core still needs).

## Sources and licensing

The component is BSD-2-Clause. Register definitions (`drivers/rtl815x/rtl815x_regs.h`)
and the chip initialisation sequences in `drivers/rtl815x/rtl815x.c` are derived from
the FreeBSD `ure(4)` driver, `sys/dev/usb/net/if_ure.c` and `if_urereg.h`
(Copyright (c) 2015-2016 Kevin Lo, BSD-2-Clause). The RTL8153B parts were
cross-checked against OpenBSD `ure(4)`, `sys/dev/usb/if_ure.c` and `if_urereg.h`
(Copyright (c) 2015, 2016, 2019 Kevin Lo, (c) 2020 Jonathon Fletcher,
BSD-2-Clause); where they differ the code says which one it follows. Both notices
are kept in those files and in `LICENSE`. The Linux `r8152.c` driver (GPL-2.0) was **not** used as a
source. The RX/TX descriptor layouts follow FreeBSD's `struct ure_rxpkt` /
`struct ure_txpkt`.
