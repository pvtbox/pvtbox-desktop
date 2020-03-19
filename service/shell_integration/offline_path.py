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

from service.events_db import FileNotFound, FileInProcessing
from common.utils import ensure_unicode
from service.shell_integration import params
from common.async_qt import qt_run
from common.application import Application
from common.translator import tr
from common.file_path import FilePath
from common.constants import FILE_LINK_SUFFIX
from service.events_db.file_events_db import FileEventsDBError
from .share_path import node_synced, get_relpath, SharePathException, \
    INCORRECT_PATH, NOT_IN_SYNC

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@qt_run
def offline_paths(paths, is_offline=True):
    """
    Makes given paths offline as is_offline flag
    @param paths paths [list]
    @param is_offline flag [bool]
    @return None
    """
    def process_error(error, error_info=''):
        msg = {
            INCORRECT_PATH:
                "Failed to change offline status '%s'. Incorrect path",
            NOT_IN_SYNC:
                "Path for changing offline status not in sync '%s'",
        }
        logger.error(msg[error], paths)
        if params.tracker:
            tracker_errors = {
                INCORRECT_PATH: params.tracker.INCORRECT_PATH,
                NOT_IN_SYNC: params.tracker.NOT_IN_SYNC,
            }
            params.tracker.share_error(
                0,
                tracker_errors[error],
                time.time() - start_time)

    start_time = time.time()

    timeout = 10 * 60  # seconds
    message_timeout = 2  # seconds

    step = 0
    command_str = tr("add to offline") if is_offline \
        else tr("remove from offline")

    for path in paths:
        path = ensure_unicode(path)
        try:
            # Name of the file relative to the root directory
            root, rel_path = get_relpath(path)
            if not rel_path:
                raise SharePathException()
        except SharePathException:
            process_error(INCORRECT_PATH)
            return

        if rel_path.endswith(FILE_LINK_SUFFIX):
            rel_path = rel_path[: -len(FILE_LINK_SUFFIX)]
            if not rel_path:
                process_error(INCORRECT_PATH)
                return

        logger.info("Offline on=%s, path '%s'...", is_offline, rel_path)
        while True:
            # Wait if file not in db yet
            try:
                if op.isfile(path):
                    uuid = params.sync.get_file_uuid(rel_path)
                elif op.isdir(path):
                    uuid = params.sync.get_folder_uuid(rel_path)
                else:
                    process_error(INCORRECT_PATH)
                    return

            except (FileNotFound, FileInProcessing, FileEventsDBError):
                uuid = None

            if uuid or (time.time() - start_time > timeout and node_synced):
                break

            if step == message_timeout:
                filename = op.basename(path)
                Application.show_tray_notification(
                    tr("Prepare {}.\n"
                       "Action will be completed after {} synced").format(
                        command_str, filename))
            step += 1
            time.sleep(1)

        if not uuid:
            process_error(NOT_IN_SYNC)
            Application.show_tray_notification(
                tr("Can't {}.\n"
                   "{} not in sync").format(command_str, path))
            return

        try:
            params.sync.file_added_to_indexing.emit(FilePath(path))
            success = params.sync.make_offline(uuid, is_offline)
        except FileEventsDBError:
            success = False
        if not success:
            params.sync.file_removed_from_indexing.emit(FilePath(path))
            Application.show_tray_notification(
                tr("Can't {} for path: {}.").format(command_str, path))


def get_offline_status(paths):
    if not params.cfg.smart_sync:
        return 2        # no smart sync

    rel_paths = list()
    for path in paths:
        path = ensure_unicode(path)
        try:
            # Name of the file relative to the root directory
            root, rel_path = get_relpath(path)
            if not rel_path:
                raise SharePathException()

            rel_paths.append(rel_path)
        except SharePathException:
            logger.warning("Incorrect path %s", path)
            return 2     # no smart sync


    return params.sync.get_offline_status(rel_paths, timeout=0.5)
