# -*- coding: utf-8 -*-
###############################################################################
#   
#   Pvtbox. Fast and secure file transfer & sync directly across your devices. 
#   Copyright © 2020  Pb Private Cloud Solutions Ltd. 
#   
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#   
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#   
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.
#   
###############################################################################
import logging
import socket
import time
import subprocess
import errno

from autobahn.asyncio import WebSocketClientFactory, WebSocketClientProtocol

from common.utils import remove_file, is_launched_from_code

import asyncio

from os.path import join

from common import async_utils
from common.utils import get_cfg_dir, get_service_start_command, \
    get_platform, ensure_unicode, get_bases_filename, kill_all_services
from common.config import load_config

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ServiceClientProtocol(WebSocketClientProtocol):
    def onConnect(self, response):
        logger.debug("Server connected: %s", response.peer)

    def onOpen(self):
        logger.info("WebSocket connection open.")
        self.factory.service_client.on_connected(self)

    def onMessage(self, payload, isBinary):
        self.factory.service_client.on_message(self, payload.decode())

    def onPing(self, payload):
        self.factory.service_client.on_ping(self, payload)

    def onClose(self, wasClean, code, reason):
        logger.info("WebSocket connection closed: %s", reason)
        self.factory.service_client.on_disconnected(self)


class ServiceClient(object):
    start_service_timeout = 30.
    connect_to_service_interval = 0.1
    connect_timeout = 2.
    ping_timeout = 10.       # seconds

    def __init__(self, args=None, start_only=False, start_service=True,
                 starting_service_signal=lambda: None):
        self._starting_service = False
        self._connecting = False

        self._service_process = None
        self._client = None
        self._on_received = None
        self._start_only = start_only

        self._last_ping_ts = time.time()
        self._timer_handle = None
        self._stderr_log = None
        self._starting_service_signal = starting_service_signal

        if start_service:
            self.start(args)

    def _open_stderr_log(self):
        config = load_config()
        root = config.sync_directory
        baseFilename = get_bases_filename(
            root, time.strftime('%Y%m%d_%H%M%S_stderr.log'))
        self._stderr_log = open(baseFilename, 'a')

    def _close_stderr_log(self):
        if self._stderr_log:
            self._stderr_log.close()
        self._stderr_log = None

    def _reopen_stderr_log(self):
        self._close_stderr_log()
        self._open_stderr_log()

    def start(self, args=None):
        self._args = args if args else []
        logger.debug("Parameters to start service '%s'", self._args)
        if self._start_only:
            self._start_service()
            return

        self._loop = asyncio.get_event_loop()
        config_dir = get_cfg_dir(create=True)
        self._port_file = join(config_dir, 'service.port')
        self.run()

    def stop(self):
        self._loop.stop()
        self._stop_service()

    def _stop_service(self):
        try:
            remove_file(self._port_file)
        except Exception as e:
            logger.warning("Removing port file exception: %s", e)

        platform = get_platform()
        logger.debug("Stopping service process. Platform: %s", platform)
        result = 0
        try:
            while True:
                if self._service_process is None or platform == 'Windows':
                    kill_all_services()
                    logger.debug("All services killed")
                    result = 1
                else:
                    self._service_process.terminate()
                    if self._service_process.wait(timeout=10) in (0, -15):
                        result = 1
                if result != 0:
                    break
                else:
                    logger.debug("Service killing: result == 0!")
                    self._service_process = None
                time.sleep(0.5)
        except OSError as e:
            if e.errno == errno.ESRCH:
                pass
            else:
                logger.warning("Stopping service exception: %s", e)
        except Exception as e:
            logger.warning("Stopping service exception: %s", e)
        logger.debug("Stopping service returned %s", result)
        self._close_stderr_log()

    def send_gui_message(self, message):
        self._loop.call_soon_threadsafe(self._send_message, message)

    def _send_message(self, message):
        if self._client:
            try:
                logger.verbose('Sending message to service %s', message)
            except AttributeError:
                pass
            self._client.sendMessage(message.encode())
        else:
            logger.warning('Sending message to service without service connection')

    def set_receive_callback(self, cb):
        self._on_received = cb

    def on_connected(self, client):
        self._client = client
        self._args = []

        self._last_ping_ts = self._loop.time()
        self._check_ping()

    def on_disconnected(self, client):
        if self._client != client:
            return

        self._client = None
        if self._timer_handle:
            self._timer_handle.cancel()
        self._loop.call_soon_threadsafe(self._connect_to_service)

    def on_message(self, client, message):
        if self._client != client:
            return

        self._last_ping_ts = self._loop.time()
        if self._on_received:
            self._on_received(message)

    def on_ping(self, client, payload):
        if self._client != client:
            return

        self._last_ping_ts = self._loop.time()
        # logger.debug("Got ping at %s", self._last_ping_ts)

    def _check_ping(self):
        if self._loop.time() > self._last_ping_ts + self.ping_timeout:
            logger.error("Ping timeout exceeded")
            self._starting_service = False
            self._stop_service()
            self._timer_handle = None
            if self._client:
                self._client.sendClose()
            else:
                self._start_service()
        else:
            self._timer_handle = self._loop.call_later(
                self.ping_timeout / 2, self._check_ping)

    @async_utils.run_daemon
    def run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon_threadsafe(self._connect_to_service, True)
        self._loop.run_forever()

    def _connect_to_service(self, first_launch=False):
        port = None if first_launch else self._get_service_port()
        if port:
            if not self._connecting and not self._client:
                self._connecting = True
                logger.debug("Port file exist, connecting to service...")
                self._loop.create_task(self._connect(port))
        else:
            logger.debug(
                "Port file doesn't exist, starting or waiting service...")
            self._start_service()

    def _get_service_port(self):
        try:
            with open(self._port_file, 'rb') as f:
                return int(f.read().strip())
        except:
            return None

    def _start_service(self):
        if not self._starting_service:
            self._starting_service = True
            self._stop_service()
            logger.debug("Starting service...")
            options = dict(shell=True) \
                if is_launched_from_code() else dict(shell=False)
            platform = get_platform()
            if platform == 'Darwin':
                options['close_fds'] = True

            from_code = is_launched_from_code()
            if not from_code:
                self._args = map(lambda a: a.strip('"'), self._args)
            args = get_service_start_command() + \
                   list(map(lambda a: ensure_unicode(a),
                            self._args))
            cmd = ' '.join(args) if from_code else \
                list(args)
            if "--logging-disabled" not in self._args:
                self._reopen_stderr_log()
            else:
                self._stderr_log = None

            logger.debug("Service start command: %s", cmd)
            self._service_process = subprocess.Popen(
                cmd, stderr=self._stderr_log, **options)
            if not self._start_only:
                self._loop.call_later(
                    self.start_service_timeout, self._drop_starting_service)
            self._starting_service_signal.emit()

        if not self._start_only:
            self._loop.call_later(
                self.connect_to_service_interval, self._connect_to_service)

    def _drop_starting_service(self):
        self._starting_service = False

    @asyncio.coroutine
    def _connect(self, port):
        self._factory = WebSocketClientFactory(
            "ws://127.0.0.1:{}".format(port))
        self._factory.protocol = ServiceClientProtocol
        self._factory.service_client = self
        coro = self._loop.create_connection(self._factory, '127.0.0.1', port)
        try:
            yield from asyncio.wait_for(
                    coro, timeout=self.connect_timeout)
            self._loop.call_later(self.connect_timeout, self._check_connected)
            return
        except asyncio.TimeoutError:
            logger.warning(
                "Connection to service server timed out")
        except (socket.error, OSError) as e:
            logger.warning(
                "Failed to connect to service server (%s)", e)
        self._connecting = False
        self._starting_service = False
        self._start_service()

    def _check_connected(self):
        self._starting_service = False
        self._connecting = False
        if not self._client:
            self._start_service()
