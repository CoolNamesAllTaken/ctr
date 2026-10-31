#!/bin/sh
# Push a firmware image to a chipper reader over the network (net_ota).
#   tools/ota_push.sh <host> [firmware.bin]
# <host> is the reader's mDNS name (chipper-reader-<mac>.local) or IP address;
# list readers with: dns-sd -B _http._tcp
set -e
host=${1:?usage: $0 <host> [firmware.bin]}
bin=${2:-.pio/build/crowpanel_p4/firmware.bin}
curl --fail --show-error --data-binary @"$bin" -H 'Content-Type: application/octet-stream' "http://$host/ota"
