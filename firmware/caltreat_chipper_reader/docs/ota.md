# Network and firmware updates

The reader joins the LAN through a USB Ethernet adapter (RTL8152 / RTL8153B,
`shared_esp_components/usb_eth`) on the CrowPanel's USB host port. It takes
updates pushed over HTTP (`shared_esp_components/net_ota`) and checks that it
can reach the backend.

## Network behaviour (`src/net.c`)

- **Hostname:** `chipper-reader-<factory MAC>.local`, for example `chipper-reader-a1b2c3d4e5f6.local`. The same name is used for mDNS and DHCP. The 12 hex digits are also the device ID.
- **Bring-up runs in a background task.** The reader works without an adapter. The task retries the USB host and Ethernet driver install with backoff (5 s → 60 s).
- **Hot-plug:** unplugging and replugging the adapter or the cable recovers on its own. If there's no address for `CHIPPER_NET_REINIT_S` (300 s), the Ethernet driver is reinstalled. That covers adapters that failed to initialise.
- **Backend:** `GET <CHIPPER_BACKEND_URL>/api/health/` over HTTPS every `CHIPPER_BACKEND_PROBE_S` (30 s), backing off to 60 s while it fails. It reports `unreachable` after 2 consecutive failures, since one timeout can just be a Cloud Run cold start.
- **Status:** the serial `net` command prints the adapter state, IP, backend state, hostname and device ID.

Settings are under `menuconfig` → *Chipper reader network*.

## One-time migration to the OTA partition table

Readers flashed before OTA support have a single `factory` app partition. The
new table (`partitions.csv`: `otadata`, `ota_0`, `ota_1`) overlaps the old
layout, so erase once over USB serial:

```sh
pio run -t erase && pio run -t upload
```

This clears NVS too. The reader stores nothing there; card accounts live in RAM
and reset on every reboot anyway. After this, use network updates.

## Pushing an update

```sh
pio run                                                    # builds .pio/build/crowpanel_p4/firmware.bin
dns-sd -B _http._tcp                                       # find readers (Ctrl-C to stop)
tools/ota_push.sh chipper-reader-a1b2c3d4e5f6.local        # or an IP address
curl http://chipper-reader-a1b2c3d4e5f6.local/ota          # version, partition, state
```

- The screen shows "UPDATING FIRMWARE" while the image is written. The reader then restarts into the other slot.
- An update is refused with `503` while a tap result is on screen. Retry a few seconds later.
- Images from another project or chip are refused with `400` before anything is written.
- **Rollback:** the new image confirms itself once it gets an IP address. If it doesn't within 120 s (crash loop, broken network code, or no adapter or cable at that moment), the reader restarts into the previous image. Keep the network connected while updating.
- The serial `ota` command shows the running version, slot and state (`pending_verify` until confirmed, then `valid`).
- There's no authentication. Anyone on the LAN can push an image of this project.
