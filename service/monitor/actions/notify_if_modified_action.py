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
from common.constants import MODIFY


class NotifyIfModifiedAction(ActionBase):
    def __init__(self, path_converter):
        super(NotifyIfModifiedAction, self).__init__()
        self.file_modified = Signal(str,    # relative_path
                                    float)      # modified time

        self._patch_converter = path_converter

    def add_new_event(self, fs_event):
        if fs_event.event_type == MODIFY:
            self.file_modified.emit(
                self._patch_converter.create_relpath(fs_event.src),
                fs_event.mtime)
