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

from autobahn.asyncio import WebSocketClientProtocol
from service.signalling.signalling_protocol import parse_msg

from hashlib import sha256

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class WebSocketConnectionProtocol(WebSocketClientProtocol):
    def onConnecting(self, transport_details):
        if self.factory.ssl_fingerprint:
            cert_fingerprint = sha256(
                self.transport._ssl_protocol._extra["ssl_object"]
                    .getpeercert(True)).hexdigest()
            if cert_fingerprint != self.factory.ssl_fingerprint:
                logger.error("ssl certificate mismatch, expected: %s, actual: %s",
                             self.factory.ssl_fingerprint, cert_fingerprint)
                self.failHandshake("ssl validation error")

    def onOpen(self):
        logger.info("WebSocket connection opened.")
        self._closing = False
        self.factory.on_connected(self)
        self.factory.update_last_ping_time()

    def onMessage(self, payload, isBinary):
        self.factory.update_last_ping_time()
        try:
            if not self._closing:
                message_id = self.factory.get_message_id()
                self.factory.loop.run_in_executor(
                    None, self._on_message, payload, message_id)
        except AttributeError as e:
            logger.warning("No attribute: %s", e)
        except RuntimeError as e:
            logger.warning("Possibly trying to process message "
                           "after executor shutdown. %s", e)

    def onClose(self, wasClean, code, reason):
        logger.debug("WebSocket connection closed, wasClean: {} "
                     "code: {}, reason: {}.".format(wasClean, code, reason))
        if code == 1006 and "403 - Forbidden" in reason:
            self.factory.callbacks.call('on_auth_failure')
        if code != 1008:
            # 1008 - ignore closing if client is reconnecting
            self.factory.on_disconnected()

    def onPing(self, payload):
        if not self.factory.get_ping_timeout():
            # detect server ping timeout
            ping_timeout = int(payload)
            self.factory.set_ping_timeout(ping_timeout)
            logger.debug("Ping timeout detected: {} ".format(
                self.factory.get_ping_timeout()))

        # Update ping timestamp
        self.factory.update_last_ping_time()
        logger.debug("WEBSOCKET PING")
        WebSocketClientProtocol.onPing(self, payload)

    def sendMessage(self,
                    payload,
                    isBinary=False,
                    fragmentSize=None,
                    sync=False,
                    doNotCompress=False):
        super(WebSocketConnectionProtocol, self).sendMessage(
            payload, isBinary, fragmentSize, sync, doNotCompress)

    def sendClose(self, code=None, reason=None):
        self._closing = True
        self.factory.message_id_to_skip = self.factory.get_message_id()
        super(WebSocketConnectionProtocol, self).sendClose(
            code, reason)

    def _on_message(self, payload, message_id):
        if message_id <= self.factory.message_id_to_skip:
            return

        # Try to parse message obtained
        try:
            operation, node_id, data = parse_msg(payload)
        except Exception as e:
            logger.error("Failed to parse message '%s' (%s)", payload, e)
            return
        # Try to process message obtained
        try:
            self.factory.process_message(operation, node_id, data)
        except Exception as e:
            logger.error(
                "Exception occured while processing message '%s' (%s)",
                payload, e)

    def get_message_id(self):
        return self.factory.get_message_id()
