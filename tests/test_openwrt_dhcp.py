import json
import unittest
import unittest.mock

from openwrt_dhcp import (DhcpSyncError, OpenWrtDhcpSync, dry_run, parse_system_cfg,
                         plan_reservations)


def payload(network="192.168.0.0/24", mappings=None):
    mappings = mappings or {}
    return json.dumps(
        {
            "service": {
                "dhcp-server": {
                    "shared-network-name": {
                        "LAN": {
                            "subnet": {
                                network: {
                                    "lease": "43200",
                                    "start": {
                                        "192.168.0.100": {"stop": "192.168.0.254"}
                                    },
                                    "static-mapping": mappings,
                                }
                            }
                        }
                    }
                }
            }
        }
    )


class ParseTests(unittest.TestCase):
    def test_parse_pool_and_mapping(self):
        result = parse_system_cfg(
            payload(
                mappings={
                    "printer": {
                        "mac-address": "AA-BB-CC-DD-EE-FF",
                        "ip-address": "192.168.0.20",
                        "name": "Office Printer",
                    }
                }
            ),
            "192.168.0.0/24",
        )
        self.assertEqual(
            result["pool"], {"start": 100, "limit": 155, "leasetime": "43200s"}
        )
        self.assertEqual(
            result["reservations"]["aa:bb:cc:dd:ee:ff"],
            {
                "mac": "aa:bb:cc:dd:ee:ff",
                "ip": "192.168.0.20",
                "name": "office-printer",
            },
        )

    def test_reject_wrong_subnet(self):
        with self.assertRaises(DhcpSyncError):
            parse_system_cfg(payload(network="192.168.1.0/24"), "192.168.0.0/24")

    def test_reject_duplicate_ip(self):
        mappings = {
            "one": {"mac-address": "00:11:22:33:44:55", "ip-address": "192.168.0.20"},
            "two": {"mac-address": "00:11:22:33:44:66", "ip-address": "192.168.0.20"},
        }
        with self.assertRaises(DhcpSyncError):
            parse_system_cfg(payload(mappings=mappings), "192.168.0.0/24")


class PlanTests(unittest.TestCase):
    def test_adopts_exact_existing_mapping_and_domains(self):
        desired = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "name": "printer",
            }
        }
        hosts = {
            "cfg01": {"mac": "00:11:22:33:44:55", "ip": "192.168.0.20", "name": "printer"}
        }
        domains = {
            "cfg02": {"name": "printer", "ip": "192.168.0.20"},
            "cfg03": {"name": "printer.lan", "ip": "192.168.0.20"},
        }
        plan = plan_reservations(desired, hosts, domains)
        self.assertEqual([item["op"] for item in plan], ["upsert", "domain-upsert", "domain-upsert"])

    def test_preserves_existing_name_when_unifi_uses_mac_as_key(self):
        desired = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "name": "00-11-22-33-44-55",
            }
        }
        hosts = {
            "cfg01": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "name": "Office-Printer",
            }
        }
        plan = plan_reservations(desired, hosts, {})
        self.assertEqual(plan[0]["name"], "office-printer")

    def test_rejects_unmanaged_conflict(self):
        desired = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.21",
                "name": "printer",
            }
        }
        hosts = {
            "cfg01": {"mac": "00:11:22:33:44:55", "ip": "192.168.0.20", "name": "printer"}
        }
        with self.assertRaises(DhcpSyncError):
            plan_reservations(desired, hosts, {})

    def test_deletes_only_managed_mapping(self):
        hosts = {
            "managed": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "unifi_managed": "1",
                "unifi_mac": "00:11:22:33:44:55",
            },
            "manual": {"mac": "00:11:22:33:44:66", "ip": "192.168.0.21"},
        }
        plan = plan_reservations({}, hosts, {})
        self.assertEqual(plan, [{"op": "delete", "section": "managed", "mac": "00:11:22:33:44:55"}])

    def test_managed_mapping_is_idempotent(self):
        desired = {
            "00:11:22:33:44:55": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "name": "printer",
            }
        }
        hosts = {
            "managed": {
                "mac": "00:11:22:33:44:55",
                "ip": "192.168.0.20",
                "name": "printer",
                "dns": "1",
                "unifi_managed": "1",
                "unifi_mac": "00:11:22:33:44:55",
            }
        }
        domains = {
            "short": {
                "name": "printer",
                "ip": "192.168.0.20",
                "unifi_managed": "1",
                "unifi_mac": "00:11:22:33:44:55",
            },
            "fqdn": {
                "name": "printer.lan",
                "ip": "192.168.0.20",
                "unifi_managed": "1",
                "unifi_mac": "00:11:22:33:44:55",
            },
        }
        self.assertEqual(plan_reservations(desired, hosts, domains), [])


if __name__ == "__main__":
    unittest.main()


# ── UniFi OS ("udapi") format, as sent to a UXG (Gateway Lite) ─────────────

def udapi(servers, host_records=None):
    return json.dumps({"udapi": {"services": {
        "dhcpServers": servers,
        "dnsForwarder": {"hostRecords": host_records or []},
    }}, "ubntconf": "# misc\n"})


def server(**overrides):
    base = {"enabled": True, "interfaces": [{"id": "br0"}], "name": "net_Default_br0_192-168-0-0-24",
            "leaseTime": 43200, "ipVersion": "v4", "rangeStart": "192.168.0.100",
            "rangeStop": "192.168.0.254", "ipv4Netmask": 24, "gatewayAddress": "192.168.0.1",
            "dnsServers": ["192.168.0.1"], "domain": "lan",
            "staticLeases": [{"id": "00:00:00:00:00:00", "addresses": ["192.168.1.20"]},
                             {"id": "AA:BB:CC:DD:EE:01", "addresses": ["192.168.0.20"]},
                             {"id": "aa:bb:cc:dd:ee:02", "addresses": ["192.168.0.21"]},
                             {"id": "aa:bb:cc:dd:ee:03", "addresses": ["192.168.0.22"]}]}
    base.update(overrides)
    return base


VLAN20 = {"enabled": True, "interfaces": [{"id": "br20"}], "ipVersion": "v4", "leaseTime": 3600,
          "rangeStart": "192.168.20.100", "rangeStop": "192.168.20.199", "ipv4Netmask": 24,
          "gatewayAddress": "192.168.20.1", "staticLeases": []}


class UdapiParseTests(unittest.TestCase):
    def parse(self, payload, **kw):
        return parse_system_cfg(payload, "192.168.0.0/24", interface_gateway="192.168.0.1/24", **kw)

    def test_pool_reservations_and_name_precedence(self):
        result = self.parse(
            udapi([server(), VLAN20],
                  host_records=[{"hostName": "nas.lan", "address": {"address": "192.168.0.20", "version": "v4"}}]),
            client_names={"aa:bb:cc:dd:ee:01": "ignored-dns-wins", "aa:bb:cc:dd:ee:02": "Living Room TV"})
        self.assertEqual(result["format"], "udapi")
        self.assertEqual(result["pool"], {"start": 100, "limit": 155, "leasetime": "43200s"})
        names = {mac: r["name"] for mac, r in result["reservations"].items()}
        # DNS host record, then controller client name, then MAC fallback; placeholder skipped
        self.assertEqual(names, {"aa:bb:cc:dd:ee:01": "nas",
                                 "aa:bb:cc:dd:ee:02": "living-room-tv",
                                 "aa:bb:cc:dd:ee:03": "host-aabbccddee03"})

    def test_requires_existing_openwrt_interface(self):
        with self.assertRaises(DhcpSyncError):
            parse_system_cfg(udapi([server()]), "192.168.0.0/24")

    def test_gateway_must_match_openwrt_lan_ip(self):
        with self.assertRaises(DhcpSyncError):
            parse_system_cfg(udapi([server()]), "192.168.0.0/24", interface_gateway="192.168.0.2/24")

    def test_disabled_server_is_refused(self):
        with self.assertRaises(DhcpSyncError):
            self.parse(udapi([server(enabled=False)]))

    def test_lease_outside_network_is_refused(self):
        with self.assertRaises(DhcpSyncError):
            self.parse(udapi([server(staticLeases=[{"id": "aa:bb:cc:dd:ee:09", "addresses": ["10.0.0.5"]}])]))

    def test_only_the_configured_subnet_counts(self):
        with self.assertRaises(DhcpSyncError):
            self.parse(udapi([VLAN20]))


class DryRunTests(unittest.TestCase):
    def test_matching_router_gives_zero_changes(self):
        hosts = {"unifi_aabbccddee02": {"name": "living-room-tv", "mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.0.21",
                                        "dns": "1", "unifi_managed": "1", "unifi_mac": "aa:bb:cc:dd:ee:02"},
                 "handmade": {"name": "server", "mac": "aa:bb:cc:dd:ee:99", "ip": "192.168.0.9"}}
        snapshot = {"interface_gateway": "192.168.0.1/24",
                    "lan": {"start": "100", "limit": "155", "leasetime": "43200s"},
                    "hosts": hosts, "domains": {}}
        payload = udapi([server(staticLeases=[{"id": "aa:bb:cc:dd:ee:02", "addresses": ["192.168.0.21"]}])])
        plan = dry_run(payload, "192.168.0.0/24", snapshot, client_names={"aa:bb:cc:dd:ee:02": "Living Room TV"})
        self.assertEqual(plan["changes"], 0)
        self.assertFalse(plan["applied"])


class SyncConfigTests(unittest.TestCase):
    def config(self, **openwrt):
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read_dict({"openwrt": openwrt} if openwrt else {})
        return cfg

    def test_disabled_by_default(self):
        sync = OpenWrtDhcpSync(self.config())
        self.assertFalse(sync.enabled)
        self.assertEqual(sync.apply("{}"), {"enabled": False, "changed": False})

    def test_name_lookup_skipped_without_url_or_credentials(self):
        sync = OpenWrtDhcpSync(self.config(dhcp_sync_enabled="true"))
        with unittest.mock.patch("urllib.request.build_opener") as opener:
            self.assertEqual(sync._controller_names(), {})
            opener.assert_not_called()
