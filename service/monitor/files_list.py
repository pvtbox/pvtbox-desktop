# coding=utf-8
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

from threading import RLock
from os.path import exists, join, relpath

from common.file_path import FilePath
from common.signal import Signal
from common.constants import FILE_LIST_COUNT_LIMIT, FILE_LINK_SUFFIX
from common.path_converter import PathConverter

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FilesList(object):
    CREATE_TOLERANCE_INTERVAL = 30

    def __init__(self, storage, root):
        super(FilesList, self).__init__()
        self._storage = storage
        self._pc = PathConverter(root)

        self._files_dict = dict()
        self._store_limit = FILE_LIST_COUNT_LIMIT * 10

        self.file_list_changed = Signal()

        self._lock = RLock()
        self._last_sent = None

    def on_file_added(self, rel_path, is_dir, mtime):
        if is_dir:
            return

        with self._lock:
            self._files_dict[rel_path] = (rel_path, is_dir, mtime, False)
        self.file_list_changed.emit()

    def on_file_deleted(self, rel_path):
        with self._lock:
            for file_path in self._files_dict.copy():
                if FilePath(file_path) in FilePath(rel_path) or \
                                file_path == rel_path:
                    self._files_dict.pop(file_path)
        self.file_list_changed.emit()

    def on_file_moved(self, old_path, new_path):
        with self._lock:
            for file_path in self._files_dict.copy():
                if FilePath(file_path) in FilePath(old_path) or \
                        file_path == old_path:
                    path = str(FilePath(join(new_path, relpath(file_path, old_path))))
                    old_file = self._files_dict.pop(file_path)
                    self._files_dict[path] = (
                        path, old_file[1], old_file[2], old_file[3])

        self.file_list_changed.emit()

    def on_file_modified(self, rel_path, mtime):
        with self._lock:
            old_mtime = self._files_dict.get(rel_path, ('', False, 0, False))[2]
            modified = (mtime - old_mtime) > self.CREATE_TOLERANCE_INTERVAL
            # can't modify directory
            self._files_dict[rel_path] = (rel_path, False, mtime, modified)
        self.file_list_changed.emit()

    def on_idle(self):
        self._clear_old()

    def get(self):
        files_to_return = []
        offset = 0
        while len(files_to_return) < FILE_LIST_COUNT_LIMIT:
            with self._lock:
                files = list(self._files_dict.values())
            # sort by mtime
            files.sort(key=lambda f: f[2], reverse=True)

            for item in files:
                path = item[0]

                abs_path = self._pc.create_abspath(path)
                if exists(abs_path) or exists(abs_path + FILE_LINK_SUFFIX):
                    files_to_return.append(item)
                    if len(files_to_return) >= FILE_LIST_COUNT_LIMIT:
                        break
                else:
                    logger.warning("File does not exists: %s", path)
                    with self._lock:
                        try:
                            self._files_dict.pop(path)
                        except KeyError:
                            pass

            if len(self._files_dict) < FILE_LIST_COUNT_LIMIT * 2:
                loaded = self._load_from_storage(offset)
                offset += self._store_limit
                if loaded:
                    files_to_return = []
                else:
                    break

        if self._last_sent is None or self._last_sent != files_to_return:
            self._last_sent = files_to_return
            return files_to_return
        else:
            return None

    def clear(self):
        with self._lock:
            self._files_dict.clear()

    def start(self):
        self._load_from_storage()
        self._last_sent = None

    def stop(self):
        with self._lock:
            self.clear()

    def _clear_old(self):
        with self._lock:
            if len(self._files_dict) <= self._store_limit:
                return

            files = list(self._files_dict.values())
            files.sort(key=lambda f: f[2], reverse=True)
            self._files_dict = {f[0]: f for f in files[:self._store_limit]}

    def _load_from_storage(self, offset=0):
        files_stored = self._storage.get_last_files(self._store_limit, offset)
        stored_dict = {file.relative_path:
                           (file.relative_path, file.is_folder,
                            file.mtime, file.was_updated)
                       for file in files_stored}
        with self._lock:
            self._files_dict.update(stored_dict)
        return len(files_stored)
