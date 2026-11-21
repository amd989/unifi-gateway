"""Inform payload for UniFi OS gateway models (UXG = Gateway Lite) vs the classic USG."""
import json
from types import SimpleNamespace

import pytest

import unifi_protocol
from unifi_protocol import _create_complete_inform, is_unifi_os_gateway


def _dc():
    stats = {'rx_bytes': '1000', 'tx_bytes': '2000', 'rx_packets': '10', 'tx_packets': '20',
             'rx_errors': '0', 'tx_errors': '0', 'rx_dropped': '0', 'tx_dropped': '0',
             'rx_multicast': '0', 'rx_bps': 0, 'tx_bps': 0}
    return SimpleNamespace(data={
        'ifstat': {'eth0': dict(stats), 'eth1': dict(stats, rx_bytes='5000')},
        'ip': {'eth0': {'address': '192.168.1.1', 'netmask': '255.255.255.0'},
               'eth1': {'address': '203.0.113.7', 'netmask': '255.255.255.0', 'gateway': '203.0.113.1'}},
        'macs': {'eth0': '02:00:00:00:00:01', 'eth1': '02:00:00:00:00:02'},
        'dhcp_leases': [{'expiry': '1794602129', 'mac': '02:11:22:33:44:55', 'ip': '192.168.1.50',
                         'hostname': 'nas'}],
        'nameservers': ['1.1.1.1'],
    })


@pytest.fixture
def config(gateway_config):
    gateway_config.set('gateway', 'ports',
                       '[{"ifname":"eth0","name":"LAN","type":"lan","realif":"br-lan"},'
                       '{"ifname":"eth1","name":"WAN","type":"wan","realif":"wan"}]')
    for k, v in {'device_display': 'Gateway Lite', 'url': 'http://c/inform', 'lan_ip': '192.168.1.1',
                 'lan_mac': '02:00:00:00:00:01', 'hostname': 'gw'}.items():
        gateway_config.set('gateway', k, v)
    return gateway_config


@pytest.fixture(autouse=True)
def _no_system_calls(monkeypatch):
    monkeypatch.setattr(unifi_protocol, 'uptime', lambda: 100)


def _inform(config, device):
    config.set('gateway', 'device', device)
    return json.loads(_create_complete_inform(config, _dc()))


def test_model_detection(config):
    config.set('gateway', 'device', 'UGW3')
    assert not is_unifi_os_gateway(config)
    config.set('gateway', 'device', 'UXG')
    assert is_unifi_os_gateway(config)


def test_usg_inform_unchanged(config):
    p = _inform(config, 'UGW3')
    assert 'port_table' not in p
    assert 'udapi_version' not in p


def test_uxg_port_table_names_and_live_counters(config):
    p = _inform(config, 'UXG')
    ports = {e['name']: e for e in p['port_table']}
    assert [e['port_idx'] for e in p['port_table']] == [1, 2]
    assert ports['eth0']['ifname'] == 'eth0' and not ports['eth0']['is_uplink']
    assert ports['eth1']['is_uplink'] and ports['eth1']['mac'] == '02:00:00:00:00:02'
    # live counters as ints: the controller merges this entry over if_table when it builds the WAN stats
    assert ports['eth1']['rx_bytes'] == 5000 and ports['eth1']['tx_packets'] == 20


def test_uxg_udapi_version_detail(config):
    detail = _inform(config, 'UXG')['udapi_version']['versionDetail']
    assert detail['services/dhcpServers'] == 2
    assert detail['interfaces'] == 40


def test_lease_expiry_and_count(config):
    p = _inform(config, 'UXG')
    assert p['ipv4_active_leases'][0]['leaseExpiry'] == 1794602129
    lan = next(n for n in p['network_table'] if n['name'] == 'eth0')
    assert lan['active_dhcp_lease_count'] == 1


def test_extra_inform_overrides(config):
    config.set('gateway', 'extra_inform', '{"udapi_version": {"versionDetail": {"interfaces": 41}}}')
    assert _inform(config, 'UXG')['udapi_version']['versionDetail'] == {'interfaces': 41}


@pytest.mark.parametrize('raw', ['{"udapi_version": ', '[1, 2]'])
def test_extra_inform_invalid_is_skipped(config, raw, caplog):
    config.set('gateway', 'extra_inform', raw)
    p = _inform(config, 'UXG')  # still builds an inform
    assert p['udapi_version']['versionDetail']['interfaces'] == 40
    assert 'extra_inform' in caplog.text


def test_port_table_mac_is_the_collectors(config):
    """port_table uses the collector's normalized MAC, not the raw configured one."""
    config.set('gateway', 'ports',
               '[{"ifname":"eth0","name":"LAN","type":"lan","realif":"br-lan"},'
               '{"ifname":"eth1","name":"WAN","type":"wan","realif":"pppoe-wan","mac":"1A-0E-E3-DE-26-90"}]')
    dc = _dc()
    dc.data['macs']['eth1'] = '1a:0e:e3:de:26:90'
    config.set('gateway', 'device', 'UXG')
    ports = {e['name']: e for e in json.loads(_create_complete_inform(config, dc))['port_table']}
    assert ports['eth1']['mac'] == '1a:0e:e3:de:26:90'


def _dc_with(**extra):
    dc = _dc()
    dc.data['ifstat']['eth1'].update({'rx_bps': 1250, 'tx_bps': 250})
    dc.data.update(extra)
    return dc


def _inform_dc(config, dc, device='UXG'):
    config.set('gateway', 'device', device)
    return json.loads(_create_complete_inform(config, dc))


def test_port_table_live_rates(config):
    wan = {e['name']: e for e in _inform_dc(config, _dc_with())['port_table']}['eth1']
    # collector rates are bytes/s; *_bytes-r is bytes/s, *_rate is bits/s
    assert (wan['rx_bytes-r'], wan['tx_bytes-r'], wan['bytes-r']) == (1250, 250, 1500)
    assert (wan['rx_rate'], wan['tx_rate']) == (10000, 2000)


def test_link_speed_and_media(config):
    p = _inform_dc(config, _dc_with(link_speeds={'eth0': 2500}))
    ports = {e['name']: e for e in p['port_table']}
    assert (ports['eth0']['speed'], ports['eth0']['media']) == (2500, '2.5GE')
    assert (ports['eth1']['speed'], ports['eth1']['media']) == (1000, 'GE')   # unknown -> 1000
    assert {i['name']: i['speed'] for i in p['if_table']}['eth0'] == 2500
    assert {n['name']: n['speed'] for n in p['network_table']}['eth0'] == '2500'


def test_configured_speed_wins(config):
    config.set('gateway', 'ports',
               '[{"ifname":"eth0","name":"LAN","type":"lan","realif":"br-lan","speed":10000},'
               '{"ifname":"eth1","name":"WAN","type":"wan","realif":"wan"}]')
    ports = {e['name']: e for e in _inform_dc(config, _dc_with(link_speeds={'eth0': 2500}))['port_table']}
    assert (ports['eth0']['speed'], ports['eth0']['media']) == (10000, '10GE')


def test_uptime_stats_wan_online(config):
    wan = _inform_dc(config, _dc_with(latency=3.5))['uptime_stats']['WAN']
    assert wan['uptime'] == 100 and wan['availability'] == 100
    # the controller takes latency/availability from alerting_monitors[0]
    first = wan['alerting_monitors'][0]
    assert (first['availability'], first['latency_average'], first['type']) == (100, 4, 'icmp')


def test_uptime_stats_wan_unreachable(config):
    wan = _inform_dc(config, _dc_with(latency=0))['uptime_stats']['WAN']
    assert wan['uptime'] == 100            # link has an IP: up
    assert wan['availability'] == 0 and wan['alerting_monitors'][0]['availability'] == 0


def test_usg_has_no_uptime_stats(config):
    assert 'uptime_stats' not in _inform_dc(config, _dc_with(latency=3.5), device='UGW3')
