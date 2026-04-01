# -*- coding: utf-8 -*-
"""FreeBSD-specific data collection via netstat and arp."""
import logging
import re
import subprocess

from .base import BaseCollector

logger = logging.getLogger('unifi-gateway')


class FreeBSDCollector(BaseCollector):

    def _get_default_gateway(self):
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
        return super()._get_default_gateway()

    def _get_neighbors_raw(self):
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
