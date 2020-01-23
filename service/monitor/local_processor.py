# coding=utf-8
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
import time

from common.constants import MOVE, FILE, DIRECTORY, event_names
from common.signal import Signal


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class LocalProcessor(object):

    def __init__(self, root, storage, path_converter, tracker):
        self._root = root
        self._path_converter = path_converter
        self._tracker = tracker
        self._storage = storage
        self.event_is_arrived = Signal(dict, object)

    def process(
            self,
            fs_event):
        """ crates message about event """

        template_message = dict(
            event=fs_event.event_type,
            time=time.time(),
            type=DIRECTORY if fs_event.is_dir else FILE
        )

        if fs_event.event_type == MOVE:
            template_message['src'] = self._path_converter.create_relpath(
                fs_event.src)
            template_message['dst'] = self._path_converter.create_relpath(
                fs_event.dst)
            template_message['hash'] = fs_event.old_hash
        else:
            template_message['path'] = self._path_converter.create_relpath(
                fs_event.src)
            template_message['hash'] = fs_event.new_hash
            template_message['old_hash'] = fs_event.old_hash
        template_message['file_size'] = fs_event.file_size

        self.event_is_arrived.check_connected()
        self.event_is_arrived(template_message, fs_event)
        if fs_event.event_type != MOVE:
            path = template_message['path']
        else:
            path = template_message['src']

        logger.info(
            "Event type %s for path '%s'",
            event_names[template_message['event']],
            path)
