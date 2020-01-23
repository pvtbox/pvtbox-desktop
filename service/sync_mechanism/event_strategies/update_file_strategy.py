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

from datetime import datetime, timedelta

from .local_event_strategy import LocalEventStrategy
from .remote_event_strategy import RemoteEventStrategy
from common.constants import MIN_DIFF_SIZE, PATCH_WAIT_TIMEOUT

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class UpdateFileStrategy(object):
    """Common parts for local and remote update strategy"""
    def skip_if_file_will_be_deleted(self, session=None):
        return True


class LocalUpdateFileStrategy(UpdateFileStrategy, LocalEventStrategy):
    def __init__(self, db, event, file_path, get_download_backups_mode):
        assert event.file_hash
        super(LocalUpdateFileStrategy, self).__init__(
            db=db,
            event=event,
            file_path=file_path,
            get_download_backups_mode=get_download_backups_mode)

    ''' Overloaded methods ====================================================
    '''
    def _register_in_server(self, web_api, file):
        assert self.event.file_uuid
        assert self.event.last_event
        assert self.event.last_event.server_event_id

        return web_api.file_event_update(
            event_uuid=self.event.uuid,
            file_uuid=self.event.file_uuid,
            file_size=self.event.file_size,
            last_event_id=self.event.last_event.server_event_id,
            diff_file_size=self.event.diff_file_size,
            rev_diff_file_size=self.event.rev_diff_file_size,
            file_hash=self.event.file_hash)

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        assert self.event.file

        conflicted_name = self._get_free_file_name(
            self.event.file.name, fs)
        try:
            fs.copy_file(self.event.file.path, conflicted_name)
        except fs.Exceptions.FileNotFound:
            logger.warning("Can't copy file. File does not exist %s",
                           self.event.file.path)

        deleted_count, \
        remote_count = self._remove_or_restore_collaboration_file(
            self.event.file_id, session, copies_storage)
        return deleted_count, remote_count, None


class RemoteUpdateFileStrategy(UpdateFileStrategy, RemoteEventStrategy):
    def __init__(self, db, event, last_server_event_id,
                 patches_storage, copies_storage,
                 get_download_backups_mode):
        self._patches_storage = patches_storage
        self._copies_storage = copies_storage
        self._must_download_copy = False

        super(RemoteUpdateFileStrategy, self).__init__(
            db=db,
            event=event,
            last_server_event_id=last_server_event_id,
            get_download_backups_mode=get_download_backups_mode)

    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        assert self.event.diff_file_uuid

        logger.debug('Applying update %s', self.event.file.path)
        if self.file_download:
            if self.event.file_size:
                self._create_file_from_copy(self.event.file.path, fs,
                                            search_by_id=True)
            else:
                fs.create_empty_file(self.event.file.path,
                                     self.event.file_hash,
                                     self.event.file_id,
                                     search_by_id=True)
        else:
            assert self.event.diff_file_size
            if not self._apply_patch(
                    fs=fs,
                    file_path=self.event.file.path,
                    patch_uuid=self.event.diff_file_uuid):
                # download file if patch applying failed
                self.event.state = 'received'
                self._must_download_copy = True
                self.file_download = self.is_file_download()
                if hasattr(self, 'download_success'):
                    delattr(self, 'download_success')
                return

        if self._download_backups:
            patches_storage.check_patches()

    def is_file_download(self):
        timestamp = datetime.strptime(self.event.timestamp, "%Y-%m-%d %H:%M:%S.%f") \
            if isinstance(self.event.timestamp, str) \
            else self.event.timestamp
        return (
            self._must_download_copy or (
                    not self._patches_storage.patch_exists(
                        self.event.diff_file_uuid) and (
                        self.event.outdated or
                        self.event.file_size < MIN_DIFF_SIZE or
                        (self.event.diff_file_size > 0 and
                         self.event.diff_file_size > self.event.file_size) or
                        (self.event.file and
                         not self.event.file.event_id and
                         not self.event.file.last_skipped_event_id) or
                        (not self.event.diff_file_size and
                         datetime.utcnow() - timestamp > timedelta(
                                    seconds=PATCH_WAIT_TIMEOUT)))
            )
        )
