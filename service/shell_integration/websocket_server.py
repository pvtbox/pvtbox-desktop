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
from common.utils import get_platform, HOME_DIR, ensure_unicode, make_dirs, get_cfg_dir
from .protocol import \
    parse_message, get_sync_dir_reply, emit_signal, create_command, \
    get_is_sharing_reply, get_shared_reply, get_files_status_reply, \
    get_clear_path_reply, get_share_copy_move_reply, get_file_info_reply, \
    get_offline_status_reply, get_smart_sync_reply

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class IPCWebSocketProtocol(WebSocketServerProtocol):
    def __init__(self):
        super(IPCWebSocketProtocol, self).__init__()

    def onOpen(self):
        logger.debug("Incoming WebSocket connection opened")
        clients = getattr(self.factory, 'client_connections', set())
        first_client = not clients and get_platform() == 'Windows'
        clients.add(self)
        self.factory.client_connections = clients
        self.factory.loop.call_soon_threadsafe(
           self._on_connected, first_client)

    def onClose(self, wasClean, code, reason):
        logger.debug("Incoming WebSocket connection closed")
        emit_signal("status_unsubscribe", self.peer, "")
        clients = getattr(self.factory, 'client_connections', set())
        clients.discard(self)

    def onMessage(self, payload, isBinary):
        # Parse message received
        try:
            cmd, path, link, paths, context = parse_message(payload.decode())
        except Exception as e:
            logger.warning("Can't parse message. Reason: (%s)", e)
            return

        # Sync folder path requested
        if cmd == 'sync_dir':
            self.sendMessage(get_sync_dir_reply().encode())
            return
        # Is path shared requested
        elif cmd == 'is_shared':
            self.sendMessage(get_is_sharing_reply(paths if paths else [path]).encode())
            return
        elif cmd in ('status_subscribe', 'status_unsubscribe'):
            emit_signal(cmd, self.peer, path)
            return
        elif cmd in ('share_copy', 'share_move'):
            emit_signal(cmd, paths, context)
            return
        elif cmd in('file_info', ):
            emit_signal(cmd, [path], context)
            return
        elif cmd in ('offline_off', 'offline_on'):
            is_offline = cmd == 'offline_on'
            emit_signal('offline_paths', paths, is_offline, True)
            self.sendMessage(create_command(cmd).encode())
            return
        elif cmd == 'offline_status':
            self.sendMessage(get_offline_status_reply(
                paths if paths else [path]).encode())
            return

        # Process other commands
        try:
            if link:
                emit_signal(cmd, link)
            elif paths:
                emit_signal(cmd, paths)
            else:
                emit_signal(cmd, [path])
            # Confirm successful command processing
            self.sendMessage(create_command(cmd).encode())
        except Exception as e:
            logger.error("Failed to process command '%s' (%s)", cmd, e)
            return

        self.sendMessage(payload, isBinary)

    def _on_connected(self, first_client):
        self.sendMessage(get_sync_dir_reply().encode())
        if first_client:
            self.sendMessage(create_command('refresh').encode())

        emit_signal("status_subscribe", self.peer, "")
        shared_reply = get_shared_reply()
        if shared_reply is not None:
            self.sendMessage(shared_reply.encode())

        self.sendMessage(get_smart_sync_reply().encode())


class IPCWebSocketServer(object):
    def __init__(self):
        self._loop = None
        self._factory = None
        self._server = None

    def on_settings_changed(self, settings):
        if 'sync_directory' in settings:
            old_dir, new_dir = settings['sync_directory']
            if old_dir != new_dir:
                self._loop.call_soon_threadsafe(
                    self._broadcast_sync_dir)

    def on_share_changed(self):
        self._loop.call_soon_threadsafe(self._broadcast_share)

    def on_files_status(self, client, paths, status):
        self._loop.call_soon_threadsafe(
            self._send_files_status, client, paths, status)

    def on_clear_path(self, client, path):
        self._loop.call_soon_threadsafe(self._send_clear_path, client, path)

    def on_file_info(self, path, error, context):
        self._loop.call_soon_threadsafe(
            self._broadcast_file_info, path, error, context)

    def on_paths_links(self, paths, links, context, move=False):
        self._loop.call_soon_threadsafe(
            self._broadcast_paths_links, paths, links, context, move)

    def on_smart_sync_changed(self):
        self._broadcast_smart_sync()

    def _broadcast_sync_dir(self):
        if not self._factory or not self._server:
            return

        clients = getattr(self._factory, 'client_connections', set())
        for client in clients:
            client.sendMessage(get_sync_dir_reply().encode())

    def _broadcast_share(self):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_shared_reply()
        if msg is None:
            self._loop.call_later(1., self._broadcast_share)
            return

        msg = msg.encode()
        for client in clients:
            client.sendMessage(msg)

    def _send_files_status(self, client_id, paths, status):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_files_status_reply(paths, status).encode()
        for client in clients:
            if client.peer == client_id:
                client.sendMessage(msg)
                return

    def _send_clear_path(self, client_id, path):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_clear_path_reply(path).encode()
        for client in clients:
            if client.peer == client_id:
                client.sendMessage(msg)
                return

    def _broadcast_file_info(self, path, error, context):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_file_info_reply(path, error, context).encode()
        for client in clients:
            client.sendMessage(msg)

    def _broadcast_paths_links(self,  paths, links, context, move):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_share_copy_move_reply(paths, links, context, move).encode()
        for client in clients:
             client.sendMessage(msg)

    def _broadcast_smart_sync(self):
        clients = getattr(self._factory, 'client_connections', set())
        msg = get_smart_sync_reply().encode()
        for client in clients:
            client.sendMessage(msg)

    @run_daemon
    def start(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._factory = WebSocketServerFactory(
            "ws://127.0.0.1", loop=self._loop)
        self._factory.protocol = IPCWebSocketProtocol
        coro = self._factory.loop.create_server(self._factory, '127.0.0.1', 0)
        self._server = self._factory.loop.run_until_complete(coro)

        _, port = self._server.sockets[0].getsockname()
        self._factory.setSessionParameters(url="ws://127.0.0.1:{}".format(port))

        if get_platform() == 'Darwin':
            port_path = join(
                HOME_DIR,
                'Library',
                'Containers',
                'net.pvtbox.Pvtbox.PvtboxFinderSync',
                'Data',
                'pvtbox.port')
        else:
            port_path = join(
                get_cfg_dir(),
                'pvtbox.port')
        port_path = ensure_unicode(port_path)
        self._loop.call_soon_threadsafe(
            self._write_opened_port_to_accessible_file, port, port_path)

        if get_platform() == 'Darwin':
            port_path = join(
                HOME_DIR,
                'Library',
                'Containers',
                'net.pvtbox.Pvtbox.PvtboxShareExtension',
                'Data',
                'pvtbox.port')
            port_path = ensure_unicode(port_path)
            self._loop.call_soon_threadsafe(
                self._write_opened_port_to_accessible_file, port, port_path)

        self._factory.loop.run_forever()

    def _write_opened_port_to_accessible_file(self, port, port_file):
        make_dirs(port_file, False)
        with open(port_file, 'wb') as f:
            f.write('{}'.format(port).encode())

    def close(self):
        self._loop.stop()
        self._server.close()
