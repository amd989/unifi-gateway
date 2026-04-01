# -*- coding: utf-8 -*-
"""Tests for LinuxCollector — /proc, ip neigh, /sys MAC fallback."""
import io
from unittest.mock import patch, MagicMock

import psutil

from collectors.linux import LinuxCollector
from tests.conftest import MOCK_COUNTERS, MOCK_ADDRS, SAddr


def _create_linux(config):
    return LinuxCollector(config)


class TestLinuxGateway:

    PROC_NET_ROUTE = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "enp0s3\t00000000\t0101000A\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "enp0s3\t0001000A\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    )

    def test_gateway_from_proc(self, gateway_config):
        def fake_open(path, *args, **kwargs):
            if 'route' in str(path):
                return io.StringIO(self.PROC_NET_ROUTE)
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
            dc = _create_linux(gateway_config)

        # 0101000A in little-endian = 10.0.1.1
        assert dc.data['ip']['eth0']['gateway'] == '10.0.1.1'


class TestLinuxNeighbors:

    IP_NEIGH_OUTPUT = (
        "192.168.1.10 dev enp0s8 lladdr aa:bb:cc:11:22:33 used 5/10/15 probes 1 REACHABLE\n"
        "192.168.1.11 dev enp0s8 lladdr aa:bb:cc:44:55:66 used 100/200/300 probes 0 STALE\n"
        "192.168.1.12 dev enp0s8 lladdr aa:bb:cc:77:88:99 used 300/400/500 probes 0 STALE\n"
        "192.168.1.99 dev enp0s3 lladdr ff:ff:ff:00:00:01 used 5/10/15 probes 1 REACHABLE\n"
        "192.168.1.50 dev enp0s8 lladdr 00:00:00:00:00:00 used 0/0/0 probes 3 FAILED\n"
    )

    def test_neighbors_parsed(self, gateway_config, base_patches):
        dc = _create_linux(gateway_config)
        neigh_result = MagicMock(stdout=self.IP_NEIGH_OUTPUT.encode())
        with patch('subprocess.run', return_value=neigh_result):
            neighbors = dc._get_neighbors_raw()

        macs = [n['mac'] for n in neighbors]
        assert 'aa:bb:cc:11:22:33' in macs  # REACHABLE on LAN
        assert 'aa:bb:cc:44:55:66' in macs  # STALE but used < 240
        assert 'aa:bb:cc:77:88:99' not in macs  # STALE with used > 240
        assert 'ff:ff:ff:00:00:01' not in macs  # WAN interface, filtered
        assert '00:00:00:00:00:00' not in macs  # FAILED state

    def test_neighbors_empty_on_error(self, gateway_config, base_patches):
        dc = _create_linux(gateway_config)
        with patch('subprocess.run', side_effect=OSError('no ip command')):
            neighbors = dc._get_neighbors_raw()
        assert neighbors == []

    def test_neighbors_dhcp_hostname_enrichment(self, gateway_config, base_patches):
        dc = _create_linux(gateway_config)
        dc.data['dhcp_leases'] = [
            {'mac': 'aa:bb:cc:11:22:33', 'ip': '192.168.1.10', 'hostname': 'laptop'},
        ]
        neigh_result = MagicMock(stdout=self.IP_NEIGH_OUTPUT.encode())
        with patch('subprocess.run', return_value=neigh_result):
            neighbors = dc._get_neighbors_raw()
        laptop = [n for n in neighbors if n['mac'] == 'aa:bb:cc:11:22:33'][0]
        assert laptop['hostname'] == 'laptop'


class TestLinuxMulticast:

    PROC_NET_DEV = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes ...\n"
        " enp0s3: 1000 10 0 0 0 0 0 5 2000 20 0 0 0 0 0 0\n"
        " enp0s8: 3000 30 0 0 0 0 0 42 4000 40 0 0 0 0 0 0\n"
    )

    def test_multicast_supplemented(self, gateway_config, base_patches):
        dc = _create_linux(gateway_config)
        ifstat = dict(dc.data['ifstat'])

        def fake_open(path, *args, **kwargs):
            if 'proc/net/dev' in str(path):
                return io.StringIO(self.PROC_NET_DEV)
            raise FileNotFoundError(path)

        with patch('builtins.open', side_effect=fake_open):
            dc._supplement_multicast(ifstat)

        assert ifstat['eth0']['rx_multicast'] == '5'
        assert ifstat['eth1']['rx_multicast'] == '42'


class TestLinuxMACFallback:

    def test_sys_class_net_fallback(self, gateway_config):
        addrs_no_mac = {
            'enp0s3': [SAddr(psutil.AF_LINK, '00:00:00:00:00:00', None, None, None)],
            'enp0s8': [SAddr(psutil.AF_LINK, 'aa:bb:cc:dd:ee:02', None, None, None)],
        }

        def fake_open(path, *args, **kwargs):
            if '/sys/class/net/enp0s3/address' in str(path):
                return io.StringIO('aa:bb:cc:dd:ee:99\n')
            raise FileNotFoundError(path)

        with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
             patch('psutil.net_if_addrs', return_value=addrs_no_mac), \
             patch('psutil.cpu_percent', return_value=25.0), \
             patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
             patch('psutil.boot_time', return_value=1000000.0), \
             patch('subprocess.run', return_value=MagicMock(stdout=b'')), \
             patch('subprocess.check_output', return_value=b''), \
             patch('builtins.open', side_effect=fake_open), \
             patch('os.path.exists', return_value=False), \
             patch('os.path.getmtime', side_effect=FileNotFoundError):
            dc = _create_linux(gateway_config)

        assert dc.data['macs']['eth0'] == 'aa:bb:cc:dd:ee:99'
        assert dc.data['macs']['eth1'] == 'aa:bb:cc:dd:ee:02'
