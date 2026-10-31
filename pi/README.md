# Caltreat Pi app

The web app that runs the train's lights and sounds. It runs on the Raspberry
Pi on the train and serves two pages:

- **http://caltreat.local/**: the **engineer page**. Big buttons only:
  the cab display, Depart / Arrive / Crossing, HORN (hold), BELL, DOORS,
  TALK (hold, the intercom), power with AIR and STOP, the horn signals, the
  lights and the **E-STOP** (see [Emergency stop](#emergency-stop-e-stop)). Hover (or long-press) any control for what it does.
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
- **Lights**: GP0–GP7 on the lighting controller boards
  ([`firmware/caltreat_lighting_controller`](../firmware/caltreat_lighting_controller)),
  found on the Pi's wired network over Modbus TCP (the Pi has no relay shield). Each output is mapped to a
  *light function* in the settings (headlights, ditch lights, markers, the walkers' stack light, interior, door
  lights, crossing flash, horn, bell, or your own), so you can rewire without
  changing code.
- **Stack lights and E-STOP**: a four-colour stack light on each board tells the
  people walking inside when to walk; the engineer's E-STOP stops the train at
  once (see [Stack lights](#stack-lights-walk--slowing--stopped) and [Emergency stop](#emergency-stop-e-stop)).

The code is in [`src/caltreat/`](src/caltreat/), built with
[NiceGUI](https://nicegui.io/) and numpy:

| File | What |
| --- | --- |
| `__main__.py` | speakers, lights, boards and both pages; the old numpy sound engine (a port of [`publish.html`](publish.html)), used only when Node.js is missing. The first ~1000 lines are an older version kept as comments. |
| `sidecar.py` | starts the v12 engine (`../../engine/sidecar.js`) and reads each speaker's sound from it. |
| `sequencer.py` | Depart / Arrive / Crossing / Auto run and the horn signals (a port of the v12 browser app's `sequencer.js`). |
| `safety.py` | the stack lights' state (walk / slowing / stopped) and the E-STOP alarm tone. |
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

### Stutters, frozen pages, lost Wi-Fi

**Settings → Health** shows the Pi's CPU, temperature and power (undervoltage /
throttling), the web side's event loop lag (and where it was stuck last), each
sound card's gaps / underruns, the sound engine's render speed and Wi-Fi drops.
The log has a line for each event loop freeze over 200 ms
(`caltreat: event loop blocked 850 ms ... in file.py:123 function`) and for
sound card underruns: `journalctl --user-unit caltreat | grep -E 'blocked|underrun'`.

- **Power**: a Pi 4 needs the official 5.1 V 3 A supply; the USB sound cards
  draw from it too. "undervoltage" on the Health panel (or a ⚠ from
  diagnose.sh) means a weaker supply or a long / thin cable: expect sound
  glitches, USB resets and Wi-Fi drops until it's replaced.
- **Sound priority**: the app runs its sound threads, aplay and the sound
  engine at nice -10 (the pages at 0) once `sudo ~/caltreat/setup/network.sh`
  has allowed it (`LimitNICE` for the app user's services) and the Pi has
  rebooted. Without it the log says "can't give the sound a higher priority".
- **Buffer**: each card holds 60 ms (`CALTREAT_ALSA_BUFFER_MS`). If the Health
  panel keeps showing underruns, 80-100 rides out more (add
  `Environment=CALTREAT_ALSA_BUFFER_MS=80` to `caltreat.service`) at the cost
  of the intercom running ~160 ms late.

#### `tools/diagnose.sh`

Run it on the Pi while the problem happens (keep playing sounds and using the
pages for the few minutes it runs). No sudo needed. It writes one file to send
back, with a summary of what looks wrong at the top (undervoltage, throttling,
CPU per process and thread, sound buffers running dry, a slow app, Wi-Fi drops,
access point restarts, signal, ping loss) and the logs behind it:

```sh
bash ~/caltreat/tools/diagnose.sh        # 3 minutes; or: ... diagnose.sh 300
```

Not deployed yet? From your computer, on the same network:

```sh
scp pi/tools/diagnose.sh caltreat@caltreat.local: && ssh -t caltreat@caltreat.local bash diagnose.sh
scp 'caltreat@caltreat.local:caltreat-diag-*.tar.gz' .
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
channel): plug it in and run the setup with `AP_IFACE=wlan1`. With the built-in
Wi-Fi doing both, the one radio is shared: client and access point must sit on
the same channel, throughput is roughly halved, and every scan or roam of
wlan0 (NetworkManager scans more when the signal is weak) briefly takes the
radio away from the phones. When wlan0 joins on another channel the access
point has to move, which drops every phone for a few seconds.

The channel helper (`caltreat-ap-channel`, every minute and on wlan0 up/down)
restarts the access point only when it must move: never while it is up on the
right channel. While wlan0 isn't joined it moves only when the network has
been seen on the same channel twice in a row, at most every 5 minutes, so a
flickering Wi-Fi doesn't drop the phones every minute. It also turns Wi-Fi
power saving off on wlan0 and uap0 (power saving makes the brcmfmac driver drop
frames and adds latency spikes); NetworkManager is told the same
(`wifi.powersave=2`).

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
**Channel test** plays it on one channel of any card. Level goes up to
400 % (+12 dB); loud parts are then softly limited, not clipped. **Card
volume** is each card's own mixer volume in dB (0 dB = full scale, the
default). **Trim** is extra gain per card so the same level sounds the same
on any card: the 3.5 mm jack's full scale is about 9 dB below a USB DAC's,
so its trim starts at +9 dB. That is an estimate; set it by ear (play the
same sound on the jack and a USB speaker and match them). Defaults: FRONT = the jack, INSIDE = USB #1
(both channels), REAR = none.

Each speaker plays its own v12 mix (see "Sounds"). Front / Rear env under Settings → Sound sets what the outside
speakers sound like (on the train, approaching, platform, distance).

### Lights

Every output, each board's GP0–GP7, is mapped to
a light function, or to "by hand". The phase (A/B) makes two outputs of one
function alternate when it flashes (ditch lights, crossing flashers).
Functions:

| Function | Switched by |
| --- | --- |
| Headlights, Markers, Interior, your own (＋) | the engineer page: off / on / flash. Headlights also flash with the horn and 5 s after (Settings: Horn ☀). |
| Door lights | the doors: on while open, flashing while they move. |
| Crossing flash | flashing during a Crossing sequence or crossing signal. |
| Horn lamp, Bell lamp | on while the horn sounds / the bell rings. |
| Stack green / yellow / red | the walkers' stack light (see [Stack lights](#stack-lights-walk--slowing--stopped)). |
| Stack blue | not assigned yet: off / on / flash from the engineer page's lights row. |

Each output row shows its live state; the ◐ button switches a "by hand"
output, or forces a mapped one on as a test (tap again to release). Boards are
known by their chip ID (the MAC is shown too) and named A, B, … in the order
they are first seen; you can rename them. Default mapping: see below (car
lamps on GP0–GP3, stack light on GP4–GP7).

## Cars A and B: direction, speakers, lights

The train runs with car A or car B leading. The engineer page's reverser
(A ◀ / ▶ B, also in the display) switches it, only at a standstill with the
power off; otherwise it says "■ stop first". The direction is saved and is
the same in every browser.

**Speakers.** Each car has an outside (ext) and an inside (int) speaker, each
on one half of a USB DAC. Settings → Speakers maps them; the defaults are
DAC #1 (first by USB port) L = A ext, R = A int and DAC #2 L = B ext,
R = B int. What each one plays follows the direction:

| | leading car | trailing car |
| --- | --- | --- |
| outside | front mix: horn, bell, train | rear mix: bell (-6 dB), train, no horn |
| inside | inside mix + intercom | inside mix + intercom |

Reversing fades the two outside speakers over to their new mix (60 ms).
Recordings' F / I / R mean front / inside / rear, so they follow the direction.

**Lights.** Each car has a headlight, ditch lights L and R and markers, as
light functions (A headlight, A ditch L, ...) that board pins map
to. They follow 49 CFR 229.125. At the leading car the headlight and ditch
lights burn steady. The ditch lights flash alternately (L / R) when the horn
sounds at grade crossings (229.125(d)): here they flash while the horn sounds
or the Crossing sequence runs, and for 15 s after the last horn (the rule lets
them continue until the train is past). The trailing car shows its markers,
with its headlight and ditch lights off. Settings → Light functions sets the
flash rate (40-180 per minute per lamp, default 60) and how long the flashing
lasts after the horn. The engineer page's ☀ Lights button switches all of
them off and on.

Default mapping of a lighting controller named A or B (they get letters in
the order they are found):

| Pin | Light |
| --- | --- |
| GP0 | ditch L (its car) |
| GP1 | ditch R |
| GP2 | headlight |
| GP3 | markers |
| GP4 | stack green |
| GP5 | stack yellow |
| GP6 | stack red |
| GP7 | stack blue |

GP0–GP3 are the MOSFET module's lights 1–4, as before; the stack light takes
the four pins that were free. Change any of these in Settings.

A config from before (version 1) is updated on first start. If its speakers
were still at their old defaults, it gets the new ones; otherwise
front -> A ext, inside -> A int, rear -> B ext (B int off). Check Settings →
Speakers.

**No relay shield (config version 3).** Older configs mapped lights to a
relay shield that isn't on the train. On the first start of this version the
relays are dropped: a light that a board pin already drives (the default
headlights and markers) just loses its relay, any other relay light moves to
a free board pin (car lamps to their car's board), and boards A and B get
the stack light on GP4–GP7 where those pins are free. What moved, or couldn't
(no free pin), is listed under Settings → Light functions. The old file is kept as
`config.json.v2`.

## Stack lights: walk / slowing / stopped

People walk inside the costume; the four-colour stack light on each lighting
controller (both show the same) tells them when to walk:

| Light | Means | When |
| --- | --- | --- |
| green, steady | walk forward | power on and the train speeding up or holding its speed |
| yellow, steady | slowing down | moving with the power off or reduced, STOP, Arrive braking |
| red, steady | stopped | standing (below 0.3 mph), at power-on, while the doors aren't closed, while the sound engine starts |
| red, flashing (2 Hz) | E-STOP | until the engineer resets it |
| blue | not assigned yet | off / on / flash by hand (engineer page, Lights) |

It comes from the engine's own speed, power and braking (`safety.py`). A
change towards red shows at once; a change towards green only after the new
state has held for 0.5 s, so it never flickers. The cab display shows the
same stack (the small column at the right).

If the Pi stops talking to a board (app crash, cable, power), the board itself
turns the stack red on and the other colours off after its heartbeat
(about 3 s): the app writes these failsafe levels to each board when it
connects (in the board's RAM, not its flash). Nothing can get greener without
the app.

## Emergency stop (E-STOP)

The big red **E-STOP** under the header on the engineer page (or hold
<kbd>E</kbd>): **hold it 0.3 s** (the bar fills). Letting go earlier does
nothing, so a brush of a sleeve or a pocket doesn't stop the show, but a real
press still takes only a fraction of a second.

At once:

- the train stops: power off and speed 0 (the engine's STOP brakes over
  seconds, so the app sends its own instant stop), the brake-air dump and, if
  it was moving, the squeal;
- any sequence or Auto run is cancelled, horn signals and the bell stop,
  playing recordings stop;
- an alarm (two-tone, 1 kHz / 800 Hz, 0.5 s cycle) sounds on **all four
  speakers**, whatever the direction, over the intercom's and recordings'
  ducking: loud for 5 s, then a quieter burst every 3 s (the interior speakers
  at 70 % of that by default: people stand close to them);
- the stack lights flash red (ditch lights, headlights and markers stay as the
  rule has them).

It stays **latched**: every browser shows a red E-STOP banner, and power,
Depart / Arrive / Crossing / Auto run and closing the doors are locked (the
doors can still open). To reset: ↺ in the banner, or hold E-STOP 2 s. The
train then stands with the power off (red) until the engineer applies power.

Settings → E-STOP sets the alarm's loud level (default 60 %, peaks at about
-4.5 dBFS), how long it stays loud, the quieter level and the inside share,
with ▶ to test it (2 s, nothing else happens).

## Recordings

Settings → ♪ Recordings: ＋ uploads sound files (wav, mp3, m4a, ogg, flac; up to
10 min / 100 MB each). The browser converts them to WAV, so the Pi needs no
ffmpeg; a file the browser can't play is refused. Each recording has ▶ / ■,
⟳ loop, its speakers (F / I / R), a level, how much it ducks the train while it
plays, a pin for the engineer page, ⤓ download and delete. Pinned recordings
are big buttons on the engineer page (tap to play / stop, several at once, ■
stops all), the same in every browser.

The files live in `~/.config/caltreat/recordings/`, their settings in
`config.json`. ⤓ / ⤒ next to ♪ download / restore all of them as a zip; the
settings backup carries only the settings.

## Look

Dark by default, for running at night; ☀ / ☾ in the header switches to a light
theme in the website's colors (remembered per browser). Logo, icons and the
Roboto font come from [`website/frontend`](../website/frontend) and are served
by the app, so they work without internet. "Add to Home Screen" gets the
Caltreat icon.

## Hardware

| What | How the app reaches it |
| --- | --- |
| Speakers | 3.5 mm jack (ALSA card `Headphones`) and C-Media USB sound cards, one `aplay` per card in use. Volumes are set with `amixer`. |
| Lighting controller boards | Modbus TCP on `eth0`. The Pi is `192.168.50.1` and hands out `.10`–`.254`. |

The `caltreat` user has to be in the `audio` group.

## Trying it without the Pi

On a computer without `aplay` the app runs with fake sound cards (two fake
USB cards, the sound goes nowhere):

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
