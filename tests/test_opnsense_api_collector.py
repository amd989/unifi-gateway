# -*- coding: utf-8 -*-
"""Tests for the remote OPNsense API collector (platform = opnsense-api)."""
import configparser
import copy
import json

import pytest

import tools
import unifi_protocol
from collectors import create_collector
from collectors import opnsense_api
from collectors.opnsense_api import (
    OPNsenseApiCollector, OPNsenseApiError, parse_cpu_from_top, parse_media_speed, parse_ms, parse_uptime,
)

IFINFO = {
    'total': 3, 'rowCount': 3, 'current': 1,
    'rows': [
        {
            'device': 'igc0', 'identifier': 'wan', 'description': 'WAN', 'status': 'up',
            'macaddr': 'aa:bb:cc:00:00:01', 'media': '1000baseT <full-duplex>',
            'ipv4': [{'ipaddr': '203.0.113.84/22'}], 'addr4': '203.0.113.84/22',
            'gateways': ['203.0.113.1', 'fe80::1'],
            'ifctl.nameserver': ['198.51.100.53', '198.51.100.54'],
            'statistics': {
                'bytes received': '1000', 'bytes transmitted': '2000',
                'packets received': '10', 'packets transmitted': '20',
                'input errors': '1', 'output errors': '2',
                'input queue drops': '3', 'send queue drops': '4',
                'multicasts received': '5', 'multicasts transmitted': '6',
                'line rate': '1000 Mbit/s',
            },
        },
        {
            'device': 'igc1', 'identifier': 'lan', 'description': 'LAN', 'status': 'up',
            'macaddr': 'aa:bb:cc:00:00:02', 'media': '2500Base-T <full-duplex>',
            'ipv4': [{'ipaddr': '192.168.1.1/24'}], 'gateways': [],
            'statistics': {'bytes received': '5000', 'bytes transmitted': '7000'},
        },
        {
            'device': 'vlan01', 'identifier': 'opt1', 'description': 'IOT', 'status': 'up',
            'macaddr': 'aa:bb:cc:00:00:02', 'media': 'Ethernet autoselect',
            'ipv4': [{'ipaddr': '10.0.10.1/24'}],
            'statistics': {'bytes received': '1', 'bytes transmitted': '1', 'line rate': '0 bit/s'},
        },
    ],
}

RESPONSES = {
    '/api/interfaces/overview/interfaces_info/1': IFINFO,
    '/api/routes/gateway/status': {'status': 'ok', 'items': [
        {'name': 'WAN_DHCP6', 'address': 'fe80::1', 'delay': '9.0 ms', 'loss': '0.0 %', 'status': 'none'},
        {'name': 'WAN_DHCP', 'address': '203.0.113.1', 'delay': '12.3 ms', 'loss': '0.0 %', 'status': 'none'},
    ]},
    '/api/diagnostics/interface/get_arp': [
        {'mac': 'aa:bb:cc:00:00:02', 'ip': '192.168.1.1', 'intf': 'igc1', 'permanent': True, 'hostname': ''},
        {'mac': '11:22:33:44:55:66', 'ip': '192.168.1.50', 'intf': 'igc1', 'permanent': False, 'hostname': ''},
        {'mac': '11:22:33:44:55:77', 'ip': '192.168.1.51', 'intf': 'igc1', 'permanent': False,
         'hostname': 'printer.lan'},
        {'mac': 'de:ad:be:ef:00:01', 'ip': '203.0.113.1', 'intf': 'igc0', 'permanent': False, 'hostname': ''},
    ],
    '/api/kea/leases4/search': {'rows': [
        {'address': '192.168.1.50', 'hwaddr': '11:22:33:44:55:66', 'expire': 1790000000,
         'hostname': 'laptop.', 'state': 0},
        {'address': '192.168.1.60', 'hwaddr': '11:22:33:44:55:88', 'expire': 1790000000,
         'hostname': '', 'state': 2},
        {'address': '192.168.1.61', 'hwaddr': '11:22:33:44:55:99', 'expire': 1790000000,
         'hostname': 'phone', 'state': 0},
    ]},
    '/api/diagnostics/system/system_time': {'uptime': '3 days, 04:12:33', 'loadavg': '0.1, 0.1, 0.1'},
    '/api/diagnostics/system/system_resources': {'memory': {'total': '8000000000', 'used': 2000000000}},
    '/api/diagnostics/system/system_information': {'name': 'fw.home.lan', 'versions': []},
    '/api/diagnostics/activity/get_activity': {'headers': [
        'last pid: 1; load averages: 0.1, 0.1, 0.1 up 3+04:12:33 12:00:00',
        'CPU:  2.3% user,  0.0% nice,  1.2% system,  0.1% interrupt, 96.4% idle',
    ], 'details': []},
}


class FakeApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(path)
        if path not in self.responses:
            raise OPNsenseApiError(f'GET {path}: HTTP 404')
        return copy.deepcopy(self.responses[path])


@pytest.fixture
def api_config():
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        'global': {'pid_file': 'test.pid'},
        'gateway': {
            'ports': '[{"ifname":"eth0","name":"WAN","type":"wan","realif":"wan"},'
                     '{"ifname":"eth1","name":"LAN","type":"lan","realif":"igc1"},'
                     '{"ifname":"eth2","name":"LAN2","type":"lan2","realif":"opt1"}]',
            'is_adopted': 'True',
            'platform': 'opnsense-api',
            'lan_ip': '192.168.1.1',
            'lan_mac': 'aa:bb:cc:00:00:02',
            'firmware': '4.4.57.5578372',
            'device': 'UGW3',
            'device_display': 'UniFi-Gateway-3',
            'url': 'http://controller:8080/inform',
        },
        'opnsense': {'api_url': 'https://192.168.1.1', 'api_key': 'k', 'api_secret': 's'},
        'provisioned': {},
    })
    return cfg


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi(copy.deepcopy(RESPONSES))
    monkeypatch.setattr(OPNsenseApiCollector, '_make_client', classmethod(lambda cls, config: api))
    yield api
    tools.set_host_provider()


@pytest.fixture
def collector(api_config, fake_api):
    return create_collector(api_config)


class TestParsers:

    @pytest.mark.parametrize('media,speed', [
        ('1000baseT <full-duplex>', 1000), ('2500Base-T <full-duplex>', 2500),
        ('10Gbase-T <full-duplex>', 10000), ('100baseTX <full-duplex>', 100),
        ('Ethernet autoselect', 0), (None, 0),
    ])
    def test_media_speed(self, media, speed):
        assert parse_media_speed(media) == speed

    @pytest.mark.parametrize('text,secs', [
        ('3 days, 04:12:33', 3 * 86400 + 4 * 3600 + 12 * 60 + 33),
        ('1 dag, 00:00:01', 86401),
        ('04:12:33', 4 * 3600 + 12 * 60 + 33),
        ('', None), ('N/A', None),
    ])
    def test_uptime(self, text, secs):
        assert parse_uptime(text) == secs

    def test_cpu(self):
        assert parse_cpu_from_top(RESPONSES['/api/diagnostics/activity/get_activity']['headers']) == 4
        assert parse_cpu_from_top([]) is None

    def test_ms(self):
        assert parse_ms('12.3 ms') == 12.3
        assert parse_ms('~') is None


class TestCollector:

    def test_factory_selects_api_collector(self, collector):
        assert isinstance(collector, OPNsenseApiCollector)

    def test_macs_match_device_and_identifier(self, collector):
        assert collector.data['macs'] == {
            'eth0': 'aa:bb:cc:00:00:01', 'eth1': 'aa:bb:cc:00:00:02', 'eth2': 'aa:bb:cc:00:00:02'}

    def test_addresses_and_gateway(self, collector):
        ip = collector.data['ip']
        assert ip['eth0'] == {'address': '203.0.113.84', 'netmask': '255.255.252.0', 'gateway': '203.0.113.1'}
        assert ip['eth1'] == {'address': '192.168.1.1', 'netmask': '255.255.255.0'}
        assert ip['eth2']['address'] == '10.0.10.1'

    def test_ifstat_counters_and_rates(self, collector, fake_api, monkeypatch):
        st = collector.data['ifstat']['eth0']
        assert st['rx_bytes'] == '1000' and st['tx_bytes'] == '2000'
        assert st['rx_dropped'] == '3' and st['tx_dropped'] == '4' and st['rx_multicast'] == '5'
        assert st['rx_bps'] == 0

        wan = fake_api.responses['/api/interfaces/overview/interfaces_info/1']['rows'][0]['statistics']
        wan['bytes received'] = '11000'
        wan['bytes transmitted'] = '1000'   # counter reset -> 0, not negative
        collector._prev_time -= 10
        collector.update()
        st = collector.data['ifstat']['eth0']
        assert 900 <= st['rx_bps'] <= 1000
        assert st['tx_bps'] == 0

    def test_link_speeds(self, collector):
        assert collector.data['link_speeds'] == {'eth0': 1000, 'eth1': 2500}

    def test_leases_kea_active_only(self, collector):
        leases = {lease['ip']: lease for lease in collector.data['dhcp_leases']}
        assert set(leases) == {'192.168.1.50', '192.168.1.61'}
        assert leases['192.168.1.50']['hostname'] == 'laptop'

    def test_leases_fall_back_to_dnsmasq(self, api_config, fake_api):
        del fake_api.responses['/api/kea/leases4/search']
        fake_api.responses['/api/dnsmasq/leases/search'] = {'rows': [
            {'address': '192.168.1.70', 'hwaddr': '11:22:33:44:55:aa', 'expire': 1, 'hostname': 'tv'},
            {'address': 'fd00::70', 'hwaddr': '11:22:33:44:55:aa', 'expire': 1, 'hostname': 'tv'},
        ]}
        c = create_collector(api_config)
        assert c.data['dhcp_leases'] == [{'ip': '192.168.1.70', 'mac': '11:22:33:44:55:aa',
                                          'expiry': '1', 'hostname': 'tv'}]

    def test_host_table_lan_only(self, collector):
        hosts = {h['mac']: h for h in collector.data['host_table']}
        assert 'aa:bb:cc:00:00:02' not in hosts      # router's own permanent entry
        assert 'de:ad:be:ef:00:01' not in hosts      # WAN neighbor
        assert hosts['11:22:33:44:55:66']['hostname'] == 'laptop'
        assert hosts['11:22:33:44:55:77']['hostname'] == 'printer.lan'
        assert '11:22:33:44:55:99' in hosts          # lease without ARP entry

    def test_latency_from_dpinger(self, collector):
        assert collector.data['latency'] == 12.3

    def test_nameservers_from_wan(self, collector):
        assert collector.data['nameservers'] == ['198.51.100.53', '198.51.100.54']

    def test_nameservers_override(self, api_config, fake_api):
        api_config.set('opnsense', 'nameservers', '1.1.1.1, 9.9.9.9')
        assert create_collector(api_config).data['nameservers'] == ['1.1.1.1', '9.9.9.9']

    def test_system_stats(self, collector):
        assert collector.data['system_stats'] == {'cpu': '4', 'mem': '25'}

    def test_router_uptime_and_hostname(self, collector):
        expected = 3 * 86400 + 4 * 3600 + 12 * 60 + 33
        assert abs(tools.uptime() - expected) <= 2
        assert tools.get_hostname() == 'fw'

    def test_api_down_does_not_crash(self, api_config, fake_api):
        fake_api.responses.clear()
        c = create_collector(api_config)
        assert c.data['ifstat'] == {}
        assert c.data['macs']['eth0'] == '00:00:00:00:00:00'

    def test_complete_inform_builds(self, collector, api_config):
        payload = json.loads(unifi_protocol._create_complete_inform(api_config, collector))
        assert payload['hostname'] == 'fw'
        names = {e['name']: e for e in payload['if_table']}
        assert names['eth0']['gateways'] == ['203.0.113.1']
        assert names['eth0']['latency'] == 12.3


class TestClientConfig:

    def test_env_overrides(self, api_config, monkeypatch):
        monkeypatch.setenv('OPNSENSE_API_URL', 'https://fw.example:4443/')
        monkeypatch.setenv('OPNSENSE_VERIFY_SSL', 'false')
        client = OPNsenseApiCollector._make_client(api_config)
        assert client.base_url == 'https://fw.example:4443'
        assert client._ssl.verify_mode == opnsense_api.ssl.CERT_NONE

    def test_missing_credentials(self, api_config):
        api_config.remove_option('opnsense', 'api_secret')
        with pytest.raises(ValueError):
            OPNsenseApiCollector._make_client(api_config)

    def test_secret_file(self, api_config, tmp_path):
        api_config.remove_option('opnsense', 'api_secret')
        f = tmp_path / 'secret'
        f.write_text('s3cret\n')
        api_config.set('opnsense', 'api_secret_file', str(f))
        assert OPNsenseApiCollector._make_client(api_config) is not None


class TestEnvConfig:

    def test_defaults_without_config_file(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        apply_env_config(cfg, environ={
            'UNIFI_GW_PLATFORM': 'opnsense-api',
            'UNIFI_GW_PORTS': '[{"ifname":"eth0","name":"WAN","type":"wan","realif":"wan"}]',
            'UNIFI_GW_LAN_IP': '192.168.1.1',
            'UNIFI_GW_LAN_MAC': 'aa:bb:cc:00:00:02',
            'UNIFI_GW_HOSTNAME': '  ',
        })
        assert cfg.get('gateway', 'platform') == 'opnsense-api'
        assert cfg.get('gateway', 'lan_mac') == 'aa:bb:cc:00:00:02'
        assert cfg.get('gateway', 'is_adopted') == 'False'
        assert cfg.get('global', 'pid_file') == 'unifi-gateway.pid'
        assert not cfg.has_option('gateway', 'hostname')   # blank env values are ignored
        assert cfg.has_section('provisioned')

    def test_env_never_touches_adoption_state(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        cfg.read_dict({'gateway': {'is_adopted': 'True', 'key': 'abc', 'lan_ip': '10.0.0.1'}})
        apply_env_config(cfg, environ={'UNIFI_GW_IS_ADOPTED': 'False', 'UNIFI_GW_KEY': 'x',
                                       'UNIFI_GW_LAN_IP': '10.0.0.2'})
        assert cfg.get('gateway', 'is_adopted') == 'True'
        assert cfg.get('gateway', 'key') == 'abc'
        assert cfg.get('gateway', 'lan_ip') == '10.0.0.2'

    def test_opnsense_api_default_ports(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        apply_env_config(cfg, environ={'UNIFI_GW_PLATFORM': 'opnsense-api'})
        ports = json.loads(cfg.get('gateway', 'ports'))
        assert [p['realif'] for p in ports] == ['wan', 'lan']


class TestLanIdentity:

    def test_lan_ip_and_mac_from_api(self, api_config, fake_api):
        api_config.remove_option('gateway', 'lan_ip')
        api_config.set('gateway', 'lan_mac', '00:00:00:00:00:00')
        create_collector(api_config)
        assert api_config.get('gateway', 'lan_mac') == 'aa:bb:cc:00:00:02'
        assert api_config.get('gateway', 'lan_ip') == '192.168.1.1'

    def test_configured_identity_kept(self, api_config, fake_api):
        api_config.set('gateway', 'lan_mac', 'aa:aa:aa:aa:aa:aa')
        create_collector(api_config)
        assert api_config.get('gateway', 'lan_mac') == 'aa:aa:aa:aa:aa:aa'


class TestAutoPlatform:

    def test_api_url_selects_opnsense_api(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        apply_env_config(cfg, environ={'OPNSENSE_API_URL': 'https://192.168.1.1'})
        assert cfg.get('gateway', 'platform') == 'opnsense-api'
        assert 'realif' in cfg.get('gateway', 'ports')

    def test_explicit_platform_wins(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        apply_env_config(cfg, environ={'OPNSENSE_API_URL': 'https://x', 'UNIFI_GW_PLATFORM': 'linux'})
        assert cfg.get('gateway', 'platform') == 'linux'

    def test_missing_ports_is_a_clear_error(self):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        apply_env_config(cfg, environ={'UNIFI_GW_PLATFORM': 'linux'})
        with pytest.raises(SystemExit, match='No \\[gateway\\] ports'):
            create_collector(cfg)


class TestDhcpProbeLogging:

    def _forbid_kea(self, fake_api):
        resp = fake_api.responses.pop('/api/kea/leases4/search')
        fake_api.responses['/api/dnsmasq/leases/search'] = resp and {'rows': [
            {'address': '192.168.1.70', 'hwaddr': '11:22:33:44:55:aa', 'expire': 1, 'hostname': 'tv'}]}
        real_get = fake_api.get

        def get(path, params=None):
            if path == '/api/kea/leases4/search':
                raise OPNsenseApiError('GET /api/kea/leases4/search: HTTP 403', status=403)
            return real_get(path, params)
        fake_api.get = get

    def test_auto_probe_403_is_not_an_error(self, api_config, fake_api, caplog):
        self._forbid_kea(fake_api)
        c = create_collector(api_config)
        assert c._dhcp_backend == 'dnsmasq'
        assert not [r for r in caplog.records if r.levelname == 'ERROR']

    def test_explicit_backend_403_is_an_error(self, api_config, fake_api, caplog):
        self._forbid_kea(fake_api)
        api_config.set('opnsense', 'dhcp_backend', 'kea')
        create_collector(api_config)
        assert any('HTTP 403' in r.getMessage() for r in caplog.records if r.levelname == 'ERROR')


class TestModelPreset:

    def _cfg(self, environ, existing=None):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        if existing:
            cfg.read_dict(existing)
        apply_env_config(cfg, environ=environ)
        return cfg

    def test_uxg_preset_and_unifi_os_port_layout(self):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'gateway-lite', 'OPNSENSE_API_URL': 'https://x'})
        assert cfg.get('gateway', 'device') == 'UXG'
        assert cfg.get('gateway', 'device_display') == 'Gateway Lite'
        assert cfg.get('gateway', 'firmware') == '6.0.17.36539'
        ports = json.loads(cfg.get('gateway', 'ports'))
        assert [(p['ifname'], p['type'], p['realif']) for p in ports] == [('eth0', 'lan', 'lan'), ('eth1', 'wan', 'wan')]

    def test_upgraded_firmware_is_kept(self):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg'},
                        {'gateway': {'device': 'UXG', 'firmware': '6.1.2.99999', 'is_adopted': 'True'}})
        assert cfg.get('gateway', 'firmware') == '6.1.2.99999'

    def test_model_switch_resets_firmware(self):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg'}, {'gateway': {'device': 'UGW3', 'firmware': '4.4.57.5578372'}})
        assert cfg.get('gateway', 'firmware') == '6.0.17.36539'

    def test_explicit_values_override_preset(self):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg', 'UNIFI_GW_FIRMWARE': '7.0.0.1', 'UNIFI_GW_DEVICE_DISPLAY': 'Lab GW'})
        assert cfg.get('gateway', 'firmware') == '7.0.0.1'
        assert cfg.get('gateway', 'device_display') == 'Lab GW'

    def test_unknown_model_ignored(self):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'udm-pro'})
        assert cfg.get('gateway', 'device') == 'UGW3'

    def test_no_model_leaves_existing_usg_alone(self):
        cfg = self._cfg({}, {'gateway': {'device': 'UGW3', 'firmware': '4.4.57.5578372'}})
        assert cfg.get('gateway', 'firmware') == '4.4.57.5578372'

    def test_uxg_complete_inform_from_opnsense(self, fake_api):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg', 'OPNSENSE_API_URL': 'https://x', 'OPNSENSE_API_KEY': 'k',
                         'OPNSENSE_API_SECRET': 's'})
        cfg.set('gateway', 'url', 'http://controller:8080/inform')
        dc = create_collector(cfg)
        payload = json.loads(unifi_protocol._create_complete_inform(cfg, dc))
        assert payload['model'] == 'UXG'
        ports = {p['name']: p for p in payload['port_table']}
        assert set(ports) == {'eth0', 'eth1'}
        assert ports['eth1']['speed'] == 1000          # WAN: igc0, 1000baseT
        assert 'versionDetail' in payload['udapi_version']
        wan = payload['uptime_stats']['WAN']
        assert wan['uptime'] > 0                       # WAN shown online
        assert wan['alerting_monitors'][0]['latency_average'] == 12     # dpinger latency from OPNsense


class TestPortLayoutOnModelSwitch:

    USG_PORTS = ('[{"ifname": "eth0", "name": "WAN", "type": "wan", "realif": "wan"}, '
                 '{"ifname": "eth1", "name": "LAN", "type": "lan", "realif": "lan"}]')

    def _cfg(self, environ, gateway):
        from unifi_gateway import apply_env_config
        cfg = configparser.RawConfigParser()
        cfg.read_dict({'gateway': gateway})
        apply_env_config(cfg, environ=environ)
        return cfg

    def test_saved_usg_default_ports_follow_switch_to_uxg(self):
        # the reported case: config saved as USG (eth0 = WAN), then UNIFI_GW_MODEL=uxg
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg', 'UNIFI_GW_PLATFORM': 'opnsense-api'},
                        {'device': 'UGW3', 'ports': self.USG_PORTS, 'is_adopted': 'True'})
        wan = [p for p in json.loads(cfg.get('gateway', 'ports')) if p['type'] == 'wan'][0]
        assert wan['ifname'] == 'eth1' and wan['realif'] == 'wan'

    def test_explicit_ports_kept_with_warning(self, caplog):
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg', 'UNIFI_GW_PLATFORM': 'opnsense-api',
                         'UNIFI_GW_PORTS': self.USG_PORTS}, {})
        assert '"eth0", "name": "WAN"' in cfg.get('gateway', 'ports')
        assert any('controller expects the WAN' in r.getMessage() for r in caplog.records)

    def test_custom_ports_kept(self):
        custom = ('[{"ifname": "eth0", "name": "LAN", "type": "lan", "realif": "igc1"}, '
                  '{"ifname": "eth1", "name": "WAN", "type": "wan", "realif": "pppoe0"}, '
                  '{"ifname": "eth2", "name": "IOT", "type": "lan2", "realif": "opt1"}]')
        cfg = self._cfg({'UNIFI_GW_MODEL': 'uxg', 'UNIFI_GW_PLATFORM': 'opnsense-api'},
                        {'device': 'UXG', 'ports': custom})
        assert cfg.get('gateway', 'ports') == custom

    def test_usg_layout_untouched_and_no_warning(self, caplog):
        cfg = self._cfg({'UNIFI_GW_PLATFORM': 'opnsense-api'}, {'device': 'UGW3', 'ports': self.USG_PORTS})
        assert cfg.get('gateway', 'ports') == self.USG_PORTS
        assert not any('controller expects the WAN' in r.getMessage() for r in caplog.records)
