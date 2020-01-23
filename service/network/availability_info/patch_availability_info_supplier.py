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
import logging
from service.network.browser_sharing import Message

from service.network.availability_info.availability_info_supplier \
    import AvailabilityInfoSupplier


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class PatchAvailabilityInfoSupplier(AvailabilityInfoSupplier):
    def __init__(self, parent, download_manager,
                 connectivity_service, node_list,
                 patches_storage):
        AvailabilityInfoSupplier.__init__(
            self, parent, download_manager, connectivity_service,
            node_list)

        self._patches = patches_storage

    def _process_request(self, obj_id, node_id, node_type, to_send=True):
        length = self._patches.get_patch_size(obj_id)
        if length:
            logger.debug("Patch with obj_id %s fully loaded, sending info",
                         obj_id)
            return self._send_info(node_id, obj_id, [(0, length)], to_send)
        else:
            logger.debug("Patch with obj_id %s is downloading now, "
                         "sending downloaded chunks, adding subscription",
                         obj_id)
            self._add_subscription(obj_id, node_id)
            return self._send_already_downloaded_chunks_if_any(
                node_id, node_type, obj_id, to_send)

    def _generate_response_message(self, obj_id, info):
        return Message().availability_info_response(Message.PATCH,
                                                    obj_id, info)

    def _generate_failure_message(self, obj_id, err):
        return Message().availability_info_failure(Message.PATCH, obj_id, err)
