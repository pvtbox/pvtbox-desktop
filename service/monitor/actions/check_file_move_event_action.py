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

from common.constants import MOVE, MODIFY, CREATE, FILE_LINK_SUFFIX
from service.monitor.actions.action_base import ActionBase
from service.monitor.fs_event import FsEvent
from common.file_path import FilePath


class CheckFileMoveEventAction(ActionBase):
    def __init__(self, storage):
        super(CheckFileMoveEventAction, self).__init__()
        self._storage = storage

    def _on_new_event(self, fs_event):
        self._process(fs_event)

    def _is_sutable(self, fs_event):
        return fs_event.event_type in (MOVE, )

    def _process(self, fs_event):
        src_path = fs_event.src[: -len(FILE_LINK_SUFFIX)] if fs_event.is_link \
            else fs_event.src
        dst_path = fs_event.dst[: -len(FILE_LINK_SUFFIX)] if fs_event.is_link \
            else fs_event.dst
        if not self._check_file_exists_on_fs(fs_event.src) \
                and self._check_file_exists_in_storage(src_path) \
                and self._check_file_exists_on_fs(fs_event.dst) \
                and not self._check_file_exists_in_storage(dst_path):
            self.event_passed(fs_event)
            if not fs_event.is_dir:
                self.event_spawned(FsEvent(
                    event_type=MODIFY,
                    src=fs_event.dst,
                    is_dir=fs_event.is_dir))
            return

        fs_event.event_type = CREATE if fs_event.is_dir else MODIFY
        self.event_spawned(FsEvent(
            event_type=fs_event.event_type,
            src=fs_event.dst,
            is_dir=fs_event.is_dir))
        fs_event.dst = None
        self.event_returned(fs_event)

    def _check_file_exists_on_fs(self, file):
        return op.exists(FilePath(file).longpath)

    def _check_file_exists_in_storage(self, file):
        f = self._storage.get_known_file(file)
        return f is not None
