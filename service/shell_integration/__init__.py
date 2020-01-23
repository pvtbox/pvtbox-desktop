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
from common.utils import get_platform
from service.shell_integration import params
from .actions import connect_slots
from .ipc_server import rx_thread_worker, close_ipc
from .signals import signals
from .copy_path import copy_to_sync_worker
from .websocket_server import IPCWebSocketServer


def init(web_api, ss_client, sync, file_status_manager, cfg,
         tracker=None, ipc_address=None,
         get_shared_paths=None):
    '''
    Initializes shell integration module

    @param web_api Client_API class instance [Client_API]
    @param ss_client Instance of signalling.SignalServerClient
    @param sync Instance of Sync class
    @param cfg Instance of includes.config.ConfigLoader
    @param tracker Instance of stat_tracking.Tracker
    @param ipc_address Name of unix domain socket/named pipe to be used for
        communication with OS shell [string]
    @param logged_in_signal [Signal]
    '''

    params.web_api = web_api
    params.ss_client = ss_client
    params.sync = sync
    params.cfg = cfg
    params.tracker = tracker
    params.get_shared_paths_func = get_shared_paths

    params.ipc_ws_server = IPCWebSocketServer()
    params.ipc_ws_server.start()

    cfg.settings_changed.connect(
        params.ipc_ws_server.on_settings_changed)
    ss_client.sharing_changed.connect(
        lambda _: params.ipc_ws_server.on_share_changed())
    file_status_manager.files_status.connect(
        params.ipc_ws_server.on_files_status)
    file_status_manager.clear_path.connect(
        params.ipc_ws_server.on_clear_path)

    # Save address if necessary
    if ipc_address:
        params.IPC_ADDRESS = ipc_address

    # Connect signals
    connect_slots()

    # Start receiving thread
    rx_thread_worker()
    # Start file copying thread
    copy_to_sync_worker()


def close():
    if get_platform() == 'Darwin' and params.ipc_ws_server:
        params.ipc_ws_server.close()
    close_ipc()


__all__ = [init, signals]
