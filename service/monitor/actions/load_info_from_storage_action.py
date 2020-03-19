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
import pickle

from service.monitor.actions.action_base import ActionBase
from common.signal import Signal
from common.file_path import FilePath
from common.constants import FILE_LINK_SUFFIX, MODIFY, CREATE
from common.utils import remove_file, set_ext_invisible

class LoadInfoFromStorageAction(ActionBase):
    def __init__(self, storage):
        super(LoadInfoFromStorageAction, self).__init__()
        self._storage = storage
        self.rename_file = Signal(FilePath)

    def _on_new_event(self, fs_event):
        if fs_event.src.endswith(FILE_LINK_SUFFIX) and not fs_event.is_dir:
            fs_event.is_link = True
            path = fs_event.src[: -len(FILE_LINK_SUFFIX)]
        else:
            fs_event.is_link = False
            path = fs_event.src

        fs_event.file = self._storage.get_known_file(path)
        fs_event.in_storage = fs_event.file is not None

        suppress_event = False
        if fs_event.in_storage:
            if not fs_event.is_link and self._check_file_exists_on_fs(
                    fs_event.src + FILE_LINK_SUFFIX) and \
                    fs_event.event_type == CREATE:
                self.rename_file.emit(fs_event.src)
                return self.event_suppressed(fs_event)

            elif fs_event.is_link and fs_event.event_type == MODIFY:
                if not self._get_events_file_id_from_link(fs_event):
                    suppress_event = True

            self._load_info_from_storage(fs_event)
        else:
            # possibly copy of file link
            if fs_event.is_link:
                suppress_event = not self._get_copy_info_from_storage(fs_event)
                if not suppress_event:
                    set_ext_invisible(fs_event.src)

        if suppress_event:
            try:
                remove_file(fs_event.src)
            except Exception:
                pass
            return self.event_suppressed(fs_event)

        if fs_event.is_link:
            fs_event.file_size = fs_event.old_size

        self.event_passed(fs_event)

    def _load_info_from_storage(self, fs_event):
        fs_event.is_dir = fs_event.file.is_folder
        if not fs_event.is_dir:
            self._load_file_info_from_storage(fs_event)

    def _load_file_info_from_storage(self, fs_event):
        fs_event.old_hash = fs_event.file.file_hash
        fs_event.old_mtime = fs_event.file.mtime
        fs_event.old_size = fs_event.file.size
        fs_event.old_signature = self._storage.get_file_signature(
            fs_event.file)

    def _check_file_exists_on_fs(self, file):
        return op.exists(FilePath(file).longpath)

    def _get_events_file_id_from_link(self, fs_event):
        try:
            with open(fs_event.src, 'rb') as f:
                file_id = pickle.load(f)
                return int(file_id)
        except Exception:
            return None

    def _get_copy_info_from_storage(self, fs_event):
        file_id = self._get_events_file_id_from_link(fs_event)
        if not file_id:
            return False

        fs_event.file = self._storage.get_known_file_by_id(file_id)
        if not fs_event.file:
            return False

        self._load_info_from_storage(fs_event)
        fs_event.new_hash = fs_event.old_hash
        fs_event.file_size = fs_event.old_size
        fs_event.new_signature = fs_event.old_signature
        fs_event.file = None
        return True
