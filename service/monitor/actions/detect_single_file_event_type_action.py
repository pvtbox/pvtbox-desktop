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
import os.path as op

from common.constants import CREATE, MODIFY, DELETE, MOVE, FILE_LINK_SUFFIX
from service.monitor.actions.action_base import ActionBase
from common.file_path import FilePath


class DetectSingleFileEventTypeAction(ActionBase):
    def __init__(self):
        super(DetectSingleFileEventTypeAction, self).__init__()

    def _on_new_event(self, fs_event):
        self._process(fs_event)

    def _is_sutable(self, fs_event):
        return fs_event.event_type not in (MOVE,)

    def _process(self, fs_event):
        file_exists_on_fs = self._check_file_exists_on_fs(fs_event.src)
        file_exists_in_storage = fs_event.in_storage
        if file_exists_on_fs:
            if file_exists_in_storage:
                fs_event.event_type = MODIFY
            else:
                fs_event.event_type = CREATE
        else:
            if file_exists_in_storage and \
                    not self._check_pair_file_exists_on_fs(fs_event):
                fs_event.event_type = DELETE
            else:
                return self.event_suppressed(fs_event)
        self.event_passed(fs_event)

    def _check_file_exists_on_fs(self, file):
        return op.exists(FilePath(file).longpath)

    def _check_pair_file_exists_on_fs(self, fs_event):
        if fs_event.is_link:
            path2 = fs_event.src[: -len(FILE_LINK_SUFFIX)]
        else:
            path2 = fs_event.src + FILE_LINK_SUFFIX
        return op.exists(FilePath(path2).longpath)
