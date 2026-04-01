# -*- coding: utf-8 -*-
"""OpenWRT-specific data collection.

Extends Linux collector with ubus/LuCI RPC integration for:
- DHCP leases (ubus call luci-rpc getDHCPLeases)
- Host hints (ubus call luci-rpc getHostHints)
- Per-device traffic via conntrack (ubus call luci-rpc getConntrackList)
- Interface details (ubus call network.interface dump)
"""
import logging

from .linux import LinuxCollector

logger = logging.getLogger('unifi-gateway')


class OpenWRTCollector(LinuxCollector):
    # TODO: ubus session authentication
    # TODO: _get_dhcp_leases() via ubus getDHCPLeases
    # TODO: _get_neighbors_raw() enriched via getHostHints
    # TODO: Per-device traffic via getConntrackList
    pass
