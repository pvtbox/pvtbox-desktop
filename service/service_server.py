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
from autobahn.asyncio import WebSocketServerFactory, WebSocketServerProtocol
from os.path import join
import asyncio

from common.async_utils import run_daemon
from common.utils import make_dirs

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ServiceServerProtocol(WebSocketServerProtocol):
    def __init__(self):
        super(ServiceServerProtocol, self).__init__()

    def onOpen(self):
        logger.debug("Incoming WebSocket connection opened")
        clients = getattr(self.factory, 'client_connections', set())
        clients.add(self)
        self.factory.client_connections = clients
        if self.factory.on_client_connected:
            self.factory.on_client_connected()

    def onClose(self, wasClean, code, reason):
        logger.debug("Incoming WebSocket connection closed")
        clients = getattr(self.factory, 'client_connections', set())
        clients.discard(self)

    def onMessage(self, payload, isBinary):
        self.factory.on_received(payload.decode())


class ServiceServer(object):
    ping_interval = 2.   # seconds

    def __init__(self, config_path):
        self._loop = asyncio.get_event_loop()
        self._factory = None
        self._server = None
        self._on_client_connected = None
        self._on_received = None
        self.start(config_path)

    def set_on_client_connected_callback(self, cb):
        self._on_client_connected = cb

    def set_receive_callback(self, cb):
        self._on_received = cb
        if self._factory:
            self._factory.on_received = cb

    def send_gui_message(self, message):
        self._loop.call_soon_threadsafe(self._broadcast_message, message)

    def _broadcast_message(self, message):
        clients = getattr(self._factory, 'client_connections', set())
        for client in clients:
            try:
                logger.verbose('Sending message to app %s', message)
            except AttributeError:
                pass
            client.sendMessage(message.encode())

    def _on_connected(self):
        self._do_ping()
        if self._on_client_connected:
            self._on_client_connected()

    def _do_ping(self):
        clients = getattr(self._factory, 'client_connections', set())
        for client in clients:
            client.sendPing()
        self._loop.call_later(self.ping_interval, self._do_ping)

    @run_daemon
    def start(self, config_path):
        asyncio.set_event_loop(self._loop)
        self._factory = WebSocketServerFactory(
            "ws://127.0.0.1", loop=self._loop)
        self._factory.protocol = ServiceServerProtocol
        self._factory.on_client_connected = self._on_client_connected
        self._factory.on_received = self._on_received
        self._factory.on_client_connected = self._on_connected
        coro = self._factory.loop.create_server(self._factory, '127.0.0.1', 0)
        self._server = self._factory.loop.run_until_complete(coro)

        _, port = self._server.sockets[0].getsockname()
        self._factory.setSessionParameters(url="ws://127.0.0.1:{}".format(port))

        port_file = join(config_path, 'service.port')
        self._loop.call_soon_threadsafe(
            self._write_opened_port_to_accessible_file, port, port_file)

        self._factory.loop.run_forever()

    def _write_opened_port_to_accessible_file(self, port, port_file):
        make_dirs(port_file, False)
        with open(port_file, 'wb') as f:
            f.write('{}'.format(port).encode())

    def close(self):
        self._loop.stop()
        self._server.close()
