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
from common.constants import CREATE, MODIFY
from service.monitor.actions.action_base import ActionBase


class DeleteFileCopyReferenceAction(ActionBase):
    def __init__(self, copies_storage):
        super(DeleteFileCopyReferenceAction, self).__init__()
        self._copies_storage = copies_storage

    def _on_new_event(self, fs_event):
        self._copies_storage.remove_copy_reference(
            fs_event.new_hash,
            reason="DeleteFileCopyReferenceAction {}".format(fs_event.src))

        self.event_passed(fs_event)

    def _is_sutable(self, fs_event):
        return (
            not fs_event.is_dir
            and fs_event.event_type in (CREATE, MODIFY)
        )
