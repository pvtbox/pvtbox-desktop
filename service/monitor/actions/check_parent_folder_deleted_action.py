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
from common.constants import DELETE
from service.monitor.fs_event import FsEvent


class CheckParentFolderDeletedAction(ActionBase):
    def __init__(self, root):
        super(CheckParentFolderDeletedAction, self).__init__()
        self._root = root

    def _on_new_event(self, fs_event):
        dirname = FilePath(op.dirname(fs_event.src))

        if dirname != self._root and not op.exists(dirname):
            self.event_spawned(FsEvent(
                DELETE, dirname, True,
                is_offline=fs_event.is_offline, quiet=fs_event.quiet,
                event_time=fs_event.time))
            self.event_suppressed(fs_event)
        else:
            self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return fs_event.event_type == DELETE
