#!/bin/sh
# Build an OpenWRT .ipk from the PyInstaller binary
# Usage: ./build-ipk.sh <version> <arch>
#   arch: x86_64, aarch64_generic, arm_cortex-a7_neon-vfpv4, etc.
set -e

VERSION="${1:?Usage: build-ipk.sh <version> <arch>}"
ARCH="${2:?Usage: build-ipk.sh <version> <arch>}"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PKG_DIR/../.." && pwd)"
WORK="$(mktemp -d)"

trap 'rm -rf "$WORK"' EXIT

# Create data archive
mkdir -p "$WORK/data/usr/bin"
mkdir -p "$WORK/data/etc/init.d"
mkdir -p "$WORK/data/etc/unifi-gateway"

cp "$ROOT_DIR/dist/unifi-gateway" "$WORK/data/usr/bin/unifi-gateway"
chmod 755 "$WORK/data/usr/bin/unifi-gateway"

cp "$PKG_DIR/unifi-gateway.init" "$WORK/data/etc/init.d/unifi-gateway"
chmod 755 "$WORK/data/etc/init.d/unifi-gateway"

cp "$ROOT_DIR/conf/unifi-gateway.sample.conf" "$WORK/data/etc/unifi-gateway/unifi-gateway.sample.conf"

# Create control file
mkdir -p "$WORK/control"
cat > "$WORK/control/control" <<EOF
Package: unifi-gateway
Version: ${VERSION}
Architecture: ${ARCH}
Maintainer: amd989 <amd989@users.noreply.github.com>
Section: net
Priority: optional
Description: UniFi Gateway Emulator — emulates a UGW3 to a UniFi Controller
Homepage: https://github.com/amd989/unifi-gateway
License: MIT
EOF

cat > "$WORK/control/conffiles" <<EOF
/etc/unifi-gateway/unifi-gateway.sample.conf
EOF

cat > "$WORK/control/postinst" <<'EOF'
#!/bin/sh
if [ ! -f /etc/unifi-gateway/unifi-gateway.conf ]; then
    cp /etc/unifi-gateway/unifi-gateway.sample.conf /etc/unifi-gateway/unifi-gateway.conf
    echo "Created /etc/unifi-gateway/unifi-gateway.conf from sample — edit before starting!"
fi
/etc/init.d/unifi-gateway enable 2>/dev/null || true
echo "UniFi Gateway installed. Edit /etc/unifi-gateway/unifi-gateway.conf then run:"
echo "  /etc/init.d/unifi-gateway start"
EOF
chmod 755 "$WORK/control/postinst"

cat > "$WORK/control/prerm" <<'EOF'
#!/bin/sh
/etc/init.d/unifi-gateway stop 2>/dev/null || true
/etc/init.d/unifi-gateway disable 2>/dev/null || true
EOF
chmod 755 "$WORK/control/prerm"

# Build archives
cd "$WORK/data" && tar czf "$WORK/data.tar.gz" .
cd "$WORK/control" && tar czf "$WORK/control.tar.gz" .

# Build .ipk (ar archive)
echo "2.0" > "$WORK/debian-binary"
mkdir -p "$ROOT_DIR/dist"
cd "$WORK" && ar r "$ROOT_DIR/dist/unifi-gateway_${VERSION}_${ARCH}.ipk" \
    debian-binary control.tar.gz data.tar.gz

echo "Package created: dist/unifi-gateway_${VERSION}_${ARCH}.ipk"
