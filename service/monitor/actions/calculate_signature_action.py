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
from common.constants import CREATE, MODIFY
from common.file_path import FilePath

class CalculateSignatureAction(ActionBase):
    def __init__(self, rsync):
        super(CalculateSignatureAction, self).__init__()
        self._rsync = rsync

    def _on_new_event(self, fs_event):
        if fs_event.is_link:
            fs_event.new_signature = fs_event.old_signature
        else:
            try:
                fs_event.new_signature = self._rsync.block_checksum(
                    FilePath(fs_event.file_recent_copy).longpath)
            except (IOError, OSError):
                return self.event_returned(fs_event)

        self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return (
            not fs_event.is_dir
            and fs_event.event_type in (CREATE, MODIFY, )
            and fs_event.file_size
        )
