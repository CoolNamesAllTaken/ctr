# RC522 → USB NFC bridge (PN532 emulation)

Firmware for a **Waveshare RP2040-Zero** that bridges an **RFID-RC522 (MFRC522)**
module to USB. The RP2040 presents itself as an **NXP PN532 UART reader** over a
USB CDC serial port, so existing NFC software (libnfc, nfcpy,
adafruit-circuitpython-pn532, NFCToolsGUI) can drive it with no custom host stack.

**Scope:** ISO14443A UID detection and **MIFARE Classic 1K/4K** read/write/value
operations with host-supplied keys, plus raw ISO14443A frames (Ultralight/NTAG
commands, `InCommunicateThru`). The RC522 is ISO14443A @ 106 kbps only; other tag
families (14443B, FeliCa) are out of scope. ISO14443-4 cards (DESFire, bank
cards, phones) are detected and their ATS reported, but APDU exchange (ISO-DEP
I-block framing) isn't implemented: `InDataExchange` to them returns status
`0x27`.

## PN532 commands

| Command | Notes |
|---------|-------|
| `Diagnose`, `GetFirmwareVersion`, `GetGeneralStatus` | Firmware reports PN532 v1.6, ISO14443A only |
| `SAMConfiguration`, `SetParameters`, `SetSerialBaudRate`, `PowerDown` | Accepted; `SetParameters` auto-RATS flag is honoured |
| `RFConfiguration` | RF field on/off and `MaxRetries` (passive activation retry count) |
| `ReadRegister`, `WriteRegister`, `ReadGPIO`, `WriteGPIO` | Register shadow; CIU `TxMode`/`RxMode` CRC bits and `BitFraming` TxLastBits shape `InCommunicateThru`, `Status2` reflects/clears crypto1 |
| `InListPassiveTarget`, `InAutoPoll` | 106 kbps type A; waits for a card per `MaxRetries` (default: forever) until the host aborts by sending anything |
| `InDataExchange` | MIFARE auth/read/write/increment/decrement/restore/transfer; other commands sent raw with CRC checked and stripped |
| `InCommunicateThru` | Raw frames, including 7-bit short frames; parity can't be disabled |
| `InSelect`, `InDeselect`, `InRelease` | |
| anything else | PN532 syntax-error frame |

Invalid frames are ignored (the host times out and retries), a host NACK
resends the last response, and a frame that stalls partway, or a port
close/reopen, resynchronises the parser.

## Status (verified on hardware)

| Capability | Status |
|------------|--------|
| USB enumeration (1× CDC + reset interface) | ✅ |
| 1200-baud reset into BOOTSEL (`pio run -t upload`) | ✅ |
| PN532 framing + `GetFirmwareVersion` (reports IC 0x32, v1.6) | ✅ |
| Card detect + UID + SAK (`InListPassiveTarget`) | ✅ re-verified after the protocol fixes on a Classic 1K: ATQA now reported MSB first (`00 04`), `GetGeneralStatus` shows the active target, `InRelease` OK |
| Continuous polling | ✅ |
| MIFARE Classic auth (key A and key B) | ✅ |
| MIFARE Classic **read** — full 1K card (all 64 blocks) | ✅ |
| MIFARE Classic **write** | ✅ write, read back and restore of one data block (key A) |
| PN532 protocol fixes (wait-for-card, error frame, `InAutoPoll`, value ops, raw CRC handling, ATS) | ✅ verified on hardware: wait/abort and retries, `InAutoPoll`, `InSelect`/`InDeselect`/`InRelease` and the no-target error, NACK resend, extended frames and `LEN=0xFF` frames, bad-checksum frames ignored, resync after a stalled frame, syntax-error frame, `InCommunicateThru` 7-bit frames and CRC on/off in each direction (manual WUPA/anticollision/SELECT/HLTA), register restore, pn532.js connect sequence, MIFARE key A auth/read/write<br>⚠️ not yet verified on hardware: key B auth, value ops (increment/decrement/restore/transfer); unit-tested with the RC522 mocked |
| Browser page (`host/webserial.html`) | ⚠️ loads and connects; card operations not yet tested on a card |
| libnfc CLI / NFCToolsGUI | ◻️ not yet exercised end-to-end |

## Wiring (RC522 → RP2040-Zero, SPI1)

![RC522 soldered to an RP2040-Zero](docs/img/wiring.jpg)

The RC522's 8-pin SPI header is, in silkscreen order,
`SDA SCK MOSI MISO IRQ GND RST 3.3V`. Wire it to the RP2040-Zero as:

| RC522 pin | RP2040 GPIO / pin | Function |
|-----------|-------------------|----------|
| SDA/NSS | GPIO 9  | chip select (driven manually) |
| SCK     | GPIO 10 | SPI1 SCK |
| MOSI    | GPIO 11 | SPI1 TX |
| MISO    | GPIO 12 | SPI1 RX |
| IRQ     | GPIO 13 | input (unused; firmware polls) |
| RST     | GPIO 14 | reset (active low) |
| GND     | GND     | ground |
| 3.3V    | 3V3     | power — **3.3 V only, never 5 V** |

Notes on the board pictured above:
- It's a genuine NXP **RC522** (chip marked `RC522`) with the usual **27.12 MHz**
  crystal — standard ISO14443A @ 13.56 MHz / 106 kbps.
- GPIO 10–13 are the RP2040's **SPI1** function pins, so the module sits on the
  `spi1` instance (see [include/pins.h](include/pins.h)).
- In the photo the six SPI/control pins are soldered straight onto the header;
  **GND** and **3.3V** are the two flying leads to the RP2040's `GND`/`3V3` pins.
- The RC522 is powered from the RP2040's 3V3 rail, which is fed from USB 5 V
  through the RP2040-Zero's regulator — the module draws well under the
  regulator's budget, so no external supply is needed.
- `IRQ` (GPIO 13) is wired but unused: the firmware polls, so you can leave this
  pin unconnected if you re-wire. All other pins are required.

## Build & flash

```sh
pio run                          # build
pio run -t upload                # reboots the running firmware into BOOTSEL, then flashes
```

`pio run -t upload` opens the PN532 port at **1200 baud**, which makes the
firmware reboot into the BOOTSEL bootloader; picotool then flashes it. No
button or enclosure access needed. `picotool reboot -f -u` works too, through
the "Reset" vendor interface.

If more than one serial device is attached, PlatformIO may pick the wrong one;
pass the port explicitly with `pio run -t upload --upload-port /dev/tty.usbmodemXXXX1`.

The **BOOTSEL button** is still the fallback: hold it, plug in the RP2040-Zero,
release, and the RPI-RP2 drive appears. You need it for the first flash of this
firmware over an older build that had the 1200-baud reset disabled, or if the
running firmware has crashed.

## USB ports

The device uses the Pico SDK's stock USB setup (product
"RC522-PN532-Bridge", `2E8A:000B`). It enumerates **one** CDC serial port, which
carries the binary PN532 protocol. Point host tools here. It also exposes a
vendor "Reset" interface for picotool, which isn't a serial port.

On macOS it appears as `/dev/tty.usbmodemXXXX1`:

```sh
ls /dev/tty.usbmodem*
```

## Debug log (UART)

`printf` output (MFRC522 version, per-command trace) goes to **UART0 TX on
GPIO 0**, 115200 8N1. Wire a 3.3 V USB-serial adapter's RX to GPIO 0 (and GND)
to watch it.

## Python

```sh
cd host
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python read_uid.py --port /dev/tty.usbmodemXXXX1
python mfc_dump.py --port /dev/tty.usbmodemXXXX1 --out card.mfd
```

`mfc_dump.py` re-selects the card before each sector and, by default, tries
**key A then key B** with the given `--key` (default factory `FFFFFFFFFFFF`).
Restrict with `--key-a` / `--key-b`, and set `--size 4` for a 4K card. The
re-select per sector is required: after a read or a failed auth the card leaves
the authenticated state, so the reader must re-run anticollision+select before
the next crypto1 handshake.

> **Note on keys:** different sectors — and different cards — may hold the
> factory key as **key A or key B** (not always A). The default A→B fallback
> handles this; the dump output labels which key worked for each block.

## Browser (Web Serial)

[host/webserial.html](host/webserial.html) drives the bridge from Chrome or Edge
using stock [pn532.js](https://github.com/taichunmin/pn532.js): its Web Serial
adapter and its `Hf14a` MIFARE plugin. It can read the card UID/ATQA/SAK, watch
for cards, read/write blocks, handle value blocks, and dump a MIFARE Classic
card to `.mfd` with a list of keys to try.

Limits that come from pn532.js:
- **7-byte UIDs:** its MIFARE auth sends the first 4 UID bytes, while 7-byte-UID
  cards expect the last 4, so key operations fail on them. 4-byte-UID cards,
  which most Classic 1K cards are, work. The page warns when it sees a longer UID.
- **Port picker:** its adapter only lists USB-serial chips, so the page adds the
  bridge (`2E8A:000B`) to the filter.
- **Disconnect:** its adapter can't close the port it has piped, so Disconnect
  may reload the page.

Open `host/webserial.html` straight from disk in Chrome or Edge (double-click
it, or drag it into the browser), press **Connect**, and pick the bridge in the
port chooser. No web server is needed: Web Serial requires a secure context, and
the browser treats a local `file://` page as one. The page loads lodash and
pn532.js from CDNs (pinned, with integrity hashes), so it needs internet access.

## libnfc (CLI)

```sh
brew install libnfc
# the pn532_uart driver wants the PN532 CDC port:
nfc-list   -c "pn532_uart:/dev/tty.usbmodemXXXX1"
nfc-poll   -c "pn532_uart:/dev/tty.usbmodemXXXX1"
```

Or put it in `~/.nfc/libnfc.conf` / `/usr/local/etc/nfc/libnfc.conf`:

```
device.name = "RC522 bridge"
device.connstring = "pn532_uart:/dev/tty.usbmodemXXXX1"
```

`nfc-poll` uses `InAutoPoll`. Set `LIBNFC_LOG_LEVEL=3` to see every PN532
command libnfc sends, which is useful when adding compatibility stubs.

## GUI — NFCToolsGUI

[NFCToolsGUI](https://github.com/GSWXXN/NFCToolsGUI) (AGPL-3.0, Electron, macOS
supported) wraps libnfc and talks to PN532 boards over serial, so it works with
this bridge for scan / read / write with known keys / dump editing.

Notes:
- It expects a serial-port PN532. Select the bridge's port
  (`/dev/tty.usbmodem*`). If the app only lists `wchusbserial`/`ttyUSB` names,
  point it at the port via its libnfc config or a symlink.
- The app is unsigned: first launch needs right-click → **Open** (Gatekeeper),
  and on Apple Silicon you may need to build it from source.
- Features needing raw frames with manual parity rely on real-PN532 behaviour
  the RC522 can't reproduce, and will fail cleanly rather than hang. Gen1A
  "magic" card commands only need 7-bit frames with CRC off, which are now
  supported, so they may work (untested).

## Layout

```
include/pins.h              pin / SPI assignments
src/mfrc522.*               MFRC522 SPI driver (regs, transceive, CRC, auth)
src/iso14443a.*             REQA/WUPA, anticollision + select, HLTA
src/mifare_classic.*        auth / read / write 16-byte blocks
src/pn532_frame.*           PN532 frame parser + response builder
src/pn532_cmd.*             command dispatcher -> RC522 stack
src/main.c                  init + USB/PN532 poll loop
host/                       Python examples (read_uid, mfc_dump), Web Serial page
docs/img/wiring.jpg         as-built RC522 + RP2040-Zero wiring photo
```
