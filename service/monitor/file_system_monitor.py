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
import os
import os.path as op
import glob
import shutil
import errno
from multiprocessing import freeze_support
from datetime import date
import time

from PySide2.QtCore import QThread, QObject
from PySide2.QtCore import Signal as pyqtSignal

import common.utils
from common.file_path import FilePath
from service.monitor.actions.fs_event_actions import FsEventActions
from service.monitor.fs_event import FsEvent
from service.monitor.storage.file import File
from common.signal import Signal
from common.utils \
    import remove_dir, remove_file, get_patches_dir, \
    get_copies_dir, get_signatures_dir, \
    set_custom_folder_icon, reset_custom_folder_icon, benchmark
from common.constants import HIDDEN_FILES, HIDDEN_DIRS, MAX_FILE_NAME_LEN
from common.constants import CREATE, MOVE, MODIFY, DELETE

from .local_processor import LocalProcessor
from common.path_converter import PathConverter
from .quiet_processor import QuietProcessor
from .storage import Storage
from .watchdog_handler import WatchdogHandler
from .rsync import Rsync
from common.path_utils import is_contained_in_dirs
from .files_list import FilesList
from .observer_wrapper import ObserverWrapper

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FilesystemMonitor(QObject):
    """
    Class provides all functions needed to work with filesystem
    in scope of project
    """
    max_file_name_length = MAX_FILE_NAME_LEN - 5
    selective_sync_conflict_suffix = "selective sync conflict"

    started = pyqtSignal()
    stopped = pyqtSignal()
    process_offline = pyqtSignal(bool)

    def __init__(self,
                 root,
                 events_processing_delay,
                 copies_storage,
                 get_sync_dir_size,
                 conflict_file_suffix='',
                 tracker=None,
                 storage=None,
                 excluded_dirs=(),
                 parent=None,
                 max_relpath_len=3096,
                 db_file_created_cb=None):
        QObject.__init__(self, parent=parent)
        freeze_support()

        self._tracker = tracker

        self._root = root

        self._path_converter = PathConverter(self._root)
        self._storage = storage if storage else Storage(
            self._path_converter, db_file_created_cb)
        self._copies_storage = copies_storage
        self._copies_storage.delete_copy.connect(self.on_delete_copy)
        self.possibly_sync_folder_is_removed = \
            self._storage.possibly_sync_folder_is_removed
        self.db_or_disk_full = self._storage.db_or_disk_full
        self._get_sync_dir_size = get_sync_dir_size
        self._conflict_file_suffix = conflict_file_suffix

        self._rsync = Rsync

        _hide_files = HIDDEN_FILES
        _hide_dirs = HIDDEN_DIRS

        self._clean_recent_copies()

        self._actions = FsEventActions(
            self._root,
            events_processing_delay=events_processing_delay,
            path_converter=self._path_converter,
            storage=self._storage,
            copies_storage=self._copies_storage,
            rsync=self._rsync,
            tracker=self._tracker,
            parent=None,
            max_relpath_len=max_relpath_len,
        )

        self._watch = WatchdogHandler(
            root=FilePath(self._root).longpath,
            hidden_files=_hide_files,
            hidden_dirs=_hide_dirs)

        self._download_watch = WatchdogHandler(
            root=FilePath(self._root).longpath,
            hidden_files=_hide_files,
            hidden_dirs=_hide_dirs,
            patterns=['*.download'],
            is_special=True)

        self._observer = ObserverWrapper(
            self._storage, self._get_sync_dir_size, self._tracker,
            parent=None)
        self._observer.event_handled.connect(
            self._observer.on_event_is_handled_slot)
        self._actions.event_passed.connect(
            lambda ev: self._observer.event_handled.emit(ev, False))
        self._actions.event_suppressed.connect(
            lambda ev: self._observer.event_handled.emit(ev, True))

        # Add FS root for events tracking
        self._observer.schedule(self._watch, root)

        self._local_processor = LocalProcessor(
            self._root,
            self._storage,
            self._path_converter,
            self._tracker)
        self.event_is_arrived = self._local_processor.event_is_arrived
        self._quiet_processor = QuietProcessor(
            self._root,
            self._storage,
            self._path_converter,
            self.Exceptions)

        self._files_list = FilesList(self._storage, self._root)

        self._thread = QThread()
        self._thread.started.connect(self._on_thread_started)
        self._actions.moveToThread(self._thread)
        self._observer.moveToThread(self._thread)

        self._watch.event_is_arrived.connect(self._on_event_arrived)
        self._download_watch.event_is_arrived.connect(self._on_event_arrived)
        self._actions.event_passed.connect(self._local_processor.process)

        self._local_events_flag = False
        self._actions.event_passed.connect(self._set_local_events_flag)

        self.error_happens = self._actions.error_happens
        self.no_disk_space = self._actions.no_disk_space
        self.idle = self._actions.idle
        self.working = self._actions.working
        self.file_added_to_ignore = self._actions.file_added_to_ignore
        self.file_removed_from_ignore = self._actions.file_removed_from_ignore
        self.file_added_to_indexing = self._actions.file_added_to_indexing
        self.file_removed_from_indexing = self._actions.file_removed_from_indexing
        self.file_added = self._actions.file_added
        self.file_modified = self._actions.file_modified
        self.file_deleted = Signal(str)
        self._actions.file_deleted.connect(self.file_deleted)
        self._quiet_processor.file_deleted.connect(self.file_deleted)
        self._quiet_processor.file_modified.connect(self.file_modified)
        self.file_moved = self._quiet_processor.file_moved
        self._actions.file_moved.connect(lambda o, n: self.file_moved(o, n))
        self.access_denied = self._quiet_processor.access_denied

        self.file_list_changed = self._files_list.file_list_changed
        self.file_added.connect(self._files_list.on_file_added)
        self.file_deleted.connect(self._files_list.on_file_deleted)
        self.file_moved.connect(self._files_list.on_file_moved)
        self.file_modified.connect(self._files_list.on_file_modified)
        self.idle.connect(self._files_list.on_idle)

        self.process_offline.connect(self._observer.process_offline_changes)

        self.copy_added = Signal(str)
        self._actions.copy_added.connect(self.copy_added)

        self.special_file_event = Signal(str,   # path
                                         int,       # event type
                                         str)   # new path
        self._special_files = list()
        self._excluded_dirs = list(map(FilePath, excluded_dirs))

        self._online_processing_allowed = False
        self._online_modifies_processing_allowed = False

        self._paths_with_modify_quiet = set()

    def on_initial_sync_finished(self):
        logger.debug("on_initial_sync_finished")
        self._actions.on_initial_sync_finished()
        if not self._actions.get_fs_events_count() \
                and not self._observer.is_processing_offline:
            self.idle.emit()

    def _on_processed_offline_changes(self):
        logger.debug("_on_processed_offline_changes")
        if not self._actions.get_fs_events_count():
            self.idle.emit()

    def on_initial_sync_started(self):
        logger.debug("on_initial_sync_started")
        self._actions.on_initial_sync_started()
        self._online_processing_allowed = False
        self._online_modifies_processing_allowed = False

    def start_online_processing(self):
        logger.debug("start_online_processing")
        if not self._online_processing_allowed:
            logger.debug("start_online_processing, emit process_offline")
            self.process_offline.emit(self._online_modifies_processing_allowed)
        self._online_processing_allowed = True

    def start_online_modifies_processing(self):
        logger.debug("start_online_modifies_processing")
        if not self._online_modifies_processing_allowed:
            logger.debug("start_online_modifies_processing, emit process_offline")
            self.process_offline.emit(True)
        self._online_modifies_processing_allowed = True

    def get_root(self):
        return self._root

    def root_exists(self):
        return op.isdir(self._root)

    def _on_thread_started(self):
        logger.info("Start monitoring of '%s'", self._root)
        self._observer.offline_event_occured.connect(
            self._on_event_arrived)
        self._observer.processed_offline_changes.connect(
            self._on_processed_offline_changes)
        self.started.emit()
        self._actions.start.emit()
        self._observer.start.emit()
        self._local_events_flag = False

    @benchmark
    def start(self):
        logger.debug("start")
        self._observer.set_active()
        if self._thread.isRunning():
            self._on_thread_started()
        else:
            self._thread.start()
        self._files_list.start()

    def stop(self):
        logger.info("stopped monitoring")
        try:
            self._observer.offline_event_occured.disconnect(
                self._on_event_arrived)
        except RuntimeError:
            logger.warning("Can't disconnect offline_event_occured")
        try:
            self._observer.processed_offline_changes.disconnect(
                self._on_processed_offline_changes)
        except RuntimeError:
            logger.warning("Can't disconnect processed_offline_changes")
        self._actions.stop()
        self._observer.stop()
        self._files_list.stop()
        self.stopped.emit()

    def quit(self):
        self.stop()
        self._thread.quit()
        self._thread.wait()

    def is_processing(self, file_path):
        return self._actions.is_processing(
            self._path_converter.create_abspath(file_path))

    def is_known(self, file_path):
        return self._storage.get_known_file(file_path) is not None

    def process_offline_changes(self):
        if self._local_events_flag:
            self.process_offline.emit(self._online_modifies_processing_allowed)
            self._local_events_flag = False

    def _set_local_events_flag(self, fs_event):
        if not fs_event.is_offline:
            self._local_events_flag = True

    def clean_storage(self):
        self._storage.clean()

    def clean_copies(self, with_files=True):
        self._copies_storage.clean(with_files=with_files)

    def move_files_to_copies(self):
        with self._storage.create_session(read_only=False, locked=True) as session:
            files_with_hashes = session\
                .query(File.relative_path, File.file_hash) \
                .filter(File.is_folder == 0) \
                .all()
            copies_dir = get_copies_dir(self._root)
            for (file, hashsum) in files_with_hashes:
                hash_path = op.join(copies_dir, hashsum)
                file_path = self._path_converter.create_abspath(file)
                if not op.exists(hash_path):
                    try:
                        os.rename(file_path, hash_path)
                    except Exception as e:
                        logger.error("Error moving file to copy: %s", e)
                remove_file(file_path)
        abs_path = FilePath(self._root).longpath
        folders_plus_hidden = [self._path_converter.create_abspath(f)
                   for f in os.listdir(abs_path) if f not in HIDDEN_DIRS]
        for folder in folders_plus_hidden:
            if not op.isdir(folder):
                continue

            try:
                remove_dir(folder)
            except Exception as e:
                logger.error("Error removing dir '%s' (%s)", folder, e)
        logger.info("Removed all files and folders")
        self._storage.clean()

    def clean(self):
        files = self._storage.get_known_files()
        for file in files:
            try:
                remove_file(file)
            except Exception as e:
                logger.error("Error removing file '%s' (%s)", file, e)
        folders = self._storage.get_known_folders()
        for folder in sorted(folders, key=len):
            try:
                remove_dir(folder)
            except Exception as e:
                logger.error("Error removing dir '%s' (%s)", folder, e)
        logger.info("Removed all files and folders")
        self._storage.clean()

    def accept_delete(self, path, is_directory=False, events_file_id=None):
        '''
        Processes file deletion

        @param path Name of file relative to sync directory [unicode]
        '''

        full_path = self._path_converter.create_abspath(path)
        object_type = 'directory' if is_directory else 'file'

        logger.debug("Deleting '%s' %s...", path, object_type)
        if is_directory:
            self._quiet_processor.delete_directory(full_path, events_file_id)
        else:
            self._quiet_processor.delete_file(full_path, events_file_id)
        self.file_removed_from_indexing.emit(FilePath(full_path), True)

        logger.info("'%s' %s is deleted", path, object_type)

    def set_patch_uuid(self, patch_path, diff_file_uuid):
        shutil.move(patch_path, self.get_patch_path(diff_file_uuid))

    def get_patch_path(self, diff_file_uuid):
        return os.path.join(get_patches_dir(self._root), diff_file_uuid)

    def create_directory(self, path, events_file_id):
        full_path = self._path_converter.create_abspath(path)
        try:
            self._quiet_processor.create_directory(
                full_path, events_file_id=events_file_id,
                wrong_file_id=self.Exceptions.WrongFileId)
        except AssertionError:
            self._on_event_arrived(FsEvent(
                DELETE, op.dirname(full_path), True, is_offline=True, quiet=True))
            raise

    def apply_patch(self, filename, patch, new_hash, old_hash, events_file_id):
        '''
        Applies given patch for the file specified

        @param filename Name of file relative to sync directory [unicode]
        @param patch Patch data [dict]
        '''

        full_fn = self._path_converter.create_abspath(filename)

        try:
            self._apply_patch(full_fn, patch, new_hash, old_hash,
                          events_file_id=events_file_id)
        except AssertionError:
            self._on_event_arrived(FsEvent(
                DELETE, op.dirname(full_fn), True, is_offline=True, quiet=True))
            raise

    def accept_move(self, src, dst, is_directory=False, events_file_id=None):
        src_full_path = self._path_converter.create_abspath(src)
        dst_full_path = self._path_converter.create_abspath(dst)

        try:
            object_type = 'directory' if is_directory else 'file'
            logger.debug(
                "Moving '%s' %s to '%s'...", src, object_type, dst)
            if is_directory:
                self._quiet_processor.move_directory(
                    src_full_path, dst_full_path, events_file_id,
                    self.Exceptions.FileAlreadyExists,
                    self.Exceptions.FileNotFound,
                    wrong_file_id=self.Exceptions.WrongFileId)
            else:
                self._quiet_processor.move_file(
                    src_full_path, dst_full_path, events_file_id,
                    self.Exceptions.FileAlreadyExists,
                    self.Exceptions.FileNotFound,
                    wrong_file_id=self.Exceptions.WrongFileId)
            logger.info(
                "'%s' %s is moved to '%s'", src, object_type, dst)
            self.file_removed_from_indexing.emit(FilePath(src_full_path), True)
        except AssertionError:
            self._on_event_arrived(FsEvent(
                DELETE, op.dirname(dst_full_path), True,
                is_offline=True, quiet=True))
            raise

    def change_events_file_id(self, old_id, new_id):
        self._storage.change_events_file_id(old_id, new_id)

    class Exceptions(object):
        """ User-defined exceptions are stored here """

        class FileNotFound(Exception):
            """ File doesn't exist exception"""

            def __init__(self, file):
                self.file = file

            def __str__(self):
                return repr(self.file)

        class FileAlreadyExists(Exception):
            """ File already exists exception (for move) """

            def __init__(self, path):
                self.path = path

            def __str__(self):
                return "File already exists {}".format(self.path)

        class AccessDenied(Exception):
            """ Access denied exception (for move or delete) """

            def __init__(self, path):
                self.path = path

            def __str__(self):
                return "Access denied for {}".format(self.path)

        class WrongFileId(Exception):
            """ Wrong file if exception """

            def __init__(self, path, file_id_expected=None, file_id_got=None):
                self.path = path
                self.file_id_expected = file_id_expected
                self.file_id_got= file_id_got

            def __str__(self):
                return "Wrong file id for {}. Expected id {}. Got id {}".format(
                    self.path, self.file_id_expected, self.file_id_got)

        class CopyDoesNotExists(Exception):
            def __init__(self, hash):
                self.hash = hash

            def __str__(self):
                return "Copy with hash {} does not exists".format(
                    self.hash)

    def _apply_patch(self, filename, patch, new_hash, old_hash, silent=True,
                     events_file_id=None):
        start_time = time.time()
        patch_size = os.stat(patch).st_size
        success = False
        try:
            patched_new_hash, old_hash = self._quiet_processor.patch_file(
                filename,
                patch,
                silent=silent,
                events_file_id=events_file_id,
                wrong_file_id=self.Exceptions.WrongFileId)
            assert patched_new_hash == new_hash
            success = True
            self.copy_added.emit(new_hash)
        except Rsync.AlreadyPatched:
            success = True
        except:
            raise
        finally:
            if self._tracker:
                try:
                    file_size = os.stat(filename).st_size
                except OSError:
                    file_size = 0
                duration = time.time() - start_time
                self._tracker.monitor_patch_accept(file_size, patch_size,
                                                   duration, success)

    def generate_conflict_file_name(self, filename, is_folder=False,
                                    name_suffix=None, with_time=True):
        orig_filename = filename
        directory, filename = op.split(filename)
        original_ext = ''
        if is_folder:
            original_name = filename
        else:
            # consider ext as 2 '.'-delimited last filename substrings
            # if they don't contain spaces
            dots_list = filename.split('.')
            name_parts_len = len(dots_list)
            for k in range(1, min(name_parts_len, 3)):
                if ' ' in dots_list[-k]:
                    break

                original_ext = '.{}{}'.format(dots_list[k], original_ext)
                name_parts_len -= 1
            original_name = '.'.join(dots_list[:name_parts_len])

        index = 0
        if name_suffix is None:
            name_suffix = self._conflict_file_suffix
        date_today = date.today().strftime('%d-%m-%y') if with_time else ''
        suffix = '({} {})'.format(name_suffix, date_today)
        while len(bytes(suffix.encode('utf-8'))) > \
                int(self.max_file_name_length / 3):
            suffix = suffix[int(len(suffix) / 2):]

        name = '{}{}{}'.format(original_name,
                                suffix,
                                original_ext)
        while True:
            to_cut = len(bytes(name.encode('utf-8'))) - \
                     self.max_file_name_length
            if to_cut <= 0:
                break
            if len(original_name) > to_cut:
                original_name = original_name[:-to_cut]
            else:
                remained = to_cut - len(original_name) + 1
                original_name = original_name[:1]
                if remained < len(original_ext):
                    original_ext = original_ext[remained:]
                else:
                    original_ext = original_ext[int(len(original_ext) / 2):]
            name = '{}{}{}'.format(original_name,
                                    suffix,
                                    original_ext)

        while op.exists(self._path_converter.create_abspath(
                FilePath(op.join(directory, name)))):
            index += 1
            name = '{}{} {}{}'.format(original_name,
                                            suffix,
                                            index,
                                            original_ext)
        conflict_file_name = FilePath(op.join(directory, name))
        logger.info("Generated conflict file name: %s, original name: %s, "
                    "is_folder: %s, name_suffix: %s, with_time: %s",
                    conflict_file_name, orig_filename, is_folder, name_suffix, with_time)
        return conflict_file_name

    def move_file(self, src, dst):
        src_full_path = self._path_converter.create_abspath(src)
        dst_full_path = self._path_converter.create_abspath(dst)

        if not op.exists(src_full_path):
            raise self.Exceptions.FileNotFound(src_full_path)
        elif op.exists(dst_full_path):
            raise self.Exceptions.FileAlreadyExists(dst_full_path)

        dst_parent_folder_path = op.dirname(dst_full_path)
        if not op.exists(dst_parent_folder_path):
            self._on_event_arrived(FsEvent(
                DELETE, dst_parent_folder_path, True, is_offline=True, quiet=True))

        try:
            os.rename(src_full_path, dst_full_path)
        except OSError as e:
            logger.warning("Can't move file (dir) %s. Reason: %s",
                           src_full_path, e)
            if e.errno == errno.EACCES:
                self._quiet_processor.access_denied()
                raise self.Exceptions.AccessDenied(src_full_path)
            else:
                raise e

    def copy_file(self, src, dst, is_directory=False):
        src_full_path = self._path_converter.create_abspath(src)
        dst_full_path = self._path_converter.create_abspath(dst)

        if not op.exists(src_full_path):
            raise self.Exceptions.FileNotFound(src_full_path)

        if is_directory:
            shutil.copytree(src_full_path, dst_full_path)
        else:
            common.utils.copy_file(src_full_path, dst_full_path)

    def restore_file_from_copy(self, file_name, copy_hash, events_file_id,
                               search_by_id=False):
        try:
            old_hash = self._quiet_processor.create_file_from_copy(
                file_name, copy_hash, silent=True, events_file_id=events_file_id,
                search_by_id=search_by_id,
                wrong_file_id=self.Exceptions.WrongFileId,
                copy_does_not_exists=self.Exceptions.CopyDoesNotExists)
        except AssertionError:
            self._on_event_arrived(FsEvent(
                DELETE, op.dirname(
                    self._path_converter.create_abspath(file_name)),
                True, is_offline=True, quiet=True))
            raise

        return old_hash

    def create_file_from_copy(
            self, file_name, copy_hash, events_file_id, search_by_id=False):
        self.restore_file_from_copy(file_name, copy_hash,
                                    events_file_id=events_file_id,
                                    search_by_id=search_by_id)

    @benchmark
    def make_copy_from_existing_files(self, copy_hash):
        self._quiet_processor.make_copy_from_existing_files(copy_hash)

    def create_empty_file(self, file_name, file_hash, events_file_id,
                          search_by_id=False):
        try:
            self._quiet_processor.create_empty_file(
                file_name, file_hash, silent=True,
                events_file_id=events_file_id,
                search_by_id=search_by_id,
                wrong_file_id=self.Exceptions.WrongFileId)
        except AssertionError:
            self._on_event_arrived(FsEvent(
                DELETE, op.dirname(
                    self._path_converter.create_abspath(file_name)),
                True, is_offline=True, quiet=True))
            raise

    def on_delete_copy(self, hash, with_signature=True):
        if not hash:
            logger.error("Invalid hash '%s'", hash)
            return
        copy = op.join(get_copies_dir(self._root), hash)
        try:
            remove_file(copy)
            logger.info("File copy deleted %s", copy)
            if not with_signature:
                return

            signature = op.join(get_signatures_dir(self._root), hash)
            remove_file(signature)
            logger.info("File copy signature deleted %s", signature)
        except Exception as e:
            logger.error("Can't delete copy. "
                         "Possibly sync folder is removed %s", e)
            self.possibly_sync_folder_is_removed()

    def delete_old_signatures(self, delete_all=False):
        logger.debug("Deleting old signatures...")
        self._quiet_processor.delete_old_signatures(
            get_signatures_dir(self._root), delete_all)

    def path_exists(self, path):
        full_path = self._path_converter.create_abspath(path)
        return op.exists(full_path)

    def rename_excluded(self, rel_path):
        logger.debug("Renaming excluded dir %s", rel_path)
        new_path = self.generate_conflict_file_name(
            rel_path,
            name_suffix=self.selective_sync_conflict_suffix,
            with_time=False)
        self.move_file(rel_path, new_path)

    def db_file_exists(self):
        return self._storage.db_file_exists()

    def _clean_recent_copies(self):
        mask = op.join(get_copies_dir(self._root), "*.recent_copy_[0-9]*")
        recent_copies = glob.glob(mask)
        list(map(os.remove, recent_copies))

    def add_special_file(self, path):
        self._special_files.append(path)
        watch = None
        if not (path in FilePath(self._root)):
            watch = self._download_watch
        self._observer.add_special_file(path, watch)

    def remove_special_file(self, path):
        logger.debug("Removing special file %s...", path)
        if not (path in FilePath(self._root)):
            self._observer.remove_special_file(path)
        try:
            self._special_files.remove(path)
        except ValueError:
            logger.warning("Can't remove special file %s from list %s",
                           path, self._special_files)

    def change_special_file(self, old_file, new_file):
        self.add_special_file(new_file)
        self.remove_special_file(old_file)

    def _on_event_arrived(self, fs_event, is_special=False):
        logger.debug(
            "Event arrived %s, special %s, online_processing_allowed: %s, "
            "online_modifies_processing_allowed: %s",
            fs_event, is_special, self._online_processing_allowed,
            self._online_modifies_processing_allowed)
        if is_special or fs_event.src in self._special_files:
            self.special_file_event.emit(
                fs_event.src, fs_event.event_type, fs_event.dst)
        elif fs_event.is_offline or self._online_processing_allowed:
            if not self._online_modifies_processing_allowed and \
                    not fs_event.is_offline and fs_event.event_type == MODIFY:
                return
            elif fs_event.src in self._paths_with_modify_quiet \
                    and fs_event.event_type in (CREATE, MODIFY):
                fs_event.is_offline = True
                fs_event.quiet = True

            path = fs_event.src if fs_event.event_type == CREATE \
                else fs_event.dst if fs_event.event_type == MOVE else ""
            name = op.basename(path)
            parent_path = op.dirname(path)
            stripped_name = name.strip()
            if stripped_name != name:
                new_path = op.join(parent_path, stripped_name)
                if op.exists(new_path):
                    new_path = self.generate_conflict_file_name(
                        new_path,
                        is_folder=fs_event.is_dir, name_suffix="",
                        with_time=True)
                logger.debug("Renaming '%s' to '%s'...", path, new_path)
                os.rename(FilePath(path).longpath, FilePath(new_path).longpath)

                path = new_path

                if fs_event.event_type == CREATE:
                    fs_event.src = new_path
                elif fs_event.event_type == MOVE:
                    fs_event.dst = new_path

            hidden_dir = FilePath(
                self._path_converter.create_abspath(HIDDEN_DIRS[0]))
            if fs_event.event_type == MOVE:
                if FilePath(fs_event.src) in hidden_dir or \
                        op.basename(fs_event.src).startswith('._'):
                    fs_event.event_type = CREATE
                    fs_event.src = fs_event.dst
                    fs_event.dst = None
                elif FilePath(fs_event.dst) in hidden_dir or \
                        op.basename(fs_event.dst).startswith('._'):
                    fs_event.event_type = DELETE
                    fs_event.dst = None
            if FilePath(fs_event.src) in hidden_dir or \
                    op.basename(fs_event.src).startswith('._'):
                return

            if FilePath(path) in self._excluded_dirs:
                self.rename_excluded(
                    self._path_converter.create_relpath(path))
            else:
                self._actions.add_new_event(fs_event)

    def get_long_paths(self):
        return self._actions.get_long_paths()

    def set_excluded_dirs(self, excluded_dirs):
        self._excluded_dirs = list(map(FilePath, excluded_dirs))

    def remove_dir_from_excluded(self, directory):
        try:
            self._excluded_dirs.remove(directory)
        except Exception as e:
            logger.warning("Can't remove excluded dir %s from %s. Reason: %s",
                           directory, self._excluded_dirs, e)

    def sync_events_file_id(self, file_path, events_file_id, is_folder):
        self._quiet_processor.sync_events_file_id(
            file_path, events_file_id, is_folder)

    def sync_events_file_id_by_old_id(self, events_file_id,
                                      old_events_file_id):
        self._quiet_processor.sync_events_file_id_by_old_id(
            events_file_id, old_events_file_id)

    def set_collaboration_folder_icon(self, folder_name):
        set_custom_folder_icon('collaboration',
                               self._root, folder_name)

    def reset_collaboration_folder_icon(self, folder_name):
        reset_custom_folder_icon(self._root, folder_name,
                                 resource_name='collaboration')

    def reset_all_collaboration_folder_icons(self):
        root_folders = [f for f in os.listdir(self._root)
                        if op.isdir(self._path_converter.create_abspath(f))]
        logger.debug("root_folders %s", root_folders)
        list(map(self.reset_collaboration_folder_icon, root_folders))

    def get_excluded_dirs_to_change(self, excluded_dirs, src_path, dst_path=None):
        src_path = FilePath(src_path)
        if dst_path:
            dst_path = FilePath(dst_path)
        excluded_dirs = list(map(FilePath, excluded_dirs))
        dirs_to_add = []
        dirs_to_delete = list(filter(lambda ed: ed in src_path, excluded_dirs))
        if dst_path is not None and \
                not is_contained_in_dirs(dst_path, excluded_dirs):
            # we have to add new excluded dirs only if folder is not moved
            # to excluded dir
            l = len(src_path)
            dirs_to_add = [dst_path + d[l:] for d in dirs_to_delete]
        logger.debug("get_excluded_dirs_to_change. "
                     "excluded_dirs %s, src_path %s, dst_path %s, "
                     "dirs_to_delete %s, dirs_to_add %s",
                     excluded_dirs, src_path, dst_path,
                     dirs_to_delete, dirs_to_add)
        return dirs_to_delete, dirs_to_add

    def change_excluded_dirs(self, dirs_to_delete, dirs_to_add):
        for directory in dirs_to_delete:
            self.remove_dir_from_excluded(directory)
        for directory in dirs_to_add:
            self._excluded_dirs.append(directory)

    def clear_excluded_dirs(self):
        self._excluded_dirs = []

    def get_fs_events_count(self):
        return self._actions.get_fs_events_count()

    def force_create_copies(self):
        self._storage.clear_files_hash_mtime()
        self.delete_old_signatures(delete_all=True)
        self._local_events_flag = True
        self.process_offline_changes()

    def get_file_list(self):
        return self._files_list.get()

    def get_actual_events_file_id(self, path, is_folder=None):
        abs_path = self._path_converter.create_abspath(path)
        file = self._storage.get_known_file(abs_path, is_folder=is_folder)
        return file.events_file_id if file else None

    def is_directory(self, path):
        abs_path = self._path_converter.create_abspath(path)
        return op.isdir(abs_path)

    def set_waiting(self, to_wait):
        self._actions.set_waiting(to_wait)

    def set_path_quiet(self, path):
        logger.debug("Setting path %s quiet...", path)
        self._paths_with_modify_quiet.add(FilePath(path))

    def clear_paths_quiet(self):
        logger.debug("Clearing quiet paths...")
        self._paths_with_modify_quiet.clear()

    def delete_files_with_empty_events_file_ids(self):
        if self._storage.delete_files_with_empty_events_file_ids():
            self.working.emit()

    def is_file_in_storage(self, events_file_id):
        return self._storage.get_known_file_by_id(events_file_id)