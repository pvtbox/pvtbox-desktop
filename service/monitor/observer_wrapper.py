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
import time
from collections import defaultdict
import os.path as op
from threading import RLock

from PySide2.QtCore import Signal as pyqtSignal, QObject
from watchdog.observers import Observer

from common.file_path import FilePath
from common.utils import get_files_dir_list
from common.constants import DELETE, CREATE, MODIFY, event_names
from service.monitor.fs_event import FsEvent
from common.signal import Signal


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ObserverWrapper(QObject):
    """
    Wrapper for watchdog's Observer performing checks for offline events
    on observing start
    """


    event_handled = pyqtSignal(FsEvent, bool)

    start = pyqtSignal()

    processed_offline_changes = pyqtSignal()

    def __init__(self, storage, get_sync_dir_size, tracker=None, parent=None):
        """
        Constructor
        @param storage Storage class instance [Storage]
        @param tracker
            Statictics event tracker instance [stat_tracking.Tracker] or None
        """
        QObject.__init__(self, parent=parent)
        self._storage = storage
        self._observer = None
        self._active = True
        self._root_handlers = {}
        self._tracker = tracker
        self._reset_stats()
        self._started = False
        self.is_processing_offline = False
        self._processed_offline_changes = False
        self._get_sync_dir_size = get_sync_dir_size
        self._special_dirs = dict()
        self._special_files = set()
        self._lock = RLock()
        self.start.connect(self._start)

        # Signal to be emitted when detecting offline changes
        self.offline_event_occured = Signal(FsEvent, bool)

    def has_processed_offline_changes(self):
        return self._processed_offline_changes

    def _reset_stats(self):
        self._start_time = 0
        self._offline_stats_count = 0
        self._online_stats = defaultdict(int)
        self._offline_stats = defaultdict(int)
        self._start_stats_sended = False

    def set_active(self, active=True):
        logger.debug("set_active: %s", active)
        self._active = active

    def _start(self):
        """
        Starts monitoring of roots added with schedule().
        Creates wrapped observer instance
        """
        if not self._active:
            return
        logger.debug("Start")
        self._reset_stats()
        # Initialize observer
        self._observer = Observer()
        self._observer.start()
        # Detect offline changes (if any)
        self.is_processing_offline = False
        self._processed_offline_changes = False
        self._started = True

        self.process_offline_changes(process_modifies=False)
        # Register roots added previously
        with self._lock:
            if not self._active or not self._started:
                return
            for root, (event_handler, recursive) in self._root_handlers.items():
                self._schedule_root(event_handler, root, recursive)

    def _schedule_root(self, event_handler, root, recursive):
        logger.info("Starting watching root '%s'...", root)
        if not self._observer:
            return
        return self._observer.schedule(
            event_handler, root, recursive=recursive)

    def stop(self):
        """
        Stops monitoring of roots added with schedule()
        """

        logger.debug("Stop")
        self._active = False
        if not self._started:
            logger.warning("Already stopped")
            return

        logger.info("Stop watching all roots")
        self._started = False
        self._processed_offline_changes = False
        try:
            self._observer.unschedule_all()
            self._observer.stop()
            self._observer.join()
        except TypeError as e:
            logger.error('Exception while stopping fs observer: %s', e)
        self._observer = None

    def schedule(self, event_handler, root, recursive=True):
        """
        Register given event handler to be used for events from given root path

        @param event_handler
            Observer class instance [watchdog.observers.BaseObserver]
        @param root Path (absolute) to process event from [unicode]
        @param recursive
            Flag enabling processing events from nested folders/files [bool]
        """

        root = FilePath(root).longpath
        self._root_handlers[root] = (event_handler, recursive)
        watch = None
        if self._started:
            watch = self._schedule_root(event_handler, root, recursive)
        return watch

    def process_offline_changes(self, process_modifies):
        logger.debug("process_offline_changes")
        if self.is_processing_offline or not self._started:
            logger.debug(
                "process_offline_changes, already processing offline changes")
            return

        self.is_processing_offline = True
        for root in self._root_handlers.copy():
            self._check_root(root, process_modifies)

    def _check_root(self, root, process_modifies):
        """
        Check given root path for offline events

        @param root Path (absolute) to be checked [unicode]
        """
        if FilePath(root) in self._special_dirs:
            return

        logger.info(
            "Checking root '%s' folder for offline changes...", root)

        self._start_time = time.time()

        logger.debug("Obtaining known files from storage...")
        known_files = set(self._storage.get_known_files())
        if not self._active or not self._started:
            return
        logger.debug("Known files: %s", len(known_files))

        logger.debug("Obtaining actual files and folders from filesystem...")
        actual_folders, actual_files = get_files_dir_list(
            root,
            exclude_dirs=self._root_handlers[root][0].hidden_dirs,
            exclude_files=self._root_handlers[root][0].hidden_files)
        if not self._active or not self._started:
            return
        logger.debug("Actual folders: %s", len(actual_folders))
        logger.debug("Actual files: %s", len(actual_files))

        actual_files = set(map(FilePath, actual_files)) - self._special_files
        actual_folders = set(map(FilePath, actual_folders))

        if not self._active or not self._started:
            return

        self._offline_stats['file_COUNT'] = len(actual_files)

        logger.debug("Finding files that were created...")
        files_created = actual_files.difference(known_files)
        if not self._active or not self._started:
            return

        logger.debug("Finding files that were deleted...")
        files_deleted = known_files.difference(actual_files)
        if not self._active or not self._started:
            return

        logger.debug("Obtaining known folders from storage...")
        known_folders = set(self._storage.get_known_folders())

        if not self._active or not self._started:
            return
        logger.debug("Known folders: %s", len(known_folders))

        logger.debug("Finding folders that were created...")
        folders_created = sorted(
            actual_folders.difference(known_folders),
            key=len, reverse=True)
        if not self._active or not self._started:
            return

        logger.debug("Finding folders that were deleted...")
        folders_deleted = sorted(
            known_folders.difference(actual_folders),
            key=len, reverse=True)

        if not self._active or not self._started:
            return

        logger.info(
            "Folders found: %s (created: %s, deleted: %s)",
            len(actual_folders), len(folders_created), len(folders_deleted))

        self._offline_stats['dir_COUNT'] = len(actual_folders)

        logger.debug("Appending deleted files to processing...")
        for filename in files_deleted:
            if not self._active or not self._started:
                return
            self._emit_offline_event(FsEvent(
                event_type=DELETE,
                src=filename,
                is_dir=False,
                is_offline=True))

        logger.debug("Appending deleted folders to processing...")
        for foldername in folders_deleted:
            if not self._active or not self._started:
                return
            self._emit_offline_event(FsEvent(
                event_type=DELETE,
                src=foldername,
                is_dir=True,
                is_offline=True))

        logger.debug("Appending created files to processing...")
        for filename in files_created:
            if not self._active or not self._started:
                return
            self._emit_offline_event(FsEvent(
                event_type=CREATE,
                src=filename,
                is_dir=False,
                is_offline=True))

        logger.debug("Appending created folders to processing...")
        for foldername in folders_created:
            if not self._active or not self._started:
                return
            self._emit_offline_event(FsEvent(
                event_type=CREATE,
                src=foldername,
                is_dir=True,
                is_offline=True))

        self._processed_offline_changes = True
        self.is_processing_offline = False
        logger.debug("Emitting ofline events processed signal")
        self.processed_offline_changes.emit()

        if not process_modifies:
            return

        logger.debug("Finding files with possible modifications...")
        same_files = actual_files.intersection(known_files)

        if not self._active or not self._started:
            return

        logger.info(
            "Files found: %s (created: %s, deleted: %s, remaining: %s)",
            len(actual_files), len(files_created), len(files_deleted),
            len(same_files))

        logger.debug("Appending possible modified files to processing...")
        for filename in same_files:
            if not self._active or not self._started:
                return
            # Actual file modification will be checked by event filters
            # applied in WatchdogHandler instance
            self._emit_offline_event(FsEvent(
                event_type=MODIFY,
                src=filename,
                is_dir=False,
                is_offline=True,
                quiet=True,
            ))
        logger.debug("work complete")

    def _emit_offline_event(self, fs_event):
        assert fs_event.is_offline
        self._offline_stats_count += 1
        self.offline_event_occured.emit(fs_event, False)

    def on_event_is_handled_slot(self, fs_event, suppressed=False):
        """
        Slot to process FsEventFilters.event_is_handled signal

        @param fs_event Event being reported [FsEvent]
        """

        logger.info(
            "on_event_is_handled_slot: %s", fs_event)

        assert len(self._root_handlers) > 0

        if fs_event.is_offline:
            stat = self._offline_stats
        else:
            stat = self._online_stats

        # Determine name of stat counter
        stat_name_prefix = 'dir_' if fs_event.is_dir else 'file_'
        event_name = event_names[fs_event.event_type]
        stat_name = stat_name_prefix + event_name

        if suppressed:
            if not fs_event.is_dir:
                stat['file_IGNORED'] += 1
        else:
            # Increment counter corresponding to event obtained
            stat[stat_name] += 1

        if fs_event.is_offline:
            # Not handled offline events remaining
            if self._offline_stats_count > 0:
                self._offline_stats_count -= 1
                # All emitted events has been handled
                if self._offline_stats_count == 0:
                    # Online total counts should be based on offline ones
                    self._online_stats['file_COUNT'] += \
                        self._offline_stats['file_COUNT']
                    self._online_stats['dir_COUNT'] += \
                        self._offline_stats['dir_COUNT']
                    # Send stats accumulated
                    self._send_start_stats()
            else:
                logger.warning(
                    "FsEventFilters handled more offline events than "
                    "have been emitted by ObserverWrapper")
        else:
            counter_name = stat_name_prefix + 'COUNT'
            if event_name == 'CREATE':
                self._online_stats[counter_name] += 1
            elif event_name == 'DELETE':
                self._online_stats[counter_name] -= 1

    def _send_start_stats(self):
        if self._start_stats_sended:
            return

        duration = time.time() - self._start_time
        logger.info(
            "ObserverWrapper started in %s seconds", duration)

        if self._tracker:
            self._tracker.monitor_start(
                self._offline_stats['file_COUNT'],
                self._offline_stats['dir_COUNT'],
                self._offline_stats['file_CREATE'],
                self._offline_stats['file_MODIFY'],
                self._offline_stats['file_MOVE'],
                self._offline_stats['file_DELETE'],
                self._offline_stats['dir_CREATE'],
                self._offline_stats['dir_DELETE'],
                self._get_sync_dir_size(),
                duration)
        self._start_stats_sended = True

    def _send_stop_stats(self):
        duration = time.time() - self._start_time
        logger.info(
            "ObserverWrapper worked %s seconds", duration)

        if self._tracker:
            self._tracker.monitor_stop(
                self._online_stats['file_COUNT'],
                self._online_stats['dir_COUNT'],
                self._online_stats['file_CREATE'],
                self._online_stats['file_MODIFY'],
                self._online_stats['file_MOVE'],
                self._online_stats['file_DELETE'],
                self._online_stats['dir_CREATE'],
                self._online_stats['dir_MOVE'],
                self._online_stats['dir_DELETE'],
                duration,
                self._online_stats['file_IGNORED'])

    def add_special_file(self, path, event_handler):
        # Observer has to be started here. So watch is not None
        with self._lock:
            if not event_handler:
                self._special_files.add(FilePath(path))
            else:
                special_dir = FilePath(op.dirname(path))
                watch = self.schedule(event_handler, special_dir, recursive=False)
                self._special_dirs[special_dir] = watch

    def remove_special_file(self, path):
        logger.debug("Removing special file %s...", path)
        with self._lock:
            special_dir = FilePath(op.dirname(path))
            if special_dir in self._special_dirs:
                watch = self._special_dirs.pop(special_dir)
                self._root_handlers.pop(special_dir.longpath)
                if self._started:
                    self._observer.unschedule(watch)
                logger.debug("Unscheduled path %s", path)
            elif FilePath(path) in self._special_files:
                self._special_files.discard(FilePath(path))
            else:
                logger.warning("Can't remove special file %s from %s and %s",
                               path, self._special_dirs, self._special_files)
