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
from PySide2.QtCore import Signal, QObject, Qt
from .gui_protocol import GuiProtocol

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class MessageProxy(QObject):
    _message_received = Signal(str)

    def __init__(self, parent=None, receivers=(),
                 socket_client=None, verbose=False):
        QObject.__init__(self, parent=parent)
        self._protocol = GuiProtocol(receivers, verbose)
        self._socket_client = socket_client
        self._message_received.connect(self._on_message_received,
                                       Qt.QueuedConnection)
        self._socket_client.set_receive_callback(
            self.on_message_received)
        self._is_deaf = False

    def add_receiver(self, receiver):
        self._protocol.add_receiver(receiver)

    def make_deaf(self):
        self._is_deaf = True

    def make_hearing(self):
        self._is_deaf = False

    def send_message(self, action, data=None):
        """
        Sends message with given action name and parameteres
        :param action: Name of sugnal or method to be invoked [str]
        :param data: list of paramaters or None
        :return:
        """
        if self._is_deaf:
            logger.debug("Send: Message proxy is deaf")
            return

        if data:
            message = self._protocol.create_action(action, data)
        else:
            message = self._protocol.create_action(action)
        self._socket_client.send_gui_message(message)

    def on_message_received(self, encoded):
        self._message_received.emit(encoded)

    def _on_message_received(self, encoded):
        if self._is_deaf:
            logger.debug("Receive: Message proxy is deaf")
            return

        try:
            action, data = self._protocol.parse_message(encoded)
        except Exception as e:
            logger.warning("Can't parse message %s. Reason: %s", encoded, e)
            return

        try:
            if data:
                self._protocol.call(action, *data)
            else:
                self._protocol.call(action)
        except ValueError:
            logger.warning("Invalid action %s, data %s", action, data)
