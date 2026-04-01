# -*- coding: utf-8 -*-
"""Tests for BaseCollector — cross-platform logic."""
import io
import json
import os
import textwrap
import time
from unittest.mock import patch, mock_open, MagicMock

import psutil
import pytest

from collectors.base import BaseCollector
from tests.conftest import MOCK_COUNTERS, MOCK_ADDRS


class TestDHCPParsing:
    """DHCP parsers read real temp files, so these tests don't use base_patches."""

    def _make_collector(self, config):
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b''), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            return BaseCollector(config)

    def test_parse_dnsmasq_leases(self, gateway_config):
        dc = self._make_collector(gateway_config)
        content = textwrap.dedent("""\
            1711900000 aa:bb:cc:dd:ee:01 192.168.1.10 myhost 01:aa:bb:cc:dd:ee:01
            1711900000 aa:bb:cc:dd:ee:02 192.168.1.11 * 01:aa:bb:cc:dd:ee:02
        """)
        leases = dc._parse_dnsmasq_leases(_fake_path(content))
        assert len(leases) == 2
        assert leases[0]['hostname'] == 'myhost'
        assert leases[0]['mac'] == 'aa:bb:cc:dd:ee:01'
        assert leases[0]['ip'] == '192.168.1.10'
        assert 'hostname' not in leases[1]

    def test_parse_isc_leases(self, gateway_config):
        dc = self._make_collector(gateway_config)
        content = textwrap.dedent("""\
            lease 192.168.1.50 {
              starts 2 2024/03/28 10:00:00;
              ends 2 2024/03/28 22:00:00;
              hardware ethernet aa:bb:cc:11:22:33;
              client-hostname "desktop-pc";
            }
            lease 192.168.1.51 {
              starts 2 2024/03/28 10:00:00;
              ends 2 2024/03/28 22:00:00;
              hardware ethernet aa:bb:cc:44:55:66;
            }
        """)
        leases = dc._parse_isc_leases(_fake_path(content))
        assert len(leases) == 2
        assert leases[0]['ip'] == '192.168.1.50'
        assert leases[0]['mac'] == 'aa:bb:cc:11:22:33'
        assert leases[0]['hostname'] == 'desktop-pc'
        assert 'hostname' not in leases[1]

    def test_parse_kea_leases(self, gateway_config):
        dc = self._make_collector(gateway_config)
        content = textwrap.dedent("""\
            address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state,user_context,pool_id
            192.168.1.100,aa:bb:cc:dd:ee:ff,,86400,1711986400,1,0,0,myphone,0,,0
            192.168.1.101,11:22:33:44:55:66,,86400,1711986400,1,0,0,,0,,0
            192.168.1.102,de:ad:be:ef:00:01,,86400,1711986400,1,0,0,expired,2,,0
        """)
        leases = dc._parse_kea_leases(_fake_path(content))
        assert len(leases) == 2  # state=2 filtered out
        assert leases[0]['hostname'] == 'myphone'
        assert leases[0]['mac'] == 'aa:bb:cc:dd:ee:ff'
        assert 'hostname' not in leases[1]

    def test_kea_skips_header_and_comments(self, gateway_config):
        dc = self._make_collector(gateway_config)
        content = textwrap.dedent("""\
            # KEA lease file
            address,hwaddr,client_id,valid_lifetime,expire,subnet_id,fqdn_fwd,fqdn_rev,hostname,state
            192.168.1.5,aa:bb:cc:00:00:01,,3600,1711900000,1,0,0,host1,0
        """)
        leases = dc._parse_kea_leases(_fake_path(content))
        assert len(leases) == 1
        assert leases[0]['ip'] == '192.168.1.5'


class TestHostTableMerge:

    def test_arp_only(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        dc.data['dhcp_leases'] = []
        arp = [{'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10'}]
        with patch('time.time', return_value=2000000.0):
            result = dc._merge_dhcp_into_hosts(arp)
        assert len(result) == 1
        assert result[0]['mac'] == 'aa:bb:cc:00:00:01'
        assert result[0]['authorized'] is True
        assert result[0]['rx_bytes'] == 0

    def test_dhcp_adds_missing_hosts(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'aa:bb:cc:00:00:02', 'ip': '192.168.1.20', 'hostname': 'printer'},
        ]
        arp = [{'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10'}]
        with patch('time.time', return_value=2000000.0):
            result = dc._merge_dhcp_into_hosts(arp)
        assert len(result) == 2
        hostnames = {h.get('hostname') for h in result}
        assert 'printer' in hostnames

    def test_dhcp_enriches_hostname(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10', 'hostname': 'desktop'},
        ]
        arp = [{'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10'}]
        with patch('time.time', return_value=2000000.0):
            result = dc._merge_dhcp_into_hosts(arp)
        assert len(result) == 1
        assert result[0]['hostname'] == 'desktop'

    def test_arp_hostname_not_overwritten(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10', 'hostname': 'dhcp-name'},
        ]
        arp = [{'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10', 'hostname': 'arp-name'}]
        with patch('time.time', return_value=2000000.0):
            result = dc._merge_dhcp_into_hosts(arp)
        assert result[0]['hostname'] == 'arp-name'

    def test_case_insensitive_mac_merge(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'AA:BB:CC:00:00:01', 'ip': '192.168.1.10'},
        ]
        arp = [{'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.1.10'}]
        with patch('time.time', return_value=2000000.0):
            result = dc._merge_dhcp_into_hosts(arp)
        assert len(result) == 1


class TestInterfaceStats:

    def test_ifstat_basic(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        ifstat = dc.data['ifstat']
        assert 'eth0' in ifstat
        assert 'eth1' in ifstat
        assert ifstat['eth0']['rx_bytes'] == '1000'
        assert ifstat['eth0']['tx_bytes'] == '2000'
        assert ifstat['eth1']['rx_packets'] == '30'

    def test_ifstat_rate_calculation(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        from tests.conftest import NetIO, MOCK_COUNTERS
        updated_counters = {
            'enp0s3': NetIO(2000, 4000, 20, 40, 0, 0, 0, 0),
            'enp0s8': NetIO(5000, 6000, 50, 60, 1, 2, 0, 0),
        }
        with patch('psutil.net_io_counters', return_value=updated_counters), \
             patch('time.time', return_value=dc._prev_time + 10):
            ifstat = dc._get_ifstat()
        assert ifstat['eth0']['rx_bps'] == 100  # (2000-1000)/10
        assert ifstat['eth0']['tx_bps'] == 200  # (4000-2000)/10

    def test_missing_interface_skipped(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        with patch('psutil.net_io_counters', return_value={'lo': MOCK_COUNTERS_ENTRY}):
            ifstat = dc._get_ifstat()
        assert ifstat == {}


class TestMACAddresses:

    def test_macs_from_psutil(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        assert dc.data['macs']['eth0'] == 'aa:bb:cc:dd:ee:01'
        assert dc.data['macs']['eth1'] == 'aa:bb:cc:dd:ee:02'

    def test_mac_normalization(self, gateway_config):
        """Windows-style dash-separated MACs get normalized to colons."""
        from tests.conftest import SAddr, MOCK_COUNTERS
        import socket
        dash_addrs = {
            'enp0s3': [SAddr(psutil.AF_LINK, 'AA-BB-CC-DD-EE-01', None, None, None)],
            'enp0s8': [SAddr(psutil.AF_LINK, 'AA-BB-CC-DD-EE-02', None, None, None)],
        }
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=dash_addrs), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b''), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = BaseCollector(gateway_config)
        assert dc.data['macs']['eth0'] == 'aa:bb:cc:dd:ee:01'


class TestIPAddresses:

    def test_ip_addresses(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        ips = dc.data['ip']
        assert ips['eth1']['address'] == '192.168.1.1'
        assert ips['eth1']['netmask'] == '255.255.255.0'

    def test_wan_gets_gateway_via_ip_route(self, gateway_config):
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b'default via 10.0.0.1 dev enp0s3'), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = BaseCollector(gateway_config)
        assert dc.data['ip']['eth0']['gateway'] == '10.0.0.1'

    def test_wan_gets_gateway_via_bsd_route(self, gateway_config):
        bsd_output = b'   route to: default\n    gateway: 10.0.0.1\n'
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=bsd_output), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = BaseCollector(gateway_config)
        assert dc.data['ip']['eth0']['gateway'] == '10.0.0.1'


class TestSystemStats:

    def test_system_stats(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        stats = dc.data['system_stats']
        assert stats['cpu'] == '25'
        assert stats['mem'] == '40'


class TestSpeedtest:

    def test_speedtest_missing_file(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        st = dc.data['speedtest']
        assert st['lastrun'] == 0
        assert st['download'] == 0

    def test_speedtest_valid_file(self, gateway_config):
        from tests.conftest import MOCK_COUNTERS, MOCK_ADDRS
        sp_data = json.dumps({'ping': 5.0, 'upload': 104857600, 'download': 209715200})
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b''), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', return_value=1711900000.0), \
             patch('builtins.open', side_effect=lambda p, *a, **kw: io.StringIO(sp_data)):
            gateway_config.set('gateway', 'speedtest_file', 'speedtest.json')
            dc = BaseCollector(gateway_config)
        assert dc.data['speedtest']['ping'] == 5.0
        assert dc.data['speedtest']['download'] == 200.0  # 209715200 / 1024 / 1024


class TestNameservers:

    def test_nameservers_from_resolv_conf(self, gateway_config):
        from tests.conftest import MOCK_COUNTERS, MOCK_ADDRS
        resolv = "nameserver 1.1.1.1\nnameserver 8.8.8.8\n"

        def fake_open(path, *args, **kwargs):
            if 'resolv.conf' in str(path):
                return io.StringIO(resolv)
            raise FileNotFoundError(path)

        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b''), \
             patch('builtins.open', side_effect=fake_open), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = BaseCollector(gateway_config)
        assert dc.data['nameservers'] == ['1.1.1.1', '8.8.8.8']

    def test_nameservers_fallback(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        assert dc.data['nameservers'] == ['8.8.8.8', '8.8.4.4']


class TestDataKeys:
    """Verify the data dict has all the keys that unifi_protocol.py expects."""

    def test_all_data_keys_present(self, gateway_config, base_patches):
        dc = create_base(gateway_config)
        expected_keys = {'macs', 'ifstat', 'dhcp_leases', 'ip', 'nameservers',
                         'speedtest', 'system_stats', 'latency', 'host_table'}
        assert expected_keys.issubset(dc.data.keys())


# ── Helpers ──────────────────────────────────────────────────────────────

from tests.conftest import NetIO

MOCK_COUNTERS_ENTRY = NetIO(100, 200, 1, 2, 0, 0, 0, 0)


def create_base(config):
    """Create a BaseCollector with all external calls mocked."""
    return BaseCollector(config)


def _fake_path(content):
    """Write content to a temp-like StringIO and return a readable path.

    Since we can't easily mock open() per-call in the parse methods
    (they take a path argument), we write to an actual temp file.
    """
    import tempfile
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False)
    f.write(content)
    f.close()
    return f.name
