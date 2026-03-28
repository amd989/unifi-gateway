# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python 3 daemon that emulates a Ubiquiti UniFi Gateway (UGW3) to a UniFi Controller. It allows non-Ubiquiti routers (OpenWRT, OPNSense, pfSense, etc.) to report network stats to the UniFi Controller UI.

Inspired by [stephanlascar/unifi-gateway](https://github.com/stephanlascar/unifi-gateway) and [qvr/unifi-gateway](https://github.com/qvr/unifi-gateway).

## Running

```bash
# Install dependencies
pip install -r requirements.txt

# First-time adoption to controller
python unifi_gateway.py set-adopt -s http://your.controller/inform

# Run in foreground (for development/debugging)
python unifi_gateway.py run

# Run as background daemon (Linux only)
python unifi_gateway.py start

# Stop / restart daemon
python unifi_gateway.py stop
python unifi_gateway.py restart

# Docker
docker compose up -d
```

## Architecture

The codebase has no tests and no build/lint system. It uses psutil for cross-platform data collection (Linux + FreeBSD).

**Core modules:**

- `unifi_gateway.py` — Entry point and main loop. Subclasses `Daemon`, handles the inform loop (periodic POSTs to the controller), processes controller responses (`noop`, `setparam`, `reboot`, `cmd`, `upgrade`, `setdefault`). Manages adoption workflow and config persistence. Supports graceful shutdown via SIGTERM/SIGINT. Configurable via environment variables.
- `unifi_protocol.py` — Implements the UniFi inform binary protocol. Handles AES-CBC and AES-GCM encryption/decryption, zlib/snappy compression, and builds the JSON inform payload (partial for unadopted, complete for adopted devices). Uses `MASTER_KEY` for unadopted communication, then switches to device-specific authkey after adoption.
- `datacollector.py` — Cross-platform data collection via psutil: interface traffic, IP addresses, MAC addresses, system stats (CPU/mem). Platform-specific code handles DHCP leases (dnsmasq, ISC dhcpd, and KEA DHCP formats), neighbor/ARP tables (ip neigh on Linux, arp -an on FreeBSD), default gateway routing, DNS nameservers from resolv.conf, latency via ping, and speedtest results. Host table merges ARP and DHCP data with enriched fields for controller topology.
- `tools.py` — Helper functions for building `if_table` and `network_table` structures that the controller expects, plus conversion utilities (MAC/IP string-to-array, netmask-to-CIDR, uptime via psutil).
- `tlv.py` — TLV (Type-Length-Value) encoding for UDP broadcast discovery messages.
- `daemon.py` — Generic Unix daemon class (double-fork, PID file management).

**Config:** `conf/unifi-gateway.conf` — INI format with `[global]`, `[gateway]`, and `[provisioned]` sections. The `ports` field maps emulated UniFi interface names (`eth0`/`eth1`/`eth2`) to real system interfaces via `realif`. Config is modified at runtime by controller responses and saved back. Optional keys: `hostname`, `dhcp_lease_file`, `dhcp_lease_format`, `ping_target`, `speedtest_file`, `platform`.

**Environment variables:** `UNIFI_GW_CONFIG` (config path), `UNIFI_GW_LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR), `UNIFI_GW_LOG_FILE` (log to file).

**Key protocol detail:** The inform packet format is `TNBU` magic + version + MAC + flags + IV + payload version + payload length + encrypted/compressed JSON payload. Flags indicate encryption type (CBC vs GCM) and compression (zlib vs snappy).

**Unhandled command log:** `conf/unhandled_commands.json` records controller operations we don't act on (unknown response types, commands, setparam keys, mgmt_cfg keys). This serves as a living TODO for missing features.

## Phase 2: Platform Abstraction & API Integration

### Architecture Refactor

The current `DataCollector` class uses `if self._platform` branches for platform-specific code. Before adding API integrations, refactor into a class hierarchy:

- `BaseCollector` — psutil-based cross-platform logic (interface stats, IPs, MACs, CPU/mem, speedtest, latency)
- `LinuxCollector` — `/proc/net/dev`, `ip neigh`, dnsmasq leases, `/proc/net/route`
- `OPNSenseCollector` — KEA DHCP, OPNSense REST API (ARP, Netflow, firewall)
- `OpenWRTCollector` — ubus/LuCI RPC (`getDHCPLeases`, `getHostHints`, `getConntrackList`)
- `PfSenseCollector` — similar to OPNSense, different API surface

Selection via config: `platform = auto | linux | opnsense | openwrt | pfsense`

### OPNSense API Integration

Requires API key+secret stored in config (`opnsense_api_key`, `opnsense_api_secret`, `opnsense_api_url`).

**Read-only (monitoring):**
- `GET /api/diagnostics/interface/get_arp` — richer ARP data with manufacturer, replaces `arp -an`
- `GET /api/diagnostics/networkinsight/top/FlowSourceAddrTotals/...` — per-IP traffic (rx/tx bytes) from Netflow, populates `host_table` traffic fields
- `GET /api/diagnostics/netflow/status` — verify Netflow is active

**Write (controller→router config push):**
- DHCP static reservations via KEA API
- Port forwarding / NAT rules via firewall API
- DNS host overrides via Unbound API
- Speed test triggering

### OpenWRT API Equivalents

Uses ubus JSON-RPC (authenticated via session token or uhttpd credentials).

- `ubus call luci-rpc getDHCPLeases` — DHCP leases
- `ubus call luci-rpc getHostHints` — hostname/MAC/IP mapping
- `ubus call luci-rpc getConntrackList` — per-device traffic via conntrack byte counts
- `ubus call network.interface dump` — interface details
- `/cgi-bin/luci/rpc/` — LuCI RPC for config changes

### Topology Limitation

Wired devices won't show topology connection lines through dumb/unmanaged switches. The controller needs UniFi switches to report per-port MAC forwarding tables. A single UniFi switch closest to the gateway solves this for most setups.
