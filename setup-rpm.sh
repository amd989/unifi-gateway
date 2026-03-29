#!/bin/bash
set -e
echo "Adding unifi-gateway YUM/DNF repository..."
rpm --import https://amd989.github.io/unifi-gateway/gpg.key
cat > /etc/yum.repos.d/unifi-gateway.repo <<REPOEOF
[unifi-gateway]
name=unifi-gateway
baseurl=https://amd989.github.io/unifi-gateway/rpm/
enabled=1
gpgcheck=1
gpgkey=https://amd989.github.io/unifi-gateway/gpg.key
REPOEOF
echo "Done! Run: sudo dnf install unifi-gateway"
