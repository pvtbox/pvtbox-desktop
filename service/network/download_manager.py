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
from glob import iglob
from heapq import heappop, heappush

import logging
from itertools import chain
from copy import deepcopy

from PySide2.QtCore import QObject, Signal, QTimer, Qt

from service.network.browser_sharing import Message, get_event_name, Messages
from os.path import join

from service.network.availability_info.file_availability_info_consumer \
    import FileAvailabilityInfoConsumer
from service.network.availability_info.file_availability_info_supplier \
    import FileAvailabilityInfoSupplier
from service.network.availability_info.patch_availability_info_consumer \
    import PatchAvailabilityInfoConsumer
from service.network.availability_info.patch_availability_info_supplier \
    import PatchAvailabilityInfoSupplier
from service.network.data.file_data_consumer import FileDataConsumer
from service.network.data.file_data_supplier import FileDataSupplier
from service.network.data.patch_data_consumer import PatchDataConsumer
from service.network.data.patch_data_supplier import PatchDataSupplier

from service.network.download_task.file_download_task \
    import FileDownloadTask
from service.network.download_task.patch_download_task \
    import PatchDownloadTask
from service.network.download_task.download_task \
    import DownloadTask
from common.constants import IMPORTANT_DOWNLOAD_PRIORITY, \
    DOWNLOAD_PRIORITY_WANTED_DIRECT_PATCH, \
    DOWNLOAD_NOT_READY, DOWNLOAD_READY, DOWNLOAD_STARTING, \
    DOWNLOAD_LOADING, DOWNLOAD_FINISHING, DOWNLOAD_FAILED, \
    DOWNLOAD_NO_DISK_ERROR
from common.utils import remove_file

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DownloadManager(QObject):
    idle = Signal()
    working = Signal()
    progress = Signal(str, int, int)
    downloads_status = Signal(str, int, int,
                              list,     # downloads info -
                              # list(added_info, changed_info, deleted_info)
                              dict)     # uploads_info
    error = Signal(str)
    clear_error = Signal()

    no_disk_space = Signal(QObject,     # task
                           str,     # display_name
                           bool)        # is error

    # workaround for PySide crash. see
    # https://stackoverflow.com/questions/23728401
    # /pyside-crashing-python-when-emitting-none-between-threads
    # tuple is (int, unicode, long, unicode, unicode, object, object)
    _file_download = Signal(tuple)
    # tuple is (int, unicode, long, unicode, unicode, object, object,
    # object, list)
    _patch_download = Signal(tuple)
    _many_file_downloads = Signal(list)     # downloads list
    _pause_all = Signal(bool)
    _resume_all = Signal()
    _cancel_all = Signal()
    _cancel_one = Signal(str)
    _accept_one = Signal(str)
    _prepare_cleanup = Signal()

    # tuple is (object, )
    _set_download_limiter = Signal(tuple)
    _task_priority = Signal(str,    # obj_id
                            int)        # new priority
    _copy_added = Signal(str)

    on_patch_availability_info_request = Signal(Message, str)
    on_patch_availability_info_response = Signal(Message, str)
    on_patch_availability_info_abort = Signal(Message, str)
    on_patch_availability_info_failure = Signal(Message, str)
    on_patch_data_request = Signal(Message, str)
    on_patch_data_response = Signal(Message, str)
    on_patch_data_abort = Signal(Message, str)
    on_patch_data_failure = Signal(Message, str)
    on_file_availability_info_request = Signal(Message, str)
    on_file_availability_info_response = Signal(Message, str)
    on_file_availability_info_abort = Signal(Message, str)
    on_file_availability_info_failure = Signal(Message, str)
    on_file_data_request = Signal(Message, str)
    on_file_data_response = Signal(Message, str)
    on_file_data_abort = Signal(Message, str)
    on_file_data_failure = Signal(Message, str)
    on_file_availability_info_requests = Signal(Messages, str)
    on_file_availability_info_responses = Signal(Messages, str)
    on_patch_availability_info_requests = Signal(Messages, str)
    on_patch_availability_info_responses = Signal(Messages, str)
    signal_info_tx = Signal(tuple)
    signal_info_rx = Signal(tuple)

    quit = Signal()

    possibly_sync_folder_is_removed = \
        DownloadTask.possibly_sync_folder_is_removed
    supplying_finished = Signal()

    ready_timeout = 10 * 1000
    download_error_timeout = 10 * 1000
    rerequest_info_timeout = 10 * 1000
    cleanup_timeout = 5 * 60 * 1000
    progress_timeout = 1 * 1000

    def __init__(self, connectivity_service, ss_client,
                 events_db=None, copies_storage=None, patches_storage=None,
                 upload_enabled=True, tracker=None, parent=None,
                 get_download_backups_mode=lambda: None,
                 get_file_path=lambda p, sq=False: ""):
        QObject.__init__(self, parent=parent)

        self._paused = False
        self._connectivity_service = connectivity_service
        self._ss_client = ss_client
        self._tracker = tracker
        self._events_db = events_db
        self._copies_storage = copies_storage
        self._patches_storage = patches_storage
        self._upload_enabled = upload_enabled

        self._downloads = dict()
        self._ready_downloads_queue = []
        self._current_task = None
        self._node_incoming_list = self._connectivity_service. \
            get_connected_incoming_nodes().copy()
        self._node_outgoing_list = self._connectivity_service.\
            get_connected_outgoing_nodes().copy()
        self._important_downloads_info = dict()
        self._last_uploads_info = dict()

        self._limiter = None

        self._info_priority = DOWNLOAD_PRIORITY_WANTED_DIRECT_PATCH
        self._empty_progress = ("", 0, 0)
        self._last_progress_sent = self._empty_progress
        self._error_set = False

        self._cleanup_directories = []

        self._init_suppliers(get_download_backups_mode, get_file_path)
        self._init_consumers()
        self._connect_slots()
        self._init_timers()

    def _init_suppliers(self, get_download_backups_mode, get_file_path):
        if not self._upload_enabled:
            self._file_availability_info_supplier = None
            self._patch_availability_info_supplier = None
            self._file_data_supplier = None
            self._patch_data_supplier = None
            return

        self._file_availability_info_supplier = \
            FileAvailabilityInfoSupplier(
                self, self, self._connectivity_service,
                self._node_incoming_list, self._events_db, self._copies_storage,
                get_download_backups_mode,
                get_file_path)

        self._patch_availability_info_supplier = \
            PatchAvailabilityInfoSupplier(
                self, self, self._connectivity_service,
                self._node_incoming_list, self._patches_storage)

        self._file_data_supplier = FileDataSupplier(
            self, self._connectivity_service,
            self._events_db, self._copies_storage,
            get_file_path)

        self._patch_data_supplier = PatchDataSupplier(
            self, self._connectivity_service,
            self._patches_storage, self._events_db)

        self._connect_suppliers_slots()

    def _connect_suppliers_slots(self):
        self.on_file_availability_info_request.connect(
            self._file_availability_info_supplier._availability_info_request,
            Qt.QueuedConnection)
        self.on_file_availability_info_abort.connect(
            self._file_availability_info_supplier._availability_info_abort)
        self.on_patch_availability_info_request.connect(
            self._patch_availability_info_supplier._availability_info_request,
            Qt.QueuedConnection)
        self.on_patch_availability_info_abort.connect(
            self._patch_availability_info_supplier._availability_info_abort)
        self.on_file_availability_info_requests.connect(
            self._file_availability_info_supplier._availability_info_requests,
            Qt.QueuedConnection)
        self.on_patch_availability_info_requests.connect(
            self._patch_availability_info_supplier._availability_info_requests,
            Qt.QueuedConnection)

        self.on_file_data_request.connect(
            self._file_data_supplier._data_request, Qt.QueuedConnection)
        self.on_file_data_abort.connect(self._file_data_supplier._data_abort)
        self.on_patch_data_request.connect(
            self._patch_data_supplier._data_request, Qt.QueuedConnection)
        self.on_patch_data_abort.connect(self._patch_data_supplier._data_abort)

        self._connectivity_service.node_incoming_disconnected.connect(
            self._file_availability_info_supplier.on_node_disconnected,
            Qt.QueuedConnection)
        self._connectivity_service.node_incoming_disconnected.connect(
            self._patch_availability_info_supplier.on_node_disconnected,
            Qt.QueuedConnection)
        self._connectivity_service.connected_nodes_incoming_changed.connect(
            self._file_availability_info_supplier.on_connected_nodes_changed,
            Qt.QueuedConnection)
        self._connectivity_service.connected_nodes_incoming_changed.connect(
            self._patch_availability_info_supplier.on_connected_nodes_changed,
            Qt.QueuedConnection)

        # connect traffic info signal 'signal_info_tx'
        self._file_data_supplier.signal_info_tx.connect(
            self._on_info_tx, Qt.QueuedConnection)
        self._patch_data_supplier.signal_info_tx.connect(
            self._on_info_tx, Qt.QueuedConnection)

        self._file_data_supplier.supplying_finished.connect(
            self.supplying_finished.emit, Qt.QueuedConnection)

    def _init_consumers(self):
        self._file_availability_info_consumer = FileAvailabilityInfoConsumer(
            self, self._connectivity_service, self._node_outgoing_list)
        self._patch_availability_info_consumer = PatchAvailabilityInfoConsumer(
            self, self._connectivity_service, self._node_outgoing_list)

        self._file_data_consumer = FileDataConsumer(
            self, self._connectivity_service)
        self._patch_data_consumer = PatchDataConsumer(
            self, self._connectivity_service)

        self._connect_consumers_slots()

    def _connect_consumers_slots(self):
        self.on_file_availability_info_response.connect(
            self._file_availability_info_consumer._availability_info_response,
            Qt.QueuedConnection)
        self.on_file_availability_info_failure.connect(
            self._file_availability_info_consumer._availability_info_failure,
            Qt.QueuedConnection)
        self.on_patch_availability_info_response.connect(
            self._patch_availability_info_consumer._availability_info_response,
            Qt.QueuedConnection)
        self.on_patch_availability_info_failure.connect(
            self._patch_availability_info_consumer._availability_info_failure,
            Qt.QueuedConnection)
        self.on_file_availability_info_responses.connect(
            self._file_availability_info_consumer._availability_info_responses,
            Qt.QueuedConnection)
        self.on_patch_availability_info_responses.connect(
            self._patch_availability_info_consumer
                ._availability_info_responses,
            Qt.QueuedConnection)

        self._file_availability_info_consumer.availability_info_received\
            .connect(self._on_availability_info_received,
                     Qt.QueuedConnection)
        self._file_availability_info_consumer.availability_info_failure\
            .connect(self._on_availability_info_failure,
                     Qt.QueuedConnection)
        self._file_data_consumer.data_received.connect(
            self._on_task_data_received,  Qt.QueuedConnection)
        self._file_data_consumer.error_received.connect(
            self._on_task_data_failed, Qt.QueuedConnection)
        self._patch_availability_info_consumer.availability_info_received\
            .connect(self._on_availability_info_received,
                     Qt.QueuedConnection)
        self._patch_availability_info_consumer.availability_info_failure\
            .connect(self._on_availability_info_failure,
                     Qt.QueuedConnection)
        self._patch_data_consumer.data_received.connect(
            self._on_task_data_received, Qt.QueuedConnection)
        self._patch_data_consumer.error_received.connect(
            self._on_task_data_failed, Qt.QueuedConnection)

        self.on_file_data_response.connect(
            self._file_data_consumer._data_response, Qt.QueuedConnection)
        self.on_file_data_failure.connect(
            self._file_data_consumer._data_failure, Qt.QueuedConnection)
        self.on_patch_data_response.connect(
            self._patch_data_consumer._data_response, Qt.QueuedConnection)
        self.on_patch_data_failure.connect(
            self._patch_data_consumer._data_failure, Qt.QueuedConnection)

        self._connectivity_service.connected_nodes_outgoing_changed.connect(
            self._file_availability_info_consumer.on_connected_nodes_changed,
            Qt.QueuedConnection)
        self._connectivity_service.connected_nodes_outgoing_changed.connect(
            self._patch_availability_info_consumer.on_connected_nodes_changed,
            Qt.QueuedConnection)
        self._connectivity_service.node_outgoing_connected.connect(
            self._file_availability_info_consumer.on_node_connected,
            Qt.QueuedConnection)
        self._connectivity_service.node_outgoing_connected.connect(
            self._patch_availability_info_consumer.on_node_connected,
            Qt.QueuedConnection)

    def _connect_slots(self):
        self._connectivity_service.data_received.connect(
            self._on_data_received,
            Qt.QueuedConnection)

        self._connectivity_service.connected_nodes_outgoing_changed.connect(
            self._connected_nodes_outgoing_changed,
            Qt.QueuedConnection)
        self._connectivity_service.connected_nodes_incoming_changed.connect(
            self._connected_nodes_incoming_changed,
            Qt.QueuedConnection)

        self._file_download.connect(self._add_file_download,
                                    Qt.QueuedConnection)
        self._patch_download.connect(self._add_patch_download,
                                     Qt.QueuedConnection)
        self._many_file_downloads.connect(self._add_many_file_downloads,
                                          Qt.QueuedConnection)
        self._pause_all.connect(self._on_pause_all_downloads,
                                Qt.QueuedConnection)
        self._resume_all.connect(self._on_resume_all_downloads,
                                 Qt.QueuedConnection)
        self._cancel_all.connect(self._on_cancel_all_downloads,
                                 Qt.QueuedConnection)
        self._cancel_one.connect(self._on_cancel_download,
                                 Qt.QueuedConnection)

        self._accept_one.connect(self._on_accept_download,
                                 Qt.QueuedConnection)

        self._set_download_limiter.connect(self._on_set_download_limiter,
                                           Qt.QueuedConnection)
        self._task_priority.connect(self._on_set_task_priority,
                                    Qt.QueuedConnection)
        self._copy_added.connect(self._on_copy_added, Qt.QueuedConnection)
        self._prepare_cleanup.connect(self._on_prepare_cleanup,
                                      Qt.QueuedConnection)
        self.quit.connect(self._on_quit, Qt.QueuedConnection)

    def _init_timers(self):
        self._timers = []

        self._ready_timer = QTimer(self)
        self._ready_timer.setInterval(self.ready_timeout)
        self._ready_timer.setSingleShot(True)
        self._ready_timer.timeout.connect(self._check_downloads)
        self._timers.append(self._ready_timer)

        self._downloads_error_timer = QTimer(self)
        self._downloads_error_timer.setInterval(self.download_error_timeout)
        self._downloads_error_timer.setSingleShot(True)
        self._downloads_error_timer.timeout.connect(
            self._check_send_download_error)
        self._timers.append(self._downloads_error_timer)

        self._rerequest_info_timer = QTimer(self)
        self._rerequest_info_timer.setInterval(self.rerequest_info_timeout)
        self._rerequest_info_timer.setSingleShot(True)
        self._rerequest_info_timer.timeout.connect(
            self._rerequest_info_for_not_ready_downloads)
        self._timers.append(self._rerequest_info_timer)

        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(self.cleanup_timeout)
        self._cleanup_timer.setSingleShot(True)
        self._cleanup_timer.timeout.connect(
            self._on_cleanup)
        self._timers.append(self._cleanup_timer)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(self.progress_timeout)
        self._progress_timer.timeout.connect(
            self._on_download_progress)
        self._timers.append(self._progress_timer)
        self._progress_timer.start()

    def get_downloads_count(self):
        return len(self._downloads)

    def is_download_ready(self, obj_id):
        for task in self._ready_downloads_queue:
            if task.id == obj_id:
                return True
        return False

    def set_info_priority(self, new_priority):
        self._info_priority = new_priority

    def prepare_cleanup(self, cleanup_directories):
        self._cleanup_directories = cleanup_directories
        self._prepare_cleanup.emit()

    def set_download_limiter(self, limiter):
        self._set_download_limiter.emit((limiter, ))

    def pause_all_downloads(self, disconnect_callbacks=True):
        self._pause_all.emit(disconnect_callbacks)

    def resume_all_downloads(self):
        self._resume_all.emit()

    def cancel_all_downloads(self):
        self._cancel_all.emit()

    def cancel_download(self, obj_id):
        self._cancel_one.emit(obj_id)

    def accept_download(self, obj_id):
        self._accept_one.emit(obj_id)

    def add_file_download(
            self, priority, obj_id, obj_size, file_hash, path, display_name,
            on_downloaded=None, on_failed=None, files_info=None):
        logger.info("adding file download, id: %s, size: %s, priority: %s",
                    obj_id, obj_size, priority)
        self._file_download.emit((
            priority, obj_id, obj_size, file_hash, path, display_name,
            on_downloaded, on_failed, files_info))

    def add_many_file_downloads(self, downloads_list):
        logger.info("adding many file downloads: %s", len(downloads_list))
        self._many_file_downloads.emit(downloads_list)

    def add_patch_download(
            self, priority, obj_id, obj_size, path, display_name,
            on_downloaded=None, on_failed=None, files_info=None):
        logger.info("adding patch download, id: %s, size: %s, priority: %s",
                    obj_id, obj_size, priority)
        self._patch_download.emit((
            priority, obj_id, obj_size, path, display_name,
            on_downloaded, on_failed, files_info))

    def get_downloaded_chunks(self, obj_id):
        task = self._downloads.get(obj_id, None)
        return task.get_downloaded_chunks() if task else set()

    def on_file_changed(self, event_uuid_before, event_uuid_after):
        if self._file_availability_info_supplier:
            self._file_availability_info_supplier.on_file_changed(
                event_uuid_before, event_uuid_after)

    def set_task_priority(self, obj_id, new_priority):
        logger.debug("Setting priority %s for task %s", new_priority, obj_id)
        self._task_priority.emit(obj_id, new_priority)

    def copy_added(self, file_hash):
        logger.debug("Copy added. File hash: %s", file_hash)
        self._copy_added.emit(file_hash)

    def _on_prepare_cleanup(self):
        logger.debug("Download manager prepare cleanup")
        self._cleanup_timer.start()

    def _on_set_download_limiter(self, limiter_tuple):
        self._limiter, = limiter_tuple
        if self._current_task:
            self._current_task.start(self._limiter)

    def _on_cleanup(self):
        if self._downloads:
            return

        logger.debug("Cleaning all download files in %s",
                     self._cleanup_directories)
        for paths in chain(
                (iglob(join(d, '*.download'))
                       for d in self._cleanup_directories),
                (iglob(join(d, '*.info'))
                       for d in self._cleanup_directories)):
            for path in paths:
                try:
                    remove_file(path)
                except:
                    pass

    def _on_pause_all_downloads(self, disconnect_callbacks=True):
        self._paused = True
        for download in self._downloads.values():
            download.pause(disconnect_callbacks)

        if self._ready_timer.isActive():
            self._ready_timer.stop()

        self._on_download_progress()
        self.idle.emit()

    def _on_resume_all_downloads(self):
        self._paused = False
        for download in self._downloads.values():
            if download is not self._current_task:
                download.resume(start_download=False)

        if not self._current_task:
            self._start_next_task()
        else:
            self._current_task.resume()

        if self._get_important_downloads_count():
            self.working.emit()
        self._on_download_progress(force_sending=True)

    def _on_cancel_all_downloads(self):
        logger.debug("_on_cancel_all_downloads")
        for download in self._downloads.values():
            download.cancel()
            self._finish_task(download)

        self._current_task = None
        self._downloads.clear()
        self._ready_downloads_queue = list()

        if self._ready_timer.isActive():
            self._ready_timer.stop()

        self._on_download_progress()
        self.idle.emit()

    def _on_cancel_download(self, obj_id):
        task = self._find_task_by_id(obj_id)
        if task:
            task.download_failed.emit(task)
            self._clear_network_error()

    def _on_accept_download(self, obj_id):
        task = self._find_task_by_id(obj_id)
        if task:
            task.download_complete.emit(task)
            self._clear_network_error()

    def _on_data_received(self, param_tuple, connection_id):
        node_id, data = param_tuple
        try:
            messages = Messages().decode(data, node_id)
            msg = messages.msg[0]
            event_name = get_event_name(
                msg.obj_type, msg.mtype, repeating=True)
            logger.debug("Repeating event detected: event %s, obj_id %s, "
                         "node_id %s, connection_id %s",
                         event_name, msg.obj_id, node_id, connection_id)
            signal = getattr(self, event_name, None)
            if isinstance(signal, Signal):
                signal.emit(messages, node_id)
        except Exception as e:
            try:
                msg = Message().decode(data, node_id)
                event_name = get_event_name(msg.obj_type, msg.mtype)
                logger.debug("Event detected: event %s, obj_id %s, "
                             "node_id %s, connection_id %s",
                             event_name, msg.obj_id, node_id, connection_id)
                signal = getattr(self, event_name, None)
                if isinstance(signal, Signal):
                    signal.emit(msg, node_id)
            except Exception:
                logger.error("Unhandled exception while processing")
                logger.error("data: '%s'", data)

    def _add_file_download(self, param_tuple, to_subscribe=True):
        priority, obj_id, obj_size, file_hash, file_path, display_name, \
        on_downloaded, on_failed, files_info = param_tuple

        task = self._find_task_by_id(obj_id)
        if task:
            task.connect_callbacks(on_downloaded, on_failed)
            return

        task = FileDownloadTask(
            self._tracker, self._connectivity_service,
            priority, obj_id, obj_size, file_path, file_hash, display_name,
            parent=self, files_info=files_info)

        self._connect_task_signals(
            task,
            self._file_availability_info_supplier,
            self._file_data_consumer,
            on_downloaded, on_failed)

        self._downloads[obj_id] = task

        if not task.check_disk_space():
            self._on_download_not_ready(task)
            return

        if to_subscribe:
            self._file_availability_info_consumer.subscribe(
                obj_id, priority=priority)

        self._emit_add_download_signals(
            emit_working=priority > IMPORTANT_DOWNLOAD_PRIORITY)

        if self._cleanup_timer.isActive():
            self._cleanup_timer.stop()

    def _add_many_file_downloads(self, downloads_list):
        list(map(
            lambda d: self._add_file_download((*d, None), to_subscribe=False),
            downloads_list))
        # d[1] - obj_id, d[0] - priority
        subscription_list = [(d[1], d[0]) for d in downloads_list]
        self._file_availability_info_consumer.subscribe_many(subscription_list)

    def _add_patch_download(self, param_tuple):
        priority, obj_id, obj_size, file_path, display_name, on_downloaded, \
            on_failed, files_info = param_tuple

        task = self._find_task_by_id(obj_id)
        if task:
            task.connect_callbacks(on_downloaded, on_failed)
            return

        task = PatchDownloadTask(
            self._tracker, self._connectivity_service,
            priority, obj_id, obj_size, file_path, display_name,
            parent=self, files_info=files_info)

        self._connect_task_signals(
            task,
            self._patch_availability_info_supplier,
            self._patch_data_consumer,
            on_downloaded, on_failed)

        self._downloads[obj_id] = task

        if not task.check_disk_space():
            return

        self._patch_availability_info_consumer.subscribe(
            obj_id, priority=priority)

        self._emit_add_download_signals(
            emit_working=priority > IMPORTANT_DOWNLOAD_PRIORITY)

        if self._cleanup_timer.isActive():
            self._cleanup_timer.stop()

    def _on_set_task_priority(self, obj_id, new_priority):
        task = self._find_task_by_id(obj_id)
        if not task:
            return

        task.priority = new_priority
        if task is self._current_task:
            self._swap_current_task()

        if not self._get_important_downloads_count():
            self.idle.emit()
        logger.debug("Priority %s for task.id %s is set", new_priority, obj_id)

    def _swap_current_task(self):
        if not self._ready_downloads_queue:
            return

        first_task = heappop(self._ready_downloads_queue)
        heappush(self._ready_downloads_queue, first_task)
        if first_task < self._current_task:
            self._current_task.pause(disconnect_cb=False)
            heappush(self._ready_downloads_queue, self._current_task)
            logger.debug("Task %s with priority %s "
                         "swapped by task %s with priority %s",
                         self._current_task.id, self._current_task.priority,
                         first_task.id, first_task.priority)
            self._start_next_task()

    def _connect_task_signals(
            self, task, info_supplier, data_consumer,
            on_downloaded, on_failed):
        self._connectivity_service.node_outgoing_disconnected.connect(
            task.on_node_disconnected, Qt.QueuedConnection)
        task.download_ready.connect(
            self._on_download_ready, Qt.QueuedConnection)
        task.download_not_ready.connect(
            self._on_download_not_ready, Qt.QueuedConnection)
        task.download_complete.connect(
            self._on_download_complete, Qt.QueuedConnection)
        task.download_failed.connect(
            self._on_download_failure, Qt.QueuedConnection)
        task.download_error.connect(
            self._on_download_error, Qt.QueuedConnection)
        task.download_ok.connect(
            self._clear_network_error, Qt.QueuedConnection)
        task.download_finishing.connect(
            self._on_download_progress, Qt.DirectConnection)

        task.request_data.connect(
            data_consumer.request_data)
        task.abort_data.connect(
            data_consumer.abort_data_request, Qt.QueuedConnection)
        if info_supplier:
            task.chunk_downloaded.connect(
                info_supplier.on_new_availability_info, Qt.QueuedConnection)
        # connect traffic info signal 'signal_info_rx'
        task.signal_info_rx.connect(self._on_info_rx, Qt.QueuedConnection)

        task.no_disk_space.connect(self.no_disk_space.emit)
        task.copy_added.connect(self._on_copy_added, Qt.QueuedConnection)
        task.wrong_hash.connect(self._on_wrong_hash, Qt.QueuedConnection)

        task.connect_callbacks(on_downloaded, on_failed)

    def _emit_add_download_signals(self, emit_working=True):
        if not self._ready_timer.isActive():
            self._ready_timer.start()
        if not self._rerequest_info_timer.isActive():
            self._rerequest_info_timer.start()
        if emit_working:
            self.working.emit()

    def _on_download_ready(self, task):
        if not self._find_task_by_id(task.id):
            return

        logger.debug("download ready: %s", task.id)
        self._clear_network_error()
        if not self._paused and self._current_task is None:
            self._current_task = task
            task.start(self._limiter)
        else:
            self._add_to_queue(task)
            if self._current_task:
                self._swap_current_task()

    def _on_download_not_ready(self, task):
        logger.debug("download not ready: %s", task.id)
        try:
            self._ready_downloads_queue.remove(task)
        except ValueError:
            pass
        if task == self._current_task:
            if self._paused:
                self._current_task = None
            else:
                self._start_next_task()
        if isinstance(task, FileDownloadTask):
            info_consumer = self._file_availability_info_consumer
        else:
            info_consumer = self._patch_availability_info_consumer

        info_consumer.subscribe(task.id, force=True, priority=task.priority)

        if not self._paused and not self._ready_downloads_queue:
            self._emit_add_download_signals(emit_working=False)

    def _on_download_complete(self, task):
        logger.debug("on_download_complete: %s", task.id)
        self._downloads.pop(task.id, None)
        self._finish_task(task)

        if not self._current_task or task == self._current_task:
            self._start_next_task()
        else:
            try:
                self._ready_downloads_queue.remove(task)
            except ValueError:
                pass
        logger.debug("on_download_complete, tasks left: %s, "
                     "important downloads: %s,"
                     "ready_downloads_queue size: %s, current task: %s",
                     self.get_downloads_count(),
                     self._get_important_downloads_count(),
                     len(self._ready_downloads_queue),
                     self._current_task.id if self._current_task else "None")
        if not self._ready_downloads_queue and not self._current_task:
            self._process_empty_ready_downloads()

    def _on_download_failure(self, task):
        try:
            self._ready_downloads_queue.remove(task)
        except ValueError:
            pass
        self._downloads.pop(task.id, None)

        if task == self._current_task or not self._current_task:
            self._start_next_task()

        task.cancel()
        # task.clean()
        self._finish_task(task)

    def _on_download_progress(self, force_sending=False):
        downloads_info = self._get_important_downloads_info()
        uploads_info = self._get_important_uploads_info()

        task = self._current_task
        if not task or not isinstance(task, FileDownloadTask) or \
                task.priority <= IMPORTANT_DOWNLOAD_PRIORITY or \
                task.received == task.size:
            to_send = self._empty_progress
        else:
            progress = int(float(task.received) / float(task.size) * 100)
            objects_num = len([t for t in self._downloads.values() if (
                    isinstance(t, FileDownloadTask) and
                    t.priority > IMPORTANT_DOWNLOAD_PRIORITY)])
            to_send = (task.display_name, min(progress, 100), objects_num)
        return self._send_progress(to_send, downloads_info, uploads_info,
                                   force_sending=force_sending)

    def _send_progress(self, to_send, downloads_info, uploads_info,
                       force_sending=False):
        if to_send == self._last_progress_sent and \
                not any(downloads_info) and \
                uploads_info == self._last_uploads_info and not force_sending:
            return

        logger.verbose("Sending downloads status %s, %s, %s",
                       to_send, downloads_info, uploads_info)

        self._last_uploads_info = deepcopy(uploads_info)
        self._last_progress_sent = to_send
        self.downloads_status.emit(
            *to_send, list(downloads_info), uploads_info)

    def _check_send_download_error(self):
        has_important_downloads = self._get_important_downloads_count()
        logger.debug("_check_send_download_error, current_task: %s, "
                     "ready downloads: %s, downloads: %s, "
                     "has_important_downloads: %s",
                     self._current_task, len(self._ready_downloads_queue),
                     len(self._downloads), has_important_downloads)
        if (self._downloads
                and not self._current_task
                and not self._ready_downloads_queue):
            if not has_important_downloads:
                return
            self._on_download_error()

    def _on_download_error(self, error=""):
        if not error:
            error = "Waiting for nodes."
        self.error.emit(error)
        self._error_set = True

    def _start_next_task(self):
        self._current_task = None
        if self._paused:
            return
        if self._ready_downloads_queue:
            task = heappop(self._ready_downloads_queue)
            self._current_task = task
            task.start(self._limiter)
            if self._get_important_downloads_count():
                self.working.emit()
            self._clear_network_error()
        else:
            self._process_empty_ready_downloads()

    def _process_empty_ready_downloads(self):
        self._current_task = None
        logger.debug("_process_empty_ready_downloads, downloads: %s",
                     self._downloads)
        if not self._get_important_downloads_count():
            self.idle.emit()
            self._clear_network_error()
            if not self._downloads and not self._cleanup_timer.isActive():
                self._cleanup_timer.start()
        if self._downloads:
            if not self._ready_timer.isActive():
                self._ready_timer.start()

    def _add_to_queue(self, task):
        heappush(self._ready_downloads_queue, task)

    def _connected_nodes_incoming_changed(self, nodes):
        self._node_incoming_list = nodes.copy()

    def _connected_nodes_outgoing_changed(self, nodes):
        self._node_outgoing_list = nodes.copy()

    def _check_downloads(self):
        has_important_downloads = self._get_important_downloads_count()
        logger.debug("_check_downloads, current_task: %s, "
                     "ready downloads: %s, downloads: %s, "
                     "has_important_downloads: %s",
                     self._current_task, len(self._ready_downloads_queue),
                     len(self._downloads), has_important_downloads)
        if (self._downloads
                and not self._current_task
                and not self._ready_downloads_queue):
            if not has_important_downloads:
                return

            if not self._downloads_error_timer.isActive():
                self._downloads_error_timer.start()

            if not self._rerequest_info_timer.isActive():
                self._rerequest_info_timer.start()
        else:
            self._ready_timer.stop()

    def _get_important_downloads_count(self):
        return len(list(filter(
            lambda t: t.priority > IMPORTANT_DOWNLOAD_PRIORITY,
            self._downloads.values())))

    def _on_wrong_hash(self, task):
        self._on_download_progress()

    def _rerequest_info_for_not_ready_downloads(self):
        if self._downloads and not self._current_task \
                and not self._ready_downloads_queue:
            for task in self._downloads.values():
                if task.priority >= IMPORTANT_DOWNLOAD_PRIORITY:
                    if isinstance(task, FileDownloadTask):
                        info_consumer = self._file_availability_info_consumer
                    else:
                        info_consumer = self._patch_availability_info_consumer

                    info_consumer.subscribe(
                        task.id, force=True, priority=task.priority)

    def _find_task_by_id(self, obj_id):
        return self._downloads.get(obj_id)

    def _on_availability_info_received(self, node_id, obj_id, info):
        task = self._find_task_by_id(obj_id)
        if task:
            task.on_availability_info_received(node_id, obj_id, info)

    def _on_availability_info_failure(self, node_id, obj_id, error):
        task = self._find_task_by_id(obj_id)
        if task:
            task.on_availability_info_failure(node_id, obj_id, error)

    def _on_task_data_received(self, param_tuple):
        node_id, obj_id, offset, length, data = param_tuple
        task = self._find_task_by_id(obj_id)
        if task:
            task.on_data_received(node_id, obj_id, offset, length, data)
        else:
            logger.warning("No task to receive data. node_id %s, "
                           "obj_id %s, offset %s, length %s",
                           node_id, obj_id, offset, length)

    def _on_task_data_failed(self, node_id, obj_id, offset_str, error):
        task = self._find_task_by_id(obj_id)
        if task:
            task.on_data_failed(node_id, obj_id, int(offset_str), error)

    def _on_copy_added(self, file_hash):
        for task in self._downloads.values():
            if task.file_hash == file_hash:
                task.complete()

    def _finish_task(self, task):
        if isinstance(task, FileDownloadTask):
            info_consumer = self._file_availability_info_consumer
            info_supplier = self._file_availability_info_supplier
            data_consumer = self._file_data_consumer
        else:
            info_consumer = self._patch_availability_info_consumer
            info_supplier = self._patch_availability_info_supplier
            data_consumer = self._patch_data_consumer

        info_consumer.unsubscribe(task.id, silently=True)
        if info_supplier:
            info_supplier.remove_subscriptions_on_download(task.id, task.size)
            try:
                task.chunk_downloaded.disconnect(
                    info_supplier.on_new_availability_info)
            except Exception:
                pass

        try:
            task.disconnect(data_consumer)
        except Exception:
            pass

        try:
            self._connectivity_service.node_outgoing_disconnected.disconnect(
                task.on_node_disconnected)
        except Exception:
            pass

        try:
            task.disconnect(self)
        except Exception:
            pass

        try:
            task.signal_info_rx.disconnect(self._on_info_rx)
        except Exception:
            pass

        try:
            task.disconnect(task)
        except Exception:
            pass

        task.deleteLater()

    def _clear_network_error(self):
        if self._downloads_error_timer.isActive():
            self._downloads_error_timer.stop()

        if self._error_set:
            self.clear_error.emit()
        self._error_set = False

    def _get_important_downloads_info(self):
        added_info = dict()
        changed_info = dict()
        for task in self._downloads.values():
            if task.priority <= self._info_priority:
                continue

            obj_id = task.id
            downloaded = task.received
            state = \
                DOWNLOAD_NO_DISK_ERROR if task.no_disk_space_error else \
                DOWNLOAD_NOT_READY if not self._current_task else \
                DOWNLOAD_READY if task != self._current_task else \
                DOWNLOAD_STARTING if downloaded == 0 and \
                                     not task.hash_is_wrong else \
                DOWNLOAD_FAILED if downloaded == 0 else \
                DOWNLOAD_LOADING if downloaded < task.size else \
                DOWNLOAD_FINISHING
            short_info = {"state": state,
                          "downloaded": downloaded,
                          "priority": task.priority,
                          "is_file": isinstance(task, FileDownloadTask)}

            if obj_id not in self._important_downloads_info:
                added_info[obj_id] = \
                    {"files_info": task.files_info,
                     "size": task.size,}
                added_info[obj_id].update(short_info)
            elif self._important_downloads_info[obj_id] != short_info:
                changed_info[obj_id] = short_info
            self._important_downloads_info[obj_id] = short_info

        deleted_info = list(
            set(self._important_downloads_info) - set(self._downloads))
        for obj_id in deleted_info:
            self._important_downloads_info.pop(obj_id, None)
        return added_info, changed_info, deleted_info

    def _get_important_uploads_info(self):
        if self._upload_enabled:
            uploads_info = deepcopy(
                self._file_data_supplier.get_uploads_info())
            uploads_info.update(
                deepcopy(self._patch_data_supplier.get_uploads_info()))
        else:
            uploads_info = dict()

        return uploads_info

    def _kill_timers(self):
        for timer in self._timers:
            if timer.isActive():
                timer.stop()

    def _on_quit(self):
        self._connectivity_service.connected_nodes_outgoing_changed.disconnect(
            self._file_availability_info_consumer.on_connected_nodes_changed)
        self._connectivity_service.connected_nodes_outgoing_changed.disconnect(
            self._patch_availability_info_consumer.on_connected_nodes_changed)
        self._connectivity_service.node_outgoing_connected.disconnect(
            self._file_availability_info_consumer.on_node_connected)
        self._connectivity_service.node_outgoing_connected.disconnect(
            self._patch_availability_info_consumer.on_node_connected)
        self._connectivity_service.data_received.disconnect(
            self._on_data_received)

        self.disconnect(self)

        self._on_cancel_all_downloads()
        self._kill_timers()

        if self._file_availability_info_supplier:
            self._connectivity_service.node_incoming_disconnected.disconnect(
                self._file_availability_info_supplier.on_node_disconnected)
            self._file_availability_info_supplier.disconnect(
                self._file_availability_info_supplier)
            self._connectivity_service.connected_nodes_incoming_changed\
                .disconnect(self._file_availability_info_supplier
                            .on_connected_nodes_changed)
        if self._patch_availability_info_supplier:
            self._connectivity_service.node_incoming_disconnected.disconnect(
                self._patch_availability_info_supplier.on_node_disconnected)
            self._patch_availability_info_supplier.disconnect(
                self._patch_availability_info_supplier)
            self._connectivity_service.connected_nodes_incoming_changed\
                .disconnect(self._patch_availability_info_supplier
                            .on_connected_nodes_changed)

        self._file_availability_info_consumer.disconnect(
            self._file_availability_info_consumer)
        self._file_availability_info_consumer.stop()
        self._file_data_consumer.disconnect(self._file_data_consumer)
        self._patch_availability_info_consumer.disconnect(
            self._patch_availability_info_consumer)
        self._patch_availability_info_consumer.stop()
        self._patch_data_consumer.disconnect(self._patch_data_consumer)
        if self._file_data_supplier:
            self._file_data_supplier.signal_info_tx.disconnect(
                self._on_info_tx)
        if self._patch_data_supplier:
            self._patch_data_supplier.signal_info_tx.disconnect(
                self._on_info_tx)

    def _on_info_tx(self, info_tx):
        self.signal_info_tx.emit(info_tx)

    def _on_info_rx(self, info_rx):
        self.signal_info_rx.emit(info_rx)
