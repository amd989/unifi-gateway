# -*- coding: utf-8 -*-
"""Linux-specific data collection via /proc and ip commands."""
import logging
import socket
import struct
import subprocess

from .base import BaseCollector

logger = logging.getLogger('unifi-gateway')


class LinuxCollector(BaseCollector):

    def _get_ifstat(self):
        ret = super()._get_ifstat()
        self._supplement_multicast(ret)
        return ret

    def _supplement_multicast(self, ifstat):
        """Read multicast counters from /proc/net/dev."""
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

    def _get_interface_macs(self):
        ret = super()._get_interface_macs()
        for port in self.ports:
            ifname = port['ifname']
            if ret.get(ifname) == '00:00:00:00:00:00':
                try:
                    with open('/sys/class/net/%s/address' % port['realif'], 'r') as f:
                        mac = f.read().strip().lower()
                    if mac and mac != '00:00:00:00:00:00':
                        ret[ifname] = mac
                except (IOError, OSError):
                    pass
        return ret

    def _get_default_gateway(self):
        try:
            with open('/proc/net/route') as f:
                for line in f:
                    fields = line.strip().split()
                    if len(fields) < 4:
                        continue
                    if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                        continue
                    return socket.inet_ntoa(struct.pack('<L', int(fields[2], 16)))
        except (IOError, OSError, IndexError, ValueError) as e:
            logger.debug('Failed to read default gateway from /proc/net/route: %s', e)
        return super()._get_default_gateway()

    def _get_neighbors_raw(self):
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
