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
import logging
from collections import defaultdict
from json import JSONDecodeError
from time import time
from PySide2.QtCore import QObject, QTimer, Signal, Qt
from service.network.leakybucket import LeakyBucketException
from contextlib import contextmanager
import random

import faulthandler
faulthandler.enable()
from webrtc import WebRtc

from common.async_qt import qt_run
from service.network.connectivity.connection import Connection
from service.network.connectivity.statistic_parser import StatisticParser
from service.network.connectivity.webrtc_listener import WebRtcListener
from common.constants import NETWORK_WEBRTC_RELAY, NETWORK_WEBRTC_DIRECT, \
    CONNECTIVITY_ALIVE_TIMEOUT

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ConnectivityService(QObject):
    HARD_CONNECTIONS_LIMIT = 8
    CONNECT_TIMEOUT = 20 * 1000
    CONNECT_INTERVAL = 1000
    RESEND_INTERVAL = 250
    LEAKY_INTERVAL = 15
    connected_nodes_incoming_changed = Signal(set)
    connected_nodes_outgoing_changed = Signal(set)
    node_incoming_connected = Signal(str)
    node_outgoing_connected = Signal(str)
    node_incoming_disconnected = Signal(str)
    node_outgoing_disconnected = Signal(str)
    # workaround for PySide crash. see
    # https://stackoverflow.com/questions/23728401
    # /pyside-crashing-python-when-emitting-none-between-threads
    # tuple is (unicode, object)
    data_received = Signal(tuple,       # params
                           str)     # connection id
    quit = Signal()
    exited = Signal()

    init = Signal()
    disconnect_ss_slots = Signal()
    connect_ss_slots = Signal()

    _refresh_connections = Signal()
    _connect_to_node = Signal(str)
    _check_connected = Signal(bytes, bool)
    _disconnect_from_node = Signal(str, bool, bool)
    _disconnect_from_all_nodes = Signal()
    _send_messages = Signal(tuple)
    _add_ice_server = Signal(tuple)
    _reconnect = Signal(str)

    connected = Signal(bytes)
    disconnected = Signal(bytes)
    message = Signal(tuple)
    buffered_amount_changed = Signal(bytes, int)
    statistic = Signal(bytes, bytes)
    on_local_description = Signal(bytes, bytes, bytes)
    on_candidate = Signal(bytes, bytes, int, bytes)

    # sdp_message_from_peer = Signal(str, str, str)

    _connection_is_relayed = Signal(bytes)

    def __init__(self, ss_client, network_speed_calculator,
                 parent=None, webrtc_class=WebRtc):
        QObject.__init__(self, parent=parent)
        self._webrtc_class = webrtc_class
        self._ss_client = ss_client

        self._ice_servers = dict()

        self._incoming_connections = dict()
        self._incoming_node_connections = defaultdict(set)

        self._outgoing_connections = dict()
        self._outgoing_node_connections = defaultdict(set)

        self._connected_incoming_nodes = set()
        self._connected_outgoing_nodes = set()
        self._relayed_nodes = set()
        self._nodes_waiting_for_connect = set()

        self._upload_limiter = None
        self._network_speed_calculator = network_speed_calculator
        self._refresh_connections_timer = QTimer(self)
        self._refresh_connections_timer.setInterval(1000)
        self._refresh_connections_timer.setSingleShot(True)
        self._refresh_connections_timer.timeout.connect(
            self._refresh_connections.emit)

        self._refresh_connections.connect(
            self._on_refresh_connections, Qt.QueuedConnection)
        self._disconnect_from_node.connect(
            self._on_disconnect_from_node, Qt.QueuedConnection)
        self._disconnect_from_all_nodes.connect(
            self._on_disconnect_from_all_nodes, Qt.QueuedConnection)

        self.init.connect(
            self._on_init, Qt.QueuedConnection)
        self.disconnect_ss_slots.connect(
            self._disconnect_ss_slots, Qt.QueuedConnection)
        self.connect_ss_slots.connect(
            self._connect_ss_slots, Qt.QueuedConnection)

        self._connect_to_node.connect(self._on_connect_to_node,
                                      Qt.QueuedConnection)
        self._check_connected.connect(self._on_check_connected,
                                      Qt.QueuedConnection)
        self._reconnect.connect(self._on_reconnect_to_node,
                                Qt.QueuedConnection)

        self.quit.connect(self._on_quit, Qt.QueuedConnection)

        self._start_method_time = 0
        self._end_method_time = 0

    @contextmanager
    def _mark_time(self):
        self._start_method_time = time()
        try:
            yield
        finally:
            self._end_method_time = time()

    def add_ice_server(self, server_id, url, login, password):
        self._add_ice_server.emit((server_id, url, login, password))

    def send(self, node_id, message, by_incoming_connection):
        self._send_messages.emit(
            (node_id, [message], None, None, None, False,
             by_incoming_connection))

    def send_messages(self, node_id, messages, request,
                      on_sent_callback=None, check_func=None):
        self._send_messages.emit(
            (node_id, messages, request, on_sent_callback,
             check_func, True, True))

    def is_relayed(self, node_id):
        return node_id in self._relayed_nodes

    def set_upload_limiter(self, upload_limiter):
        self._upload_limiter = upload_limiter

    def reconnect(self, node_id):
        self._reconnect.emit(node_id)

    def _on_add_ice_server(self, server_data):
        with self._mark_time():
            server_id, url, login, password = server_data
            if server_id in self._ice_servers:
                return

            self._ice_servers[server_id] = (url, login, password)
            self._webrtc.add_ice_server(
                url.encode(), login.encode(), password.encode())

    def _on_send_messages(self, messages_tuple):
        node_id, messages, request, on_sent_callback, \
        check_func, limit_upload, is_incoming = \
            messages_tuple
        logger.debug("Sending messages for node %s", node_id)
        existing_connections = self._get_existing_connections(
            node_id, request, on_sent_callback, check_func, is_incoming)
        if not existing_connections:
            return

        while messages:
            is_sent, interval = self._send_message_through_existing_connection(
                node_id, messages, existing_connections, limit_upload)
            if not is_sent:
                QTimer.singleShot(
                    interval,
                    lambda: self._send_messages.emit(messages_tuple))
                return

        if callable(on_sent_callback):
            on_sent_callback(request)

    def _get_existing_connections(self, node_id, request, on_sent_callback,
                                  check_func, is_incoming):
        connections = list()
        if callable(check_func):
            if not check_func(request):
                logger.warning("check_func returned None (False) "
                               "for node_id %s, request %s",
                               node_id, request)
                if callable(on_sent_callback):
                    on_sent_callback(request)
                return connections

        if is_incoming:
            connection_ids = self._incoming_node_connections.get(node_id, [])
            connections = [self._incoming_connections[c_id]
                           for c_id in connection_ids]
        else:
            connection_ids = self._outgoing_node_connections.get(node_id, [])
            connections = [self._outgoing_connections[c_id]
                           for c_id in connection_ids]
        if not connections:
            logger.warning("No connections for node %s", node_id)
            if callable(on_sent_callback):
                on_sent_callback(request)

        return connections

    def _send_message_through_existing_connection(self, node_id, messages,
                                                  existing_connections,
                                                  limit_upload):
        with self._mark_time():
            ready_connections = [c for c in existing_connections
                                 if c.open and not c.is_buffer_overflow()]
            if not ready_connections:
                return False, self.RESEND_INTERVAL

            message = messages[0]
            message_len = len(message)
            if self._upload_limiter and limit_upload:
                try:
                    self._upload_limiter.leak(message_len)
                except LeakyBucketException:
                    return False, self.LEAKY_INTERVAL

            connection = random.choice(ready_connections)
            connection.used = True
            messages.pop(0)
            logger.verbose("Sending message through connection %s",
                           connection.id)
            self._webrtc.send(connection.id, message, message_len, True)

            if self._network_speed_calculator:
                self._network_speed_calculator.on_data_uploaded(
                    message_len,
                    NETWORK_WEBRTC_RELAY if self.is_relayed(node_id)
                    else NETWORK_WEBRTC_DIRECT)
            return True, 0

    def _on_init(self):
        logger.debug("Connectivity thread started")
        self._start_method_time = self._end_method_time = time()

        with self._mark_time():
            self._send_messages.connect(
                self._on_send_messages, Qt.QueuedConnection)
            self._add_ice_server.connect(
                self._on_add_ice_server, Qt.QueuedConnection)
            self.connected.connect(self._on_connected,
                                   Qt.QueuedConnection)
            self.disconnected.connect(self._on_disconnected,
                                      Qt.QueuedConnection)
            self.message.connect(self._on_message,
                                 Qt.QueuedConnection)
            self.buffered_amount_changed.connect(
                self._on_buffered_amount_changed, Qt.QueuedConnection)
            self.on_local_description.connect(
                self._on_local_description, Qt.QueuedConnection)
            self.on_candidate.connect(self._on_candidate, Qt.QueuedConnection)
            self.statistic.connect(self._on_statistic, Qt.QueuedConnection)
            self._connection_is_relayed.connect(
                self._on_connection_is_relayed, Qt.QueuedConnection)

            self._webrtc_listener = WebRtcListener(self)
            self._webrtc = self._webrtc_class()
            self._webrtc.set_listener(self._webrtc_listener)

            # self.sdp_message_from_peer.connect(self._sdp_message_from_peer)

            self._connect_ss_slots()

            self._refresh_connections.emit()

    def _connect_ss_slots(self):
        self._ss_client.node_list_obtained.connect(
            self.on_node_list_obtained_cb, Qt.QueuedConnection)
        self._ss_client.node_connect.connect(
            self.on_node_connect_cb, Qt.QueuedConnection)
        self._ss_client.node_disconnect.connect(
            self.on_node_disconnect_cb, Qt.QueuedConnection)
        self._ss_client.server_disconnect.connect(
            self.on_server_disconnect_cb, Qt.QueuedConnection)
        self._ss_client.sdp_message.connect(
            self._sdp_message_from_peer, Qt.QueuedConnection)

    def _disconnect_ss_slots(self):
        try:
            self._ss_client.node_list_obtained.disconnect(
                self.on_node_list_obtained_cb)
            self._ss_client.node_connect.disconnect(
                self.on_node_connect_cb)
            self._ss_client.node_disconnect.disconnect(
                self.on_node_disconnect_cb)
            self._ss_client.server_disconnect.disconnect(
                self.on_server_disconnect_cb)
            self._ss_client.sdp_message.disconnect(
                self._sdp_message_from_peer)
        except Exception as e:
            logger.warning("Can't disconnect ss slots. Reason: (%s)", e)

    def on_node_list_obtained_cb(self, _):
        self._refresh_connections.emit()

    def on_node_connect_cb(self, node_info):
        self._disconnect_from_node.emit(node_info.get('id', ''), True, True)
        self._refresh_connections.emit()

    def on_node_disconnect_cb(self, node_id):
        self._disconnect_from_node.emit(node_id, True, True)

    def on_server_disconnect_cb(self):
        self._disconnect_from_all_nodes.emit()

    def get_connected_incoming_nodes(self):
        return self._connected_incoming_nodes

    def get_connected_outgoing_nodes(self):
        return self._connected_outgoing_nodes

    def _on_refresh_connections(self):
        logger.info("Refreshing connections")
        if not self._ss_client.is_connected():
            return

        online_node_ids = set(
            self._ss_client.get_nodes(
                allowed_types=('node',), online_only=True))
        logger.debug("Online node ids: %s", online_node_ids)
        count = 0
        for node_id in online_node_ids:
            count += 1
            self._schedule_node_connect(self.CONNECT_INTERVAL * count, node_id)

    def _schedule_node_connect(self, interval, node_id):
        def connect():
            self._nodes_waiting_for_connect.discard(node_id)
            self._connect_to_node.emit(node_id)

        if node_id not in self._nodes_waiting_for_connect:
            self._nodes_waiting_for_connect.add(node_id)
            QTimer.singleShot(interval, connect)

    def _on_connect_to_node(self, node_id):
        if not self._ss_client.is_connected():
            return

        logger.debug("on_connect_to_node: %s", node_id)
        online_node_ids = set(
            self._ss_client.get_nodes(
                allowed_types=('node',), online_only=True))
        if node_id not in online_node_ids:
            return

        if self._is_connections_limit_reached(
                self._outgoing_node_connections.get(node_id),
                5 // len(online_node_ids) + 1):
            return

        self._connect_to_node_via_webrtc(node_id)

    def _on_reconnect_to_node(self, node_id):
        self._on_disconnect_from_node(node_id, False, True)
        if self._refresh_connections_timer.isActive():
            self._refresh_connections_timer.stop()
        self._refresh_connections_timer.start()

    def _is_connections_limit_reached(self, connections, limit):
        return len(connections) >= limit if connections else False

    def _connect_to_node_via_webrtc(self, node_id):
        with self._mark_time():
            connection = Connection(node_id)
            connection_id = connection.id
            logger.debug("Connecting to node %s via webrtc... "
                         "Connection id %s",
                         node_id, connection_id)
            self._outgoing_connections[connection_id] = connection
            self._outgoing_node_connections[node_id].add(connection_id)
            self._webrtc.create_connection(connection_id)
            self._webrtc.initiate_connection(connection_id)
            QTimer.singleShot(self.CONNECT_TIMEOUT,
                              lambda: self._check_connected.emit(
                                  connection_id, False))

    def _on_check_connected(self, connection_id, is_incoming):
        with self._mark_time():
            logger.debug("Check connected %s", connection_id)
            if is_incoming:
                connection = self._incoming_connections.get(
                    connection_id, None)
            else:
                connection = self._outgoing_connections.get(
                    connection_id, None)
            if not connection:
                logger.debug("No connection %s", connection_id)
                return

            if not connection.open:
                logger.debug("Connection %s is not opened", connection_id)
                self._webrtc.disconnect(connection_id)
                self._on_disconnected(connection_id)

    def _on_connected(self, connection_id):
        with self._mark_time():
            logger.info("_on_connected %s", connection_id)
            is_incoming = False
            connection = self._outgoing_connections.get(
                connection_id, None)
            if not connection:
                connection = self._incoming_connections.get(
                    connection_id, None)
                is_incoming = True
            if not connection:
                logger.debug("No node id for connection id %s", connection_id)
                self._webrtc.disconnect(connection_id)
                return

            connection.open = True
            if connection.node_id not in self._connected_incoming_nodes and \
                    connection.node_id not in self._connected_outgoing_nodes:
                self._webrtc.request_statistic(connection_id)

            if not is_incoming and connection.node_id not in \
                    self._connected_outgoing_nodes:
                self._connected_outgoing_nodes.add(connection.node_id)
                self.connected_nodes_outgoing_changed.emit(
                    self._connected_outgoing_nodes)
                self.node_outgoing_connected.emit(connection.node_id)
            elif is_incoming and connection.node_id not in \
                    self._connected_incoming_nodes:
                self._connected_incoming_nodes.add(connection.node_id)
                self.connected_nodes_incoming_changed.emit(
                    self._connected_incoming_nodes)
                self.node_incoming_connected.emit(connection.node_id)

            self._refresh_connections.emit()

    def _on_disconnected(self, connection_id):
        logger.info("_on_disconnected %s", connection_id)
        is_incoming = False
        connection = self._outgoing_connections.pop(connection_id, None)
        if not connection:
            connection = self._incoming_connections.pop(connection_id, None)
            is_incoming = True
        if connection:
            connection.open = False
            if is_incoming:
                connections = self._incoming_node_connections[
                    connection.node_id]
                connections.discard(connection_id)
                if not connections:
                    self._on_disconnect_from_node(
                        connection.node_id, True, False)
            else:
                connections = self._outgoing_node_connections[
                    connection.node_id]
                connections.discard(connection_id)
                self._on_disconnect_from_node(
                    connection.node_id, False, True)
                
        if self._refresh_connections_timer.isActive():
            self._refresh_connections_timer.stop()
        self._refresh_connections_timer.start()

    def _on_buffered_amount_changed(self, connection_id, amount):
        connection = self._incoming_connections.get(connection_id, None)
        if not connection:
            connection = self._outgoing_connections.get(connection_id, None)
        if connection:
            connection.buffered_amount = amount

    def _on_message(self, message_tuple):
        connection_id, message = message_tuple
        logger.debug("_on_message. Connection id %s",
                     connection_id)
        connection = self._incoming_connections.get(connection_id, None)
        if not connection:
            connection = self._outgoing_connections.get(connection_id, None)
        node_id = connection.node_id if connection else None

        if self._network_speed_calculator:
            self._network_speed_calculator.on_data_downloaded(
                len(message),
                NETWORK_WEBRTC_RELAY if self.is_relayed(node_id)
                else NETWORK_WEBRTC_DIRECT)

        if node_id:
            self.data_received.emit((node_id, message), connection_id.decode())
        else:
            logger.warning("_on_message from unknown connection: %s",
                           connection_id)

    @qt_run
    def _on_statistic(self, connection_id, statistics):
        logger.debug("_on_statistic")
        statistic = StatisticParser.parse_statistic(statistics.decode())
        if statistic is None:
            logger.warning("Failed to parse connection statistic")
            return

        if StatisticParser.determine_if_connection_relayed(statistic):
            self._connection_is_relayed.emit(connection_id)

    def _on_connection_is_relayed(self, connection_id):
        logger.debug("_on_connection_is_relayed")
        connection = self._outgoing_connections.get(connection_id, None)
        if not connection:
            connection = self._incoming_connections.get(connection_id, None)
        if connection:
            self._relayed_nodes.add(connection.node_id)

    def _on_disconnect_from_node(self, node_id, disconnect_incoming,
                                 disconnect_outgoing):
        with self._mark_time():
            logger.info("Node %s disconnected from signal server, "
                        "or no reliable connections for node."
                        "disconnect from it", node_id)
            if disconnect_incoming:
                connections = self._incoming_node_connections[node_id]
                for connection_id in connections:
                    logger.debug("Disconnect connection %s", connection_id)
                    self._webrtc.disconnect(connection_id)
                    self._incoming_connections.pop(connection_id, None)
                del self._incoming_node_connections[node_id]

                if node_id in self._connected_incoming_nodes:
                    logger.debug("Disconnect node %s", node_id)
                    self._connected_incoming_nodes.discard(node_id)
                    self.node_incoming_disconnected.emit(node_id)
                    self.connected_nodes_incoming_changed.emit(
                        self._connected_incoming_nodes)

            if disconnect_outgoing:
                connections = self._outgoing_node_connections[node_id]
                for connection_id in connections:
                    logger.debug("Disconnect connection %s", connection_id)
                    self._webrtc.disconnect(connection_id)
                    self._outgoing_connections.pop(connection_id, None)
                del self._outgoing_node_connections[node_id]

                self._relayed_nodes.discard(node_id)

                if node_id in self._connected_outgoing_nodes:
                    logger.debug("Disconnect node %s", node_id)
                    self._connected_outgoing_nodes.discard(node_id)
                    self.node_outgoing_disconnected.emit(node_id)
                    self.connected_nodes_outgoing_changed.emit(
                        self._connected_outgoing_nodes)

    def _on_disconnect_from_all_nodes(self):
        with self._mark_time():
            logger.info("Connection with signal server lost, "
                        "disconnect from all nodes")
            for connection_id in self._incoming_connections.keys():
                self._webrtc.disconnect(connection_id)
            self._incoming_connections.clear()
            self._incoming_node_connections.clear()

            for connection_id in self._outgoing_connections.keys():
                self._webrtc.disconnect(connection_id)
            self._outgoing_connections.clear()
            self._outgoing_node_connections.clear()

            self._relayed_nodes.clear()

            for node_id in self._connected_incoming_nodes:
                self.node_incoming_disconnected.emit(node_id)
            self._connected_incoming_nodes.clear()
            self.connected_nodes_incoming_changed.emit(
                self._connected_incoming_nodes)

            for node_id in self._connected_outgoing_nodes:
                self.node_outgoing_disconnected.emit(node_id)
            self._connected_outgoing_nodes.clear()
            self.connected_nodes_outgoing_changed.emit(
                self._connected_outgoing_nodes)

    def _on_quit(self):
        self._on_disconnect_from_all_nodes()

        if self._refresh_connections_timer.isActive():
            self._refresh_connections_timer.stop()
            self._refresh_connections_timer = None

        self.disconnect(self)
        self._webrtc.close()
        self._webrtc = None

        self.exited.emit()

    def _on_local_description(self, connection_id, type, sdp):
        logger.debug("on_local_description")
        with self._mark_time():
            connection = self._incoming_connections.get(
                connection_id, None)
            if not connection:
                connection = self._outgoing_connections.get(
                    connection_id, None)
            if not connection:
                logger.warning("on_local_description: connection %s not found",
                               connection_id)
                self._webrtc.disconnect(connection_id)
                return

            self._ss_client.send_sdp_message(
                connection.node_id, connection_id.decode(), json.dumps(dict(
                    type=type.decode(),
                    sdp=sdp.decode(),
                )))

    def _on_candidate(self, connection_id, sdp_mid, sdp_m_line_index,
                      candidate):
        with self._mark_time():
            connection = self._incoming_connections.get(
                connection_id, None)
            if not connection:
                connection = self._outgoing_connections.get(
                    connection_id, None)
            if not connection:
                self._webrtc.disconnect(connection_id)
                return

            self._ss_client.send_sdp_message(
                connection.node_id, connection_id.decode(), json.dumps(dict(
                    sdpMid=sdp_mid.decode(),
                    sdpMLineIndex=sdp_m_line_index,
                    candidate=candidate.decode(),
                )))

    def _sdp_message_from_peer(self, node_id, connection_id, message):
        logger.verbose("sdp_message_from_peer: %s", message)
        connection_id = connection_id.encode()
        try:
            sdp_message = json.loads(message)
        except JSONDecodeError as e:
            logger.warning("Failed to decode json: %s", e)
            return

        with self._mark_time():
            if not self._check_add_connection(node_id, connection_id):
                return

            if "type" not in sdp_message:
                sdp_mid_found = "sdpMid" in sdp_message
                sdp_m_line_index_found = "sdpMLineIndex" in sdp_message
                candidate_found = "candidate" in sdp_message
                if sdp_mid_found and sdp_m_line_index_found and \
                        candidate_found:
                    sdp_mid = sdp_message.get("sdpMid", "")
                    sdp_m_line_index = int(sdp_message.get("sdpMLineIndex", 0))
                    candidate = sdp_message.get("candidate", "")
                    self._webrtc.set_candidate(
                        connection_id, sdp_mid.encode(), sdp_m_line_index,
                        candidate.encode())
                else:
                    logger.warning("Invalid spd message, spd_mid_found: %s, "
                                   "sdp_m_line_index_found: %s, "
                                   "candidate_found: %s",
                                   sdp_mid_found, sdp_m_line_index_found,
                                   candidate_found)
                return

            type = sdp_message.get("type", "")
            sdp = sdp_message.get("sdp", "")

            logger.debug("On sdp message: type: %s, sdp: %s", type, sdp)
            self._webrtc.set_remote_description(
                connection_id, type.encode(), sdp.encode())
            logger.debug("On sdp message: done")

    def _check_add_connection(self, node_id, connection_id):
        incoming_connection_ids = self._incoming_node_connections[node_id]
        outgoing_connection_ids = self._outgoing_node_connections[node_id]
        if connection_id not in incoming_connection_ids and \
                connection_id not in outgoing_connection_ids:
            if self._is_connections_limit_reached(
                    incoming_connection_ids, self.HARD_CONNECTIONS_LIMIT):
                logger.debug("Incomming connections limit reached for node %s",
                             node_id)
                return False

            connection = Connection(node_id, connection_id)
            self._incoming_connections[connection_id] = connection
            incoming_connection_ids.add(connection_id)
            self._webrtc.create_connection(connection_id)
            logger.debug("On sdp message: connection added %s", connection_id)
            QTimer.singleShot(self.CONNECT_TIMEOUT,
                              lambda: self._check_connected.emit(
                                  connection_id, True))
        return True

    def get_node_type(self, node_id):
        node_type = None
        try:
            node_info = self._ss_client.get_node_info(node_id)
            node_type = node_info.get("type", None)
            if not node_type or type(node_type) not in (str, str):
                raise NameError("Invalid node type")

        except Exception as e:
            logger.warning("Can't get node type for node %s. Reason: %s",
                           node_id, e)
        return node_type

    def get_self_node_type(self):
        return self._ss_client.get_self_client_type()

    def get_sharing_info(self):
        return self._ss_client.get_sharing_info()

    def is_alive(self):
        return not (
                self._start_method_time > self._end_method_time and
                time() - self._start_method_time > CONNECTIVITY_ALIVE_TIMEOUT)
