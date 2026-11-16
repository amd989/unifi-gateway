# -*- coding: utf-8 -*-
"""Tests for UnifiGateway lifecycle — stop/restart must not require [gateway]."""
import importlib
import sys
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def _mock_daemon():
    """Replace Daemon base class with a stub that works on Windows."""
    class FakeDaemon:
        def __init__(self, pidfile='_.pid', **kw):
            self.pidfile = pidfile

        def stop(self):
            import os, time
            from signal import SIGTERM
            try:
                with open(self.pidfile, 'r') as pf:
                    pid = int(pf.read().strip())
            except (IOError, FileNotFoundError):
                pid = None
            if not pid:
                return
            try:
                os.kill(pid, SIGTERM)
            except OSError:
                if os.path.exists(self.pidfile):
                    os.remove(self.pidfile)

        def restart(self):
            self.stop()
            self.start()

        def start(self):
            pass

    with patch.dict(sys.modules, {'daemon': MagicMock(Daemon=FakeDaemon)}):
        if 'unifi_gateway' in sys.modules:
            del sys.modules['unifi_gateway']
        import unifi_gateway
        yield unifi_gateway
    if 'unifi_gateway' in sys.modules:
        del sys.modules['unifi_gateway']


class TestStopWithoutGatewaySection:
    """The stop command only needs [global].pid_file to send SIGTERM.
    It must never touch the data collector or require [gateway]."""

    def test_init_without_gateway_section(self, tmp_path, _mock_daemon):
        conf = tmp_path / 'minimal.conf'
        conf.write_text('[global]\npid_file = %s\n' % (tmp_path / 'test.pid'))

        with patch.object(_mock_daemon, 'CONFIG_FILE', str(conf)):
            gw = _mock_daemon.UnifiGateway()

        assert gw.datacollector is None
        assert gw._unhandled == {}

    def test_stop_reads_pidfile_and_kills(self, tmp_path, _mock_daemon):
        pid_file = tmp_path / 'test.pid'
        conf = tmp_path / 'minimal.conf'
        conf.write_text('[global]\npid_file = %s\n' % pid_file)
        pid_file.write_text('99999\n')

        with patch.object(_mock_daemon, 'CONFIG_FILE', str(conf)):
            gw = _mock_daemon.UnifiGateway()

        with patch('os.kill') as mock_kill:
            mock_kill.side_effect = OSError('No such process')
            gw.stop()

        mock_kill.assert_called_once_with(99999, 15)  # SIGTERM = 15

    def test_restart_without_gateway_section(self, tmp_path, _mock_daemon):
        conf = tmp_path / 'minimal.conf'
        conf.write_text('[global]\npid_file = %s\n' % (tmp_path / 'test.pid'))

        with patch.object(_mock_daemon, 'CONFIG_FILE', str(conf)):
            gw = _mock_daemon.UnifiGateway()

        with patch.object(gw, 'start'):
            gw.restart()

        assert gw.datacollector is None


class TestRunInitializesCollector:
    """run() must initialize the collector before entering the inform loop."""

    def test_run_calls_init_collector(self, tmp_path, _mock_daemon):
        conf = tmp_path / 'full.conf'
        conf.write_text(
            '[global]\npid_file = %s\n'
            'disable_broadcast = True\n'
            '[gateway]\n'
            'is_adopted = False\n'
            'ports = []\n'
            'firmware = 4.4.57.5578372\n'
            % (tmp_path / 'test.pid')
        )
        with patch.object(_mock_daemon, 'CONFIG_FILE', str(conf)):
            gw = _mock_daemon.UnifiGateway()

        assert gw.datacollector is None

        with patch.object(gw, '_init_collector') as mock_init:
            gw.run()

        mock_init.assert_called_once()


class TestSystemCfgDhcpSyncHook:
    """setparam system_cfg goes to the OpenWrt DHCP sync only when [openwrt] enables it."""

    def _gateway(self, tmp_path, module, openwrt=''):
        conf = tmp_path / 'gw.conf'
        conf.write_text('[global]\npid_file = %s\n[provisioned]\n%s' % (tmp_path / 'test.pid', openwrt))
        with patch.object(module, 'CONFIG_FILE', str(conf)), \
             patch.object(module, 'UNHANDLED_LOG', str(tmp_path / 'unhandled.json')):
            gw = module.UnifiGateway()
        gw._save_config = MagicMock()
        gw._record_unhandled = MagicMock()
        return gw

    def test_disabled_sync_records_unhandled(self, tmp_path, _mock_daemon):
        gw = self._gateway(tmp_path, _mock_daemon)
        gw.dhcp_sync.apply = MagicMock()
        gw._handle_response({'_type': 'setparam', 'system_cfg': '{"udapi": {}}'})
        gw.dhcp_sync.apply.assert_not_called()
        assert gw._record_unhandled.call_args[0][1] == 'system_cfg'

    def test_enabled_sync_applies_and_errors_are_contained(self, tmp_path, _mock_daemon):
        gw = self._gateway(tmp_path, _mock_daemon, '[openwrt]\ndhcp_sync_enabled = true\n')
        gw.dhcp_sync.apply = MagicMock(side_effect=_mock_daemon.DhcpSyncError('bad'))
        gw._handle_response({'_type': 'setparam', 'system_cfg': '{"udapi": {}}'})
        gw.dhcp_sync.apply.assert_called_once_with('{"udapi": {}}')
        gw._record_unhandled.assert_not_called()
        assert gw.config.get('provisioned', 'system_cfg') == '{"udapi": {}}'
