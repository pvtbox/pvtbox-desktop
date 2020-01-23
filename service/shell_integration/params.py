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

from common.utils import get_ipc_address

IPC_ADDRESS = get_ipc_address()

# Timeout for nanomsg socket operations (in milliseconds)
TIMEOUT = 1000

# Client_API class instance
web_api = None

# Sync class instance
sync = None

# Tracker class instance
tracker = None

# Signalling server client instance
ss_client = None

# Instance of includes.config.ConfigLoader
cfg = None

# Instance of shell_integration.websocket_server.IPCWebSocketServer
ipc_ws_server = None

get_shared_paths_func = None
