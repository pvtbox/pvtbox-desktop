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
import logging
from time import time

from service.events_db import File
from common.utils import benchmark

from .event_strategy import atomic
from .local_event_strategy import LocalEventStrategy
from .remote_event_strategy import RemoteEventStrategy
from .exceptions import ParentDeleted
from .utils import dirname, basename
from common.constants import FREE_LICENSE

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class CreateFileStrategy(object):
    """Common parts for local and remote create strategy"""
    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        assert self.event.file

        path = self.event.file.path
        assert path
        assert self.event.file_hash
        assert self.file_download

        if self.event.file_size and self.event.file.is_offline:
            self._create_file_from_copy(path, fs)
        else:
            fs.create_empty_file(
                path,
                self.event.file_hash,
                self.event.file_id,
                is_offline=self.event.file.is_offline)

        fs.file_added.emit(path, False, time())
        # file can't be excluded if we are here
        self.event.file.excluded = False

        patches_storage.check_patches()

    def skip_if_file_will_be_deleted(self, session=None):
        return True


class LocalCreateFileStrategy(CreateFileStrategy, LocalEventStrategy):
    """docstring for LocalCreateFileStrategy"""
    def __init__(self, db, event, file_path, license_type,
                 get_download_backups_mode, is_smart_sync=False):
        super(LocalCreateFileStrategy, self).__init__(
            db=db,
            event=event,
            file_path=file_path,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)
        self._license_type = license_type

    ''' Overloaded methods ====================================================
    '''
    def _get_file(self, session, events_file_id=None):
        event = self.event
        assert event.file_name

        if event.file_id:
            return session.query(File).filter(File.id == event.file_id).one()

        folder = self.db.find_folder_by_relative_path(
            folder_path=dirname(self._file_path),
            session=session)
        files = session.query(File) \
            .filter(File.name == event.file_name) \
            .filter(File.folder_id == folder.id if folder else None) \
            .all()

        files = [f for f in files if f and f.is_existing]
        assert len(files) == 0, "Can't create. Such file name already exist"

        logger.debug(
            'creating new file in the db for the local event: %s',
            self.event.file_name)

        file = File(name=event.file_name,
                    is_folder=False)
        file.folder = folder
        return file

    def _assign_operation_specific_event_props(self, session):
        folder = self.event.file.folder
        self.event.folder_uuid = folder.uuid if folder else None

    def _register_in_server(self, web_api, file):
        assert self.event.file_name
        assert self.event.file_hash

        return web_api.file_event_create(
            event_uuid=self.event.uuid,
            file_name=self.event.file_name,
            file_size=self.event.file_size,
            folder_uuid=self.event.folder_uuid,
            diff_file_size=0,
            file_hash=self.event.file_hash)

    def _update_db_file_on_register(self, file, session=None):
        file.uuid = self.event.file_uuid

    @atomic
    def process_conflict(self,
                         session,
                         fs,
                         copes_storage,
                         reenter_event,
                         create_strategy_from_event,
                         change_processing_events_count,
                         excluded_dirs):
        event = self.event
        assert event.file_id

        self.make_conflicting_copy(fs, session=session)

        if self._license_type != FREE_LICENSE:
            self._set_actual_file_state_to_this(session, update_file_event=False)
        return True

    def make_conflicting_copy(self, fs, session=None):
        # taking path from event makes difference for free license
        file_path = self.db.get_path_from_event(self.event, session=session)
        conflict_name = fs.generate_conflict_file_name(
            file_path, is_folder=self.event.is_folder)
        next_events = filter(lambda e: e.id > self.event.id,
                             self.event.file.events)
        next_events = sorted(next_events, key = lambda e: e.id)
        move_event_exist = any(e.type == 'move' for e in next_events)
        delete_event_exist = any(e.type == 'delete' for e in next_events)
        if not move_event_exist \
                and not delete_event_exist \
                and (not self.event.file.folder
                     or not self.event.file.folder.is_deleted) \
                and (self._license_type != FREE_LICENSE
                     or self._conflicting_name_exists(session=session)):
            try:
                fs.accept_move(
                    src=self.event.file.path,
                    dst=conflict_name,
                    is_directory=self.event.is_folder,
                    events_file_id=self.event.file.id,
                    is_offline=self.event.file.is_offline)
            except fs.Exceptions.FileNotFound as e:
                logger.warning("Original file missing %s", e)
            self.event.file.name = self.event.file_name

        self.event.file_name = basename(conflict_name)
        for event in next_events:
            if event.state not in ('occured', 'conflicted'):
                continue

            if event.type != 'move':
                event.file_name = self.event.file_name
            if event.type in ('move', 'delete'):
                break

        if self._license_type != FREE_LICENSE:
            self.event.file.name = self.event.file_name
        self.event.type = 'create'
        self.event.last_event = None
        self.event.file_hash_before_event = None
        self.event.file_size_before_event = 0
        self.event.state = 'occured'

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        conflicted_name = self._get_free_file_name(
            self.event.file.name, fs)
        try:
            fs.copy_file(
                self.event.file.path, conflicted_name,
                self.event.file.is_offline)
        except fs.Exceptions.FileNotFound:
            logger.warning("Can't copy file. File does not exist %s",
                           self.event.file.path)

        deleted_count, remote_count = self._remove_or_restore_collaboration_file(
            self.event.file_id, session, copies_storage)
        return deleted_count, remote_count, None


class RemoteCreateFileStrategy(CreateFileStrategy, RemoteEventStrategy):
    def __init__(self, db, event, copies_storage=None,
                 get_download_backups_mode=lambda: None, is_smart_sync=False):
        super(RemoteCreateFileStrategy, self).__init__(
            db=db,
            event=event,
            last_server_event_id=None,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)
        self._copies_storage = copies_storage

    ''' Overloaded methods ====================================================
    '''

    def is_file_download(self):
        return True

    def _get_file(self, session, excluded_dirs=(), initial_sync=False):
        event = self.event
        logger.debug(
            'creating new file in db for the remote event: %s',
            event.file_name)
        assert event.file_name
        assert event.file_uuid
        return self.create_file(
            event, False, session,
            excluded_dirs=excluded_dirs, initial_sync=initial_sync)

    def _get_last_event_id(self, session):
        return None

    @benchmark
    def _ready_to_apply(self, session, is_deleted=False, files=None):
        path = self._get_target_path(session)
        files = self.db.find_files_by_relative_path(path,
                                                    on_parent_not_found=lambda: False,
                                                    session=session,
                                                    include_deleted=True)
        if files is False:
            raise ParentDeleted
        if len(files) > 1 and any(map(lambda f: f.is_locally_modified, files)):
            return False

        return super(RemoteCreateFileStrategy, self)._ready_to_apply(
            session, is_deleted, files)

    def _apply_move_if_needed(self, session, fs,
                              excluded_dirs, patches_storage,
                              events_queue):
        if self.event.file.event:
            return super(RemoteCreateFileStrategy, self)._apply_move_if_needed(
                session, fs, excluded_dirs, patches_storage, events_queue)

        folder = self.find_folder_by_uuid(session, self.event.folder_uuid)
        logger.debug("self.event.folder_uuid %s, folder %s",
                     self.event.folder_uuid, folder)
        parent_found = True
        if folder and not folder.is_existing and not folder.excluded:
            parent_found = False
            logger.debug("Parent folder does not exist for %s",
                         self.event.file.path)
            self._process_parent_not_found(session)

        self.event.file.name = self.event.file_name
        self.event.file.folder = folder
        if folder:
            self.event.file.folder_id = folder.id
        return True, parent_found
