# Caltreat Pi app

The web app that runs the train's lights and sounds. It runs on the Raspberry
Pi on the train and serves two pages:

- **http://caltreat.local/**: the **engineer page**. Big buttons only:
  the cab display, Depart / Arrive / Crossing, HORN (hold), BELL, DOORS,
  TALK (hold, the intercom), power with AIR and STOP, the horn signals and the
  lights. Hover (or long-press) any control for what it does.
- **http://caltreat.local/settings** (the ⚙ on the engineer page): the
  sound mix, which sound card and channel each speaker is on, the intercom
  levels, what every light output does, and a backup of all of it.

Open them from any phone or laptop on the Pi's own Wi-Fi, **Caltreat Cab**,
or on the "caltreat" Wi-Fi the Pi joins (see [Network](#network-caltreatlocal-and-the-cab-wi-fi)). Every browser
controls the same train, so they all show the same state. The old addresses
with `:8080` / `:8443` keep working.

For the intercom (TALK) the phone needs the **https** address,
**https://caltreat.local/**: see [Intercom and HTTPS](#intercom-and-https).

What it does:

- **Plays Caltrain KISS EMU cab sounds**: horn, bell, door chime, traction
  motor whine, air hiss and brake squeal, plus horn patterns. The sounds go to
  up to three speakers, FRONT, INSIDE and REAR, each one the 3.5 mm jack or a
  channel of a USB sound card (all three play the same sound; INSIDE is also
  the intercom's).
- **Sequences**: Depart, Arrive and Grade crossing play the bell, horn, doors,
  power and brakes in the order US rules ask for (GCOR 5.8.1 / 5.8.2, 49 CFR
  222.21), in "Show" (short, for the costume) or "Real" timing. Auto run (↻)
  repeats Depart, two crossings and Arrive. Pressing a control yourself during
  a sequence takes that control back; ✕ stops the sequence.
- **Intercom**: hold TALK and speak into the phone; it plays on the INSIDE
  speakers, and the train sounds there get quieter while you talk.
- **Lights**: the relay shield (white head/ditch lights and red markers by
  default) and GP0–GP7 on the lighting controller boards
  ([`firmware/caltreat_lighting_controller`](../firmware/caltreat_lighting_controller)),
  found on the Pi's wired network over Modbus TCP. Each output is mapped to a
  *light function* in the settings (headlights, markers, interior, door
  lights, crossing flash, horn, bell, or your own), so you can rewire without
  changing code.

The code is in [`src/caltreat/`](src/caltreat/), built with
[NiceGUI](https://nicegui.io/) and numpy:

| File | What |
| --- | --- |
| `__main__.py` | speakers, lights, boards and both pages; the old numpy sound engine (a port of [`publish.html`](publish.html)), used only when Node.js is missing. The first ~1000 lines are an older version kept as comments. |
| `sidecar.py` | starts the v12 engine (`../../engine/sidecar.js`) and reads each speaker's sound from it. |
| `sequencer.py` | Depart / Arrive / Crossing / Auto run and the horn signals (a port of the v12 browser app's `sequencer.js`). |
| `intercom.py` | push-to-talk: one talker at a time, jitter buffer, resampling. |
| `config.py` | the settings file. |
| `hw.py`, `fakesink.py` | sound cards (`aplay`, `amixer`), and the stand-in used without them. |
| `static/` | the page's look (`cab.css`), its script (`cab.js`), the microphone capture (`mic-worklet.js`) and the fonts (Barlow, IBM Plex Mono; SIL Open Font License), served by the Pi so the page looks right without internet. |

## Sounds: the v12 engine (Node.js)

The sounds are the v12 KISS engine (`engine/engine.js`, `engine/buses.js`, copied unchanged from the
caltrain-sounds repo by `engine/sync.sh`), run by Node.js in `engine/sidecar.js`, which the app starts and restarts.
Each speaker gets its own v12 mix: FRONT outside at the front cab, INSIDE in the car (doors leak outside sounds in),
REAR outside at the back (no horn), exactly what the v12 page's Output selector plays.

- **Install Node.js on the Pi once** (needs internet): `sudo apt install nodejs` (Raspberry Pi OS Bookworm's 18.x
  works). Without it the app plays the old sounds and the page shows ⚠.
- **Door recording**: not in this repo (it isn't ours to publish). `deploy.sh` copies it from the private repo
  `ctr-assets` (cached on your computer; `--no-assets` skips it) to `~/.config/caltreat/assets/` on the Pi; or upload
  `doors_v10.wav` + `doors_v10.json` in Settings → Sound. Without it the doors are the engine's synth doors.
- **CPU**: one core per speaker bus on a Pi 4 (about 0.4–0.6 of a core each, worst moments ~0.85); a Pi 5 has
  room to spare. After a start the engine warms up for ~25 s on a Pi 4 (silent) before it plays.

## Deploying to the train

From the repo root on your computer:

```sh
pi/deploy.sh            # deploy what's on GitHub's main branch
pi/deploy.sh my-branch  # deploy a branch, tag or commit
pi/deploy.sh --local    # deploy pi/ as it is on your computer, including uncommitted changes
```

The script:

1. Takes `pi/` from the chosen version.
2. Copies it to `~/caltreat` on the Pi. Files that aren't in the repo get
   deleted there; the virtualenv is kept.
3. Runs `poetry sync` if `pyproject.toml` or `poetry.lock` changed. If they
   didn't, it skips this step, so deploys work while the train has no internet.
4. Installs [`caltreat.service`](caltreat.service) and restarts the app.
5. Waits until the page responds (http and https), or prints the app's logs if
   it doesn't.

Your computer has to be on the same network as the Pi. The script asks for
the Pi's password once per deploy. To skip the password, copy your SSH key to
the Pi once:

```sh
ssh-copy-id caltreat@caltreat.local
```

If `caltreat.local` doesn't resolve, use the IP address:
`CALTREAT_HOST=caltreat@10.26.81.200 pi/deploy.sh`.

To see which version is running: `ssh caltreat@caltreat.local cat caltreat/DEPLOYED_VERSION`.

## Running on the Pi

The app runs as a systemd **user** service for the `caltreat` user. Linger is
on for that user (`loginctl enable-linger caltreat`), so the service starts at
boot without anyone logging in and restarts within 3 seconds if it crashes.
After power-on, give the page about a minute to come up.
Because it's a user service, no sudo is needed.

On the Pi:

```sh
systemctl --user status caltreat     # is it running?
journalctl --user-unit caltreat -f     # follow the logs
systemctl --user restart caltreat
systemctl --user stop caltreat       # stop it, e.g. to run it by hand
```

To run it by hand (stop the service first, or the port will be in use):

```sh
cd ~/caltreat
poetry run python -m caltreat
```

## Intercom and HTTPS

Phones only let a web page use the microphone on a secure (https) page, so
the app also serves **https://caltreat.local/** (the plain
http://caltreat.local/ keeps working for everything else; its TALK
button shows "https ↗" and opens the https page).

The train has no internet and no real domain, so the app makes its own
**self-signed certificate** the first time it starts (in
`~/.config/caltreat/https-cert.pem` and `https-key.pem` on the Pi, never in
the repo; it is renewed 30 days before it expires, after about 2 years). The
trade-off: every phone has to accept it once, and the browser keeps showing
"Not secure" in the address bar. The connection is still encrypted. The
alternative (a real certificate) needs a domain name and internet to renew it.

Accept it once per phone:

- **iPhone (Safari)**: open https://caltreat.local/ → "This Connection
  Is Not Private" → **Show Details** → **visit this website** → **Visit
  Website**. Then hold TALK and allow the microphone. If Safari forgets it
  later, do the same again.
- **Android (Chrome)**: open https://caltreat.local/ → "Your connection
  is not private" → **Advanced** → **Proceed to caltreat.local (unsafe)**.
  Then hold TALK and allow the microphone. If `caltreat.local` doesn't open,
  use the Pi's address: https://10.42.0.1/ on Caltreat Cab.

The certificate names caltreat.local, caltreat, 10.42.0.1 and every address
the Pi has had. When the Pi gets a new address (it joins another Wi-Fi), the
app makes the certificate again with the same key and the new address added;
phones that accepted the old one ask once more (Safari and Chrome remember the
certificate, not the key). Phones on Caltreat Cab use caltreat.local /
10.42.0.1, which don't change, so this is rare.

Using it: hold TALK, speak, let go. One person talks at a time (the button
shows busy on the other phones). It stops by itself when you let go, switch
away from the page, lose the connection, or after the "Max talk" time
(60 s by default). The bar on the button is your microphone level.

If it squeals (the phone hears the inside speakers), lower **Mic gain** or
**Output** under Settings → Intercom, or stand further from the speakers.
**Duck train** sets how much quieter the train sounds on the inside speakers
get while someone talks.

Delay from the phone's microphone to the speaker is about 140 ms measured
without Wi-Fi (10 ms frames, a jitter buffer that starts at 20 ms and grows
only if the network stutters, 40 ms of sound card buffer). If the log shows
sound card underruns on the Pi, give the cards more buffer: set
`Environment=CALTREAT_ALSA_BUFFER_MS=60` in `caltreat.service`.

## Network: caltreat.local and the cab Wi-Fi

The Pi is on three networks at once:

| Network | Interface | The Pi | What for |
| --- | --- | --- | --- |
| **caltreat** Wi-Fi (joins it) | `wlan0` | from its DHCP | your laptop (deploys), internet if it has some |
| **Caltreat Cab** Wi-Fi (its own) | `uap0` | `10.42.0.1`, hands out `10.42.0.x` | phones on the train |
| wired | `eth0` | `192.168.50.1` | the lighting controller boards |

The built-in Wi-Fi has one radio: it can be a client and an access point at
the same time, but only on one channel. So Caltreat Cab follows the channel of
the caltreat Wi-Fi (it moves when the Pi joins, and phones reconnect by
themselves); while caltreat is out of range Caltreat Cab stays up on its last
channel and the Pi keeps looking for caltreat. Put the caltreat Wi-Fi on
2.4 GHz (or 5 GHz channel 36-48 / 149-165; the access point can't use radar
channels).

**Set it up once** (on the Pi, after a deploy; safe to run again):

```sh
sudo ~/caltreat/setup/network.sh --dry-run   # shows what it would change
sudo WIFI_COUNTRY=US ~/caltreat/setup/network.sh
```

It asks for the caltreat Wi-Fi's password (unless the Pi already has a
connection for it, which it keeps) and a password for Caltreat Cab (8-63
characters). Passwords are kept only in NetworkManager's files on the Pi
(`/etc/NetworkManager/system-connections/`, root only), never in the repo.
Other names: `STA_SSID=…` (the Wi-Fi to join), `AP_SSID=…` (the Pi's own).
To change a password, run it again with `AP_PASS=…` or `STA_PASS=…` set;
Caltreat Cab restarts with its new password (phones on it rejoin). If it ever
keeps the old one, restart it by hand: `sudo nmcli connection up caltreat-ap`.
`--undo` removes it all. It also lets ordinary users open ports 80 and up
(`net.ipv4.ip_unprivileged_port_start=80`), so the app serves the pages
without `:8080`.

**Phones**: join **Caltreat Cab**, open **http://caltreat.local/** (or
http://10.42.0.1/). If the phone says the Wi-Fi has no internet, choose to
stay connected (or turn off mobile data for a moment). caltreat.local works by
mDNS, and on Caltreat Cab also by its DNS for phones without mDNS.

Settings → Network shows both Wi-Fis, the wired network and the ports.

A USB Wi-Fi adapter for the access point is more robust (its own radio, any
channel): plug it in and run the setup with `AP_IFACE=wlan1`.

Troubleshooting on the Pi:

```sh
nmcli device status                 # wlan0 → caltreat, uap0 → caltreat-ap, eth0 → boards
iw dev                              # wlan0 and uap0 on the same channel
journalctl -t caltreat-ap           # access point channel moves
journalctl -u NetworkManager -b     # why a connection failed
nmcli connection up caltreat-ap     # start the access point by hand
sysctl net.ipv4.ip_unprivileged_port_start   # 80 after the setup
```

## Settings, backup and restore

All settings are in one file on the Pi, `~/.config/caltreat/config.json`
(not in the repo; deploys leave it alone). The settings page saves every
change at once. On the settings page:

- **⤓** downloads it as `caltreat-config.json` (keep one as a backup);
- **⤒** restores a downloaded file: it is checked first, and you see the list
  of what would change before you apply it;
- **⟲** resets everything to the defaults (board names are kept).

The old `boards.json` (board names) is read into it on the first start.

### Speakers

FRONT, INSIDE and REAR each pick a sound card and a channel (L, R or both)
and a level. "USB #1" means the first USB sound card by USB port, "USB port
1.3" one exact socket (port paths stay the same across reboots; ALSA's card
names like `Device_1` don't). The row shows ✓ and the ALSA card when it is
plugged in, ✗ when it isn't. ▶ plays a 440 Hz test tone on that speaker;
**Channel test** plays it on one channel of any card. **Card volume** is
each card's own mixer volume. Defaults: FRONT = the jack, INSIDE = USB #1
(both channels), REAR = none.

Each speaker plays its own v12 mix (see "Sounds"). Front / Rear env under Settings → Sound sets what the outside
speakers sound like (on the train, approaching, platform, distance).

### Lights

Every output, the relay shield's R1–R4 and each board's GP0–GP7, is mapped to
a light function, or to "by hand". The phase (A/B) makes two outputs of one
function alternate when it flashes (ditch lights, crossing flashers).
Functions:

| Function | Switched by |
| --- | --- |
| Headlights, Markers, Interior, your own (＋) | the engineer page: off / on / flash. Headlights also flash with the horn and 5 s after (Settings: Horn ☀). |
| Door lights | the doors: on while open, flashing while they move. |
| Crossing flash | flashing during a Crossing sequence or crossing signal. |
| Horn lamp, Bell lamp | on while the horn sounds / the bell rings. |

Each output row shows its live state; the ◐ button switches a "by hand"
output, or forces a mapped one on as a test (tap again to release). Boards are
known by their chip ID (the MAC is shown too) and named A, B, … in the order
they are first seen; you can rename them. Default mapping: R1/R2 headlights
A/B, R3/R4 markers A/B, board pins by hand.

## Hardware

| What | How the app reaches it |
| --- | --- |
| Speakers | 3.5 mm jack (ALSA card `Headphones`) and C-Media USB sound cards, one `aplay` per card in use. Volumes are set with `amixer`. |
| Relay shield (Inland / Keyestudio KS0212-style) | BCM GPIO 4, 22, 6, 26 for channels 1–4, through `pinctrl`. |
| Lighting controller boards | Modbus TCP on `eth0`. The Pi is `192.168.50.1` and hands out `.10`–`.254`. |

The `caltreat` user has to be in the `audio` and `gpio` groups.

## Trying it without the Pi

On a computer without `aplay` the app runs with fake sound cards (two fake
USB cards, the sound goes nowhere), and without the relays:

```sh
cd pi
poetry install
CALTREAT_PORT=8090 CALTREAT_HTTP_PORTS= CALTREAT_HTTPS_PORT=8453 CALTREAT_CONFIG_DIR=/tmp/caltreat-config poetry run python -m caltreat
```

`CALTREAT_FAKE_AUDIO=<folder>` also writes what each card would play to raw
files there. A fake lighting controller for the settings page:
`python tests/fake_board.py 127.0.1.10 15020` with
`CALTREAT_BOARD_NET=127.0.1. CALTREAT_BOARD_PORT=15020` set for the app.

Tests (sequence timings, settings file, intercom, speaker and light routing;
pytest isn't one of the app's dependencies, so the Pi's install doesn't
change):

```sh
cd pi
poetry run pip install pytest
poetry run pytest
```

## Lighting controller firmware

Settings → Lighting controllers → Firmware shows each board's firmware and
register map version (⚠ when it isn't the chosen image) and updates boards
over the network: ⤓ for one board, ⤓ All for every board not on the image.
A board's lights go off while it updates, and the engineer page shows it as
unavailable. A failed update leaves the old firmware running.

The image is the one deployed with the app, or one uploaded on the page
(`.pio/build/app/firmware.bin`). To deploy one, so updates work offline:

```sh
pi/deploy.sh --with-lc-firmware path/to/firmware.bin   # or: --with-lc-firmware build (needs PlatformIO)
```

It goes to `~/caltreat/lc-firmware/` (not in the repo) and stays there on
later deploys until you deploy another. Boards refuse an older image unless ↓
is on, and refuse the version they already run. The update code is lcctl's
([`firmware/caltreat_lighting_controller/tools`](../firmware/caltreat_lighting_controller/tools)),
copied into `src/caltreat/lcctl/`; a test fails when the copies differ.

## Setting up a new Pi

1. Flash Raspberry Pi OS (64-bit) with hostname `caltreat` and user `caltreat`.
   Turn on SSH.
2. Install poetry: `curl -sSL https://install.python-poetry.org | python3 -`.
3. Turn on linger: `sudo loginctl enable-linger caltreat`.
4. To give the lighting controllers addresses, set `eth0` to a NetworkManager
   "shared" connection with address `192.168.50.1`.
5. From your computer, run `pi/deploy.sh`. The first deploy creates the
   virtualenv, which needs internet on the Pi. The app needs `openssl` for
   its https certificate (Raspberry Pi OS has it).
6. On the Pi, once: `sudo ~/caltreat/setup/network.sh` (see
   [Network](#network-caltreatlocal-and-the-cab-wi-fi)).
