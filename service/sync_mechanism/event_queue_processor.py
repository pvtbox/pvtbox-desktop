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
import queue
from os import path as op
import threading

from collections import defaultdict
from datetime import datetime
from time import sleep, time
import multiprocessing

from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import func
from sqlalchemy.exc import OperationalError

from common.application import Application
from service.events_db import Event, File, EventsDbBusy, FileInProcessing
from common import async_utils
from common.errors import handle_exception
from common.signal import Signal
from common.constants import RETRY_DOWNLOAD_TIMEOUT, FREE_LICENSE, DIRECTORY
from service.sync_mechanism.event_strategies.create_file_strategy import \
    LocalCreateFileStrategy, RemoteCreateFileStrategy
from service.sync_mechanism.event_strategies.create_folder_strategy import \
    LocalCreateFolderStrategy
from service.sync_mechanism.event_strategies.delete_file_strategy \
    import RemoteDeleteFileStrategy
from service.sync_mechanism.event_strategies.local_event_strategy \
    import LocalEventStrategy
from service.sync_mechanism.event_strategies.remote_event_strategy \
    import RemoteEventStrategy
from common.utils import get_relative_root_folder, benchmark
from common.path_utils import is_contained_in_dirs
from service import daque

from .event_strategies import create_strategy_from_database_event
from .event_strategies import create_strategy_from_local_event
from .event_strategies import create_strategy_from_remote_event
from .event_strategies import create_local_stategy_from_event
from .event_strategies import EventStrategy
from .event_strategies.exceptions import FolderUUIDNotFound, SkipEventForNow, \
    ProcessingAborted, EventAlreadyAdded, EventConflicted, \
    RenameDstPathFailed, SkipExcludedMove, ParentDeleted
from .event_strategies.utils import basename
from .events_loader import EventsLoader, EVENTS_QUERY_LIMIT


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

MIN_EVENTS_PROCESSING = 50

class EventQueueProcessor(object):
    workers_count = max(multiprocessing.cpu_count(), 1) * 2

    def __init__(
            self,
            download_manager,
            fs,
            db,
            web_api,
            copies_storage,
            patches_storage,
            notify_patches_ready_callback,
            license_type,
            tracker=None,
            send_request_to_user=Application.request_to_user,
            notify_user=Application.show_tray_notification,
            excluded_dirs=(),
            check_processing_timeout=RETRY_DOWNLOAD_TIMEOUT,
            collaborated_folders=(),
            get_download_backups_mode=lambda: None):
        super(EventQueueProcessor, self).__init__()

        self.collaboration_alert_is_active = threading.Event()

        self._is_initial_syncing = True

        self._download_manager = download_manager
        self._fs = fs
        self._db = db
        self._web_api = web_api
        self._copies_storage = copies_storage
        self._patches_storage = patches_storage
        self._notify_patches_ready_callback = notify_patches_ready_callback
        self._send_request_to_user = send_request_to_user
        self._notify_user = notify_user
        self._license_type = license_type
        self._check_processing_events_timer = None
        self._check_processing_events_timeout = check_processing_timeout
        self._get_download_backups_mode = get_download_backups_mode

        self._tracker = tracker
        self._statistic = defaultdict(int)

        # List of directory paths (relative) to ignore events for [iterable]
        self._excluded_dirs = excluded_dirs

        self._processing_events_lock = threading.RLock()
        self._processing_events = dict()
        self._processing_local_files = set()
        self._loading_remotes_allowed = True
        self._excluded_moves_waiting = set()
        self._restored_folders = list()
        self._paths_to_register = set()

        self._thread = None
        self._remote_msg_thread = None
        self._events_queue = daque.Daque(self.workers_count)
        self._remote_packs_queue = None
        self._last_remote_pack = None

        self._stop_processing = False

        self._min_server_event_lock = threading.RLock()
        self._min_server_event_id = 0

        self._appending_remote_messages = False
        self._events_queue_worker_idle = False
        self._local_count = self._remote_count = 0
        self._events_erased = 0
        self._events_counts_lock = threading.RLock()
        self._must_recalculate = False

        self._trash_cleaned = False

        self._collaborated_folders = collaborated_folders
        self._collaborated_folders_pending = None

        self.events_added = threading.Event()

        self._events_loader = EventsLoader(
            self, self._db, self._fs, self._excluded_dirs)

        self._init_signals()

    def _init_signals(self):
        self.event_processed = Signal(str,  # relative path
                                      bool,  # is directory
                                      datetime,  # modification time
                                      bool)  # local event
        self.file_changed = Signal(str,  # event_uuid before change
                                   str)  # event_uuid after change
        self.remote_pack_processed = Signal(int)    # events number

        self.change_excluded_dirs = Signal(list,        # dirs_to_delete
                                           list)        # dirs_to_add

        self.update_special_paths = Signal(str,         # old path
                                           str)         # new path
        self.notify_collaboration_move_error = Signal(str,  # old path
                                                      str)  # new path

        self.possibly_sync_folder_is_removed = Signal()
        self.downloading_started = Signal(EventStrategy)
        self.download_failed = Signal(EventStrategy)
        self.downloaded = Signal(EventStrategy,
                                 int,  # spent_time_in_sec
                                 int,  # p2p_bytes
                                 int)  # relayed_bytes
        self.request_last_file_events = Signal()

        self.download_failed.connect(self._on_download_failed)

    ''' Public interface ======================================================
    '''

    def set_license_type(self, license_type):
        self._license_type = license_type

    def update_patches_size(self, uuid, size, set_downloaded=False):
        with self._db.create_session(expire_on_commit=False) as session:
            events = session.query(Event) \
                .filter(Event.diff_file_uuid == uuid).all()
            for event in events:
                if not event.diff_file_size:
                    success = False
                    while not success:
                        event.diff_file_size = size
                        try:
                            event = session.merge(event)
                            success = True
                        except OperationalError:
                            session.rollback()
                            sleep(0.5)
                            pass
                    session.commit()

            events = session.query(Event) \
                .filter(Event.rev_diff_file_uuid == uuid).all()
            for event in events:
                if not event.rev_diff_file_size:
                    success = False
                    while not success:
                        event.rev_diff_file_size = size
                        try:
                            event = session.merge(event)
                            success = True
                        except OperationalError:
                            session.rollback()
                            sleep(0.5)
                            pass
                    session.commit()

        if set_downloaded:
            self._set_downloaded(uuid)

    def _set_downloaded(self, uuid):
        with self._processing_events_lock:
            logger.debug("Updating processing events. Count %s",
                         len(self._processing_events))
            processing_events = dict()
            for strategy in self._processing_events.values():
                if not strategy.event:
                    continue

                try:
                    state = strategy.event.state
                    diff_file_uuid = strategy.event.diff_file_uuid
                except Exception:
                    with self._db.create_session(
                            read_only=True) as session:
                        event = session.query(Event) \
                            .filter(Event.id == strategy.event_id).one()
                        state = event.state
                        diff_file_uuid = event.diff_file_uuid

                if state in ('occured', 'sent') or diff_file_uuid != uuid:
                    processing_events[strategy.file_id] = strategy
            self._processing_events = processing_events
            logger.debug("Updated processing events. New count %s",
                         len(self._processing_events))

    def on_patch_created(self, uuid, size):
        logger.debug("On patch created %s, %s", uuid, size)
        self.update_patches_size(uuid, size, set_downloaded=True)
        logger.debug("Updated patches size")

        result = None
        while not result:
            result = self._web_api.patch_ready(uuid, size)
            logger.debug(result)
            if not result or \
                    'result' not in result or \
                    result['result'] != 'success':
                result = None
                sleep(0.5)

        self._patches_storage.on_patch_registered(uuid)

    def append_messages_from_remote_peer(self, msg):
        if self._remote_packs_queue is None:
            raise ProcessingAborted()
        self._set_appending_remote_messages()
        self._remote_packs_queue.put(msg)


    @async_utils.run
    def _remote_packs_queue_worker(self, first_start):
        '''
        Remote packs queue processing thread
        '''
        logger.info("Starting remote packs queue processing thread")
        is_missed_events_end = False
        unhandled = False
        self._is_initial_syncing = True
        if first_start:
            logger.debug("recalculate processing events count on first_start")
            self.recalculate_processing_events_count()
        self._fs.on_initial_sync_started()
        while not self._stop_processing:
            try:
                try:
                    if not unhandled:
                        if not self._remote_packs_queue:
                            return
                        if self._last_remote_pack is not None:
                            msg = self._last_remote_pack
                        else:
                            msg = self._remote_packs_queue.get(timeout=1)
                    unhandled = False
                    if not is_missed_events_end:
                        is_missed_events_end = not msg
                    try:
                        events_count = len(msg)
                        if msg:
                            previous_remote_count = self._remote_count
                            self._last_remote_pack = msg
                            self._set_appending_remote_messages()
                            self._clear_copies_patches_changes()
                            self._append_messages_from_remote_peer(list(msg))
                            self._last_remote_pack = None
                            self.remote_pack_processed.emit(events_count)
                    except ProcessingAborted:
                        return
                    except Exception:
                        logger.error("Unhandled error appending messages "
                                     "from remote peer. Trying again...")
                        unhandled = True
                        self._statistic['received'] -= (self._remote_count -
                                                        previous_remote_count)
                        self._remote_count = previous_remote_count
                    finally:
                        # if not self._is_initial_syncing:
                        #     self.recalculate_processing_events_count()
                        logger.debug("processed pack of %s events. "
                                     "is_missed_events_end %s",
                                     events_count, is_missed_events_end)
                        if self._is_initial_syncing \
                                and is_missed_events_end \
                                and not self._thread:
                            self._do_post_initial_syncing_tasks()

                except queue.Empty:
                    self._set_appending_remote_messages(
                        self._is_initial_syncing)
                    continue

            except Exception:
                handle_exception(
                    "Can't add received remote event(s) to database")

        logger.info("Stopping remote packs queue processing thread")

    def _do_post_initial_syncing_tasks(self):
        self._is_initial_syncing = False
        with self._db.db_lock:
            self._check_excluded_dirs()
            self._skip_events()
        logger.debug("on_initial_sync_finished calling...")
        self._fs.on_initial_sync_finished()
        if self._collaborated_folders_pending:
            self.set_collaborated_folders_icons(
                self._collaborated_folders_pending)

    def _append_messages_from_remote_peer(self, msg):
        logger.debug("Messages, received from remote peer %s", msg)

        with self._db.db_lock, self._db.create_session(
                    read_only=False, expire_on_commit=False,
                    pre_commit=self._commit_copies_patches_changes,
                    pre_rollback=self._clear_copies_patches_changes) \
                as session:
            while msg:
                message = msg[0]
                if self._stop_processing:
                    raise ProcessingAborted()

                self._statistic['received'] += 1
                strategy = create_strategy_from_remote_event(
                    self._db, message, self._patches_storage,
                    self._copies_storage, self._get_download_backups_mode)

                if self._license_type == FREE_LICENSE and \
                        not strategy.event.erase_nested:
                    msg.pop(0)
                    continue

                elif strategy.event.file_name and \
                        op.isabs(strategy.event.file_name):
                    logger.debug(
                        "absolute path is received in remote event: '%s'\n%s",
                        strategy,
                        message)

                    raise Exception("absolute path is received in remote event")

                if self._stop_processing:
                    raise ProcessingAborted()

                if self._add_one_remote_message_to_db(
                        strategy, session, message):
                    msg.pop(0)

            if self._must_recalculate:
                self._recalculate_processing_events_count(session)
                self._must_recalculate = False

    def _add_one_remote_message_to_db(self, strategy, session, message):
        is_checked = strategy.event.checked
        strategy.event.checked = False
        try:
            # disable changing excluded dirs on initial sync
            excluded_dirs = self._excluded_dirs \
                if (strategy.event.type != 'delete'
                    or not self._is_initial_syncing) else []
            strategy.add_to_local_database(
                session=session,
                patches_storage=self._patches_storage,
                copies_storage=self._copies_storage,
                events_queue=self,
                excluded_dirs=excluded_dirs,
                fs=self._fs,
                initial_sync=self._is_initial_syncing,
            )
            self.change_processing_events_counts(remote_inc=1)
            self.events_added.set()
            self._loading_remotes_allowed = True
        except FolderUUIDNotFound:
            message['parent_folder_uuid'] = None
            message['event_type'] = 'delete'
            message['file_hash_before_event'] = strategy.event.file_hash \
                if strategy.event.file_hash \
                else strategy.event.file_hash_before_event
            return False

        except EventAlreadyAdded:
            if is_checked:
                self._db.set_event_checked(
                    strategy.event.uuid,
                    strategy.event.server_event_id,
                    session=session)
        except ProcessingAborted:
            logger.debug("Cancel adding events to db "
                         "because processing stopped")
            session.rollback()
            raise
        except Exception:
            try:
                session.rollback()
            except Exception as e:
                self.possibly_sync_folder_is_removed.emit()
                logger.error("Possibly sync "
                             "folder is removed: %s", e)
                return
            handle_exception(
                "Error adding event to the database. %s",
                strategy)
            self._statistic['error'] += 1
            raise

        return True

    def append_message_from_local_fs(self, msg, generated=False,
                                     fs_event=None):
        logger.info("new local message is received: %s", msg)
        event_strategy = create_strategy_from_local_event(
            self._db, msg, self._license_type, self._patches_storage,
            self._get_download_backups_mode)

        if event_strategy.event.type in ('create', 'move',):
            self._check_move_to_excluded(msg)
            if event_strategy.event.type == 'move':
                self._process_collaboration_move(msg, fs_event)

        lock_acquired = False
        try:
            tries = 0
            while tries < 10:
                lock_acquired = self._db.db_lock.acquire(blocking=False)
                if lock_acquired:
                    break
                tries += 1
                sleep(0.2)
            if not lock_acquired:
                raise Exception("sync busy")

            if self._excluded_moves_waiting and not generated:
                raise FileInProcessing(fs_event.file.events_file_id)

            if event_strategy.event.type in ('create', 'update') \
                    and not generated:
                has_conflict, to_process, file_id = self.check_conflict(
                    event_strategy)
                if has_conflict:
                    if not to_process:
                        fs_event.file.events_file_id = file_id
                        return

                    raise EventConflicted
            elif fs_event.file.events_file_id and \
                    event_strategy.event.type == 'move':
                with self._processing_events_lock:
                    processing_strategy = self._processing_events.get(
                        fs_event.file.events_file_id, None)
                if processing_strategy \
                        and isinstance(processing_strategy,
                                       LocalEventStrategy):
                    raise FileInProcessing(processing_strategy.file_id)

            self._statistic['produced'] += 1
            logger.debug("add_to_local_database %s", event_strategy)
            try:
                added_count = event_strategy.add_to_local_database(
                    patches_storage=self._patches_storage, fs_event=fs_event)
                self.change_processing_events_counts(local_inc=added_count)
                self.events_added.set()
            except EventAlreadyAdded:
                logger.debug("Event already added to database")
                raise
        finally:
            if lock_acquired:
                self._db.db_lock.release()

    def _process_collaboration_move(self, msg, fs_event):
        new_path = msg['dst']
        old_path = msg['src']
        src_root = get_relative_root_folder(old_path)
        dst_root = get_relative_root_folder(new_path)
        if src_root == dst_root or (
                not self._db.is_collaborated(src_root) and
                not self._db.is_collaborated(dst_root)):
            return

        if self._db.is_collaborated(old_path):
            try:
                self._fs.move_file(new_path, old_path)
            except Exception as e:
                logger.error("Error moving collaboration folder back (%s)", e)
            else:
                self._fs.reset_collaboration_folder_icon(old_path)
                self._fs.set_collaboration_folder_icon(old_path)
                self.notify_collaboration_move_error(old_path, new_path)
        else:
            is_directory = msg['type'] == DIRECTORY
            events_file_id = fs_event.file.events_file_id if fs_event else None
            try:
                self._fs.accept_move(new_path, old_path, is_directory,
                                     events_file_id=events_file_id)
                self._fs.copy_file(old_path, new_path, is_directory)
                self._fs.accept_delete(old_path, is_directory,
                                       events_file_id=events_file_id)
            except self._fs.Exceptions.FileNotFound as e:
                logger.warning("File %s not found in "
                               "processing collaboration move", e.file)
            except Exception as e:
                logger.error("Error processing collaboration move (%s)", e)
        raise EventConflicted

    def _check_move_to_excluded(self, msg):
        new_path = msg.get('dst', None)
        if not new_path:
            new_path = msg['path']
        if new_path in self._excluded_dirs:
            logger.debug("Folder created or moved (renamed) "
                         "to excluded dir %s",
                         new_path)
            conflicted_name = self._fs.generate_conflict_file_name(
                new_path,
                name_suffix=self._fs.selective_sync_conflict_suffix,
                with_time=False)
            try:
                self._fs.move_file(
                    src=new_path,
                    dst=conflicted_name)
            except Exception as e:
                logger.error("Error processing move to excluded (%s)", e)
            raise EventConflicted

    def append_local_event(self, event, file_path, new_file_path=None,
                           file_id=None):
        logger.info("new local event appending: %s", event)
        event_strategy = create_local_stategy_from_event(
            self._db, event, file_path, self._license_type, new_file_path,
            self._get_download_backups_mode)
        event_strategy.file_id = file_id

        with self._db.db_lock:
            logger.debug("add_to_local_database %s", event_strategy)
            event_strategy.add_to_local_database(
                patches_storage=self._patches_storage)
            self.change_processing_events_counts(local_inc=1)
            self.events_added.set()

            assert event_strategy.file_id, \
                "No file id for strategy {}".format(event_strategy)
            file_id = event_strategy.file_id
            path = new_file_path if new_file_path else file_path
            self._fs.sync_events_file_id(path, file_id,
                                         event_strategy.event.is_folder)
            self._add_event_for_processing(event_strategy)
        return event_strategy

    def clear_queue(self):
        logger.debug("Clearing remote packs queue...")
        self._local_count = self._remote_count = 0
        try:
            while True:
                self._remote_packs_queue.get_nowait()
        except (queue.Empty, AttributeError):
            pass
        self._remote_packs_queue = None
        self._last_remote_pack = None
        self._collaborated_folders_pending = None

    def clear_last_remote_pack(self):
        self._last_remote_pack = None

    @benchmark
    def start(self, first_start=True):
        self._stop_processing = False
        self._events_queue.enable()
        if self._thread or self._remote_msg_thread:
            return

        if self._remote_packs_queue is None:
            self._remote_packs_queue = queue.Queue()

        self._db_get_min_server_event_id()
        self._remote_msg_thread = self._remote_packs_queue_worker(first_start)

        self.collaboration_alert_is_active.clear()
        self._trash_cleaned = False
        self._loading_remotes_allowed = True

    def stop(self):
        logger.info("Stopping...")
        self._stop_processing = True
        if not self._thread and not self._remote_msg_thread:
            return

        self._events_queue.disable()
        self._events_queue.clear()  # stop _events_queue

        if self._tracker:
            self._tracker.sync_stop(self._statistic['received'],
                                    self._statistic['produced'],
                                    self._statistic['processed'],
                                    self._statistic['error'])
        self._statistic.clear()
        if self._check_processing_events_timer is not None:
            self._check_processing_events_timer.cancel()
            self._check_processing_events_timer = None

        if self._thread:
            self._thread.join()
            self._thread = None
        if self._remote_msg_thread:
            self._remote_msg_thread.join()
            self._remote_msg_thread = None

        with self._processing_events_lock:
            self._processing_events.clear()
            self._processing_local_files.clear()
            self._excluded_moves_waiting.clear()

        self.clear_events_erased()
        self._paths_to_register.clear()
        logger.info("Stopped.")

    def get_min_server_event_id(self):
        with self._min_server_event_lock:
            self._min_server_event_id -= 1
        return self._min_server_event_id

    ''' Utility functions =====================================================
    '''

    def _add_event_for_processing(self, event_strategy, add_to_start=False):
        with self._processing_events_lock:
            self._processing_events[event_strategy.file_id] = event_strategy
            logger.debug("Event is added to processing events. "
                         "File_id %s, Count %s",
                         event_strategy.file_id, len(self._processing_events))
            if self._check_processing_events_timer is None:
                self._check_processing_events_timer = threading.Timer(
                    self._check_processing_events_timeout,
                    self._check_processing_events)
                self._check_processing_events_timer.start()
            if isinstance(event_strategy, LocalEventStrategy):
                self._processing_local_files.add(
                    event_strategy.file_id)
                logger.debug("Added to processing local: %s",
                             event_strategy.get_file_path())
        if add_to_start:
            self._events_queue.putleft(event_strategy)
            put_str = 'put_left'
        else:
            self._events_queue.put(event_strategy)
            put_str = 'put'
        if not isinstance(event_strategy, RemoteDeleteFileStrategy):
            logger.debug("Event is %s to queue %s", put_str, event_strategy)

    def _check_processing_events(self):
        with self._processing_events_lock:
            logger.debug("Checking processing events. Count %s",
                         len(self._processing_events))
            with self._db.create_session(read_only=True) as session:
                for file_id in self._processing_events.copy():
                    strategy = self._processing_events[file_id]
                    event = self._db.get_event_by_id(
                        strategy.event_id, session=session)
                    if not event:
                        logger.warning("No found event ID=%s for file ID=%s",
                                       strategy.event_id, file_id)
                        continue
                    if (event.type == 'update'
                            and not isinstance(strategy, LocalEventStrategy)
                            and not strategy.file_download):
                        if not event.diff_file_size:
                            self._processing_events.pop(file_id)
                        elif not self._download_manager.is_download_ready(
                                    event.diff_file_uuid):
                            strategy._must_download_copy = True
                            strategy.file_download = strategy.is_file_download()
                            if hasattr(strategy, 'download_success'):
                                delattr(strategy, 'download_success')
                            self._add_event_for_processing(strategy)
                if len(self._processing_events):
                    self._check_processing_events_timer = threading.Timer(
                        self._check_processing_events_timeout,
                        self._check_processing_events)
                    self._check_processing_events_timer.start()
                else:
                    self._check_processing_events_timer = None
            logger.debug("Checked processing events. New count %s",
                         len(self._processing_events))

    def _skip_events(self):
        start_time = time()
        with self._db.create_session(
                expire_on_commit=False,
                enable_logging=False,
                read_only=False) as session:
            limit = EVENTS_QUERY_LIMIT * 5
            skipped_files_events = False
            while True:
                events_to_skip, events_count = \
                    self._events_loader.load_new_files_to_skip(
                        limit, session)
                if not events_count:
                    break

                skipped_files_events = True
                mappings = map(
                    lambda x:
                        dict(id=x[1], event_id=x[0]),
                    events_to_skip)  #[(event_id, file_id)]
                session.bulk_update_mappings(File, mappings)
                session.commit()
                self.change_processing_events_counts(
                    remote_inc=-events_count)
                if len(events_to_skip) < limit:
                    break
            while True:
                events_to_skip, events_count = \
                    self._events_loader.load_existing_files_to_skip(
                        limit, session)
                if not events_count:
                    break

                skipped_files_events = True
                mappings = map(
                    lambda x:
                        dict(id=x[1], last_skipped_event_id=x[0]),
                    events_to_skip)  #[(event_id, file_id)]
                session.bulk_update_mappings(File, mappings)
                session.commit()
                self.change_processing_events_counts(
                    remote_inc=-events_count)
                if len(events_to_skip) < limit:
                    break
            if skipped_files_events:
                self._recalculate_processing_events_count(session)
        logger.debug(
            "_skip_events took %s sec", time() - start_time)

    def _append_suspended_events_from_db(
            self, load_local_events_count=0,
            exclude_local_events_for_files=tuple(),
            load_remote_events_count=0,
            exclude_remote_events_for_files=()):
        logger.debug("_append_suspended_events_from_db, "
                     "load_local_events_count: %d, "
                     "load_remote_events_count: %d",
                     load_local_events_count, load_remote_events_count)
        with self._db.create_session(
                expire_on_commit=False,
                enable_logging=False,
                read_only=True) as session:
            try:
                if load_local_events_count:
                    # Count <= load_local_events_count <= EVENTS_QUERY_LIMIT
                    local_events = self._events_loader.load_local_events(
                        session,
                        events_count=load_local_events_count,
                        exclude_files=exclude_local_events_for_files)
                else:
                    local_events = []

                if self._excluded_moves_waiting:
                    load_remote_events_count = 0
                if load_remote_events_count:
                    remote_events = self._events_loader.load_remote_events(
                        session,
                        events_count=load_remote_events_count,
                        exclude_files=exclude_remote_events_for_files)
                else:
                    remote_events = []
                if not remote_events:
                    self._loading_remotes_allowed = \
                        len(exclude_remote_events_for_files) < \
                        self._remote_count or \
                        self._excluded_moves_waiting

                events = list(local_events + remote_events)

            except OperationalError:
                self.possibly_sync_folder_is_removed.emit()
                raise Exception

            if events:
                logger.debug("appending events from DB to queue...")

            with self._processing_events_lock:
                remote_folders_in_processing = any(
                    s.event and s.event.is_folder and
                    isinstance(s, RemoteEventStrategy)
                    for s in self._processing_events.values())
            self._add_loaded_events_for_processing(
                events, remote_folders_in_processing)

            self._add_excluded_moves_waiting(local_events)

            if events:
                self._trash_cleaned = False
                logger.debug(
                    '%s suspended events are loaded from db', len(events))
                logger.debug("remote_events_in_processing %s",
                             [key for key in self._processing_events.keys()
                              if isinstance(self._processing_events[key],
                                            RemoteEventStrategy)])
            elif not self._trash_cleaned:
                self._clean_trash()
            return len(events)

    def _add_loaded_events_for_processing(self, events,
                                          remote_folders_in_processing):
        for event in events:
            strategy = create_strategy_from_database_event(
                self._db, event, self._license_type,
                self._patches_storage, self._copies_storage,
                self._get_download_backups_mode)
            with self._processing_events_lock:
                if strategy.event.file_id in self._processing_local_files \
                        or not strategy.event.is_folder and \
                        isinstance(strategy, RemoteEventStrategy) and \
                        remote_folders_in_processing:
                    continue
            self._add_event_for_processing(strategy)

    def _add_excluded_moves_waiting(self, local_events):
        with self._processing_events_lock:
            if (self._excluded_moves_waiting and
                    not local_events and
                    not self._processing_local_files):
                for strategy in self._excluded_moves_waiting:
                    strategy.set_excluded_ready()
                    self._add_event_for_processing(strategy)
                self._excluded_moves_waiting.clear()
                self._fs.set_waiting(False)

    def _load_prev_event(self, file_id, event_id, session):
        logger.debug("Loading prev event for file_id %s, event_id %s",
                     file_id, event_id)
        file = session.query(File)\
            .filter(File.id == file_id)\
            .one_or_none()
        if not file:
            logger.warning("File not found for strategy file id %s",
                           file_id)
            return False

        file_event_id = file.event_id if file.event_id else 0
        previous_ids = [e.id for e in file.events
                        if file_event_id < e.id < event_id]
        if not previous_ids:
            return False

        prev_event_id = max(previous_ids)
        prev_event = session.query(Event)\
            .filter(Event.id == prev_event_id)\
            .one_or_none()
        if prev_event:
            prev_strategy = create_strategy_from_database_event(
                self._db, prev_event, self._license_type,
                self._patches_storage, self._copies_storage,
                self._get_download_backups_mode)
            prev_strategy.force_move()
            self._add_event_for_processing(prev_strategy)

        return bool(prev_event)

    @async_utils.run
    def _events_queue_worker(self):
        '''
        Events queue processing thread
        '''

        logger.info("Starting events queue processing thread")
        self._events_queue_worker_idle = False
        self._on_events_queue_worker_start()
        executor = ThreadPoolExecutor(max_workers=self.workers_count)
        should_load_events = True
        while True:
            try:
                logger.debug(
                    "should_load_events: %s, events_added.is_set: %s, "
                    "local_count: %s, remote_count: %s",
                    should_load_events, self.events_added.is_set(),
                    self._local_count, self._remote_count)
                if should_load_events or self.events_added.is_set() or \
                        self._local_count or self._remote_count:
                    should_load_events, \
                    loaded_count = self._load_events_from_db()
                    if (not should_load_events or
                        should_load_events and not loaded_count) \
                            and len(self._processing_events) == 0:
                        self.recalculate_processing_events_count()
                        if self._local_count or self._remote_count:
                            should_load_events = True
                            self._loading_remotes_allowed = True
                        else:
                            self.events_added.clear()

                    self._events_queue_worker_idle = False

                if (not self._events_queue_worker_idle and
                        not should_load_events and
                        not self.events_added.is_set() and
                        not self._local_count and not self._remote_count):
                    self._fs.delete_files_with_empty_events_file_ids()
                    self._fs.start_online_modifies_processing()
                    self._set_events_queue_worker_idle()

                if self._stop_processing:
                    break

                try:
                    strategy = self._events_queue.get(timeout=1,
                                                      to_process=True)
                except daque.Empty:
                    continue

                if self._stop_processing:
                    break

                future = executor.submit(self._process_single_event, strategy)
                # will wait getting next event above max_workers
                # until some event is processed
                future.add_done_callback(self._events_queue.task_done)

            except OperationalError as e:
                self.possibly_sync_folder_is_removed.emit()
                logger.error("Possibly sync "
                             "folder is removed: %s", e)

            except Exception:
                handle_exception('Failed to process event queue task')

        logger.debug("Before executor shutdown")
        executor.shutdown(wait=True)
        logger.info("Stopping events queue processing thread")

    def _on_events_queue_worker_start(self):
        with self._db.db_lock:
            try:
                self._skip_events()
                suspended_events_count = self._append_suspended_events_from_db(
                    load_local_events_count=EVENTS_QUERY_LIMIT,
                    load_remote_events_count=EVENTS_QUERY_LIMIT * 3)
            except OperationalError as e:
                suspended_events_count = 0
                self.possibly_sync_folder_is_removed.emit()
                logger.error("Possibly sync "
                             "folder is removed: %s", e)

        self._fs.start_online_processing()

        if self._tracker:
            self._tracker.sync_start(suspended_events_count)

    @benchmark
    def _load_events_from_db(self):
        should_load = True
        loaded_count = 0
        if not self._events_queue.empty():
            return should_load, loaded_count

        with self._processing_events_lock:
            processing_local_events = [
                s for s in list(self._processing_events.values())
                if isinstance(s, LocalEventStrategy)]
            processing_local_files = tuple(
                e.file_id for e in processing_local_events)

            load_local_events_count = \
                EVENTS_QUERY_LIMIT - len(processing_local_files)

            if self._loading_remotes_allowed:
                processing_remote_events = filter(
                    lambda s: isinstance(s, RemoteEventStrategy),
                    self._processing_events.values())
                processing_remote_files = tuple(
                    e.file_id for e in processing_remote_events)

                load_remote_events_count = \
                    EVENTS_QUERY_LIMIT * 3 - len(processing_remote_files)
                load_remote_events_count = max(0, load_remote_events_count)
            else:
                load_remote_events_count = 0
                processing_remote_files = ()

        if load_local_events_count or self._loading_remotes_allowed:
            with self._db.db_lock:
                if self._loading_remotes_allowed and self.events_added.is_set():
                    self._skip_events()
                loaded_count = self._append_suspended_events_from_db(
                    load_local_events_count=load_local_events_count,
                    exclude_local_events_for_files=processing_local_files,
                    load_remote_events_count=load_remote_events_count,
                    exclude_remote_events_for_files=processing_remote_files)
                logger.debug("loaded_count: %s, load_local_events_count: %s, "
                             "loading_remotes_allowed: %s",
                             loaded_count, load_local_events_count,
                             self._loading_remotes_allowed)
                should_load = loaded_count > 0 or \
                         not load_local_events_count or \
                         self._loading_remotes_allowed
                if loaded_count > 0 or \
                        (load_local_events_count and self._loading_remotes_allowed):
                    logger.debug("Clear events_added")
                    self.events_added.clear()
        return should_load, loaded_count

    def _set_events_queue_worker_idle(self):
        logger.debug("_set_events_queue_worker_idle")
        self._events_queue_worker_idle = True
        self.collaboration_alert_is_active.clear()

    def allow_loading_remotes(self):
        logger.debug("allow_loading_remotes")
        self._loading_remotes_allowed = True

    def _process_single_event(self, strategy):
        if self._stop_processing or not self._thread:
            return

        with self._db.create_session(
                expire_on_commit=False,
                enable_logging=True,
                read_only=True) as session:
            if self._stop_processing or not self._thread:
                return

            strategy.change_processing_events_counts.connect(
                self.change_processing_events_counts)
            strategy.append_local_event.connect(self.append_local_event)
            strategy.rename_or_delete_dst_path.connect(
                self.rename_or_delete_dst_path)
            # Say that by default event is processed at current time because
            # it will be loaded from DB when it processing become possible
            processed = True
            try:
                processed = self._do_process(session, strategy)
            except SkipEventForNow as e:
                logger.debug(e)
            except ProcessingAborted as e:
                processed = False
                logger.debug(e)
            except Exception as e:
                logger.debug(e)
                if self._stop_processing or not self._thread:
                    return

                self.possibly_sync_folder_is_removed.emit()

                handle_exception(
                    "Error during event processing. Skip it for a while. %s",
                    strategy)
                self._statistic['error'] += 1
            finally:
                strategy.change_processing_events_counts.disconnect(
                    self.change_processing_events_counts)
                strategy.append_local_event.disconnect(self.append_local_event)
                strategy.rename_or_delete_dst_path.disconnect(
                    self.rename_or_delete_dst_path)

            if self._stop_processing or not self._thread:
                return

            if processed:
                with self._processing_events_lock:
                    self._processing_events.pop(strategy.file_id, None)
                    logger.debug("Event removed from processing events. "
                                 "Count %s",
                                 len(self._processing_events))
                    if isinstance(strategy, LocalEventStrategy) and \
                            strategy.event:
                        self._processing_local_files.discard(
                            strategy.event.file_id)
                if strategy.event and not strategy.event.is_folder:
                    old_uuid = strategy.get_old_uuid(session=session)
                    self.file_changed.emit(old_uuid, strategy.event.uuid)

    @benchmark
    def _do_process(self, session, strategy):
        if self._stop_processing or not self._thread:
            return
        event = self._db.get_event_by_id(
            strategy.event_id, session=session)
        if not event:
            logger.warning("No found event ID=%s", strategy.event_id)
            return False
        strategy.event = event

        logger.debug('process %s', strategy)
        if event.state in ('sent', 'occured', 'conflicted'):
            return self._process_local_event(strategy, session)
        else:
            processed = self._process_remote_event(strategy, session)
            if processed:
                self._loading_remotes_allowed = True
            return processed

    def _process_local_event(self, strategy, session):
        if strategy.event.state == 'occured':
            processed = self._register_event(strategy, session)
            if strategy.event and strategy.event.is_folder and \
                    strategy.event.type == 'delete':
                self._set_events_added_to_skip()
            if processed is not None:
                return processed

        if strategy.event.state == 'conflicted':
            processed = self._process_conflict(strategy, session)
            if processed is not None:
                self.events_added.set()
                return processed

        if strategy.event.state == 'sent':
            self.change_processing_events_counts(local_inc=-1)
        return True

    def _register_event(self, strategy, session):
        if self._stop_processing or not self._thread:
            return
        logger.debug("register %s", strategy)
        file_path = strategy.event.file.path
        try:
            with self._processing_events_lock:
                ready_to_register = file_path not in self._paths_to_register
                self._paths_to_register.add(file_path)

            ready_to_register = ready_to_register and \
                                strategy.ready_to_register()
            if ready_to_register:
                file_id_before_register = strategy.event.file_id
                try:
                    strategy.register(
                        self._web_api, self._fs, self._copies_storage,
                        self._patches_storage, self._tracker,
                        license=self._license_type,
                        event_queue=self,
                        notify_user=self._notify_user,
                        excluded_dirs=self._excluded_dirs)
                    if not strategy.event:
                        self._add_restored_folders_to_queue()
                except OperationalError as e:
                    logger.error(
                        "OperationalError while registering event %s: %s",
                        strategy.event, e)
                    strategy.event = None
                finally:
                    if not strategy.event:
                        # event is deleted
                        self._processing_local_files.discard(
                            file_id_before_register)
                        self.change_processing_events_counts(local_inc=-1)
                        with self._processing_events_lock:
                            # may have new strategy here
                            # if restoring collaboration subfolder
                            processed = \
                                self._processing_events[
                                    file_id_before_register] \
                                is strategy
                            self._restored_folders = list()

                        return processed
            else:
                logger.debug("Not ready to register %s", strategy)
        finally:
            with self._processing_events_lock:
                self._paths_to_register.discard(file_path)

    def _process_conflict(self, strategy, session):
        if self._stop_processing or not self._thread:
            return
        logger.debug("process_conflict %s", strategy)
        file_path_before_conflict = \
            strategy.get_file_path(session=session)
        file_id_before_conflict = strategy.event.file_id
        if self._fs.is_processing(file_path_before_conflict):
            logger.debug(
                "Skip conflict processing, cause fs processing %s",
                strategy)
            return True

        try:
            with self._db.soft_lock():
                processed = (
                    strategy.process_conflict(
                        self._fs, self._copies_storage,
                        self._add_event_for_processing,
                        self._create_strategy_from_event,
                        self.change_processing_events_counts,
                        self._excluded_dirs)
                    )
        except EventsDbBusy:
            logger.debug("Events db busy")
            processed = True
        except Exception:
            if self._stop_processing:
                raise ProcessingAborted
            else:
                raise
        if processed:
            self._processing_local_files.discard(
                file_id_before_conflict)
        if not strategy.event:
            self.change_processing_events_counts(local_inc=-1)
            return True
        if not processed:
            return False  # do not remove from self._processing_events

    def _create_strategy_from_event(self, event, is_folder):
        return (
            LocalCreateFolderStrategy(
                self._db, event, None, self._license_type,
                self._get_download_backups_mode)
            if is_folder else
            LocalCreateFileStrategy(
                self._db, event, None, self._license_type,
                self._get_download_backups_mode)
        )

    def _process_remote_event(self, strategy, session):
        if self._stop_processing or not self._thread:
            return

        # RemoteCreateFolderStrategy is subclass of RemoteCreateFileStrategy
        if strategy.event.type == 'move' \
                and isinstance(strategy, RemoteCreateFileStrategy):
            self._set_move_event_file_folder(strategy, session)

        if self._stop_processing or not self._thread:
            return

        if strategy.file_will_be_deleted(session=session) \
                and strategy.skip_if_file_will_be_deleted(session=session):
            self._skip_event(strategy, session)
            # dummy delete may be generated
            self._loading_remotes_allowed = True
            return True
        elif strategy.is_event_skipped(session):
            logger.debug("Event %s already skipped")
            return True

        if strategy.event.state == 'received':
            processed = self._download(strategy)
            if processed is not None:
                return processed

        return self._apply(strategy, session)

    @benchmark
    def _set_move_event_file_folder(self, strategy, session):
        if strategy.event.folder_uuid:
            folder = strategy.find_folder_by_uuid(
                session=session, uuid=strategy.event.folder_uuid)
            folder_id = folder.id
        else:
            folder, folder_id = None, None
        strategy.event.file.folder_id = folder_id
        strategy.event.file.folder = folder

    @benchmark
    def _skip_event(self, strategy, session):
        if strategy.ready_to_skip(session=session):
            logger.debug("Event skipped because file will be deleted")
            min_server_event_id = self.get_min_server_event_id()
            not_applied = self._get_not_applied_events_count(
                strategy.event.file, strategy.event.id, session)
            logger.debug("not applied %s", not_applied)
            strategy.skip(min_server_event_id, fs=self._fs)
            self.change_processing_events_counts(
                remote_inc=-not_applied)
            # we may add dummy delete here
            self.events_added.set()
        else:
            logger.debug("Ignore event until it will be skipped")

    def _download(self, strategy):
        if self._stop_processing or not self._thread:
            return

        if not self._get_download_backups_mode() and strategy.event.file_size:
            self._fs.make_copy_from_existing_files(strategy.event.file_hash)

        logger.debug("download %s", strategy)
        processed = strategy.download(
            download_manager=self._download_manager,
            fs=self._fs,
            patches_storage=self._patches_storage,
            signals=self,
            reenter_event=self._add_event_for_processing)
        if not processed:
            logger.debug(
                "Strategy download, returned False %s", strategy)
            return False  # do not remove from self._processing_events

    @benchmark
    def _apply(self, strategy, session):
        strategy_file_path = strategy.get_file_path(session=session)

        fs_processing = self._fs.is_processing(strategy_file_path)
        in_local_processing = strategy.event.file_id in \
                              self._processing_local_files
        ready_to_apply, processed = self._ready_to_apply(strategy, session)

        if not fs_processing and not in_local_processing and ready_to_apply:
            if self._stop_processing or not self._thread:
                return

            logger.debug("apply %s", strategy)
            file_path = strategy.get_file_path(session=session)
            is_folder = strategy.event.is_folder
            timestamp = strategy.event.timestamp
            was_excluded = strategy.event.file.excluded
            try:
                not_applied = 1 if strategy.event.file.excluded \
                    else self._get_not_applied_events_count(
                    strategy.event.file, strategy.event.id, session)
                logger.debug("not applied %s", not_applied)
                strategy.apply(
                    fs=self._fs, excluded_dirs=self._excluded_dirs,
                    patches_storage=self._patches_storage,
                    collaborated_folders=self._collaborated_folders,
                    events_queue=self)
                if not self._do_post_apply_ops(
                        strategy, file_path,
                        not_applied, was_excluded, session):
                    return False

            except SkipExcludedMove:
                with self._db.db_lock:
                    self._excluded_moves_waiting.add(strategy)
                    self._fs.set_waiting(True)
                return False

            except Exception:
                if self._stop_processing:
                    raise ProcessingAborted
                else:
                    raise

            self._statistic['processed'] += 1
            self.event_processed.emit(
                file_path,
                is_folder,
                timestamp,
                isinstance(strategy, LocalEventStrategy))
        else:
            logger.debug(
                "Skip strategy apply %s, "
                "fs_processing: %s, in_local_processing: %s, "
                "ready_to_apply: %s",
                strategy, fs_processing, in_local_processing,
                ready_to_apply)

        return processed

    def _ready_to_apply(self, strategy, session):
        processed = True
        is_deleted = strategy.event.file.is_deleted and \
                     strategy.event.type != 'move'
        try:
            ready_to_apply = strategy.ready_to_apply(is_deleted=is_deleted)
            if not ready_to_apply:
                previous_delete_checked = \
                    strategy.event.type in ('update', 'move') and \
                    strategy.check_previous_delete(
                        events_queue=self, fs=self._fs)

                if strategy.event.is_folder and \
                        not strategy.event.file.excluded:
                    processed = not self._load_prev_event(
                        strategy.file_id, strategy.event.id, session)
        except ParentDeleted:
            ready_to_apply = False
            strategy.add_dummy_if_parent_deleted(events_queue=self)
        return ready_to_apply, processed

    def _do_post_apply_ops(self, strategy, file_path,
                           not_applied, was_excluded, session):
        if strategy.event.state != 'downloaded' and \
                not strategy.event.file.excluded:
            self._add_event_for_processing(strategy)
            return False

        if strategy.event.type == 'move' and strategy.event.is_folder:
            new_path = self._db.get_path_from_event(
                strategy.event, session)
            self.update_special_paths.emit(file_path, new_path)

        if strategy.event.is_folder and \
                (was_excluded or strategy.event.file.excluded):
            self._recalculate_processing_events_count(session)
        else:
            self.change_processing_events_counts(
                remote_inc=-not_applied)
        if strategy.event.is_folder and was_excluded:
            self._set_events_added_to_skip()

        self.cancel_file_download(strategy.file_id,
                                  session,
                                  previous_only=True,
                                  server_event_id=
                                  strategy.event.server_event_id)
        return True

    def _on_download_failed(self, strategy):
        logger.debug("Failed to download file for %s", strategy)
        with self._db.create_session(
                expire_on_commit=False,
                enable_logging=True) as session:
            event = self._db.get_event_by_id(
                strategy.event_id, session=session)
            next_events = [ev for ev in event.file.events
                           if ev.server_event_id and
                           ev.server_event_id > event.server_event_id]
            if next_events:
                logger.debug("Event %s skipped because newer events exist",
                             strategy)
                event.file.last_skipped_event_id = event.id

        with self._processing_events_lock:
            self._processing_events.pop(strategy.file_id, None)
            logger.debug("Event removed from processing events "
                         "because download failed. Count %s",
                         len(self._processing_events))

    def _db_get_min_server_event_id(self):
        min_server_event_id = self._db.get_min_server_event_id()
        if not min_server_event_id or \
                min_server_event_id and min_server_event_id > 0:
            min_server_event_id = 0
        self._min_server_event_id = min_server_event_id

    def discard_hang_file_changes(self, file_id, patch_uuids):
        with self._db.db_lock:
            with self._db.create_session(read_only=False) as session:
                file = session.query(File).filter(File.id == file_id).one()
                file_events = file.events

                assert file_events, "File to revert must have events"

                if file_id not in self._processing_events:
                    logger.debug("File is already processed, "
                                 "revert is not needed for %s", file)
                    return

                logger.debug("Reverting file %s...", file)
                file_events = sorted(file_events, key=lambda e: e.id)

                file_event_id = file.event_id if file.event_id else 0
                file_last_skipped_event_id = file.last_skipped_event_id \
                    if file.last_skipped_event_id else 0
                not_applied_events = list(filter(
                    lambda e: e.id > file_event_id and
                              e.id > file_last_skipped_event_id, file_events))
                not_applied_count = len(not_applied_events)
                self.change_processing_events_counts(
                    remote_inc=-not_applied_count)

                last_event = file_events[-1]
                logger.debug("last event %s", last_event)
                last_delete = last_event.type == 'delete'

                if file.event:
                    if file.event.file_size:
                        hash_to_add_copy = file.event.file_hash \
                            if file.event.file_hash \
                            else file.event.file_hash_before_event
                        self._copies_storage.add_copy_reference(
                            hash_to_add_copy,
                            reason="discard_hang_file_changes. Event {}. "
                                   "File {}".format(
                                file.event.uuid, file.name))
                    hash_to_remove_copy = last_event.file_hash \
                        if last_event.file_hash \
                        else last_event.file_hash_before_event
                    self._copies_storage.remove_copy_reference(
                        hash_to_remove_copy,
                        reason="discard_hang_file_changes. Event {}. "
                               "File {}".format(last_event.uuid, file.name))

                self._form_new_hang_file_events(
                    file, last_event, last_delete, session)

                for event in not_applied_events:
                    if event == last_event and last_delete:
                        self._download_manager.accept_download(event.uuid)
                    else:
                        self._download_manager.cancel_download(event.uuid)
                    if event.type == 'update':
                        if event.diff_file_uuid and \
                                event.diff_file_uuid in patch_uuids:
                            self._download_manager.cancel_download(
                                event.diff_file_uuid)
                        if event.rev_diff_file_uuid and \
                                event.rev_diff_file_uuid in patch_uuids:
                            self._download_manager.cancel_download(
                                event.rev_diff_file_uuid)
                logger.debug("Reverted file %s", file)

    def _form_new_hang_file_events(self, file, last_event, last_delete,
                                   session):
        applied_event = file.event
        file_path = file.path
        base_event = applied_event if applied_event else last_event
        new_delete_event = Event(
            type='delete',
            is_folder=False,
            file_size=base_event.file_size,
            diff_file_size=base_event.diff_file_size,
            rev_diff_file_size=base_event.rev_diff_file_size,
            file_hash=base_event.file_hash,
        )
        if applied_event:
            new_file_path = self._fs.generate_conflict_file_name(
                file.path, is_folder=file.is_folder,
                name_suffix='Reverted')
            new_create_event = Event(
                type='create',
                is_folder=False,
                file_size=applied_event.file_size,
                diff_file_size=applied_event.diff_file_size,
                rev_diff_file_size=applied_event.rev_diff_file_size,
                file_hash=applied_event.file_hash,
                file_name=basename(new_file_path)
            )

        file.event_id = last_event.id
        session.commit()
        logger.debug("File to revert %s", file)

        if not last_delete:
            self.collaboration_alert_is_active.clear()
            self.append_local_event(
                new_delete_event, file_path, file_id=file.id)
            if applied_event:
                try:
                    self._fs.accept_delete(file_path,
                                           events_file_id=file.id)
                except Exception as e:
                    logger.warning("Can't delete reverted file %s. "
                                   "Reason %s", file, e)
                    applied_event = False
                if applied_event:
                    try:
                        self._fs.create_file_from_copy(
                            new_file_path, new_create_event.file_hash,
                            events_file_id=None)
                        self.append_local_event(new_create_event,
                                                new_file_path)
                    except Exception as e:
                        logger.warning("Can't create reverted file %s "
                                       "from copy. Reason %s", file, e)

    def _get_current_strategy(self, event):
        with self._processing_events_lock:
            all_strategies = set(self._processing_events.values())
        event_strategies = list(filter(lambda s: s.event_id == event.id,
                                  all_strategies))
        if not event_strategies:
            strategy = create_strategy_from_database_event(
                self._db, event, self._license_type,
                self._patches_storage, self._copies_storage,
                self._get_download_backups_mode)
            return strategy
        else:
            return event_strategies[0]

    def get_event_uuids_by_patch_uuids(self, patch_uuids):
        strategies = self._get_strategies_by_patch_uuids(patch_uuids)
        event_uuids = [s.event.uuid for s in strategies]
        return event_uuids

    def _get_strategies_by_patch_uuids(self, patch_uuids):
        with self._processing_events_lock:
            all_strategies = set(self._processing_events.values())
        event_strategies = set(filter(
            lambda s: s.event.diff_file_uuid in patch_uuids or
                      s.event.rev_diff_file_uuid == patch_uuids,
            all_strategies))
        return event_strategies

    def check_conflict(self, strategy):
        file_path = strategy.get_dst_path()
        assert file_path

        with self._db.create_session(expire_on_commit=False) as session:
            conflicting_file, conflicting_event = \
                self._db.find_conflicting_file_or_folder(
                    file_path,
                    session=session)
            file_id = conflicting_file.id \
                if conflicting_file and not conflicting_file.is_deleted \
                else None
            logger.debug("Checking conflict %s. Event id %s", strategy, file_id)

            has_conflict = bool(file_id) and (
                    strategy.event.type != 'update' or
                    strategy.event.file_hash == conflicting_event.file_hash)

            to_process = False
            if has_conflict:
                move_not_applied = (
                    conflicting_event.type == 'move'
                    and (not conflicting_file.event_id or
                         conflicting_file.event_id < conflicting_event.id))
                to_process = (
                    strategy.event.file_hash != conflicting_event.file_hash
                    or strategy.event.file_size != conflicting_event.file_size
                    or move_not_applied)
                if to_process:
                    conflicted_name = self._fs.generate_conflict_file_name(
                        file_path, is_folder=strategy.event.is_folder)
                    self._fs.move_file(
                        src=strategy.get_src_path(),
                        dst=conflicted_name)
                else:
                    self._apply_remote_event_for_early_conflict(
                        session, conflicting_event, conflicting_file)

            return has_conflict, to_process, file_id

    def _apply_remote_event_for_early_conflict(self, session,
                                               conflicting_event,
                                               conflicting_file):
        if conflicting_event.state in ('received', 'downloaded') \
                and (not conflicting_file.event_id or
                     conflicting_file.event_id <
                     conflicting_event.id):
            not_applied = self._get_not_applied_events_count(
                conflicting_file, conflicting_event.id, session)
            logger.debug("not applied %s", not_applied)
            logger.debug("Conflicting file: %s", conflicting_file)
            conflicting_event.state = 'downloaded'
            conflicting_file.event = conflicting_event
            conflicting_file.event_id = conflicting_event.id
            if conflicting_file.is_folder and \
                    conflicting_file.uuid in self._collaborated_folders:
                conflicting_file.is_collaborated = True
                self._fs.set_collaboration_folder_icon(conflicting_file.name)
            if conflicting_event.file_hash and \
                    conflicting_event.file_size:
                self._copies_storage.remove_copy_reference(
                    conflicting_event.file_hash,
                    reason="check_conflict. Event {}. File {}"
                        .format(conflicting_event.uuid,
                                conflicting_file.name))
            self.change_processing_events_counts(
                remote_inc=-not_applied)

    def cancel_file_download(self, file_id, session, previous_only=False,
                             server_event_id=None, to_skip=False):
        logger.debug("Cancel download of file id %s", file_id)
        file = session.query(File).filter(File.id == file_id).one_or_none()
        if not file:
            return

        file_event_id = file.event_id if file.event_id else 0
        file_last_skipped_event_id = file.last_skipped_event_id \
            if file.last_skipped_event_id else 0
        events = list(filter(lambda e: e.state == 'received' and
                                       e.id > file_last_skipped_event_id and
                                       e.id > file_event_id,
                             file.events))
        if events and previous_only:
            events = [] if not server_event_id else list(filter(
                lambda e: not e.server_event_id or
                          e.server_event_id < server_event_id, events))

        for event in events:
            logger.debug("Cancel download for event uuuid %s", event.uuid)
            self._download_manager.cancel_download(event.uuid)
        if to_skip and events and \
                (not file.last_skipped_event_id or
                 file.last_skipped_event_id and
                 file.last_skipped_event_id < events[-1].id):
            logger.debug("Event %s skipped because new event arrived",
                         events[-1])
            counted_event_id = 0 if not file.event_id and not file.last_skipped_event_id \
                else file.event_id if not file.last_skipped_event_id \
                else file.last_skipped_event_id if not file.event_id \
                else max(file.event_id, file.last_skipped_event_id)
            not_counted = len(list(filter(
                lambda e: counted_event_id < e.id <= events[-1].id, events)))
            self.change_processing_events_counts(remote_inc=-not_counted)
            file.last_skipped_event_id = events[-1].id

    def recalculate_processing_events_count(self):
        with self._db.create_session(
                enable_logging=False,
                read_only=True) as session:
            self._recalculate_processing_events_count(session)

    @benchmark
    def _recalculate_processing_events_count(self, session):
        logger.debug("Recalculating processing events count")
        self._local_count, self._remote_count = \
            self._events_loader.recalculate_processing_events_count(session)

    def get_processing_events_count(self):
        with self._processing_events_lock:
            if not self._is_initial_syncing and \
                    self._local_count + self._remote_count < \
                    len(self._processing_events):
                self.recalculate_processing_events_count()

        logger.debug("local_count %s, remote_count %s, "
                     "events in processing %s, initial_syncing: %s "
                     "appending_remote_messages: %s, "
                     "events_queue_worker_idle: %s, events_erased: %s",
                     self._local_count, self._remote_count,
                     len(self._processing_events),
                     self._is_initial_syncing,
                     self._appending_remote_messages,
                     self._events_queue_worker_idle,
                     self._events_erased)
        return self._local_count, \
               self._remote_count, \
               self._appending_remote_messages or \
               not self._events_queue_worker_idle, \
               self._events_erased

    def change_processing_events_counts(self, local_inc=0, remote_inc=0):
        with self._events_counts_lock:
            self._local_count = max(self._local_count + local_inc, 0)
            self._remote_count = max(self._remote_count + remote_inc, 0)

    @benchmark
    def _get_not_applied_events_count(self, file, event_id, session):
        file_event_id = 0 if not file.event_id and not file.last_skipped_event_id \
            else file.event_id if not file.last_skipped_event_id \
            else file.last_skipped_event_id if not file.event_id \
            else max(file.event_id, file.last_skipped_event_id)
        query = session.query(func.count(Event.id))\
            .filter(Event.file_id == file.id)\
            .filter(Event.id <= event_id)
        if file_event_id:
            query = query.filter(Event.id > file_event_id)
        count = query.scalar()
        return count

    def _clean_trash(self):
        self._events_loader.clean_trash_local()
        self._events_loader.clean_trash_remote()
        self._trash_cleaned = True

    def set_collaborated_folders_icons(self, collaborated_folders):
        if self._is_initial_syncing:
            self._collaborated_folders_pending = collaborated_folders
            return
        else:
            self._collaborated_folders_pending = None

        logger.debug("Collaborated folders old %s, new %s",
                     self._collaborated_folders,
                     collaborated_folders)

        self._collaborated_folders = set(collaborated_folders)
        with self._db.soft_lock():
            with self._db.create_session(read_only=False) as session:
                folders = session.query(File) \
                    .filter(File.is_folder) \
                    .filter(File.folder_id.is_(None)) \
                    .filter(File.is_collaborated) \
                    .all()

                existing_uuids = {f.uuid for f in folders}
                folders_removed = [f for f in folders
                                   if f.uuid in existing_uuids.difference(
                        self._collaborated_folders)]
                for folder in folders_removed:
                    folder.is_collaborated = False
                    self._fs.reset_collaboration_folder_icon(folder.name)

                uuids_added = self._collaborated_folders
                added_folders_ready = session.query(File) \
                    .filter(File.is_folder) \
                    .filter(File.folder_id.is_(None)) \
                    .filter(File.uuid.in_(uuids_added)) \
                    .all()
                added_folders_ready = filter(lambda f: f.is_existing,
                                             added_folders_ready)
                for folder in added_folders_ready:
                    folder.is_collaborated = True
                    self._fs.set_collaboration_folder_icon(folder.name)

    def is_processing_stopped(self):
        return self._stop_processing

    def file_in_processing(self, file_id):
        with self._processing_events_lock:
            return file_id in self._processing_events

    def on_monitor_idle(self):
        logger.debug(
            "on_monitor_idle, is_initial_syncing: %s, "
            "thread: %s, stop_processing: %s",
            self._is_initial_syncing, bool(self._thread),
            self._stop_processing)
        if not self._is_initial_syncing \
                and not self._thread \
                and not self._stop_processing:
            self._thread = self._events_queue_worker()

    def _check_excluded_dirs(self):
        dirs_to_delete = []
        with self._db.create_session(read_only=False) as session:
            for path in self._excluded_dirs:
                folders = []
                try:
                    folders = self._db.find_folders_by_future_path(
                        path, session=session, include_deleted=True)
                except Exception:
                    logger.warning("Error finding folder %s by path", path)

                if not folders:
                    logger.warning("Can't find folders for excluded dir %s",
                                   path)
                    continue

                for folder in folders:
                    is_deleted = folder.events and max(
                        folder.events, key=lambda e: e.id).type == 'delete'
                    if is_deleted and folder.excluded:
                        folder.excluded = False
                        self._db.mark_child_excluded(
                            folder.id, session, is_excluded=False)
                        dirs_to_delete.append(path)

        if dirs_to_delete:
            self.change_excluded_dirs.emit(dirs_to_delete, [])

    def is_initial_syncing(self):
        return self._is_initial_syncing

    def inc_events_erased(self):
        with self._events_counts_lock:
            self._events_erased += 1

    def clear_events_erased(self):
        with self._events_counts_lock:
            self._events_erased = 0

    def _commit_copies_patches_changes(self):
        self._copies_storage.commit_last_changes()
        self._patches_storage.commit_last_changes()

    def _clear_copies_patches_changes(self):
        self._copies_storage.clear_last_changes()
        self._patches_storage.clear_last_changes()

    def set_recalculate(self, recalculate=True):
        self._must_recalculate = recalculate

    def process_non_registered_in_excluded(self, session):
        paths_not_successfull = set()
        files = session.query(File) \
            .filter(Event.file_id == File.id) \
            .filter(File.excluded == 1) \
            .filter(Event.state.in_(('occured', 'conflicted'))) \
            .all()
        logger.debug("Found %s non-registered files", len(files))
        deleted_count = 0
        for file in files[:]:
            move_index = 0
            non_registered_count = 0
            file_events = sorted(file.events, key=lambda e: e.id)
            events_count = len(file_events)
            folder_uuid = None
            event_id = None
            for i, event in enumerate(file_events):
                if event.state in ('occured', 'conflicted'):
                    non_registered_count += 1
                    if event.type == 'move':
                        move_index = i
                        assert move_index > 0, \
                            "Move event must have previous events"
                    else:
                        event_id = event.id

                if not move_index and (event.server_event_id or
                                       event.type != 'update'):
                    folder_uuid = event.folder_uuid

            if move_index and non_registered_count < events_count:
                old_path = self._db.get_path_from_event(
                    file_events[move_index - 1])
                new_path = file.path
                try:
                    self._move_file_back(new_path, old_path, file, session)
                except Exception as e:
                    logger.warning("Can't move file(folder) %s back to %s. "
                                   "Reason: %s",
                                   new_path, old_path, e)
                    paths_not_successfull.add(new_path)
                    continue

                file_name = basename(old_path)
                folder = self._db.get_folder_by_uuid(folder_uuid)
                folder_id = folder.id if folder else None
                for i in range(move_index + 1, len(file_events)):
                    if file_events[i].state in ('occured', 'conflicted'):
                        file_events[i].file_name = file_name
                        file_events[i].folder_uuid = folder_uuid
                file.name = file_name
                file.folder_id = folder_id
                # file.event_id is set to last non-registered event or None
                file.event_id  = event_id

                session.delete(file_events[move_index])
                deleted_count += 1
                if not is_contained_in_dirs(old_path, self._excluded_dirs):
                    return

            events_to_delete = [e for e in file.events
                                if e.state in ('occured', 'conflicted')]
            deleted_count += len(events_to_delete)
            list(map(session.delete, events_to_delete))
            if non_registered_count == events_count:
                session.delete(file)

        self.change_processing_events_counts(local_inc=-deleted_count)
        return paths_not_successfull

    def _move_file_back(self, new_path, old_path, file, session):
        while True:
            try:
                self._fs.accept_move(new_path, old_path,
                                     is_directory=file.is_folder,
                                     events_file_id=file.id)
                break
            except self._fs.Exceptions.FileAlreadyExists:
                try:
                    if not self.rename_or_delete_dst_path(
                            old_path, file.id, session):
                        raise RenameDstPathFailed
                except Exception as e:
                    logger.error("Error renaming dst path %s. Reason: %s",
                                 old_path, e)
                    raise e
            except self._fs.Exceptions.FileNotFound:
                break

    def rename_or_delete_dst_path(self, path, file_id, session):
        files = self._db.find_files_by_relative_path(path, session=session)
        files = [f for f in files if f.id != file_id]
        events_found = False
        is_directory = self._fs.is_directory(path)
        new_name = self._fs.generate_conflict_file_name(
            path, is_directory)

        # search for local event for file in db or remote delete
        delete_found = False
        dst_file_id = base_event = None
        for file in files:
            events = file.events
            if not events or file.is_folder != is_directory:
                continue

            dst_file_id = file.id
            for event in events:
                if event.state in ('occured', 'conflicted'):
                    file.name = basename(new_name)
                    event.file_name = file.name
                    event.state = 'occured'
                    events_found = True
                    logger.debug("Found event %s for dst path %s",
                                 event, path)
                elif event.type == 'delete':
                    delete_found = True
                    logger.debug("Found delete %s for dst path %s",
                                 event, path)
                elif event.id == file.event_id:
                    base_event = event

            if events_found or delete_found:
                break

        if not events_found:
            if self._fs.is_processing(path) or not dst_file_id or \
                    not base_event:
                raise RenameDstPathFailed

            if delete_found:
                # have delete event not applied
                self._fs.accept_delete(path, is_directory=is_directory)
                return

        try:
            self._fs.accept_move(path, new_name, is_directory=is_directory)
            if not events_found:
                new_move_event = Event(
                    type='move',
                    is_folder=is_directory,
                    file_size=base_event.file_size,
                    diff_file_uuid=base_event.diff_file_uuid,
                    rev_diff_file_uuid=base_event.rev_diff_file_uuid,
                    file_hash=base_event.file_hash,
                    file_name=new_name)
                self.append_local_event(new_move_event, path, new_name,
                                        dst_file_id)
        except self._fs.Exceptions.FileNotFound:
            pass
        except Exception as e:
            logger.error("Error renaming dest path %s. Error: %s", path, e)
            raise e

    def _set_appending_remote_messages(self, is_appending=True):
        self._appending_remote_messages = is_appending
        self._events_queue.set_postponed(is_appending)

    def add_restored_folders_to_processing(self, restored_folders):
        with self._processing_events_lock:
            self._restored_folders.extend(restored_folders)

    def _add_restored_folders_to_queue(self):
        with self._processing_events_lock:
            restored_folders = self._restored_folders
        for event in restored_folders:
            strategy = create_strategy_from_database_event(
                self._db, event, self._license_type,
                self._patches_storage, self._copies_storage,
                self._get_download_backups_mode)
            with self._processing_events_lock:
                if strategy.event.file_id in self._processing_local_files \
                        or strategy.event.file_id in self._processing_events:
                    continue

            self._add_event_for_processing(strategy, add_to_start=True)

    def _set_events_added_to_skip(self):
        with self._db.db_lock:
            self.events_added.set()
            self._loading_remotes_allowed = True

    def set_excluded_dirs(self, excluded_dirs):
        self._excluded_dirs = excluded_dirs
