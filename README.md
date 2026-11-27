# UniFi Gateway Emulator  [![Build & Release](https://github.com/amd989/unifi-gateway/actions/workflows/release.yml/badge.svg)](https://github.com/amd989/unifi-gateway/actions/workflows/release.yml)

A Python 3 daemon that emulates a Ubiquiti UniFi Gateway (UGW3) to a UniFi Controller. This allows non-Ubiquiti routers (OpenWRT, OPNSense, pfSense, or any Linux/FreeBSD router) to appear in the UniFi Controller UI and report network statistics.

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

### Debian / Ubuntu (apt)

```bash
curl -fsSL https://amd989.github.io/unifi-gateway/setup-apt.sh | sudo bash
sudo apt install unifi-gateway
```

### RHEL / Rocky / Fedora (dnf)

```bash
curl -fsSL https://amd989.github.io/unifi-gateway/setup-rpm.sh | sudo bash
sudo dnf install unifi-gateway
```

### OpenWRT

The OpenWRT packages ship pure Python source and depend on `python3`, `python3-psutil`, and `python3-pycryptodome` from the OpenWRT feeds — keeping the package tiny for flash-constrained devices.

```bash
# Auto-detects apk (25.12+) or opkg (older)
curl -fsSL https://amd989.github.io/unifi-gateway/setup-openwrt.sh | sh
```

Then install with whichever package manager your version uses:

```bash
# OpenWRT 25.12+ (apk)
apk add unifi-gateway

# OpenWRT pre-25.12 (opkg)
opkg install unifi-gateway
```

### FreeBSD / OPNSense / pfSense (pkg)

```bash
curl -fsSL https://amd989.github.io/unifi-gateway/setup-pkg.sh | sudo sh
sudo pkg install unifi-gateway
```

### Docker

```bash
docker pull ghcr.io/amd989/unifi-gateway:latest
docker compose up -d
```

See the [Docker section](#docker) below for details. To run in Docker on a separate machine against an OPNsense
router (no install on the router), see [Deploy with Docker against OPNsense](#deploy-with-docker-against-opnsense-no-install-on-the-router).

### Standalone Binaries

Pre-built binaries are also available from [Releases](https://github.com/amd989/unifi-gateway/releases) if you prefer manual installation.

| Platform | Binary |
|---|---|
| Linux x86_64 | `unifi-gateway-linux-amd64` |
| Linux ARM64 | `unifi-gateway-linux-arm64` |
| Linux ARMv7 | `unifi-gateway-linux-armhf` |
| FreeBSD x86_64 | `unifi-gateway-freebsd-amd64` |

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

### OPNsense, remotely via the REST API

To run the emulator in Docker on another machine and read OPNsense over its API instead of installing it
on the router, see [Deploy with Docker against OPNsense](#deploy-with-docker-against-opnsense-no-install-on-the-router).

### pfSense (FreeBSD)

Same as OPNSense but uses ISC dhcpd instead of KEA:

```ini
dhcp_lease_file = /var/dhcpd/var/db/dhcpd.leases
dhcp_lease_format = isc
```

### Generic Linux (Debian, Ubuntu, etc.)

Works out of the box. Uses `/proc/net/dev` for multicast counters, `/proc/net/route` for default gateway, and `/etc/resolv.conf` for nameservers.

## Deploy with Docker against OPNsense (no install on the router)

With `platform = opnsense-api` the emulator reads everything from the OPNsense REST API, so it runs in
a container on any Docker host (NAS, home server, VM, Raspberry Pi, Dokploy) instead of on the router. It only
makes outbound HTTP(S) calls, to the OPNsense API and to the controller's `/inform`, so it needs
**no host networking and no open ports**.

```
 Docker host                      OPNsense                    UniFi Network controller
 unifi-gateway  ──HTTPS GET /api/*──▶  web GUI / API
 container      ──HTTP POST /inform────────────────────────▶  shows a "USG"/gateway
```

What you need:

- A Docker host with Compose v2, or a platform like Dokploy or Portainer, that can reach the OPNsense web GUI (its LAN address, e.g. `https://192.168.1.1`) and the controller's inform port (usually `8080`).
- A current OPNsense release. This was developed against the 26.x API; older releases may lack some endpoints (Dnsmasq DHCP leases, for example, only exist since Dnsmasq became a DHCP server). The endpoints used are listed under [OPNsense API reference](#opnsense-api-reference).
- A UniFi Network controller, self-hosted or on a UniFi OS console, with **no real UniFi gateway** adopted on the same site.

### 1. Create an API user in OPNsense

1. **System → Access → Users → Add**. Name it, for example, `unifi-gateway`, set a long random password (it is never used to sign in), and save.
2. Edit the user, open **Effective Privileges → Edit**, and add:
   **Status: Interfaces**, **System: Gateways**, **Diagnostics: ARP Table**, **Lobby: Dashboard**,
   **Diagnostics: System Activity**, and the one for your DHCP server: **Services: DHCP: Kea(v4)**
   *or* **Services: Dnsmasq DNS/DHCP: Settings**. Save.
3. Under **API keys**, click **+**. Your browser downloads `apikey.txt`, which contains `key=` and `secret=`. Keep it safe: OPNsense won't show the secret again.

OPNsense privileges are per page, not read-only. The Kea and Gateways privileges also allow changes through
the API, so treat the key like a password. The emulator itself only makes GET requests.

### 2. The compose file

Everything is configured through environment variables, so this one file is the whole deployment. There's no
config file to copy over, and the adoption state goes in a named volume. It's the same as
[`docker-compose.opnsense.yml`](docker-compose.opnsense.yml) in this repo:

```yaml
services:
  unifi-gateway:
    image: ${UNIFI_GW_IMAGE:-ghcr.io/amd989/unifi-gateway:latest}
    # always fetch the newest :latest on (re)deploy instead of reusing the image already on the host
    pull_policy: always
    restart: unless-stopped
    environment:
      # OPNsense API (System > Access > Users > API keys)
      OPNSENSE_API_URL: ${OPNSENSE_API_URL:?OPNSENSE_API_URL is required}
      OPNSENSE_API_KEY: ${OPNSENSE_API_KEY:?OPNSENSE_API_KEY is required}
      OPNSENSE_API_SECRET: ${OPNSENSE_API_SECRET:?OPNSENSE_API_SECRET is required}
      OPNSENSE_VERIFY_SSL: ${OPNSENSE_VERIFY_SSL:-true}
      OPNSENSE_DHCP_BACKEND: ${OPNSENSE_DHCP_BACKEND:-auto}
      OPNSENSE_WAN_GATEWAY: ${OPNSENSE_WAN_GATEWAY:-}
      # Controller inform URL: the container adopts itself once you click Adopt in the controller
      UNIFI_ADOPT_URL: ${UNIFI_ADOPT_URL:?UNIFI_ADOPT_URL is required}
      # Emulated model: usg = UniFi Security Gateway (default), uxg = Gateway Lite (UniFi OS gateway).
      # Changing it on an adopted device means adopting again (README, "Choosing the emulated model").
      UNIFI_GW_MODEL: ${UNIFI_GW_MODEL:-}
      UNIFI_GW_DEVICE: ${UNIFI_GW_DEVICE:-}
      UNIFI_GW_DEVICE_DISPLAY: ${UNIFI_GW_DEVICE_DISPLAY:-}
      UNIFI_GW_FIRMWARE: ${UNIFI_GW_FIRMWARE:-}
      # Emulated ports. Default: usg eth0=WAN (wan), eth1=LAN (lan); uxg eth0=LAN, eth1=WAN.
      # lan_ip/lan_mac default to OPNsense's first LAN port.
      UNIFI_GW_PLATFORM: opnsense-api
      UNIFI_GW_PORTS: ${UNIFI_GW_PORTS:-}
      UNIFI_GW_LAN_IP: ${UNIFI_GW_LAN_IP:-}
      UNIFI_GW_LAN_MAC: ${UNIFI_GW_LAN_MAC:-}
      UNIFI_GW_HOSTNAME: ${UNIFI_GW_HOSTNAME:-}
      UNIFI_GW_LOG_LEVEL: ${UNIFI_GW_LOG_LEVEL:-INFO}
      # Adoption state (device key) is written here: keep this volume
      UNIFI_GW_CONFIG: /data/unifi-gateway.conf
    volumes:
      - unifi-gateway-data:/data
      # Optional, to verify OPNsense's self-signed certificate (set OPNSENSE_VERIFY_SSL=/certs/opnsense.pem).
      # Dokploy: add the cert as a file mount and mount it from ../files:
      # - ../files/opnsense.pem:/certs/opnsense.pem:ro

volumes:
  unifi-gateway-data:
```

### 3. The environment variables

Only four are required. This is the content for Dokploy's **Environment** tab or a `.env` file next to
the compose file:

```bash
OPNSENSE_API_URL=https://192.168.1.1
OPNSENSE_API_KEY=<key from apikey.txt>
OPNSENSE_API_SECRET=<secret from apikey.txt>
UNIFI_ADOPT_URL=http://192.168.1.10:8080/inform

# Optional
# UNIFI_GW_MODEL=uxg                 # emulate a Gateway Lite instead of a USG, see below
# OPNSENSE_VERIFY_SSL=false          # see the certificate note below
# OPNSENSE_DHCP_BACKEND=kea          # auto (default), kea, dnsmasq, isc, none
# OPNSENSE_WAN_GATEWAY=WAN_DHCP      # gateway name whose latency is reported
# UNIFI_GW_PORTS='[{"ifname":"eth0","name":"WAN","type":"wan","realif":"wan"},{"ifname":"eth1","name":"LAN","type":"lan","realif":"lan"},{"ifname":"eth2","name":"IOT","type":"lan2","realif":"opt1"}]'
# UNIFI_GW_LAN_MAC=aa:bb:cc:dd:ee:ff # default: read from OPNsense's first LAN port
# UNIFI_GW_IMAGE=ghcr.io/amd989/unifi-gateway:X.Y.Z   # pin a release
# UNIFI_GW_LOG_LEVEL=DEBUG
```

- **Inform URL:** `http://<controller-ip>:8080/inform`, for both a self-hosted Network application and a UniFi OS console.
- **Ports:** by default WAN is OPNsense's `wan` interface and LAN is `lan`. To add VLANs or other LANs, set
  `UNIFI_GW_PORTS` and wrap the JSON in single quotes. `realif` is the device (`igc0`, `vlan01`) or identifier
  (`wan`, `lan`, `opt1`) shown in **Interfaces → Overview**; `ifname` is the UniFi side and must be `eth0`, `eth1`, `eth2`…
- **LAN IP and MAC:** if you don't set them, they're read from OPNsense's first LAN port at start. The controller
  identifies the gateway by that MAC.
- **HTTPS certificate:** OPNsense's web GUI uses a self-signed certificate by default, which fails the default
  `OPNSENSE_VERIFY_SSL=true`. Either export the certificate (**System → Trust → Certificates**, the one selected
  under **System → Settings → Administration**), mount it (see the commented line in the compose file) and set
  `OPNSENSE_VERIFY_SSL=/certs/opnsense.pem`, or set `OPNSENSE_VERIFY_SSL=false`. With the certificate, the host in
  `OPNSENSE_API_URL` must match it, which usually means the router's hostname rather than its IP.
- Any `[opnsense]` option from the [reference](#opnsense-api-reference) that isn't in the compose file can be
  added as an extra line under `environment:`.

#### Choosing the emulated model

`UNIFI_GW_MODEL` picks the hardware the controller sees:

| `UNIFI_GW_MODEL` | Shows up as | Notes |
|---|---|---|
| `usg` *(default)* | UniFi Security Gateway (`UGW3`) | USG config format. UniFi Network 10.6 is the last release line that supports the USG |
| `uxg` (or `gateway-lite`) | Gateway Lite (`UXG`) | UniFi OS gateway, provisioned in the newer "udapi" format. See [Emulating a Gateway Lite](#emulating-a-gateway-lite-uxg-instead-of-a-usg) |

The preset sets the device, its display name, a starting firmware version and the default port layout. The
Gateway Lite numbers its ports the other way round: eth0 = LAN, eth1 = WAN. If you set `UNIFI_GW_PORTS` yourself,
follow that layout. A firmware version the controller upgrades the device to is kept across restarts.
`UNIFI_GW_DEVICE`, `UNIFI_GW_DEVICE_DISPLAY` and `UNIFI_GW_FIRMWARE` override the preset, for example to match the
version your controller lists as current, so it doesn't show an Update button. Other model codes are untested.

The controller can't change an adopted device's model. To switch an existing deployment, for example from USG to
Gateway Lite:

1. In the controller, remove (**forget**) the gateway: open the device, and in its settings choose Forget.
2. Delete the `unifi-gateway-data` volume (Dokploy: the service's volumes; Compose: `docker compose down -v`).
3. Set `UNIFI_GW_MODEL=uxg` and deploy again.
4. Adopt the new Gateway Lite in the controller (step 5).

Network settings such as DHCP ranges are stored in the controller, not in the gateway.

To check the key before deploying, from the Docker host:

```bash
curl -sS -u "<key>:<secret>" https://192.168.1.1/api/diagnostics/system/system_time
# {"uptime":"3 days, 04:12:33",...}   401 = wrong key/secret, 403 = missing privilege
```

Add `-k` if the certificate isn't set up yet.

### 4. Deploy

**Dokploy**

1. In a project, **Create Service → Compose**. As the source, either paste the compose file above (*Raw*), or
   connect this GitHub repository and set **Compose Path** to `./docker-compose.opnsense.yml`.
   The default, `./docker-compose.yml`, is the on-router setup (host networking, local interfaces) and won't
   work here: its log shows `Using platform collector: linux`.
2. In the service's **Environment** tab, paste the variables from step 3 and save.
3. Click **Deploy** and open **Logs**. Continue with step 5.

The service doesn't need a domain or published ports. Dokploy keeps the `unifi-gateway-data` volume across
redeploys.

**Plain Docker Compose** (Portainer stacks work the same way)

```bash
mkdir unifi-gateway && cd unifi-gateway
# save the compose file above as docker-compose.yml, the variables as .env
chmod 600 .env
docker compose up -d && docker compose logs -f
```

### 5. Adopt in the controller

Adoption runs on its own:

1. The container announces itself, logs `Controller has received initial inform, adopt from GUI`, and exits.
   Docker restarts it and it announces again. Until you adopt, these restarts are expected, and Dokploy may show
   the service as restarting.
2. In the controller, a new gateway (a USG, or a Gateway Lite) appears as *Pending Adoption*. Click **Adopt**.
3. On the next restart (Docker waits a little longer between each one) the log shows
   `setparam received, device now adopted` and `Device adopted, starting inform loop`. The device key is saved in
   the `unifi-gateway-data` volume, and from then on the container keeps running.

You can leave `UNIFI_ADOPT_URL` set: once the device is adopted it's ignored.

### 6. Check that it works

A healthy start looks like this:

```
INFO : unifi-gateway build 0468dd5, emulating UGW3 (UniFi-Gateway-3), firmware 4.4.57.5578372
INFO : Using platform collector: opnsense-api
INFO : Using OPNsense DHCP backend: kea
INFO : lan_mac not set, using the OPNsense lan MAC aa:bb:cc:dd:ee:ff
INFO : Device adopted, starting inform loop (interval=10s)
```

The informs themselves only show at `UNIFI_GW_LOG_LEVEL=DEBUG`. In the controller, the gateway should show
WAN IP, throughput, latency and clients within a minute.

| Symptom | Cause |
|---|---|
| still shows as a USG after setting `UNIFI_GW_MODEL=uxg` | check the first log line, `unifi-gateway build <sha>, emulating …`. If it says `UGW3` or doesn't appear, the old image or compose file is still in use: redeploy (or update a pasted compose file). If it says `UXG`, the controller still has the adopted USG record for this MAC: forget it and delete the volume (see [Choosing the emulated model](#choosing-the-emulated-model)) |
| the WAN1 panel shows your LAN IP and MAC, or the log warns `The WAN port is eth0, but the controller expects …` | the port layout doesn't match the model: the USG expects the WAN on `eth0`, the Gateway Lite on `eth1`. The default layouts follow `UNIFI_GW_MODEL` by themselves; if you set `UNIFI_GW_PORTS`, put the WAN on the port the model expects |
| `Using platform collector: linux`, then `No [gateway] ports configured` | Dokploy deployed `./docker-compose.yml` instead of `./docker-compose.opnsense.yml`: fix the Compose Path (step 4) |
| deploy fails: `required variable … is missing a value` | one of the four required variables isn't set (step 3) |
| `HTTP 401 - check api_key/api_secret` | wrong key or secret |
| `HTTP 403 - the API user lacks the privilege` | add the privilege for the endpoint named in the log (step 1) |
| `CERTIFICATE_VERIFY_FAILED` / hostname mismatch | see the certificate note in step 3 |
| `timed out` / `Connection refused` to OPNsense | wrong URL or port, or a firewall rule between the Docker host and the GUI |
| `Could not determine MAC for eth0 (…)` | a `realif` in `UNIFI_GW_PORTS` doesn't match a device or identifier from Interfaces → Overview |
| `Authentication failed -- wrong authkey or device removed from controller?`, or inform errors after you removed the device in the controller | adoption state is stale: delete the `unifi-gateway-data` volume (Dokploy: the service's volumes; Compose: `docker compose down -v`) and deploy again to re-adopt. All other settings come from the environment, so nothing else is lost |
| no clients listed | missing DHCP or ARP privilege, or the DHCP backend isn't detected: set `OPNSENSE_DHCP_BACKEND=kea` or `dnsmasq` |
| WAN latency 0 | gateway monitoring is off in OPNsense (System → Gateways), or set `OPNSENSE_WAN_GATEWAY` to the gateway name |

### 7. Updating and pinning

Dokploy: click **Deploy** again, which pulls the image anew. Compose: `docker compose pull && docker compose up -d`.

Tags: `:latest` is the newest release, `:X.Y.Z` / `:X.Y` a specific one. To pin a version, set
`UNIFI_GW_IMAGE=ghcr.io/amd989/unifi-gateway:X.Y.Z`.

Updating or recreating the container keeps the device adopted, as long as the `unifi-gateway-data` volume stays.

Not included: speed tests started from the controller need `speedtest-cli`, which the image doesn't contain.
They're logged as unavailable.

### OPNsense API reference

| Data | Endpoint | Privilege |
|---|---|---|
| Interfaces, MACs, IPv4, gateways, link speed, counters | `/api/interfaces/overview/interfaces_info/1` | Status: Interfaces |
| WAN latency (dpinger) | `/api/routes/gateway/status` | System: Gateways |
| Neighbors | `/api/diagnostics/interface/get_arp` | Diagnostics: ARP Table |
| DHCP leases (Kea / Dnsmasq / ISC plugin) | `/api/kea/leases4/search`, `/api/dnsmasq/leases/search`, `/api/dhcpv4/leases/search_lease` | Services: DHCP: Kea(v4) / Services: Dnsmasq DNS/DHCP: Settings / the ISC DHCPv4 leases page |
| Uptime, memory, hostname | `/api/diagnostics/system/system_time`, `system_resources`, `system_information` | Lobby: Dashboard |
| CPU (sampled every `cpu_interval`, default 60 s) | `/api/diagnostics/activity/get_activity` | Diagnostics: System Activity |

OPNsense privileges are per page, not read-only: some of these (Kea, Gateways) also allow changes through
the API. Only grant the DHCP privilege for the server you use, and keep the key secret.
A 401 or 403 is logged as an error naming the endpoint.

Options go in an `[opnsense]` section (see `conf/unifi-gateway.opnsense.sample.conf`) or environment variables:

| Key | Env | Default | Description |
|---|---|---|---|
| `api_url` | `OPNSENSE_API_URL` | *(required)* | e.g. `https://192.168.1.1` |
| `api_key` / `api_secret` | `OPNSENSE_API_KEY` / `OPNSENSE_API_SECRET` | *(required)* | API key pair |
| `api_secret_file` | `OPNSENSE_API_SECRET_FILE` | | Read the secret from a file (Docker secrets) |
| `verify_ssl` | `OPNSENSE_VERIFY_SSL` | `true` | `true`, `false`, or a path to a CA/cert file (for the self-signed GUI cert) |
| `timeout` | | `10` | HTTP timeout in seconds |
| `dhcp_backend` | `OPNSENSE_DHCP_BACKEND` | `auto` | `auto`, `kea`, `dnsmasq`, `isc`, `none` |
| `wan_gateway` | `OPNSENSE_WAN_GATEWAY` | *(WAN's gateway)* | Gateway name whose dpinger delay is the reported latency |
| `latency_source` | | `gateway` | `gateway` (dpinger) or `ping` (from the container) |
| `nameservers` | `OPNSENSE_NAMESERVERS` | *(DNS received on WAN, else LAN IP)* | Comma-separated |
| `cpu_interval` | | `60` | Seconds between CPU samples (runs `top` on OPNsense for ~2 s); `0` disables |


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

**Note:** this setup runs the emulator *on the router itself*, so `network_mode: host` is required for the daemon to read the host's real network interfaces. For OPNsense you can instead run it on any Docker host without host networking, see [Deploy with Docker against OPNsense](#deploy-with-docker-against-opnsense-no-install-on-the-router). This only works on **Linux** — Docker Desktop on Windows/macOS does not support host networking.

## Service Management

Packages automatically install and enable the service. After configuring, just start it:

| Platform | Start | Logs |
|---|---|---|
| systemd (Debian/Ubuntu/RHEL) | `sudo systemctl start unifi-gateway` | `journalctl -u unifi-gateway -f` |
| rc.d (FreeBSD/OPNSense) | `sudo service unifi_gateway start` | `tail -f /var/log/unifi-gateway.log` |
| procd (OpenWRT) | `/etc/init.d/unifi-gateway start` | `logread -e unifi-gateway` |

Updates are handled by your package manager: `apt upgrade`, `dnf upgrade`, `opkg upgrade`, `apk upgrade`, or `pkg upgrade`.

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `UNIFI_GW_CONFIG` | `conf/unifi-gateway.conf` | Path to config file |
| `UNIFI_GW_LOG_LEVEL` | `DEBUG` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `UNIFI_GW_LOG_FILE` | *(stderr)* | Log to file instead of stderr |
| `UNIFI_ADOPT_URL` | *(none)* | Controller inform URL for automatic adoption on start |
| `UNIFI_ADOPT_KEY` | *(none)* | Auth key for auto-adoption (optional, rarely needed) |
| `UNIFI_GW_MODEL` | *(none)* | Emulated model preset: `usg` or `uxg` (Gateway Lite). Sets `device`, `device_display`, a starting `firmware` (kept once the controller upgrades it) and the default port layout |
| `UNIFI_GW_PLATFORM`, `UNIFI_GW_PORTS`, `UNIFI_GW_LAN_IP`, `UNIFI_GW_LAN_MAC`, `UNIFI_GW_HOSTNAME`, `UNIFI_GW_DEVICE`, `UNIFI_GW_DEVICE_DISPLAY`, `UNIFI_GW_FIRMWARE`, `UNIFI_GW_PING_TARGET`, `UNIFI_GW_EXTRA_INFORM`, `UNIFI_GW_DISABLE_BROADCAST` | *(none)* | Override the matching `[gateway]`/`[global]` key on every start; blank values are ignored. With these, no config file is needed: a missing one is created with defaults. Adoption state (`is_adopted`, keys, `[provisioned]`) always stays with the daemon |

### Config File (`conf/unifi-gateway.conf`)

#### `[global]`

| Key | Default | Description |
|---|---|---|
| `pid_file` | `unifi-gateway.pid` | PID file path for daemon mode |
| `disable_broadcast` | `True` | Disable UDP multicast discovery |

#### `[gateway]`

| Key | Default | Description |
|---|---|---|
| `ports` | *(required)* | JSON list mapping UniFi ports to real interfaces. Optional per port: `mac` (overrides detection, e.g. for PPPoE interfaces without a MAC) |
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
| `platform` | `auto` | Collector: `auto`, `linux`, `openwrt`, `opnsense`, `opnsense-api` (remote, see above), `pfsense`, `freebsd`. Also sent as the platform string in discovery broadcasts |
| `extra_inform` | *(none)* | JSON object merged into every complete inform last (testing/overrides). An invalid value is logged and ignored |

#### `[openwrt]`: DHCP from the controller (optional, off by default)

On OpenWrt, the daemon can apply the DHCP settings you make in the UniFi controller to `/etc/config/dhcp`:
the pool (start, size, lease time) and IPv4 reservations (fixed IPs) of **one** subnet. It reads the controller's
`system_cfg` in either format: the USG tree, or the udapi format a UXG (Gateway Lite) receives. Everything else the
controller sends (firewall, VLANs, other networks, DHCP options) is ignored.

| Key | Default | Description |
|---|---|---|
| `dhcp_sync_enabled` | `False` | Turn the sync on |
| `dhcp_sync_mode` | `apply` | `apply`, or `dry-run` to only log the planned changes |
| `dhcp_sync_network` | *(required)* | The one subnet that may be changed, e.g. `192.168.0.0/24`. It must match OpenWrt's `network.lan` address |
| `dhcp_sync_max_changes` | `128` | Refuse a run that would change more than this |
| `dhcp_config_path` | `/etc/config/dhcp` | UCI DHCP file |
| `dhcp_backup_dir` | `/etc/unifi-gateway/backups` | Copies of the DHCP file before each change (last 10 kept) |
| `dhcp_state_file` | `/etc/unifi-gateway/dhcp-sync-state.json` | Summary of the last applied run |
| `dhcp_controller_url` | *(none)* | e.g. `https://192.168.1.10:8443` (must be `https://`). With the credentials file, reservation names come from the controller's client list |
| `dhcp_controller_credentials_file` | *(none)* | JSON `{"username": ..., "password": ...}` for a controller account; keep it `chmod 600` |
| `dhcp_controller_site` | `default` | Controller site name |
| `dhcp_controller_cert_sha256` | *(none)* | SHA-256 fingerprint of the controller's certificate (hex, colons optional). Use this for the usual self-signed certificate |
| `dhcp_controller_ca_file` | *(none)* | CA bundle to verify the controller's certificate. Without this and without a fingerprint, the system CAs are used |

How it stays safe:
- **Ownership:** reservations it creates or takes over are marked `unifi_managed '1'` and `unifi_mac`. Only those are
  changed or deleted.
- **Hand-made entries:** these are never touched. A reservation from the controller that conflicts with one aborts
  the run.
- **Rollback:** if `dnsmasq` doesn't accept the result, the previous file is restored.
- **Names:** first the controller's local DNS record, then the client's name in the controller, then `host-<mac>`.
  An existing reservation keeps its current name.
- **Placeholder:** the controller's built-in placeholder lease (`00:00:00:00:00:00` → `192.168.1.20`) is skipped.
- **Start with a test run:** begin with `dhcp_sync_mode = dry-run` and read the planned changes in the log.

**Controller client names (optional).** The controller's certificate is always checked before the credentials are
sent. A standalone controller usually has a self-signed certificate, so pin it. Get its fingerprint with:

```bash
openssl s_client -connect 192.168.1.10:8443 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```

and set `dhcp_controller_cert_sha256` to the value after `Fingerprint=`. When the controller gets a new
certificate, update the fingerprint. Until then the lookup logs a warning and new reservations are named
`host-<mac>`; existing ones keep their names.

The lookup uses the standalone Network application's API (`/api/login`, `/api/s/<site>/...`). UniFi OS consoles
(UDM, Cloud Key Gen2+, UniFi OS Server) use other paths, so there the lookup fails and names fall back to the DNS
records and `host-<mac>`.

#### `[provisioned]`

Populated automatically by the controller after adoption. Do not edit manually.

## Emulating a Gateway Lite (UXG) instead of a USG

UniFi Network 10.6 is the last release line that supports the USG (`UGW3`). With `device = UXG` the daemon poses
as a UniFi Gateway Lite, a supported UniFi OS gateway, and the controller provisions it with the UniFi OS ("udapi")
config format instead of the USG one.

In Docker, `UNIFI_GW_MODEL=uxg` does all of this (see [Choosing the emulated model](#choosing-the-emulated-model)).
In a config file:

```ini
[gateway]
device = UXG
device_display = Gateway Lite
# use the version the controller lists as current for UXG, or it shows an Update button
firmware = 6.0.17.36539
# Gateway Lite ports: eth0 = LAN, eth1 = WAN (optional per port: "mac", "port_idx", "media", "speed" in Mb/s)
ports = [ { "ifname": "eth0", "name": "LAN", "type": "lan", "realif": "br-lan" },
          { "ifname": "eth1", "name": "WAN", "type": "wan", "realif": "wan" } ]
```

For any model that isn't `UGW*`, the complete inform also carries:

- `port_table`: one entry per configured port with `port_idx` and `name` = the emulated ifname, plus live counters
  and rates (`rx_bytes-r`/`tx_bytes-r` in bytes/s, `rx_rate`/`tx_rate` in bits/s). Without it the controller logs
  `Could not find port information for NetworkConf interface eth1` and sends no interfaces or DHCP config. The WAN
  entry becomes the controller's WAN stats (throughput, "Down/Up Activity"). Speed is the detected link speed
  (psutil), or the port's `speed`; the media follows from it (`FE`, `GE`, `2.5GE`, `10GE`) unless `media` is set.
- `uptime_stats.WAN`: uptime, availability and latency from the ping to `ping_target`. The controller marks the
  WAN online when `uptime > 0`, and takes latency/availability from the first `alerting_monitors` entry. The UI's
  "Connection" is Excellent at availability 100 and latency < 50 ms, and Failed when those values are missing.
- `udapi_version.versionDetail`: the udapi features the "firmware" supports. The controller only sends config
  sections listed here (`services/dhcpServers`, `interfaces`, `vlans`, ...).
- `leaseExpiry` per `ipv4_active_leases` entry, and `active_dhcp_lease_count` on the LAN `network_table` entry.

The config arrives as `setparam` → `system_cfg`, a JSON string `{"udapi": {...}, "ubntconf": "..."}`.
`udapi.services.dhcpServers[]` holds each network's pool, lease time, DNS, domain, options and
`staticLeases[{"id": MAC, "addresses": [IP]}]`. It's recorded in `unhandled_commands.json` and contains secrets
(tokens, certificates, password hashes). Moving an existing USG to UXG means forgetting the device and adopting
it again. Tested against UniFi Network 10.6.109 only, not UniFi OS Server or Network 11.

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

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

This project builds upon and was inspired by:

- [stephanlascar/unifi-gateway](https://github.com/stephanlascar/unifi-gateway) — original UniFi Gateway emulator
- [qvr/unifi-gateway](https://github.com/qvr/unifi-gateway) — Python 3 port, FreeBSD support, AES-GCM encryption

Protocol documentation:

- [jk-5/unifi-inform-protocol](https://github.com/jk-5/unifi-inform-protocol)
- [fxkr/unifi-protocol-reverse-engineering](https://github.com/fxkr/unifi-protocol-reverse-engineering)
