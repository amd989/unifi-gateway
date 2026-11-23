import json
import unittest
import unittest.mock

from provisioners.openwrt_dhcp import (
    DhcpSyncError,
    OpenWrtDhcpSync,
    dry_run,
    parse_system_cfg,
    plan_reservations,
)


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


def usg_pool(start="192.168.0.100", stop="192.168.0.254", lease="43200"):
    data = json.loads(payload())
    subnet = data["service"]["dhcp-server"]["shared-network-name"]["LAN"]["subnet"]["192.168.0.0/24"]
    subnet["start"] = {start: {"stop": stop}}
    subnet["lease"] = lease
    return json.dumps(data)


class UsgChecksTests(unittest.TestCase):
    def test_usg_format_is_checked_against_network_lan(self):
        parse_system_cfg(payload(), "192.168.0.0/24", interface_gateway="192.168.0.1/24")
        with self.assertRaises(DhcpSyncError):
            parse_system_cfg(payload(), "192.168.0.0/24", interface_gateway="192.168.1.1/24")

    def test_dry_run_checks_usg_format_against_network_lan(self):
        snapshot = {"interface_gateway": "10.0.0.1/24", "lan": {}, "hosts": {}, "domains": {}}
        with self.assertRaises(DhcpSyncError):
            dry_run(payload(), "192.168.0.0/24", snapshot)

    def test_usg_pool_rejects_network_and_broadcast_addresses(self):
        for start, stop in (("192.168.0.0", "192.168.0.100"), ("192.168.0.100", "192.168.0.255")):
            with self.subTest(start=start, stop=stop), self.assertRaises(DhcpSyncError):
                parse_system_cfg(usg_pool(start, stop), "192.168.0.0/24")

    def test_unparsable_usg_pool_values_are_a_rejected_config(self):
        for pool in (usg_pool(start="not-an-ip"), usg_pool(stop="192.168.0.x"), usg_pool(lease="soon")):
            with self.subTest(pool=pool), self.assertRaises(DhcpSyncError):
                parse_system_cfg(pool, "192.168.0.0/24")


class ApplyBatchTests(unittest.TestCase):
    """apply() writes everything with one `uci batch` and rolls back when uci or dnsmasq fails."""

    def setUp(self):
        import configparser
        import tempfile
        from pathlib import Path
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "dhcp").write_text("original\n")
        cfg = configparser.ConfigParser()
        cfg.read_dict({"openwrt": {
            "dhcp_sync_enabled": "true", "dhcp_sync_network": "192.168.0.0/24",
            "dhcp_config_path": str(self.tmp / "dhcp"), "dhcp_backup_dir": str(self.tmp / "backups"),
            "dhcp_state_file": str(self.tmp / "state.json")}})
        self.sync = OpenWrtDhcpSync(cfg)
        self.calls = []
        self.batch_stderr = ""
        self.sync._run = self.fake_run
        self.sync._network_lan_cidr = lambda: "192.168.0.1/24"
        self.sync._uci_values = lambda kind: {"host": {"handmade": {"name": "nas", "mac": "aa:bb:cc:dd:ee:99",
                                                                     "ip": "192.168.0.9"}},
                                              "domain": {}}[kind]
        self.sync._uci_section = lambda section: {"start": "100", "limit": "150", "leasetime": "43200s"}

    def fake_run(self, arguments, *, input_text=None, check=True):
        self.calls.append((arguments, input_text))
        stderr = self.batch_stderr if arguments == ["uci", "batch"] else ""
        return unittest.mock.Mock(returncode=0, stdout="", stderr=stderr)

    def mappings(self):
        return {"tv": {"mac-address": "aa:bb:cc:dd:ee:02", "ip-address": "192.168.0.21", "name": "TV"},
                "pc": {"mac-address": "aa:bb:cc:dd:ee:03", "ip-address": "192.168.0.22", "name": "PC"}}

    def test_one_batch_then_one_commit(self):
        result = self.sync.apply(payload(mappings=self.mappings()))
        self.assertTrue(result["changed"])
        commands = [arguments for arguments, _ in self.calls]
        self.assertEqual(commands[:2], [["uci", "batch"], ["uci", "commit", "dhcp"]])
        self.assertEqual(sum(arguments[0] == "uci" for arguments in commands), 2)
        batch = self.calls[0][1].splitlines()
        self.assertIn("set dhcp.unifi_aabbccddee02=host", batch)
        self.assertIn("set dhcp.unifi_aabbccddee02.name='tv'", batch)
        self.assertIn("set dhcp.unifi_aabbccddee03.unifi_mac='aa:bb:cc:dd:ee:03'", batch)
        self.assertIn("set dhcp.lan.limit='155'", batch)
        self.assertFalse(any("handmade" in line for line in batch))

    def test_uci_batch_error_rolls_back(self):
        self.batch_stderr = "uci: Invalid argument\n"
        with self.assertRaises(DhcpSyncError):
            self.sync.apply(payload(mappings=self.mappings()))
        commands = [arguments for arguments, _ in self.calls]
        self.assertNotIn(["uci", "commit", "dhcp"], commands)
        self.assertIn(["uci", "revert", "dhcp"], commands)
        self.assertEqual((self.tmp / "dhcp").read_text(), "original\n")

    def test_network_lan_mismatch_writes_nothing(self):
        self.sync._network_lan_cidr = lambda: "10.0.0.1/24"
        with self.assertRaises(DhcpSyncError):
            self.sync.apply(payload(mappings=self.mappings()))
        self.assertEqual(self.calls, [])

    def test_batch_refuses_quotes(self):
        with self.assertRaises(DhcpSyncError):
            OpenWrtDhcpSync._batch_lines([{"op": "upsert", "section": "x", "name": "a'b",
                                           "mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.0.5"}], {}, {})


class ControllerTlsTests(unittest.TestCase):
    def sync(self, **extra):
        import configparser
        import tempfile
        credentials = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"username": "u", "password": "p"}, credentials)
        credentials.close()
        cfg = configparser.ConfigParser()
        cfg.read_dict({"openwrt": {"dhcp_sync_enabled": "true", "dhcp_controller_credentials_file": credentials.name,
                                   "dhcp_controller_url": "https://127.0.0.1:1", **extra}})
        return OpenWrtDhcpSync(cfg)

    def test_plain_http_is_refused(self):
        with unittest.mock.patch("urllib.request.build_opener") as opener:
            self.assertEqual(self.sync(dhcp_controller_url="http://controller:8080")._controller_names(), {})
            opener.assert_not_called()

    def test_invalid_fingerprint_is_refused(self):
        with unittest.mock.patch("urllib.request.build_opener") as opener:
            self.assertEqual(self.sync(dhcp_controller_cert_sha256="abc")._controller_names(), {})
            opener.assert_not_called()

    def test_fingerprint_formats(self):
        from provisioners.openwrt_dhcp import normalize_fingerprint
        hex64 = "ab" * 32
        self.assertEqual(normalize_fingerprint(":".join(["AB"] * 32)), hex64)
        self.assertEqual(normalize_fingerprint(hex64), hex64)
        self.assertIsNone(normalize_fingerprint(hex64[:-2]))


@unittest.skipUnless(__import__("shutil").which("openssl"), "needs the openssl CLI to make a test certificate")
class ControllerTlsLiveTests(unittest.TestCase):
    """Against a local HTTPS server with a self-signed certificate, like a standalone UniFi controller."""

    @classmethod
    def setUpClass(cls):
        import hashlib
        import http.server
        import ssl
        import subprocess
        import tempfile
        import threading
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj",
                        "/CN=unifi", "-keyout", str(tmp / "key.pem"), "-out", str(tmp / "cert.pem")],
                       check=True, capture_output=True)
        cls.cert = tmp / "cert.pem"
        cls.fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cls.cert.read_text())).hexdigest()
        cls.logins = []

        class Controller(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                cls.logins.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                body = json.dumps({"meta": {"rc": "ok"},
                                   "data": [{"mac": "AA:BB:CC:DD:EE:02", "name": "Living Room TV"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Controller)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, tmp / "key.pem")
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"https://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def names(self, **extra):
        self.logins.clear()
        return ControllerTlsTests.sync(self, dhcp_controller_url=self.url, **extra)._controller_names()

    def test_self_signed_is_refused_by_default_before_login(self):
        with self.assertLogs("unifi-gateway", "WARNING") as logs:
            self.assertEqual(self.names(), {})
        self.assertEqual(self.logins, [])
        self.assertIn("dhcp_controller_cert_sha256", logs.output[0])

    def test_matching_pin_is_accepted(self):
        self.assertEqual(self.names(dhcp_controller_cert_sha256=self.fingerprint),
                         {"aa:bb:cc:dd:ee:02": "living-room-tv"})
        self.assertEqual(len(self.logins), 1)

    def test_wrong_pin_is_refused_before_login(self):
        with self.assertLogs("unifi-gateway", "WARNING"):
            self.assertEqual(self.names(dhcp_controller_cert_sha256="00" * 32), {})
        self.assertEqual(self.logins, [])
