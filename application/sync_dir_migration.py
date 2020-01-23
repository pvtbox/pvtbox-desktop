# -*- coding: utf-8 -*-
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
from threading import Event
from os import path as op

import logging
from PySide2.QtCore import QObject
from PySide2.QtCore import Signal as pyqtSignal

from common.async_qt import qt_run
from common.utils \
    import make_dirs, get_filelist, get_dir_list, remove_dir, remove_file, \
    create_shortcuts, remove_shortcuts, copy_file, ensure_unicode, \
    reset_all_custom_folder_icons
from common.constants import HIDDEN_FILES
from common.file_path import FilePath

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SyncDirMigration(QObject):
    progress = pyqtSignal(int)
    done = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, cfg, parent=None):
        QObject.__init__(self, parent=parent)

        self._cancelled = Event()
        self._cfg = cfg

    def cancel(self):
        logger.debug("Migration cancelled")
        self._cancelled.set()

    @qt_run
    def migrate(self, old_dir, new_dir):
        logger.info("Starting sync dir migration from %s, to %s",
                    old_dir, new_dir)
        old_dir = FilePath(old_dir).longpath
        new_dir = FilePath(new_dir).longpath
        old_files = get_filelist(old_dir)
        old_dirs = get_dir_list(old_dir)
        total_count = len(old_files) + len(old_dirs) + 1
        progress = 0
        sent_progress = 0
        logger.debug("Migration progress: %s/%s (%s%%)", 0, total_count, sent_progress)
        count = 1

        copied_dirs = []
        copied_files = []

        make_dirs(new_dir, is_folder=True)
        copied_dirs.append(new_dir)
        logger.debug("Migration progress: %s/%s (%s%%)", count, total_count, sent_progress)
        self.progress.emit(sent_progress)

        for dir in old_dirs:
            if self._cancelled.isSet():
                self._delete(dirs=copied_dirs)
                logger.debug("Migration done because cancelled")
                self.done.emit()
                return

            new_dir_path = ensure_unicode(op.join(
                new_dir, op.relpath(dir, start=old_dir)))

            try:
                make_dirs(new_dir_path, is_folder=True)
            except Exception as e:
                logger.error("Make dirs error: %s", e)
                self.failed.emit(str(e))
                self._delete(dirs=copied_dirs)
                return

            copied_dirs.append(new_dir_path)
            count += 1
            progress = int(count / total_count * 100)
            if progress > sent_progress:
                sent_progress = progress
                self.progress.emit(sent_progress)
            logger.debug("Migration progress: %s/%s (%s%%)", count, total_count, sent_progress)

        for file in old_files:
            if self._cancelled.isSet():
                self._delete(dirs=copied_dirs, files=copied_files)
                logger.debug("Migration done because cancelled")
                self.done.emit()
                return

            if file in HIDDEN_FILES:
                continue

            new_file_path = ensure_unicode(op.join(
                new_dir, op.relpath(file, start=old_dir)))

            logger.info("Copying file %s, to %s",
                        file, new_file_path)
            try:
                copy_file(file, new_file_path, preserve_file_date=True)
            except Exception as e:
                logger.error("Copy file error: %s", e)
                self.failed.emit(str(e))
                self._delete(dirs=copied_dirs, files=copied_files)
                return

            copied_files.append(new_file_path)
            count += 1
            progress = int(count / total_count * 100)
            if progress > sent_progress:
                sent_progress = progress
                self.progress.emit(sent_progress)
            logger.debug("Migration progress: %s/%s (%s%%)", count, total_count, sent_progress)

        logger.debug("Saving new config")
        self._cfg.set_settings(dict(sync_directory=FilePath(new_dir)))
        self._cfg.sync()
        logger.info("New config saved")

        logger.debug("Updating shortcuts")
        create_shortcuts(new_dir)
        remove_shortcuts(old_dir)
        logger.debug("Resetting custom folder icons")
        reset_all_custom_folder_icons(old_dir)

        logger.debug("Migration done")
        self.done.emit()

        logger.info("Migration thread end")

    def _delete(self, dirs=[], files=[]):
        for dir in dirs:
            remove_dir(dir)
        for file in files:
            remove_file(file)
