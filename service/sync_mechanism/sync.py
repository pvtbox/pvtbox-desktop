# -*- coding: utf-8 -*-#

import logging
import os
import os.path as op
import time

from contextlib import contextmanager
from threading import RLock

from PySide2.QtCore import QObject, QThread, Qt, QTimer
from PySide2.QtCore import Signal as pyqtSignal
from service.network.leakybucket import ThreadSafeLeakyBucket

from service.events_db import EventsDbBusy
from common.async_qt import qt_run
from common.errors import handle_exception
from common.file_path import FilePath
from service.monitor import FilesystemMonitor
from common.constants import STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK, \
    STATUS_INDEXING, FREE_LICENSE, DOWNLOAD_PART_SIZE, DOWNLOAD_CHUNK_SIZE, \
    DOWNLOAD_PRIORITY_REVERSED_PATCH, SUBSTATUS_SYNC, SUBSTATUS_SHARE, \
    license_names, UNKNOWN_LICENSE
from service.monitor.copies.copies import Copies
from service.monitor.patches.patches import Patches
from service.network.connectivity.connectivity_service import ConnectivityService
from service.network.download_manager import DownloadManager
from common.signal import AsyncSignal
from common.utils import ensure_unicode, log_sequence, \
    get_copies_dir, get_patches_dir, benchmark, get_cfg_dir
from service.transport_setup import signals as transport_setup_signals
from common.path_converter import PathConverter
from common.application import Application
from common.translator import tr
from common.path_utils import is_contained_in_dirs

from .event_queue_processor import EventQueueProcessor

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Sync(QObject):
    status_changed = pyqtSignal(int, int, int, int, int, int)
    download_progress = pyqtSignal(str, int, int)
    downloads_status = pyqtSignal(str, int, int, list, dict)
    download_error = pyqtSignal(str)
    clear_download_error = pyqtSignal()

    file_moved = pyqtSignal(FilePath,  # file old path
                            FilePath)  # file new path
    file_list_changed = pyqtSignal()
    sync_folder_is_removed = pyqtSignal(bool,  # sync consistent
                                        bool)  # cfg consistent
    sync_dir_size_changed = pyqtSignal()
    file_changed = pyqtSignal(str,  # event_uuid before change
                              str)  # event_uuid after change
    exited = pyqtSignal()
    no_disk_space = pyqtSignal(object,      # task or fs_event
                               str,     # display_name
                               bool,        # is_error
                               bool)        # is_remote
    db_or_disk_full = pyqtSignal()
    started = pyqtSignal()
    stopped = pyqtSignal()
    sync_stopped = pyqtSignal(bool)
    sync_start_completed = pyqtSignal()
    collaborated_folders_obtained = pyqtSignal(list)
    long_paths_ignored = pyqtSignal(list)
    config_changed = pyqtSignal()
    update_special_paths = pyqtSignal(str,  # old path
                                      str)     # new path
    license_alert = pyqtSignal(str)         # license name
    request_last_file_events = pyqtSignal()
    license_type_changed = pyqtSignal(int)
    revert_failed = pyqtSignal(list)
    connected_nodes_changed = pyqtSignal(int)

    _monitor_idle_signal = pyqtSignal()
    _special_file_event = pyqtSignal(str,   # path
                                     int,       # event_type
                                     str)   # new_path
    _remote_pack_processed = pyqtSignal(int)    # events_num

    _change_excluded_dirs = pyqtSignal(list,    # dirs_to_delete
                                       list)    # dirs_to_add

    _remote_file_event_messages = pyqtSignal(str)    # node_id
    _patches_info_obtained = pyqtSignal(dict)
    _copies_cleaned = pyqtSignal()
    _access_denied = pyqtSignal(str)
    signal_info_tx = pyqtSignal(tuple)
    signal_info_rx = pyqtSignal(tuple)

    file_added_to_ignore = pyqtSignal(FilePath)
    file_removed_from_ignore = pyqtSignal(FilePath)
    file_added_to_indexing = pyqtSignal(FilePath)
    file_removed_from_indexing = pyqtSignal(FilePath, bool)
    file_added_to_disk_error = pyqtSignal(FilePath)

    events_check_after_checked_interval = 30 * 60 * 1000
    events_check_after_online_interval = 60 * 1000

    def __init__(self, cfg, web_api, db, ss_client,
                 get_sync_dir_size, tracker=None, parent=None,
                 network_speed_calculator=None):
        assert(cfg)
        assert(web_api)
        assert(db)
        QObject.__init__(self, parent=parent)

        self.error_happens = AsyncSignal(Exception)
        self._root = None
        self._cfg = cfg
        self.fs = None
        self._event_queue = None
        self._patches_storage = None
        self._copies_storage = None
        self.web_api = web_api
        self._db = db
        self._ss_client = ss_client
        self._network_speed_calculator = network_speed_calculator
        self._download_manager = None
        self._get_sync_dir_size = get_sync_dir_size
        self._tracker = tracker
        self.first_start = True
        self._max_remote_event_received = 0

        self._init_connectivity()

        self._work_count = 0

        self._excluded_dirs_relpaths = None
        self._excluded_set = True
        self._excluded_lock = RLock()

        self._special_files = dict()

        self._connect_ss_slots()

        self._monitor_idle = True
        self._event_queue_idle = True
        self._download_tasks_idle = True
        self._share_idle = True
        self._uploads_idle = True
        self._local_events_count = 0
        self._remote_events_count = 0
        self._events_erased = 0
        self._recalculate_threshold = 500

        self._started = False
        self._waiting_file_events = True
        self._loading_missed_events = True
        self._remote_events_lock = RLock()
        self._stop_count = 0

        self._collaborated_folders_pending = None
        self._collaborated_folders = set()

        self._events_count_calculating = False

        self._last_event_uuid = None
        self._cleaning_old_events_pending = False
        self._download_backups_changed = False
        self._remove_patches_time = 0
        self._last_event_server_event_id = 0

        self._remote_events_received = 0
        self._rerequest_all_events = False

        self._sending_file_list = False

        self._alert_intervals = [1 * 60, 5 * 60, 10 * 60, 30 * 60, 60 * 60]
        self._alert_info = dict()

        self._connect_signals()

    def _init_connectivity(self):
        self._connectivity_service = ConnectivityService(
            self._ss_client, self._network_speed_calculator)
        self._connectivity_service_thread = QThread()
        self._connectivity_service.moveToThread(
            self._connectivity_service_thread)
        self._connectivity_service_thread.started.connect(
            self._connectivity_service.init.emit)
        self._connectivity_service.exited.connect(self._on_exit,
                                                  Qt.QueuedConnection)
        self._download_manager_thread = QThread()
        self._download_manager_thread.start()
        self._connectivity_service.connected_nodes_outgoing_changed.connect(
            self._on_connected_nodes_changed, Qt.QueuedConnection)

    def _connect_ss_slots(self):
        self._ss_client.file_events.connect(
            self._on_remote_file_event_messages_cb, Qt.QueuedConnection)
        self._ss_client.patches_info.connect(
            self._patches_info_obtained, Qt.QueuedConnection)
        self._ss_client.min_stored_event.connect(
            self.save_last_event_uuid, Qt.QueuedConnection)
        self._ss_client.collaborated_folders.connect(
            self.collaborated_folders_obtained, Qt.QueuedConnection)

    def _connect_signals(self):
        # Connect to loggedIn signal to get license_type
        transport_setup_signals.license_type_changed.connect(
            self.on_license_type_changed_slot)

        self._events_check_timer = QTimer(self)
        self._events_check_timer.setSingleShot(True)
        self._events_check_timer.timeout.connect(
            self._request_last_file_events)
        self.request_last_file_events.connect(self._request_last_file_events)

        self.file_list_changed.connect(self.sync_dir_size_changed)


        # self.status_changed.connect(self.process_offline_changes)
        self.status_changed.connect(self._try_clean_old_events)
        self.status_changed.connect(self._reset_alert_info)

        self.no_disk_space.connect(self._on_no_disk_space)
        self.collaborated_folders_obtained.connect(
            self._on_collaborated_folders, Qt.QueuedConnection)

        self.sync_stopped.connect(lambda _: self._on_stopped())

        self._remote_pack_processed.connect(self._on_remote_pack_processed)

        self._change_excluded_dirs.connect(
            lambda dirs_to_delete, dirs_to_add:
            self._on_change_excluded_dirs(dirs_to_delete, dirs_to_add))
        self.update_special_paths.connect(self._on_update_special_paths)
        self._remote_file_event_messages.connect(
            self._on_remote_file_event_messages)
        self._patches_info_obtained.connect(
            self._on_patches_info, Qt.QueuedConnection)
        self._copies_cleaned.connect(
            self._on_copies_cleaned, Qt.QueuedConnection)
        self._access_denied.connect(self._on_access_denied)

    def check_if_sync_folder_is_removed(self):
        sync_consistent, cfg_consistent = self.check_consistency()
        if sync_consistent and cfg_consistent:
            return False

        self.sync_folder_is_removed.emit(
            sync_consistent, cfg_consistent)
        return True

    def check_consistency(self):
        logger.debug(
            "Checking system consistency...")
        try:
            sync_dbs = [self._copies_storage,
                        self._patches_storage,
                        self.fs, self._db, self._tracker]
            sync_consistent = all(map(lambda d: d.db_file_exists(), sync_dbs))
        except AttributeError:
            sync_consistent = os.path.isdir(self._cfg.sync_directory)

        cfg_consistent = os.path.isdir(get_cfg_dir())
        logger.debug(
            "System consistency checked. """
            "Sync consistent: %s. Cfg consistent: %s",
            sync_consistent, cfg_consistent)
        return sync_consistent, cfg_consistent

    def clean_patches(self):
        self._patches_storage.clean()

    @contextmanager
    def pause_guard(self):
        active = self._started
        if active:
            self.stop()
        try:
            yield self
        finally:
            if active:
                self.start()

    def _on_downloads_idle(self):
        self._download_tasks_idle = True
        self._update_status()

    def _on_downloading(self):
        self._download_tasks_idle = False
        self._update_status()

    def on_share_idle(self):
        self._share_idle = True
        self._update_status()

    def on_share_downloading(self):
        self._share_idle = False
        self._update_status()

    def on_share_downloading_error(self, text):
        if self._download_tasks_idle and not self._share_idle:
            self.download_error.emit(text)

    def on_uploads_idle(self):
        self._uploads_idle = True
        self._update_status()

    def on_uploads_downloading(self):
        self._uploads_idle = False
        self._update_status()

    def on_license_type_changed_slot(self, license_type, old_license_type):
        if license_type == old_license_type:
            return

        upgrade_license_types = (FREE_LICENSE, )
        was_active = self.is_enabled()
        stopped = False
        logger.debug(
            "Processing license type change from %s to %s",
            old_license_type, license_type)

        if old_license_type != UNKNOWN_LICENSE and old_license_type:
            if license_type == FREE_LICENSE:
                stopped = True
                self.stop()
                self._on_license_downgraded_to_free()
            elif old_license_type == FREE_LICENSE:
                stopped = True
                self.pause()
                self._on_license_upgraded_from_free()

        self._cfg.set_settings({'license_type': license_type})
        self.license_type_changed.emit(license_type)

        if self._event_queue:
            self._event_queue.set_license_type(license_type)

        if was_active and stopped:
            self.start()

        if license_type in upgrade_license_types:
            self.license_alert.emit(license_names[FREE_LICENSE])

    def _on_license_downgraded_to_free(self):
        self._clear_excluded()
        self._delete_on_license_downgrade()

    def _on_license_upgraded_from_free(self):
        logger.info('License upgraded from free')
        self._rerequest_all_events = True
        self.fs.move_files_to_copies()
        self._copies_storage.clean(with_files=False)
        self._patches_storage.clean(with_files=not self._cfg.download_backups)
        self.first_start = True
        self._ss_client.reconnect()

    def _on_patches_info(self, patches_info):
        """
        Callback to handle data of message 'patches_info' from signalling
        patches_info = [
            {'diff_uuid':'xx', 'diff_size':'123'},
            {'diff_uuid':'yy', 'diff_size':'321'},
        ]
        """
        try:
            with self._db.soft_lock():
                for patch_info in patches_info:
                    try:
                        uuid = patch_info["diff_uuid"]
                        size = int(patch_info["diff_size"])
                        self._on_patch_ready(uuid, size)
                    except KeyError as e:
                        logger.warning("Invalid patch_info, error: '%s'", e)
                    except ValueError as e:
                        logger.warning("Invalid diff_size, error: '%s'", e)
        except EventsDbBusy:
            logger.debug("Events db busy")
            self._patches_info_obtained.emit(patches_info)

    def _on_patch_ready(self, uuid, size):
        self._patches_storage.update_patch(uuid, size)
        self._event_queue.update_patches_size(uuid, size)

    def _on_remote_file_event_messages_cb(self, events, node_id):
        logger.info("Obtained %s event(s)", len(events))
        with self._remote_events_lock:
            self._waiting_file_events = False
            if events or self._loading_missed_events:
                try:
                    self._event_queue.append_messages_from_remote_peer(events)
                    if events and events[-1]['event_id'] > \
                            self._max_remote_event_received:
                        self._max_remote_event_received = events[-1]['event_id']
                except Exception:
                    handle_exception("Can't put received remote event to queue.")
            if not events:
                self._loading_missed_events = False
        self._remote_file_event_messages.emit(node_id)

    def _on_remote_file_event_messages(self, node_id):
        if not self._started:
            return
        logger.debug(
            "Reset events check timer after remote file messages obtained")
        self._events_check_timer.stop()
        if node_id == '__SERVER__':
            self._events_check_timer.setInterval(
                self.events_check_after_checked_interval)
        else:
            self._events_check_timer.setInterval(
                self.events_check_after_online_interval)
        self._events_check_timer.start()

    def clear_remote_events_received(self):
        self._remote_events_received = 0

    def _request_last_file_events(self, is_true_check=True):
        logger.debug("Requesting last file events")
        with self._remote_events_lock:
            if self._events_check_timer.isActive():
                self._events_check_timer.stop()
            if is_true_check:
                try:
                    with self._db.soft_lock():
                        max_server_event_id, \
                        max_checked_server_event_id, \
                        events_count = self.get_server_event_ids(
                            force_db_values=True)
                except EventsDbBusy:
                    logger.debug("Events db busy")
                    self._events_check_timer.stop()
                    self._events_check_timer.setInterval(100)
                    self._events_check_timer.start()
                    return
            else:
                max_server_event_id = self._max_remote_event_received
                max_checked_server_event_id = self._max_remote_event_received
                events_count = 0

            logger.debug("max_server_event_id %s, max_checked_server_event_id %s, events_count %s",
                         max_server_event_id, max_checked_server_event_id, events_count)

            self.clear_remote_events_received()
            node_without_backup = not self._cfg.download_backups
            self._waiting_file_events = True
            self._ss_client.send_last_file_events_request(
                max_server_event_id, max_checked_server_event_id, events_count,
                node_without_backup)
        self._update_status()

    def _on_remote_pack_processed(self, events_num):
        if not self._started:
            return
        logger.debug("_on_remote_pack_processed %s", events_num)
        self._remote_events_received += events_num
        if self._remote_events_received >= self._cfg.remote_events_max_total:
            self._request_last_file_events(is_true_check=False)

    @benchmark
    def start(self):
        if self.check_if_sync_folder_is_removed():
            return
        if self._started:
            self.started.emit()
            return
        if not self._excluded_set:
            QTimer.singleShot(500, self.start)
            return

        logger.debug("start")
        self._started = True
        self._monitor_idle = True
        self._download_tasks_idle = True
        self._share_idle = True
        self._uploads_idle = True
        self._event_queue_idle = False

        self._sending_file_list = False

        self._monitor_idle_signal.connect(self._event_queue.on_monitor_idle)

        self._download_manager.resume_all_downloads()
        self._event_queue.start(self.first_start)
        if self._patches_storage:
            self._patches_storage.start()

        with self._remote_events_lock:
            if not self.first_start:
                self._request_last_file_events(is_true_check=False)
            else:
                self._waiting_file_events = True
            self._loading_missed_events = True

        self.fs.start()
        self.first_start = False
        self.sync_start_completed.emit()

    def is_known(self, path):
        if not self._started or not self.fs:
            return False
        return self.fs.is_known(path)

    def pause(self):
        logger.debug("Pause")
        self._stop()

    def stop(self):
        logger.debug("Stop")
        self._stop(cancel_downloads=True, clear_queue=True)
        with self._remote_events_lock:
            self._max_remote_event_received = 0

    def _stop(self, cancel_downloads=False, clear_queue=False):
        if cancel_downloads:
            self._local_events_count = self._remote_events_count = 0
            self._events_erased = 0

        if clear_queue and self._event_queue:
            self._event_queue.clear_queue()

        if not self._started:
            logger.debug("Already stopped")
            self.stopped.emit()
            return
        self._monitor_idle_signal.disconnect(self._event_queue.on_monitor_idle)
        self._started = False
        if self.fs:
            self.fs.stop()

        if self._events_check_timer.isActive():
            self._events_check_timer.stop()

        if self._event_queue:
            self._event_queue.stop()
            if clear_queue:
                self._event_queue.clear_last_remote_pack()
        if self._patches_storage:
            self._patches_storage.stop()

        if cancel_downloads:
            if self._download_manager:
                self._download_manager.cancel_all_downloads()
        else:
            if self._download_manager:
                self._download_manager.pause_all_downloads()

        self.sync_stopped.emit(cancel_downloads)

    def _on_stopped(self):
        self._stop_count = (self._stop_count + 1) % 2
        # emit stopped signawhen fs and queue both stopped
        if not self._stop_count:
            self.stopped.emit()

    def update_status(self):
        self._update_status()

    def _update_status(self, force_update=False):
        if not self._started and not force_update:
            return

        fs_events_count = self.fs.get_fs_events_count() if self.fs else 0
        self._monitor_idle = self._monitor_idle and not fs_events_count
        downloads_idle = self._download_tasks_idle and \
            self._share_idle and self._uploads_idle
        all_idle = (downloads_idle
                    and self._monitor_idle
                    and self._event_queue_idle
                    )
        new_status = STATUS_PAUSE \
            if self._event_queue is None or not self._started \
            else STATUS_WAIT \
            if all_idle and not self._waiting_file_events \
            else STATUS_INDEXING \
            if downloads_idle and self._event_queue_idle \
               and not self._waiting_file_events\
            else STATUS_IN_WORK

        new_substatus = SUBSTATUS_SHARE if not self._share_idle \
            else SUBSTATUS_SYNC

        logger.debug(
            "update_status, download_tasks_idle: %s, share_idle: %s, "
            "uploads_idle: %s, monitor_idle: %s, event_queue_idle: %s, "
            "event_queue is None: %s, waiting_file_events: %s, "
            "fs_events_count: %s",
            self._download_tasks_idle, self._share_idle, self._uploads_idle,
            self._monitor_idle, self._event_queue_idle,
            self._event_queue is None, self._waiting_file_events,
            fs_events_count)

        self._try_remove_old_patches(new_status)

        self.status_changed.emit(new_status, new_substatus,
                                 self._local_events_count,
                                 self._remote_events_count,
                                 fs_events_count,
                                 self._events_erased)

    def _set_monitor_idle(self, is_idle):
        logger.debug("_set_monitor_idle %s", is_idle)
        self._monitor_idle = is_idle
        if is_idle:
            self._monitor_idle_signal.emit()

    def apply_config(self):
        if self.check_if_sync_folder_is_removed():
            return

        with self.pause_guard():
            self._apply_config()

    def force_apply_config(self):
        self._apply_config()

    def _apply_config(self):
        if self.fs:
            logger.info("stop module monitor")
            self.fs.stop()

        logger.info("initialize module monitor")
        self._root = FilePath(self._cfg.sync_directory)
        self._copies_storage = Copies(
            self._root, self._db_file_created_cb,
            extended_logging=self._cfg.copies_logging)
        self._patches_storage = Patches(
            self._root, self._copies_storage,
            self._tracker, db_file_created_cb=self._db_file_created_cb,
            extended_logging=self._cfg.copies_logging,
            events_db=self._db)
        self._connect_copies_patches_signals()

        self._excluded_dirs_relpaths = self._cfg.excluded_dirs
        pc = PathConverter(self._root)
        excluded_dirs_abs_paths = list(map(
            lambda p: pc.create_abspath(p), self._excluded_dirs_relpaths))

        self.fs = FilesystemMonitor(
            root=self._root,
            events_processing_delay=self._cfg.fs_events_processing_delay,
            get_sync_dir_size=self._get_sync_dir_size,
            conflict_file_suffix=self._cfg.conflict_file_suffix,
            tracker=self._tracker,
            excluded_dirs=list(map(FilePath, excluded_dirs_abs_paths)),
            copies_storage=self._copies_storage,
            parent=self,
            max_relpath_len=self._cfg.max_relpath_len,
            db_file_created_cb=self._db_file_created_cb
        )

        self._re_start_downloads()
        self._connect_fs_signals()
        self._connect_disk_full_signals()

        logger.info("starting EventQueueProcessor")
        self._event_queue = EventQueueProcessor(
            download_manager=self._download_manager,
            fs=self.fs,
            db=self._db,
            web_api=self.web_api,
            copies_storage=self._copies_storage,
            patches_storage=self._patches_storage,
            license_type=self._cfg.license_type,
            tracker=self._tracker,
            notify_patches_ready_callback=self._ss_client.send_patches_info,
            excluded_dirs=self._excluded_dirs_relpaths,
            collaborated_folders=self._collaborated_folders,
            get_download_backups_mode=self._get_download_backups_mode)

        with self._excluded_lock:
            excluded_set = self._excluded_set
            if excluded_set:
                self._excluded_set = False
        if excluded_set:
            self._process_excluded()
        self._restore_special_files()

        self._connect_event_queue_signals()

        if self._collaborated_folders_pending:
            self._event_queue.set_collaborated_folders_icons(
                self._collaborated_folders_pending)
            self._collaborated_folders_pending = None

        self._connect_patches_copies_signals()

    def _connect_copies_patches_signals(self):
        self._copies_storage.possibly_sync_folder_is_removed.connect(
            self.check_if_sync_folder_is_removed)
        self._patches_storage.possibly_sync_folder_is_removed.connect(
            self.check_if_sync_folder_is_removed)

    def _re_start_downloads(self):
        if self._download_manager:
            self._connectivity_service.disconnect_ss_slots.emit()
            self._download_manager.quit.emit()

        self._download_manager = DownloadManager(
            connectivity_service=self._connectivity_service,
            ss_client=self._ss_client,
            events_db=self._db,
            copies_storage=self._copies_storage,
            patches_storage=self._patches_storage,
            tracker=self._tracker,
            get_download_backups_mode=self._get_download_backups_mode,
            get_file_path=self.get_file_abs_path_by_event_uuid)
        self._download_manager.moveToThread(self._download_manager_thread)

        download_limiter = self._create_limiter(
            self._cfg.download_limit, DOWNLOAD_PART_SIZE)
        upload_limiter = self._create_limiter(
            self._cfg.upload_limit, DOWNLOAD_CHUNK_SIZE * 2)

        self._patches_storage.set_download_manager(self._download_manager)

        self._connect_downloads_signals()

        if not self._connectivity_service_thread.isRunning():
            self._connectivity_service_thread.started.connect(
                lambda: self._connectivity_service.set_upload_limiter(
                    upload_limiter))
            self._connectivity_service_thread.start()
        else:
            self._connectivity_service.connect_ss_slots.emit()

        if not self._download_manager_thread.isRunning():
            self._download_manager_thread.started.connect(
                lambda: self._download_manager.prepare_cleanup(
                    [get_copies_dir(self._root), get_patches_dir(self._root)]))
            self._download_manager_thread.started.connect(
                lambda: self._download_manager.set_download_limiter(
                    download_limiter))
        else:
            self._download_manager.prepare_cleanup(
                [get_copies_dir(self._root), get_patches_dir(self._root)])
            self._download_manager.set_download_limiter(download_limiter)

    def _connect_downloads_signals(self):
        self.file_changed.connect(self._download_manager.on_file_changed)
        self._download_manager.idle.connect(self._on_downloads_idle)
        self._download_manager.idle.connect(
            lambda: self.sync_dir_size_changed.emit())
        self._download_manager.working.connect(self._on_downloading)
        self._download_manager.progress.connect(self.send_download_progress)
        self._download_manager.downloads_status.connect(
            self.send_downloads_status)
        self._download_manager.error.connect(
            lambda text: self.download_error.emit(text))
        self._download_manager.clear_error.connect(
            self.clear_download_error.emit)
        self._download_manager.possibly_sync_folder_is_removed.connect(
            self.check_if_sync_folder_is_removed)
        self._download_manager.no_disk_space.connect(
            lambda task, display_name, is_error: self.no_disk_space.emit(
                task, display_name, is_error, True))
        self._download_manager.signal_info_tx.connect(self._on_info_tx)
        self._download_manager.signal_info_rx.connect(self._on_info_rx)
        self.fs.copy_added.connect(self._download_manager.copy_added)
        self._download_manager.supplying_finished.connect(
            self.fs.clear_paths_quiet)

    def _connect_fs_signals(self):
        self.fs.started.connect(self.started.emit, Qt.QueuedConnection)
        self.fs.stopped.connect(self._on_stopped, Qt.QueuedConnection)
        self.fs.error_happens.connect(self.error_happens)
        self.fs.idle.connect(lambda: self._set_monitor_idle(True))
        self.fs.working.connect(lambda: self._set_monitor_idle(False))
        self.fs.file_added_to_ignore.connect(
            lambda p: self.file_added_to_ignore.emit(p))
        self.fs.file_removed_from_ignore.connect(
            lambda p: self.file_removed_from_ignore.emit(p))
        self.fs.file_added_to_indexing.connect(
            lambda p: self.file_added_to_indexing.emit(p))
        self.fs.file_removed_from_indexing.connect(
            lambda p, path_removed: self.file_removed_from_indexing.emit(
                p, path_removed))
        self.fs.file_moved.connect(
            lambda old_path, new_path:
                self.file_moved.emit(old_path, new_path))
        self.fs.file_list_changed.connect(self._on_file_list_changed)
        self.fs.possibly_sync_folder_is_removed.connect(
            self.check_if_sync_folder_is_removed)
        self.fs.no_disk_space.connect(
            lambda fs_event, display_name, is_error: self.no_disk_space.emit(
                fs_event, display_name, is_error, False))
        self.fs.special_file_event.connect(
            lambda path, event_type, new_path: self._special_file_event.emit(
                path, event_type, new_path))
        self._special_file_event.connect(self._on_special_file_event)
        self.fs.access_denied.connect(
            lambda path: self._access_denied.emit(path))

    def _connect_disk_full_signals(self):
        self._db.db_or_disk_full.connect(
            lambda: self.db_or_disk_full.emit())
        self._copies_storage.db_or_disk_full.connect(
            lambda: self.db_or_disk_full.emit())
        self._patches_storage.db_or_disk_full.connect(
            lambda: self.db_or_disk_full.emit())
        self.fs.db_or_disk_full.connect(
            lambda: self.db_or_disk_full.emit())

    def _connect_event_queue_signals(self):
        self._event_queue.file_changed.connect(
            lambda event_uuid_before, event_uuid_after:
                self.file_changed.emit(event_uuid_before, event_uuid_after))
        self._event_queue.event_processed.connect(
            lambda rel_path, is_dir, mod_time, is_loc:
            self.sync_dir_size_changed.emit())
        self._event_queue.change_excluded_dirs.connect(
            lambda dirs_to_delete, dirs_to_add:
            self._change_excluded_dirs.emit(dirs_to_delete, dirs_to_add))
        self._event_queue.update_special_paths.connect(
            lambda old_path, new_path:
            self.update_special_paths.emit(old_path, new_path))
        self.fs.event_is_arrived.connect(
            lambda event, fs_event:
            self._event_queue.append_message_from_local_fs(
                event, False, fs_event))
        self._event_queue.possibly_sync_folder_is_removed.connect(
            self.check_if_sync_folder_is_removed)
        self._event_queue.remote_pack_processed.connect(
            lambda n: self._remote_pack_processed.emit(n))
        self._event_queue.request_last_file_events.connect(
            lambda: self.request_last_file_events.emit())
        self._event_queue.notify_collaboration_move_error.connect(
            self._notify_collaboration_move_error)

    def _connect_patches_copies_signals(self):
        self._patches_storage.patch_created.connect(
            self._event_queue.on_patch_created)
        self._patches_storage.patch_created.connect(
            lambda a, b: self.sync_dir_size_changed.emit())
        self._patches_storage.patch_deleted.connect(
            lambda _: self.sync_dir_size_changed.emit())
        self._copies_storage.delete_copy.connect(
            lambda _h, _ws: self.sync_dir_size_changed.emit())

    def apply_download_limit(self, download_limit):
        if not self._event_queue or \
                not self._connectivity_service_thread.isRunning():
            return None

        download_limiter = self._create_limiter(
            download_limit, DOWNLOAD_PART_SIZE)
        self._download_manager.set_download_limiter(download_limiter)
        return download_limiter

    def apply_upload_limit(self, upload_limit):
        if not self._connectivity_service:
            return None

        upload_limiter = self._create_limiter(
            upload_limit, DOWNLOAD_CHUNK_SIZE * 2)
        self._connectivity_service.set_upload_limiter(upload_limiter)
        return upload_limiter

    def _create_limiter(self, limit, chunk_size):
        limit *= 1024
        capacity = (
            limit * 2  # @@ magic const
            if limit > chunk_size
            else chunk_size)

        return (
            ThreadSafeLeakyBucket(capacity, limit, time.time)
            if limit
            else None)

    def is_enabled(self):
        '''
        Checks whether sync is enabled

        @return Sync enabled status [bool]
        '''

        return bool(self._event_queue)

    def get_file_uuid(self, path, timeout=2.0):
        '''
        Returns uuid for path specified

        @param path relative path for file [unicode]

        @return file_uuid[str]
        '''
        try:
            with self._db.soft_lock(timeout_sec=timeout):
                uuid = self._db.find_file_uuid_by_relative_path(path)
        except EventsDbBusy:
            logger.debug("Events db busy")
            return None

        return uuid

    def get_folder_uuid(self, path, timeout=2.0):
        '''
        Returns uuid for path specified

        @param path relative path for folder [unicode]

        @return folder_uuid[str]

        '''
        try:
            with self._db.soft_lock(timeout_sec=timeout):
                uuid = self._db.find_folder_uuid_by_relative_path(path)
        except EventsDbBusy:
            logger.debug("Events db busy")
            return None

        return uuid

    def get_file_abs_path_by_event_uuid(self, uuid, set_quiet=False):
        try:
            with self._db.soft_lock():
                path = self._db.get_file_path_by_event_uuid(uuid)
                if path:
                    pc = PathConverter(self._root)
                    path = pc.create_abspath(path)
                    if set_quiet and self.fs:
                        self.fs.set_path_quiet(path)
        except EventsDbBusy:
            logger.debug("Events db busy")
            return ''

        return path

    def is_path_shared(self, path):
        sharing_info = self._ss_client.get_sharing_info()
        abs_path = PathConverter(self._root).create_abspath(path)
        try:
            # Given path is a file inside sync directory
            if os.path.isfile(abs_path):
                uuid = self.get_file_uuid(path, timeout=0.5)
            elif os.path.isdir(abs_path):
                uuid = self.get_folder_uuid(path, timeout=0.5)
            else:
                uuid = None
        except Exception:
            uuid = None

        logger.debug("UUID for path %s is %s. Sharing info %s",
                     path, uuid, sharing_info)

        # Check that UUID is known as shared
        return uuid and uuid in sharing_info

    def _try_clean_old_events(self, status, substatus, lc, rc, fsc):
        if self._download_backups_changed:
            self._cleaning_old_events_pending = True

        logger.debug("Trying to clean old events: %s, %s",
                     self._cleaning_old_events_pending,
                     self._loading_missed_events)
        with self._remote_events_lock:
            if status == STATUS_WAIT and not self._loading_missed_events and \
                    self._cleaning_old_events_pending:
                self.clean_old_events()
                self._cleaning_old_events_pending = False
            elif status != STATUS_WAIT and not self._cfg.download_backups:
                self._cleaning_old_events_pending = True

    def save_last_event_uuid(self, last_event_uuid):
        self._last_event_uuid = last_event_uuid
        self._cleaning_old_events_pending = True

    @qt_run
    def clean_old_events(self):
        '''
        Cleans old events from events_db,
        removes old patches,
        removes old copies fro deleted files
        @param last_event_uuid: last event that won't be cleaned
        '''

        self._process_backups_download()

        if not self._last_event_uuid:
            return

        logger.info("Deleting all events before %s", self._last_event_uuid)
        with self._db.create_session(read_only=False) as session:
            last_event = self._db.get_registered_event_by_event_uuid(
                self._last_event_uuid, session=session)
            if not last_event:
                logger.warning("Event with uuid %s not found",
                               self._last_event_uuid)
                return

            self._last_event_server_event_id = last_event.server_event_id
            self._remove_patches_time = time.time() + 10 * 60

            files_to_delete = self._db.get_files_with_deletes_prior_to(
                last_event.server_event_id, session=session)
            logger.debug("files_to_delete %s", log_sequence(files_to_delete))
            events_to_delete = []
            self._populate_files_events_from_subdirs(
                files_to_delete, events_to_delete, session)
            # remove copy references
            hashes = [f.event.file_hash if f.event.file_hash
                      else f.event.file_hash_before_event
                      for f in files_to_delete
                      if f.event and f.event.file_size]
            list(map(lambda h, f: self._copies_storage.remove_copy_reference(
                h, reason="clean_old_events. File {}".format(f.name)),
                     hashes, files_to_delete))
            logger.debug("Removed copies referencies for %s copies",
                         len(hashes))

            self._delete_old_events(last_event, events_to_delete, session)

            for file in files_to_delete:
                file.events = []

            self.calculate_processing_events_count()
            if not self._event_queue_idle or not self._monitor_idle:
                logger.debug("'Syncing' status, rollback delete old events")
                session.rollback()
            else:
                session.commit()
                # delete files
                list(map(session.delete, files_to_delete))
                logger.debug("Deleted %s old files", len(files_to_delete))

        self._last_event_uuid = None

    def _try_remove_old_patches(self, status):
        if status == STATUS_WAIT:
            if self._remove_patches_time and \
                    time.time() >= self._remove_patches_time:
                logger.debug("clean old patches")
                if self._cfg.download_backups:
                    self._remove_old_patches()
                else:
                    self._patches_storage.clean(with_files=True)
        else:
            self._remove_patches_time = 0

    @qt_run
    def _remove_old_patches(self):
        self._remove_patches_time = 0
        updates_to_delete = self._db.get_updates_registered_prior_to(
            self._last_event_server_event_id)
        self._last_event_server_event_id = 0

        # remove old patches
        list(map(
            lambda e:
            not self._patches_storage.remove_direct_patch(
                e.diff_file_uuid,
                reason="clean_old_events. Event {}. File {}"
                    .format(e.uuid, e.file_name))
            and not self._patches_storage.remove_reverse_patch(
                e.rev_diff_file_uuid,
                reason="clean_old_events. Event {}. File {}"
                    .format(e.uuid, e.file_name)), updates_to_delete))
        logger.debug("Removed patches references for %s events",
                     len(updates_to_delete))

    def _populate_files_events_from_subdirs(self, files_to_delete,
                                            events_to_delete, session):
        logger.debug("Adding files to delete from subfolders")
        folder_ids = [f.id for f in files_to_delete if f.is_folder]
        file_ids = {f.id for f in files_to_delete}
        files, events = self._db.get_files_events_form_subdirs(
            folder_ids, file_ids, purpose='to delete ', session=session)
        files_to_delete.extend(files)
        events_to_delete.extend(events)

    def _delete_old_events(self, last_event, events_to_delete, session):
        list(map(session.delete, events_to_delete))

        remaining_events_to_delete = self._db.get_previous_events_prior_to(
            last_event.server_event_id, session=session)
        logger.debug("remaining_events_to_delete %s", log_sequence(
            remaining_events_to_delete))
        list(map(session.delete, remaining_events_to_delete))

        logger.debug("Deleted %s old events",
                     len(events_to_delete) + len(remaining_events_to_delete))

    def _process_backups_download(self):
        try:
            if self._cfg.download_backups:
                if self._download_backups_changed:
                    logger.debug("Force create copies")
                    if self.fs:
                        self.fs.force_create_copies()

                self.download_backups()
            else:
                self._last_event_uuid = self._db.get_max_server_event_uuid()
                self._copies_storage.clean(with_files=True, with_signatures=False)
                if self.fs:
                    self.fs.delete_old_signatures()
        finally:
            self._download_backups_changed = False

    def clean_unnecessary_copies(self):
        @qt_run
        def clean():
            logger.debug("Cleaning unnecessary copies")
            self._copies_storage.clean_unnecessary()
            if self._started:
                self._copies_storage.remove_copies_not_in_db()
            self._copies_cleaned.emit()

        if not self._event_queue_idle or not self._monitor_idle \
                or not self._copies_storage or \
                not self._download_tasks_idle or \
                not self._share_idle:
            return False

        logger.debug("Before cleaning unnecessary copies")
        clean()
        return True

    def _on_copies_cleaned(self):
        self._get_sync_dir_size(recalculate=True)

    def is_dir_excluded(self, rel_path):
        return is_contained_in_dirs(rel_path, self._excluded_dirs_relpaths)

    def set_excluded_dirs(self):
        if not self.fs or not self._event_queue:
            return

        logger.debug("Setting excluded dirs to %s", self._cfg.excluded_dirs)
        started = self._started
        if started:
            self.pause()
        with self._excluded_lock:
            excluded_set = self._excluded_set
            if excluded_set:
                self._excluded_set = False
            self._excluded_dirs_relpaths = self._cfg.excluded_dirs
            pc = PathConverter(self._root)
            excluded_dirs_abs_paths = list(map(
                lambda p: pc.create_abspath(p), self._excluded_dirs_relpaths))
            self.fs.set_excluded_dirs(excluded_dirs_abs_paths)
            self._event_queue.set_excluded_dirs(self._excluded_dirs_relpaths)
        if excluded_set:
            self._process_excluded()
        if started:
            self.start()

    @qt_run
    def _process_excluded(self):
        excluded_marked = False
        try:
            while True:
                with self._excluded_lock:
                    if set(self._excluded_dirs_relpaths) == \
                            set(self._cfg.excluded_dirs_applied):
                        break
                self._mark_files_as_excluded()
                excluded_marked = True
        except Exception as e:
            logger.error("Error marking excluded dirs: %s", e)
        finally:
            with self._excluded_lock:
                self._excluded_set = True
            if excluded_marked:
                self._update_status(force_update=True)

    @benchmark
    def _mark_files_as_excluded(self):
        excluded_dirs = set(self._excluded_dirs_relpaths)
        excluded_applied = set(self._cfg.excluded_dirs_applied)
        excluded_added = excluded_dirs - excluded_applied
        excluded_removed = excluded_applied - excluded_dirs
        excluded_retained = excluded_dirs & excluded_applied
        excluded_is_changed = False
        with self._db.create_session(read_only=False) as session:
            excluded_is_changed = self._process_excluded_added(
                excluded_added, excluded_is_changed, session)
            self._process_excluded_removed(excluded_removed, session)

            if excluded_added:
                excluded_is_changed = self._process_paths_not_successfull(
                    excluded_added, excluded_is_changed, session)

        self._cfg.set_settings(dict(
            excluded_dirs_applied=
            [ed for ed in excluded_retained | excluded_added]))
        excluded_is_changed = excluded_is_changed or \
                              self._clean_excluded(excluded_added)

        if excluded_is_changed:
            self.config_changed.emit()

    def _process_excluded_added(self, excluded_added, 
                                excluded_is_changed, session):
        excluded_dirs_ids = []
        for path in excluded_added.copy():
            try:
                folders = self._db.find_folders_by_future_path(
                    path, session=session)
            except AssertionError:
                folders = []
            if not folders:
                with self._excluded_lock:
                    self._on_remove_dir_from_excluded(
                        path, emit_change_signal=False)
                excluded_added.discard(path)
                excluded_is_changed = True
                continue
            folder = folders[0]
            excluded_dirs_ids.append(folder.id)
            if folder.event:
                folder.event.state = 'downloaded'
            folder.event_id = None
            folder.excluded = True

        for file_id in excluded_dirs_ids:
            self._db.mark_child_excluded(file_id, session)
        logger.debug("Marked %s folders as excluded in db",
                     len(excluded_dirs_ids))
        return excluded_is_changed

    def _process_excluded_removed(self, excluded_removed, session):
        unexcluded_dirs_ids = []
        for path in excluded_removed:
            try:
                folders = self._db.find_folders_by_future_path(
                    path, session=session)
            except AssertionError:
                folders = []
            if not folders:
                logger.warning("Haven't found folders to remove "
                               "from excluded dirs by path %s", path)
                continue
            for folder in folders:
                if folder.excluded:
                    unexcluded_dirs_ids.append(folder.id)
                    folder.excluded = False

        for file_id in unexcluded_dirs_ids:
            self._db.mark_child_excluded(file_id, session,
                                         is_excluded=False)
        logger.debug("Marked %s folders as not excluded in db",
                     len(unexcluded_dirs_ids))

    def _process_paths_not_successfull(self, excluded_added,
                                       excluded_is_changed, session):
        paths_not_successful = \
            self._event_queue.process_non_registered_in_excluded(
                session)
        for path in paths_not_successful:
            path = FilePath(path)
            for excluded_path in excluded_added.copy():
                if path in excluded_path:
                    with self._excluded_lock:
                        self._on_remove_dir_from_excluded(
                            excluded_path, emit_change_signal=False)
                    excluded_added.discard(path)
                    excluded_is_changed = True
                    break
            return excluded_is_changed

    def process_offline_changes(self, status, substatus, l, r):
        logger.debug("process_offline_changes")
        if self.fs and status == STATUS_WAIT:
            self.fs.process_offline_changes()

    @qt_run
    def revert_hanged_tasks(self, hanged_files, hanged_patches):
        if not self._started:
            QTimer.singleShot(
                1000, lambda: self.revert_hanged_tasks(
                    hanged_files, hanged_patches))

        logger.debug("Reverting files %s, patches %s",
                     hanged_files, hanged_patches)
        try:
            event_uuids = hanged_files
            patch_uuids = hanged_patches
            patch_event_uuids = self._event_queue\
                .get_event_uuids_by_patch_uuids(patch_uuids)
            all_uuids = event_uuids + patch_event_uuids
            file_ids = self._db.get_file_ids_by_event_uuids(all_uuids)

            for file_id in file_ids:
                if not self._started:
                    raise Exception("Can't revert files when sync stopped")
                self._event_queue.discard_hang_file_changes(
                    file_id, patch_uuids)
        except Exception as e:
            logger.error("Error reverting files: %s", e)
            self.revert_failed.emit(hanged_files + hanged_patches)


    @qt_run
    def _on_no_disk_space(self, task_or_event, display_name,
                          is_error, is_remote):
        if display_name:
            path = op.join(self._cfg.sync_directory, display_name) \
                if is_remote else display_name
            self.file_added_to_disk_error.emit(FilePath(path))

        if not self._alert_allowed("no disk space"):
            return

        display_name = op.basename(display_name)
        msg = tr('Insufficient disk space '
                 'to complete operation for file {}')
        Application.show_tray_notification(msg.format(display_name))

    def exit(self):
        if self.fs:
                self.fs.quit()

        if self._download_manager:
            self._download_manager.quit.emit()
        self._connectivity_service.quit.emit()
        logger.debug("Connectivity service quit")

    def _on_exit(self):
        self._connectivity_service_thread.quit()
        self._connectivity_service_thread.wait()
        logger.debug("Connectivity service thread quit")
        self._download_manager_thread.quit()
        self._download_manager_thread.wait()
        self.exited.emit()

    def _quit_connectivity(self):
        self._connectivity_service.disconnect_ss_slots.emit()
        if self._download_manager:
            self._download_manager.quit.emit()
            self._download_manager = None
        self._connectivity_service_thread.quit()

    def is_connectivity_alive(self):
        return self._connectivity_service.is_alive()

    def restart_connectivity(self):
        self.stop()
        self._quit_connectivity()
        self._init_connectivity()
        self._apply_config()
        self.start()

    def _on_collaborated_folders(self, collaborated_folders):
        logger.debug("Collaborated folders obtained %s",
                     collaborated_folders)
        self._collaborated_folders = collaborated_folders
        if self._event_queue:
            try:
                self._event_queue.set_collaborated_folders_icons(
                    collaborated_folders)
            except EventsDbBusy:
                logger.debug("Events db busy")
                self.collaborated_folders_obtained.emit(
                    collaborated_folders)
        else:
            self._collaborated_folders_pending = collaborated_folders

    def reset_all_collaboration_folder_icons(self):
        self._collaborated_folders = []
        self.fs.reset_all_collaboration_folder_icons()

    def download_backups(self):
        logger.debug("Downloading backups...")
        downloads_list = []
        with self._db.create_session(read_only=True) as session:
            events = self._db.get_backups_events(session=session)

            for event in events:
                if not self._started:
                    break

                hash = event.file_hash if event.file_hash \
                    else event.file_hash_before_event
                file_size = event.file_size if event.file_size \
                    else event.file_size_before_event
                if not hash or file_size == 0 or \
                        self._copies_storage.copy_exists(hash):
                    continue

                path = os.path.join(get_copies_dir(self._root), hash)
                path = ensure_unicode(path)
                display_name = 'Syncing backup for file {}'.format(
                    event.file_name)
                priority = DOWNLOAD_PRIORITY_REVERSED_PATCH

                logger.debug("Preparing backup download %s, name %s",
                             event.uuid, event.file_name)
                downloads_list.append((
                    priority, event.uuid, file_size,
                    hash, path, display_name, None, None))

        if downloads_list:
            self._download_manager.add_many_file_downloads(downloads_list)

    def add_special_file(self, path, on_event_cb):
        self._special_files[path] = on_event_cb
        if self.fs:
            self.fs.add_special_file(path)

    def _restore_special_files(self):
        for path in self._special_files:
            self.fs.add_special_file(path)

    def remove_special_file(self, path):
        logger.debug("Removing special file %s...", path)
        if self.fs:
            self.fs.remove_special_file(path)
        try:
            self._special_files.pop(path)
        except KeyError:
            logger.warning("Can't remove special file %s from dict %s",
                           path, self._special_files)

    def _on_update_special_paths(self, old_path, new_path):
        logger.debug("Updating special paths from %s to %s",
                     old_path, new_path)
        if not self.fs:
            return
        pc = PathConverter(self._root)
        new_special_files = {}
        old_abs_path = FilePath(pc.create_abspath(old_path))
        new_abs_path = pc.create_abspath(new_path)
        for special_file in self._special_files:
            if FilePath(special_file) in old_abs_path:
                rel_path = os.path.relpath(
                    FilePath(special_file), old_abs_path)
                new_file = FilePath(
                    os.path.join(new_abs_path, rel_path))
                new_special_files[new_file] = \
                    self._special_files[special_file]
                self.fs.change_special_file(special_file, new_file)
            else:
                new_special_files[special_file] = \
                    self._special_files[special_file]
        self._special_files = new_special_files

    def _on_special_file_event(self, path, event_type, new_path):
        callback = self._special_files.get(path)
        if callable(callback):
            callback(path, event_type, new_path)
            logger.debug("Called callback for special file event. "
                         "Path %s, event_type %s", path, event_type, new_path)

    def check_long_paths(self, status, substatus, l, r):
        if status == STATUS_WAIT and self._db.all_local_events_processsed():
            long_paths = self.fs.get_long_paths()
            if long_paths:
                self.long_paths_ignored.emit(list(long_paths))

    def get_long_paths(self):
        if not self.fs:
            return set()

        return {FilePath(p) for p in self.fs.get_long_paths()}

    def _on_remove_dir_from_excluded(self, directory, emit_change_signal=True):
        pc = PathConverter(self._root)
        abs_path = pc.create_abspath(directory)
        try:
            logger.debug("Removing excluded dir %s from %s",
                         directory, self._cfg.excluded_dirs)
            self._cfg.excluded_dirs.remove(directory)
            self._cfg.sync()
        except Exception as e:
            logger.warning("Can't remove excluded dir %s from %s. Reason: %s",
                           directory, self._cfg.excluded_dirs, e)
        if self.fs:
            self.fs.remove_dir_from_excluded(FilePath(abs_path))
        if emit_change_signal:
            self.config_changed.emit()

    def _on_change_excluded_dirs(self, dirs_to_delete, dirs_to_add):
        pc = PathConverter(self._root)
        for directory in dirs_to_delete:
            try:
                logger.debug("Removing excluded dir %s from %s",
                             directory, self._cfg.excluded_dirs)
                self._cfg.excluded_dirs.remove(directory)
                self._cfg.excluded_dirs_applied.remove(directory)
            except Exception as e:
                logger.warning("Can't remove excluded dir %s from %s. "
                               "Reason: %s",
                               directory, self._cfg.excluded_dirs, e)
        for directory in dirs_to_add:
            logger.debug("Adding excluded dir %s to %s",
                         directory, self._cfg.excluded_dirs)
            self._cfg.excluded_dirs.append(directory)
            self._cfg.excluded_dirs_applied.append(directory)

        self._cfg.sync()
        abs_paths_to_delete = [FilePath(pc.create_abspath(d))
                               for d in dirs_to_delete]
        abs_paths_to_add = [FilePath(pc.create_abspath(d))
                               for d in dirs_to_add]
        if self.fs:
            self.fs.change_excluded_dirs(abs_paths_to_delete, abs_paths_to_add)
        if dirs_to_delete or dirs_to_add:
            self.config_changed.emit()

    def _clean_excluded(self, excluded_to_clean):
        paths_to_remove = set()
        for path in excluded_to_clean:
            try:
                self.fs.accept_delete(path, is_directory=True)
            except self.fs.Exceptions.AccessDenied:
                logger.warning("Can't clean excluded dir %s", path)
                paths_to_remove.add(path)

        for path in paths_to_remove:
            self._on_remove_dir_from_excluded(path, emit_change_signal=False)

        return bool(paths_to_remove)

    def _on_access_denied(self, path):
        if not self._alert_allowed("access denied"):
            return

        pc = PathConverter(self._root)
        rel_path = pc.create_relpath(path)
        name = op.basename(rel_path)
        msg = tr("Operation for {} could not be completed, "
                 "because some file (subfolder) was blocked "
                 "by other application. Please close blocking app")
        Application.show_tray_notification(msg.format(name))

    @benchmark
    def calculate_processing_events_count(self):
        if not self._started or not self._event_queue:
            return

        # if not self._events_count_calculating and \
        #         not self._event_queue.is_initial_syncing() and \
        #         self._remote_events_count < self._recalculate_threshold:
        #     self._recalculate_processing_events_count()

        self._local_events_count, self._remote_events_count, \
        workers_busy, self._events_erased = \
            self._event_queue.get_processing_events_count()
        self._event_queue_idle = (self._local_events_count == 0 and
                                  self._remote_events_count == 0 and
                                  not workers_busy)
        self._update_status()

    @benchmark
    def _recalculate_processing_events_count(self):
        self._events_count_calculating = True
        try:
            with self._db.soft_lock():
                self._event_queue.recalculate_processing_events_count()
        except EventsDbBusy:
            logger.warning("Events DB busy")
        finally:
            self._events_count_calculating = False

    def get_server_event_ids(self, force_db_values=False):
        logger.debug("Getting server event ids...")
        if self._rerequest_all_events:
            self._db.clean()
            max_server_event_id = 0
            max_checked_server_event_id = 0
            events_count = 0
            self._rerequest_all_events = False
        elif self._max_remote_event_received and not force_db_values:
            max_server_event_id = self._max_remote_event_received
            max_checked_server_event_id = self._max_remote_event_received
            events_count = 0
        else:
            with self._db.create_session(read_only=True) as session:
                max_server_event_id = self._db.get_max_server_event_id(
                    session=session)
                if not self._get_download_backups_mode():
                    max_checked_server_event_id = max_server_event_id
                    events_count = 0
                else:
                    max_checked_server_event_id = \
                        self._db.get_max_checked_server_event_id(
                            session=session)
                    events_count = self._db.get_events_count(
                        max_checked_server_event_id, max_server_event_id,
                        session=session)

        return max_server_event_id, max_checked_server_event_id, events_count

    def _db_file_created_cb(self):
        logger.debug("DB file was created")
        self._rerequest_all_events = True

    def _notify_collaboration_move_error(self, old_path, new_path):
        msg = tr('Moving or renaming collaboration folder is not allowed. '
                 'Folder {0} moved back to {1}')
        Application.show_tray_notification(msg.format(new_path, old_path))

    def _get_download_backups_mode(self):
        return self._cfg.download_backups

    def download_backups_changed(self):
        logger.debug("Download backups changed")
        self._download_backups_changed = True

    def send_download_progress(self, text, percent, total):
        if self._started:
            logger.debug("Sending download progress: %s, %s, %s",
                         text, percent, total)
            self.download_progress.emit(text, percent, total)

    def send_downloads_status(self, text, percent, total,
                                      downloads_info, uploads_info):
        if not self._started:
            text = ""
            percent = total = None
        logger.debug("Sending downloads status: %s, %s, %s, %s, %s",
                     text, percent, total, downloads_info, uploads_info)
        self.downloads_status.emit(
            text, percent, total, downloads_info, uploads_info)

    def _on_file_list_changed(self):
        if not self._sending_file_list:
            self._sending_file_list = True
            self.file_list_changed.emit()

    def get_file_list(self):
        file_list = self.fs.get_file_list() if self.fs else []
        self._sending_file_list = False
        return file_list

    def _clear_excluded(self):
        del self._cfg.excluded_dirs[:]
        self._cfg.sync()
        if self.fs:
            self.fs.clear_excluded_dirs()

    def _delete_on_license_downgrade(self):
        self._db.delete_remote_events_not_applied()

    def _on_info_tx(self, info_tx):
        self.signal_info_tx.emit(info_tx)

    def _on_info_rx(self, info_rx):
        self.signal_info_rx.emit(info_rx)

    def make_copy_from_existing_files(self, copy_hash):
        if not self.fs:
            return False

        return self.fs.make_copy_from_existing_files(copy_hash)

    def _alert_allowed(self, alert_name):
        now = time.time()
        alert_data = self._alert_info.get(alert_name)
        if alert_data:
            interval_index, last_time_sent = alert_data
        else:
            interval_index = -1
        allow_alert = interval_index < 0 or now - last_time_sent > \
                      self._alert_intervals[interval_index]
        if allow_alert:
            if interval_index + 1 < len(self._alert_intervals):
                interval_index += 1
            self._alert_info[alert_name] = (interval_index, now)
        return allow_alert

    def _reset_alert_info(self, status, substatus, lc, rc, fsc):
        if status == STATUS_WAIT:
            self._alert_info.clear()

    def set_db(self, db):
        self._db = db

    def _on_connected_nodes_changed(self, nodes):
        self.connected_nodes_changed.emit(len(nodes))
