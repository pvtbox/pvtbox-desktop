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
import time
import os.path as op

from service.shell_integration import params
from common.async_qt import qt_run
from .signals import signals
from .protocol import FILE_PATH_ERRORS, FILE_DELETED, FILE_EXCLUDED, \
    FILE_NOT_FOUND, INVALID_JSON

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@qt_run
def file_info(uuids, context):
    """
    Queries file path for uuid given

    @param uuids file uuids (1 element list) [list]
    @return None
    """

    if not uuids or not uuids[0]:
        logger.warning("Empty uuid obtained")
        error = FILE_PATH_ERRORS[INVALID_JSON]
        path = ""
        signals.file_path.emit(path, error, context)
        return

    uuid = uuids[0]

    while True:
        # Wait if db is locked
        try:
            path, \
            deleted, \
            excluded = params.sync.get_path_deleted_excluded_by_uuid(uuid)
            break
        except Exception:
            time.sleep(2)

    error = ""
    if path and not deleted and not excluded and not op.exists(path):
        path = None
    if not path or deleted or excluded:
        key = FILE_DELETED if deleted \
            else FILE_EXCLUDED if excluded \
            else FILE_NOT_FOUND
        error = FILE_PATH_ERRORS[key]
        path = ""

    logger.debug("File path for uuid %s, is %s, deleted: %s, excluded: %s",
                 uuid, path, deleted, excluded)
    signals.file_info_reply.emit(path, error, context)
