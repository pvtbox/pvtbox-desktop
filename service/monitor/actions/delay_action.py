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
import threading

import multiprocessing

from concurrent.futures import ThreadPoolExecutor
import time
import traceback
from os import stat

from os.path import isfile

import logging

from common.constants import MOVE, CREATE, DELETE
from service.monitor.actions.action_base import ActionBase
from common.signal import Signal
from common.utils import log_sequence
from common.file_path import FilePath


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DelayAction(ActionBase):
    workers_count = max(multiprocessing.cpu_count(), 1)
    processing_events_limit = workers_count * 8

    def __init__(self, events_processing_delay, tracker):
        super(DelayAction, self).__init__()
        self.idle = Signal()
        self.working = Signal()
        self.file_added_to_indexing = Signal(FilePath)
        self.file_removed_from_indexing = Signal(FilePath, bool)

        self._started = False
        self._delay = events_processing_delay
        self._tracker = tracker
        self._offline_delay = 0.5
        self._delayed_paths = {}
        self._delayed_move_dst_paths = set()
        self._lock = threading.RLock()
        self._time_provider = time.time
        self._executor = ThreadPoolExecutor(max_workers=self.workers_count)
        self._processing_events = set()
        self._batch_checking_paths = set()
        self._loud_events = set()
        self._is_idle = True

        self._sorted_offline_events = None

    def _on_new_event(self, fs_event):
        if not self._started:
            return
        if not fs_event.time:
            fs_event.time = self._time_provider()
        with self._lock:
            if fs_event.src in self._delayed_move_dst_paths:
                return

            if not fs_event.quiet:
                if not self._loud_events:
                    logger.debug("Added first loud event")
                    # self.working.emit()
                self._loud_events.add(fs_event.src)
                self.file_added_to_indexing.emit(fs_event.src)

            prev_event = self._delayed_paths.get(fs_event.src, None)
            prev_sort_key = self._event_sort_key(prev_event) if prev_event \
                else None
            if prev_event and prev_event.quiet:
                prev_event.quiet = fs_event.quiet
            if fs_event.event_type is MOVE or prev_event is None or \
                    prev_event.event_type is not MOVE and fs_event.is_offline:
                if prev_event and not prev_event.quiet and fs_event.quiet:
                    fs_event.quiet = False
                    fs_event.is_offline = False
                self._delayed_paths[fs_event.src] = fs_event
                self._update_event_size_and_time(fs_event)
            elif prev_event:
                self._update_event_size_and_time(prev_event)

            new_event = self._delayed_paths[fs_event.src]
            if new_event.is_offline and \
                    (not prev_event or
                     prev_sort_key != self._event_sort_key(new_event)):
                self._sorted_offline_events = None

            if fs_event.event_type in (MOVE,) and not fs_event.is_dir:
                self._delayed_move_dst_paths.add(fs_event.dst)

            logger.debug(
                '%s processing fs_events, %s delayed fs_events, '
                '%s delayed move paths',
                len(self._processing_events),
                len(self._delayed_paths),
                len(self._delayed_move_dst_paths))

    def _update_event_size_and_time(self, fs_event):
        src_longpath = FilePath(fs_event.src).longpath
        dst_longpath = FilePath(fs_event.dst).longpath \
            if fs_event.dst and fs_event.event_type == MOVE \
            else None
        if dst_longpath and isfile(dst_longpath):
            path = dst_longpath
        elif isfile(src_longpath):
            path = src_longpath
        else:
            fs_event.mtime = fs_event.time
            return

        try:
            st = stat(path)
        except:
            self._on_new_event(fs_event)
            return

        fs_event.file_size = st.st_size
        fs_event.mtime = st.st_mtime

    def dispatch(self):
        with self._lock:
            events_to_add = self.processing_events_limit - \
                            len(self._processing_events)
            if events_to_add <= 0 or not self._delayed_paths and self._is_idle:
                return

            if self._is_idle and self._loud_events:
                self.working.emit()
                self._is_idle = False
            elif not self._is_idle and \
                    (not self._loud_events or
                     not self._processing_events and not self._delayed_paths):
                self.idle.emit()
                self._is_idle = True
                if self._loud_events:
                    logger.warning("Loud events uncleen %s", self._loud_events)
                    for path in self._loud_events:
                        self.file_removed_from_indexing(FilePath(path), False)
                    self._loud_events.clear()

            expired_events, offline_events = self._get_expired_events(
                events_to_add)
            self._batch_checking_paths.update(
                set(event.src for event in expired_events),
                set(event.src for event in offline_events))

        if expired_events or offline_events:
            self._batch_check_if_changing(expired_events, offline_events)
        for fs_event in expired_events:
            if not self._executor or events_to_add <= 0:
                break
            self._process_event(fs_event)
            events_to_add -= 1
        for fs_event in offline_events:
            if not self._executor or events_to_add <= 0:
                break
            self._process_event(fs_event)
            events_to_add -= 1
        with self._lock:
            self._batch_checking_paths.clear()

    def _event_sort_key(self, fs_event):
        priority = (
            10 if fs_event.is_dir and fs_event.event_type == CREATE
            else 9 if fs_event.is_dir
            else 8 if fs_event.event_type == CREATE
            else 7 if fs_event.event_type == DELETE
            else 6 if fs_event.event_type == MOVE
            else 5)
        sub_priority = 1 if fs_event.quiet else 1000000
        addition = -int(fs_event.mtime) if fs_event.is_offline \
            else 0
        return 1 - priority * sub_priority * 1000 + addition + \
               len(fs_event.src)

    def _process_event(self, fs_event):
        try:
            with self._lock:
                if fs_event.src in self._processing_events:
                    logger.debug('fs_event.src in self._processing_events')
                    self._on_new_event(fs_event)
                    return

            if self._executor:
                self._processing_events.add(fs_event.src)
                self._executor.submit(self._pass_event, fs_event)
        except Exception as e:
            tb = traceback.format_list(traceback.extract_stack())
            if self._tracker:
                self._tracker.error(tb, str(e))

            logger.error('Filesystem monitor actions exception: %s\n%s',
                         e, tb)
            self.event_returned(fs_event)

    def _pass_event(self, fs_event):
        try:
            self.event_passed(fs_event)
        finally:
            with self._lock:
                self._processing_events.discard(fs_event.src)
                if self._loud_events:
                    self._loud_events.discard(fs_event.src)
                    if not self._loud_events:
                        logger.debug("All loud events processed")

                if not fs_event.quiet:
                    self.file_removed_from_indexing(
                        FilePath(fs_event.src),
                        fs_event.event_type in (DELETE, MOVE))

                logger.debug(
                    '%s processing fs_events, %s delayed fs_events, '
                    '%s delayed move paths, '
                    'workers count: %s',
                    len(self._processing_events), len(self._delayed_paths),
                    len(self._delayed_move_dst_paths),
                    self.workers_count)

    def is_processing(self, file_path):
        file_path = FilePath(file_path)
        with self._lock:
            return (
                file_path in self._processing_events
                or file_path in self._delayed_move_dst_paths
                or file_path in self._batch_checking_paths
                or (file_path in self._delayed_paths
                    and not self._delayed_paths.get(file_path).is_offline
                    and not self._delayed_paths.get(file_path).is_dir)
            )

    def _get_expired_events(self, events_to_add):
        if events_to_add <= 0:
            return
        now = self._time_provider()
        with self._lock:
            expired_events = filter(lambda event:
                                    not event.is_offline and
                                    event.time + self._delay <= now,
                                    self._delayed_paths.values())
            sorted_expired_events = sorted(
                expired_events,
                key=self._event_sort_key)

            processing_paths = self._processing_events.copy()
            result_expired_events = []
            result_offline_events = []
            for event in sorted_expired_events:
                if not self._to_exclude_path(event.src, processing_paths):
                    result_expired_events.append(event)
                    processing_paths.add(event.src)
                    events_to_add -= 1
                    if events_to_add == 0:
                        break

            if events_to_add > 0:
                if self._sorted_offline_events is None:
                    logger.debug("Sorting offline events...")
                    offline_events = filter(lambda event: event.is_offline,
                                            self._delayed_paths.values())
                    self._sorted_offline_events = sorted(
                        offline_events,
                        key=self._event_sort_key)

                i = 0
                for event in self._sorted_offline_events[:]:
                    if self._to_exclude_path(event.src, processing_paths):
                        i += 1
                        continue

                    result_offline_events.append(event)
                    processing_paths.add(event.src)
                    del self._sorted_offline_events[i]
                    events_to_add -= 1
                    if events_to_add == 0:
                        break

            for delayed_event in result_offline_events:
                self._delayed_paths.pop(delayed_event.src, None)
            for delayed_event in result_expired_events:
                self._delayed_paths.pop(delayed_event.src, None)
                if delayed_event.event_type in (MOVE,):
                    self._delayed_move_dst_paths.discard(delayed_event.dst)

            logger.debug(
                "get_expired_events: "
                "%d expired events, "
                "%d expired offline events, "
                "%d events delayed, "
                "elapsed %s",
                len(result_expired_events), len(result_offline_events),
                len(self._delayed_paths),
                self._time_provider() - now)

            if not result_expired_events and not result_offline_events \
                    and self._delayed_paths:
                logger.debug("delayed_paths: %s, processing_paths: %s",
                             log_sequence(self._delayed_paths),
                             log_sequence(processing_paths))

            return result_expired_events, result_offline_events

    def _to_exclude_path(self, path, processing_paths):
        return any(map(lambda p: path in p, processing_paths))

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._is_idle = True

            self._delayed_paths = dict()
            self._delayed_move_dst_paths = set()
            self._processing_events = set()
            self._batch_checking_paths = set()
            self._loud_events = set()
            self._sorted_offline_events = None

        if not self._executor:
            self._executor = ThreadPoolExecutor(max_workers=self.workers_count)

    def stop(self):
        logger.debug("Stopping fs events processing")
        with self._lock:
            self._delayed_paths.clear()
            self._delayed_move_dst_paths.clear()
            self._processing_events.clear()
            self._batch_checking_paths.clear()
            # todo: mb notify about files removed from indexing
            self._loud_events.clear()
            if not self._started:
                return
        with self._lock:
            executor = self._executor if self._executor else None
            self._executor = None
            self._started = False
        if executor:
            executor.shutdown(wait=True)
        logger.debug("monitor idle emitted")
        self.idle.emit()

    def _batch_check_if_changing(self, expired_events, offline_events):
        logger.debug('batch check if changing %s files',
                     len(expired_events) + len(offline_events))
        for fs_event in set(expired_events):
            src_longpath = FilePath(fs_event.src).longpath
            dst_longpath = FilePath(fs_event.dst).longpath \
                if fs_event.dst and fs_event.event_type == MOVE \
                else None
            if dst_longpath and isfile(dst_longpath):
                path = dst_longpath
            elif isfile(src_longpath):
                path = src_longpath
            else:
                continue

            try:
                st = stat(path)
            except Exception as e:
                logger.warning("Can't get stat for %s. Reason: %s", path, e)
                expired_events.remove(fs_event)
                self.event_returned.emit(fs_event)
                continue

            if fs_event.file_size != st.st_size or \
                    fs_event.mtime != st.st_mtime:
                expired_events.remove(fs_event)
                self.event_returned.emit(fs_event)
                continue
                
            if fs_event.dst:
                fs_event.mtime += 0.1

        for fs_event in set(offline_events):
            src_longpath = FilePath(fs_event.src).longpath
            if isfile(src_longpath):
                try:
                    st = stat(src_longpath)
                except:
                    offline_events.remove(fs_event)
                    self.event_returned.emit(fs_event)
                    continue

                if fs_event.file_size != st.st_size or \
                        fs_event.mtime != st.st_mtime:
                    fs_event.is_offline = False
                    offline_events.remove(fs_event)
                    self.event_returned.emit(fs_event)

    def get_fs_events_count(self):
        return len(self._loud_events)
