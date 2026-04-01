# -*- coding: utf-8 -*-
"""Backward-compatibility shim — use collectors.create_collector() for new code."""
from collectors import create_collector


class DataCollector:
    """Proxy that delegates to the platform-appropriate collector.

    New code should use create_collector(config) directly.
    """

    def __new__(cls, config):
        return create_collector(config)
