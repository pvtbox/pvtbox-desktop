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
import os
import shutil
import logging
import errno
import pickle
from threading import RLock

import unicodedata

from os.path import join, exists, dirname, basename, isfile, isdir

from service.monitor.rsync import Rsync
from common.path_utils import get_signature_path
from common.signal import Signal
from common.utils import remove_dir, mkdir, \
    get_copies_dir, create_empty_file, copy_file, remove_file, get_temp_dir, \
    set_ext_invisible, copy_time
from common.constants import FILE_LINK_SUFFIX

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class QuietProcessor(object):

    def __init__(self,
                 root,
                 storage,
                 path_converter,
                 exceptions):
        self._root = root
        self._storage = storage
        self._path_converter = path_converter
        self._exceptions = exceptions

        self._tmp_id = 0
        self._tmp_id_lock = RLock()

        self._init_temp_dir()

        self.file_moved = Signal(str, str)
        self.file_deleted = Signal(str)
        self.file_modified = Signal(str, float)
        self.access_denied = Signal(str)

    def delete_file(self, full_path, events_file_id=None, is_offline=True):
        full_path = unicodedata.normalize('NFC', full_path)
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _full_path = self._get_file_by_id(
                events_file_id, session)
            if not file:
                if events_file_id is not None:
                    logger.warning(
                        "Skipping file deletion because "
                        "file with same events_file_id not found")
                    return
                file = self._storage.get_known_file(
                    full_path, session=session)
            else:
                full_path = _full_path

            if file:
                try:
                    remove_file(self.get_hard_path(full_path, is_offline))
                except OSError as e:
                    logger.warning("Can't remove file. Reason: %s", e)
                    if e.errno == errno.EACCES:
                        self._raise_access_denied(full_path)
                    else:
                        raise e
                self._storage.delete_file(file, session=session)

        self.file_deleted.emit(self._path_converter.create_relpath(full_path))

    def delete_directory(self, full_path, events_file_id=None):
        full_path = unicodedata.normalize('NFC', full_path)
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _full_path = self._get_file_by_id(
                events_file_id, session)
            if file:
                full_path = _full_path
            elif events_file_id is not None:
                logger.warning(
                    "Skipping directory deletion because "
                    "directory with same events_file_id not found")
                return

            rel_path = self._path_converter.create_relpath(full_path)
            files = self._storage.get_known_folder_children(
                rel_path, session=session)
            try:
                temp_path = join(self._temp_dir, basename(full_path))
                if isdir(temp_path):
                    remove_dir(temp_path, suppress_not_exists_exception=True)
                elif isfile(temp_path):
                    remove_file(temp_path)
                if isdir(full_path):
                    os.rename(full_path, temp_path)
                    try:
                        remove_dir(temp_path, suppress_not_exists_exception=True)
                    except Exception:
                        logger.debug("Dir %s delete failed", temp_path)
            except OSError as e:
                logger.warning("Can't remove dir %s. Reason: %s", full_path, e)
                if e.errno == errno.EACCES:
                    self._raise_access_denied(full_path)
                elif e.errno != errno.ENOENT:   # directory does not exist
                    raise e

            deleted_paths = [f.relative_path for f in files]
            self._storage.delete_known_folder_children(
                rel_path, session=session)

        for path in deleted_paths:
            self.file_deleted.emit(path)

    def create_directory(self, full_path, events_file_id, wrong_file_id=None):
        full_path = unicodedata.normalize('NFC', full_path)

        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            assert exists(dirname(full_path))
            file = self._storage.get_known_file(full_path, True,
                                                session=session)

            if file is None:
                mkdir(full_path)
                file = self._storage.get_new_file(full_path, True,
                                                  session=session)
            elif events_file_id and file.events_file_id and \
                        file.events_file_id != events_file_id and \
                        wrong_file_id:
                    logger.error("Wrong file id for %s. Expected %s. Got %s",
                                 full_path, events_file_id,
                                 file.events_file_id if file else None)
                    raise wrong_file_id(full_path,
                                        events_file_id,
                                        file.events_file_id)

            file.events_file_id = events_file_id
            self._storage.save_file(file, session=session)

    def patch_file(self, full_fn, patch_archive, silent=True,
                   events_file_id=None, wrong_file_id=None):
        full_fn = unicodedata.normalize('NFC', full_fn)

        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _full_path = self._get_file_by_id(
                events_file_id, session)
            if file:
                full_fn = _full_path
            else:
                file = self._storage.get_known_file(
                    full_fn, is_folder=False, session=session)
            if (file is None or file and events_file_id and
                file.events_file_id
                and file.events_file_id != events_file_id) and \
                        wrong_file_id:
                    logger.error("Wrong file id for %s. Expected %s. Got %s",
                                 full_fn, events_file_id,
                                 file.events_file_id if file else None)
                    raise wrong_file_id(full_fn,
                                        events_file_id,
                                        file.events_file_id if file else None)

                # file = self._storage.get_new_file(full_fn, False,
                #                                   session=session)

            assert exists(dirname(full_fn))
            hash, signature, old_hash = Rsync.accept_patch(
                patch_archive=patch_archive,
                unpatched_file=full_fn,
                known_old_hash=file.file_hash if file else None,
                root=self._root)

            if silent:
                file.mtime = os.stat(full_fn).st_mtime
                file.size = os.stat(full_fn).st_size
                file.file_hash = hash
                file.events_file_id = events_file_id
                file.was_updated = True
                self._storage.save_file(file, session=session)
                self._storage.update_file_signature(file, signature)
                self.file_modified.emit(file.relative_path, file.mtime)

        return hash, old_hash

    def move_file(self, src_full_path, dst_full_path, events_file_id=None,
                  already_exists=None, file_not_found=None,
                  wrong_file_id=None, is_offline=True):
        dst_full_path = unicodedata.normalize('NFC', dst_full_path)
        dst_rel_path = self._path_converter.create_relpath(dst_full_path)
        src_full_path = unicodedata.normalize('NFC', src_full_path)
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _full_path = self._get_file_by_id(
                events_file_id, session)
            if not file:
                file = self._storage.get_known_file(
                    src_full_path, False, session=session)
            else:
                src_full_path = _full_path
            src_rel_path = self._path_converter.create_relpath(src_full_path)
            if src_rel_path == dst_rel_path or not self._check_paths_exist(
                    src_full_path, dst_full_path,
                    already_exists, file_not_found):
                return

            assert exists(dirname(dst_full_path))
            if file:
                if events_file_id and file.events_file_id and \
                        file.events_file_id != events_file_id and \
                        wrong_file_id:
                    logger.error("Wrong file id for %s. Expected %s. Got %s",
                                 dst_full_path, events_file_id,
                                 file.events_file_id)
                    raise wrong_file_id(src_full_path,
                                        events_file_id,
                                         file.events_file_id)

                file.relative_path = self._path_converter.create_relpath(
                    dst_full_path)
                try:
                    shutil.move(
                        src=self.get_hard_path(src_full_path, is_offline),
                        dst=self.get_hard_path(dst_full_path, is_offline))
                except OSError as e:
                    logger.warning("Can't move file. Reason: %s", e)
                    if e.errno == errno.EACCES:
                        self._raise_access_denied(src_full_path)
                    else:
                        raise e
                self._storage.save_file(file, session=session)

            self.file_moved(src_rel_path, dst_rel_path)

    def move_directory(self, src_full_path, dst_full_path,
                       events_file_id=None, already_exists=None,
                       file_not_found=None, wrong_file_id=None):
        dst_full_path = unicodedata.normalize('NFC', dst_full_path)
        dst_rel_path = self._path_converter.create_relpath(dst_full_path)
        src_full_path = unicodedata.normalize('NFC', src_full_path)

        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _full_path = self._get_file_by_id(
                events_file_id, session)
            if not file:
                file = self._storage.get_known_file(
                    src_full_path, True, session=session)
            else:
                src_full_path = _full_path

            src_rel_path = self._path_converter.create_relpath(src_full_path)
            if src_rel_path == dst_rel_path or not self._check_paths_exist(
                    src_full_path, dst_full_path,
                    already_exists, file_not_found):
                return

            assert exists(dirname(dst_full_path))
            if file:
                if events_file_id and file.events_file_id and \
                        file.events_file_id != events_file_id and \
                        wrong_file_id:
                    logger.error("Wrong file id for %s. Expected %s. Got %s",
                                 src_full_path, events_file_id,
                                 file.events_file_id if file else None)
                    raise wrong_file_id(src_full_path,
                                        events_file_id,
                                        file.events_file_id)
            try:
                os.rename(src_full_path, dst_full_path)
            except OSError as e:
                logger.warning("Can't move dir %s. Reason: %s",
                               src_full_path, e)
                if e.errno == errno.EACCES:
                    self._raise_access_denied(src_full_path)
                else:
                    raise e

            self._storage.move_known_folder_children(
                src_rel_path, dst_rel_path, session=session)

            self.file_moved(src_rel_path, str(dst_rel_path))

    def create_file_from_copy(self, file_rel_path, copy_hash, silent,
                              events_file_id, search_by_id=False,
                              wrong_file_id=None,
                              copy_does_not_exists=None):
        dst_full_path = self._path_converter.create_abspath(file_rel_path)
        copy_full_path = join(get_copies_dir(self._root), copy_hash)
        if copy_does_not_exists is not None and not exists(copy_full_path):
            if not self.make_copy_from_existing_files(copy_hash):
                raise copy_does_not_exists(copy_hash)
        return self._create_file(copy_full_path, dst_full_path, silent,
                                 copy_hash, events_file_id, search_by_id,
                                 wrong_file_id)

    def make_copy_from_existing_files(self, copy_hash):
        copy_full_path = join(get_copies_dir(self._root), copy_hash)
        if exists(copy_full_path):
            return True

        tmp_full_path = self._get_temp_path(copy_full_path)
        with self._storage.create_session(read_only=True,
                                          locked=False) as session:
            excludes = []
            while True:
                file = self._storage.get_file_by_hash(
                    copy_hash, exclude=excludes, session=session)
                if not file:
                    return False

                file_path = self._path_converter.create_abspath(
                    file.relative_path)
                if not exists(file_path):
                    excludes.append(file.id)
                    continue

                try:
                    copy_file(file_path, tmp_full_path)
                    hash = Rsync.hash_from_block_checksum(
                        Rsync.block_checksum(tmp_full_path))
                    if hash == copy_hash:
                        os.rename(tmp_full_path, copy_full_path)
                        return True
                    else:
                        excludes.append(file.id)
                        remove_file(tmp_full_path)
                except Exception as e:
                    logger.warning("Can't operate tmp file %s. Reason: (%s)",
                                   tmp_full_path, e)
                    if file.id not in excludes:
                        excludes.append(file.id)
                    try:
                        remove_file(tmp_full_path)
                    except Exception:
                        tmp_full_path = self._get_temp_path(copy_full_path)

    def _get_temp_path(self, copy_full_path):
        while True:
            with self._tmp_id_lock:
                self._tmp_id += 1
            tmp_full_path = "{}_{}.tmp".format(copy_full_path, self._tmp_id)
            if not exists(tmp_full_path):
                return tmp_full_path

    def create_empty_file(self, file_rel_path, file_hash, silent,
                          events_file_id, search_by_id=False,
                          wrong_file_id=None, is_offline=True):
        dst_full_path = self._path_converter.create_abspath(file_rel_path)
        self._create_file(None, dst_full_path, silent, file_hash,
                          events_file_id, search_by_id, wrong_file_id,
                          is_offline)

    def _create_file(self, src_full_path, dst_full_path, silent,
                     file_hash, events_file_id, search_by_id,
                     wrong_file_id, is_offline=True):

        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file = None
            file_exists = False
            was_updated = True
            if search_by_id:
                file, _full_path = self._get_file_by_id(
                    events_file_id, session)
                if file:
                    dst_full_path = _full_path

            assert exists(dirname(dst_full_path))
            hard_path = self.get_hard_path(dst_full_path, is_offline)
            if not file:
                file = self._storage.get_known_file(
                    dst_full_path, is_folder=False, session=session)
                if file and events_file_id and file.events_file_id and \
                        file.events_file_id != events_file_id and \
                        wrong_file_id:
                    logger.error("Wrong file id for %s. Expected %s. Got %s",
                                 dst_full_path, events_file_id,
                                 file.events_file_id)
                    raise wrong_file_id(dst_full_path,
                                        events_file_id,
                                        file.events_file_id)
            if file:
                file_exists = file.file_hash == file_hash and \
                              (exists(dst_full_path) and is_offline or
                               exists(hard_path) and not is_offline)
                logger.debug("The fact that file %s with same hash "
                             "already exists in storage and filesystem is %s",
                             dst_full_path, file_exists)

            if file is None:
                # if search_by_id and wrong_file_id:
                #     logger.error("Wrong file id for %s. Expected %s. Got None",
                #                  dst_full_path, events_file_id)
                #     raise wrong_file_id(dst_full_path,
                #                         events_file_id,
                #                         None)

                file = self._storage.get_new_file(
                    dst_full_path, False, session=session)
                was_updated = False
            old_hash = file.file_hash

            signature = None
            if not file_exists:
                if src_full_path:
                    # create file from copy
                    if not exists(get_signature_path(file_hash)):
                        signature = Rsync.block_checksum(src_full_path)
                    tmp_full_path = self._get_temp_path(src_full_path)
                    copy_file(src_full_path, tmp_full_path)
                    try:
                        remove_file(dst_full_path)
                        os.rename(tmp_full_path, dst_full_path)
                        copy_time(dst_full_path + FILE_LINK_SUFFIX, dst_full_path)
                        remove_file(dst_full_path + FILE_LINK_SUFFIX)
                    except Exception as e:
                        logger.warning("Can't rename to dst file %s. "
                                       "Reason: %s", dst_full_path, e)
                        try:
                            remove_file(tmp_full_path)
                        except Exception:
                            pass
                        raise e
                else:
                    create_empty_file(hard_path)
                    if not is_offline:
                        self.write_events_file_id(hard_path, events_file_id)
                        set_ext_invisible(hard_path)
                    if hard_path.endswith(FILE_LINK_SUFFIX):
                        copy_time(dst_full_path, hard_path)
                        remove_file(dst_full_path)
                    else:
                        copy_time(hard_path, dst_full_path)
                        remove_file(dst_full_path + FILE_LINK_SUFFIX)

            if silent:
                file.mtime = os.stat(hard_path).st_mtime
                file.size = os.stat(hard_path).st_size
                file.file_hash = file_hash
                file.events_file_id = events_file_id
                file.was_updated = was_updated
                logger.debug("Saving file. id=%s", file.events_file_id)
                self._storage.save_file(file, session=session)
                if src_full_path and signature:
                    # create file from copy
                    self._storage.update_file_signature(file, signature)
                if was_updated:
                    self.file_modified.emit(file.relative_path, file.mtime)

            return old_hash

    def sync_events_file_id(self, file_path, events_file_id, is_folder):
        full_path = self._path_converter.create_abspath(file_path)
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file = self._storage.get_known_file(full_path,
                                                is_folder=is_folder,
                                                session=session)
            if file:
                file.events_file_id = events_file_id
                self._storage.save_file(file, session=session)
            else:
                logger.warning("Can't sync events_file_id for path %s",
                               file_path)

    def sync_events_file_id_by_old_id(self, events_file_id, old_events_file_id):
        with self._storage.create_session(read_only=False,
                                          locked=True) as session:
            file, _ = self._get_file_by_id(old_events_file_id, session)
            if file:
                file.events_file_id = events_file_id
                self._storage.save_file(file, session=session)
            else:
                logger.debug("Can't sync events_file_id for old_id %s",
                             old_events_file_id)

    def _get_file_by_id(self, events_file_id, session):
        file = full_path = None
        if events_file_id:
            file = self._storage.get_known_file_by_id(
                events_file_id, session)
            if file:
                full_path = self._path_converter.create_abspath(
                    file.relative_path)
            else:
                logger.warning("Can't find file by id %s", events_file_id)
        return file, full_path

    def _check_paths_exist(self, src_full_path, dst_full_path,
                           already_exists, file_not_found):
        if exists(dst_full_path):
            if exists(src_full_path):
                if already_exists:
                    raise already_exists(dst_full_path)
                else:
                    return False
            else:
                logger.debug("Destination exists %s, source does not exist %s."
                             " Moving accepted", dst_full_path, src_full_path)
                return False

        if not exists(src_full_path):
            if file_not_found:
                raise file_not_found(src_full_path)
            else:
                return False

        return True

    def delete_old_signatures(self, signatures_dir, delete_all=False):
        # we believe that signatires dir contains only signature files
        # and no subdirs
        try:
            signatures_to_delete = os.listdir(signatures_dir)
        except Exception as e:
            logger.warning("Can't delete old signatures. Reason: %s", e)
            return

        if not delete_all:
            # taking storage lock to prevent adding new signatures
            # during deletion
            with self._storage.create_session(read_only=False,
                                              locked=True) as session:
                signatures_to_delete = filter(
                    lambda h:
                    not self._storage.hash_in_storage(h, session=session),
                    signatures_to_delete)

        try:
            list(map(lambda s: remove_file(join(signatures_dir, s)),
                     signatures_to_delete))
        except Exception as e:
            logger.warning("Can't delete old signatures. Reason: %s", e)

    def _init_temp_dir(self):
        self._temp_dir = get_temp_dir(self._root)
        if exists(self._temp_dir):
            try:
                remove_dir(self._temp_dir)
            except Exception as e:
                logger.warning("Can't remove temp dir. Reason: %s", e)

        self._temp_dir = get_temp_dir(self._root, create=True)

    def _raise_access_denied(self, full_path):
        self.access_denied(full_path)
        raise self._exceptions.AccessDenied(full_path)

    def get_hard_path(self, full_path, is_offline=True):
        suffix = "" if is_offline else FILE_LINK_SUFFIX
        return full_path + suffix

    def write_events_file_id(self, hard_path, events_file_id):
        with open(hard_path, 'wb') as f:
            pickle.dump(events_file_id, f)
