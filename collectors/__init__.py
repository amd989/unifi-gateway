# -*- coding: utf-8 -*-
"""Platform-aware data collector hierarchy.

Usage:
    from collectors import create_collector
    dc = create_collector(config)
"""
import logging
import os
import platform

logger = logging.getLogger('unifi-gateway')


def create_collector(config):
    """Create the appropriate collector for the configured/detected platform."""
    platform_name = 'auto'
    if config.has_option('gateway', 'platform'):
        platform_name = config.get('gateway', 'platform').lower().strip()

    if platform_name == 'auto':
        platform_name = _detect_platform()

    logger.info('Using platform collector: %s', platform_name)

    if platform_name == 'opnsense':
        from .opnsense import OPNSenseCollector
        return OPNSenseCollector(config)
    if platform_name == 'openwrt':
        from .openwrt import OpenWRTCollector
        return OpenWRTCollector(config)
    if platform_name == 'pfsense':
        from .pfsense import PfSenseCollector
        return PfSenseCollector(config)
    if platform_name == 'linux':
        from .linux import LinuxCollector
        return LinuxCollector(config)
    if platform_name in ('freebsd', 'openbsd', 'netbsd', 'darwin'):
        from .freebsd import FreeBSDCollector
        return FreeBSDCollector(config)

    logger.warning('Unknown platform %r, falling back to base collector', platform_name)
    from .base import BaseCollector
    return BaseCollector(config)


def _detect_platform():
    system = platform.system().lower()
    if system == 'linux':
        if os.path.exists('/etc/openwrt_release'):
            return 'openwrt'
        return 'linux'
    if system == 'freebsd':
        if os.path.exists('/usr/local/opnsense'):
            return 'opnsense'
        if os.path.exists('/etc/platform'):
            return 'pfsense'
        return 'freebsd'
    if system in ('openbsd', 'netbsd', 'darwin'):
        return 'freebsd'
    return system
