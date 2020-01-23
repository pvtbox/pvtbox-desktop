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
import json
from PySide2.QtCore import Signal

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GuiProtocol(object):
    def __init__(self, receivers, verbose=False):
        self._receivers = set(receivers)
        self._verbose = verbose

    def parse_message(self, encoded):
        '''
        Parses JSON-encoded message received from websocket

        @param encoded JSON encoded message [string]
        @return Parsed message data in the form (action, data) [tuple]
        @raise ValueError
        @raise KeyError
        '''

        # Decode message from json format
        try:
            decoded = json.loads(encoded)
        except ValueError as e:
            logger.error("Failed to decode message: '%s' (%s)", encoded, e)
            raise

        # Unpack message
        try:
            action = decoded['action']
            data = decoded.get('data', None)
        except KeyError as e:
            logger.error("Wrong format of message: '%s' (%s)", encoded, e)
            raise

        if self._verbose:
            try:
                logger.verbose(
                    "Received action '%s' data '%s'", action, data)
            except AttributeError:
                pass

        return action, data

    def call(self, action, *args):
        '''
        Calls callable corresponding to action received

        @param action Action name [string]
        @param *args action arguments
        '''

        is_signal = False
        # Find callable corresponding to the command
        for reciever in self._receivers:
            func = getattr(reciever, action, None)
            if not func:
                continue

            is_signal = isinstance(func, Signal)
            if is_signal or callable(func):
                break
        else:
            logger.error("Unknown action %s", action)
            raise ValueError

        if is_signal:
            func.emit(*args)
        else:
            func(*args)

    def create_action(self, action, data=None):
        '''
        Creates protocol action using data specified

        @param action Action name [string]
        @param path Filesystem path [unicode] or None

        @return JSON encoded protocol command
        '''

        result = {'action': action}
        if data is not None:
            result['data'] = data

        return json.dumps(result)

    def add_receiver(self, receiver):
        self._receivers.add(receiver)
