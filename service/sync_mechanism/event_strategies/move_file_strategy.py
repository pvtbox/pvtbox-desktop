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

from .local_event_strategy import LocalEventStrategy
from .remote_event_strategy import RemoteEventStrategy
from .utils import dirname, basename

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class BaseLocalMoveStrategy(object):
    """
    Common part for local file/folder move strategy
    """

    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        event = self.event
        assert event.file_id
        folder = self._find_folder_by_uuid(session, event.folder_uuid)
        new_path = ('/'.join([folder.path, event.file_name])
                    if folder
                    else event.file_name)
        fs.accept_move(event.file.path, new_path,
                       is_directory=event.is_folder,
                       events_file_id=self.event.file_id,
                       is_offline=self.event.file.is_offline)

    def get_dst_path(self):
        return self._new_path


class LocalMoveFileStrategy(BaseLocalMoveStrategy, LocalEventStrategy):
    """
    Handle 'move' file event obtained from monitor
    """

    def __init__(self, db, event, file_path, new_file_path,
                 get_download_backups_mode, is_smart_sync=False):
        assert not event.diff_file_size
        super(LocalMoveFileStrategy, self).__init__(
            db=db,
            event=event,
            file_path=file_path,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)
        # new_file_path is None if event already saved to db with new path
        if new_file_path:
            event.file_name = basename(new_file_path)
        assert event.file_name and \
            '/' not in event.file_name
        self._new_path = new_file_path

    ''' Overloaded methods ====================================================
    '''
    def _assign_operation_specific_event_props(self, session):
        folder = self.db.find_folder_by_relative_path(
            folder_path=dirname(self._new_path),
            session=session)
        self.event.folder_uuid = folder.uuid if folder else None
        self.event.file.folder_id = folder.id if folder else None
        self.event.file.folder = folder


    def _register_in_server(self, web_api, file):
        event = self.event
        assert event.file_uuid
        assert event.file_name
        assert event.last_event
        assert event.last_event.server_event_id

        return web_api.file_event_move(
            event_uuid=self.event.uuid,
            file_uuid=event.file_uuid,
            last_event_id=event.last_event.server_event_id,
            new_file_name=event.file_name,
            new_folder_uuid=event.folder_uuid)

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        deleted_count, \
        remote_count = self._remove_or_restore_collaboration_file(
            self.event.file_id, session, copies_storage)
        return deleted_count, remote_count, None


class BaseRemoteMoveStrategy(object):
    """
    Common part for remote file/folder move strategy
    """
    pass

class RemoteMoveFileStrategy(BaseRemoteMoveStrategy, RemoteEventStrategy):
    """
    Handle 'move' file event received from signal server
    """

    def __init__(self, db, event, last_server_event_id, copies_storage=None,
                 get_download_backups_mode=lambda: None,
                 is_smart_sync=False):
        super(RemoteMoveFileStrategy, self).__init__(
            db=db,
            event=event,
            last_server_event_id=last_server_event_id,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync = is_smart_sync)
        self._copies_storage = copies_storage

    def is_file_download(self):
        return True
