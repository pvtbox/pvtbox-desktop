# -*- coding: utf-8 -*-#
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
import traceback

from os import path, stat
from time import time

from service.events_db import FolderNotFound
from common.constants import CREATE, MODIFY, MOVE, FILE_LINK_SUFFIX
from common.file_path import FilePath
from service.monitor.actions.action_base import ActionBase
from common.errors import ExpectedError, EventConflicted, EventAlreadyAdded
from common.utils import touch

import logging

from service.monitor.fs_event import FsEvent
from common.signal import Signal

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class UpdateStorageAction(ActionBase):
    def __init__(self, storage, path_converter, tracker=None):
        super(UpdateStorageAction, self).__init__()
        self._storage = storage
        self._path_converter = path_converter
        self.event_processed = Signal(FsEvent)
        self._tracker = tracker
        self._waiting = False

    def _on_new_event(self, fs_event):
        self._process_new_event(fs_event)

    def _process_new_event(self, fs_event):
        if self._waiting:
            return self.event_returned(fs_event)

        src_path = fs_event.src[: -len(FILE_LINK_SUFFIX)] if fs_event.is_link \
            else fs_event.src
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            try:
                file = self._storage.get_known_file(
                    src_path, session=session)
                if file != fs_event.file or \
                        not self._process(fs_event, file, session):
                    return self.event_returned(fs_event)
                self.event_passed(fs_event)
                if fs_event.event_type in (CREATE, ):
                    # store events_file_id
                    self._storage.save_file(fs_event.file, session=session)
                session.commit()
                self.event_processed(fs_event)
            except EventConflicted:
                logger.debug("UpdateStorage event conflicted")
                if session:
                    session.rollback()
                return self.event_suppressed(fs_event)
            except EventAlreadyAdded:
                logger.debug("UpdateStorage Event already added")
                if session:
                    session.commit()
                self.event_processed(fs_event)
                return
            except ExpectedError as e:
                logger.debug("UpdateStorage expected error: %s", e)
                if session:
                    session.rollback()
                return self.event_suppressed(fs_event)
            except FolderNotFound:
                logger.debug("Folder not found")
                if session:
                    session.rollback()
                self._delete_file_and_parent_folder_from_storage(fs_event, session)
                session.commit()
                return self.event_returned(fs_event)
            except Exception as e:
                logger.debug(
                    '%s exception %s',
                    self.__class__.__name__,
                    e)
                if session:
                    session.rollback()
                if self._tracker:
                    tb = traceback.format_list(traceback.extract_stack())
                    if self._tracker:
                        self._tracker.error(tb, str(e))
                return self.event_returned(fs_event)

    def _process(self, fs_event, file, session):
        if not fs_event.in_storage and \
                fs_event.event_type not in (CREATE, ):
            return False

        file_saved = False
        if fs_event.event_type in (CREATE, MODIFY, MOVE):
            if fs_event.event_type in (CREATE, MODIFY):
                if fs_event.event_type in (CREATE, ):
                    abs_path = fs_event.src if not fs_event.is_link \
                        else fs_event.src[: -len(FILE_LINK_SUFFIX)]
                    fs_event.file = (
                        self._storage.get_new_file(
                            abs_path=abs_path,
                            is_folder=fs_event.is_dir,
                            session=session))
                    if not fs_event.is_dir:
                        self._change_mtime(fs_event)
                if not fs_event.is_dir:
                    fs_event.file.file_hash = fs_event.new_hash
                    if fs_event.new_signature:
                        self._storage.update_file_signature(
                            fs_event.file, fs_event.new_signature)
            else:
                if fs_event.is_dir:
                    self._move_all_folder_files_in_storage(fs_event,
                                                           session=session)
                    file_saved = True
                else:
                    fs_event.file.relative_path = \
                        self._path_converter.create_relpath(fs_event.dst)
            fs_event.file.was_updated = fs_event.event_type == MODIFY
            if not fs_event.file.mtime or fs_event.file.mtime <= fs_event.mtime:
                fs_event.file.mtime = fs_event.mtime
            fs_event.file.size = fs_event.file_size
            if fs_event.event_type not in (CREATE,) and not file_saved:
                self._storage.save_file(fs_event.file, session=session)
        else:
            if fs_event.is_dir:
                self._delete_all_folder_files_from_storage(fs_event.file.relative_path,
                                                           session=session)
            else:
                self._storage.delete_file(file, session=session)

        return True

    def _delete_file_and_parent_folder_from_storage(self, fs_event, session):
        if fs_event.event_type == MOVE:
            dirname = path.dirname(fs_event.dst)
        else:
            dirname = path.dirname(fs_event.src)

        dirname = FilePath(dirname)

        parent = self._storage.get_known_file(dirname, True, session=session)
        if parent:
            self._delete_all_folder_files_from_storage(
                parent.relative_path, session=session)
        else:
            session.delete(fs_event.file)

    def _delete_all_folder_files_from_storage(self, relative_path, session):
        self._storage.delete_known_folder_children(
            relative_path, session=session)

    def _move_all_folder_files_in_storage(self, fs_event, session):
        old_dir_rel_path = self._path_converter.create_relpath(fs_event.src)
        new_dir_rel_path = self._path_converter.create_relpath(fs_event.dst)

        self._storage.move_known_folder_children(
            old_dir_rel_path, new_dir_rel_path, session=session)

    def _change_mtime(self, fs_event):
        src_longpath = FilePath(fs_event.src).longpath
        try:
            touch(src_longpath)
            st = stat(src_longpath)
            fs_event.mtime = st.st_mtime
        except Exception as e:
            logger.warning("Can't touch or get stat for file %s. Reason: %s",
                           fs_event.src, e)
            fs_event.mtime = time()

    def set_waiting(self, to_wait):
        self._waiting = to_wait
