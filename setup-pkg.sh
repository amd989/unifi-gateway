#!/bin/sh
set -e
echo "Adding unifi-gateway FreeBSD pkg repository..."
mkdir -p /usr/local/etc/pkg/repos
cat > /usr/local/etc/pkg/repos/unifi-gateway.conf <<REPOEOF
unifi-gateway: {
  url: "https://amd989.github.io/unifi-gateway/freebsd",
  enabled: yes,
  signature_type: "none"
}
REPOEOF
pkg update
echo "Done! Run: pkg install unifi-gateway"
