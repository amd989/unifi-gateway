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


class TestCleanShutdown:
    """A stop signal must end run() at once (procd SIGKILLs after its term timeout)."""

    def test_signal_interrupts_inform_wait(self, tmp_path, _mock_daemon):
        import time
        conf = tmp_path / 'gw.conf'
        conf.write_text('[global]\npid_file = %s\n[gateway]\nis_adopted = True\n[provisioned]\n'
                        % (tmp_path / 'test.pid'))
        with patch.object(_mock_daemon, 'CONFIG_FILE', str(conf)), \
             patch.object(_mock_daemon, 'UNHANDLED_LOG', str(tmp_path / 'unhandled.json')):
            gw = _mock_daemon.UnifiGateway()
            gw.interval = 30

            def inform_then_stop(data):
                gw._handle_signal(15, None)          # SIGTERM arrives during the cycle
                return {'_type': 'noop'}

            with patch.object(_mock_daemon, 'create_collector', return_value=MagicMock()), \
                 patch.object(_mock_daemon, 'create_inform', return_value=b''), \
                 patch('signal.signal'), \
                 patch.object(gw, '_send_inform', side_effect=inform_then_stop):
                start = time.monotonic()
                gw.run()
                elapsed = time.monotonic() - start
        assert elapsed < 2, f'run() kept sleeping after the stop signal ({elapsed:.1f}s)'


class TestAtomicWrite:

    def test_replaces_and_keeps_mode(self, tmp_path, _mock_daemon):
        import os
        target = tmp_path / 'gw.conf'
        target.write_text('old')
        os.chmod(target, 0o600)
        _mock_daemon._atomic_write(str(target), lambda f: f.write('new'))
        assert target.read_text() == 'new'
        assert os.stat(target).st_mode & 0o777 == 0o600
        assert [p.name for p in tmp_path.iterdir()] == ['gw.conf']   # no temp file left

    def test_failure_keeps_old_file(self, tmp_path, _mock_daemon):
        target = tmp_path / 'gw.conf'
        target.write_text('old')

        def broken(f):
            f.write('half')
            raise RuntimeError('killed mid-write')

        with pytest.raises(RuntimeError):
            _mock_daemon._atomic_write(str(target), broken)
        assert target.read_text() == 'old'
        assert [p.name for p in tmp_path.iterdir()] == ['gw.conf']
