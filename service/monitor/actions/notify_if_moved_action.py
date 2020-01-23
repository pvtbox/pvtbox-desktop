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
from service.monitor.actions.action_base import ActionBase
from common.signal import Signal
from common.constants import MOVE


class NotifyIfMovedAction(ActionBase):
    def __init__(self, path_converter):
        super(NotifyIfMovedAction, self).__init__()
        self.file_moved = Signal(str, str)  # old, new file path

        self._patch_converter = path_converter

    def add_new_event(self, fs_event):
        if fs_event.event_type == MOVE:
            self.file_moved.emit(
                self._patch_converter.create_relpath(fs_event.src),
                self._patch_converter.create_relpath(fs_event.dst)
            )
