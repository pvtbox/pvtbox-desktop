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
from nanomsg import Socket, PAIR
from common.async_utils import run_daemon
from common.utils import remove_socket_file
from service.shell_integration import params as params
from service.shell_integration.protocol import parse_message, emit_signal, \
    get_sync_dir_reply, create_command, get_is_sharing_reply, \
    get_offline_status_reply


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# nanomsg socket
_socket = None

_exiting = False


def _init_socket():
    '''
    Initializes nanomsg socket
    '''

    global _socket

    logger.info("Initializing nanomsg socket for address '%s'...",
                params.IPC_ADDRESS)

    # Close socket if necessary
    try:
        if _socket:
            _socket.close()
    except Exception as e:
        logger.error("Failed to close nanomsg socket (%s)", e)
        return

    # Initialize
    try:
        _socket = Socket(PAIR)
        _socket.bind(params.IPC_ADDRESS)
        _socket.send_timeout = params.TIMEOUT
    except Exception as e:
        logger.error("Failed to init nanomsg socket for address '%s' (%s)",
                     params.IPC_ADDRESS, e)
        return


def send_msg(msg):
    '''
    Sends message to the client connected to nanomsg socket

    @param msg Message to be sent [string]
    @return Operation success flag [bool]
    '''

    global _socket

    try:
        logger.debug("Sending '%s'...", msg)
        _socket.send(msg.encode())
        return True
    except Exception as e:
        logger.error("Failed to send '%s' (%s)", msg, e)
        return False


@run_daemon
def rx_thread_worker():
    '''
    Receiving thread worker function
    '''

    global _socket

    logger.debug("Starting nanomsg receiving thread...")

    # Initialize nanomsg server socket
    _init_socket()

    while True:
        try:
            # Block until message received
            message = _socket.recv()
        except Exception as e:
            if _exiting:
                break
            logger.error("Failed to receive data from nanomsg socket (%s)", e)
            continue

        logger.debug("Received data from nanomsg socket: '%s'", message)

        # Parse message received
        try:
            cmd, path, link, paths, context = parse_message(message)
        except Exception as e:
            continue

        if cmd == 'show':
            emit_signal(cmd)
            send_msg("")
            continue

        if cmd == 'wipe_internal':
            emit_signal(cmd)
            send_msg("")
            continue

        # Sync folder path requested
        if cmd == 'sync_dir':
            send_msg(get_sync_dir_reply())
            continue
        # Is path shared requested
        elif cmd == 'is_shared':
            send_msg(get_is_sharing_reply(paths if paths else [path]))
            continue
        elif cmd in ('offline_off', 'offline_on'):
            is_offline = cmd == 'offline_on'
            emit_signal('offline_paths', paths, is_offline)
            send_msg(create_command(cmd))
            continue
        elif cmd == 'offline_status':
            send_msg(get_offline_status_reply(paths if paths else [path]))
            continue

        # Process other commands
        try:
            if link:
                emit_signal(cmd, link)
            elif paths:
                emit_signal(cmd, paths)
            else:
                emit_signal(cmd, [path])
            # Confirm successful command processing
            send_msg(create_command(cmd))
        except Exception as e:
            logger.error("Failed to process command '%s' (%s)", cmd, e)
            continue


def close_ipc():
    global _exiting

    _exiting = True
    try:
        if _socket:
            logger.debug("Close nanomsg socket")
            _socket.close()
            remove_socket_file()
    except Exception as e:
        logger.error("Failed to close nanomsg socket (%s)", e)
