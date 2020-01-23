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

from common.file_path import FilePath
from service.monitor.actions.action_base import ActionBase
from common.constants import MOVE, DELETE, CREATE
from service.monitor.fs_event import FsEvent


class CheckParentFolderCreatedAction(ActionBase):
    def __init__(self, root, storage):
        super(CheckParentFolderCreatedAction, self).__init__()
        self._root = root
        self._storage = storage

    def _on_new_event(self, fs_event):
        if fs_event.event_type == MOVE:
            dirname = op.dirname(fs_event.dst)
        else:
            dirname = op.dirname(fs_event.src)

        dirname = FilePath(dirname)

        if dirname != self._root:
            parent = self._storage.get_known_file(dirname, True)

            if parent is None:
                self.event_spawned(FsEvent(
                    CREATE, dirname, True, event_time=fs_event.time))
                return self.event_returned(fs_event)

        self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return fs_event.event_type != DELETE
