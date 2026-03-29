#!/bin/bash
set -e
echo "Adding unifi-gateway APT repository..."
curl -fsSL https://amd989.github.io/unifi-gateway/gpg.key | gpg --dearmor -o /usr/share/keyrings/unifi-gateway.gpg
echo "deb [signed-by=/usr/share/keyrings/unifi-gateway.gpg] https://amd989.github.io/unifi-gateway stable main" > /etc/apt/sources.list.d/unifi-gateway.list
apt-get update
echo "Done! Run: sudo apt install unifi-gateway"
