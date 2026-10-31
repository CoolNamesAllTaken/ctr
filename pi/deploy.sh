#!/usr/bin/env bash
# Deploy the Caltreat app to the train's Raspberry Pi and restart it.
#
#   pi/deploy.sh            deploy origin/main (fetched first)
#   pi/deploy.sh <ref>      deploy a branch, tag or commit
#   pi/deploy.sh --local    deploy pi/ as it is in your working tree
#   ... --with-lc-firmware <firmware.bin | build>
#                           also deploy a lighting controller image for the settings page's firmware
#                           updates ("build": PlatformIO build of firmware/ in your working tree). Without it,
#                           the image deployed last time stays.
#
# Your computer must be on the same network as the Pi. Set CALTREAT_HOST to
# use a different address (default: caltreat@caltreat.local). See README.md.
set -euo pipefail

HOST="${CALTREAT_HOST:-caltreat@caltreat.local}"
URL="http://${HOST#*@}:8080/"
HTTPS_URL="https://${HOST#*@}:8443/"  # self-signed certificate, made by the app on the Pi (for the intercom mic)
REPO="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
STAGE="$(mktemp -d)"
SOCK="/tmp/caltreat-deploy-$$.sock"  # short: macOS caps socket paths at 104 chars
trap 'ssh -o ControlPath="$SOCK" -O exit "$HOST" 2>/dev/null || true; rm -rf "$STAGE"' EXIT

# One SSH connection for the whole deploy, so a password is asked for at most once.
SSH=(ssh -o ControlMaster=auto -o ControlPath="$SOCK" -o ControlPersist=60 -o ConnectTimeout=10)

LC_FW=""
ARGS=()
while (($#)); do
  case "$1" in
    --with-lc-firmware) LC_FW="${2:?--with-lc-firmware needs an image path or build}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

if [[ "${1:-}" == "--local" ]]; then
  echo "==> Staging pi/ from your working tree"
  rsync -a --exclude .venv --exclude __pycache__ "$REPO/pi/" "$STAGE/pi/"
  VERSION="$(git -C "$REPO" rev-parse --short HEAD)"
  [[ -n "$(git -C "$REPO" status --porcelain -- pi)" ]] && VERSION="$VERSION-dirty"
else
  REF="${1:-origin/main}"
  echo "==> Fetching origin"
  git -C "$REPO" fetch --quiet origin
  echo "==> Staging pi/ from $REF"
  git -C "$REPO" archive "$REF" pi | tar -x -C "$STAGE"
  VERSION="$(git -C "$REPO" rev-parse --short "$REF^{commit}")"
fi
KEEP_LC_FW=(--filter "P /lc-firmware/")  # rsync --delete keeps the image deployed last time
if [[ -n "$LC_FW" ]]; then
  if [[ "$LC_FW" == build ]]; then
    FW_DIR="$REPO/firmware/caltreat_lighting_controller"
    PIO="$(command -v pio || echo "$HOME/.platformio/penv/bin/pio")"
    echo "==> Building the lighting controller firmware ($FW_DIR)"
    (cd "$FW_DIR" && "$PIO" run -s -e app)
    LC_FW="$FW_DIR/.pio/build/app/firmware.bin"
  fi
  mkdir -p "$STAGE/pi/lc-firmware"
  cp "$LC_FW" "$STAGE/pi/lc-firmware/lighting-controller.bin"
  LC_VER="$(PYTHONPATH="$STAGE/pi/src" python3 -c 'import sys; from caltreat.lcctl import image; print(image.load(sys.argv[1]).version_str)' "$STAGE/pi/lc-firmware/lighting-controller.bin")"
  echo "==> Lighting controller firmware $LC_VER from $LC_FW"
  KEEP_LC_FW=()
fi
echo "$VERSION deployed $(date '+%Y-%m-%d %H:%M:%S') from $(hostname -s)" > "$STAGE/pi/DEPLOYED_VERSION"

echo "==> Connecting to $HOST"
"${SSH[@]}" "$HOST" true

echo "==> Copying files"
# --delete keeps ~/caltreat identical to the repo, apart from the virtualenv.
rsync -a --delete --exclude .venv --exclude __pycache__ "${KEEP_LC_FW[@]+"${KEEP_LC_FW[@]}"}" \
  -e "${SSH[*]}" "$STAGE/pi/" "$HOST:caltreat/"

echo "==> Installing dependencies (if changed) and restarting"
"${SSH[@]}" "$HOST" bash -s <<'EOF'
set -euo pipefail
cd ~/caltreat
# Only touch the virtualenv when the dependencies change, so a deploy works
# while the train has no internet.
hash="$(cat pyproject.toml poetry.lock | sha256sum | cut -d' ' -f1)"
if [[ ! -x .venv/bin/python || "$(cat .venv/.deployed-deps 2>/dev/null)" != "$hash" ]]; then
  ~/.local/bin/poetry sync --no-interaction
  echo "$hash" > .venv/.deployed-deps
else
  echo "Dependencies unchanged"
fi
mkdir -p ~/.config/systemd/user
cp caltreat.service ~/.config/systemd/user/caltreat.service
systemctl --user daemon-reload
systemctl --user enable --quiet caltreat.service
systemctl --user restart caltreat.service
EOF

echo "==> Waiting for $URL"
for _ in $(seq 30); do
  if curl -fs -o /dev/null --max-time 2 "$URL"; then
    # -k: the certificate is self-signed (see README, "Intercom and HTTPS")
    if curl -fsk -o /dev/null --max-time 3 "$HTTPS_URL"; then
      echo "Deployed $VERSION. Caltreat is up at $URL (intercom: $HTTPS_URL)"
    else
      echo "Deployed $VERSION. Caltreat is up at $URL, but $HTTPS_URL doesn't answer (no intercom). Logs:" >&2
      "${SSH[@]}" "$HOST" journalctl --user-unit caltreat -n 20 --no-pager | grep -i https >&2 || true
    fi
    exit 0
  fi
  sleep 1
done

echo "Caltreat didn't come up within 30 s. Recent logs:" >&2
"${SSH[@]}" "$HOST" journalctl --user-unit caltreat -n 40 --no-pager >&2 || true
exit 1
