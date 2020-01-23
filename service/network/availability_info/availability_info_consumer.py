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
from PySide2.QtCore import QObject, Signal, Qt, QTimer
from service.network.browser_sharing import Message, Messages


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class AvailabilityInfoConsumer(QObject):
    availability_info_received = Signal(str, str, list)
    availability_info_failure = Signal(str, str, str)

    _subscribe_availability_info = Signal(str, bool, int)
    _unsubscribe_availability_info = Signal(str, bool)
    _subscribe_many_availability_infos = Signal(list)
    _availability_info_response = Signal(Message, str)
    _availability_info_failure = Signal(Message, str)
    _availability_info_responses = Signal(Messages, str)
    _stop = Signal()

    _priority_requests_count = 5
    _pack_threshold = 100

    def __init__(self, parent, connectivity_service, node_list):
        QObject.__init__(self, parent=parent)

        self._connectivity_service = connectivity_service
        self._subscription_objects = dict()
        self._subscriptions_queue = dict()
        self._node_list = node_list.copy()
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.setInterval(5 * 1000)
        self._timer.timeout.connect(
            self._process_subscriptions_queue)

        self._subscribe_availability_info.connect(
            self._subscribe_for_info, Qt.QueuedConnection)
        self._unsubscribe_availability_info.connect(
            self._unsubscribe_from_info, Qt.QueuedConnection)
        self._subscribe_many_availability_infos.connect(
            self._subscribe_for_many_infos, Qt.QueuedConnection)
        self._availability_info_response.connect(
            self._on_availability_info_response, Qt.QueuedConnection)
        self._availability_info_failure.connect(
            self._on_availability_info_failure, Qt.QueuedConnection)
        self._availability_info_responses.connect(
            self._on_availability_info_responses, Qt.QueuedConnection)
        self._stop.connect(self._on_stop)

        self._timer.start()

    def on_connected_nodes_changed(self, node_list):
        self._node_list = node_list.copy()

    def on_node_connected(self, node_id):
        if self._subscription_objects and \
                self._connectivity_service.get_node_type(node_id) == 'node':
            self._send_availability_info_requests(
                self._subscription_objects, [node_id])

    def subscribe(self, obj_id, force=False, priority=0):
        self._subscribe_availability_info.emit(obj_id, force, priority)

    def subscribe_many(self, subscription_list):
        self._subscribe_many_availability_infos.emit(subscription_list)

    def unsubscribe(self, obj_id, silently=False):
        self._unsubscribe_availability_info.emit(obj_id, silently)

    def stop(self):
        self._stop.emit()

    def _on_stop(self):
        self._subscriptions_queue.clear()
        self._subscription_objects.clear()
        if self._timer.isActive():
            self._timer.stop()

    def _process_subscriptions_queue(self):
        nodes_type_node = [
            n for n in self._node_list
            if self._connectivity_service.get_node_type(n) == 'node']
        if self._subscriptions_queue and nodes_type_node:
            self._send_availability_info_requests(
                self._subscriptions_queue, nodes_type_node)
            self._subscription_objects.update(self._subscriptions_queue)
            self._subscriptions_queue.clear()

    def _send_availability_info_request(self, obj_id, node_ids):
        logger.debug(
            "Sending availability info request for obj_id: %s to nodes: [%s]",
            obj_id, ','.join(map(str, node_ids)))
        msg = self._generate_request_message(obj_id)
        self._send(msg, node_ids)

    def _send_availability_info_requests(self, objects, node_ids):
        sorted_objs = list(objects.keys())
        logger.debug("Preparing to send availability info requests "
                     "for %s subscription objects...", len(sorted_objs))
        sorted_objs.sort(key=lambda x: objects[x], reverse=True)
        requests = []
        for count, obj_id in enumerate(sorted_objs):
            logger.debug(
                "Preparing to send availability info request "
                "for obj_id: %s",
                obj_id)
            if count < self._priority_requests_count:
                self._send_availability_info_request(obj_id, node_ids)
            else:
                requests.append(self._generate_request_message(obj_id))
                if len(requests) >= self._pack_threshold:
                    request_messages = Messages().messages(requests)
                    logger.debug("Sending %s availability info requests...",
                                 len(requests))
                    self._send(request_messages, node_ids)
                    requests = []

        if requests:
            request_messages = Messages().messages(requests)
            logger.debug("Sending %s availability info requests...",
                         len(requests))
            self._send(request_messages, node_ids)

    def _send(self, message, node_ids):
        for node_id in node_ids:
            self._connectivity_service.send(
                node_id, message, False)

    def _subscribe_for_info(self, obj_id, force, priority):
        if not force and obj_id in self._subscription_objects:
            return
        logger.info("Adding availability info subscription for obj_id: %s",
                    obj_id)
        self._subscriptions_queue[obj_id] = priority

    def _subscribe_for_many_infos(self, subscription_list):
        logger.info("Adding many availability info subscriptions: %s",
                    len(subscription_list))
        # s[0] - obj_id, s[1] - priority
        list(map(lambda s: self._subscribe_for_info(s[0], False, s[1]),
                 subscription_list))

    def _unsubscribe_from_info(self, obj_id, silently=False):
        if obj_id not in self._subscription_objects \
                and obj_id not in self._subscriptions_queue:
            return
        logger.info("Removing availability info subscription for obj_id: %s",
                    obj_id)
        found = self._subscription_objects.pop(obj_id, None)
        self._subscriptions_queue.pop(obj_id, None)
        nodes_type_node = [
            n for n in self._node_list
            if self._connectivity_service.get_node_type(n) == 'node']
        if not silently and found is not None:
            for node_id in nodes_type_node:
                self._send_availability_info_abort(obj_id, node_id)

    def _on_availability_info_response(self, msg, node_id):
        if msg.obj_id not in self._subscription_objects:
            self._send_availability_info_abort(
                msg.obj_id, node_id)
            return
        if not msg.info:
            logger.debug(
                "empty availability info received from node_id %s for obj_id: %s",
                node_id, msg.obj_id)
            return

        logger.debug(
            "availability info received from node_id %s for obj_id: %s",
            node_id, msg.obj_id)

        self.availability_info_received.emit(node_id, msg.obj_id, msg.info)

    def _on_availability_info_responses(self, messages, node_id):
        for msg in messages.msg:
            self._on_availability_info_response(msg, node_id)

    def _send_availability_info_abort(self, obj_id, node_id):
        logger.debug(
            "Sending availability info abort for obj_id: %s to node_id: %s",
            obj_id, node_id)
        msg = self._generate_abort_message(obj_id)
        self._connectivity_service.send(node_id, msg, False)

    def _on_availability_info_failure(self, msg, node_id):
        logger.debug(
            "availability info received from node_id %s for obj_id: %s",
            node_id, msg.obj_id)
        self.availability_info_failure.emit(node_id, msg.obj_id, msg.error)

    @abstractmethod
    def _generate_request_message(self, obj_id):
        raise NotImplemented()

    @abstractmethod
    def _generate_abort_message(self, obj_id):
        raise NotImplemented()
