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
from threading import RLock

from service.events_db import FileNotFound, FileInProcessing
from common.utils import ensure_unicode
from common.constants import STATUS_WAIT
from common.webserver_client import Client_APIError
from service.shell_integration import params
from common.file_path import FilePath
from common.async_qt import qt_run
from common.application import Application
from common.translator import tr
from service.events_db.file_events_db import FileEventsDBError

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


INCORRECT_PATH = 1
NOT_IN_SYNC = 2
INCORRECT_SERVER_RESPONSE = 4
SAVE_TO_CLIPBOARD_FAILED = 8

node_synced = False

link_copied_to_clipboard = None
share_link_thread_id = 0
share_link_thread_active = 0
share_link_thread_lock = RLock()



class SharePathException(Exception):
    pass


def update_sync_status(status, substatus, l, r, fs, ee):
    global node_synced
    node_synced = status == STATUS_WAIT


def link_copy_success(success):
    global link_copied_to_clipboard
    link_copied_to_clipboard = success


def get_relpath(path):
    # Get sync directory path
    root = params.cfg.sync_directory
    if not root:
        logger.error("Sync directory is not set")
        raise SharePathException()

    # Path is not in sync directory
    if not (FilePath(path) in FilePath(root)):
        logger.debug("Path '%s' is not in sync directory '%s'", path, root)
        raise SharePathException()

    # Path is not exist
    if not op.exists(path):
        logger.warning("Path '%s' is not exist", path)
        raise SharePathException()

    # Name of the file relative to the root directory
    return root, op.relpath(FilePath(path), FilePath(root))


@qt_run
def share_paths(paths, link_ready_cb, save_to_clipboard=False,
                context='', move=False):
    """
    Shares given paths via API

    @param paths Paths to be shared [list]
    @param link_ready_cb Callback to be called on links ready or on error
        [callable]
    @param save_to_clipboard Whether links are to be saved to clipboard [bool]
    @param context Context to be return in message if any [str]
    @param move Type of share message recieved (move or copy) [bool]
    @return None
    """
    def process_error(error, error_info=''):
        msg = {
            INCORRECT_PATH:
                "Failed to share '%s'. Incorrect path",
            NOT_IN_SYNC:
                "Path for share not in sync '%s'",
            INCORRECT_SERVER_RESPONSE:
                "Failed to share '%s'. Incorrect server response",
            SAVE_TO_CLIPBOARD_FAILED:
                "Failed to save share link '%s' to clipboard",
        }
        logger.error(msg[error], path)
        if params.tracker:
            tracker_errors = {
                INCORRECT_PATH: params.tracker.INCORRECT_PATH,
                NOT_IN_SYNC: params.tracker.NOT_IN_SYNC,
                INCORRECT_SERVER_RESPONSE:
                    params.tracker.INCORRECT_SERVER_RESPONSE,
                SAVE_TO_CLIPBOARD_FAILED:
                    params.tracker.INTERNAL_ERROR,
            }
            params.tracker.share_error(
                0,
                tracker_errors[error],
                time.time() - start_time)
        if callable(link_ready_cb):
            link_ready_cb(paths, None, error_info)

    start_time = time.time()
    global share_link_thread_id
    global share_link_thread_active
    global link_copied_to_clipboard
    with share_link_thread_lock:
        share_link_thread_id += 1
        thread_id = share_link_thread_id
        share_link_thread_active = thread_id

    # Share without expire
    share_ttl = 0
    num_tries = 5
    num_save_to_clipboard_tries = 5
    timeout = 10 * 60  # seconds
    message_timeout = 2  # seconds

    step = 0
    share_links = []
    result_paths = []

    for path in paths:
        path = ensure_unicode(path)
        try:
            # Name of the file relative to the root directory
            root, rel_path = get_relpath(path)
        except SharePathException:
            process_error(INCORRECT_PATH)
            return
        logger.info("Sharing path '%s'...", rel_path)

        share_link = None

        while True:
            # Wait if file not in db yet
            try:
                if op.isfile(path):
                    is_file = True
                    uuid = params.sync.get_file_uuid(rel_path)
                elif op.isdir(path):
                    is_file = False
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
                    tr("Prepare to copy URL(s) for downloading to clipboard.\n"
                       "URL(s) will be copied after {} synced").format(
                        filename),
                    tr("Sharing"))

            step += 1
            time.sleep(1)

        if not uuid:
            process_error(NOT_IN_SYNC)
            return

        error_info = ''
        existing_share = params.ss_client.get_sharing_info()
        if uuid in existing_share:
            share_link = existing_share[uuid].get('share_link')
            logger.debug("Link for %s already exists: %s", path, share_link)
        else:
            # Register sharing enabling on API server
            for i in range(num_tries):
                # wait if file not registered yet
                try:
                    share_link, share_hash, error_info = params.web_api.sharing_enable(
                        uuid, share_ttl)
                except Client_APIError:
                    pass

                if share_link:
                    break

                time.sleep(1)

        if not share_link:
            process_error(INCORRECT_SERVER_RESPONSE, error_info)
            return
        share_links.append(share_link)
        result_paths.append(FilePath(path).shortpath)

    if save_to_clipboard:
        share_link = '\r\n'.join(share_links)
        with share_link_thread_lock:
            link_copied_to_clipboard = False
        tries = 0
        while tries < num_save_to_clipboard_tries:
            with share_link_thread_lock:
                if thread_id != share_link_thread_active:
                    return

            if link_copied_to_clipboard is not None:
                link_copied_to_clipboard = None
                tries += 1
                # Copy URL to clipboard (if any)
                Application.save_to_clipboard(share_link)
            time.sleep(0.1)

            if link_copied_to_clipboard:
                break
        else:
            process_error(SAVE_TO_CLIPBOARD_FAILED)
            return

    if params.tracker:
        pass
# todo fix me
#        params.tracker.share_add(
#            is_file,
#            uuid, share_link,
#            time.time() - start_time)

    if callable(link_ready_cb):
        link_ready_cb(
            result_paths, share_links, save_to_clipboard=save_to_clipboard,
            context=context, move=move)


def cancel_sharing(paths):
    '''
    Cancels sharing of paths given via API

    @param path Path to be shared [unicode]
    @return Operation success flag [bool]
    '''

    # Name of the file relative to the root directory
    success = False
    for path in paths:
        try:
            _, rel_path = get_relpath(path)
        except SharePathException:
            if params.tracker:
                params.tracker.share_cancel(0, False)
            continue

        logger.info("Cancelling sharing path '%s'...", rel_path)

        sharing_info = params.ss_client.get_sharing_info()

        # Given path is a file inside sync directory
        if op.isfile(path):
            uuid = params.sync.get_file_uuid(rel_path)
        elif op.isdir(path):
            uuid = params.sync.get_folder_uuid(rel_path)
        else:
            if params.tracker:
                params.tracker.share_cancel(0, False)
            continue

        # Check that UUID is known as shared
        if not uuid or uuid not in sharing_info:
            logger.error("No share for path '%s'", rel_path)
            if params.tracker:
                params.tracker.share_cancel(0, False)
            continue

        # Register sharing disabling on API server
        try:
            params.web_api.sharing_disable(uuid)
        except Client_APIError:
            logger.error(
                "API request for cancel sharing of '%s' failed", rel_path)
            if params.tracker:
                params.tracker.share_cancel(uuid, False)
            continue

        if params.tracker:
            params.tracker.share_cancel(uuid, True)
        success = True

    return success


def is_paths_shared(paths):
    for path in paths:
        try:
            _, rel_path = get_relpath(path)
        except SharePathException:
            continue
        logger.debug("Checking sharing path '%s'...", rel_path)
        if params.sync.is_path_shared(rel_path):
            return True

    return False


def is_folder(path):
    return op.isdir(path)
