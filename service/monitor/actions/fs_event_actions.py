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
from PySide2.QtCore import QObject, QTimer
from PySide2.QtCore import Signal as pyqtSignal

from service.monitor.actions.calculate_hash_action \
    import CalculateHashAction
from service.monitor.actions.calculate_signature_action \
    import CalculateSignatureAction
from service.monitor.actions.check_file_mtime_or_size_changed_action \
    import CheckFileMTimeOrSizeChangedAction
from service.monitor.actions.check_if_hash_changed_action \
    import CheckIfHashChangedAction
from service.monitor.actions.check_if_signature_changed_action \
    import CheckIfSignatureChangedAction
from service.monitor.actions.check_file_move_event_action \
    import CheckFileMoveEventAction
from service.monitor.actions.check_parent_folder_created_action \
    import CheckParentFolderCreatedAction
from service.monitor.actions.check_parent_folder_deleted_action \
    import CheckParentFolderDeletedAction
from service.monitor.actions.delay_action \
    import DelayAction
from service.monitor.actions.delete_file_copy_reference_action \
    import DeleteFileCopyReferenceAction
from service.monitor.actions.delete_file_recent_copy_action \
    import DeleteFileRecentCopyAction
from service.monitor.actions.detect_single_file_event_type_action \
    import DetectSingleFileEventTypeAction
from service.monitor.actions.ignore_folder_modify_event_action \
    import IgnoreFolderModifyEventAction
from service.monitor.actions.load_info_from_storage_action \
    import LoadInfoFromStorageAction
from service.monitor.actions.make_file_recent_copy_action \
    import MakeFileRecentCopyAction
from service.monitor.actions.move_file_recent_copy_action \
    import MoveFileRecentCopyAction
from service.monitor.actions.notify_if_created_action \
    import NotifyIfCreatedAction
from service.monitor.actions.notify_if_deleted_action \
    import NotifyIfDeletedAction
from service.monitor.actions.notify_if_moved_action \
    import NotifyIfMovedAction
from service.monitor.actions.update_storage_action \
    import UpdateStorageAction
from service.monitor.actions.check_long_path_action \
    import CheckLongPathAction
from service.monitor.actions.notify_if_modified_action \
    import NotifyIfModifiedAction
from service.monitor.actions.save_file_mtime_and_size_action \
    import SaveFileMtimeAndSizeAction
from common.signal import Signal
from ..fs_event import FsEvent
from common.file_path import FilePath


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FsEventActions(QObject):
    start = pyqtSignal()

    timer_interval = 1 * 1000

    def __init__(self,
                 root,
                 events_processing_delay,
                 path_converter,
                 storage,
                 copies_storage,
                 rsync,
                 tracker=None,
                 parent=None,
                 max_relpath_len=3096,
                 timer=None):
        QObject.__init__(self, parent=parent)
        logger.debug("Init")
        self._active = True
        self._define_signals()
        self._long_paths = set()

        self._init_actions(
            root, events_processing_delay,
            path_converter, storage, copies_storage, rsync,
            tracker, max_relpath_len, self._long_paths)

        self._actions_timer = QTimer(self)
        self._actions_timer.setInterval(self.timer_interval)
        self._actions_timer.timeout.connect(self._dispatch)

        self.start.connect(self._start)
        self._connect_signals_and_slots()

    def on_initial_sync_started(self):
        logger.debug("on_initial_sync_started")
        self._active = False

    def on_initial_sync_finished(self):
        logger.debug("on_initial_sync_finished")
        self._active = True

    def _define_signals(self):
        self.event_passed = Signal(FsEvent)
        self.event_suppressed = Signal(FsEvent)
        self.event_is_handled = Signal(FsEvent)
        self.event_processed = Signal(FsEvent)
        self.error_happens = Signal(Exception)

    def _init_actions(self,
                      root,
                      events_processing_delay,
                      path_converter,
                      storage,
                      copies_storage,
                      rsync,
                      tracker,
                      max_relpath_len,
                      long_paths):
        root = FilePath(root)
        self._actions = list()
        self._notify_if_created = NotifyIfCreatedAction(path_converter)
        self._actions.append(self._notify_if_created)
        self._delay = DelayAction(events_processing_delay, tracker)
        self._actions.append(self._delay)
        self._load_info_from_storage = LoadInfoFromStorageAction(storage)
        self._actions.append(self._load_info_from_storage)
        self._check_file_move_event = CheckFileMoveEventAction(storage)
        self._actions.append(self._check_file_move_event)
        self._check_parent_folder_deleted = \
            CheckParentFolderDeletedAction(root)
        self._actions.append(self._check_parent_folder_deleted)
        self._detect_single_file_event_type = DetectSingleFileEventTypeAction()
        self._actions.append(self._detect_single_file_event_type)
        self._check_file_mtime_or_size_changed = CheckFileMTimeOrSizeChangedAction()
        self._actions.append(self._check_file_mtime_or_size_changed)
        self._ignore_folder_modify_event = IgnoreFolderModifyEventAction()
        self._actions.append(self._ignore_folder_modify_event)
        self._check_parent_folder_created = \
            CheckParentFolderCreatedAction(root, storage)
        self._actions.append(self._check_parent_folder_created)
        self._make_file_recent_copy = MakeFileRecentCopyAction(root)
        self._actions.append(self._make_file_recent_copy)
        self._calculate_signature = CalculateSignatureAction(rsync)
        self._actions.append(self._calculate_signature)
        self._check_if_signature_changed = CheckIfSignatureChangedAction()
        self._actions.append(self._check_if_signature_changed)
        self._delete_file_recent_copy = DeleteFileRecentCopyAction(root)
        self._actions.append(self._delete_file_recent_copy)
        self._calculate_hash = CalculateHashAction()
        self._actions.append(self._calculate_hash)
        self._check_if_hash_changed = CheckIfHashChangedAction()
        self._actions.append(self._check_if_hash_changed)
        self._move_file_recent_copy = \
            MoveFileRecentCopyAction(root, copies_storage)
        self._actions.append(self._move_file_recent_copy)
        self._delete_file_copy_reference = DeleteFileCopyReferenceAction(
            copies_storage)
        self._actions.append(self._delete_file_copy_reference)
        self._notify_if_deleted = NotifyIfDeletedAction(path_converter)
        self._actions.append(self._notify_if_deleted)
        self.file_deleted = self._notify_if_deleted.file_deleted
        self._notify_if_moved = NotifyIfMovedAction(path_converter)
        self._actions.append(self._notify_if_moved)
        self.file_moved = self._notify_if_moved.file_moved
        self._notify_if_modified = NotifyIfModifiedAction(path_converter)
        self._actions.append(self._notify_if_modified)
        self._update_storage = UpdateStorageAction(
            storage, path_converter, tracker)
        self._actions.append(self._update_storage)
        self._check_long_path = CheckLongPathAction(
            path_converter, max_relpath_len, long_paths)
        self._actions.append(self._check_long_path)
        self._save_mtime_and_size = SaveFileMtimeAndSizeAction(storage)
        self._actions.append(self._save_mtime_and_size)

    def _connect_signals_and_slots(self):
        self._connect_delay_signals()
        self._connect_load_info_from_storage_signals()
        self._connect_check_file_move_event_signals()
        self._connect_check_parent_folder_deleted_signals()
        self._connect_detect_single_file_event_type_signals()
        self._connect_check_file_mtime_or_size_changed_signals()
        self._connect_ignore_folder_modify_event_signals()
        self._connect_check_parent_folder_created_signals()
        self._connect_make_file_recent_copy_signals()
        self._connect_calculate_signature_signals()
        self._connect_check_if_signature_changed_signals()
        self._connect_calculate_hash_signals()
        self._connect_check_if_hash_changed_signals()
        self._connect_move_file_recent_copy_signals()
        self._connect_update_storage_signals()
        self._connect_check_long_path_signals()
        self._connect_save_mtime_and_size_signals()

        self.idle = self._delay.idle
        self.working = self._delay.working
        self.file_added_to_ignore = self._check_long_path.long_path_added
        self.file_removed_from_ignore = self._check_long_path.long_path_removed
        self.file_added_to_indexing = self._delay.file_added_to_indexing
        self.file_removed_from_indexing = self._delay.file_removed_from_indexing
        self.file_added = self._notify_if_created.file_added
        self.file_modified = self._notify_if_modified.file_modified
        self.no_disk_space = self._make_file_recent_copy.no_disk_space
        self.copy_added = self._move_file_recent_copy.copy_added
        self.set_waiting = self._update_storage.set_waiting
        self.rename_file = self._load_info_from_storage.rename_file

    def _disconnect_signals_and_slots(self):
        for action in self._actions:
            action.event_suppressed.disconnect_all()
            action.event_returned.disconnect_all()
            if action is not self._update_storage:
                action.event_passed.disconnect_all()
            action.event_spawned.disconnect_all()

    def _connect_delay_signals(self):
        self._delay.event_suppressed.connect(
            self._error_happens)
        self._delay.event_returned.connect(
            self._event_returned)
        self._delay.event_passed.connect(
            self._load_info_from_storage.add_new_event)
        self._delay.event_passed.connect(
            self._notify_if_created.add_new_event)
        self._delay.event_spawned.connect(
            self._error_happens)

    def _connect_load_info_from_storage_signals(self):
        self._load_info_from_storage.event_suppressed.connect(
            self._event_suppressed)
        self._load_info_from_storage.event_returned.connect(
            self._event_returned)
        self._load_info_from_storage.event_passed.connect(
            self._check_file_move_event.add_new_event)
        self._load_info_from_storage.event_spawned.connect(
            self._error_happens)

    def _connect_check_file_move_event_signals(self):
        self._check_file_move_event.event_suppressed.connect(
            self._event_suppressed)
        self._check_file_move_event.event_returned.connect(
            self._event_returned)
        self._check_file_move_event.event_passed.connect(
            self._check_parent_folder_deleted.add_new_event)
        self._check_file_move_event.event_spawned.connect(
            self._delay.add_new_event)

    def _connect_check_parent_folder_deleted_signals(self):
        self._check_parent_folder_deleted.event_suppressed.connect(
            self._event_suppressed)
        self._check_parent_folder_deleted.event_returned.connect(
            self._event_returned)
        self._check_parent_folder_deleted.event_passed.connect(
            self._detect_single_file_event_type.add_new_event)
        self._check_parent_folder_deleted.event_spawned.connect(
            self._delay.add_new_event)

    def _connect_detect_single_file_event_type_signals(self):
        self._detect_single_file_event_type.event_suppressed.connect(
            self._event_suppressed)
        self._detect_single_file_event_type.event_returned.connect(
            self._error_happens)
        self._detect_single_file_event_type.event_passed.connect(
            self._check_file_mtime_or_size_changed.add_new_event)
        self._detect_single_file_event_type.event_spawned.connect(
            self._error_happens)

    def _connect_check_file_mtime_or_size_changed_signals(self):
        self._check_file_mtime_or_size_changed.event_suppressed.connect(
            self._event_suppressed)
        self._check_file_mtime_or_size_changed.event_returned.connect(
            self._error_happens)
        self._check_file_mtime_or_size_changed.event_passed.connect(
            self._ignore_folder_modify_event.add_new_event)
        self._check_file_mtime_or_size_changed.event_spawned.connect(
            self._error_happens)

    def _connect_ignore_folder_modify_event_signals(self):
        self._ignore_folder_modify_event.event_suppressed.connect(
            self._event_suppressed)
        self._ignore_folder_modify_event.event_returned.connect(
            self._error_happens)
        self._ignore_folder_modify_event.event_passed.connect(
            self._check_parent_folder_created.add_new_event)
        self._ignore_folder_modify_event.event_spawned.connect(
            self._error_happens)

    def _connect_check_parent_folder_created_signals(self):
        self._check_parent_folder_created.event_suppressed.connect(
            self._error_happens)
        self._check_parent_folder_created.event_returned.connect(
            self._event_returned)
        self._check_parent_folder_created.event_passed.connect(
            self._make_file_recent_copy.add_new_event)
        self._check_parent_folder_created.event_spawned.connect(
            self._delay.add_new_event)

    def _connect_make_file_recent_copy_signals(self):
        self._make_file_recent_copy.event_suppressed.connect(
            self._delete_file_recent_copy.add_new_event)
        self._make_file_recent_copy.event_suppressed.connect(
            self._event_suppressed)
        self._make_file_recent_copy.event_returned.connect(
            self._delete_file_recent_copy.add_new_event)
        self._make_file_recent_copy.event_returned.connect(
            self._event_returned)
        self._make_file_recent_copy.event_passed.connect(
            self._calculate_signature.add_new_event)
        self._make_file_recent_copy.event_spawned.connect(
            self._error_happens)

    def _connect_calculate_signature_signals(self):
        self._calculate_signature.event_suppressed.connect(
            self._error_happens)
        self._calculate_signature.event_returned.connect(
            self._delete_file_recent_copy.add_new_event)
        self._calculate_signature.event_returned.connect(
            self._event_returned)
        self._calculate_signature.event_passed.connect(
            self._check_if_signature_changed.add_new_event)
        self._calculate_signature.event_spawned.connect(
            self._error_happens)

    def _connect_check_if_signature_changed_signals(self):
        self._check_if_signature_changed.event_suppressed.connect(
            self._delete_file_recent_copy.add_new_event)
        self._check_if_signature_changed.event_suppressed.connect(
            self._save_mtime_and_size.add_new_event)
        self._check_if_signature_changed.event_returned.connect(
            self._error_happens)
        self._check_if_signature_changed.event_passed.connect(
            self._calculate_hash.add_new_event)
        self._check_if_signature_changed.event_spawned.connect(
            self._error_happens)

    def _connect_calculate_hash_signals(self):
        self._calculate_hash.event_suppressed.connect(
            self._error_happens)
        self._calculate_hash.event_returned.connect(
            self._delete_file_recent_copy.add_new_event)
        self._calculate_hash.event_returned.connect(
            self._event_returned)
        self._calculate_hash.event_passed.connect(
            self._check_if_hash_changed.add_new_event)
        self._calculate_hash.event_spawned.connect(
            self._error_happens)

    def _connect_check_if_hash_changed_signals(self):
        self._check_if_hash_changed.event_suppressed.connect(
            self._delete_file_recent_copy.add_new_event)
        self._check_if_hash_changed.event_suppressed.connect(
            self._save_mtime_and_size.add_new_event)
        self._check_if_hash_changed.event_returned.connect(
            self._error_happens)
        self._check_if_hash_changed.event_passed.connect(
            self._move_file_recent_copy.add_new_event)
        self._check_if_hash_changed.event_spawned.connect(
            self._error_happens)

    def _connect_move_file_recent_copy_signals(self):
        self._move_file_recent_copy.event_suppressed.connect(
            self._error_happens)
        self._move_file_recent_copy.event_returned.connect(
            self._delete_file_recent_copy.add_new_event)
        self._move_file_recent_copy.event_returned.connect(
            self._event_returned)
        self._move_file_recent_copy.event_passed.connect(
            self._delete_file_recent_copy.add_new_event)
        self._move_file_recent_copy.event_passed.connect(
            self._update_storage.add_new_event)
        self._move_file_recent_copy.event_spawned.connect(
            self._error_happens)

    def _connect_update_storage_signals(self):
        self._update_storage.event_suppressed.connect(
            self._event_suppressed)
        self._update_storage.event_returned.connect(
            self._event_returned)
        self._update_storage.event_passed.connect(
            self._event_passed)
        self._update_storage.event_passed.connect(
            self._notify_if_created.add_new_event)
        self._update_storage.event_passed.connect(
            self._notify_if_deleted.add_new_event)
        self._update_storage.event_passed.connect(
            self._notify_if_moved.add_new_event)
        self._update_storage.event_passed.connect(
            self._notify_if_modified.add_new_event)
        self._update_storage.event_processed.connect(
            self._event_processed)
        self._update_storage.event_spawned.connect(
            self._error_happens)

    def _connect_check_long_path_signals(self):
        self._check_long_path.event_suppressed.connect(
            self._event_suppressed)
        self._check_long_path.event_returned.connect(
            self._error_happens)
        self._check_long_path.event_passed.connect(
            self._delay.add_new_event)
        self._check_long_path.event_spawned.connect(
            self._error_happens)

    def _connect_save_mtime_and_size_signals(self):
        self._save_mtime_and_size.event_suppressed.connect(
            self._event_suppressed)
        self._save_mtime_and_size.event_returned.connect(
            self._event_returned)
        self._save_mtime_and_size.event_passed.connect(
            self._error_happens)
        self._save_mtime_and_size.event_spawned.connect(
            self._error_happens)

    def add_new_event(self, event):
        self._check_long_path.add_new_event(event)

    def _start(self):
        logger.debug("Start")
        self._connect_signals_and_slots()
        for action in self._actions:
            action.set_active(True)
        self._delay.start()
        if not self._actions_timer.isActive():
            self._actions_timer.start()
        self._long_paths.clear()

    def stop(self):
        logger.debug("Stop")
        self._disconnect_signals_and_slots()
        for action in self._actions:
            action.set_active(False)
        self._delay.stop()
        if self._actions_timer.isActive():
            self._actions_timer.stop()

    def _event_passed(self, fs_event):
        self.event_passed(fs_event)
        self.event_is_handled(fs_event)

    def _event_suppressed(self, fs_event):
        self.event_suppressed(fs_event)
        self.event_is_handled(fs_event)
        self.event_processed.emit(fs_event)

    def _event_returned(self, fs_event):
        fs_event.time = None
        fs_event.is_offline = False
        self._delay.add_new_event(fs_event)

    def _event_processed(self, fs_event):
        self.event_processed.emit(fs_event)

    def _error_happens(self, fs_event):
        self.error_happens.emit(
            Exception('FsActions event handling error: %s'.format(fs_event)))

    def is_processing(self, file_path):
        return self._delay.is_processing(file_path)

    def get_long_paths(self):
        return self._long_paths

    def _dispatch(self):
        if self._active:
            self._delay.dispatch()

    def get_fs_events_count(self):
        return self._delay.get_fs_events_count()
