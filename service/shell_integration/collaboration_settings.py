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
from service.events_db.file_events_db import FileEventsDBError
from .share_path import node_synced, get_relpath, SharePathException, \
    INCORRECT_PATH, NOT_IN_SYNC
from .signals import signals

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@qt_run
def collaboration_path_settings(paths):
    """
    Prepares opening of collaboration settings dialog for given paths

    @param paths Path (1 element list) to folder with (potential)
        collaboration [list]
    @return None
    """
    def process_error(error, error_info=''):
        msg = {
            INCORRECT_PATH:
                "Failed to open collaboration settings '%s'. Incorrect path",
            NOT_IN_SYNC:
                "Path for collaboration settings not in sync '%s'",
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
    if len(paths) != 1:
        process_error(INCORRECT_PATH)
        return

    path = ensure_unicode(paths[0])
    if not op.isdir(path):
        process_error(INCORRECT_PATH)
        return

    try:
        # Name of the file relative to the root directory
        root, rel_path = get_relpath(path)
        if not rel_path or '/' in rel_path:
            raise SharePathException()
    except SharePathException:
        process_error(INCORRECT_PATH)
        return

    logger.info("Collaboration settings path '%s'...", rel_path)

    while True:
        # Wait if file not in db yet
        try:
            uuid = params.sync.get_folder_uuid(rel_path)
        except (FileNotFound, FileInProcessing, FileEventsDBError):
            uuid = None

        if uuid or (time.time() - start_time > timeout and node_synced):
            break

        if step == message_timeout:
            filename = op.basename(path)
            Application.show_tray_notification(
                tr("Prepare open collaboration settings.\n"
                   "Dialog will be opened after {} synced").format(
                    filename))
        step += 1
        time.sleep(1)

    if not uuid:
        process_error(NOT_IN_SYNC)
        Application.show_tray_notification(
            tr("Can't open collaboration settings.\n"
               "{} not in sync").format(path))
        return

    logger.debug("Collaboration settings requested for path %s, uuid %s",
                 rel_path, uuid)
    signals.show_collaboration_settings.emit(rel_path, uuid)
