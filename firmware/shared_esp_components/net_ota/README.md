# net_ota: push firmware updates over HTTP

Small ESP-IDF component for updating a device over the LAN. You POST an app
image to the device. It writes the image into the next OTA slot and restarts.
On its first boot the new image must confirm itself, or the bootloader rolls
back to the previous image.

It uses only stock IDF pieces (`esp_http_server`, `esp_ota_ops`, bootloader
rollback) plus `espressif/mdns`. It doesn't depend on the network transport;
the application brings the network up (usb_eth, Wi-Fi, ...).

| | |
|---|---|
| Targets | ESP32-S3, ESP32-P4 |
| ESP-IDF | 5.4.x, 5.5.x |

## Endpoints

| Request | Result |
|---|---|
| `POST /ota` with the `.bin` as body | `200` and a restart. `400` for a wrong chip or project, a truncated upload or a failed validation. `411`/`413` for a missing or oversized length. `503` while another update is running or the app vetoes |
| `GET /ota` | `{"project","version","idf","partition","state","hostname","busy"}` |

The handler reads the image header and app description **before erasing
anything**. It rejects images built for another chip or with a different
`project_name` than the running app. Flash is erased sector by sector as data
arrives (`OTA_WITH_SEQUENTIAL_WRITES`).

```sh
curl --fail --data-binary @build/app.bin http://<device>.local/ota
dns-sd -B _http._tcp          # list devices (macOS); TXT: project, version, path
```

## What the application must provide

1. **A partition table with `otadata` and two app slots** (`ota_0`, `ota_1`).
2. **`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`.** Without it, the component builds with a `#warning` and a bad image can't roll back.
3. **Network and, optionally, mDNS:** call `mdns_init()` and `mdns_hostname_set()` before `net_ota_start()`. The component then adds an `_http._tcp` service.
4. **A self-test that calls `net_ota_mark_valid()`.** A new image boots as `pending_verify`. If it hasn't confirmed within `CONFIG_NET_OTA_CONFIRM_TIMEOUT_S` (120 s), net_ota restarts the chip and the bootloader goes back to the previous image. Confirm once the image has shown it can take the *next* update, for example after it gets an IP. Don't make confirmation depend on external services.

```c
net_ota_config_t cfg = NET_OTA_DEFAULT_CONFIG();
cfg.can_update = app_is_idle;     // optional: false -> 503
cfg.on_begin = show_updating;     // optional
cfg.on_end = hide_updating;       // optional, ok=false on failure
ESP_ERROR_CHECK(net_ota_start(&cfg));
```

Pass `cfg.server` to register on an existing `httpd` instead of starting one. Set `cfg.port` to that server's port, since it is advertised over mDNS.

## Configuration (`menuconfig` → *Network OTA (net_ota)*)

| Option | Default | Meaning |
|---|---|---|
| `NET_OTA_PORT` | 80 | Port of the server net_ota starts |
| `NET_OTA_MAX_SOCKETS` | 2 | Its `max_open_sockets`; httpd reserves 3 more, so keep this small to leave lwIP sockets for the app |
| `NET_OTA_BUF_SIZE` | 4096 | Receive buffer (heap) |
| `NET_OTA_CONFIRM_TIMEOUT_S` | 120 | Rollback guard |

## Security

There is no authentication: anyone who can reach the port can install an image
of the same project. Use it only on networks you trust.
