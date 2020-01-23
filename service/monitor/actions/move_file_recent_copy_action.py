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
from os import stat
from os.path import join, exists

import shutil

from common.constants import CREATE, MODIFY
from common.file_path import FilePath
from service.monitor.actions.action_base import ActionBase
from common.utils import get_copies_dir
from common.signal import Signal


class MoveFileRecentCopyAction(ActionBase):
    def __init__(self, root, copies_storage):
        super(MoveFileRecentCopyAction, self).__init__()
        self._root = root
        self._copies_storage = copies_storage

        self.copy_added = Signal(str)

    def _on_new_event(self, fs_event):
        file_synced_copy_name = FilePath(
            join(get_copies_dir(self._root), fs_event.new_hash)).longpath
        file_recent_copy_name = FilePath(fs_event.file_recent_copy).longpath

        self._copies_storage.add_copy_reference(
            fs_event.new_hash,
            reason="MoveFileRecentCopyAction {}".format(fs_event.src))

        if exists(file_recent_copy_name):
            if not exists(file_synced_copy_name):
                try:
                    shutil.move(file_recent_copy_name,
                                file_synced_copy_name)
                    self.copy_added.emit(fs_event.new_hash)
                except (OSError, IOError):
                    self._copies_storage.remove_copy_reference(
                        fs_event.new_hash,
                        reason="MoveFileRecentCopyAction {}"
                            .format(fs_event.src))
                    self.event_returned(fs_event)
                    return
                if stat(file_synced_copy_name).st_size != fs_event.file_size:
                    self.event_returned(fs_event)
                    return
            fs_event.file_synced_copy = FilePath(file_synced_copy_name)

            self.event_passed(fs_event)
        else:
            self.event_returned(fs_event)

    def _is_sutable(self, fs_event):
        return (
            not fs_event.is_dir
            and fs_event.event_type in (CREATE, MODIFY)
            and fs_event.file_recent_copy
        )
