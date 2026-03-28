# -*- coding: utf-8 -*-
import logging
import os
import platform
import csv
import re
import ast
import time
import json
import subprocess
import socket

import psutil

logger = logging.getLogger('unifi-gateway')


class DataCollector:
    """Collects system and network stats for the UniFi inform payload.

    Uses psutil for cross-platform interface stats, addresses, and system metrics.
    Platform-specific code handles DHCP leases, neighbor tables, and routing.
    """

    def __init__(self, config):
        self.data = {}
        self.updated = {}
        self.config = config
        self.ports = ast.literal_eval(config.get('gateway', 'ports'))
        self._platform = platform.system().lower()
        self._prev_io = {}
        self._prev_time = None

        self.update_oneshot()

    def update_oneshot(self):
        self.data['macs'] = self._get_interface_macs()
        self.update()

    def update(self):
        self.data['ifstat'] = self._get_ifstat()
        self.data['dhcp_leases'] = self._get_dhcp_leases()
        self.data['ip'] = self._get_interface_addresses()
        self.data['nameservers'] = self._get_nameservers()
        self.data['speedtest'] = self._get_speedtest_results()
        self.data['system_stats'] = self._get_system_stats()
        self.updated['general'] = time.time()

        if 'latency' not in self.updated or (time.time() - self.updated['latency'] >= 300):
            self.data['latency'] = self._get_latency()
            self.updated['latency'] = time.time()

        if 'host_table' not in self.updated or (time.time() - self.updated['host_table'] >= 120):
            self.data['host_table'] = self._get_neighbors()
            self.updated['host_table'] = time.time()

    # ── Interface traffic stats ──────────────────────────────────────────

    def _get_ifstat(self):
        ret = {}
        now = time.time()
        try:
            counters = psutil.net_io_counters(pernic=True)
        except Exception as e:
            logger.warning('Failed to get network counters: %s', e)
            return ret

        for port in self.ports:
            realif = port['realif']
            ifname = port['ifname']
            if realif not in counters:
                logger.debug('Interface %s not found in system counters', realif)
                continue

            c = counters[realif]
            info = {
                'rx_bytes': str(c.bytes_recv),
                'rx_packets': str(c.packets_recv),
                'rx_errors': str(c.errin),
                'rx_dropped': str(c.dropin),
                'rx_fifo': '0',
                'rx_frame': '0',
                'rx_compressed': '0',
                'rx_multicast': '0',
                'tx_bytes': str(c.bytes_sent),
                'tx_packets': str(c.packets_sent),
                'tx_errors': str(c.errout),
                'tx_dropped': str(c.dropout),
                'tx_fifo': '0',
                'tx_frame': '0',
                'tx_compressed': '0',
                'tx_multicast': '0',
            }

            if ifname in self._prev_io and self._prev_time:
                elapsed = now - self._prev_time
                if elapsed > 0:
                    prev = self._prev_io[ifname]
                    info['rx_bps'] = int((c.bytes_recv - prev.bytes_recv) / elapsed)
                    info['tx_bps'] = int((c.bytes_sent - prev.bytes_sent) / elapsed)
                else:
                    info['rx_bps'] = 0
                    info['tx_bps'] = 0
            else:
                info['rx_bps'] = 0
                info['tx_bps'] = 0

            self._prev_io[ifname] = c
            ret[ifname] = info

        if self._platform == 'linux':
            self._supplement_multicast(ret)

        self._prev_time = now
        return ret

    def _supplement_multicast(self, ifstat):
        """Read multicast counters from /proc/net/dev on Linux."""
        try:
            with open('/proc/net/dev', 'r') as f:
                lines = f.readlines()
            for line in lines[2:]:
                if ':' not in line:
                    continue
                iface, data = line.split(':', 1)
                iface = iface.strip()
                matches = [d['ifname'] for d in self.ports if d['realif'] == iface]
                if not matches or matches[0] not in ifstat:
                    continue
                cols = data.split()
                if len(cols) >= 8:
                    ifstat[matches[0]]['rx_multicast'] = cols[7]
        except (IOError, OSError):
            pass

    # ── MAC addresses ────────────────────────────────────────────────────

    def _get_interface_macs(self):
        ret = {}
        try:
            addrs = psutil.net_if_addrs()
        except Exception as e:
            logger.warning('Failed to get interface addresses for MACs: %s', e)
            addrs = {}

        for port in self.ports:
            realif = port['realif']
            ifname = port['ifname']
            mac = None

            if realif in addrs:
                for addr in addrs[realif]:
                    if addr.family == psutil.AF_LINK:
                        mac = addr.address
                        break

            if not mac and self._platform == 'linux':
                try:
                    with open('/sys/class/net/%s/address' % realif, 'r') as f:
                        mac = f.read().strip()
                except (IOError, OSError):
                    pass

            if mac:
                # Normalize MAC format to colon-separated lowercase
                mac = mac.replace('-', ':').lower()
                ret[ifname] = mac
            else:
                logger.warning('Could not determine MAC for %s (%s)', ifname, realif)
                ret[ifname] = '00:00:00:00:00:00'
        return ret

    # ── IP addresses and netmasks ────────────────────────────────────────

    def _get_interface_addresses(self):
        ret = {}
        try:
            addrs = psutil.net_if_addrs()
        except Exception as e:
            logger.warning('Failed to get interface addresses: %s', e)
            return ret

        for port in self.ports:
            realif = port['realif']
            ifname = port['ifname']
            if realif not in addrs:
                continue

            ip_addr = None
            netmask = None
            for addr in addrs[realif]:
                if addr.family == socket.AF_INET:
                    ip_addr = addr.address
                    netmask = addr.netmask
                    break

            if not ip_addr or not netmask:
                continue

            entry = {'address': ip_addr, 'netmask': netmask}
            if port['type'].lower() == 'wan':
                gw = self._get_default_gateway()
                if gw:
                    entry['gateway'] = gw
            ret[ifname] = entry

        return ret

    # ── Default gateway ──────────────────────────────────────────────────

    def _get_default_gateway(self):
        if self._platform == 'linux':
            return self._get_default_gateway_linux()
        elif self._platform in ('freebsd', 'openbsd', 'netbsd', 'darwin'):
            return self._get_default_gateway_bsd()
        return self._get_default_gateway_fallback()

    def _get_default_gateway_linux(self):
        try:
            with open('/proc/net/route') as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) < 4:
                        continue
                    if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                        continue
                    import struct as _struct
                    return socket.inet_ntoa(_struct.pack('<L', int(fields[2], 16)))
        except (IOError, OSError, IndexError, ValueError) as e:
            logger.debug('Failed to read default gateway from /proc/net/route: %s', e)
        return None

    def _get_default_gateway_bsd(self):
        try:
            output = subprocess.check_output(
                ['netstat', '-rn', '-f', 'inet'], stderr=subprocess.DEVNULL, timeout=5
            ).decode('utf-8')
            for line in output.splitlines():
                fields = line.split()
                if len(fields) >= 2 and fields[0] == 'default':
                    return fields[1]
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.debug('Failed to get default gateway via netstat: %s', e)
        return None

    def _get_default_gateway_fallback(self):
        for cmd in [['ip', 'route', 'show', 'default'], ['route', '-n', 'get', 'default']]:
            try:
                output = subprocess.check_output(
                    cmd, stderr=subprocess.DEVNULL, timeout=5
                ).decode('utf-8')
                match = re.search(r'(?:via|gateway:?\s+)([\d.]+)', output)
                if match:
                    return match.group(1)
            except (subprocess.SubprocessError, OSError, FileNotFoundError):
                continue
        return None

    # ── DHCP leases ──────────────────────────────────────────────────────

    def _get_dhcp_leases(self):
        lease_file = None
        lease_format = 'dnsmasq'

        if self.config.has_option('gateway', 'dhcp_lease_file'):
            lease_file = self.config.get('gateway', 'dhcp_lease_file')
        if self.config.has_option('gateway', 'dhcp_lease_format'):
            lease_format = self.config.get('gateway', 'dhcp_lease_format')

        if not lease_file:
            candidates = [
                ('/tmp/dhcp.leases', 'dnsmasq'),
                ('/var/lib/misc/dnsmasq.leases', 'dnsmasq'),
                ('/var/db/kea/kea-leases4.csv', 'kea'),
                ('/var/lib/kea/kea-leases4.csv', 'kea'),
                ('/var/db/dhcpd.leases', 'isc'),
                ('/var/dhcpd/var/db/dhcpd.leases', 'isc'),
                ('/var/lib/dhcp/dhcpd.leases', 'isc'),
            ]
            for path, fmt in candidates:
                if os.path.exists(path):
                    lease_file = path
                    lease_format = fmt
                    break

        if not lease_file or not os.path.exists(lease_file):
            return []

        try:
            if lease_format == 'isc':
                return self._parse_isc_leases(lease_file)
            elif lease_format == 'kea':
                return self._parse_kea_leases(lease_file)
            return self._parse_dnsmasq_leases(lease_file)
        except Exception as e:
            logger.warning('Failed to read DHCP leases from %s: %s', lease_file, e)
            return []

    def _parse_dnsmasq_leases(self, path):
        leases = []
        with open(path, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 4:
                    continue
                lease = {'expiry': parts[0], 'mac': parts[1], 'ip': parts[2]}
                if parts[3] != '*':
                    lease['hostname'] = parts[3]
                leases.append(lease)
        return leases

    def _parse_isc_leases(self, path):
        leases = []
        current = None
        with open(path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('lease '):
                    current = {'ip': line.split()[1]}
                elif current and line.startswith('hardware ethernet '):
                    current['mac'] = line.split()[2].rstrip(';')
                elif current and line.startswith('client-hostname '):
                    hostname = line.split('"')[1] if '"' in line else line.split()[1].rstrip(';')
                    current['hostname'] = hostname
                elif current and line.startswith('ends '):
                    parts = line.split()
                    if len(parts) >= 3:
                        current['expiry'] = parts[2].rstrip(';')
                elif current and line == '}':
                    if 'mac' in current:
                        leases.append(current)
                    current = None
        return leases

    def _parse_kea_leases(self, path):
        """Parse KEA DHCP4 CSV lease file.

        CSV columns: address,hwaddr,client_id,valid_lifetime,expire,subnet_id,
                     fqdn_fwd,fqdn_rev,hostname,state[,user_context,pool_id]
        State 0 = active, 1 = declined, 2 = expired-reclaimed.
        """
        leases = {}
        with open(path, 'r', newline='') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or row[0].startswith('#') or row[0] == 'address':
                    continue
                if len(row) < 10:
                    continue
                state = row[9].strip()
                if state != '0':
                    continue
                ip = row[0].strip()
                mac = row[1].strip()
                expiry = row[4].strip()
                hostname = row[8].strip()
                lease = {'ip': ip, 'mac': mac, 'expiry': expiry}
                if hostname:
                    lease['hostname'] = hostname
                leases[ip] = lease
        return list(leases.values())

    # ── Neighbor / ARP table ─────────────────────────────────────────────

    def _get_neighbors(self):
        if self._platform == 'linux':
            arp_hosts = self._get_neighbors_linux()
        else:
            arp_hosts = self._get_neighbors_bsd()
        return self._merge_dhcp_into_hosts(arp_hosts)

    def _merge_dhcp_into_hosts(self, arp_hosts):
        """Merge DHCP leases into host table so all clients appear,
        and enrich entries with fields the controller needs for topology."""
        boot = psutil.boot_time()
        now = time.time()
        hosts_by_mac = {}

        for entry in arp_hosts:
            enriched = {
                'mac': entry['mac'],
                'ip': entry['ip'],
                'authorized': True,
                'age': 0,
                'uptime': int(now - boot),
                'rx_bytes': 0,
                'tx_bytes': 0,
                'rx_packets': 0,
                'tx_packets': 0,
                'bc_bytes': 0,
                'mc_bytes': 0,
            }
            if 'hostname' in entry:
                enriched['hostname'] = entry['hostname']
            hosts_by_mac[entry['mac'].lower()] = enriched

        for lease in self.data.get('dhcp_leases', []):
            mac = lease['mac'].lower()
            if mac not in hosts_by_mac:
                enriched = {
                    'mac': lease['mac'],
                    'ip': lease['ip'],
                    'authorized': True,
                    'age': 0,
                    'uptime': 0,
                    'rx_bytes': 0,
                    'tx_bytes': 0,
                    'rx_packets': 0,
                    'tx_packets': 0,
                    'bc_bytes': 0,
                    'mc_bytes': 0,
                }
                if 'hostname' in lease:
                    enriched['hostname'] = lease['hostname']
                hosts_by_mac[mac] = enriched
            elif 'hostname' not in hosts_by_mac[mac] and 'hostname' in lease:
                hosts_by_mac[mac]['hostname'] = lease['hostname']

        return list(hosts_by_mac.values())

    def _get_neighbors_linux(self):
        neigh_table = []
        lan_ifs = [d['realif'] for d in self.ports if 'lan' in d['name'].lower()]
        try:
            result = subprocess.run(
                ['ip', '-4', '-s', 'neigh', 'list'],
                capture_output=True, timeout=10
            )
            output = result.stdout.decode('utf-8')
        except (subprocess.SubprocessError, OSError) as e:
            logger.warning('Failed to get neighbor table: %s', e)
            return neigh_table

        for line in output.splitlines():
            fields = line.split()
            if not fields:
                continue
            state = fields[-1]
            if state not in ('REACHABLE', 'STALE'):
                continue

            try:
                dev_i = fields.index('dev')
                lladdr_i = fields.index('lladdr')
                stats_i = fields.index('used')
            except ValueError:
                continue

            dev = fields[dev_i + 1]
            if dev not in lan_ifs:
                continue

            mac = fields[lladdr_i + 1]
            try:
                used = int(fields[stats_i + 1].split('/')[0])
            except (ValueError, IndexError):
                used = 0

            if state == 'STALE' and used > 240:
                continue

            neigh = {'mac': mac, 'ip': fields[0]}
            dhcp_hostname = [
                d['hostname'] for d in self.data.get('dhcp_leases', [])
                if 'hostname' in d and d['mac'].lower() == mac.lower()
            ]
            if dhcp_hostname:
                neigh['hostname'] = dhcp_hostname[0]
            neigh_table.append(neigh)

        return neigh_table

    def _get_neighbors_bsd(self):
        neigh_table = []
        lan_ifs = [d['realif'] for d in self.ports if 'lan' in d['name'].lower()]
        try:
            result = subprocess.run(
                ['arp', '-an'], capture_output=True, timeout=10
            )
            output = result.stdout.decode('utf-8')
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.warning('Failed to get ARP table: %s', e)
            return neigh_table

        for line in output.splitlines():
            match = re.match(
                r'\?\s+\(([\d.]+)\)\s+at\s+([\da-f:]+)\s+on\s+(\S+)', line, re.I
            )
            if not match:
                continue
            ip_addr, mac, iface = match.groups()
            if mac == '(incomplete)' or iface not in lan_ifs:
                continue

            neigh = {'mac': mac, 'ip': ip_addr}
            dhcp_hostname = [
                d['hostname'] for d in self.data.get('dhcp_leases', [])
                if 'hostname' in d and d['mac'].lower() == mac.lower()
            ]
            if dhcp_hostname:
                neigh['hostname'] = dhcp_hostname[0]
            neigh_table.append(neigh)

        return neigh_table

    # ── DNS nameservers ──────────────────────────────────────────────────

    def _get_nameservers(self):
        nameservers = []
        for path in ('/etc/resolv.conf', '/tmp/resolv.conf.auto'):
            try:
                with open(path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('nameserver '):
                            ns = line.split()[1]
                            if ns not in nameservers:
                                nameservers.append(ns)
            except (IOError, OSError):
                continue
        if not nameservers:
            logger.warning('No nameservers found in resolv.conf, using fallback')
            nameservers = ['8.8.8.8', '8.8.4.4']
        return nameservers

    # ── Latency ──────────────────────────────────────────────────────────

    def _get_latency(self):
        target = 'ping.ubnt.com'
        if self.config.has_option('gateway', 'ping_target'):
            target = self.config.get('gateway', 'ping_target')

        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '5', target],
                capture_output=True, timeout=10
            )
            output = result.stdout.decode('utf-8')
            match = re.search(r'([\d.]+)/([\d.]+)/([\d.]+)', output)
            if match:
                return float(match.group(2))
        except (subprocess.SubprocessError, OSError, FileNotFoundError) as e:
            logger.debug('Ping failed: %s', e)
        return 0

    # ── Speedtest ────────────────────────────────────────────────────────

    def _get_speedtest_results(self):
        noresult = {'lastrun': 0, 'ping': 0, 'upload': 0, 'download': 0}
        results_file = './speedtest.json'
        if self.config.has_option('gateway', 'speedtest_file'):
            results_file = self.config.get('gateway', 'speedtest_file')

        try:
            ts = os.path.getmtime(results_file)
            with open(results_file) as f:
                sp = json.load(f)
            return {
                'lastrun': int(ts),
                'ping': sp.get('ping', 0),
                'upload': sp.get('upload', 0) / 1024 / 1024,
                'download': sp.get('download', 0) / 1024 / 1024,
            }
        except (IOError, OSError, json.JSONDecodeError, KeyError, TypeError):
            return noresult

    # ── System stats ─────────────────────────────────────────────────────

    def _get_system_stats(self):
        try:
            return {
                'cpu': str(int(psutil.cpu_percent(interval=None))),
                'mem': str(int(psutil.virtual_memory().percent)),
            }
        except Exception as e:
            logger.warning('Failed to get system stats: %s', e)
            return {'cpu': '0', 'mem': '0'}
