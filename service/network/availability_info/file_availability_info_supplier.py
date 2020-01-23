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

from PySide2.QtCore import Signal
from service.network.browser_sharing import Message, ProtoError

from common.constants import UNKNOWN_LICENSE
from service.network.availability_info.availability_info_supplier \
    import AvailabilityInfoSupplier
from service.network.utils import get_file_hash
from common.utils import get_file_size

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FileAvailabilityInfoSupplier(AvailabilityInfoSupplier):
    _fail_subscriptions = Signal(str)
    _check_subscriptions_response = Signal(str)

    def __init__(self, parent, download_manager,
                 connectivity_service, node_list,
                 events_db, copies_storage,
                 get_download_backups_mode, get_file_path):

        AvailabilityInfoSupplier.__init__(
            self, parent, download_manager, connectivity_service,
            node_list)

        self._events_db = events_db
        self._copies = copies_storage
        self._license_type = UNKNOWN_LICENSE
        self._get_file_path = get_file_path
        self._get_download_backups_mode=get_download_backups_mode

        self._fail_subscriptions.connect(self._on_fail_subscriptions)
        self._check_subscriptions_response.connect(
            self._on_check_subscriptions_response)

    def on_file_changed(self, event_uuid_before, event_uuid_after):
        if event_uuid_before:
            self._fail_subscriptions.emit(event_uuid_before)
        self._check_subscriptions_response.emit(event_uuid_after)

    def _on_fail_subscriptions(self, obj_id):
        if obj_id not in self._subscriptions:
            return

        # TODO: implement error message
        msg = self._generate_failure_message(obj_id, b'')
        for node_id in self._subscriptions[obj_id]:
            self._connectivity_service.send(node_id, msg, True)

    def _on_check_subscriptions_response(self, obj_id):
        if obj_id not in self._subscriptions:
            return
        for node_id in self._subscriptions[obj_id]:
            node_type = self._connectivity_service.get_node_type(node_id)
            if node_type and node_type != "node" or node_id in self._node_list:
                self._process_availability_info_request(obj_id, node_id,
                                                        node_type)

    def _process_request(self, obj_id, node_id, node_type, to_send=True):
        try:
            hash = self._get_file_hash(obj_id, node_type)
        except ProtoError as e:
            logger.debug("get file hash error: %s (%s)", e.err_code, e.err_message)
            if e.err_code in (
                    "UNKNOWN_EVENT_UUID", "UNKNOWN_FILE", "UNKNOWN_FILE_HASH",
                    "FILE_NOT_REGISTERED", "FILE_NOT_SYNCHRONIZED",
            ):
                hash = None
            else:
                raise
        if not hash:
            logger.debug("File with obj_id %s not known yet,"
                         " adding subscription", obj_id)
            self._add_subscription(obj_id, node_id)
            if node_type == 'node':
                return None
            return self._send_info(node_id, obj_id, list(), to_send)

        length = self._copies.get_copy_size(hash)
        if length:
            logger.debug("File with obj_id %s copy found, size: %s", obj_id, length)
        if not length and self._get_download_backups_mode() is False:
            path = self._get_file_path(obj_id)
            logger.debug("File with obj_id %s copy not found, get file size: %s", obj_id, path)
            length = get_file_size(path)

        if length:
            logger.debug("File with obj_id %s fully loaded, sending info, length: %s",
                         obj_id, length)
            return self._send_info(node_id, obj_id, [(0, length)], to_send)
        else:
            logger.debug("File with obj_id %s is downloading now, "
                         "sending downloaded chunks, adding subscription",
                         obj_id)
            self._add_subscription(obj_id, node_id)
            return self._send_already_downloaded_chunks_if_any(
                node_id, node_type, obj_id, to_send)

    def _get_file_hash(self, obj_id, node_type):
        return get_file_hash(self, obj_id, node_type)

    def _generate_response_message(self, obj_id, info):
        return Message().availability_info_response(Message.FILE, obj_id, info)

    def _generate_failure_message(self, obj_id, err):
        return Message().availability_info_failure(Message.FILE, obj_id, err)

    def get_sharing_info(self):
        return self._connectivity_service.get_sharing_info()
