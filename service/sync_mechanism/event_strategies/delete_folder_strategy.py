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

from .delete_file_strategy import RemoteDeleteFileStrategy, \
    LocalDeleteFileStrategy
from .event_strategy import db_read


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DeleteFolderStrategy(object):
    """Common parts for local and remote delete strategy"""
    pass


class LocalDeleteFolderStrategy(DeleteFolderStrategy, LocalDeleteFileStrategy):
    def __init__(self, db, event, folder_path, get_download_backups_mode,
                 is_smart_sync=False):
        assert not event.diff_file_size
        super(LocalDeleteFolderStrategy, self).__init__(
            db=db,
            event=event,
            file_path=folder_path,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)

    ''' Overloaded methods ====================================================
    '''
    def _register_in_server(self, web_api, file):
        assert self.event.file_uuid
        assert self.event.last_event
        assert self.event.last_event.server_event_id

        return web_api.folder_event_delete(
            event_uuid=self.event.uuid,
            folder_uuid=self.event.file_uuid,
            last_event_id=self.event.last_event.server_event_id)

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        file_id = self.event.file_id
        deleted_count, \
        remote_count, \
        folders_to_restore = self._remove_or_restore_folder_files(
            file_id, session, copies_storage)
        deleted_count_adding, remote_count_adding = \
            self._remove_or_restore_collaboration_file(
                file_id, session, copies_storage, folders_to_restore)

        return deleted_count + deleted_count_adding, \
               remote_count + remote_count_adding, \
               folders_to_restore


class RemoteDeleteFolderStrategy(DeleteFolderStrategy,
                                 RemoteDeleteFileStrategy):
    """Handle 'delete' file_event received from signal server"""
    def __init__(self, db, event, last_server_event_id, copies_storage=None,
                 get_download_backups_mode=lambda:None,
                 is_smart_sync=False):
        super(RemoteDeleteFolderStrategy, self).__init__(
            db=db,
            event=event,
            last_server_event_id=last_server_event_id,
            copies_storage=copies_storage,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)

    @db_read
    def skip_if_file_will_be_deleted(self, session):
        return (not self.event.file.is_existing and
                not self.event.erase_nested)
