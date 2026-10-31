#!/usr/bin/env bash
# One-time network setup for the train's Raspberry Pi (run on the Pi, with sudo). Safe to run again.
#
#   sudo ~/caltreat/setup/network.sh            set up (asks for what it needs)
#   sudo ~/caltreat/setup/network.sh --dry-run  only print what it would change
#   sudo ~/caltreat/setup/network.sh --undo     remove what it set up (the joined Wi-Fi's connection stays)
#
# What it sets up:
#  1. The app on http://caltreat.local/ and https://caltreat.local/ without a port: lets ordinary users open
#     ports 80 and up (sysctl net.ipv4.ip_unprivileged_port_start=80), so the app (a user service) can listen on
#     80 and 443 itself. 8080 and 8443 keep working.
#  2. Wi-Fi client + access point at once: the Pi joins the "caltreat" Wi-Fi (wlan0) AND serves its own
#     network ("Caltreat Cab", uap0, 10.42.0.1/24, DHCP) for phones on the train. The built-in Wi-Fi has one
#     radio, so the access point follows the joined network's channel (caltreat-ap-channel). With a USB Wi-Fi
#     adapter (AP_IFACE=wlan1) the access point gets its own radio instead.
#  3. Names: caltreat.local by mDNS (avahi, on every interface) and by the access point's DNS (for phones
#     whose mDNS doesn't work).
# The wired network for the lighting controllers (eth0, 192.168.50.1) is left alone.
#
# Settings (environment, or asked for when missing):
#   STA_SSID (caltreat)  STA_PASS      the Wi-Fi network to join; STA_PASS unset keeps the saved password
#   AP_SSID (Caltreat Cab)  AP_PASS    the Pi's own network; WPA2, 8-63 characters; unset keeps the saved one
#   AP_ADDR (10.42.0.1/24)  AP_CHANNEL (6: used until the joined network is found)  WIFI_COUNTRY (e.g. US)
#   STA_IFACE (wlan0)  AP_IFACE (uap0, or a USB adapter such as wlan1)
# Passwords go only into NetworkManager's connection files (/etc/NetworkManager/system-connections, root only).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CALTREAT_SETUP_ROOT:-}"  # tests: install into this folder instead of /
SYS="${CALTREAT_SYS:-/sys}"     # tests
DRY=0
UNDO=0
for a in "$@"; do
  case "$a" in
    -n | --dry-run) DRY=1 ;;
    --undo) UNDO=1 ;;
    -h | --help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (see --help)" >&2; exit 2 ;;
  esac
done

STA_SSID="${STA_SSID:-caltreat}"
AP_SSID="${AP_SSID:-Caltreat Cab}"
AP_ADDR="${AP_ADDR:-10.42.0.1/24}"
AP_CHANNEL="${AP_CHANNEL:-6}"
STA_IFACE="${STA_IFACE:-wlan0}"
AP_IFACE="${AP_IFACE:-uap0}"
WIFI_COUNTRY="${WIFI_COUNTRY:-}"
AP_CON=caltreat-ap
STA_CON=caltreat-sta
AP_IP="${AP_ADDR%/*}"
SHARED_RADIO=0
[[ "$AP_IFACE" == uap0 ]] && SHARED_RADIO=1

NMDIR="$ROOT/etc/NetworkManager"
AP_FILE="$NMDIR/system-connections/$AP_CON.nmconnection"
STA_FILE="$NMDIR/system-connections/$STA_CON.nmconnection"
SYSCTL_FILE="$ROOT/etc/sysctl.d/90-caltreat-ports.conf"
CONF_FILE="$ROOT/etc/caltreat/network.conf"
HOSTS_FILE="$ROOT/etc/caltreat/hosts"
DNSMASQ_FILE="$NMDIR/dnsmasq-shared.d/caltreat.conf"
NMCONF_FILE="$NMDIR/conf.d/90-caltreat.conf"
DISPATCH_FILE="$NMDIR/dispatcher.d/90-caltreat-ap"
SBIN="$ROOT/usr/local/sbin"
UNITS="$ROOT/etc/systemd/system"

say() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
# Run a command (or only print it with --dry-run). Arguments after "--secret" are hidden in what's printed.
run() {
  local shown=() hide=0 a
  for a in "$@"; do
    if [[ "$a" == --secret ]]; then hide=1; continue; fi
    if ((hide)); then shown+=("<hidden>"); hide=0; else shown+=("$(printf '%q' "$a")"); fi
  done
  if ((DRY)); then
    note "would run: ${shown[*]}"
  else
    note "\$ ${shown[*]}"
    local args=()
    for a in "$@"; do [[ "$a" == --secret ]] || args+=("$a"); done
    "${args[@]}"
  fi
}
CHANGED=()
# put_file PATH MODE [secret] < content: write PATH if its content or mode differs; prints what changes.
put_file() {
  local path="$1" mode="$2" secret="${3:-}" new
  new="$(cat; echo x)"
  new="${new%x}"
  if [[ -r "$path" ]] && [[ "$(cat "$path"; echo x)" == "${new}x" ]] && [[ "$(stat -c %a "$path")" == "$mode" ]]; then
    note "unchanged: $path"
    return 0
  fi
  CHANGED+=("$path")
  if ((DRY)); then
    note "would write: $path (mode $mode)"
  else
    note "writing: $path (mode $mode)"
  fi
  if [[ -z "$secret" ]]; then
    diff -u --label "$path (now)" --label "$path (new)" "$path" <(printf '%s' "$new") 2>/dev/null |
      tail -n +3 | sed 's/^/      /' || true
    if [[ ! -f "$path" ]]; then
      if (($(printf '%s' "$new" | wc -l) <= 12)); then printf '%s' "$new" | sed 's/^/      + /'
      else note "  (new file, $(printf '%s' "$new" | wc -l) lines: setup/files/$(basename "$path"))"; fi
    fi
  else
    note "  (holds a password: not shown)"
  fi
  ((DRY)) && return 0
  mkdir -p "$(dirname "$path")"
  local tmp="$path.caltreat-new"
  (umask 077; printf '%s' "$new" > "$tmp")
  chmod "$mode" "$tmp"
  mv -f "$tmp" "$path"
}
del_file() {
  [[ -e "$1" ]] || return 0
  CHANGED+=("$1")
  if ((DRY)); then note "would delete: $1"; else note "deleting: $1"; rm -f "$1"; fi
}
changed() { local p; for p in "$@"; do [[ " ${CHANGED[*]-} " == *" $p "* ]] && return 0; done; return 1; }
# Values in NetworkManager keyfiles (GKeyFile): backslash and a leading space are escaped.
kf_escape() {
  local v="${1//\\/\\\\}"
  [[ "$v" == " "* ]] && v="\\s${v:1}"
  printf '%s' "$v"
}
kf_get() { [[ -r "$2" ]] && sed -n "s/^$1=//p" "$2" | head -n 1; return 0; }
ask() {  # ask VAR "prompt" [silent]
  local reply
  if ((DRY)) || [[ ! -t 0 ]]; then return 1; fi
  if [[ -n "${3:-}" ]]; then read -rsp "$2" reply; echo; else read -rp "$2" reply; fi
  printf -v "$1" '%s' "$reply"
}
have() { command -v "$1" >/dev/null 2>&1; }
template() { sed -e "s/@STA_IFACE@/$STA_IFACE/g" -e "s/@AP_IFACE@/$AP_IFACE/g" "$HERE/files/$1"; }

if ((!DRY)) && [[ -z "$ROOT" && $EUID -ne 0 ]]; then
  echo "Run it with sudo: sudo $0 $*  (or --dry-run to see what it would do)" >&2
  exit 1
fi
if ((DRY)); then
  echo "Dry run: nothing is changed."
  [[ -z "$ROOT" && $EUID -ne 0 ]] && echo "(Without sudo it can't read the saved connection files: run it with sudo for exact differences.)"
fi

# ---------------------------------------------------------------- undo
if ((UNDO)); then
  say "Removing the Caltreat network setup"
  if have nmcli && nmcli -t -f NAME connection show 2>/dev/null | grep -qxF "$AP_CON"; then
    run nmcli connection delete "$AP_CON"
  fi
  del_file "$AP_FILE"
  if have systemctl; then
    for u in caltreat-ap-channel.timer caltreat-ap-channel.service caltreat-uap0.service; do
      if [[ -e "$UNITS/$u" ]]; then run systemctl disable --now "$u" || true; fi
    done
  fi
  for f in "$UNITS/caltreat-ap-channel.timer" "$UNITS/caltreat-ap-channel.service" "$UNITS/caltreat-uap0.service" \
    "$SBIN/caltreat-ap-channel" "$SBIN/caltreat-uap0" "$DISPATCH_FILE" "$DNSMASQ_FILE" "$NMCONF_FILE" \
    "$HOSTS_FILE" "$CONF_FILE" "$SYSCTL_FILE"; do
    del_file "$f"
  done
  if [[ -e $SYS/class/net/uap0 && "$AP_IFACE" == uap0 ]] && have iw; then run iw dev uap0 del || true; fi
  if have systemctl; then run systemctl daemon-reload; fi
  if have sysctl; then run sysctl -w net.ipv4.ip_unprivileged_port_start=1024; fi
  if have nmcli; then run nmcli general reload || true; fi
  say "Done. The app is back on ports 8080 / 8443 (it keeps trying 80 / 443 quietly)."
  note "The connection to the Wi-Fi \"$STA_SSID\" is kept (nmcli connection show; nmcli connection delete <name>)."
  exit 0
fi

# ---------------------------------------------------------------- checks
say "Checking the Pi"
if have hostname; then
  host="$(hostname)"
  if [[ "$host" != caltreat ]]; then
    warn "The hostname is \"$host\", so mDNS calls it $host.local. For caltreat.local: sudo hostnamectl set-hostname caltreat"
  else
    note "hostname: caltreat (mDNS name caltreat.local)"
  fi
fi
if have systemctl && ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
  warn "avahi-daemon (mDNS, caltreat.local) isn't running: sudo apt install avahi-daemon && sudo systemctl enable --now avahi-daemon"
fi
if [[ -f /etc/avahi/avahi-daemon.conf ]] && grep -Eq '^[[:space:]]*(allow|deny)-interfaces[[:space:]]*=' /etc/avahi/avahi-daemon.conf; then
  warn "/etc/avahi/avahi-daemon.conf limits avahi's interfaces (allow-/deny-interfaces): caltreat.local may not answer on $AP_IFACE"
fi
have nmcli || { echo "NetworkManager (nmcli) is needed: Raspberry Pi OS Bookworm or later has it." >&2; exit 1; }
have iw || { echo "iw is needed: sudo apt install iw" >&2; exit 1; }
if [[ ! -e "$SYS/class/net/$STA_IFACE" ]]; then
  warn "There's no $STA_IFACE (Wi-Fi). Is Wi-Fi blocked? rfkill list; sudo rfkill unblock wifi"
fi
if ((SHARED_RADIO)); then
  # brcmfmac on the Pi 3B+/4/5/Zero 2 W: "#{ managed } <= 1, #{ AP } <= 1, ... total <= 2, #channels <= 1"
  if iw list 2>/dev/null | grep -A4 'valid interface combinations' | grep -q 'AP'; then
    note "the built-in Wi-Fi can be a client and an access point at once (one channel)"
  else
    warn "iw list shows no client+access point combination for this Wi-Fi; plug in a USB Wi-Fi adapter and run with AP_IFACE=wlan1"
  fi
elif [[ ! -e "$SYS/class/net/$AP_IFACE" ]]; then
  warn "$AP_IFACE isn't there now; the access point starts when it's plugged in"
fi
reg="$(iw reg get 2>/dev/null | awk '/^country/ { sub(":", "", $2); print $2; exit }')"
if [[ -n "$WIFI_COUNTRY" ]]; then
  if [[ "$reg" != "$WIFI_COUNTRY" ]]; then
    if have raspi-config; then run raspi-config nonint do_wifi_country "$WIFI_COUNTRY"; else run iw reg set "$WIFI_COUNTRY"; fi
  fi
elif [[ -z "$reg" || "$reg" == 00 ]]; then
  warn "No Wi-Fi country is set (iw reg get: ${reg:-none}); the access point may not start. Run again with WIFI_COUNTRY=US"
else
  note "Wi-Fi country: $reg"
fi

# ---------------------------------------------------------------- passwords (all asked before anything is written)
if [[ -n "${AP_PASS+x}" ]]; then
  if ((${#AP_PASS} < 8 || ${#AP_PASS} > 63)); then
    echo "AP_PASS must be 8-63 characters (WPA2)." >&2
    exit 1
  fi
  AP_PSK="$(kf_escape "$AP_PASS")"
else
  AP_PSK="$(kf_get psk "$AP_FILE")"
  if [[ -z "$AP_PSK" ]]; then
    while :; do
      ask AP_PASS "Password for \"$AP_SSID\" (8-63 characters; phones on the train use it): " silent || {
        ((DRY)) && { AP_PSK="<password>"; break; }
        echo "Set AP_PASS (8-63 characters) for the Pi's own Wi-Fi." >&2
        exit 1
      }
      if ((${#AP_PASS} >= 8 && ${#AP_PASS} <= 63)); then AP_PSK="$(kf_escape "$AP_PASS")"; break; fi
      echo "8-63 characters, please."
    done
  fi
fi

# The Wi-Fi to join: a connection for it may exist already (Raspberry Pi Imager makes "preconfigured").
sta_con=""
while IFS= read -r line; do
  name="${line%:*}"
  type="${line##*:}"
  name="${name//\\:/:}"
  [[ "$type" == 802-11-wireless && "$name" != "$AP_CON" ]] || continue
  if [[ "$(nmcli -g 802-11-wireless.ssid connection show "$name" 2>/dev/null)" == "$STA_SSID" ]]; then
    sta_con="$name"
    break
  fi
done < <(nmcli -t -f NAME,TYPE connection show 2>/dev/null)
if [[ -z "$sta_con" || "$sta_con" == "$STA_CON" ]]; then
  if [[ -z "${STA_PASS+x}" ]]; then
    STA_PASS="$(kf_get psk "$STA_FILE")"
    if [[ -z "$STA_PASS" && ! -f "$STA_FILE" ]]; then
      if ! ask STA_PASS "Password of the Wi-Fi \"$STA_SSID\" (empty: an open network): " silent; then
        ((DRY)) || { echo "Set STA_PASS (the password of \"$STA_SSID\"; empty for an open network)." >&2; exit 1; }
        STA_PASS="<password>"
      fi
      STA_PASS="$(kf_escape "$STA_PASS")"
    fi
  else
    STA_PASS="$(kf_escape "$STA_PASS")"
  fi
  if [[ -n "$STA_PASS" && "$STA_PASS" != "<password>" ]] && ((${#STA_PASS} < 8)); then
    echo "The Wi-Fi password must be at least 8 characters (WPA2)." >&2
    exit 1
  fi
elif [[ -n "${STA_PASS+x}" ]] && ((${#STA_PASS} < 8)); then
  echo "The Wi-Fi password must be at least 8 characters (WPA2)." >&2
  exit 1
fi

# ---------------------------------------------------------------- 1. ports 80 and 443
say "Ports 80 and 443 for the app (a user service)"
put_file "$SYSCTL_FILE" 644 <<'EOF'
# Installed by Caltreat's pi/setup/network.sh: ordinary users may listen on ports 80 and up, so the Caltreat app
# (a systemd user service) serves http://caltreat.local/ (80) and https://caltreat.local/ (443) itself.
net.ipv4.ip_unprivileged_port_start=80
EOF
if have sysctl && [[ -z "$ROOT" ]] && [[ "$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null)" != 80 ]]; then
  run sysctl -p "$SYSCTL_FILE"
fi

# ---------------------------------------------------------------- 2. Wi-Fi
say "Settings for the helpers ($CONF_FILE)"
put_file "$CONF_FILE" 644 <<EOF
# Written by Caltreat's pi/setup/network.sh (no passwords here: they are in NetworkManager's connection files).
STA_IFACE=$(printf '%q' "$STA_IFACE")
STA_SSID=$(printf '%q' "$STA_SSID")
AP_IFACE=$(printf '%q' "$AP_IFACE")
AP_CON=$AP_CON
AP_SSID=$(printf '%q' "$AP_SSID")
AP_IP=$AP_IP
SHARED_RADIO=$SHARED_RADIO
EOF

say "Joining the Wi-Fi \"$STA_SSID\" ($STA_IFACE)"
if [[ -n "$sta_con" && "$sta_con" != "$STA_CON" ]]; then
  # A connection made earlier (e.g. by Raspberry Pi Imager: "preconfigured"): keep it and just adjust it, so
  # running this over that same Wi-Fi doesn't drop the SSH session.
  note "using the existing connection \"$sta_con\""
  args=(802-11-wireless.powersave 2 connection.autoconnect yes connection.autoconnect-retries 0
    connection.autoconnect-priority 10 connection.interface-name "$STA_IFACE")
  run nmcli connection modify "$sta_con" "${args[@]}"  # no reconnect: the change applies from the next one
  if [[ -n "${STA_PASS+x}" ]]; then
    run nmcli connection modify "$sta_con" wifi-sec.key-mgmt wpa-psk wifi-sec.psk --secret "$STA_PASS"
  fi
else
  uuid="$(kf_get uuid "$STA_FILE")"
  [[ -n "$uuid" ]] || uuid="$(cat /proc/sys/kernel/random/uuid)"
  put_file "$STA_FILE" 600 secret < <(
    printf '[connection]\nid=%s\nuuid=%s\ntype=wifi\ninterface-name=%s\nautoconnect=true\n' "$STA_CON" "$uuid" "$STA_IFACE"
    printf 'autoconnect-priority=10\n# 0: never give up (NetworkManager stops after 4 tries otherwise)\nautoconnect-retries=0\n\n'
    printf '[wifi]\nmode=infrastructure\nssid=%s\n# 2: power saving off (it makes the shared radio drop frames)\npowersave=2\n\n' "$(kf_escape "$STA_SSID")"
    [[ -n "$STA_PASS" ]] && printf '[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n' "$STA_PASS"
    printf '[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n'
  )
fi

say "The Pi's own Wi-Fi \"$AP_SSID\" ($AP_IFACE, $AP_ADDR)"
uuid="$(kf_get uuid "$AP_FILE")"
[[ -n "$uuid" ]] || uuid="$(cat /proc/sys/kernel/random/uuid)"
chan="$(kf_get channel "$AP_FILE")"  # keep the channel caltreat-ap-channel picked last
[[ -n "$chan" ]] || chan="$AP_CHANNEL"
band="bg"
((chan > 14)) && band=a
put_file "$AP_FILE" 600 secret < <(
  printf '[connection]\nid=%s\nuuid=%s\ntype=wifi\ninterface-name=%s\nautoconnect=true\n' "$AP_CON" "$uuid" "$AP_IFACE"
  printf 'autoconnect-priority=100\nautoconnect-retries=0\n\n'
  printf '[wifi]\nmode=ap\nssid=%s\nband=%s\n# follows the joined Wi-Fi (caltreat-ap-channel)\nchannel=%s\npowersave=2\n\n' \
    "$(kf_escape "$AP_SSID")" "$band" "$chan"
  printf '[wifi-security]\nkey-mgmt=wpa-psk\nproto=rsn;\npairwise=ccmp;\ngroup=ccmp;\npsk=%s\n\n' "$AP_PSK"
  printf '# "shared": DHCP and DNS (dnsmasq) for phones, and internet through the Pi when it has some\n'
  printf '[ipv4]\nmethod=shared\naddress1=%s\n\n[ipv6]\nmethod=disabled\n' "$AP_ADDR"
)

say "Access point interface and channel helpers"
if ((SHARED_RADIO)); then
  put_file "$SBIN/caltreat-uap0" 755 < <(template caltreat-uap0)
  put_file "$UNITS/caltreat-uap0.service" 644 < <(template caltreat-uap0.service)
else
  del_file "$UNITS/caltreat-uap0.service"
  del_file "$SBIN/caltreat-uap0"
fi
put_file "$SBIN/caltreat-ap-channel" 755 < <(template caltreat-ap-channel)
put_file "$UNITS/caltreat-ap-channel.service" 644 < <(template caltreat-ap-channel.service)
put_file "$UNITS/caltreat-ap-channel.timer" 644 < <(template caltreat-ap-channel.timer)
put_file "$DISPATCH_FILE" 755 < <(template 90-caltreat-ap)
put_file "$NMCONF_FILE" 644 <<EOF
# Installed by Caltreat's pi/setup/network.sh
[device-caltreat-ap]
match-device=interface-name:$AP_IFACE
# no random MAC addresses while scanning on the access point's interface
wifi.scan-rand-mac-address=no
EOF

# ---------------------------------------------------------------- 3. names
say "caltreat.local for phones on the Pi's Wi-Fi (DNS, besides mDNS)"
put_file "$HOSTS_FILE" 644 <<EOF
# Installed by Caltreat's pi/setup/network.sh, read by NetworkManager's dnsmasq on its shared networks (the access
# point and the boards' wired network). Each network gets its own address (localise-queries).
$AP_IP caltreat.local caltreat
192.168.50.1 caltreat.local caltreat
EOF
put_file "$DNSMASQ_FILE" 644 <<EOF
# Installed by Caltreat's pi/setup/network.sh: answer caltreat.local / caltreat with the Pi's address on the
# network that asks. Only on the networks the Pi serves; the joined Wi-Fi's DNS isn't touched.
addn-hosts=/etc/caltreat/hosts
localise-queries
EOF

# ---------------------------------------------------------------- apply
say "Applying"
if [[ -n "$ROOT" && -z "${CALTREAT_SETUP_APPLY:-}" ]]; then
  note "(CALTREAT_SETUP_ROOT is set: files only, nothing started)"
else
  if changed "$UNITS/caltreat-uap0.service" "$UNITS/caltreat-ap-channel.service" "$UNITS/caltreat-ap-channel.timer" "$SBIN/caltreat-uap0"; then
    run systemctl daemon-reload
  fi
  if ((SHARED_RADIO)); then
    systemctl is-enabled --quiet caltreat-uap0.service 2>/dev/null || run systemctl enable caltreat-uap0.service
    [[ -e "$SYS/class/net/$AP_IFACE" ]] || run systemctl start caltreat-uap0.service
  fi
  systemctl is-enabled --quiet caltreat-ap-channel.timer 2>/dev/null || run systemctl enable --now caltreat-ap-channel.timer
  if changed "$NMCONF_FILE" "$DNSMASQ_FILE" "$HOSTS_FILE"; then
    run nmcli general reload conf
  fi
  if changed "$STA_FILE"; then run nmcli connection load "$STA_FILE"; fi
  if changed "$AP_FILE"; then
    run nmcli connection load "$AP_FILE"
    # Moves the access point to the joined network's channel and starts it (wlan0 isn't touched: an SSH session
    # over it stays up).
    if ((DRY)); then note "would run: $SBIN/caltreat-ap-channel"; else run "$SBIN/caltreat-ap-channel" || true; fi
  elif changed "$DNSMASQ_FILE" "$HOSTS_FILE"; then
    # dnsmasq reads its files when the shared connection starts
    run nmcli connection up "$AP_CON" || true
  fi
  if [[ -n "$sta_con" ]]; then :; elif ! nmcli -t -f NAME connection show --active 2>/dev/null | grep -qxF "$STA_CON"; then
    note "NetworkManager joins \"$STA_SSID\" by itself when it's in range (and keeps trying)."
  fi
fi

say "Done"
cat <<EOF
    Phones: join the Wi-Fi "$AP_SSID", then open http://caltreat.local/ (or http://$AP_IP/).
    Intercom: https://caltreat.local/ (accept the certificate warning once per phone).
    Check:  nmcli device status        (wlan0 connected to "$STA_SSID", $AP_IFACE connected to $AP_CON, eth0)
            iw dev                     (wlan0 and $AP_IFACE on the same channel)
            journalctl -t caltreat-ap  (the access point's channel moves)
    Change a password or name: run this again with STA_PASS / AP_PASS / AP_SSID set.
EOF
