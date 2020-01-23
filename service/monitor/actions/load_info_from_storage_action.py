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


class LoadInfoFromStorageAction(ActionBase):
    def __init__(self, storage):
        super(LoadInfoFromStorageAction, self).__init__()
        self._storage = storage

    def _on_new_event(self, fs_event):
        fs_event.file = self._storage.get_known_file(fs_event.src)
        fs_event.in_storage = fs_event.file is not None

        if fs_event.in_storage:
            self._load_info_from_storage(fs_event)

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
