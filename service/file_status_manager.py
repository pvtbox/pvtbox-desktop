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
from os.path import join

from itertools import chain
from collections import defaultdict
from PySide2.QtCore import QObject, Signal, Qt

from common.constants import SS_STATUS_SYNCING, SS_STATUS_SYNCED, \
    SS_STATUS_PAUSED, SS_STATUS_INDEXING
from common.file_path import FilePath
from common.utils import get_platform

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FileStatusManager(QObject):
    files_status = Signal(str, list, str)
    clear_path = Signal(str, str)

    _check_not_synced = Signal()
    _path_removed = Signal(str)

    def __init__(self, sync, cfg):
        self._sync = sync
        self._cfg = cfg
        self._status = "syncing"
        self._sync_resuming = True
        self._files_in_indexing = set()
        self._files_in_downloading = dict()
        self._files_ignored = set()
        self._files_disk_error = set()
        self._subscriptions = dict()
        self._not_synced_paths = set()
        QObject.__init__(self)

        self._check_not_synced.connect(
            self._on_check_not_synced, Qt.QueuedConnection)
        if get_platform() in ("Windows", "Linux"):
            self._path_removed.connect(
                self._on_path_removed, Qt.QueuedConnection)

    def connect_sync_signals(self):
        logger.debug('connect_sync_signals')
        self._sync.downloads_status.connect(
            self._on_downloads_status)
        self._sync.file_added_to_indexing.connect(self._on_file_added_to_indexing)
        self._sync.file_removed_from_indexing.connect(self._on_file_removed_from_indexing)
        self._sync.file_added_to_ignore.connect(self._on_file_added_to_ignore)
        self._sync.file_removed_from_ignore.connect(self._on_file_removed_from_ignore)
        self._sync.file_added_to_disk_error.connect(self._on_file_added_to_disk_error)

    def subscribe(self, client, path):
        logger.debug('subscribe, client: %s, path: %s', client, path)
        if path:
            path = FilePath(path)
            if path not in FilePath(self._cfg.sync_directory):
                logger.warning(
                    "Subscription out of sync directory for path %s", path)
                return

        paths = self._subscriptions.get(client)
        if paths and path:
            paths.add(path)
        elif path:
            self._subscriptions[client] = {path}
        else:
            self._subscriptions[client] = set()

        is_sync_dir = not path or path == self._cfg.sync_directory
        status = self._get_file_status(path) if not is_sync_dir else self._status
        logger.debug("emit file: %s, status: %s for client %s", path, status, client)
        self.files_status.emit(
            client, [FilePath(path).shortpath if path
                     else FilePath(self._cfg.sync_directory).shortpath], status)
        if not is_sync_dir and status != "synced":
            self._not_synced_paths.add(path)

    def unsubscribe(self, client, path):
        logger.debug('unsubscribe, client: %s, path: %s', client, path)
        if path:
            path = FilePath(path)
        else:
            self._subscriptions.pop(client, None)
            return
        for client, subscription_paths in self._subscriptions.copy().items():
            new_subscription_paths = set()
            for subscription_path in subscription_paths:
                if not (path == subscription_path or subscription_path in path):
                    new_subscription_paths.add(subscription_path)
            self._subscriptions[client] = new_subscription_paths

    def on_sync_resuming(self):
        self._sync_resuming = True
        for client, paths in self._subscriptions.items():
            self.files_status.emit(
                client, [p.shortpath
                         for p in paths] +
                        [FilePath(self._cfg.sync_directory).shortpath],
                "syncing")

    def on_global_status(self, status):
        was_sync_resuming = self._sync_resuming
        self._sync_resuming = False
        was_error_or_pause = self._status in ('paused', 'error')
        self._status = self._convert_sync_status(status)
        if self._status == 'paused':
            self._files_in_indexing.clear()
        logger.debug('on_global_status, status: %s, was_error_or_pause: %s',
                     self._status, was_error_or_pause)
        self._set_files_ignored()
        for client, paths in self._subscriptions.items():
            logger.debug("emit global status: %s", self._status)
            if self._status in ('error', 'paused'):
                self._not_synced_paths.update(paths)
                paths = [p.shortpath for p in paths] + \
                        [FilePath(self._cfg.sync_directory).shortpath]
            else:
                if was_error_or_pause or was_sync_resuming:
                    self._check_not_synced.emit()
                paths = [FilePath(self._cfg.sync_directory).shortpath]
            logger.debug('on_global_status, emit %s: %s for client %s',
                         self._status, paths, client)
            self.files_status.emit(client, paths, self._status)

    def _convert_sync_status(self, status):
        if status == SS_STATUS_SYNCED:
            return "synced"
        elif status in (SS_STATUS_INDEXING, SS_STATUS_SYNCING):
            return "syncing"
        elif status == SS_STATUS_PAUSED:
            return "paused"
        else:
            return "error"

    def _on_downloads_status(self, _, __, ___, downloads, ____):
        # downloads[0] - added downloads infos
        # downloads[2] - removed downloads uuids
        logger.debug("_on_downloads_status")
        added = set()
        for download_id, download_info in downloads[0].items():
            for info in download_info['files_info']:
                path = FilePath(
                    join(self._cfg.sync_directory, info['target_file_path']))
                added.add(path)
                self._files_in_downloading[path] = download_id

        self._files_in_downloading = {k: v for k, v in self._files_in_downloading.items()
                                      if v not in set(downloads[2])}

        for path in added:
            self._on_file_syncing(path)
        self._check_not_synced.emit()

    def _on_file_added_to_indexing(self, path):
        logger.debug('_on_file_added_to_indexing, path: %s', path)
        self._files_in_indexing.add(path)
        self._on_file_syncing(path)

    def _on_file_syncing(self, path):
        logger.debug("on_file_syncing, path: %s", path)
        if path in self._files_disk_error:
            self._files_disk_error.discard(path)
            # make send file status as 'syncing'
            self._check_not_synced.emit()

        clients_paths = defaultdict(list)
        for client, subscription_paths in self._subscriptions.items():
            for subscription_path in subscription_paths:
                if subscription_path in self._not_synced_paths:
                    continue
                if path == subscription_path or path in subscription_path:
                    clients_paths[client].append(subscription_path)
                    self._not_synced_paths.add(subscription_path)
        for client, paths in clients_paths.items():
            logger.debug("emit syncing: %s for client %s", paths, client)
            self.files_status.emit(
                client, [p.shortpath for p in paths], "syncing")

    def _on_file_removed_from_indexing(self, path, path_removed):
        logger.debug('_on_file_removed_from_indexing, path: %s', path)
        self._files_in_indexing.discard(path)
        if path_removed:
            self._path_removed.emit(path)
        self._check_not_synced.emit()

    def _on_file_added_to_ignore(self, path):
        logger.debug("_on_file_added_to_ignore, path: %s", path)
        self._files_ignored.add(path)

        clients_paths = defaultdict(list)
        for client, subscription_paths in self._subscriptions.items():
            for subscription_path in subscription_paths:
                if (path == subscription_path or
                        path in subscription_path):
                    clients_paths[client].append(subscription_path)
                    self._not_synced_paths.add(subscription_path)
        for client, paths in clients_paths.items():
            logger.debug("emit error: %s", paths)
            self.files_status.emit(
                client, [p.shortpath for p in paths], "error")

    def _on_file_removed_from_ignore(self, path):
        self._files_ignored.discard(path)
        self._check_not_synced.emit()

    def _on_file_added_to_disk_error(self, path):
        logger.debug("_on_file_added_to_disk_error, path: %s", path)
        self._files_disk_error.add(path)

        clients_paths = defaultdict(list)
        for client, subscription_paths in self._subscriptions.items():
            for subscription_path in subscription_paths:
                if (path == subscription_path or
                        path in subscription_path):
                    clients_paths[client].append(subscription_path)
                    self._not_synced_paths.add(subscription_path)
        for client, paths in clients_paths.items():
            logger.debug("emit error: %s to client %s", paths, client)
            self.files_status.emit(
                client, [p.shortpath for p in paths], "error")

    def _on_check_not_synced(self):
        logger.debug("_on_check_syncing")
        synced_paths = set()
        error_paths = set()
        syncing_paths = set()
        for path in self._not_synced_paths:
            status = self._get_file_status(path)
            if status == "synced":
                synced_paths.add(path)
            elif status == "error":
                error_paths.add(path)
            elif status == "syncing":
                syncing_paths.add(path)

        if synced_paths:
            self._not_synced_paths = self._not_synced_paths.difference(
                synced_paths)

        for client, paths in self._subscriptions.items():
            if synced_paths:
                client_synced_paths = list(paths.intersection(synced_paths))
                if client_synced_paths:
                    logger.debug("emit synced: %s to client %s", client_synced_paths, client)
                    self.files_status.emit(
                        client, [p.shortpath for p in client_synced_paths],
                        "synced")
            if error_paths:
                client_error_paths = list(paths.intersection(error_paths))
                if client_error_paths:
                    logger.debug("emit error: %s to client %s", client_error_paths, client)
                    self.files_status.emit(
                        client, [p.shortpath for p in client_error_paths],
                        "error")
            if syncing_paths:
                client_syncing_paths = list(paths.intersection(syncing_paths))
                if client_syncing_paths:
                    logger.debug("emit syncing: %s to client %s", client_syncing_paths, client)
                    self.files_status.emit(
                        client, [p.shortpath for p in client_syncing_paths],
                        "syncing")

    def _set_files_ignored(self):
        self._files_ignored = self._sync.get_long_paths()

    def _get_file_status(self, path):
        if self._sync_resuming:
            return "syncing"

        if self._status in ('paused', 'error'):
            return self._status

        if path in self._files_ignored or path in self._files_disk_error:
            return "error"

        for error_path in chain(self._files_ignored, self._files_disk_error):
            if error_path in path:
                return "error"

        if path in self._files_in_indexing \
                or path in self._files_in_downloading.keys() \
                or not self._sync.is_known(path):
            return "syncing"

        for syncing_path in chain(self._files_in_indexing, self._files_in_downloading.keys()):
            if syncing_path in path:
                return "syncing"

        return "synced"

    def _on_path_removed(self, path):
        path = FilePath(path)
        for client, paths in self._subscriptions.items():
            if any(p in path for p in paths):
                logger.debug("emit clear path: %s", path)
                self.clear_path.emit(client, path.shortpath)
                self.unsubscribe(client, path)
