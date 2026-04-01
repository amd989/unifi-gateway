#!/bin/sh
# Build an OpenWRT .ipk (opkg) from Python source files.
# Ships pure Python — depends on python3 + python3-psutil + python3-pycryptodome
# from the OpenWRT feeds, so the package is architecture-independent.
#
# Usage: ./build-ipk.sh <version>
set -e

VERSION="${1:?Usage: build-ipk.sh <version>}"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PKG_DIR/../.." && pwd)"
WORK="$(mktemp -d)"

trap 'rm -rf "$WORK"' EXIT

# ── Data (installed files) ─────────────────────────────────────────
mkdir -p "$WORK/data/usr/bin"
mkdir -p "$WORK/data/usr/lib/unifi-gateway/collectors"
mkdir -p "$WORK/data/etc/init.d"
mkdir -p "$WORK/data/etc/unifi-gateway"

# Wrapper script
cp "$PKG_DIR/unifi-gateway.wrapper" "$WORK/data/usr/bin/unifi-gateway"
chmod 755 "$WORK/data/usr/bin/unifi-gateway"

# Python source
for f in unifi_gateway.py unifi_protocol.py tools.py tlv.py daemon.py datacollector.py; do
    cp "$ROOT_DIR/$f" "$WORK/data/usr/lib/unifi-gateway/$f"
done
for f in __init__.py base.py linux.py freebsd.py openwrt.py opnsense.py pfsense.py; do
    cp "$ROOT_DIR/collectors/$f" "$WORK/data/usr/lib/unifi-gateway/collectors/$f"
done

# Init script + sample config
cp "$PKG_DIR/unifi-gateway.init" "$WORK/data/etc/init.d/unifi-gateway"
chmod 755 "$WORK/data/etc/init.d/unifi-gateway"
cp "$ROOT_DIR/conf/unifi-gateway.sample.conf" "$WORK/data/etc/unifi-gateway/unifi-gateway.sample.conf"

# ── Control metadata ───────────────────────────────────────────────
mkdir -p "$WORK/control"
cat > "$WORK/control/control" <<EOF
Package: unifi-gateway
Version: ${VERSION}
Architecture: all
Depends: python3, python3-psutil, python3-pycryptodome
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

# ── Assemble .ipk ─────────────────────────────────────────────────
cd "$WORK/data" && tar czf "$WORK/data.tar.gz" .
cd "$WORK/control" && tar czf "$WORK/control.tar.gz" .
echo "2.0" > "$WORK/debian-binary"

mkdir -p "$ROOT_DIR/dist"
cd "$WORK" && ar r "$ROOT_DIR/dist/unifi-gateway_${VERSION}_all.ipk" \
    debian-binary control.tar.gz data.tar.gz

echo "Package created: dist/unifi-gateway_${VERSION}_all.ipk"
