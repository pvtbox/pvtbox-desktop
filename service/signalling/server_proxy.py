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

import ssl
import socket
import certifi
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlunparse, urlencode
import asyncio

from service.signalling.websocket_connection_factory \
    import WebSocketConnectionFactory

# Setup logging
from common.async_utils import run_daemon

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

TCP_CONNECTION_TIMEOUT = 10
PING_TIMEOUT_MULTIPLIER = 3


class ServerProxy(object):
    """
    Incapsulates server connection related routines
    """

    ALLOWED_CLIENT_TYPES = ('node', 'webshare')

    def __init__(self, parent, client_type, storage, debug=False):
        """
        Constructor

        @param parent
            Signal server client
        @param client_type
            Type of client connection to the server (added to server URL) [str]
        @param storage
            Instance of Storage class
        """

        assert client_type in self.ALLOWED_CLIENT_TYPES, \
            "Unsupported client type '{}'".format(client_type)

        self._parent = parent

        # Indicates that attempts to connect to the server should be started
        self._enabled = False
        self._allowed_when_disabled = ('server_disconnect', 'auth_failure')

        # Websocket client connection object
        self._ws = None

        # Type of client connection to the server
        self._client_type = client_type
        # Enable encrypted (SSL/TLS) connection
        self.use_ssl = False
        # Enable server SSL certificate verification
        self.ssl_cert_verify = True
        self.ssl_fingerprint = None
        # Interval between signalling server connection attempts (in seconds)
        self.server_reconnect_interval = 10
        # Server address and port
        self.server_addr = None
        # Server port
        self.server_port = None
        # TCP connection timeout (seconds)
        self.timeout = TCP_CONNECTION_TIMEOUT
        # WS Connection Factory and ssl context
        self.factory = None
        self.context = False

        # connection transport and protocol
        self.transport = None
        self.protocol = None

        self._storage = storage
        self._connection_params_cb = None

        self.debug = debug

        # Startup threads
        self._loop = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._ws_thread()

        self._reconnect_pending = False

        # Last received message no
        self._last_message_id = 0
        self._url = None

    def _ping_checker(self):
        """Checks server ping.
        If no ping during server ping_timeout, initiates reconnection.
        """
        if self._ws is not None:    # if connected
            ping_timeout = self.factory.get_ping_timeout()
            last_ping_time = self.factory.get_last_ping_time()
            if last_ping_time and ping_timeout and \
                    self._loop.time() - last_ping_time > \
                    PING_TIMEOUT_MULTIPLIER * ping_timeout:
                logger.warning(
                    "Ping interval exceeded {} {}".format(
                        self._loop.time() - last_ping_time, ping_timeout))
                self.factory.clear_ping_info()
                # forcibly close connection
                self.transport.close()
            else:
                # schedule next call to _ping_checker
                if ping_timeout:
                    interval = ping_timeout
                else:
                    interval = self.timeout
                self._loop.call_later(interval, self._ping_checker)
        else:  # connection lost for other reason than ping timeout
            self.factory.clear_ping_info()

    @run_daemon
    def _ws_thread(self):
        self._loop = asyncio.new_event_loop()
        self._loop.set_default_executor(self._executor)
        self._loop.set_debug(self.debug)
        self._is_running = True
        self._loop.run_forever()
        self._loop.close()

    def _add_sync_task(self, method, *args, **kwargs):
        def _task_done(fut):
            try:
                future.set_result(fut.result())
            except Exception:
                future.set_result(fut.exception())

        def _add_task():
            coro = method(*args, **kwargs)
            task = asyncio.ensure_future(coro)
            task.add_done_callback(_task_done)

        future = Future()
        self._loop.call_soon_threadsafe(_add_task)
        future.result()

        res = future.result()
        if isinstance(res, Exception):
            raise res
        return res

    def is_connected(self):
        """
        Returns flag indicating whether the connection to the signalling server
        is established or not

        @return Signalling server connection flag [bool]
        """

        return self._add_sync_task(self._is_connected)

    @asyncio.coroutine
    def _is_connected(self):
        return self._ws is not None

    def ss_connect(
            self, server_addr, server_port, use_ssl=False,
            ssl_cert_verify=False, ssl_fingerprint=None,
            server_reconnect_interval=10, timeout=10):
        """
        Starts signalling server connecting attempts.
        On successful connect 'server_connect' signal would be emitted
        On server connection lost 'server_disconnect' signal would be emitted

        @param server_addr  Signalling server IP address or hostname [string]
        @param server_port  Signalling server port [int]
        @param use_ssl Enable encrypted (SSL/TLS) connection [bool]
        @param ssl_cert_verify
            Enable server SSL certificate verification [bool]
        @param server_reconnect_interval Interval between signalling server
            connection attempts (in seconds) [float]
        @param timeout TCP connection timeout (seconds) [float]
        """

        logger.info(
            "Starting signal server connection attempts...")

        # Save parameters
        self.server_addr = server_addr
        self.server_port = server_port
        self.use_ssl = use_ssl
        self.ssl_cert_verify = ssl_cert_verify
        self.ssl_fingerprint = ssl_fingerprint
        self.server_reconnect_interval = server_reconnect_interval
        self.timeout = timeout

        self._enabled = True

        self._loop.call_soon_threadsafe(self._connect)

    def _connect(self):
        self._reconnect_pending = False
        if self._enabled:
            self._parent.get_connection_params.emit()

    def on_connection_params(self, conn_params):
        self._loop.call_soon_threadsafe(
            self._on_connection_params, conn_params)

    def _on_connection_params(self, conn_params):
        self._url = self._create_connection_url(conn_params)
        if self._url:
            asyncio.ensure_future(self._connect_coro())
        else:
            self._loop.call_soon_threadsafe(self._on_connection_lost)

    @asyncio.coroutine
    def _connect_coro(self):
        if not self._enabled:
            return None

        self.factory = WebSocketConnectionFactory(
            self._url,
            process_message=self._process_message,
            on_connected=self._on_connection_established,
            on_disconnected=self._on_connection_lost,
            last_message_id=self._last_message_id,
            ssl_fingerprint=self.ssl_fingerprint)

        if self.use_ssl:
            logger.info("cafile: %s", certifi.where())
            self.context = ssl.create_default_context(cafile=certifi.where())
            # Disable server certificate verification if specified
            if not self.ssl_cert_verify:
                self.context.check_hostname = False
                self.context.verify_mode = ssl.CERT_NONE

        logger.info('Creating connection')
        coro = self._loop.create_connection(
            self.factory, str(self.server_addr), int(self.server_port),
            ssl=self.context)
        try:
            self.transport, self.protocol = yield from asyncio.wait_for(
                coro, timeout=self.timeout)
            yield from asyncio.wait_for(
                self._wait_connected(), timeout=self.timeout)
            return None
        except asyncio.TimeoutError:
            logger.warning(
                "Connection to signalling server timed out")
        except (socket.error, OSError) as e:
            logger.warning(
                "Failed to connect to signalling server (%s)", e)
        self._loop.call_soon_threadsafe(self._on_connection_lost)
        return None

    @asyncio.coroutine
    def _wait_connected(self):
        while self._ws is None:
            yield from asyncio.sleep(0.1)

        return True

    def _create_connection_url(self, conn_params):
        if not isinstance(conn_params, dict):
            logger.error(
                "connection_params returned %s instead of dict",
                type(conn_params))
            return

        if self._client_type == 'node':
            if 'user_hash' not in conn_params or not conn_params['user_hash']:
                logger.error(
                    "No user_hash obtained via connection_params")
                return

            if 'node_hash' not in conn_params or not conn_params['node_hash']:
                logger.error(
                    "No node_hash obtained via connection_params")
                return

        elif self._client_type == 'webshare':
            if 'share_hash' not in conn_params or \
                    not conn_params['share_hash']:
                logger.error(
                    "No share_hash obtained via connection_params")
                return

        # Filter parameters with None values
        conn_params = dict(
            [p for p in list(conn_params.items()) if p[1] is not None])

        # Protocol name depending on params specified
        scheme = 'wss' if self.use_ssl else 'ws'
        # Server address in addr:port form
        netloc = ':'.join((str(self.server_addr), str(self.server_port)))
        # Path components
        path_comps = [
            '/ws',
            self._client_type]

        # Add client-type specific path components
        if self._client_type == 'node':
            path_comps.append(str(conn_params.pop('user_hash')))
            path_comps.append(str(conn_params.pop('node_hash')))
        elif self._client_type == 'webshare':
            path_comps.append(str(conn_params.pop('share_hash')))

        # Construct URL for server connecting
        url = urlunparse((
            scheme, netloc, '/'.join(path_comps),
            '', urlencode(conn_params), ''))

        return url

    def reconnect(self):
        """
        Closes signalling server connection (if any).
        Starts signalling server connecting attempts
        using connection params from previous session
        """
        if self.is_connected():
            self.ss_disconnect()
        self._loop.call_soon_threadsafe(self._reconnect)

    def _reconnect(self):
        """
        Starts signalling server connecting attempts
        using connection params from previous session
        """

        self._enabled = True
        self._connect()

    def ss_disconnect(self):
        """
        Closes signalling server connection (if any).
        Stops further connection attempts.
        On server disconnect 'server_disconnect' signal would be emitted
        """
        self._enabled = False

        self._loop.call_soon_threadsafe(self._disconnect)

    def _disconnect(self):
        if self._ws:
            self._ws.sendClose()
            self._last_message_id = self._ws.get_message_id()
            self._ws = None

    def send(self, data):
        """
        Sends message via signalling server

        @param data  Message data[string]
        @return Operation success flag [bool]
        """

        if not self.is_connected():
            return False

        self._loop.call_soon_threadsafe(self._async_send, data)
        return True

    def _async_send(self, data):
        asyncio.ensure_future(self._send(data))

    @asyncio.coroutine
    def _send(self, data):
        # Connection to server is established?
        sent = False
        if self._ws:
            logger.debug(
                "Sending message via signalling server: '%s'", data)
            try:
                # Send local ICE session info to signal server
                self._ws.sendMessage(data.encode())
                sent = True
            except Exception:
                logger.critical(
                    "Unexpected exception when sending message", exc_info=True)
        else:
            logger.error(
                "No signalling server connection, can't send message '%s'",
                data)

        return sent

    def _on_connection_established(self, connection):
        """
        Updates state on signalling server connection establishing
        """

        logger.info(
            "Connected to signal server")
        self._ws = connection

        if not self._enabled:
            # disconnect called during connection attempts
            self.ss_disconnect()
            return

        # schedule ping checker
        self._loop.call_later(
                self.timeout, self._ping_checker)
        # Call user callbacks
        self._emit_signal('server_connect')

    def _on_connection_lost(self):
        """
        Updates state on signalling server connection lost
        """
        if self._ws:
            self._last_message_id = self._ws.get_message_id()

        self._ws = None
        if self._reconnect_pending:
            return

        logger.info(
            "Connection to signal server lost")
        if self._enabled:
            self._reconnect_pending = True
            self._loop.call_later(
                self.server_reconnect_interval, self._connect)

        # Call user callbacks
        self._emit_signal('server_disconnect')

    def _emit_signal(self, signal_name, *args):
        if not self._enabled and signal_name not in self._allowed_when_disabled:
            return

        self._parent.emit_signal(signal_name, *args)

    def _process_message(self, operation, node_id, data):
        """
        Processes the message obtained

        @param operation Operation name [string]
        @param node_id Other client ID (optional) [string]
        @param data Operation data (optional)
        """

        # Obtained node IDs list
        if operation == 'peer_list':
            logger.info("Node ID list obtained")
            if data is not None:
                self._storage.set_node_info(
                    dict([(i['id'], i) for i in data]))
            else:
                self._storage.set_node_info({})
            logger.info(
                "Online node IDs: %s",
                self._storage.get_known_node_ids(online_only=True))
        # Obtained node connection notification
        elif operation == 'peer_connect':
            logger.info("Node ID=%s connected (%s)", data['id'], data)
            # Store info on node connected
            self._storage.add_node(data)
        # Obtained node disconnect notification
        elif operation == 'peer_disconnect':
            logger.info("Node ID=%s disconnected", node_id)
            # Remove node_id from known_nodes
            self._storage.remove_node(node_id)
        # Obtained SDP message
        elif operation == 'sdp':
            conn_uuid = data['conn_uuid']
            sdp_message = data['message']
            # Call user callbacks
            self._emit_signal(
                'sdp_message', node_id, conn_uuid, sdp_message)
        # Obtained list of sharing info
        elif operation == 'sharing_list':
            shares_len = len(data)
            logger.info("Obtained info on %s shares", shares_len)
            for i, rec in enumerate(data):
                self._storage.sharing_enable(
                    uuid=rec['uuid'],
                    share_hash=rec['share_hash'],
                    share_link=rec['share_link'],
                    emit_signals=(i == shares_len - 1))
        # Obtained new shared file info message
        elif operation == 'sharing_enable':
            self._storage.sharing_enable(
                uuid=data['uuid'],
                share_hash=data['share_hash'],
                share_link=data['share_link'])
        # Obtained file sharing cancelling message
        elif operation == 'sharing_disable':
            self._storage.sharing_disable(uuid=data['uuid'])
        # Obtained info on file events
        elif operation == 'file_events':
            logger.info("Obtained info on %s file event(s)", len(data))
            self._emit_signal('file_events', data, node_id)
        elif operation == 'node_status':
            logger.info(
                "Received node ID %s status update: %s", node_id, data)
            self._storage.update_node_status(node_id, data)
        else:
            logger.info("Received message %s with data %s.", operation, data)
            self._emit_signal(operation, data)
