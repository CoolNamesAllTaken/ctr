# lcctl: lighting controller host tools

Python tools for the `caltreat_lighting_controller` boards (Waveshare RP2350-ETH).
Each board is a **Modbus TCP server on port 502** with a DHCP address, and
serves one client at a time. The register map is in
[`src/regmap.h`](../src/regmap.h), mirrored in
[`src/lcctl/regmap.py`](src/lcctl/regmap.py).

```sh
cd firmware/caltreat_lighting_controller/tools
uv sync
uv run lcctl discover            # boards found through DHCP leases
uv run lcctl info                # status + identification (the only board, or -H <ip>)
uv run lcctl pins
uv run lcctl adc
uv run lcctl heartbeat 3000 && uv run lcctl save
uv run lcctl ota ../.pio/build/app/firmware.bin
```

Each command opens its own connection. The board returns its pins to their
failsafe defaults whenever a connection closes, so a pin change made by a
one-shot command is undone as soon as the command exits. To work with pins by
hand, use the shell instead. It keeps one connection open and pauses the
heartbeat until you quit:

```
$ uv run lcctl shell
lcctl> set 2 output --level 1
lcctl> out 2 0
lcctl> set 4 pwm --duty 0 --freq 200
lcctl> pwm 4 65535 --fade-ms 2000
lcctl> quit
```

From Python:

```python
from lcctl.client import Board, PinConfig
from lcctl import regmap as rm

with Board("192.168.2.3") as b:
    b.configure(4, PinConfig(func=rm.FUNC_PWM, freq_hz=200))
    b.set_pwm(4, 32768, fade_ms=500)
    count, last_ms = b.edge_count(6)
```

## Network

**Bench (Mac):** turn on *System Settings › General › Sharing › Internet
Sharing*, sharing Wi-Fi to the USB LAN adapter the board is plugged into.
macOS then runs a DHCP server on 192.168.2.0/24. The board registers as
`CH9120`, and `lcctl discover` finds it in `/var/db/dhcpd_leases`.

**Deployment (Raspberry Pi 4B + unmanaged switch):** give the Pi a static
address on `eth0`, and let dnsmasq hand out fixed addresses by MAC (the MAC is
shown by `lcctl info`):

```
# /etc/dnsmasq.d/lighting.conf
interface=eth0
dhcp-range=192.168.50.100,192.168.50.200,12h
dhcp-host=0c:3d:5e:d9:23:e7,192.168.50.11   # board A
dhcp-host=xx:xx:xx:xx:xx:xx,192.168.50.12   # board B
```

`lcctl discover` also reads `/var/lib/misc/dnsmasq.leases`.

## Host responsibilities

- **Poll faster than the heartbeat.** Any valid request feeds it (default 3000 ms,
  `0` = off). If the host goes quiet longer than that, or disconnects, every pin
  goes to its failsafe default, except pins flagged `FS_HOLD`.
- **Failsafe ends at the next write, not the next read.** Pins stay at their
  defaults until the host writes again. Re-send the full output state when
  `info()["failsafe"]` is set. `failsafe_reason` says why (`boot`, `heartbeat` or
  `disconnect`).
- **Keep one connection per board.** The CH9120 accepts a second client, but that
  client never gets data, and when it closes the board briefly sees a disconnect,
  which triggers failsafe.
- **Poll the edge counters for inputs.** Inputs aren't pushed to the host. Enable
  counting per pin (`set_edges`, with debounce), then poll `edge_count(gpio)`.
  The counter wraps; compare it with the last value you read.
- **Expect a reset after long silence.** A connection with no requests for
  heartbeat + 5 s (60 s with the heartbeat off) is treated as half-open: the board
  resets its CH9120 so a new connection can be made.
- **Expect brief gaps while no client is connected.** The CH9120 only asks for a
  DHCP lease coming out of reset, so with no client the board resets it to ask
  again: after 30 s, then every 60 s. The board can't be reached for
  a moment around each reset (the reset itself plus the DHCP exchange); a
  connection attempt that lands there fails and should be retried. Connecting or
  disconnecting starts the 30 s interval over.

Failsafe and power-on defaults are pin blocks at `HR_DEFAULT_BASE`. They are
persisted together with the heartbeat by `save()` (`CMD_SAVE`).

## LED

| Colour | Meaning |
|---|---|
| White | No client connected |
| Green | Host connected and in control |
| Red | Failsafe: pins at their defaults (after boot, a heartbeat timeout or a disconnect) |
| Yellow | Receiving a firmware update |

## Firmware updates

The flash holds two app partitions (A/B). The RP2350 boot ROM boots the newer
one and enforces the update rules:

1. `lcctl ota firmware.bin` sends the image to the partition the board is not
   running from (about 3 s for 50 KB) and checks its SHA-256 on the board.
2. The board reboots into the new image **on trial**. Trial ends when the image
   **buys** itself, which happens on the first request after the host reconnects.
   `lcctl ota` does this for you.
3. The trial image rolls back to the previous image if it doesn't buy itself
   within 60 s, crashes, is rebooted, or loses power first.

Versions come from `custom_fw_version` in `platformio.ini`, which the build
seals into the image. `LC_FW_VERSION=X.Y.Z pio run -e app` overrides it.

- **Older versions:** refused unless `--allow-downgrade` is given (revert). A
  bought revert keeps booting even though the other partition held a newer
  image.
- **Same version as running:** always refused. The ROM can't choose between
  two equal versions, so give development builds a patch bump.

**Installing over USB** (new board, or recovery): use PlatformIO's Upload
(`pio run -e app -t upload`). It puts a running board into BOOTSEL, writes the
partition table and loads the firmware. A blank RP2350 is already in BOOTSEL;
if a board doesn't respond, hold BOOT while plugging in USB. A USB install
replaces the firmware completely: it doesn't keep the previous image as a
fallback the way a network update does.

## Tests

The hardware-in-the-loop tests need a board on the network, with header pins
GP0–GP9 left unconnected. No wiring is needed: the tests read back pad levels
and toggle the internal pulls to create input edges.

```sh
uv run pytest tests/hil                   # ~30 s, 34 tests
uv run pytest tests/hil -m ota            # ~4 min: builds images, updates, rolls back
uv run pytest tests/hil --ip 192.168.2.3  # skip discovery
```

The OTA tests also need PlatformIO and the board's USB connection, which they use
to simulate power cycles through BOOTSEL.

Still to check by hand: pull the Ethernet cable while the host is connected.
The LED should go red (failsafe) and then white. After plugging the cable back
in, the host should reconnect within heartbeat + 5 s, once the CH9120 drops the
half-open connection.

## Notes

- `IR_IP` (register 20) holds what the CH9120 reported at boot, which is
  usually the previous DHCP lease. The address you connected to is the real
  one.
- `picotool reboot -f` (without `-u`) does nothing on a running board. To
  reboot it, use `lcctl reboot`, or `picotool reboot -f -u` followed by
  `picotool reboot`.
