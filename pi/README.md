# Caltreat Pi app

The web app that runs the train's lights and sounds. It runs on the Raspberry
Pi on the train and serves a control page at **http://caltreat.local:8080/**.
Open it from any phone or laptop on the same network.

The page:

- **Plays Caltrain KISS EMU cab sounds** through the Pi's 3.5 mm jack: horn,
  bell, door chime, traction motor whine, air hiss and brake squeal, plus horn
  patterns. All browsers share one speaker, so they also share one set of
  controls. You can test the USB sound cards from the page too.
- **Switches the relay shield lights**: white head/ditch lights and red
  markers. Each pair can be off, on or flashing, and the ditch lights can flash
  with the horn.
- **Switches GP0–GP7 on the lighting controller boards**
  ([`firmware/caltreat_lighting_controller`](../firmware/caltreat_lighting_controller)).
  The app finds the boards on the Pi's wired network over Modbus TCP.

Everything is in one file, [`src/caltreat/__main__.py`](src/caltreat/__main__.py),
built with [NiceGUI](https://nicegui.io/) and numpy. The sound engine is a port
of the browser version in [`publish.html`](publish.html). The first ~1000 lines
of `__main__.py` are an older version kept as comments.

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
5. Waits until the page responds, or prints the app's logs if it doesn't.

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

## Hardware

| What | How the app reaches it |
| --- | --- |
| Speaker | 3.5 mm jack (ALSA card `Headphones`), through `aplay`. Volume is set with `amixer`. |
| USB sound cards | C-Media USB cards, for the speaker test on the page. |
| Relay shield (Inland / Keyestudio KS0212-style) | BCM GPIO 4, 22, 6, 26 for channels 1–4, through `pinctrl`. Channels 1–2 are white, 3–4 are red. |
| Lighting controller boards | Modbus TCP on `eth0`. The Pi is `192.168.50.1` and hands out `.10`–`.254`. |

The `caltreat` user has to be in the `audio` and `gpio` groups. The app saves
the boards' names (A, B, …) in `~/.config/caltreat/boards.json` on the Pi.
That file is not part of the repo, and deploys leave it alone.

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
   virtualenv, which needs internet on the Pi.
