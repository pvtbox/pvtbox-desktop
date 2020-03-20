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
import shutil
import os
import os.path as op
from queue import Queue

from common.application import Application
from common.async_utils import run_daemon
from service.shell_integration import params
from service.shell_integration.signals import signals
from common.translator import tr
from common.utils import get_dir_size, get_free_space, copy_file, get_next_name
from common.file_path import FilePath
from common.constants import FILE_LINK_SUFFIX


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _check_free_space(path, root, move, is_file):
    if is_file:
        size = op.getsize(path)
    else:
        size = get_dir_size(path)
    if move:
        path_list = path.split('/')
        root_list = root.split('/')
        # check if we have same drive for move
        if path_list[0] == root_list[0]:
            return True

    approx_total_size = size * 2 + size * 0.1  # size for files, copies, signs
    if get_free_space(root) < approx_total_size:
        logger.warning("Not enough disk space for  (moving) '%s'", path)
        msg = tr("Not enough disk space for copying (moving)\n{} to {}.\n"
                 "Please clean disk")
        Application.show_tray_notification(
            msg.format(path, root), tr("Sharing"))
        return False

    return True


def add_to_sync_dir(paths, move, callback):
    '''
    Copies given paths (files or directories) into sync directory.
    If destination path exists, new name in sync directory will be created

    @param paths to be copied [list]
    '''

    # Get sync directory path
    root = params.cfg.sync_directory
    if not root:
        logger.warning("Sync directory is not set")
        return

    logger.debug("Copying %d paths", len(paths))
    signals.show.emit()
    result_paths = []
    offline_paths = []
    online_paths = []
    for path in paths:
        is_file = op.isfile(path)
        path = FilePath(path)
        # Path is in sync directory already
        if path in FilePath(root):
            logger.debug("Path '%s' is in sync directory '%s' already",
                           path, root)
            result_paths.append(path)
            if is_file and not move:
                if path.endswith(FILE_LINK_SUFFIX):
                    online_paths.append(path)
                else:
                    offline_paths.append(path)
            continue

        if not op.exists(path.longpath):
            logger.warning(
                "Path requested for copying does not exist "
                "or not enough rights "
                "are granted to access it: '%s'", FilePath(path))
            Application.show_tray_notification(
                tr("Failed to copy to synchronized directory. Specified path "
                   "does not exist."),
                tr("Sharing"))
            continue

        basename = op.basename(path)
        destname = get_next_name(FilePath(op.join(root, basename)).longpath)

        if not _check_free_space(path, root, move, is_file):
            continue

        file_dir = 'file' if is_file else 'dir'
        logger.debug("Copying (moving) %s '%s' into sync directory...",
                     file_dir, path)

        # Emit corresponding signal
        signals.copying_started.emit(path)

        # Copy or move file or directory into sync directory
        try:
            if move:
                shutil.move(path, destname)
            elif is_file:
                copy_file(path, destname)
            else:
                shutil.copytree(path, destname)
        except Exception as e:
            logger.error(
                "Failed to copy (move) '%s' into sync directory (%s)",
                path, e)
            signals.copying_failed.emit(path)
            continue

        # Emit corresponding signal
        signals.copying_finished.emit(path)
        result_paths.append(destname)
        logger.debug("Copied successfully")

    if offline_paths:
        signals.offline_paths.emit(offline_paths, False, True)
    if online_paths:
        signals.offline_paths.emit(online_paths, True, True)

    logger.debug("All paths copied")
    if callable(callback):
        callback(result_paths)


# File/directory copying queue
_copying_queue = Queue()


@run_daemon
def copy_to_sync_worker():
    '''
    File copying thread worker function
    '''

    global _copying_queue

    logger.debug("Starting file copying thread...")

    while True:
        # Await for next copying task
        paths, move, callback = _copying_queue.get()
        add_to_sync_dir(paths, move, callback)


def queue_copying(paths, move=False, callback=None):
    '''
    Queues file/directory copying into sync directory

    @param path Filesystem path [unicode]
    '''

    global _copying_queue

    _copying_queue.put((paths, move, callback))
    logger.debug("Queued copying of paths '%s'", paths)
