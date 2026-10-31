#!/bin/sh
# Push a firmware image to a VMS over the network (net_ota).
#   tools/ota_push.sh <host> [firmware.bin]
# <host> is the sign's mDNS name (caltreat-vms-<mac>.local) or IP address;
# list signs with: dns-sd -B _http._tcp
set -e
host=${1:?usage: $0 <host> [firmware.bin]}
bin=${2:-.pio/build/waveshare_s3_rgb_matrix/firmware.bin}
curl --fail --show-error --data-binary @"$bin" -H 'Content-Type: application/octet-stream' "http://$host/ota"
