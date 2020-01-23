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
from autobahn.asyncio import WebSocketClientFactory

from service.signalling.websocket_connection_protocol \
    import WebSocketConnectionProtocol


class WebSocketConnectionFactory(WebSocketClientFactory):
    def __init__(self, *args, **kwargs):
        self.process_message = kwargs.pop('process_message')
        self.on_connected = kwargs.pop('on_connected')
        self.on_disconnected = kwargs.pop('on_disconnected')
        self.message_id_to_skip = kwargs.pop('last_message_id')
        self.ssl_fingerprint = kwargs.pop('ssl_fingerprint', None)
        super(WebSocketConnectionFactory, self).__init__(*args, **kwargs)
        self.setProtocolOptions(
            acceptMaskedServerFrames=False, maskClientFrames=False,
            applyMask=False, tcpNoDelay=True)
        self.protocol = WebSocketConnectionProtocol
        self.last_ping_time = None
        self.ping_timeout = None
        self._message_id = self.message_id_to_skip

    def get_last_ping_time(self):
        return self.last_ping_time

    def update_last_ping_time(self):
        self.last_ping_time = self.loop.time()

    def get_ping_timeout(self):
        return self.ping_timeout

    def set_ping_timeout(self, ping_timeout):
        self.ping_timeout = ping_timeout

    def clear_ping_info(self):
        self.last_ping_time = None

    def get_message_id(self):
        self._message_id += 1
        return self._message_id
