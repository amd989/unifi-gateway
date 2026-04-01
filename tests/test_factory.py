# -*- coding: utf-8 -*-
"""Tests for collector factory and platform auto-detection."""
from unittest.mock import patch

from collectors import create_collector, _detect_platform
from collectors.base import BaseCollector
from collectors.linux import LinuxCollector
from collectors.freebsd import FreeBSDCollector
from collectors.opnsense import OPNSenseCollector
from collectors.openwrt import OpenWRTCollector
from collectors.pfsense import PfSenseCollector


class TestDetectPlatform:

    @patch('platform.system', return_value='Linux')
    @patch('os.path.exists', return_value=False)
    def test_detects_linux(self, mock_exists, mock_system):
        assert _detect_platform() == 'linux'

    @patch('platform.system', return_value='Linux')
    @patch('os.path.exists', side_effect=lambda p: p == '/etc/openwrt_release')
    def test_detects_openwrt(self, mock_exists, mock_system):
        assert _detect_platform() == 'openwrt'

    @patch('platform.system', return_value='FreeBSD')
    @patch('os.path.exists', return_value=False)
    def test_detects_freebsd(self, mock_exists, mock_system):
        assert _detect_platform() == 'freebsd'

    @patch('platform.system', return_value='FreeBSD')
    @patch('os.path.exists', side_effect=lambda p: p == '/usr/local/opnsense')
    def test_detects_opnsense(self, mock_exists, mock_system):
        assert _detect_platform() == 'opnsense'

    @patch('platform.system', return_value='FreeBSD')
    @patch('os.path.exists', side_effect=lambda p: p == '/etc/platform')
    def test_detects_pfsense(self, mock_exists, mock_system):
        assert _detect_platform() == 'pfsense'

    @patch('platform.system', return_value='Darwin')
    @patch('os.path.exists', return_value=False)
    def test_darwin_uses_freebsd(self, mock_exists, mock_system):
        assert _detect_platform() == 'freebsd'


class TestCreateCollector:

    def test_explicit_linux(self, gateway_config, base_patches):
        gateway_config.set('gateway', 'platform', 'linux')
        dc = create_collector(gateway_config)
        assert isinstance(dc, LinuxCollector)

    def test_explicit_opnsense(self, freebsd_config, bsd_patches):
        freebsd_config.set('gateway', 'platform', 'opnsense')
        dc = create_collector(freebsd_config)
        assert isinstance(dc, OPNSenseCollector)
        assert isinstance(dc, FreeBSDCollector)

    def test_explicit_openwrt(self, gateway_config, base_patches):
        gateway_config.set('gateway', 'platform', 'openwrt')
        dc = create_collector(gateway_config)
        assert isinstance(dc, OpenWRTCollector)
        assert isinstance(dc, LinuxCollector)

    def test_explicit_pfsense(self, freebsd_config, bsd_patches):
        freebsd_config.set('gateway', 'platform', 'pfsense')
        dc = create_collector(freebsd_config)
        assert isinstance(dc, PfSenseCollector)
        assert isinstance(dc, FreeBSDCollector)

    def test_explicit_freebsd(self, freebsd_config, bsd_patches):
        freebsd_config.set('gateway', 'platform', 'freebsd')
        dc = create_collector(freebsd_config)
        assert isinstance(dc, FreeBSDCollector)

    def test_unknown_platform_uses_base(self, gateway_config, base_patches):
        gateway_config.set('gateway', 'platform', 'haiku')
        dc = create_collector(gateway_config)
        assert type(dc) is BaseCollector


class TestBackwardCompat:

    def test_datacollector_shim(self, gateway_config, base_patches):
        gateway_config.set('gateway', 'platform', 'linux')
        from datacollector import DataCollector
        dc = DataCollector(gateway_config)
        assert isinstance(dc, LinuxCollector)
