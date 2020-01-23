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

from common.signal import Signal

from service.monitor.fs_event import FsEvent

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ActionBase(object):
    def __init__(self):
        super(ActionBase, self).__init__()
        self._active = True
        self.event_passed = Signal(FsEvent)
        self.event_returned = Signal(FsEvent)
        self.event_spawned = Signal(FsEvent)
        self.event_suppressed = Signal(FsEvent)
        self.event_is_processing = Signal(FsEvent)

        self.event_passed.connect(self._on_event_passed)
        self.event_returned.connect(self._on_event_returned)
        self.event_suppressed.connect(self._on_event_suppressed)
        self.event_spawned.connect(self._on_event_spawned)

    def set_active(self, active=True):
        self._active = active

    def _on_event_passed(self, fs_event):
        pass

    def _on_event_returned(self, fs_event):
        logger.debug(
            '%s RETURNED the event %s for additional processing',
            self.__class__.__name__,
            fs_event)

    def _on_event_spawned(self, fs_event):
        logger.debug(
            '%s SPAWNED new event %s for processing',
            self.__class__.__name__,
            fs_event)

    def _on_event_suppressed(self, fs_event):
        logger.debug(
            '%s SUPRESSED the event %s',
            self.__class__.__name__,
            fs_event)

    def add_new_event(self, fs_event):
        if self._is_sutable(fs_event):
            logger.debug(
                '%s received the event %s',
                self.__class__.__name__,
                fs_event)
            self.event_is_processing(fs_event)
            self._on_new_event(fs_event)
        else:
            self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return self._active

    def _on_new_event(self, fs_event):
        self.event_passed(fs_event)
