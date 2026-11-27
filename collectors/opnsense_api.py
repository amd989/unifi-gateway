# -*- coding: utf-8 -*-
"""Remote OPNsense collector — reads everything through the OPNsense REST API.

Unlike the other collectors this one does not need to run on the router: it can run in a
container (or on any host) that can reach the OPNsense web GUI. Select it with

    [gateway]
    platform = opnsense-api

    [opnsense]
    api_url = https://192.168.1.1
    api_key = ...
    api_secret = ...

The `realif` of each port in `[gateway] ports` may be the OPNsense device name (igc0, vlan01)
or the interface identifier (wan, lan, opt1).

Endpoints used (all GET, read-only):
    /api/interfaces/overview/interfaces_info/1   devices, MACs, IPv4, gateways, media, counters
    /api/routes/gateway/status                   dpinger latency/loss per gateway
    /api/diagnostics/interface/get_arp           neighbor table
    /api/kea/leases4/search                      Kea DHCPv4 leases
    /api/dnsmasq/leases/search                   Dnsmasq DHCP leases
    /api/dhcpv4/leases/search_lease              ISC DHCPv4 leases (os-isc-dhcp plugin)
    /api/diagnostics/system/system_time          uptime
    /api/diagnostics/system/system_resources     memory
    /api/diagnostics/system/system_information   hostname
    /api/diagnostics/activity/get_activity       CPU (top header), polled less often
"""
import base64
import ipaddress
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import tools
from .base import BaseCollector

logger = logging.getLogger('unifi-gateway')

SECTION = 'opnsense'


class OPNsenseApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OPNsenseApiClient:
    """Minimal OPNsense API client (stdlib only)."""

    def __init__(self, url, key, secret, verify=True, timeout=10):
        if not url:
            raise ValueError('[opnsense] api_url (or OPNSENSE_API_URL) is required for platform opnsense-api')
        if not key or not secret:
            raise ValueError('[opnsense] api_key/api_secret (or OPNSENSE_API_KEY/OPNSENSE_API_SECRET) are required')
        self.base_url = url.rstrip('/')
        self.timeout = timeout
        token = base64.b64encode(f'{key}:{secret}'.encode()).decode()
        self._headers = {'Authorization': f'Basic {token}', 'Accept': 'application/json'}

        if verify is False:
            self._ssl = ssl.create_default_context()
            self._ssl.check_hostname = False
            self._ssl.verify_mode = ssl.CERT_NONE
        elif isinstance(verify, str):
            self._ssl = ssl.create_default_context(cafile=verify)
        else:
            self._ssl = ssl.create_default_context()

    def get(self, path, params=None):
        url = f'{self.base_url}/{path.lstrip("/")}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            raise OPNsenseApiError(f'GET {path}: HTTP {e.code}', status=e.code) from e
        except (urllib.error.URLError, OSError) as e:
            raise OPNsenseApiError(f'GET {path}: {e}') from e
        try:
            return json.loads(body)
        except ValueError as e:
            raise OPNsenseApiError(f'GET {path}: invalid JSON') from e


# ── Parsing helpers (pure functions, easy to test) ──────────────────────────

def parse_media_speed(media):
    """'1000baseT <full-duplex>' -> 1000, '2500Base-T' -> 2500, '10Gbase-T' -> 10000."""
    if not media:
        return 0
    m = re.search(r'(\d+(?:\.\d+)?)\s*(G)?base', str(media), re.I)
    if not m:
        return 0
    speed = float(m.group(1)) * (1000 if m.group(2) else 1)
    return int(speed)


def parse_uptime(text):
    """systemTime uptime ('3 days, 04:12:33' / '04:12:33', any language) -> seconds."""
    if not text:
        return None
    m = re.search(r'(?:(\d+)\D+?)?(\d+):(\d{2}):(\d{2})\s*$', str(text).strip())
    if not m:
        return None
    days = int(m.group(1) or 0)
    return days * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))


def parse_cpu_from_top(headers):
    """Find 'CPU: ... 96.4% idle' in top headers -> busy percent (int)."""
    for line in headers or []:
        if line.strip().upper().startswith('CPU'):
            m = re.search(r'([\d.]+)%\s*idle', line)
            if m:
                return max(0, min(100, int(round(100 - float(m.group(1))))))
    return None


def parse_ms(value):
    """'12.3 ms' -> 12.3; '~' or garbage -> None."""
    m = re.match(r'\s*([\d.]+)', str(value or ''))
    return float(m.group(1)) if m else None


def _to_int(value):
    try:
        return int(str(value).strip().split()[0])
    except (ValueError, IndexError, TypeError):
        return 0


def _first_ipv4(values):
    for v in values or []:
        try:
            if isinstance(ipaddress.ip_address(str(v).split('/')[0]), ipaddress.IPv4Address):
                return str(v).split('/')[0]
        except ValueError:
            continue
    return None


def _rows(response):
    if isinstance(response, dict):
        return response.get('rows') or []
    if isinstance(response, list):
        return response
    return []


class OPNsenseApiCollector(BaseCollector):

    IFINFO_TTL = 2          # seconds; one interfaces_info call per update cycle
    LEASES_TTL = 30
    SYSINFO_TTL = 3600

    def __init__(self, config):
        self.api = self._make_client(config)
        self._ifrows = []
        self._ifrows_time = 0
        self._dhcp_backend = None
        self._leases_cache = ([], 0)
        self._gw_status = []
        self._cpu = None
        self._cpu_time = 0
        self._boot_time = None
        self._hostname = None
        self._hostname_time = 0
        tools.set_host_provider(uptime_fn=self._uptime, hostname_fn=self._get_hostname)
        super().__init__(config)
        self._fill_lan_identity()

    def _fill_lan_identity(self):
        """Default lan_ip/lan_mac (the controller's identity for this gateway) to OPNsense's first LAN port."""
        lan = next((p for p in self.ports if p['type'].lower().startswith('lan')), None)
        if lan is None:
            return
        current_mac = self.config.get('gateway', 'lan_mac', fallback='').strip().lower()
        mac = self.data.get('macs', {}).get(lan['ifname'])
        if current_mac in ('', '00:00:00:00:00:00') and mac and mac != '00:00:00:00:00:00':
            self.config.set('gateway', 'lan_mac', mac)
            logger.info('lan_mac not set, using the OPNsense %s MAC %s', lan['realif'], mac)
        ip = self.data.get('ip', {}).get(lan['ifname'], {}).get('address')
        if not self.config.get('gateway', 'lan_ip', fallback='').strip() and ip:
            self.config.set('gateway', 'lan_ip', ip)
            logger.info('lan_ip not set, using the OPNsense %s address %s', lan['realif'], ip)
        if self.config.get('gateway', 'lan_mac', fallback='').strip().lower() in ('', '00:00:00:00:00:00'):
            logger.error('Could not determine the LAN MAC from the OPNsense API; set lan_mac / UNIFI_GW_LAN_MAC')

    # ── Config / client ──────────────────────────────────────────────────

    @staticmethod
    def _opt(config, key, env=None, default=None):
        if env and os.environ.get(env):
            return os.environ[env]
        if config.has_section(SECTION) and config.has_option(SECTION, key):
            return config.get(SECTION, key)
        return default

    def opt(self, key, env=None, default=None):
        return self._opt(self.config, key, env, default)

    @classmethod
    def _make_client(cls, config):
        secret = cls._opt(config, 'api_secret', 'OPNSENSE_API_SECRET')
        secret_file = cls._opt(config, 'api_secret_file', 'OPNSENSE_API_SECRET_FILE')
        if not secret and secret_file:
            with open(secret_file) as f:
                secret = f.read().strip()
        verify_raw = str(cls._opt(config, 'verify_ssl', 'OPNSENSE_VERIFY_SSL', 'true')).strip()
        if verify_raw.lower() in ('false', 'no', '0', 'off'):
            verify = False
        elif verify_raw.lower() in ('true', 'yes', '1', 'on', ''):
            verify = True
        else:
            verify = verify_raw  # path to a CA bundle / the OPNsense self-signed cert
        timeout = float(cls._opt(config, 'timeout', None, '10'))
        return OPNsenseApiClient(
            cls._opt(config, 'api_url', 'OPNSENSE_API_URL'),
            cls._opt(config, 'api_key', 'OPNSENSE_API_KEY'),
            secret, verify=verify, timeout=timeout,
        )

    def _safe_get(self, path, params=None, quiet=False, probe=False):
        try:
            return self.api.get(path, params)
        except OPNsenseApiError as e:
            if probe and e.status in (403, 404):
                # auto-detecting: an endpoint we're not allowed to (or can't) use just isn't the one
                logger.debug('OPNsense API: %s (skipped while auto-detecting)', e)
            elif e.status == 401:
                logger.error('OPNsense API: %s - check api_key/api_secret', e)
            elif e.status == 403:
                logger.error('OPNsense API: %s - the API user lacks the privilege for this page '
                             '(see README "Deploy with Docker against OPNsense", step 1)', e)
            else:
                (logger.debug if quiet else logger.warning)('OPNsense API: %s', e)
            return None

    # ── Interface rows ───────────────────────────────────────────────────

    def _interfaces(self, force=False):
        if force or time.time() - self._ifrows_time > self.IFINFO_TTL:
            resp = self._safe_get('/api/interfaces/overview/interfaces_info/1')
            if resp is not None:
                self._ifrows = _rows(resp)
                self._ifrows_time = time.time()
        return self._ifrows

    def _row_for(self, realif):
        """Match a port's realif against the OPNsense device (igc0) or identifier (wan/lan/opt1)."""
        realif = str(realif).lower()
        for row in self._interfaces():
            if str(row.get('device', '')).lower() == realif:
                return row
        for row in self._interfaces():
            if str(row.get('identifier', '')).lower() == realif:
                return row
        return None

    def _row_for_port(self, port):
        row = self._row_for(port['realif'])
        if row is None:
            logger.debug('Interface %s not found via OPNsense API', port['realif'])
        return row

    def update(self):
        self._interfaces(force=True)
        self._refresh_uptime()
        self._gw_status = (self._safe_get('/api/routes/gateway/status') or {}).get('items', []) or []
        super().update()

    # ── Interface traffic stats ──────────────────────────────────────────

    def _get_ifstat(self):
        ret = {}
        now = time.time()
        for port in self.ports:
            ifname = port['ifname']
            row = self._row_for_port(port)
            if row is None:
                continue
            st = row.get('statistics') or {}
            if not st:
                continue
            c = {
                'rx_bytes': _to_int(st.get('bytes received')),
                'tx_bytes': _to_int(st.get('bytes transmitted')),
                'rx_packets': _to_int(st.get('packets received')),
                'tx_packets': _to_int(st.get('packets transmitted')),
                'rx_errors': _to_int(st.get('input errors')),
                'tx_errors': _to_int(st.get('output errors')),
                'rx_dropped': _to_int(st.get('input queue drops')),
                'tx_dropped': _to_int(st.get('send queue drops')),
                'rx_multicast': _to_int(st.get('multicasts received')),
                'tx_multicast': _to_int(st.get('multicasts transmitted')),
            }
            info = {k: str(v) for k, v in c.items()}
            for k in ('rx_fifo', 'rx_frame', 'rx_compressed', 'tx_fifo', 'tx_frame', 'tx_compressed'):
                info[k] = '0'

            info['rx_bps'] = info['tx_bps'] = 0
            prev = self._prev_io.get(ifname)
            if prev and self._prev_time and now > self._prev_time:
                elapsed = now - self._prev_time
                # counters reset (reboot, interface re-created) -> report 0 instead of a negative rate
                info['rx_bps'] = max(0, int((c['rx_bytes'] - prev['rx_bytes']) / elapsed))
                info['tx_bps'] = max(0, int((c['tx_bytes'] - prev['tx_bytes']) / elapsed))
            self._prev_io[ifname] = c
            ret[ifname] = info
        self._prev_time = now
        return ret

    def _get_link_speeds(self):
        ret = {}
        for port in self.ports:
            row = self._row_for(port['realif'])
            if row is None:
                continue
            speed = parse_media_speed(row.get('media'))
            if not speed:
                rate = (row.get('statistics') or {}).get('line rate', '')
                m = re.match(r'\s*(\d+)\s*Mbit', str(rate))
                speed = int(m.group(1)) if m else 0
            if speed > 0:
                ret[port['ifname']] = speed
        return ret

    # ── MAC / IP ─────────────────────────────────────────────────────────

    def _get_interface_macs(self):
        ret = {}
        for port in self.ports:
            ifname = port['ifname']
            configured = port.get('mac')
            if configured:
                mac = str(configured).replace('-', ':').lower()
                if not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', mac):
                    raise ValueError(f'invalid MAC {configured!r} configured for port {ifname}')
                ret[ifname] = mac
                continue
            row = self._row_for_port(port)
            mac = str((row or {}).get('macaddr') or '').split(' ')[0].lower()
            if re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', mac):
                ret[ifname] = mac
            else:
                logger.warning('Could not determine MAC for %s (%s) via OPNsense API', ifname, port['realif'])
                ret[ifname] = '00:00:00:00:00:00'
        return ret

    def _get_interface_addresses(self):
        ret = {}
        for port in self.ports:
            row = self._row_for_port(port)
            if row is None:
                continue
            cidr = None
            for entry in row.get('ipv4') or []:
                if entry.get('ipaddr') and not entry.get('vhid'):
                    cidr = entry['ipaddr']
                    break
            cidr = cidr or row.get('addr4')
            if not cidr:
                continue
            try:
                iface = ipaddress.ip_interface(cidr if '/' in cidr else cidr + '/32')
            except ValueError:
                continue
            entry = {'address': str(iface.ip), 'netmask': str(iface.netmask)}
            if port['type'].lower() == 'wan':
                gw = _first_ipv4(row.get('gateways'))
                if gw:
                    entry['gateway'] = gw
            ret[port['ifname']] = entry
        return ret

    def _wan_row(self):
        for port in self.ports:
            if port['type'].lower() == 'wan':
                return self._row_for(port['realif'])
        return None

    def _get_default_gateway(self):
        return _first_ipv4((self._wan_row() or {}).get('gateways'))

    # ── DHCP leases ──────────────────────────────────────────────────────

    def _get_dhcp_leases(self):
        leases, fetched = self._leases_cache
        if time.time() - fetched < self.LEASES_TTL:
            return leases

        backend = str(self.opt('dhcp_backend', 'OPNSENSE_DHCP_BACKEND', 'auto')).lower()
        if backend == 'none':
            return []
        order = [backend] if backend != 'auto' else (
            [self._dhcp_backend] if self._dhcp_backend else ['kea', 'dnsmasq', 'isc'])

        for name in order:
            fetch = {'kea': self._leases_kea, 'dnsmasq': self._leases_dnsmasq, 'isc': self._leases_isc}.get(name)
            if fetch is None:
                logger.error('Unknown [opnsense] dhcp_backend %r', name)
                return []
            result = fetch(probe=(backend == 'auto' and self._dhcp_backend is None))
            if result is None:
                continue
            if backend == 'auto' and not result and self._dhcp_backend is None:
                continue  # endpoint exists but service unused: try the next one
            if self._dhcp_backend != name and result:
                logger.info('Using OPNsense DHCP backend: %s', name)
            if result:
                self._dhcp_backend = name
            self._leases_cache = (result, time.time())
            return result
        self._leases_cache = ([], time.time())
        return []

    def _leases_kea(self, probe=False):
        resp = self._safe_get('/api/kea/leases4/search', quiet=True, probe=probe)
        if resp is None:
            return None
        out = []
        for r in _rows(resp):
            if str(r.get('state', 0)) != '0' or not r.get('hwaddr') or not r.get('address'):
                continue
            out.append(self._lease(r.get('address'), r.get('hwaddr'), r.get('expire'), r.get('hostname')))
        return out

    def _leases_dnsmasq(self, probe=False):
        resp = self._safe_get('/api/dnsmasq/leases/search', quiet=True, probe=probe)
        if resp is None:
            return None
        out = []
        for r in _rows(resp):
            addr = str(r.get('address') or '')
            if ':' in addr or not r.get('hwaddr'):
                continue  # IPv6 lease
            out.append(self._lease(addr, r.get('hwaddr'), r.get('expire'), r.get('hostname')))
        return out

    def _leases_isc(self, probe=False):
        resp = self._safe_get('/api/dhcpv4/leases/search_lease', quiet=True, probe=probe)
        if resp is None:
            return None
        out = []
        for r in _rows(resp):
            if r.get('state') not in (None, 'active') or not r.get('mac'):
                continue
            out.append(self._lease(r.get('address'), r.get('mac'), r.get('ends'), r.get('hostname')))
        return out

    @staticmethod
    def _lease(ip, mac, expiry, hostname):
        lease = {'ip': str(ip), 'mac': str(mac).lower(), 'expiry': str(expiry or '')}
        if hostname and hostname != '*':
            lease['hostname'] = str(hostname).rstrip('.')
        return lease

    # ── Neighbors ────────────────────────────────────────────────────────

    def _get_neighbors_raw(self):
        lan_devices = set()
        for port in self.ports:
            if port['type'].lower().startswith('lan'):
                row = self._row_for(port['realif'])
                lan_devices.add(str((row or {}).get('device') or port['realif']))
        arp = self._safe_get('/api/diagnostics/interface/get_arp')
        if not isinstance(arp, list):
            return []
        names = {d['mac'].lower(): d['hostname'] for d in self.data.get('dhcp_leases', []) if 'hostname' in d}
        out = []
        for e in arp:
            mac, ip = str(e.get('mac', '')).lower(), e.get('ip')
            if not mac or not ip or e.get('intf') not in lan_devices or e.get('permanent'):
                continue  # permanent = the router's own address
            neigh = {'mac': mac, 'ip': ip}
            hostname = names.get(mac) or e.get('hostname')
            if hostname:
                neigh['hostname'] = hostname
            out.append(neigh)
        return out

    # ── DNS / latency ────────────────────────────────────────────────────

    def _get_nameservers(self):
        configured = self.opt('nameservers', 'OPNSENSE_NAMESERVERS')
        if configured:
            return [s.strip() for s in configured.split(',') if s.strip()]
        dyn = (self._wan_row() or {}).get('ifctl.nameserver')
        if isinstance(dyn, str):
            dyn = dyn.split()
        servers = [s for s in (dyn or []) if _first_ipv4([s])]
        if servers:
            return servers
        # static WAN without DHCP-provided DNS: OPNsense itself (Unbound) is what clients use
        lan_ip = next((v['address'] for k, v in self.data.get('ip', {}).items()
                       if any(p['ifname'] == k and p['type'].lower() == 'lan' for p in self.ports)), None)
        return [lan_ip] if lan_ip else super()._get_nameservers()

    def _wan_gateway_status(self):
        wanted = self.opt('wan_gateway', 'OPNSENSE_WAN_GATEWAY')
        wan_gw = self._get_default_gateway()
        for item in self._gw_status:
            if wanted and item.get('name') == wanted:
                return item
        if not wanted:
            for item in self._gw_status:
                if wan_gw and item.get('address') == wan_gw:
                    return item
            for item in self._gw_status:
                if _first_ipv4([item.get('address')]) and parse_ms(item.get('delay')) is not None:
                    return item
        return None

    def _get_latency(self):
        if str(self.opt('latency_source', None, 'gateway')).lower() == 'gateway':
            item = self._wan_gateway_status()
            if item:
                ms = parse_ms(item.get('delay'))
                if ms is not None:
                    return ms
            logger.debug('No dpinger latency for the WAN gateway, falling back to ping')
        return super()._get_latency()

    # ── System ───────────────────────────────────────────────────────────

    def _refresh_uptime(self):
        resp = self._safe_get('/api/diagnostics/system/system_time', quiet=True) or {}
        secs = parse_uptime(resp.get('uptime'))
        if secs is not None:
            self._boot_time = time.time() - secs

    def _uptime(self):
        if self._boot_time is None:
            self._refresh_uptime()
        return int(time.time() - self._boot_time) if self._boot_time else 0

    def _get_boot_time(self):
        return self._boot_time or time.time()

    def _get_hostname(self):
        if self._hostname is None or time.time() - self._hostname_time > self.SYSINFO_TTL:
            resp = self._safe_get('/api/diagnostics/system/system_information', quiet=True) or {}
            name = str(resp.get('name') or '').split('.')[0]
            if name:
                self._hostname = name
                self._hostname_time = time.time()
        return self._hostname or 'opnsense'

    def _get_system_stats(self):
        stats = {'cpu': '0', 'mem': '0'}
        mem = (self._safe_get('/api/diagnostics/system/system_resources', quiet=True) or {}).get('memory', {})
        try:
            stats['mem'] = str(int(round(100.0 * float(mem['used']) / float(mem['total']))))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass

        interval = int(self.opt('cpu_interval', None, '60'))
        if interval > 0 and time.time() - self._cpu_time >= interval:
            # top runs ~2s on the router, so this is polled less often than the inform
            act = self._safe_get('/api/diagnostics/activity/get_activity', quiet=True) or {}
            cpu = parse_cpu_from_top(act.get('headers'))
            if cpu is not None:
                self._cpu = cpu
            self._cpu_time = time.time()
        if self._cpu is not None:
            stats['cpu'] = str(self._cpu)
        return stats
