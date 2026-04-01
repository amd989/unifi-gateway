# -*- coding: utf-8 -*-
import configparser
import argparse
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

from collectors import create_collector
from daemon import Daemon
from unifi_protocol import (
    create_broadcast_message, create_inform, encode_inform, decode_inform,
)

logger = logging.getLogger('unifi-gateway')

CONFIG_FILE = os.environ.get('UNIFI_GW_CONFIG', 'conf/unifi-gateway.conf')


def setup_logging():
    log_level = os.environ.get('UNIFI_GW_LOG_LEVEL', 'DEBUG').upper()
    log_file = os.environ.get('UNIFI_GW_LOG_FILE', None)
    formatter = logging.Formatter(
        '%(asctime)s [unifi-gateway] : %(levelname)s : %(message)s'
    )

    if log_file:
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=3
        )
    else:
        handler = logging.StreamHandler()

    handler.setFormatter(formatter)
    logger.setLevel(getattr(logging, log_level, logging.DEBUG))
    logger.addHandler(handler)


setup_logging()


UNHANDLED_LOG = os.environ.get(
    'UNIFI_GW_UNHANDLED_LOG',
    os.path.join(os.path.dirname(CONFIG_FILE), 'unhandled_commands.json'),
)


class UnifiGateway(Daemon):

    def __init__(self, **kwargs):
        self.interval = 10
        self.running = True
        self.config = configparser.RawConfigParser()
        self.config.read(CONFIG_FILE)

        if not self.config.has_section('provisioned'):
            self.config.add_section('provisioned')

        self.datacollector = None
        self._unhandled = {}
        Daemon.__init__(
            self, pidfile=self.config.get('global', 'pid_file'), **kwargs
        )

    def _init_collector(self):
        """Initialize data collector and unhandled log. Called at run() time
        so that stop/restart commands don't need a complete [gateway] config."""
        self.datacollector = create_collector(self.config)
        self._unhandled = self._load_unhandled()

    def _load_unhandled(self):
        try:
            with open(UNHANDLED_LOG, 'r') as f:
                return json.load(f)
        except (IOError, OSError, json.JSONDecodeError):
            return {}

    def _save_unhandled(self):
        try:
            with open(UNHANDLED_LOG, 'w') as f:
                json.dump(self._unhandled, f, indent=2, sort_keys=True)
        except (IOError, OSError) as e:
            logger.warning('Failed to write unhandled log: %s', e)

    def _record_unhandled(self, category, key, response):
        entry_key = '%s/%s' % (category, key)
        is_new = entry_key not in self._unhandled
        self._unhandled[entry_key] = {
            'category': category,
            'key': key,
            'last_seen': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'count': self._unhandled.get(entry_key, {}).get('count', 0) + 1,
            'payload': {k: v for k, v in response.items()
                        if k not in ('server_time_in_utc',)},
        }
        self._save_unhandled()
        if is_new:
            logger.info(
                'New unhandled %s recorded: %s (see %s)',
                category, key, UNHANDLED_LOG,
            )

    def _try_auto_adopt(self):
        """Auto-adopt using UNIFI_ADOPT_URL env var if not already adopted."""
        if self.config.getboolean('gateway', 'is_adopted'):
            return True

        url = os.environ.get('UNIFI_ADOPT_URL')
        if not url:
            return False

        if not self.config.has_option('gateway', 'url') or \
                self.config.get('gateway', 'url', fallback='') != url:
            self.config.set('gateway', 'url', url)
            self._save_config()

        key = os.environ.get('UNIFI_ADOPT_KEY')
        if not key and self.config.has_option('provisioned', 'key'):
            key = self.config.get('provisioned', 'key')

        logger.info('Auto-adopting to controller at %s', url)
        self.set_adopt(url, key)
        return self.config.getboolean('gateway', 'is_adopted')

    def run(self):
        self._init_collector()
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if not self.config.getboolean('gateway', 'is_adopted'):
            if self._try_auto_adopt():
                logger.info('Auto-adoption succeeded')
            elif os.environ.get('UNIFI_ADOPT_URL'):
                logger.warning(
                    'Auto-adoption did not complete -- the controller '
                    'may need you to click Adopt in the UI, then the '
                    'next restart will finish adoption'
                )

        broadcast_index = 1
        while self.running and not self.config.getboolean('gateway', 'is_adopted'):
            if self.config.getboolean('global', 'disable_broadcast'):
                logger.critical(
                    'Not adopted and TLV broadcasting disabled, run set-adopt first'
                )
                return
            self._send_broadcast(broadcast_index)
            time.sleep(self.interval)
            broadcast_index += 1

        logger.info(
            'Device adopted, starting inform loop (interval=%ds)', self.interval
        )

        while self.running:
            try:
                self.datacollector.update()
                response = self._send_inform(
                    create_inform(self.config, self.datacollector)
                )
                logger.debug(
                    'Received %s from controller', response.get('_type', 'unknown')
                )
                self._handle_response(response)
            except Exception as e:
                logger.error('Error in inform loop: %s', e, exc_info=True)
                self.interval = min(self.interval * 2, 60)
            time.sleep(self.interval)

        logger.info('Shutting down gracefully')

    def _handle_signal(self, signum, frame):
        logger.info('Received signal %d, shutting down', signum)
        self.running = False

    def _handle_response(self, response):
        resp_type = response.get('_type', '')

        if resp_type == 'noop':
            self.interval = response.get('interval', self.interval)

        elif resp_type == 'setparam':
            HANDLED_SETPARAM_KEYS = {
                '_type', 'server_time_in_utc', 'blocked_sta', 'mgmt_cfg',
            }
            for key, value in response.items():
                if key == 'mgmt_cfg':
                    self._parse_mgmt_cfg(value)
                if key not in ('_type', 'server_time_in_utc', 'blocked_sta'):
                    self.config.set('provisioned', key, str(value))
                if key not in HANDLED_SETPARAM_KEYS:
                    self._record_unhandled(
                        'setparam', key,
                        {'_type': 'setparam', 'key': key, 'value': value},
                    )
            self._save_config()

        elif resp_type == 'reboot':
            logger.info('Received reboot request from controller (ignored)')

        elif resp_type == 'cmd':
            self._handle_cmd(response)

        elif resp_type == 'upgrade':
            version = response.get('version', '')
            logger.info('Received upgrade request to version %s', version)
            if version and version != self.config.get('gateway', 'firmware'):
                self.config.set(
                    'gateway', 'previous_firmware',
                    self.config.get('gateway', 'firmware'),
                )
                self.config.set('gateway', 'firmware', version)
                self._save_config()
                logger.info('Firmware version updated in config')

        elif resp_type == 'setdefault':
            logger.critical('Controller requested device reset')
            self.config.set('gateway', 'is_adopted', 'False')
            self.config.remove_option('gateway', 'key')
            self.config.set('gateway', 'use_aes_gcm', 'False')
            self._save_config()
            self.running = False

        elif resp_type == 'httperror':
            logger.warning(
                'HTTP error from controller: %s %s',
                response.get('code'), response.get('msg'),
            )

        elif resp_type == 'urlerror':
            logger.error(
                'Connection error to controller, retry in 60s: %s',
                response.get('msg'),
            )
            self.interval = 60

        else:
            logger.warning('Unhandled response type: %s', resp_type)
            self._record_unhandled('response', resp_type, response)

    def _handle_cmd(self, response):
        cmd = response.get('cmd', '')
        logger.info('Received command: %s', cmd)

        if cmd == 'speed-test':
            self._run_speedtest()
        elif cmd == 'set-locate':
            self.config.set('gateway', 'locating', 'True')
            logger.info('Locate mode enabled')
        elif cmd == 'unset-locate':
            self.config.set('gateway', 'locating', 'False')
            logger.info('Locate mode disabled')
        else:
            logger.warning('Unknown command: %s', cmd)
            self._record_unhandled('command', cmd, response)

    def _run_speedtest(self):
        logger.info('Running speed test...')
        try:
            result = subprocess.run(
                ['speedtest-cli', '--json'],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0:
                speedtest_file = './speedtest.json'
                if self.config.has_option('gateway', 'speedtest_file'):
                    speedtest_file = self.config.get('gateway', 'speedtest_file')
                with open(speedtest_file, 'w') as f:
                    f.write(result.stdout.decode('utf-8'))
                logger.info('Speed test complete, results saved')
            else:
                logger.warning(
                    'Speed test failed: %s', result.stderr.decode('utf-8')
                )
        except FileNotFoundError:
            logger.warning('speedtest-cli not installed, cannot run speed test')
        except subprocess.TimeoutExpired:
            logger.warning('Speed test timed out')
        except Exception as e:
            logger.warning('Speed test error: %s', e)

    def _send_broadcast(self, broadcast_index):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 20)
            sock.sendto(
                create_broadcast_message(self.config, broadcast_index),
                ('233.89.188.1', 10001),
            )
            sock.close()
            logger.debug(
                'Sent broadcast #%d from %s',
                broadcast_index, self.config.get('gateway', 'lan_ip'),
            )
        except Exception as e:
            logger.error('Failed to send broadcast: %s', e)

    def quit(self):
        self.running = False

    def set_adopt(self, url, key):
        if self.datacollector is None:
            self._init_collector()
        self.config.set('gateway', 'url', url)
        if key:
            self.config.set('gateway', 'key', key)
        self._save_config()

        response = self._send_inform(
            create_inform(self.config, self.datacollector)
        )
        logger.debug('Received %s from controller', response)

        if response['_type'] == 'httperror':
            if response['code'] == '404':
                logger.info(
                    'Controller has received initial inform, '
                    'adopt from GUI and re-run this command'
                )
                return
            if response['code'] == '400':
                logger.error(
                    'Authentication failed -- wrong authkey or '
                    'device removed from controller?'
                )
                return
            logger.error(
                'HTTP error from controller: %s %s',
                response['code'], response['msg'],
            )
            return

        if response['_type'] == 'urlerror':
            logger.error(
                'Connection error to controller: %s', response['msg']
            )
            return

        if response['_type'] == 'setparam':
            if not self.config.getboolean('gateway', 'is_adopted'):
                logger.info('setparam received, device now adopted')
                self.config.set('gateway', 'is_adopted', 'True')

            for k, value in response.items():
                if k == 'mgmt_cfg':
                    self._parse_mgmt_cfg(value)
                if k not in ('_type', 'server_time_in_utc', 'blocked_sta'):
                    self.config.set('provisioned', k, str(value))
            self._save_config()

    def _parse_mgmt_cfg(self, data):
        HANDLED_MGMT_KEYS = {
            'cfgversion', 'mgmt_url', 'authkey', 'use_aes_gcm',
            'inform_url', 'stun_url', 'report_crash',
            'capability', 'selfrun_guest_mode', 'led_enabled',
        }
        for row in data.split('\n'):
            if '=' not in row:
                continue
            key, value = row.split('=', 1)
            if key == 'cfgversion':
                self.config.set('provisioned', 'cfgversion', value)
            elif key == 'mgmt_url':
                self.config.set('provisioned', 'mgmt_url', value)
            elif key == 'authkey':
                logger.debug('Updating device authkey from mgmt_cfg')
                self.config.set('provisioned', 'key', value)
                self.config.set('gateway', 'key', value)
            elif key == 'use_aes_gcm':
                if not self.config.getboolean('gateway', 'use_aes_gcm'):
                    self.config.set('gateway', 'use_aes_gcm', 'True')
                    logger.debug('Switching encryption to AES-GCM')
            if key not in HANDLED_MGMT_KEYS:
                self._record_unhandled(
                    'mgmt_cfg', key,
                    {'_type': 'mgmt_cfg', 'key': key, 'value': value},
                )

    def _send_inform(self, data, encryption='CBC'):
        headers = {
            'Accept': '*/*',
            'Content-Type': 'application/x-binary',
            'User-Agent': 'AirControl Agent v1.0',
            'Expect': '100-continue',
        }
        url = self.config.get('gateway', 'url')
        request = urllib.request.Request(
            url, encode_inform(self.config, data, encryption=encryption), headers
        )
        logger.debug('Sending inform to %s', url)
        try:
            response = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as e:
            return {'_type': 'httperror', 'code': str(e.code), 'msg': str(e.reason)}
        except urllib.error.URLError as e:
            return {'_type': 'urlerror', 'msg': str(e.reason)}
        return decode_inform(self.config, response.read())

    def _save_config(self):
        with open(CONFIG_FILE, 'w') as config_file:
            self.config.write(config_file)


def main():
    parser = argparse.ArgumentParser(description='UniFi Gateway Emulator')
    subparsers = parser.add_subparsers(dest='command')

    subparsers.add_parser('start', help='Start as background daemon')
    subparsers.add_parser('stop', help='Stop the daemon')
    subparsers.add_parser('restart', help='Restart the daemon')
    subparsers.add_parser('run', help='Run in foreground')

    parser_adopt = subparsers.add_parser(
        'set-adopt', help='Send adoption request to controller'
    )
    parser_adopt.add_argument('-s', type=str, help='Controller inform URL')
    parser_adopt.add_argument('-k', type=str, help='Auth key')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    gw = UnifiGateway()

    if args.command == 'start':
        gw.start()
    elif args.command == 'stop':
        gw.stop()
    elif args.command == 'restart':
        gw.restart()
    elif args.command == 'run':
        gw.run()
    elif args.command == 'set-adopt':
        url = args.s
        if not url and gw.config.has_option('gateway', 'url'):
            url = gw.config.get('gateway', 'url')
        if not url:
            logger.error('No controller URL specified. Use -s <url>')
            sys.exit(1)
        key = args.k
        if not key and gw.config.has_option('provisioned', 'key'):
            key = gw.config.get('provisioned', 'key')
        gw.set_adopt(url, key)


if __name__ == '__main__':
    main()
