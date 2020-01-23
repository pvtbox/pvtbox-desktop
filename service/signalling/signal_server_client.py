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
from PySide2.QtCore import QObject, Qt, Signal

from service.signalling.signalling_protocol import create_msg

from service.signalling.server_proxy import ServerProxy
from service.signalling.signallling_storage import Storage


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SignalServerClient(QObject):
    """
    Incapsulates interaction with signal server
    """
    node_connect = Signal(dict)
    node_disconnect = Signal(int)
    server_connect = Signal()
    server_disconnect = Signal()
    nodes_changed = Signal(dict)
    node_list_obtained = Signal(dict)
    sharing_changed = Signal(dict)
    sharing_disable = Signal(dict)
    changed_files = Signal(list)
    file_events = Signal(list, str)
    get_connection_params = Signal()
    connection_params = Signal(dict)
    get_node_status = Signal()
    node_status = Signal(dict)
    upload_cancel = Signal(int)
    upload_add = Signal(dict)
    sdp_message = Signal(str, str, str)
    share_info = Signal(dict)
    license_type = Signal(str)
    remote_action = Signal(dict)
    patches_info = Signal(dict)
    min_stored_event = Signal(str)
    collaborated_folders = Signal(list)
    auth_failure = Signal()
    new_notifications_count = Signal(dict)

    def __init__(self, parent, client_type='node'):
        """
        Constructor

        @param client_type
            Type of client connection to the server (added to server URL) [str]
        """
        QObject.__init__(self, parent=parent)

        self._storage = Storage(parent=self)
        self._server_proxy = ServerProxy(
            parent=self,
            client_type=client_type,
            storage=self._storage,
            debug=False)

        self._connect_slots()
        self._client_type = client_type

    def _connect_slots(self):
        # Enable sending node status on server connect and other nodes connect
        self.server_connect.connect(
            self.update_node_status, Qt.QueuedConnection)
        self.node_connect.connect(
            lambda _: self.update_node_status(), Qt.QueuedConnection)
        self.node_status.connect(self.send_node_status, Qt.QueuedConnection)
        self.connection_params.connect(
            self._server_proxy.on_connection_params, Qt.QueuedConnection)

    def emit_signal(self, signal_name, *args):
        """
        Emit signal as a response to message obtained from signal server
        :param signal_name: name of signal
        :param args: args to pass to signal
        :return: None
        """
        signal = getattr(self, signal_name, None)
        if signal:
            signal.emit(*args)
        else:
            logger.warning("Invalid signal name '%s'", signal_name)

    def update_node_status(self):
        if not self._server_proxy.is_connected():
            return

        self.get_node_status.emit()

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

        self._server_proxy.ss_connect(
            server_addr, server_port, use_ssl,
            ssl_cert_verify, ssl_fingerprint,
            server_reconnect_interval, timeout)

    def reconnect(self):
        """
        Closes signalling server connection (if any).
        Starts signalling server connecting attempts
        """
        self._server_proxy.reconnect()

    def ss_disconnect(self):
        """
        Closes signalling server connection (if any).
        """

        self._server_proxy.ss_disconnect()
        self._storage.clear_sharing_info()

    def is_connected(self):
        """
        Returns flag indicating whether the connection to the signalling server
        is established or not

        @return Signalling server connection flag [bool]
        """

        return self._server_proxy.is_connected()

    def get_nodes(self, allowed_types=None, online_only=True):
        """
        Returns list of known node IDs

        @param allowed_types List of node types to return [iterable]
        @param online_only
            Enables returning IDs of nodes with 'is_online' flag set [bool]
        @return known node IDs [tuple]
        """

        return self._storage.get_known_node_ids(
            allowed_types, online_only=online_only)

    def get_node_info(self, node_id):
        """
        Returns node info fo node ID specified

        @param node_id ID of node [str]
        @return Node info [dict] or None
        """

        return self._storage.get_node_info(node_id)

    def get_self_client_type(self):
        return self._client_type

    def get_sharing_info(self):
        """
        Returns known shared files/folders info

        @return Shared files info in the form {share_hash: filename}
        """

        return self._storage.get_sharing_info()

    def send_sdp_message(self, node_id, conn_uuid, sdp_message):
        """
        Sends SDP message to another node via signalling server

        @param node_id  Other node ID [str]
        @param conn_uuid UUID of connection [str]
        @param sdp_message SDP message data [str]
        @return Operation successful flag [bool]
        """

        return self._server_proxy.send(create_msg(
            'sdp',
            node_id=node_id,
            data={
                'conn_uuid': conn_uuid,
                'message': sdp_message}))

    def notify_sharing_disable(self, uuid):
        """
        Sends info on sharing disabling to other nodes of the user.
        Removes sharing information stored locally

        @param uuid Unique shared file/folder ID [str]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        if not self._server_proxy.send(create_msg(
                'sharing_disable', data=dict(uuid=uuid))):
            return False
        # Remove info stored locally
        self._storage.sharing_disable(uuid)
        return True

    def send_upload_complete(self, upload_id):
        """
        Sends notification on successful upload completion

        @param upload_id ID of upload [str]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        status = self._server_proxy.send(create_msg(
            'upload_complete', data={'upload_id': upload_id}))
        if not status:
            logger.error(
                "Failed to notify signalling server on upload ID '%s' "
                "completion", upload_id)
        return status

    def send_upload_failed(self, upload_id):
        """
        Sends notification on upload completion fail

        @param upload_id ID of upload [str]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        status = self._server_proxy.send(create_msg(
            'upload_failed', data={'upload_id': upload_id}))
        if not status:
            logger.error(
                "Failed to notify signalling server on upload ID '%s' "
                "fail", upload_id)
        return status

    def send_node_status(self, node_status):
        """
        Sends node status update

        @param node_status, currently -
         disk_usage, download_speed, upload_speed [dict]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        status = self._server_proxy.send(create_msg(
            'node_status', data=node_status))
        if not status:
            logger.error(
                "Failed to send node status notification")
        return status

    def send_patches_info(self, patches_info):
        """
        Sends pathes info

        @param pathes_info [dict]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        status = self._server_proxy.send(create_msg(
            'patches_info', data=patches_info))
        if not status:
            logger.error(
                "Failed to send pathes info notification")
        return status

    def send_share_downloaded(self, share_hash):
        """
        Sends notification 'share_downloaded'

        @param share_hash [str]
        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        status = self._server_proxy.send(create_msg(
            'share_downloaded', data={'share_hash': share_hash}))
        if not status:
            logger.error(
                "Failed to send notification 'share_downloaded'")
        return status

    def send_last_file_events_request(self, last_event_id,
                                      checked_event_id, events_count_check,
                                      node_without_backup=False):
        """
        Sends message 'last_file_events'

        @param last_event_id [int] Max server event id.
        @param checked_event_id [int] Max checked event id.
        @param events_count_check [int] Count of checked events.
        @param node_without_backup [bool] Is node in saving backups mode
        Server will send events with ids equal or greater than last_event_id
            if events_count_check is correct on server database.

        @return Operation successful flag [bool]
        """

        # Send message via signalling server
        data = {'last_event_id': str(last_event_id),
                'checked_event_id': str(checked_event_id),
                'events_count_check': str(events_count_check)}
        data['node_without_backup'] = "1" if node_without_backup else "0"
        status = self._server_proxy.send(create_msg(
            'last_file_events', data=data))
        if not status:
            logger.error(
                "Failed to send message 'last_file_events'")
        return status

    def send_traffic_info(self, info_list):
        traffic_info_msg = create_msg("traffic_info", data=info_list)

        status = self._server_proxy.send(traffic_info_msg)
        if not status:
            logger.error("Failed to send message 'traffic_info'")
        return status
