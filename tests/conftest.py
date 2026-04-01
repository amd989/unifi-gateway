# -*- coding: utf-8 -*-
import configparser
import collections
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ── Fake psutil objects ──────────────────────────────────────────────────

NetIO = collections.namedtuple(
    'NetIO', ['bytes_recv', 'bytes_sent', 'packets_recv', 'packets_sent',
              'errin', 'errout', 'dropin', 'dropout']
)

SAddr = collections.namedtuple('SAddr', ['family', 'address', 'netmask', 'broadcast', 'ptp'])


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def gateway_config():
    """Minimal config that mirrors a typical 2-port setup."""
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        'global': {'pid_file': 'test.pid'},
        'gateway': {
            'ports': '[{"ifname":"eth0","name":"WAN","type":"wan","realif":"enp0s3"},'
                     '{"ifname":"eth1","name":"LAN","type":"lan","realif":"enp0s8"}]',
            'is_adopted': 'True',
            'firmware': '4.4.57.5578372',
        },
        'provisioned': {},
    })
    return cfg


@pytest.fixture
def freebsd_config():
    """Config with FreeBSD-style vmx interfaces."""
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        'global': {'pid_file': 'test.pid'},
        'gateway': {
            'ports': '[{"ifname":"eth0","name":"WAN","type":"wan","realif":"vmx0"},'
                     '{"ifname":"eth1","name":"LAN","type":"lan","realif":"vmx1"}]',
            'is_adopted': 'True',
            'firmware': '4.4.57.5578372',
        },
        'provisioned': {},
    })
    return cfg


MOCK_COUNTERS = {
    'enp0s3': NetIO(1000, 2000, 10, 20, 0, 0, 0, 0),
    'enp0s8': NetIO(3000, 4000, 30, 40, 1, 2, 0, 0),
}

MOCK_COUNTERS_BSD = {
    'vmx0': NetIO(1000, 2000, 10, 20, 0, 0, 0, 0),
    'vmx1': NetIO(3000, 4000, 30, 40, 1, 2, 0, 0),
}


def _make_addrs(interfaces):
    """Build a psutil.net_if_addrs()-style dict from a simple spec.

    interfaces: {realif: {'mac': ..., 'ip': ..., 'netmask': ...}}
    """
    import psutil
    result = {}
    for ifname, info in interfaces.items():
        addrs = []
        if 'mac' in info:
            addrs.append(SAddr(psutil.AF_LINK, info['mac'], None, None, None))
        if 'ip' in info:
            import socket
            addrs.append(SAddr(socket.AF_INET, info['ip'], info.get('netmask', '255.255.255.0'), None, None))
        result[ifname] = addrs
    return result


MOCK_ADDRS = _make_addrs({
    'enp0s3': {'mac': 'aa:bb:cc:dd:ee:01', 'ip': '10.0.0.2', 'netmask': '255.255.255.0'},
    'enp0s8': {'mac': 'aa:bb:cc:dd:ee:02', 'ip': '192.168.1.1', 'netmask': '255.255.255.0'},
})

MOCK_ADDRS_BSD = _make_addrs({
    'vmx0': {'mac': '00:0c:29:a3:6d:9e', 'ip': '10.0.0.2', 'netmask': '255.255.255.0'},
    'vmx1': {'mac': '00:0c:29:a3:6d:a8', 'ip': '192.168.1.1', 'netmask': '255.255.255.0'},
})


@pytest.fixture
def base_patches():
    """Patch psutil + subprocess so BaseCollector.__init__ completes on any OS."""
    with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS), \
         patch('psutil.net_if_addrs', return_value=MOCK_ADDRS), \
         patch('psutil.cpu_percent', return_value=25.0), \
         patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
         patch('psutil.boot_time', return_value=1000000.0), \
         patch('subprocess.run', return_value=MagicMock(stdout=b'', returncode=0)), \
         patch('subprocess.check_output', return_value=b''), \
         patch('builtins.open', side_effect=FileNotFoundError), \
         patch('os.path.exists', return_value=False), \
         patch('os.path.getmtime', side_effect=FileNotFoundError):
        yield


@pytest.fixture
def bsd_patches():
    """Patch psutil + subprocess for FreeBSD-style interfaces."""
    with patch('psutil.net_io_counters', return_value=MOCK_COUNTERS_BSD), \
         patch('psutil.net_if_addrs', return_value=MOCK_ADDRS_BSD), \
         patch('psutil.cpu_percent', return_value=25.0), \
         patch('psutil.virtual_memory', return_value=MagicMock(percent=40.0)), \
         patch('psutil.boot_time', return_value=1000000.0), \
         patch('subprocess.run', return_value=MagicMock(stdout=b'', returncode=0)), \
         patch('subprocess.check_output', return_value=b''), \
         patch('builtins.open', side_effect=FileNotFoundError), \
         patch('os.path.exists', return_value=False), \
         patch('os.path.getmtime', side_effect=FileNotFoundError):
        yield
