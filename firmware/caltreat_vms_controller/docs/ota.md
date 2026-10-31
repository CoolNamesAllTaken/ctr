# Network and firmware updates

The VMS joins the LAN through a USB Ethernet adapter (RTL8152 / RTL8153B,
`shared_esp_components/usb_eth`) on the board's USB-C port. It takes updates
pushed over HTTP (`shared_esp_components/net_ota`) and checks that it can reach
the backend. The network code in `src/net.c` is the chipper reader's
(`caltreat_chipper_reader/docs/ota.md`), with VMS names.

## Hardware notes

- **One USB-C port, two jobs.** The S3's only USB PHY (GPIO19/20) serves both
  the USB-Serial-JTAG port and USB host. Once the firmware starts USB host for
  the adapter, the board no longer shows up as a serial port on a computer.
- **The adapter needs 5 V from elsewhere.** The board doesn't power VBUS in
  host mode. Use a powered OTG Y-cable or a powered hub (about 200 mA).
- **Console on UART0:** TX GPIO43, RX GPIO44, 115200 baud. Use a 3.3 V USB-UART
  adapter for logs and the console keys (`i` prints network, feed and firmware info).

## Network behaviour (`src/net.c`)

- **Hostname:** `caltreat-vms-<factory MAC>.local`, used for mDNS and DHCP. The 12 hex digits are also the device ID.
- **Bring-up runs in a background task.** The sign works without an adapter. The task retries the USB host and Ethernet driver install with backoff (5 s → 60 s).
- **Hot-plug:** unplugging and replugging the adapter or the cable recovers on its own. If there's no address for `VMS_NET_REINIT_S` (300 s), the Ethernet driver is reinstalled.
- **Backend:** `GET <VMS_BACKEND_URL>/api/health/` over HTTPS every `VMS_BACKEND_PROBE_S` (30 s), backing off to 60 s while it fails. It reports `unreachable` after 2 consecutive failures.

Settings are under `menuconfig` → *CalTreat VMS network* and *Network OTA (net_ota)*.

## Flashing over USB

Use this for the first install and whenever a network update isn't possible.

1. Unplug the Ethernet adapter and plug the board's USB-C port into the computer.
2. Put the board in download mode: hold **BOOT**, tap **RESET**, release BOOT.
   The running firmware has the USB port in host mode, so PlatformIO can't
   reset the board into the bootloader by itself.
3. Upload with PlatformIO's Upload button, or `pio run -t upload`.
4. After the upload the board restarts into the new firmware and the USB serial port goes away.
   That's expected.

A USB upload also writes a blank `otadata`, so the board always boots the
uploaded image from `ota_0`, whichever slot the last network update used. It's
not on rollback watch, so it's fine to flash with the adapter unplugged. No
erase is needed to switch from the old Arduino layout; `pio run -t erase` is
there if you want a clean flash (it also clears NVS, where the sign stores
nothing).

## Pushing an update over the network

```sh
pio run                                              # builds .pio/build/waveshare_s3_rgb_matrix/firmware.bin
dns-sd -B _http._tcp                                 # find signs (Ctrl-C to stop)
tools/ota_push.sh caltreat-vms-a1b2c3d4e5f6.local    # or an IP address
curl http://caltreat-vms-a1b2c3d4e5f6.local/ota      # version, partition, state
```

- The sign shows `UPDATING` while the image is written, then restarts into the other slot.
- Images from another project or chip are refused with `400` before anything is written.
- **Rollback:** the new image confirms itself once it gets an IP address. If it doesn't within 120 s, the sign restarts into the previous image. Keep the adapter and cable connected while updating.
- There's no authentication. Anyone on the LAN can push an image of this project.

## Build note

`sdkconfig.defaults` only applies to settings that are not in the generated
`sdkconfig.waveshare_s3_rgb_matrix` yet. After changing defaults, delete that
file and rebuild.
