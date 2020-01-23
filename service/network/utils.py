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

from service.network.browser_sharing import ProtoError

from common.constants import FREE_LICENSE
from common.utils import get_local_time_from_timestamp

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def get_file_hash(requester, obj_id, node_type):
    if node_type == "webfm" or \
            (node_type == "node" and requester._license_type > FREE_LICENSE):
        logger.debug("Check for shared state is not required")
        hash = requester._events_db.get_file_hash_by_event_uuid(
            obj_id)
    elif node_type in ("webshare", "node"):  # "webshare" means browser
        logger.debug("Check for shared state is required")
        sharing_info = requester.get_sharing_info()
        shared_objects_list = [obj for obj in sharing_info.keys()]
        logger.debug("shared_objects_list: '%s'", shared_objects_list)
        hash = requester._events_db.get_file_hash_by_event_uuid(
            obj_id, check_is_file_shared=True,
            shared_objects_list=shared_objects_list)
    else:
        raise ProtoError("INVALID_CLIENT_TYPE",
                         "Invalid client type '{}'".format(node_type))
    return hash


def get_file_files_info(requester, obj_id):
    target_file_path, \
    size, \
    timestamp, \
    is_created, \
    is_deleted = requester._events_db.get_file_info_by_event_uuid(obj_id)

    if not target_file_path:
        return None, None

    files_info = [{
        "target_file_path": target_file_path,
        "mtime": get_local_time_from_timestamp(timestamp),
        "is_created": is_created,
        "is_deleted": is_deleted}]
    return files_info, size


def get_patch_files_info(requester, obj_id):
    file_list, size = requester._events_db.get_files_list_by_diff_uuid(
        obj_id, last_only=True, not_applied_only=False)
    if not file_list:
        return None, None

    target_file_path, timestamp = file_list[0]

    if not target_file_path:
        return None, None

    files_info = [{
        "target_file_path": target_file_path,
        "mtime": get_local_time_from_timestamp(timestamp),
        "is_created": False,
        "is_deleted": False}]
    return files_info, size
