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
import json

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def create_msg(operation, data=None, node_id=None):
    '''
    Formats message to be sent to peer over WS protocol and encodes it to JSON

    @param operation Operation name [string]
    @param data Operation data (optional)
    @param node_id Other node ID (optional) [string]
    @return JSON-encoded message object [string]
    '''

    msg = {
        'operation': operation}
    if data is not None:
        msg['data'] = data
    if node_id is not None:
        msg['node_id'] = node_id

    return json.dumps(msg)


def parse_msg(encoded):
    '''
    Decodes message from JSON format and extract operation and data fields

    @param encoded JSON encoded message [string]
    @return Parsed message data in the form
            (operation, node_id, data) [tuple]
    @raise ValueError
    @raise KeyError
    '''

    # Decode message from json format
    try:
        msg_decoded = json.loads(encoded)
    except ValueError as e:
        logger.error("Failed to decode message: '%s' (%s)", encoded, e)
        raise

    # Unpack message
    try:
        operation = msg_decoded['operation']
        node_id = msg_decoded.get('node_id', None)
        data = msg_decoded.get('data', None)
    except KeyError as e:
        logger.error("Wrong format of message: '%s' (%s)", encoded, e)
        raise

    return operation, node_id, data
