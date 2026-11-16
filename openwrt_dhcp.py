"""Constrained UniFi DHCP provisioning for OpenWrt.

Applies the DHCP pool (start, limit, lease time) and IPv4 static reservations from the
controller's ``system_cfg`` to OpenWrt's /etc/config/dhcp. Both payload formats are
understood: the USG tree (``service.dhcp-server.shared-network-name``) and the UniFi OS
"udapi" format (``udapi.services.dhcpServers``) sent to UniFi OS gateways such as the UXG.
Everything else in ``system_cfg`` is ignored. Off by default; see the README ([openwrt]).

Safety rules: only one configured subnet is touched, only reservations this sync created
or adopted (marked ``unifi_managed``) are changed or removed, conflicts with hand-made
entries abort the run, a change limit applies, and a failed dnsmasq reload restores the
previous file.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request
import ssl


LOGGER = logging.getLogger("unifi-gateway")
MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


class DhcpSyncError(RuntimeError):
    pass


def normalize_mac(value):
    value = str(value or "").strip().lower().replace("-", ":")
    return value if MAC_RE.fullmatch(value) else None


def mac_values(value):
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = re.split(r"[\s,]+", str(value or "").strip())
    return [mac for item in raw_values if (mac := normalize_mac(item))]


def safe_hostname(value, fallback):
    value = str(value or fallback).strip().lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value).strip("-")[:63]
    return value or fallback


def generated_mapping_name(name, mac):
    compact = mac.replace(":", "")
    return name in {compact, mac.replace(":", "-"), "host-" + compact}


def host_matches(record, wanted):
    values = record["values"]
    return (
        record["macs"] == [wanted["mac"]]
        and record["ip"] == wanted["ip"]
        and str(values.get("name", "")).lower() == wanted["name"].lower()
        and str(values.get("dns", "1")) == "1"
        and str(values.get("unifi_managed", "0")) == "1"
        and normalize_mac(values.get("unifi_mac")) == wanted["mac"]
    )


def domain_matches(values, wanted, mac):
    old_name = str(values.get("name", ""))
    suffix = ".lan" if old_name.endswith(".lan") else ""
    expected_name = wanted["name"] + suffix
    return (
        str(values.get("ip", "")) == wanted["ip"]
        and old_name.lower() == expected_name.lower()
        and str(values.get("unifi_managed", "0")) == "1"
        and normalize_mac(values.get("unifi_mac")) == mac
    )


def _first_subnet(system_cfg, allowed_network):
    try:
        shared = system_cfg["service"]["dhcp-server"]["shared-network-name"]
    except (KeyError, TypeError):
        raise DhcpSyncError("system_cfg has no DHCP shared network")

    candidates = []
    for shared_value in shared.values():
        for subnet_name, subnet in shared_value.get("subnet", {}).items():
            try:
                network = ipaddress.ip_network(subnet_name, strict=False)
            except ValueError:
                continue
            if network == allowed_network:
                candidates.append(subnet)
    if len(candidates) != 1:
        raise DhcpSyncError(
            "expected exactly one DHCP subnet for %s, found %d"
            % (allowed_network, len(candidates))
        )
    return candidates[0]


def _udapi_payload(system_cfg):
    """Accept an inform system_cfg envelope or a captured bare udapi document."""
    if "udapi" in system_cfg:
        data = system_cfg["udapi"]
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise DhcpSyncError("udapi is not valid JSON") from error
        if not isinstance(data, dict):
            raise DhcpSyncError("udapi must be an object")
        return data
    if isinstance(system_cfg.get("services"), dict) and "dhcpServers" in system_cfg["services"]:
        return system_cfg
    return None


def parse_udapi(system_cfg, allowed_network, *, interface_gateway=None, client_names=None):
    """Parse only the authorized IPv4 subnet, never create VLANs or other networks.

    interface_gateway is the *existing* OpenWrt interface CIDR; required so a
    bridge name such as br20 cannot accidentally select an OpenWrt interface.
    """
    data = _udapi_payload(system_cfg)
    if data is None:
        raise DhcpSyncError("system_cfg has no udapi DHCP servers")
    try:
        local = ipaddress.ip_interface(interface_gateway)
    except (ValueError, TypeError) as error:
        raise DhcpSyncError("existing OpenWrt interface CIDR is required for udapi") from error
    if local.network != allowed_network:
        raise DhcpSyncError("OpenWrt interface does not match allowed network")
    servers = data.get("services", {}).get("dhcpServers")
    if not isinstance(servers, list):
        raise DhcpSyncError("udapi dhcpServers must be a list")
    candidates = []
    for server in servers:
        if not isinstance(server, dict) or server.get("ipVersion") != "v4":
            continue
        try:
            gateway = ipaddress.IPv4Address(server["gatewayAddress"])
            prefix = int(server["ipv4Netmask"])
            network = ipaddress.ip_network(f"{gateway}/{prefix}", strict=False)
        except (KeyError, ValueError, TypeError) as error:
            raise DhcpSyncError("invalid udapi gateway or netmask") from error
        if network == allowed_network:
            candidates.append((server, gateway))
    if len(candidates) != 1:
        raise DhcpSyncError("expected exactly one udapi server for %s, found %d" %
                            (allowed_network, len(candidates)))
    server, gateway = candidates[0]
    if gateway != local.ip:
        raise DhcpSyncError("udapi gateway does not match existing OpenWrt interface")
    if server.get("enabled") is not True:
        raise DhcpSyncError("disabled udapi server cannot be applied by this sync")
    try:
        start = ipaddress.IPv4Address(server["rangeStart"])
        stop = ipaddress.IPv4Address(server["rangeStop"])
        seconds = int(server["leaseTime"])
    except (KeyError, ValueError, TypeError) as error:
        raise DhcpSyncError("invalid udapi DHCP pool") from error
    if (start not in allowed_network or stop not in allowed_network or start > stop or
            start in (allowed_network.network_address, allowed_network.broadcast_address) or
            stop == allowed_network.broadcast_address or not 60 <= seconds <= 31536000):
        raise DhcpSyncError("udapi DHCP pool is outside safe bounds")
    pool = {"start": int(start) - int(allowed_network.network_address),
            "limit": int(stop) - int(start) + 1, "leasetime": f"{seconds}s"}

    records = data.get("services", {}).get("dnsForwarder", {}).get("hostRecords", [])
    names_by_ip = {}
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            address = record.get("address")
            address = address.get("address") if isinstance(address, dict) else address
            hostname = record.get("hostName")
            if isinstance(hostname, str) and isinstance(address, str):
                names_by_ip.setdefault(address, hostname.split(".")[0])

    leases = server.get("staticLeases", [])
    if not isinstance(leases, list):
        raise DhcpSyncError("udapi staticLeases must be a list")
    reservations = {}
    for lease in leases:
        if not isinstance(lease, dict):
            raise DhcpSyncError("invalid udapi static lease")
        mac = normalize_mac(lease.get("id"))
        addresses = lease.get("addresses")
        if not mac or not isinstance(addresses, list) or len(addresses) != 1:
            raise DhcpSyncError("invalid udapi static lease MAC or addresses")
        try:
            ip = ipaddress.IPv4Address(addresses[0])
        except (ValueError, TypeError) as error:
            raise DhcpSyncError("invalid udapi static lease IP") from error
        if mac == "00:00:00:00:00:00" and str(ip) == "192.168.1.20":
            # hard-coded by the controller: reserves the default IP of unconfigured UniFi devices
            continue
        if ip not in allowed_network or ip in (allowed_network.network_address, allowed_network.broadcast_address):
            raise DhcpSyncError("udapi static lease outside allowed network")
        if mac in reservations or str(ip) in [v["ip"] for v in reservations.values()]:
            raise DhcpSyncError("duplicate udapi static lease MAC or IP")
        fallback = "host-" + mac.replace(":", "")
        reservations[mac] = {"mac": mac, "ip": str(ip),
                             "name": safe_hostname(names_by_ip.get(str(ip)) or
                                                   (client_names or {}).get(mac), fallback)}
    return {"pool": pool, "reservations": reservations, "format": "udapi"}


def parse_system_cfg(raw_value, allowed_network_text, *, interface_gateway=None, client_names=None):
    try:
        system_cfg = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except json.JSONDecodeError as error:
        raise DhcpSyncError("system_cfg is not valid JSON") from error
    if not isinstance(system_cfg, dict):
        raise DhcpSyncError("system_cfg must be an object")

    allowed_network = ipaddress.ip_network(allowed_network_text, strict=False)
    if _udapi_payload(system_cfg) is not None:
        return parse_udapi(system_cfg, allowed_network, interface_gateway=interface_gateway,
                           client_names=client_names)
    subnet = _first_subnet(system_cfg, allowed_network)

    pool = None
    starts = subnet.get("start", {})
    if starts:
        if len(starts) != 1:
            raise DhcpSyncError("multiple DHCP pools are not supported")
        start_text, pool_data = next(iter(starts.items()))
        stop_text = pool_data.get("stop")
        start = ipaddress.ip_address(start_text)
        stop = ipaddress.ip_address(stop_text)
        if start not in allowed_network or stop not in allowed_network or start > stop:
            raise DhcpSyncError("DHCP pool is outside the allowed LAN subnet")
        pool = {
            "start": int(start) - int(allowed_network.network_address),
            "limit": int(stop) - int(start) + 1,
        }
        lease = subnet.get("lease")
        if lease is not None:
            lease_seconds = int(lease)
            if not 60 <= lease_seconds <= 31536000:
                raise DhcpSyncError("DHCP lease duration is outside safe bounds")
            pool["leasetime"] = "%ds" % lease_seconds

    reservations = {}
    static_mappings = subnet.get("static-mapping", {}) or {}
    if not isinstance(static_mappings, dict):
        raise DhcpSyncError("static-mapping must be an object")
    for key, mapping in static_mappings.items():
        if not isinstance(mapping, dict):
            raise DhcpSyncError("invalid static mapping entry")
        mac = normalize_mac(mapping.get("mac-address"))
        if not mac:
            raise DhcpSyncError("static mapping has an invalid MAC address")
        try:
            ip = ipaddress.ip_address(mapping.get("ip-address"))
        except ValueError as error:
            raise DhcpSyncError("static mapping has an invalid IP address") from error
        if ip not in allowed_network:
            raise DhcpSyncError("static mapping is outside the allowed LAN subnet")
        if ip in (allowed_network.network_address, allowed_network.broadcast_address):
            raise DhcpSyncError("static mapping uses a reserved network address")
        name = safe_hostname(mapping.get("name") or key, "host-" + mac.replace(":", ""))
        if mac in reservations:
            raise DhcpSyncError("duplicate static mapping MAC")
        reservations[mac] = {"mac": mac, "ip": str(ip), "name": name}

    ips = [item["ip"] for item in reservations.values()]
    if len(ips) != len(set(ips)):
        raise DhcpSyncError("duplicate static mapping IP")
    return {"pool": pool, "reservations": reservations}


def plan_reservations(desired, hosts, domains):
    """Return UCI operations without changing the system."""
    operations = []
    managed = {}
    unmanaged = []
    for section, values in hosts.items():
        macs = mac_values(values.get("mac"))
        record = {
            "section": section,
            "values": values,
            "macs": macs,
            "ip": str(values.get("ip", "")),
        }
        if str(values.get("unifi_managed", "0")) == "1":
            marker_mac = normalize_mac(values.get("unifi_mac"))
            if not marker_mac and len(macs) == 1:
                marker_mac = macs[0]
            if not marker_mac or marker_mac in managed:
                raise DhcpSyncError("invalid or duplicate managed UCI reservation")
            managed[marker_mac] = record
        else:
            unmanaged.append(record)

    domain_by_mac = {}
    for section, values in domains.items():
        marker_mac = normalize_mac(values.get("unifi_mac"))
        if str(values.get("unifi_managed", "0")) == "1" and marker_mac:
            domain_by_mac.setdefault(marker_mac, []).append((section, values))

    for mac, original_wanted in sorted(desired.items()):
        wanted = dict(original_wanted)
        current = managed.get(mac)
        if current:
            if current["ip"] == wanted["ip"] and current["macs"] == [mac] and current["values"].get("name"):
                # An existing reservation retains its DNS name even if the
                # controller client list assigns a different display name.
                wanted["name"] = str(current["values"]["name"])
            elif generated_mapping_name(wanted["name"], mac):
                wanted["name"] = safe_hostname(
                    current["values"].get("name"), "host-" + mac.replace(":", "")
                )
            conflicts = [
                item for item in unmanaged
                if wanted["ip"] == item["ip"] or mac in item["macs"]
            ]
            if conflicts:
                raise DhcpSyncError("managed reservation conflicts with unmanaged UCI data")
            if not host_matches(current, wanted):
                operations.append({"op": "upsert", "section": current["section"], **wanted})
            for section, values in domain_by_mac.get(mac, []):
                if not domain_matches(values, wanted, mac):
                    operations.append(
                        {"op": "domain-upsert", "section": section, "mac": mac,
                         "ip": wanted["ip"], "name": wanted["name"]}
                    )
            continue

        exact = [
            item for item in unmanaged
            if len(item["macs"]) == 1 and item["macs"][0] == mac
            and item["ip"] == wanted["ip"]
        ]
        conflicts = [
            item for item in unmanaged
            if (wanted["ip"] == item["ip"] or mac in item["macs"])
            and item not in exact
        ]
        if conflicts or len(exact) > 1:
            raise DhcpSyncError("desired reservation conflicts with unmanaged UCI data")
        if exact:
            record = exact[0]
            if generated_mapping_name(wanted["name"], mac):
                wanted["name"] = safe_hostname(
                    record["values"].get("name"), "host-" + mac.replace(":", "")
                )
            operations.append({"op": "upsert", "section": record["section"], **wanted})
            old_name = str(record["values"].get("name", ""))
            old_ip = record["ip"]
            for section, values in domains.items():
                domain_name = str(values.get("name", ""))
                if str(values.get("ip", "")) == old_ip and domain_name in {
                    old_name, old_name + ".lan"
                }:
                    operations.append(
                        {"op": "domain-upsert", "section": section, "mac": mac,
                         "ip": wanted["ip"], "name": wanted["name"]}
                    )
            unmanaged.remove(record)
        else:
            if generated_mapping_name(wanted["name"], mac):
                wanted["name"] = "host-" + mac.replace(":", "")
            section = "unifi_" + mac.replace(":", "")
            if section in hosts:
                raise DhcpSyncError("generated UCI section name already exists")
            operations.append({"op": "upsert", "section": section, **wanted})

    for mac, current in managed.items():
        if mac not in desired:
            operations.append({"op": "delete", "section": current["section"], "mac": mac})
            for section, _values in domain_by_mac.get(mac, []):
                operations.append({"op": "domain-delete", "section": section, "mac": mac})
    return operations


def dry_run(raw_system_cfg, network, snapshot, *, client_names=None):
    """Plan against an explicit read-only UCI snapshot; no subprocess or writes.

    Snapshot fields: interface_gateway (CIDR), lan, hosts, domains. A caller
    must provide all of them; an empty fixture is NOT the production router.
    """
    required = {"interface_gateway", "lan", "hosts", "domains"}
    if not isinstance(snapshot, dict) or not required <= snapshot.keys():
        raise DhcpSyncError("dry-run requires an explicit UCI snapshot")
    if not all(isinstance(snapshot[key], dict) for key in ("lan", "hosts", "domains")):
        raise DhcpSyncError("invalid UCI snapshot sections")
    desired = parse_system_cfg(raw_system_cfg, network,
                               interface_gateway=snapshot["interface_gateway"],
                               client_names=client_names)
    operations = plan_reservations(desired["reservations"], snapshot["hosts"],
                                   snapshot["domains"])
    pool_changes = {}
    for key, value in (desired["pool"] or {}).items():
        old = snapshot["lan"].get(key)
        if str(old) != str(value):
            pool_changes[key] = {"from": old, "to": value}
    return {"format": desired.get("format", "usg"), "network": network,
            "pool_changes": pool_changes, "operations": operations,
            "reservation_count": len(desired["reservations"]),
            "changes": len(pool_changes) + len(operations), "applied": False}


class OpenWrtDhcpSync:
    def __init__(self, config):
        self.config = config
        self.enabled = config.getboolean("openwrt", "dhcp_sync_enabled", fallback=False)
        self.mode = config.get("openwrt", "dhcp_sync_mode", fallback="apply")
        self.network = config.get("openwrt", "dhcp_sync_network", fallback="")
        self.config_path = Path(
            config.get("openwrt", "dhcp_config_path", fallback="/etc/config/dhcp")
        )
        self.backup_dir = Path(
            config.get(
                "openwrt", "dhcp_backup_dir",
                fallback="/etc/unifi-gateway/backups",
            )
        )
        self.state_path = Path(
            config.get(
                "openwrt", "dhcp_state_file",
                fallback="/etc/unifi-gateway/dhcp-sync-state.json",
            )
        )
        self.max_changes = config.getint("openwrt", "dhcp_sync_max_changes", fallback=128)

    def _controller_names(self):
        """Best-effort client names (MAC -> name) from the controller's client list.

        Needs dhcp_controller_url and dhcp_controller_credentials_file (JSON with
        "username"/"password", readable only by root). Skipped when either is unset;
        any failure only logs a warning and never fails the sync. Never logs secrets.
        """
        credential_path = self.config.get("openwrt", "dhcp_controller_credentials_file", fallback="")
        base = self.config.get("openwrt", "dhcp_controller_url", fallback="").rstrip("/")
        if not credential_path or not base:
            return {}
        site = self.config.get("openwrt", "dhcp_controller_site", fallback="default")
        ca_file = self.config.get("openwrt", "dhcp_controller_ca_file", fallback="")
        try:
            with open(credential_path) as stream:
                credentials = json.load(stream)
            # UniFi controllers usually have a self-signed certificate; verify only when a CA file is given
            context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl._create_unverified_context()
            cookies = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookies),
                urllib.request.HTTPSHandler(context=context))
            body = json.dumps({"username": credentials["username"],
                               "password": credentials["password"]}).encode()
            login = urllib.request.Request(
                base + "/api/login", data=body,
                headers={"Content-Type": "application/json"})
            with opener.open(login, timeout=5) as response:
                if response.status != 200:
                    raise ValueError("controller login failed")
            with opener.open(base + "/api/s/%s/rest/user" % site, timeout=5) as response:
                payload = json.load(response)
            if payload.get("meta", {}).get("rc") != "ok" or not isinstance(payload.get("data"), list):
                raise ValueError("controller client list unavailable")
            names = {}
            for client in payload["data"]:
                if not isinstance(client, dict):
                    continue
                mac = normalize_mac(client.get("mac"))
                name = client.get("name") or client.get("hostname")
                if mac and isinstance(name, str) and name.strip():
                    names[mac] = safe_hostname(name, "host-" + mac.replace(":", ""))
            return names
        except Exception:
            LOGGER.warning("UniFi client-name lookup unavailable; preserving fallback names")
            return {}

    def _run(self, arguments, *, input_text=None, check=True):
        return subprocess.run(
            arguments,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=30,
            check=check,
        )

    def _uci_values(self, section_type):
        payload = json.dumps({"config": "dhcp", "type": section_type})
        result = self._run(["ubus", "call", "uci", "get", payload])
        return json.loads(result.stdout).get("values", {})

    def _uci_section(self, section):
        payload = json.dumps({"config": "dhcp", "section": section})
        result = self._run(["ubus", "call", "uci", "get", payload])
        return json.loads(result.stdout).get("values", {})

    def _network_lan_cidr(self):
        payload = json.dumps({"config": "network", "section": "lan"})
        values = json.loads(self._run(["ubus", "call", "uci", "get", payload]).stdout).get("values", {})
        ip = values.get("ipaddr", "")
        mask = values.get("netmask")
        if isinstance(ip, list):
            if len(ip) != 1:
                raise DhcpSyncError("expected exactly one LAN interface address")
            ip = ip[0]
        if not isinstance(ip, str) or not ip:
            raise DhcpSyncError("OpenWrt LAN has no static IPv4 address")
        return ip if "/" in ip else f"{ip}/{mask}" if mask else ""

    def _set(self, section, option, value):
        self._run(["uci", "-q", "set", "dhcp.%s.%s=%s" % (section, option, value)])

    def _delete(self, section):
        self._run(["uci", "-q", "delete", "dhcp.%s" % section])

    def _snapshot(self):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / ("dhcp.%d" % int(time.time()))
        shutil.copy2(self.config_path, path)
        backups = sorted(self.backup_dir.glob("dhcp.*"), key=lambda item: item.stat().st_mtime)
        for old_path in backups[:-10]:
            old_path.unlink()
        return path

    def _restore(self, path):
        shutil.copy2(path, self.config_path)
        self._run(["/etc/init.d/dnsmasq", "restart"], check=False)

    def _apply_operation(self, operation):
        kind = operation["op"]
        section = operation["section"]
        if kind in ("delete", "domain-delete"):
            self._delete(section)
            return
        if kind == "upsert":
            self._run(["uci", "-q", "set", "dhcp.%s=host" % section])
            self._set(section, "name", operation["name"])
            self._set(section, "mac", operation["mac"])
            self._set(section, "ip", operation["ip"])
            self._set(section, "dns", "1")
            self._set(section, "unifi_managed", "1")
            self._set(section, "unifi_mac", operation["mac"])
            return
        if kind == "domain-upsert":
            self._set(section, "ip", operation["ip"])
            self._set(section, "unifi_managed", "1")
            self._set(section, "unifi_mac", operation["mac"])
            current = self._uci_section(section)
            old_name = str(current.get("name", ""))
            suffix = ".lan" if old_name.endswith(".lan") else ""
            self._set(section, "name", operation["name"] + suffix)
            return
        raise DhcpSyncError("unknown reconciliation operation")

    def apply(self, raw_system_cfg):
        if not self.enabled:
            return {"enabled": False, "changed": False}
        if not self.network:
            raise DhcpSyncError("dhcp_sync_network is required")
        if self.mode not in ("apply", "dry-run"):
            raise DhcpSyncError("unknown dhcp_sync_mode")

        # Parse UDAPI against the actual LAN address and subnet; never match
        # the controller bridge name (which does not name an OpenWrt interface).
        try:
            envelope = json.loads(raw_system_cfg) if isinstance(raw_system_cfg, str) else raw_system_cfg
        except json.JSONDecodeError as error:
            raise DhcpSyncError("system_cfg is not valid JSON") from error
        if self.mode == "dry-run":
            snapshot = {"interface_gateway": self._network_lan_cidr(),
                        "lan": self._uci_section("lan"),
                        "hosts": self._uci_values("host"),
                        "domains": self._uci_values("domain")}
            plan = dry_run(raw_system_cfg, self.network, snapshot,
                           client_names=self._controller_names() if isinstance(envelope, dict) and _udapi_payload(envelope) is not None else None)
            if plan["changes"] > self.max_changes:
                raise DhcpSyncError("dry-run exceeds DHCP change safety limit")
            LOGGER.info("UniFi DHCP DRY RUN (nothing applied): %s", json.dumps(plan, sort_keys=True))
            return {"enabled": True, "changed": False, "dry_run": True, "plan": plan}
        is_udapi = isinstance(envelope, dict) and _udapi_payload(envelope) is not None
        desired = parse_system_cfg(
            raw_system_cfg, self.network,
            interface_gateway=self._network_lan_cidr() if is_udapi else None,
            client_names=self._controller_names() if is_udapi else None,
        )
        hosts = self._uci_values("host")
        domains = self._uci_values("domain")
        operations = plan_reservations(desired["reservations"], hosts, domains)

        pool_changes = {}
        if desired["pool"]:
            current_lan = self._uci_section("lan")
            for key in ("start", "limit", "leasetime"):
                if key in desired["pool"] and str(current_lan.get(key)) != str(desired["pool"][key]):
                    pool_changes[key] = desired["pool"][key]

        change_count = len(operations) + len(pool_changes)
        if change_count > self.max_changes:
            raise DhcpSyncError(
                "refusing %d DHCP changes (limit %d)" % (change_count, self.max_changes)
            )
        if not change_count:
            return {
                "enabled": True, "changed": False,
                "reservations": len(desired["reservations"]),
            }

        backup_path = self._snapshot()
        try:
            for operation in operations:
                self._apply_operation(operation)
            for key, value in pool_changes.items():
                self._set("lan", key, value)
            self._run(["uci", "commit", "dhcp"])
            reload_result = self._run(["/etc/init.d/dnsmasq", "reload"], check=False)
            running_result = self._run(["/etc/init.d/dnsmasq", "running"], check=False)
            if reload_result.returncode or running_result.returncode:
                raise DhcpSyncError("dnsmasq rejected the updated DHCP configuration")
        except Exception:
            self._run(["uci", "revert", "dhcp"], check=False)
            self._restore(backup_path)
            raise

        state = {
            "applied_at": int(time.time()),
            "config_sha256": hashlib.sha256(
                json.dumps(desired, sort_keys=True).encode()
            ).hexdigest(),
            "reservation_count": len(desired["reservations"]),
            "operations": len(operations),
            "pool_changes": sorted(pool_changes),
        }
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, self.state_path)
        return {"enabled": True, "changed": True, **state}
