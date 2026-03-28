# -*- coding: utf-8 -*-
import time
import json
import sys
import ast
from Crypto import Random

import zlib
try:
    import snappy
except ImportError:
    pass

from Crypto.Cipher import AES
from struct import pack, unpack
from binascii import a2b_hex

from tlv import UnifiTLV
from tools import (
    mac_string_2_array, ip_string_2_array, netmask_to_cidr,
    uptime, get_hostname, get_if_table, get_network_table,
)

MASTER_KEY = "ba86f2bbe107c7c57eb5f2690775c712"


def encode_inform(config, data, encryption='CBC'):
    iv = Random.new().read(16)
    key = MASTER_KEY

    if config.getboolean('gateway', 'is_adopted'):
        key = config.get('gateway', 'key')
        if config.getboolean('gateway', 'use_aes_gcm'):
            encryption = 'GCM'
        else:
            encryption = 'CBC'

    mac = config.get('gateway', 'lan_mac')

    # Flags: bit 0 = encrypted, bit 1 = zlib, bit 2 = snappy, bit 3 = GCM
    flags = 0x01
    if encryption == 'GCM':
        flags |= 0x08

    if 'snappy' in sys.modules:
        payload = snappy.compress(data.encode('utf-8'))
        flags |= 0x04
    else:
        payload = zlib.compress(data.encode('utf-8'))
        flags |= 0x02

    encoded_data = b'TNBU'
    encoded_data += pack('>I', 1)
    encoded_data += pack('BBBBBB', *mac_string_2_array(mac))
    encoded_data += pack('>H', flags)
    encoded_data += iv
    encoded_data += pack('>I', 1)

    if encryption == 'GCM':
        encoded_data += pack('>I', len(payload) + 16)
        _aes = AES.new(a2b_hex(key), AES.MODE_GCM, iv)
        _aes.update(encoded_data)
        payload, tag = _aes.encrypt_and_digest(payload)
        payload += tag
    elif encryption == 'CBC':
        pad_len = AES.block_size - (len(payload) % AES.block_size)
        payload += bytes([pad_len]) * pad_len
        payload = AES.new(a2b_hex(key), AES.MODE_CBC, iv).encrypt(payload)
        encoded_data += pack('>I', len(payload))

    encoded_data += payload
    return encoded_data


def decode_inform(config, encoded_data):
    magic = encoded_data[0:4]
    if magic != b'TNBU':
        raise Exception("Missing magic in response: '{}' instead of 'TNBU'".format(magic))

    header = encoded_data[:40]
    flags = unpack('>H', encoded_data[14:16])[0]
    iv = encoded_data[16:32]
    payload_len = unpack('>I', encoded_data[36:40])[0]
    payload = encoded_data[40:(40 + payload_len)]

    flag = {
        'encrypted': bool(flags & 0x01),
        'zlibCompressed': bool(flags & 0x02),
        'SnappyCompression': bool(flags & 0x04),
        'encryptedGCM': bool(flags & 0x08),
    }

    key = MASTER_KEY
    if config.getboolean('gateway', 'is_adopted'):
        key = config.get('gateway', 'key')

    if flag['encrypted']:
        if flag['encryptedGCM']:
            tag = payload[-16:]
            payload = payload[:-16]
            _aes = AES.new(a2b_hex(key), AES.MODE_GCM, iv)
            _aes.update(header)
            payload = _aes.decrypt_and_verify(payload, tag)
        else:
            payload = AES.new(a2b_hex(key), AES.MODE_CBC, iv).decrypt(payload)
            pad_size = payload[-1]
            if pad_size > AES.block_size:
                raise Exception('Response not padded or padding is corrupt')
            payload = payload[:(len(payload) - pad_size)]

    if flag['SnappyCompression'] and 'snappy' in sys.modules:
        payload = snappy.decompress(payload)
    elif flag['zlibCompressed']:
        payload = zlib.decompress(payload)

    return json.loads(payload)


def _resolve_lan_identity(config, dc, lan_if):
    """Get LAN MAC and IP, preferring live data but falling back to config."""
    mac = dc.data.get('macs', {}).get(lan_if)
    if not mac:
        mac = config.get('gateway', 'lan_mac')
    ip_info = dc.data.get('ip', {}).get(lan_if, {})
    ip_addr = ip_info.get('address')
    if not ip_addr:
        ip_addr = config.get('gateway', 'lan_ip')
    return mac, ip_addr, ip_info


def _create_partial_inform(config, dc):
    ports = ast.literal_eval(config.get('gateway', 'ports'))
    lan_if = [d['ifname'] for d in ports if d['type'].lower() == 'lan'][0]
    mac, ip_addr, _ = _resolve_lan_identity(config, dc, lan_if)

    return json.dumps({
        'hostname': 'UBNT',
        'state': 0,
        'default': True,
        'inform_url': config.get('gateway', 'url'),
        'mac': mac,
        'ip': ip_addr,
        'model': config.get('gateway', 'device'),
        'model_display': config.get('gateway', 'device_display'),
        'version': config.get('gateway', 'firmware'),
        'uptime': uptime(),
    })


def _create_complete_inform(config, dc):
    ports = ast.literal_eval(config.get('gateway', 'ports'))
    lan_if = [d['ifname'] for d in ports if d['type'].lower() == 'lan'][0]
    wan_if = [d['ifname'] for d in ports if d['type'].lower() == 'wan'][0]
    mac, ip_addr, ip_info = _resolve_lan_identity(config, dc, lan_if)
    netmask = ip_info.get('netmask', '255.255.255.0')

    hostname = get_hostname()
    if config.has_option('gateway', 'hostname'):
        hostname = config.get('gateway', 'hostname')

    system_stats = dc.data.get('system_stats', {'cpu': '0', 'mem': '0'})

    active_leases = []
    for lease in dc.data.get('dhcp_leases', []):
        entry = {'mac': lease['mac'], 'ip': lease['ip']}
        if 'hostname' in lease:
            entry['hostname'] = lease['hostname']
        active_leases.append(entry)

    speedtest = dc.data.get('speedtest', {})
    has_speedtest = speedtest.get('download', 0) > 0

    payload = {
        'bootrom_version': 'unknown',
        'cfgversion': config.get('provisioned', 'cfgversion') if config.has_option('provisioned', 'cfgversion') else '',
        'config_network_wan': {'type': 'dhcp'},
        'config_port_table': ports,
        'connect_request_ip': ip_addr,
        'connect_request_port': '36424',
        'default': False,
        'state': 2,
        'discovery_response': False,
        'fw_caps': 3,
        'guest_token': '4C1D46707239C6EB5A2366F505A44A91',
        'has_default_route_distance': True,
        'has_dnsmasq_hostfile_update': True,
        'has_dpi': False,
        'has_eth1': True,
        'has_porta': True,
        'has_ssh_disable': True,
        'has_vti': True,
        'hostname': hostname,
        'inform_url': config.get('gateway', 'url'),
        'ip': ip_addr,
        'ipv4_active_leases': active_leases,
        'isolated': False,
        'locating': config.getboolean('gateway', 'locating') if config.has_option('gateway', 'locating') else False,
        'mac': mac,
        'model': config.get('gateway', 'device'),
        'model_display': config.get('gateway', 'device_display'),
        'netmask': netmask,
        'radius_caps': 1,
        'required_version': '4.0.0',
        'selfrun_beacon': True,
        'serial': mac.replace(':', ''),
        'version': config.get('gateway', 'firmware'),
        'time': int(time.time()),
        'uplink': wan_if,
        'uptime': uptime(),
        'pfor-stats': [],
        'speedtest-status': {
            'latency': int(speedtest.get('ping', 0)),
            'rundate': speedtest.get('lastrun', 0),
            'runtime': 0,
            'status_download': 2 if has_speedtest else 0,
            'status_ping': 2 if speedtest.get('ping', 0) > 0 else 0,
            'status_summary': 2 if has_speedtest else 0,
            'status_upload': 2 if has_speedtest else 0,
            'xput_download': speedtest.get('download', 0),
            'xput_upload': speedtest.get('upload', 0),
        },
        'ddns-status': {'dyndns': []},
        'system-stats': {
            'cpu': system_stats.get('cpu', '0'),
            'mem': system_stats.get('mem', '0'),
            'uptime': str(uptime()),
        },
        'routes': _build_routes(dc, wan_if, lan_if),
        'network_table': get_network_table(dc.data, ports),
        'if_table': get_if_table(dc.data, ports),
    }

    return json.dumps(payload)


def _build_routes(dc, wan_if, lan_if):
    routes = []
    wan_gw = dc.data.get('ip', {}).get(wan_if, {}).get('gateway')
    if wan_gw:
        routes.append({
            'nh': [{'intf': wan_if, 'metric': '1/0', 't': 'S>*', 'via': wan_gw}],
            'pfx': '0.0.0.0/0',
        })

    lan_ip = dc.data.get('ip', {}).get(lan_if, {})
    if lan_ip.get('address') and lan_ip.get('netmask'):
        routes.append({
            'nh': [{'intf': lan_if, 't': 'C>*'}],
            'pfx': '%s/%s' % (lan_ip['address'], netmask_to_cidr(lan_ip['netmask'])),
        })

    return routes


def create_inform(config, dc):
    if not config.getboolean('gateway', 'is_adopted'):
        return _create_partial_inform(config, dc)
    return _create_complete_inform(config, dc)


def create_broadcast_message(config, index, version=2, command=6):
    lan_mac = config.get('gateway', 'lan_mac')
    lan_ip = config.get('gateway', 'lan_ip')
    firmware = config.get('gateway', 'firmware')
    device = config.get('gateway', 'device')

    platform_name = 'UNIFI-GW'
    if config.has_option('gateway', 'platform'):
        platform_name = config.get('gateway', 'platform')

    tlv = UnifiTLV()
    tlv.add(1, bytearray(mac_string_2_array(lan_mac)))
    tlv.add(2, bytearray(mac_string_2_array(lan_mac) + ip_string_2_array(lan_ip)))
    tlv.add(3, '{}.v{}'.format(device, firmware).encode('ascii'))
    tlv.add(10, pack('!I', uptime()))
    tlv.add(11, platform_name.encode('ascii'))
    tlv.add(12, device.encode('ascii'))
    tlv.add(19, bytearray(mac_string_2_array(lan_mac)))
    tlv.add(18, pack('!I', index))
    tlv.add(21, device.encode('ascii'))
    tlv.add(27, firmware.encode('ascii'))
    tlv.add(22, firmware.encode('ascii'))
    return tlv.get(version=version, command=command)
