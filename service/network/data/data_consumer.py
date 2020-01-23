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
from abc import abstractmethod

import logging
from PySide2.QtCore import QObject, Signal

from service.network.browser_sharing import Message

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DataConsumer(QObject):
    # workaround for PySide crash. see
    # https://stackoverflow.com/questions/23728401
    # /pyside-crashing-python-when-emitting-none-between-threads
    # tuple is (unicode, unicode, long, long, object)
    data_received = Signal(tuple)
    error_received = Signal(str,    # node_id
                            str,    # obj_id
                            str,    # str(offset) to fix offset >= 2**31
                            str)    # error message

    _data_response = Signal(Message, str)
    _data_failure = Signal(Message, str)

    def __init__(self, parent, connectivity_service):
        QObject.__init__(self, parent=parent)
        self._connectivity_service = connectivity_service

        self._download_limiter = None

        self._data_response.connect(self._on_data_response)
        self._data_failure.connect(self._on_data_failure)

    def set_download_limiter(self, limiter):
        self._download_limiter = limiter

    def request_data(self, node_id, obj_id, offset_str, length):
        logger.debug("Requesting data (%s, %s) for object %s from node_id %s",
                     offset_str, length, obj_id, node_id)
        msg = self._generate_data_request_message(
            obj_id, int(offset_str), length)
        self._send_request(node_id, msg)

    def abort_data_request(self, node_id, obj_id, offset_str):
        logger.debug("Aborting data request (%s, )"
                     " for object %s from node_id %s",
                     offset_str, obj_id, node_id)
        try:
            offset = int(offset_str)
        except (ValueError, TypeError):
            offset = None
        msg = self._generate_data_abort_message(
            obj_id, offset)
        self._connectivity_service.send(node_id, msg, False)

    def _send_request(self, node_id, msg):
        self._connectivity_service.send(node_id, msg, False)

    def _on_data_response(self, msg, node_id):
        try:
            self.data_received.emit((
                node_id, msg.obj_id,
                msg.info[0].offset, msg.info[0].length, msg.data))
        except IndexError:
            pass

    def _on_data_failure(self, msg, node_id):
        try:
            self.error_received.emit(
                node_id, msg.obj_id, str(msg.info[0].offset), msg.error)
        except IndexError:
            pass

    @abstractmethod
    def _generate_data_request_message(self, obj_id, offset, length):
        raise NotImplemented()

    @abstractmethod
    def _generate_data_abort_message(self, obj_id, offset):
        raise NotImplemented()
