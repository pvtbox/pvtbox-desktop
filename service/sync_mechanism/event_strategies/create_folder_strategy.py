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

from .create_file_strategy import LocalCreateFileStrategy
from .create_file_strategy import RemoteCreateFileStrategy
from .event_strategy import atomic
from service.events_db import File

from common.constants import FREE_LICENSE
from .exceptions import SkipEventForNow

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class CreateFolderStrategy(object):
    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        assert self.event.file_name

        try:
            fs.create_directory(self.event.file.path, self.event.file_id)
        except fs.Exceptions.WrongFileId:
            if not self._apply_folder_delete_if_any(session, fs):
                raise SkipEventForNow()

            fs.create_directory(self.event.file.path, self.event.file_id)

        if self.event.file.excluded:
            self.event.file.excluded = False
            session.query(File) \
                .filter(File.folder_id == self.event.file.id) \
                .filter(File.excluded) \
                .update(dict(excluded=False), synchronize_session=False)

    def skip_if_file_will_be_deleted(self, session=None):
        return True


class LocalCreateFolderStrategy(CreateFolderStrategy, LocalCreateFileStrategy):
    def __init__(self, db, event, folder_path, license_type,
                 get_download_backups_mode):
        super(LocalCreateFolderStrategy, self).__init__(
            db=db,
            event=event,
            file_path=folder_path,
            license_type=license_type,
            get_download_backups_mode=get_download_backups_mode)

    ''' Functions to overload in descendants ==================================
    '''
    def _get_file(self, session, events_file_id=None):
        folder = LocalCreateFileStrategy._get_file(self, session, events_file_id)
        assert not folder.id or folder.is_folder
        folder.is_folder = True
        return folder

    def _assign_operation_specific_event_props(self, session):
        folder = self.event.file.folder
        self.event.folder_uuid = folder.uuid if folder else None
        self.event.file_hash = None

    def _register_in_server(self, web_api, file):
        event = self.event
        assert event.file_name

        return web_api.folder_event_create(
            event_uuid=self.event.uuid,
            folder_name=event.file_name,
            parent_folder_uuid=event.folder_uuid)

    @atomic
    def process_conflict(self,
                         session,
                         fs,
                         copies_storage,
                         reenter_event,
                         create_strategy_from_event,
                         change_processing_events_count,
                         excluded_dirs):
        if self._license_type == FREE_LICENSE:
            self.make_conflicting_copy(fs, session=session)
        else:
            next_events = filter(lambda e: e.id > self.event.id,
                                 self.event.file.events)
            next_events = sorted(next_events, key=lambda e: e.id)
            move_event_exist = any(e.type == 'move' for e in next_events)
            delete_event_exist = any(e.type == 'delete' for e in next_events)
            if next_events and move_event_exist:
                next_events[0].type = 'create'
                session.delete(self.event)
                self.event = None
                return True

            if not delete_event_exist:
                folder_path = self.event.file.path

                logger.debug("Try find conflicting file and its event")
                conflicting_file, conflicting_event = \
                    self.db.find_conflicting_file_or_folder(
                        folder_path,
                        excluded_id=self.event.file_id,
                        session=session)

                conflicting_file_id = \
                    conflicting_file.id if conflicting_file else None
                conflicting_file_is_folder = \
                    conflicting_file.is_folder if conflicting_file else None
            else:
                conflicting_file = conflicting_event = None
                conflicting_file_is_folder = False

            if (not delete_event_exist and conflicting_file_is_folder and
                    (conflicting_event.type != 'move'
                     or conflicting_file.event_id and
                     conflicting_file.event_id >= conflicting_event.id
                     and self.event.type == 'create'
                     and len(self.event.file.events) == 1)):
                files = session.query(File) \
                    .filter(File.folder_id == self.event.file.id).all()

                session.delete(self.event)

                logger.debug("Reassign internal files folder_id")
                for file in files:
                    file.folder_id = conflicting_file_id

                fs.change_events_file_id(self.file_id, conflicting_file_id)
                session.delete(self.event.file)
                self.event = None
            else:
                logger.debug("Make conflicting copy")
                self.make_conflicting_copy(fs, session=session)
        return True

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        event = self.event
        file_path = event.file.path
        file_id = event.file_id
        conflicted_name = self._get_free_file_name(
            self.event.file.name, fs)
        try:
            fs.copy_file(file_path, conflicted_name,
                         is_directory=True)
        except fs.Exceptions.FileNotFound:
            logger.warning("Can't copy file. File does not exist %s",
                           self.event.file.path)

        # delete folder from fs to prevent new file creation events
        fs.accept_delete(file_path,
                         is_directory=True)
        deleted_count, remote_count, _ = self._remove_or_restore_folder_files(
            file_id, session, copies_storage)
        deleted_count_adding, remote_count_adding = \
            self._remove_or_restore_collaboration_file(
                file_id, session, copies_storage)

        return deleted_count + deleted_count_adding, \
               remote_count + remote_count_adding, None


class RemoteCreateFolderStrategy(CreateFolderStrategy,
                                 RemoteCreateFileStrategy):
    def __init__(self, db, event, get_download_backups_mode):
        super(RemoteCreateFolderStrategy, self).__init__(
            db=db,
            event=event,
            get_download_backups_mode=get_download_backups_mode)

    ''' Overloaded methods ====================================================
    '''
    def _get_file(self, session, excluded_dirs=(), initial_sync=False):
        if self.event.erase_nested:
            file = session.query(File) \
                .filter(File.uuid == self.event.file_uuid) \
                .one_or_none()
            if file:
                file.name = self.event.file_name
                return file

        return self.create_file(
            self.event, True, session,
            excluded_dirs=excluded_dirs, initial_sync=initial_sync)

    def set_collaborated_folder_icon(self, session, fs, collaborated_folders):
        folder = self.event.file
        if folder.uuid in collaborated_folders:
            folder.is_collaborated = True
            fs.set_collaboration_folder_icon(folder.name)
