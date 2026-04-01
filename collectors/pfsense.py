# -*- coding: utf-8 -*-
"""pfSense-specific data collection.

Extends FreeBSD collector with pfSense API integration.
"""
import logging

from .freebsd import FreeBSDCollector

logger = logging.getLogger('unifi-gateway')


class PfSenseCollector(FreeBSDCollector):
    # TODO: pfSense API integration
    pass
