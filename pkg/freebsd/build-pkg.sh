#!/bin/sh
# Build a FreeBSD .pkg from the PyInstaller binary
# Usage: ./build-pkg.sh <version>
set -e

VERSION="${1:?Usage: build-pkg.sh <version>}"
STAGING="$(mktemp -d)"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PKG_DIR/../.." && pwd)"

trap 'rm -rf "$STAGING"' EXIT

# Stage files into pkg layout
mkdir -p "$STAGING/usr/local/bin"
mkdir -p "$STAGING/usr/local/etc/rc.d"
mkdir -p "$STAGING/usr/local/etc/unifi-gateway"

cp "$ROOT_DIR/dist/unifi-gateway" "$STAGING/usr/local/bin/unifi-gateway"
chmod 755 "$STAGING/usr/local/bin/unifi-gateway"

cp "$PKG_DIR/rc.d/unifi_gateway" "$STAGING/usr/local/etc/rc.d/unifi_gateway"
chmod 755 "$STAGING/usr/local/etc/rc.d/unifi_gateway"

cp "$ROOT_DIR/conf/unifi-gateway.sample.conf" "$STAGING/usr/local/etc/unifi-gateway/unifi-gateway.conf"

# Generate manifest with version substituted
sed "s/\${VERSION}/$VERSION/" "$PKG_DIR/+MANIFEST" > "$STAGING/+MANIFEST"
cp "$PKG_DIR/+POST_INSTALL" "$STAGING/"
cp "$PKG_DIR/+POST_DEINSTALL" "$STAGING/"

# Build the package
pkg create -M "$STAGING/+MANIFEST" -r "$STAGING" -o "$ROOT_DIR/dist/"

echo "Package created: $ROOT_DIR/dist/unifi-gateway-${VERSION}.pkg"
