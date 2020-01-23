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
from PySide2.QtCore import QObject, Signal, Qt

from common.utils import license_type_constant_from_string
from common.constants import REGULAR_URI

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Signal server default address and port
SERVER = 'signalserver.pvtbox.net'
PORT = 8888


class transportSignals(QObject):
    '''
    Contains signals definition for the package
    '''

    # Signal to be emitted on signalling server connection
    signalling_connected = Signal()

    # Signal to be emitted on signalling server disconnect
    signalling_disconnected = Signal()

    # Signal to be emitted on known nodes info update
    # Arguments are: {node_id: node_type} [dict]
    known_nodes_changed = Signal(dict)

    # Signal to be emitted on license type update
    # Arguments are: new_license_type, old_license_type
    license_type_changed = Signal(int, int)

    # Signal to be emitted when node receives remote action message
    # Arguments are: {action_type: "x", action_uuid: "y"} [dict]
    remote_action = Signal(dict)

    # Signal to be emitted on node network download speed change
    # Arguments are: new speed value in bytes per sec [float]
    download_speed_changed = Signal(float)

    # Signal to be emitted on node network upload speed change
    # Arguments are: new speed value in bytes per sec [float]
    upload_speed_changed = Signal(float)

    # Signal to be emitted on node network download size change
    # Arguments are: new size value in bytes [float]
    download_size_changed = Signal(float)

    # Signal to be emitted on node network upload size change
    # Arguments are: new size value in bytes [float]
    upload_size_changed = Signal(float)

    # Signal to be emitted on share change
    # Arguments are: sharing_info[{uuid: info}]
    sharing_changed = Signal(list)

    # Signal to be emitted on authorization failure
    auth_failed = Signal()

    # Signal to be emitted on obtaining signal server address
    # Arguments are: address 'host:port'
    signalserver_address = Signal(str)

    # Signal to be emitted on new notifications count obtained
    # Arguments are: new notifications count [int]
    new_notifications_count = Signal(int)


signals = transportSignals()


def on_nodes_changed_cb(nodes_info):
    '''
    Callback to be called on node connect/disconnect when list of known node
    IDs changes

    @param nodes_info Nodes information in the form {node_id: node_info} [dict]
    '''

    logger.info("Nodes list: %s", len(nodes_info))
    signals.known_nodes_changed.emit(nodes_info)


def get_server_addr_port(login_data, connectivity_service):
    '''
    Extract signal server address and port from data returned by API server.
    If none, returns default server address and port

    @param login_data Data returned by API server [dict]
    '''

    # Signal server URL in the form HOST:PORT
    sign_url = None

    # Process servers info
    for server in login_data['servers']:
        # STUN/TURN server
        if server['server_type'] in ('STUN', 'TURN'):
            server_protocol = 'stun:' if server['server_type'] == 'STUN' \
                else 'turn:'
            server_login = server.get('server_login', '')
            server_password = server.get('server_password', '')
            connectivity_service.add_ice_server(
                server['server_id'],
                server_protocol + server['server_url'],
                server_login, server_password)
        # Signal server
        elif server['server_type'] == 'SIGN':
            sign_url = server['server_url']

    # Signal server URL found
    if sign_url:
        # Extract host/port
        try:
            ss_addr, ss_port = sign_url.split(':')[:2]
        except:
            logger.warning(
                "Failed to parse signal server address '%s', using defaults",
                sign_url)
            ss_addr, ss_port = SERVER, PORT
    # Signal server URL not found
    else:
        logger.warning("No signal server address given, using defaults")
        ss_addr, ss_port = SERVER, PORT

    return ss_addr, ss_port


def init(events_db, connectivity_service, upload_handler, ss_client, cfg,
         webshare_handler, logged_in_signal, get_sync_folder_size=None,
         get_upload_speed=None, get_download_speed=None, get_node_status=None,
         get_server_event_ids=None):
    """
    Module init function

    @param events_db File events DB [events_db.FileEventsDB]
    @param connectivity_service Instance of network.ConnectivityService
    @param ss_client Instance of signalling.SignalServerClient
    @param cfg Instance of includes.config.ConfigLoader
    @param logged_in_signal Node login on server signal
    @param get_sync_folder_size [callable]
    @param get_upload_speed [callable]
    @param get_download_speed [callable]
    @param get_node_status [callable]
    """

    def ss_connection_params_cb():
        """
        Callback function to obtain parameters to be passed to signalling
        server on connection

        @return Parameters in the form {name: value}
        """

        max_server_event_id, \
        max_checked_server_event_id, \
        events_count_from_checked_to_last = get_server_event_ids()
        # max_server_event_id = events_db.get_max_server_event_id()
        logger.info("Max known server event ID is %s", max_server_event_id)
        # max_checked_server_event_id = events_db.get_max_checked_server_event_id()
        logger.info("Max known checked server event ID is %s",
                    max_checked_server_event_id)
        # events_count_from_checked_to_last = events_db.get_events_count(
        #     max_checked_server_event_id, max_server_event_id)

        # min event ids for not-ready direct and reversed patches
        # to get patches info from API-server through signalling-server
        d_event_id, r_event_id = \
            events_db.get_min_event_ids_for_not_ready_pathes()
        logger.info("Min event IDs for not-ready patches (direct, reversed):"
                    " %s, %s", d_event_id, r_event_id)

        result = {
            'last_event_id': max_server_event_id,  # Max known server event ID
            'direct_patch_event_id': d_event_id,
            'reversed_patch_event_id': r_event_id,
            'checked_event_id': max_checked_server_event_id,
            'events_count_check': events_count_from_checked_to_last,
            'no_send_changed_files': 1,
            'max_events_total': cfg.remote_events_max_total,
            'max_events_per_request': cfg.max_remote_events_per_request,
        }
        if not cfg.download_backups:
            result['node_without_backup'] = 1

        result['user_hash'] = cfg.user_hash
        result['node_hash'] = cfg.node_hash

        return result

    def on_login_slot(login_data):
        '''
        Slot to handle register.Account.loggedIn signal

        @param login_data Data returned by API server [dict]
        '''

        webshare_handler.set_config(login_data)

        # Determine signalling server address/port to be used
        ss_addr, ss_port = get_server_addr_port(
            login_data, connectivity_service)
        signals.signalserver_address.emit('{}:{}'.format(ss_addr, ss_port))

        # Process license type change if any
        on_license_type_changed_cb(login_data['license_type'], force=True)

        # Start signalling server connecting attempts
        self_hosted = cfg.host != REGULAR_URI
        fingerprint = None if self_hosted else \
            "86025017022f6dcf9022d6fb867c3bb3bdc621103ddd8e9ed2c891a46d8dd856"
        ss_client.ss_connect(
            ss_addr, ss_port, use_ssl=True, ssl_cert_verify=True,
            ssl_fingerprint=fingerprint,
            timeout=20)

    def on_license_type_changed_cb(license_type, force=False):
        current_license_type = cfg.license_type
        # License type has been changed
        if force or license_type != current_license_type:
            cfg.set_settings({'license_type': license_type})
            signals.license_type_changed.emit(
                license_type, current_license_type)

    def on_remote_action_cb(remote_action_data):
        logger.debug("on_remote_action_cb: %s", remote_action_data)
        signals.remote_action.emit(remote_action_data)

    def on_new_notifications_count_cb(count_info):
        logger.debug("on_new_notifications_count_cb: %s", count_info)
        count = count_info.get('count', 0)
        signals.new_notifications_count.emit(count)

    def connect_slots():
        # Initialize signalling module
        ss_client.get_connection_params.connect(
            lambda: ss_client.connection_params.emit(
                ss_connection_params_cb()), Qt.QueuedConnection)
        ss_client.get_node_status.connect(
            lambda: ss_client.node_status.emit(
                dict(disk_usage=get_sync_folder_size(),
                     upload_speed=get_upload_speed(),
                     download_speed=get_download_speed(),
                     node_status=get_node_status())), Qt.QueuedConnection)
        ss_client.server_connect.connect(
            signals.signalling_connected, Qt.QueuedConnection)
        ss_client.server_connect.connect(
            upload_handler.on_signal_server_connect_cb, Qt.QueuedConnection)
        ss_client.server_disconnect.connect(
            signals.signalling_disconnected, Qt.QueuedConnection)
        ss_client.nodes_changed.connect(
            on_nodes_changed_cb, Qt.QueuedConnection)
        ss_client.node_list_obtained.connect(
            on_nodes_changed_cb, Qt.QueuedConnection)
        ss_client.upload_add.connect(
            upload_handler.on_upload_added_cb, Qt.QueuedConnection)
        ss_client.upload_cancel.connect(
            upload_handler.on_upload_cancel_cb, Qt.QueuedConnection)
        ss_client.remote_action.connect(
            on_remote_action_cb, Qt.QueuedConnection)
        ss_client.license_type.connect(
            lambda x: on_license_type_changed_cb(
                license_type_constant_from_string(x)), Qt.QueuedConnection)
        ss_client.sharing_changed.connect(
            signals.sharing_changed, Qt.QueuedConnection)
        ss_client.auth_failure.connect(
            signals.auth_failed, Qt.QueuedConnection)
        ss_client.new_notifications_count.connect(
            on_new_notifications_count_cb, Qt.QueuedConnection)

    connect_slots()
    # Register slot for 'loggedIn' signal
    logged_in_signal.connect(on_login_slot)
