# -*- coding: utf-8 -*-#

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
import os.path as op
from watchdog.events import PatternMatchingEventHandler, \
    FileCreatedEvent, FileModifiedEvent, FileMovedEvent, FileDeletedEvent, \
    DirCreatedEvent, DirMovedEvent, DirDeletedEvent

from common.constants import MODIFY, CREATE, DELETE, MOVE
from common.signal import Signal
from common.file_path import FilePath

from .fs_event import FsEvent

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class WatchdogHandler(PatternMatchingEventHandler):
    """ Getting events from watchdog.
    """

    events_map = {
        FileCreatedEvent: CREATE,
        DirCreatedEvent: CREATE,
        FileModifiedEvent: MODIFY,
        FileMovedEvent: MOVE,
        DirMovedEvent: MOVE,
        FileDeletedEvent: DELETE,
        DirDeletedEvent: DELETE,
    }

    def __init__(self,
                 root,
                 hidden_dirs,
                 hidden_files,
                 patterns=None,
                 is_special=False):
        self._hidden_files = hidden_files
        self._hidden_dirs = hidden_dirs
        self._is_special = is_special

        ignore_patterns = []
        for hidden_file in hidden_files:
            ignore_patterns.append('*' + hidden_file)
        for hidden_dir in hidden_dirs:
            ignore_patterns.append(
                FilePath(op.join(root, hidden_dir)).longpath)
            ignore_patterns.append(
                FilePath(op.join(root, hidden_dir, '*')).longpath)
        super(WatchdogHandler, self).__init__(ignore_patterns=ignore_patterns,
                                              patterns=patterns)

        self.event_is_arrived = Signal(FsEvent, bool)

    @property
    def hidden_files(self):
        """
        List of files not to generate events for [iterable]
        Paths are relative to root directory

        @return [unicode, ]
        """

        return self._hidden_files

    @property
    def hidden_dirs(self):
        """
        List of folders not to generate events for [iterable]
        Paths are relative to root directory

        @return [unicode, ]
        """

        return self._hidden_dirs

    def on_any_event(self, event):
        if event.__class__ not in WatchdogHandler.events_map:
            logger.debug('Ignoring event %s', event.__class__)
            return

        fs_event = FsEvent(
            event_type=WatchdogHandler.events_map[event.__class__],
            src=event.src_path,
            dst=event.dest_path if hasattr(event, 'dest_path') else None,
            is_dir=event.is_directory)
        logger.debug("New event from watchdog: %s %s",
                     event.__class__,
                     fs_event)
        self.event_is_arrived(fs_event, self._is_special)
