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
from os.path import join

import errno

from common.constants import CREATE, MODIFY
from common.file_path import FilePath
from service.monitor.actions.action_base import ActionBase
from common.utils import get_copies_dir, get_free_space_by_filepath, \
    get_signature_file_size, copy_file
from common.signal import Signal


class MakeFileRecentCopyAction(ActionBase):
    def __init__(self, root):
        super(MakeFileRecentCopyAction, self).__init__()
        self._root = root
        self.no_disk_space = Signal(object, str, bool)

    def _on_new_event(self, fs_event):
        if fs_event.file_size + get_signature_file_size(fs_event.file_size) > \
                get_free_space_by_filepath(fs_event.src):
            self.no_disk_space.emit(fs_event, fs_event.src, False)
            self.event_suppressed(fs_event)
            return

        file_recent_copy_name = FilePath(
            join(get_copies_dir(self._root),
                 'recent_copy_' + str(fs_event.id)))
        fs_event.file_recent_copy = file_recent_copy_name
        recent_copy_longpath = FilePath(file_recent_copy_name).longpath
        try:
            copy_file(FilePath(fs_event.src).longpath,
                      recent_copy_longpath)
        except (OSError, IOError) as e:
            if e.errno == errno.ENOSPC:
                self.no_disk_space.emit(fs_event, fs_event.src, True)
                self.event_suppressed(fs_event)
                return

            self.event_returned(fs_event)
            return

        recent_copy_size = stat(recent_copy_longpath).st_size
        if recent_copy_size != fs_event.file_size:
            self.event_returned(fs_event)
            return

        self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return (
            not fs_event.is_dir
            and fs_event.event_type in (CREATE, MODIFY)
            and fs_event.file_size
        )
