# -*- coding: utf-8 -*-
"""OPNSense-specific data collection.

Extends FreeBSD collector with OPNSense REST API integration for:
- Richer ARP data with manufacturer info (GET /api/diagnostics/interface/get_arp)
- Per-device traffic via Netflow (GET /api/diagnostics/networkinsight/top/...)
- Write-back: DHCP reservations, port forwarding, DNS overrides
"""
import logging

from .freebsd import FreeBSDCollector

logger = logging.getLogger('unifi-gateway')


class OPNSenseCollector(FreeBSDCollector):
    # TODO: OPNSense API key/secret from config
    # TODO: _get_neighbors_raw() via /api/diagnostics/interface/get_arp
    # TODO: Per-device traffic via Netflow API
    # TODO: Write-back methods for DHCP, port forwarding, DNS
    pass
