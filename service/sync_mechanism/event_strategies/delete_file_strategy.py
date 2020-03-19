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

from service.events_db import Event
from common.constants import DOWNLOAD_PRIORITY_REVERSED_PATCH

from .event_strategy import atomic, db_read
from .local_event_strategy import LocalEventStrategy
from .remote_event_strategy import RemoteEventStrategy

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DeleteFileStrategy(object):
    """Common parts for local and remote delete strategy"""
    pass


class LocalDeleteFileStrategy(DeleteFileStrategy, LocalEventStrategy):
    def __init__(self, db, event, file_path, get_download_backups_mode,
                 is_smart_sync=False):
        super(LocalDeleteFileStrategy, self).__init__(
            db=db,
            event=event,
            file_path=file_path,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)

    ''' Overloaded methods ====================================================
    '''
    def _register_in_server(self, web_api, file):
        assert self.event.file_uuid
        assert self.event.last_event
        assert self.event.last_event.server_event_id

        return web_api.file_event_delete(
            event_uuid=self.event.uuid,
            file_uuid=self.event.file_uuid,
            last_event_id=self.event.last_event.server_event_id)

    def _make_conflicted_copy(self, fs, file_name, patch_paths):
        pass

    @atomic
    def process_conflict(self,
                         session,
                         fs,
                         copies_storage,
                         reenter_event,
                         create_strategy_from_event,
                         change_processing_events_count,
                         excluded_dirs):

        max_event = self.event.file.events[-1]

        if self.event.id == max_event.id and \
                len(self.event.file.events) > 2 and \
                self.event.last_event_id != self.event.file.events[-2].id:
            self.event.last_event = self.event.file.events[-2]
            self.event.state = 'occured'
            return True
        elif self.event.id >= max_event.id:
            # wait for conflicting event(s) to be in db
            return True

        result = True
        if max_event.type != 'delete':
            new_delete_event = Event(
                type='delete',
                is_folder=self.event.is_folder,
                file_size=max_event.file_size,
                diff_file_size=max_event.diff_file_size,
                rev_diff_file_size=max_event.rev_diff_file_size,
                file_hash=max_event.file_hash,
            )

            self.append_local_event.emit(
                new_delete_event, self.event.file.path,
                None, self.event.file_id,
                self.event.file.is_offline)
            # do not remove from processing events
            # for not downloading event twice
            result = False
            self.change_processing_events_counts.emit(1, 0)

        session.delete(self.event)
        self.event = None
        return result

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        deleted_count, \
        remote_count = self._remove_or_restore_collaboration_file(
            self.event.file_id, session, copies_storage)
        return deleted_count, remote_count, None


class RemoteDeleteFileStrategy(DeleteFileStrategy, RemoteEventStrategy):
    """Handle 'delete' file_event received from signal server"""
    def __init__(self, db, event, last_server_event_id, copies_storage,
                 get_download_backups_mode, is_smart_sync=False):
        self._copies_storage = copies_storage
        self._must_download_copy = False

        super(RemoteDeleteFileStrategy, self).__init__(
            db=db,
            event=event,
            last_server_event_id=last_server_event_id,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)

        self._download_priority = DOWNLOAD_PRIORITY_REVERSED_PATCH

    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        assert self.event.file_id

        file_path = self.event.file.path
        files = self.db.find_files_by_relative_path(file_path, session=session)
        if not files:
            logger.warning("Can't find files (folders) for path %s", file_path)
        elif len(files) == 1:
            # don't delete file if other file with same name exicts
            logger.debug("Deleting file (folder) '%s'...", file_path)
            fs.accept_delete(file_path, is_directory=self.event.is_folder,
                             events_file_id=self.event.file_id,
                             is_offline=self.event.file.is_offline)
            logger.info("File (folder) '%s' is deleted", file_path)
        else:
            fs.change_events_file_id(self.event.file_id, None)

    def is_file_download(self):
        return self._must_download_copy

    @db_read
    def skip_if_file_will_be_deleted(self, session):
        logger.debug("skip_if_file_will_be_deleted. file.is_existing %s. "
                     "_must_download_copy %s. self.event.last_event_id %s",
                     self.event.file.is_existing, self._must_download_copy, self.event.last_event_id)
        return (not self.event.file.is_existing and
                not self._must_download_copy and
                self.event.last_event_id)
