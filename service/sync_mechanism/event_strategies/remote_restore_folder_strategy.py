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

from service.events_db import File

from service.sync_mechanism.event_strategies.create_folder_strategy \
    import RemoteCreateFolderStrategy

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class RemoteRestoreFolderStrategy(RemoteCreateFolderStrategy):
    def __init__(self, db, event, get_download_backups_mode, is_smart_sync):
        super(RemoteRestoreFolderStrategy, self).__init__(
            db=db,
            event=event,
            get_download_backups_mode=get_download_backups_mode,
            is_smart_sync=is_smart_sync)

    ''' Overloaded methods ====================================================
    '''

    def _get_file(self, session, excluded_dirs=(), initial_sync=False):
        event = self.event
        logger.debug(
            'getting file from db for the remote event: %s',
            event.file_name)
        assert event.file_name
        assert event.file_uuid

        file = session.query(File) \
            .filter(File.uuid == self.event.file_uuid) \
            .filter(File.is_folder == 1) \
            .one_or_none()
        if file is None:
            return super(RemoteRestoreFolderStrategy, self)._get_file(
                session, excluded_dirs, initial_sync)
        file.name = event.file_name
        folder = self.find_folder_by_uuid(session, event.folder_uuid)
        file.folder = folder
        return file
