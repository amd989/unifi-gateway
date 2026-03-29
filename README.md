# UniFi Gateway Emulator

A Python 3 daemon that emulates a Ubiquiti UniFi Gateway (UGW3) to a UniFi Controller. This allows non-Ubiquiti routers (OpenWRT, OPNSense, pfSense, or any Linux/FreeBSD router) to appear in the UniFi Controller UI and report network statistics.

## License

MIT — see [LICENSE](LICENSE).

## How It Works

The daemon runs alongside your non-Ubiquiti router and periodically sends "inform" packets to your UniFi Controller — the same binary protocol that real UniFi devices use. The controller sees a UGW3 gateway and displays its stats (interfaces, traffic, connected clients, routes, etc.) in the dashboard.

Your UniFi APs, switches, and other devices continue connecting to the controller normally. This project fills the "gateway" slot so the controller has a complete view of your network topology.

```
┌──────────────────┐     inform (HTTP POST)     ┌───────────────────┐
│  This Daemon     │ ─────────────────────────► │  UniFi Controller │
│  (emulates UGW3) │                            │                   │
└────────┬─────────┘                            │  Sees complete    │
         │ reads stats from                     │  network topology │
┌────────▼─────────┐                            │                   │
│  Your Router     │     UniFi APs/Switches ──► │                   │
│  (OPNSense/etc)  │     also inform normally   └───────────────────┘
└──────────────────┘
```

## Requirements

- A running UniFi Controller (self-hosted or Cloud Key)
- Network access from the daemon to the controller's `/inform` endpoint

## Installation

### Pre-built Binaries (Recommended)

Download the latest standalone binary from [Releases](https://github.com/amd989/unifi-gateway/releases). No Python installation required.

| Platform | Binary |
|---|---|
| Linux x86_64 | `unifi-gateway-linux-amd64` |
| Linux ARM64 (OpenWRT) | `unifi-gateway-linux-arm64` |
| Linux ARMv7 (OpenWRT) | `unifi-gateway-linux-armhf` |
| FreeBSD x86_64 (OPNSense/pfSense) | `unifi-gateway-freebsd-amd64` (built on FreeBSD 14.3 — compatible with OPNSense 25.7+ and pfSense CE 2.7+) |

```bash
# Download, make executable, and run
chmod +x unifi-gateway-linux-amd64
./unifi-gateway-linux-amd64 set-adopt -s http://your-controller:8080/inform
./unifi-gateway-linux-amd64 run
```

### Docker

```bash
docker pull ghcr.io/amd989/unifi-gateway:latest
docker compose up -d
```

See the [Docker section](#docker) below for details.

### From Source

```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy the sample config and edit it:

```bash
cp conf/unifi-gateway.sample.conf conf/unifi-gateway.conf
```

Edit `conf/unifi-gateway.conf`:

```ini
[gateway]
# Map UniFi logical ports to your real system interfaces
ports = [ { "ifname": "eth0", "name": "WAN", "type": "wan", "realif": "eth0" }, { "ifname": "eth1", "name": "LAN", "type": "lan", "realif": "br-lan" } ]

# Your LAN-side IP and MAC (how the controller identifies this device)
lan_ip = 192.168.1.1
lan_mac = aa:bb:cc:dd:ee:ff
```

The `realif` values must match your actual system interface names (check with `ip link` on Linux or `ifconfig` on FreeBSD).

### 3. Adopt to controller

```bash
python unifi_gateway.py set-adopt -s http://your-controller:8080/inform
```

The first run sends a discovery inform. Go to the UniFi Controller UI, find the new "USG" device, and click **Adopt**. Then run `set-adopt` again to complete the handshake:

```bash
python unifi_gateway.py set-adopt -s http://your-controller:8080/inform
```

### 4. Run

```bash
# Foreground (recommended for initial testing)
python unifi_gateway.py run

# Background daemon (Linux only)
python unifi_gateway.py start

# Stop / restart
python unifi_gateway.py stop
python unifi_gateway.py restart
```

## Platform Setup

### OpenWRT

The daemon works with OpenWRT's default dnsmasq DHCP server. Typical interface mapping:

```ini
ports = [ { "ifname": "eth0", "name": "WAN", "type": "wan", "realif": "eth0" }, { "ifname": "eth1", "name": "LAN", "type": "lan", "realif": "br-lan" } ]
```

DHCP leases are auto-detected at `/tmp/dhcp.leases`.

### OPNSense (FreeBSD)

The daemon auto-detects FreeBSD and uses `arp -an` for the neighbor table, `netstat -rn` for routing. It supports KEA DHCP (OPNSense's default DHCP server) with auto-detection of the lease file at `/var/db/kea/kea-leases4.csv`.

Typical interface mapping (check your interfaces with `ifconfig`):

```ini
ports = [ { "ifname": "eth0", "name": "WAN", "type": "wan", "realif": "vmx0" }, { "ifname": "eth1", "name": "LAN", "type": "lan", "realif": "vmx1" } ]
dhcp_lease_file = /var/db/kea/kea-leases4.csv
dhcp_lease_format = kea
```

Recommended setup using a Python virtual environment:

```bash
python3 -m venv /opt/unifi-gateway/venv
/opt/unifi-gateway/venv/bin/pip install -r requirements.txt
/opt/unifi-gateway/venv/bin/python unifi_gateway.py set-adopt -s http://your-controller:8080/inform
/opt/unifi-gateway/venv/bin/python unifi_gateway.py run
```

### pfSense (FreeBSD)

Same as OPNSense but uses ISC dhcpd instead of KEA:

```ini
dhcp_lease_file = /var/dhcpd/var/db/dhcpd.leases
dhcp_lease_format = isc
```

### Generic Linux (Debian, Ubuntu, etc.)

Works out of the box. Uses `/proc/net/dev` for multicast counters, `/proc/net/route` for default gateway, and `/etc/resolv.conf` for nameservers.

## Docker

### Build and run

```bash
docker build -t unifi-gateway .
docker run -d --name unifi-gateway --network host \
  -v $(pwd)/conf:/app/conf \
  unifi-gateway
```

### Docker Compose

```bash
docker compose up -d
```

For automatic adoption on first start, set the `UNIFI_ADOPT_URL` environment variable in `docker-compose.yml`:

```yaml
environment:
  - UNIFI_ADOPT_URL=http://your-controller:8080/inform
```

**Note:** `network_mode: host` is required so the daemon can read the host's real network interfaces. This only works on **Linux** — Docker Desktop on Windows/macOS does not support host networking.

## systemd Service

Copy the service file and the project:

```bash
sudo cp -r . /opt/unifi-gateway
sudo cp unifi-gateway.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now unifi-gateway
```

Check status:

```bash
sudo systemctl status unifi-gateway
sudo journalctl -u unifi-gateway -f
```

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `UNIFI_GW_CONFIG` | `conf/unifi-gateway.conf` | Path to config file |
| `UNIFI_GW_LOG_LEVEL` | `DEBUG` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `UNIFI_GW_LOG_FILE` | *(stderr)* | Log to file instead of stderr |
| `UNIFI_ADOPT_URL` | *(none)* | Controller inform URL for automatic adoption on start |
| `UNIFI_ADOPT_KEY` | *(none)* | Auth key for auto-adoption (optional, rarely needed) |

### Config File (`conf/unifi-gateway.conf`)

#### `[global]`

| Key | Default | Description |
|---|---|---|
| `pid_file` | `unifi-gateway.pid` | PID file path for daemon mode |
| `disable_broadcast` | `True` | Disable UDP multicast discovery |

#### `[gateway]`

| Key | Default | Description |
|---|---|---|
| `ports` | *(required)* | JSON list mapping UniFi ports to real interfaces |
| `lan_ip` | *(required)* | LAN IP address |
| `lan_mac` | *(required)* | LAN MAC address |
| `firmware` | `4.4.18.5052168` | Reported firmware version |
| `device` | `UGW3` | Device model string |
| `device_display` | `UniFi-Gateway-3` | Display name |
| `use_aes_gcm` | `False` | Use AES-GCM encryption (set by controller) |
| `hostname` | *(auto-detected)* | Override reported hostname |
| `dhcp_lease_file` | *(auto-detected)* | Path to DHCP lease file |
| `dhcp_lease_format` | `dnsmasq` | Lease format: `dnsmasq`, `isc`, or `kea` |
| `ping_target` | `ping.ubnt.com` | Host for latency measurement |
| `speedtest_file` | `./speedtest.json` | Path to speedtest results JSON |
| `platform` | `UNIFI-GW` | Platform string in discovery broadcasts |

#### `[provisioned]`

Populated automatically by the controller after adoption. Do not edit manually.

## Speed Test

The controller can trigger speed tests. Install `speedtest-cli` for this to work:

```bash
pip install speedtest-cli
```

Results are saved to `speedtest.json` and reported in the next inform cycle.

## Architecture

| Module | Purpose |
|---|---|
| `unifi_gateway.py` | Entry point, inform loop, controller response handling, adoption |
| `unifi_protocol.py` | TNBU binary protocol: AES-CBC/GCM encryption, zlib/snappy compression, JSON payload construction |
| `datacollector.py` | Cross-platform data collection via psutil (Linux + FreeBSD), with platform-specific fallbacks |
| `tools.py` | Helper functions for building `if_table` and `network_table` structures |
| `tlv.py` | TLV encoding for UDP discovery packets |
| `daemon.py` | Unix daemon (double-fork, PID file) |

## Acknowledgments

This project builds upon and was inspired by:

- [stephanlascar/unifi-gateway](https://github.com/stephanlascar/unifi-gateway) — original UniFi Gateway emulator
- [qvr/unifi-gateway](https://github.com/qvr/unifi-gateway) — Python 3 port, FreeBSD support, AES-GCM encryption

Protocol documentation:

- [jk-5/unifi-inform-protocol](https://github.com/jk-5/unifi-inform-protocol)
- [fxkr/unifi-protocol-reverse-engineering](https://github.com/fxkr/unifi-protocol-reverse-engineering)
