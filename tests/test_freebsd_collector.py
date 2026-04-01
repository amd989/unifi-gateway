# -*- coding: utf-8 -*-
"""Tests for FreeBSDCollector — netstat -rn, arp -an."""
from unittest.mock import patch, MagicMock

from collectors.freebsd import FreeBSDCollector
from tests.conftest import MOCK_COUNTERS_BSD, MOCK_ADDRS_BSD


def _create_bsd(config):
    return FreeBSDCollector(config)


class TestFreeBSDGateway:

    NETSTAT_OUTPUT = (
        "Routing tables\n"
        "\n"
        "Internet:\n"
        "Destination        Gateway            Flags     Netif Expire\n"
        "default            10.0.0.1           UGS       vmx0\n"
        "10.0.0.0/24        link#1             U         vmx0\n"
        "192.168.1.0/24     link#2             U         vmx1\n"
    )

    def test_gateway_from_netstat(self, freebsd_config):
        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS_BSD), \
             patch('psutil.net_if_addrs', return_value=MOCK_ADDRS_BSD), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=self.NETSTAT_OUTPUT.encode()), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = _create_bsd(freebsd_config)

        assert dc.data['ip']['eth0']['gateway'] == '10.0.0.1'

    def test_gateway_fallback_on_error(self, freebsd_config, bsd_patches):
        dc = _create_bsd(freebsd_config)
        with patch('subprocess.check_output', side_effect=OSError('fail')):
            gw = dc._get_default_gateway()
        assert gw is None


class TestFreeBSDARP:

    ARP_OUTPUT = (
        "? (192.168.1.10) at aa:bb:cc:11:22:33 on vmx1 expires in 1200 seconds [ethernet]\n"
        "? (192.168.1.11) at aa:bb:cc:44:55:66 on vmx1 permanent [ethernet]\n"
        "? (192.168.1.12) at (incomplete) on vmx1 [ethernet]\n"
        "? (10.0.0.1) at ff:ff:ff:00:00:01 on vmx0 expires in 600 seconds [ethernet]\n"
    )

    def test_arp_parsed(self, freebsd_config, bsd_patches):
        dc = _create_bsd(freebsd_config)
        arp_result = MagicMock(stdout=self.ARP_OUTPUT.encode())
        with patch('subprocess.run', return_value=arp_result):
            neighbors = dc._get_neighbors_raw()

        macs = [n['mac'] for n in neighbors]
        assert 'aa:bb:cc:11:22:33' in macs  # LAN interface
        assert 'aa:bb:cc:44:55:66' in macs  # LAN permanent
        assert '(incomplete)' not in macs  # incomplete filtered
        assert 'ff:ff:ff:00:00:01' not in macs  # WAN interface filtered

    def test_arp_empty_on_error(self, freebsd_config, bsd_patches):
        dc = _create_bsd(freebsd_config)
        with patch('subprocess.run', side_effect=OSError('no arp')):
            neighbors = dc._get_neighbors_raw()
        assert neighbors == []

    def test_arp_dhcp_hostname_enrichment(self, freebsd_config, bsd_patches):
        dc = _create_bsd(freebsd_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'aa:bb:cc:11:22:33', 'ip': '192.168.1.10', 'hostname': 'nas'},
        ]
        arp_result = MagicMock(stdout=self.ARP_OUTPUT.encode())
        with patch('subprocess.run', return_value=arp_result):
            neighbors = dc._get_neighbors_raw()
        nas = [n for n in neighbors if n['mac'] == 'aa:bb:cc:11:22:33'][0]
        assert nas['hostname'] == 'nas'


class TestInheritance:
    """Verify OPNSense and pfSense collectors inherit FreeBSD behavior."""

    def test_opnsense_is_freebsd(self, freebsd_config, bsd_patches):
        from collectors.opnsense import OPNSenseCollector
        dc = OPNSenseCollector(freebsd_config)
        assert isinstance(dc, FreeBSDCollector)
        assert 'ifstat' in dc.data

    def test_pfsense_is_freebsd(self, freebsd_config, bsd_patches):
        from collectors.pfsense import PfSenseCollector
        dc = PfSenseCollector(freebsd_config)
        assert isinstance(dc, FreeBSDCollector)
        assert 'ifstat' in dc.data
