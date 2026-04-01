#!/bin/sh
# Build an OpenWRT .apk (Alpine Package Keeper) from Python source files.
# For OpenWRT 25.12+ which uses apk instead of opkg.
# Ships pure Python — depends on python3 + python3-psutil + python3-pycryptodome.
#
# Usage: ./build-apk.sh <version>
#
# NOTE: Unlike Alpine's abuild, this builds the .apk by hand so we don't
# need the full OpenWRT SDK.  An .apk is two gzipped tars concatenated:
#   1) control.tar.gz  (.PKGINFO + scripts)
#   2) data.tar.gz     (installed files)
set -e

VERSION="${1:?Usage: build-apk.sh <version>}"
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PKG_DIR/../.." && pwd)"
WORK="$(mktemp -d)"

trap 'rm -rf "$WORK"' EXIT

# ── Data (installed files) ─────────────────────────────────────────
mkdir -p "$WORK/data/usr/bin"
mkdir -p "$WORK/data/usr/lib/unifi-gateway/collectors"
mkdir -p "$WORK/data/etc/init.d"
mkdir -p "$WORK/data/etc/unifi-gateway"

cp "$PKG_DIR/unifi-gateway.wrapper" "$WORK/data/usr/bin/unifi-gateway"
chmod 755 "$WORK/data/usr/bin/unifi-gateway"

for f in unifi_gateway.py unifi_protocol.py tools.py tlv.py daemon.py datacollector.py; do
    cp "$ROOT_DIR/$f" "$WORK/data/usr/lib/unifi-gateway/$f"
done
for f in __init__.py base.py linux.py freebsd.py openwrt.py opnsense.py pfsense.py; do
    cp "$ROOT_DIR/collectors/$f" "$WORK/data/usr/lib/unifi-gateway/collectors/$f"
done

cp "$PKG_DIR/unifi-gateway.init" "$WORK/data/etc/init.d/unifi-gateway"
chmod 755 "$WORK/data/etc/init.d/unifi-gateway"
cp "$ROOT_DIR/conf/unifi-gateway.sample.conf" "$WORK/data/etc/unifi-gateway/unifi-gateway.sample.conf"

# ── Data archive ───────────────────────────────────────────────────
cd "$WORK/data" && tar czf "$WORK/data.tar.gz" .

# Calculate installed size (in bytes)
INSTALLED_SIZE=$(du -sb "$WORK/data" | awk '{print $1}')

# ── Control metadata (.PKGINFO) ───────────────────────────────────
mkdir -p "$WORK/control"
cat > "$WORK/control/.PKGINFO" <<EOF
pkgname = unifi-gateway
pkgver = ${VERSION}-r0
pkgdesc = UniFi Gateway Emulator — emulates a UGW3 to a UniFi Controller
url = https://github.com/amd989/unifi-gateway
size = ${INSTALLED_SIZE}
arch = noarch
license = MIT
depend = python3
depend = python3-psutil
depend = python3-pycryptodome
maintainer = amd989 <amd989@users.noreply.github.com>
EOF

cat > "$WORK/control/.post-install" <<'EOF'
#!/bin/sh
if [ ! -f /etc/unifi-gateway/unifi-gateway.conf ]; then
    cp /etc/unifi-gateway/unifi-gateway.sample.conf /etc/unifi-gateway/unifi-gateway.conf
    echo "Created /etc/unifi-gateway/unifi-gateway.conf from sample — edit before starting!"
fi
/etc/init.d/unifi-gateway enable 2>/dev/null || true
echo "UniFi Gateway installed. Edit /etc/unifi-gateway/unifi-gateway.conf then run:"
echo "  /etc/init.d/unifi-gateway start"
EOF
chmod 755 "$WORK/control/.post-install"

cat > "$WORK/control/.pre-deinstall" <<'EOF'
#!/bin/sh
/etc/init.d/unifi-gateway stop 2>/dev/null || true
/etc/init.d/unifi-gateway disable 2>/dev/null || true
EOF
chmod 755 "$WORK/control/.pre-deinstall"

# ── Control archive ────────────────────────────────────────────────
cd "$WORK/control" && tar czf "$WORK/control.tar.gz" .PKGINFO .post-install .pre-deinstall

# ── Assemble .apk (control + data concatenated) ───────────────────
mkdir -p "$ROOT_DIR/dist"
cat "$WORK/control.tar.gz" "$WORK/data.tar.gz" \
    > "$ROOT_DIR/dist/unifi-gateway-${VERSION}-r0.apk"

echo "Package created: dist/unifi-gateway-${VERSION}-r0.apk"
