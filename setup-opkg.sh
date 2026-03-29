#!/bin/sh
set -e
echo "Adding unifi-gateway opkg feed..."
FEED="src/gz unifi-gateway https://amd989.github.io/unifi-gateway/openwrt"
if ! grep -q "unifi-gateway" /etc/opkg/customfeeds.conf 2>/dev/null; then
  echo "$FEED" >> /etc/opkg/customfeeds.conf
fi
opkg update
echo "Done! Run: opkg install unifi-gateway"
