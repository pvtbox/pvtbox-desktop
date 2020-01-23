#!/usr/bin/env python
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
import sys
import json
import os.path as op

from nanomsg import Socket, PAIR

from common.utils import get_ipc_address

IPC_ADDRESS = get_ipc_address()

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def connect():
    sock = Socket(PAIR)
    try:
        sock.connect(IPC_ADDRESS)
        sock.send_timeout = 10000
        sock.recv_timeout = 10000
    except Exception as e:
        logger.error(
            "Failed to connect to address '%s' (%s)", IPC_ADDRESS, e)
        sock.close()
        raise SystemExit(1)
    return sock


def send_message(sock, command, message):
    try:
        logger.debug("Sending: '%s'", message)
        sock.send(str(message).encode('utf-8'))
    except Exception as e:
        logger.error(
            "Failed to send message '%s' to address '%s' (%s)",
            message, IPC_ADDRESS, e)
        raise SystemExit(1)

    try:
        logger.debug("Obtaining reply for '%s' command ...", command)
        reply = sock.recv()
    except Exception as e:
        logger.error(
            "Failed to obtain data from address '%s' (%s)", IPC_ADDRESS, e)
        raise SystemExit(1)
    else:
        logger.info("Obtained '%s' for '%s' command", reply, command)


def send_download_link(link):
    try:
        message = json.dumps(dict(cmd='download_link', link=link))
    except Exception as e:
        logger.error(
            "Failed to serialize message '%s' to JSON (%s)", message, e)
        return
    sock = connect()
    send_message(sock, 'download_link', message)
    sock.close()


def send_copy_to_sync_dir(paths):
    try:
        message = json.dumps(dict(cmd='copy_to_sync_dir', paths=paths))
    except Exception as e:
        logger.error(
            "Failed to serialize message '%s' to JSON (%s)", message, e)
        return
    sock = connect()
    send_message(sock, 'copy_to_sync_dir', message)
    sock.close()

def send_wipe_internal():
    try:
        message = json.dumps(dict(cmd='wipe_internal'))
    except Exception as e:
        logger.error(
            "Failed to serialize message '%s' to JSON (%s)", message, e)
        return
    sock = connect()
    send_message(sock, 'wipe_internal', message)
    sock.close()

def send_show_command():
    try:
        message = json.dumps(dict(cmd='show'))
    except Exception as e:
        logger.error(
            "Failed to serialize message '%s' to JSON (%s)", message, e)
        return
    sock = connect()
    send_message(sock, 'show', message)
    sock.close()


if __name__ == "__main__":

    # Setup logging level
    logging.basicConfig(
        level='DEBUG',
        format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    try:
        # First argument is command, second is path
        command, arg = sys.argv[1:3]
    except ValueError:
        logger.error("Not enough arguments specified")
        raise SystemExit(1)

    # Passing share download link
    if command == 'download_link':
        share_link = arg
    # It is path-related command
    else:
        # Convert path to absolute
        path = op.abspath(arg)

    # Serialize message
    try:
        if command == 'download_link':
            message = json.dumps(dict(cmd=command, link=share_link))
        else:
            message = json.dumps(dict(cmd=command, path=path))
    except Exception as e:
        logger.error(
            "Failed to serialize message '%s' to JSON (%s)", message, e)
        raise SystemExit(1)

    sock = connect()

    # TODO: implement checking whether path is in sharing folder or not

    # Send message
    send_message(sock, command, message)

    sock.close()
