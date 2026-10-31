#!/usr/bin/env bash
# Deploy the Caltreat app to the train's Raspberry Pi and restart it.
#
#   pi/deploy.sh            deploy origin/main (fetched first)
#   pi/deploy.sh <ref>      deploy a branch, tag or commit
#   pi/deploy.sh --local    deploy pi/ as it is in your working tree
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
echo "$VERSION deployed $(date '+%Y-%m-%d %H:%M:%S') from $(hostname -s)" > "$STAGE/pi/DEPLOYED_VERSION"

echo "==> Connecting to $HOST"
"${SSH[@]}" "$HOST" true

echo "==> Copying files"
# --delete keeps ~/caltreat identical to the repo, apart from the virtualenv.
rsync -a --delete --exclude .venv --exclude __pycache__ \
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
