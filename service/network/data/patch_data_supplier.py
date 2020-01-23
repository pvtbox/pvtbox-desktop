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
from os.path import exists
import time
import logging

from service.network.data.data_supplier import DataSupplier
from service.network.utils import get_patch_files_info

from service.network.browser_sharing import Message, ProtoError


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class PatchDataSupplier(DataSupplier):
    def __init__(self, parent, connectivity_service,
                 patches_storage, events_db=None):
        DataSupplier.__init__(self, parent, connectivity_service)

        self._patches = patches_storage
        self._events_db = events_db

    def _generate_response_messages(self, obj_id, offset, length, node_type):
        path = self._patches.get_patch_path(obj_id)
        if not exists(path):
            path = path + '.download'
        if not exists(path):
            raise ProtoError("FILE_READING_ERROR", "")

        chunks = self._read_data_by_chunks_from_file(path, offset, length)

        if obj_id not in self._uploads_info:
            files_info, size = self._get_patch_files_info(obj_id)
            if files_info:
                self._uploads_info[obj_id] = {
                    "files_info": files_info,
                    "size": size,
                    "state": None,
                    "uploaded": length,
                    "priority": 0,
                    "is_file": False,
                    "time": time.time()}
        else:
            self._uploads_info[obj_id]["uploaded"] += length
            self._uploads_info[obj_id]["time"] = time.time()

        messages = []
        for offset, length, chunk in chunks:
            messages.append(
                Message().data_response(
                    Message.PATCH, obj_id, offset, length, chunk))
        return messages

    def _generate_failure_message(self, obj_id, offset, error):
        return Message().data_failure(Message.PATCH, obj_id, offset, error)

    def _get_patch_files_info(self, obj_id):
        return get_patch_files_info(self, obj_id)
