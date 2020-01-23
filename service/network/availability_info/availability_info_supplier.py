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
import json
from abc import abstractmethod

import logging
from collections import defaultdict

from PySide2.QtCore import QObject, Signal, Qt

from service.network.browser_sharing import Message, ProtoError, Messages


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class AvailabilityInfoSupplier(QObject):
    _availability_info_request = Signal(Message, str)
    _availability_info_abort = Signal(Message, str)
    _availability_info_requests = Signal(Messages, str)
    _availability_requests = Signal(Messages, str, str, list)

    def __init__(
            self, parent, download_manager, connectivity_service,
            node_list):
        QObject.__init__(self, parent=parent)

        self._download_manager = download_manager
        self._connectivity_service = connectivity_service
        self._node_list = node_list.copy()

        self._subscriptions = defaultdict(set)

        self._availability_info_request.connect(
            self._on_availability_info_request, Qt.QueuedConnection)
        self._availability_info_abort.connect(
            self._on_availability_info_abort)
        self._availability_info_requests.connect(
            self._on_availability_info_requests, Qt.QueuedConnection)
        self._availability_requests.connect(
            self._on_availability_info_requests_item, Qt.QueuedConnection)

    def on_connected_nodes_changed(self, node_list):
        logger.debug("on_connected_nodes_changed %s", node_list)
        self._node_list = node_list.copy()

    def on_node_disconnected(self, node_id):
        for obj_id, subscribed_nodes in list(self._subscriptions.items()):
            subscribed_nodes.discard(node_id)
            if not subscribed_nodes:
                del self._subscriptions[obj_id]

    def on_new_availability_info(self, obj_id, offset_str, length):
        logger.debug(
            "New availability info received for obj_id: %s, "
            "notifying subscribed nodes",
            obj_id)
        if obj_id in self._subscriptions:
            for node_id in self._subscriptions[obj_id]:
                self._send_info(node_id, obj_id, [(int(offset_str), length)])

    def remove_subscriptions_on_download(self, obj_id, length):
        for node_id in self._subscriptions[obj_id]:
            self._send_info(node_id, obj_id, [(0, length)])
        del self._subscriptions[obj_id]

    def _on_availability_info_request(self, msg, node_id):
        node_type = self._connectivity_service.get_node_type(node_id)
        logger.debug(
            "_on_availability_info_request node_type: %s, node_id: %s,"
            " node_id in node_list: %s",
            node_type, node_id, node_id in self._node_list)
        if node_type and node_type != "node" or node_id in self._node_list:
            self._process_availability_info_request(
                msg.obj_id, node_id, node_type)

    def _on_availability_info_requests(self, messages, node_id):
        logger.debug("Received %s availability info requests from node %s",
                     len(messages.msg), node_id)
        node_type = self._connectivity_service.get_node_type(node_id)
        if node_type and node_type != "node" or node_id in self._node_list:
            responses = []
            self._on_availability_info_requests_item(messages.msg, node_id,
                                                     node_type, responses)

    def _on_availability_info_requests_item(self, msg, node_id,
                                            node_type, responses):
        if node_type == "node" and node_id not in self._node_list:
            return

        if msg:
            message = msg.pop(0)
            response = self._process_availability_info_request(
                message.obj_id, node_id, node_type, to_send=False)
            if response:
                responses.append(response)
            self._availability_requests.emit(msg, node_id, node_type, responses)
        elif responses:
            logger.debug("Sending %s availability info responses to node %s",
                         len(responses), node_id)
            response_messages = Messages().messages(responses)
            self._connectivity_service.send(node_id, response_messages, True)

    def _process_availability_info_request(
            self, obj_id, node_id, node_type, to_send=True):
        logger.debug(
            "Availability info request received for obj_id: %s, from node: %s",
            obj_id, node_id)
        try:
            return self._process_request(obj_id, node_id, node_type, to_send)
        except ProtoError as e:
            logger.info(
                "Availability info request for obj_id %s"
                " processing failure: %s (%s)",
                obj_id, e.err_code, e.err_message)
            err = json.dumps({"err_code": e.err_code,
                              "err_message": e.err_message}).encode()
            msg = self._generate_failure_message(obj_id, err)
            if to_send:
                self._connectivity_service.send(node_id, msg, True)
            return msg

    def _send_already_downloaded_chunks_if_any(self, node_id, node_type, obj_id,
                                               to_send=True):
        chunks = self._download_manager.get_downloaded_chunks(obj_id)
        if chunks:
            chunks = list(chunks.items())
        else:
            chunks = list()
        if not chunks and node_type == 'node':
            return None
        return self._send_info(node_id, obj_id, chunks, to_send)

    def _send_info(self, node_id, obj_id, info, to_send=True):
        logger.debug("Sending availability info for obj_id: %s to node: %s",
                     obj_id, node_id)
        msg = self._generate_response_message(obj_id, info)
        if to_send:
            self._connectivity_service.send(node_id, msg, True)
        return msg

    def _add_subscription(self, obj_id, node_id):
        logger.debug("Adding subscription to obj_id: %s for node: %s",
                     obj_id, node_id)
        self._subscriptions[obj_id].add(node_id)

    def _on_availability_info_abort(self, msg, node_id):
        logger.debug("Removing subscription to obj_id: %s for node: %s",
                     msg.obj_id, node_id)
        subscriptions = self._subscriptions.get(msg.obj_id, set())
        subscriptions.discard(node_id)
        if not subscriptions:
            self._subscriptions.pop(msg.obj_id, None)

    @abstractmethod
    def _generate_response_message(self, obj_id, info):
        raise NotImplemented()

    @abstractmethod
    def _generate_failure_message(self, obj_id, err):
        raise NotImplemented()

    @abstractmethod
    def _process_request(self, obj_id, node_id, node_type, to_send=True):
        raise NotImplemented()
