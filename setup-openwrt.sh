#!/bin/sh
set -e
REPO="https://amd989.github.io/unifi-gateway/openwrt"
if command -v apk >/dev/null 2>&1; then
  echo "Detected apk (OpenWrt 25.12+)..."
  REPO_LINE="$REPO/apk"
  if ! grep -q "unifi-gateway" /etc/apk/repositories 2>/dev/null; then
    echo "$REPO_LINE" >> /etc/apk/repositories
  fi
  apk update
  echo "Done! Run: apk add unifi-gateway"
elif command -v opkg >/dev/null 2>&1; then
  echo "Detected opkg (OpenWrt pre-25.12)..."
  FEED="src/gz unifi-gateway $REPO/ipk"
  if ! grep -q "unifi-gateway" /etc/opkg/customfeeds.conf 2>/dev/null; then
    echo "$FEED" >> /etc/opkg/customfeeds.conf
  fi
  opkg update
  echo "Done! Run: opkg install unifi-gateway"
else
  echo "Error: neither apk nor opkg found. Is this OpenWrt?" >&2
  exit 1
fi
