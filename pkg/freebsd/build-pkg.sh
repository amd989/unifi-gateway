#!/bin/sh
# Build a FreeBSD .pkg from the PyInstaller binary
# Usage: ./build-pkg.sh <version>
set -e

VERSION="${1:?Usage: build-pkg.sh <version>}"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PKG_DIR/../.." && pwd)"
STAGING="$(mktemp -d)"

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

# Generate plist (file manifest)
PLIST="$(mktemp)"
cat > "$PLIST" <<EOF
/usr/local/bin/unifi-gateway
/usr/local/etc/rc.d/unifi_gateway
/usr/local/etc/unifi-gateway/unifi-gateway.conf
EOF

# Generate manifest with version substituted
sed "s/\${VERSION}/$VERSION/" "$PKG_DIR/+MANIFEST" > "$STAGING/+MANIFEST"
cp "$PKG_DIR/+POST_INSTALL" "$STAGING/+POST_INSTALL"
cp "$PKG_DIR/+POST_DEINSTALL" "$STAGING/+POST_DEINSTALL"

# Build the package
mkdir -p "$ROOT_DIR/dist"
pkg create -m "$STAGING" -r "$STAGING" -p "$PLIST" -o "$ROOT_DIR/dist/"

rm -f "$PLIST"
echo "Package created in $ROOT_DIR/dist/"
ls -lh "$ROOT_DIR/dist/"*.pkg 2>/dev/null || echo "Warning: no .pkg file found"
