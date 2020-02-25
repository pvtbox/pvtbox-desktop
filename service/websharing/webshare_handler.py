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
import shutil
import time
import pickle
from queue import Queue
from collections import defaultdict
from threading import Event, Timer, RLock

from PySide2.QtCore import QThread, Qt

from service.events_db import FileNotFound, EventsDbBusy
from service.network.connectivity.connectivity_service \
    import ConnectivityService
from service.network.download_manager import DownloadManager

try:
    from urllib.parse import urlparse
except ImportError:
    from urllib.parse import urlparse
import re

from service.signalling import SignalServerClient

from common.async_utils import run_daemon
from service.transport_setup import SERVER, PORT, get_server_addr_port
from common.signal import Signal
from common.utils import get_downloads_dir, get_next_name, get_data_dir, \
    create_empty_file, make_dirs, remove_dir, remove_file, ensure_unicode, \
    get_copies_dir, copy_file, get_bases_filename
from common.constants import DOWNLOAD_PRIORITY_FILE, DELETE, MOVE, \
    RETRY_DOWNLOAD_TIMEOUT, REGULAR_URI
from common.file_path import FilePath
from .share_info_processor import ShareInfoProcessor, FileInfo


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class WebshareHandlerSignals(object):
    """
    Contains signals definition for the WebshareHandler class
    """

    # Signal to be emitted on download start
    # Arguments are: share_hash [str]
    share_download_started = Signal(str)

    # Signal to be emitted on download successful finishing
    # Arguments are: share_hash [str]
    share_download_complete = Signal(str)

    # Signal to be emitted on download successful finishing
    # Arguments are: share_hash [str]
    share_download_failed = Signal(str)

    # Signal to be emitted on download busy
    # Arguments are: new dest_dir [unicode], current_share_name [unicode]
    share_download_busy = Signal(str, str)

    # Signal to be emitted on download cancelled
    # Arguments are: share_name [unicode]
    share_download_cancelled = Signal(str)

    # Signal to be emitted on download folder deleted
    # Arguments are: share_name [unicode]
    share_download_folder_deleted = Signal(str)

    # Signal to be emitted on download folder excluded
    # Arguments are: share_name [unicode]
    share_download_folder_excluded = Signal(str)

    # Signal to be emitted on signal server authorization failure
    share_unavailable = Signal()

    connected_nodes_changed = Signal(int)

    download_success = Signal(str,      # task.id
                              FileInfo, # file_info
                              str)      # download_path
    download_failure = Signal(str,      # task.id
                              FileInfo) # file_info


class WebshareHandlerError(Exception):
    pass


class WebshareHandlerPathNotFoundError(WebshareHandlerError):
    pass


class WebshareHandler(object):
    """
    Class incapsulating webshare downloading routines
    """
    signal_info_tx = Signal(tuple)
    signal_info_rx = Signal(tuple)

    def __init__(self, config=None, download_limiter=None, tracker=None,
                 sync=None, network_speed_calculator=None, db=None,
                 filename='shares.db', parent=None):
        """
        Constructor

        @param config Application configuration
        @param tracker Instance of stat_tracking.Tracker
        @param download_limiter Instance of Download limiter
        """

        self._cfg = config

        # Indicates that share downloading could be started
        self._enabled = Event()

        # Download limiter instance
        self._download_limiter = download_limiter
        # Tracker class instance
        self._tracker = tracker

        self._network_speed_calculator = network_speed_calculator
        # Signalling server client instance
        self._ss_client = SignalServerClient(parent, client_type='webshare')
        self._ss_addr = SERVER
        self._ss_port = PORT

        self._sync = sync
        self._db = db
        # Share processing queue
        self._queue = Queue()

        # share_hash <> its object IDs
        self._tasks = defaultdict(list)
        self._cancelled_tasks = defaultdict(set)
        self._downloaded_tasks = defaultdict(set)

        self._failed_downloads = defaultdict(list)
        self._retry_download_timer = None
        self._retry_download_timeout = RETRY_DOWNLOAD_TIMEOUT

        # Start thread
        self._share_processing_thread()
        self._stop = False

        # Hash of share being processed
        self._current_share_hash = None

        # Name of share being processed
        self._current_share_name = None

        self._is_folder = False
        self._is_deleting = False
        self._fullname = ""
        self._in_data_dir = False

        # Number of files
        self._num_files = 0

        # Destination directories dict
        self._dest_dirs = {}
        self._dest_uuids = {}

        # Initialize signalling server client
        self._connect_ss_slots()

        self._init_connectivity()

        # Signals to be emitted for current class instance
        self.signals = WebshareHandlerSignals()

        self.signals.download_success.connect(
            self._on_download_success, Qt.QueuedConnection)
        self.signals.download_failure.connect(
            self._on_download_failure, Qt.QueuedConnection)

        self._folder_uuid_ready = Event()
        self._folder_uuid_ready.set()
        self._special_event_no = 0
        self._special_event_lock = RLock()

        self._filename = get_bases_filename(self._cfg.sync_directory, filename)
        self._spec_files = dict()
        self._clean_spec_files()

    def _init_connectivity(self):
        self._connectivity_service = ConnectivityService(
            self._ss_client, self._network_speed_calculator)
        self._connectivity_service_thread = QThread()
        self._connectivity_service.moveToThread(
            self._connectivity_service_thread)
        self._connectivity_service_thread.started.connect(
            self._connectivity_service.init.emit)
        self._connectivity_service.connected_nodes_outgoing_changed.connect(
            self._on_connected_nodes_changed, Qt.QueuedConnection)
        self._download_manager = DownloadManager(
            connectivity_service=self._connectivity_service,
            ss_client=self._ss_client,
            upload_enabled=False,
            tracker=self._tracker)
        self._download_manager.moveToThread(self._connectivity_service_thread)
        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        downloads_dir = get_downloads_dir(data_dir=data_dir, create=True)
        self._connectivity_service_thread.started.connect(
            lambda: self._download_manager.prepare_cleanup(
                [downloads_dir]))
        self._connectivity_service_thread.start()

        self._download_manager.idle.connect(self._sync.on_share_idle)
        self._download_manager.working.connect(self._sync.on_share_downloading)
        self._download_manager.error.connect(
            self._sync.on_share_downloading_error)
        self._download_manager.progress.connect(
            self._sync.send_download_progress)
        self._download_manager.downloads_status.connect(
            self._sync.send_downloads_status)
        self._download_manager.signal_info_tx.connect(self._on_info_tx)
        self._download_manager.signal_info_rx.connect(self._on_info_rx)

    def _connect_ss_slots(self):
        self._ss_client.get_connection_params.connect(
            self.ss_connection_params_cb, Qt.QueuedConnection)
        self._ss_client.share_info.connect(
            self.on_share_info_cb, Qt.QueuedConnection)
        self._ss_client.auth_failure.connect(
            self._on_auth_failed, Qt.QueuedConnection)

    def enable(self):
        """
        Enables queued share info processing
        """
        self._enabled.set()

    def disable(self):
        """
        Disables queued share info processing
        """

        self._enabled.clear()

    def set_config(self, cfg_data):
        # Setup transport and get signal server address/port
        logger.debug("set_config")
        addr, port = get_server_addr_port(cfg_data, self._connectivity_service)
        if addr:
            self._ss_addr = addr
        if port:
            self._ss_port = port

    def set_download_limiter(self, download_limiter):
        self._download_limiter = download_limiter
        self._download_manager.set_download_limiter(download_limiter)

    def download_by_hash(self, share_hash, passwd=None, dest_dir=None):
        """
        Queues share downloading given its hash

        @param share_hash Share ID (assigned by API server) [str]
        @raise WebshareHandlerError
        """

        if len(share_hash) != 32:
            logger.error("Invalid share hash: '%s'", share_hash)
            raise WebshareHandlerError("Bad share hash")

        logger.info(
            "Queueing share downloading (hash='%s')...", share_hash)
        logger.debug("Dest_dir %s", dest_dir)
        uuid = self._get_folder_uuid(dest_dir)
        self._queue.put((share_hash, passwd, dest_dir, uuid))

    def download_by_url(self, share_url, dest_dir=None):
        """
        Queues share downloading given its URL

        @param share_url Share download URL [str]
        @raise WebshareHandlerError
        """

        share_url = str(share_url)
        share_hash, passwd = self.parse_share_url(share_url)
        self.download_by_hash(share_hash, passwd, dest_dir)

    @staticmethod
    def parse_share_url(share_url):
        """
        Extracts share hash from share download URL

        @param share_url Share download URL [str]
        @return Share hash [str]
        @raise WebshareHandlerError
        """

        try:
            logger.verbose(
                "Parsing share URL '%s'...", share_url)
        except AttributeError:
            pass

        pr = urlparse(share_url)

        try:
            if pr.scheme not in ('http', 'https', 'pvtbox'):
                raise WebshareHandlerError
            if not pr.path:
                raise WebshareHandlerError

            # Extract hash from URL path
            share_hash = pr.path.split('/')[-1]
            if not share_hash or len(share_hash) != 32:
                raise WebshareHandlerError
        except WebshareHandlerError:
            logger.error(
                "Failed to parse share URL '%s'", share_url)
            raise WebshareHandlerError("Bad share URL")

        # parse passwd
        m = re.search(r"passwd=(?P<passwd>\w+)", share_url)
        passwd = m.group("passwd") if m else None

        return share_hash, passwd

    @run_daemon
    def _share_processing_thread(self):
        logger.debug(
            "Starting share processing thread...")

        count = 0
        while True:
            # Do not do anything until enabled
            self._enabled.wait()
            if self._stop:
                break
            try:
                share_hash, passwd, dest_dir, uuid = self._queue.get()
                count += 1
                if self._enabled.is_set() and self._current_share_name is None:
                    count = 0
                    self._process_share(share_hash, passwd, dest_dir, uuid)
                else:
                    if self._enabled.is_set() and count == 1:
                        # emit signal only once while waiting not busy
                        self.signals.share_download_busy.emit(
                            FilePath(dest_dir), self._current_share_name)
                    elif count == 1:
                        count = 0
                    self._queue.put((share_hash, passwd, dest_dir, uuid))
                    time.sleep(0.1)
            except Exception:
                logger.error(
                    "Unhandled exception", exc_info=True)

    def ss_connection_params_cb(self):
        params = dict()
        if self._current_share_hash:
            params["share_hash"] = self._current_share_hash
        if self._current_passwd:
            params["passwd"] = self._current_passwd
        self._ss_client.connection_params.emit(params)

    def on_share_info_cb(self, share_info):
        if not share_info:
            return

        if self._current_share_name:
            return

        share_hash = share_info['share_hash']
        logger.debug(
            "Obtained info on share hash '%s'", share_hash)

        # Extract files info
        info_processor = ShareInfoProcessor(self._cfg)
        files_info = info_processor.process(share_info)
        self._current_share_name = info_processor.get_name()
        if not files_info:
            self._move()
            self._ss_client.send_share_downloaded(share_hash)
            self._tasks.pop(share_hash, None)
            # disconnect from nodes
            self._ss_client.ss_disconnect()

            self._current_share_hash = None
            self._current_share_name = None
            self._sync.send_download_progress(None, None, None)
            return

        self.signals.share_download_started.emit(share_hash)
        self._is_folder = info_processor.is_folder(share_info)
        try:
            self._create_empty_file_or_folder()
        except Exception as e:
            logger.error("Error creating empty file or folder %s (%s)",
                         self._current_share_name, e)

        # Start downloading of all files found
        self._num_files = len(files_info)
        for fi in files_info:
            self._tasks[share_hash].append(fi.event_uuid)
            self.start_file_download(fi)

    def _on_special_file_event(self, path, event_type, new_path):
        logger.debug("Special file event obtained for path %s, type %s",
                     path, event_type)
        special_dirname, special_filename = op.split(path)
        filename = special_filename.rsplit('.', 1)[0]   # cut off '.download'
        if not self._is_deleting and self._current_share_name == filename and (
                event_type in (DELETE, MOVE) or not op.exists(path)):
            to_cancel = True
            special_dirname = ensure_unicode(special_dirname)
            rel_special_dir = self._relpath(special_dirname)
            with self._special_event_lock:
                self._special_event_no += 1
            if event_type == MOVE:
                new_dir, new_file = op.split(new_path)
                if special_filename == new_file:    # folder moved locally
                    new_dir = ensure_unicode(new_dir)
                    rel_new_dir = self._relpath(new_dir)
                    self._sync.update_special_paths.emit(
                        rel_special_dir, rel_new_dir)
                    self._change_folder_uuid_local(new_dir)
                    self._update_spec_files(new_path, self._is_folder)
                    to_cancel = False
            if event_type == DELETE or to_cancel:
                folder_deleted = not op.isdir(special_dirname)
                folder_excluded = self._sync.is_dir_excluded(rel_special_dir)
                self.cancel_share_download(filename,
                                           folder_deleted=folder_deleted,
                                           folder_excluded=folder_excluded)

    def _create_empty_file_or_folder(self):
        self._fullname = FilePath(self._get_full_name())
        if not self._fullname:
            return

        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        self._in_data_dir = self._fullname in FilePath(data_dir)
        logger.debug("Adding special file %s", self._fullname)
        if self._in_data_dir:
            self._sync.add_special_file(self._fullname,
                                        self._on_special_file_event)

        special_file_created = False
        try:
            if self._is_folder:
                make_dirs(self._fullname, is_folder=True)
            else:
                create_empty_file(self._fullname)
            special_file_created = True
            self._update_spec_files(self._fullname, self._is_folder)
        except Exception as e:
            logger.warning("Can't create file or folder %s. Reason %s",
                           self._fullname, e)

        if not self._in_data_dir and special_file_created:
            self._sync.add_special_file(self._fullname,
                                        self._on_special_file_event)
        elif self._in_data_dir and not special_file_created:
            self._sync.remove_special_file(self._fullname)

    def _delete_empty_file_or_folder(self):
        self._is_deleting = True
        fullname = self._get_full_name(
            cancel=False, existing_file=True)
        if fullname:
            self._fullname = fullname

        if not self._fullname:
            self._is_deleting = False
            return

        self._fullname = FilePath(self._fullname)
        logger.debug("Removing special file %s", self._fullname)
        if not self._in_data_dir:
            self._sync.remove_special_file(self._fullname)
        try:
            if self._is_folder:
                remove_dir(self._fullname)
            else:
                remove_file(self._fullname)
        except Exception as e:
            logger.warning("Can't delete file or folder %s. Reason %s",
                           self._fullname, e)
        self._update_spec_files()
        if self._in_data_dir:
            self._sync.remove_special_file(self._fullname)
        self._fullname = ""
        self._is_deleting = False

    def _get_full_name(self, cancel=True, existing_file=False):
        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        dest_dir = self._dest_dirs.get(self._current_share_hash, data_dir)
        if FilePath(dest_dir) in FilePath(data_dir):
            if not self._renew_dest_dir(cancel, wait=False):
                return ""
            dest_dir = self._dest_dirs.get(self._current_share_hash, data_dir)
        fullname = op.join(dest_dir, self._current_share_name + '.download')
        fullname = FilePath(fullname).longpath
        if not existing_file:
            fullname = get_next_name(fullname)
        return fullname

    def _get_folder_uuid(self, dest_dir):
        data_dir = FilePath(self._cfg.sync_directory
                            if self._cfg else get_data_dir())
        if not dest_dir:
            dest_dir = data_dir
        dest_dir = FilePath(dest_dir)
        if dest_dir in data_dir:
            rel_path = self._relpath(dest_dir, data_dir)
            if not rel_path:
                folder_uuid = None
            else:
                try:
                    folder_uuid = self._sync.get_file_uuid(rel_path)
                    if folder_uuid is None:
                        raise FileNotFound("")
                    logger.debug("Folder uuid for path %s is %s",
                                 rel_path, folder_uuid)
                except (FileNotFound, EventsDbBusy):
                    logger.warning("No folder uuid for folder %s", dest_dir)
                    raise WebshareHandlerPathNotFoundError(
                        "No folder uuid for folder")
        else:
            folder_uuid = ""
        return folder_uuid

    def _relpath(self, path, data_dir=None):
        rel_path = None
        if not data_dir:
            data_dir = FilePath(self._cfg.sync_directory
                                if self._cfg else get_data_dir())
        if not path:
            path = FilePath(data_dir)
        if path in data_dir:
            rel_path = op.relpath(path, data_dir)
            if rel_path == os.curdir:
                rel_path = ""
            rel_path = ensure_unicode(rel_path)
        return rel_path

    def _get_path_relative_to_share(self, file_info):
        data_path = self._cfg.sync_directory if self._cfg else get_data_dir()
        share_dir = get_downloads_dir(data_path)
        rel_path = FilePath(op.relpath(file_info.fullname, share_dir))
        rel_path_list = rel_path.split('/')

        assert rel_path_list, "Must have relative path for share"

        # replace share hash with share name in rel path
        rel_path_list[0] = self._current_share_name
        rel_path = '/'.join(rel_path_list)
        logger.debug("Relative path for shared file %s is %s",
                     file_info, rel_path)
        return rel_path

    @run_daemon
    def _change_folder_uuid_local(self, dest_dir):
        event_no = self._special_event_no
        self._folder_uuid_ready.clear()
        try:
            while True:
                try:
                    folder_uuid = self._get_folder_uuid(dest_dir)
                    break
                except WebshareHandlerPathNotFoundError:
                    if event_no != self._special_event_no:
                        break
                    time.sleep(0.1)

            if event_no == self._special_event_no:
                self._dest_uuids[self._current_share_hash] = folder_uuid
                self._renew_dest_dir(wait=False)
        except Exception as e:
            raise e
        finally:
            with self._special_event_lock:
                if event_no == self._special_event_no:
                    # no new calls of _change_folder_uuid_local
                    self._folder_uuid_ready.set()

    def _renew_dest_dir(self, cancel=True, wait=True):
        logger.debug("Waiting for folder uuid")
        if wait:
            self._folder_uuid_ready.wait()
        folder_uuid = self._dest_uuids[self._current_share_hash]
        logger.debug("Folder uuid got %s", folder_uuid)
        if not folder_uuid:
            return True

        try:
            with self._db.soft_lock():
                path, deleted, excluded = self._db \
                    .get_folder_path_deleted_excluded_by_uuid(folder_uuid)
        except EventsDbBusy:
            logger.debug("Events db busy")
            path = deleted = excluded = None
        if path is None or deleted or excluded:
            reason_str = 'not synced' if path is None \
                else 'deleted' if deleted else 'excluded'
            logger.warning("Can't download shared file '%s' because "
                           "dir %s is %s",
                           self._current_share_name,
                           self._dest_dirs[self._current_share_hash],
                           reason_str)
            if cancel:
                if deleted:
                    self.cancel_share_download(self._current_share_name,
                                               folder_deleted=True)
                else:
                    self.cancel_share_download(self._current_share_name,
                                               folder_excluded=True)

            return False

        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        self._dest_dirs[self._current_share_hash] = op.join(data_dir, path)
        return True

    def _redownload(self):
        self._retry_download_timer = None
        failed_downloads = self._failed_downloads.get(
            self._current_share_hash, [])
        if not failed_downloads:
            return

        logger.debug("Retry failed share file downloads")
        self._failed_downloads.pop(self._current_share_hash)
        for file_info in failed_downloads:
            self.start_file_download(file_info)

    def start_file_download(self, file_info):
        data_path = self._cfg.sync_directory if self._cfg else get_data_dir()
        download_path = op.join(get_copies_dir(data_path), file_info.file_hash)

        logger.info(
            "Initiating downloading of file '%s' to '%s'...",
            file_info.fullname, download_path)

        def on_success(task):
            self.signals.download_success.emit(
                task.id, file_info, download_path)

        def on_failure(task):
            self.signals.download_failure.emit(task.id, file_info)

        if not file_info.size:
            create_empty_file(file_info.fullname)
            self._tasks[self._current_share_hash].remove(file_info.event_uuid)
            self._finish_task_download()
            return

        elif not self._cfg.download_backups:
            self._sync.make_copy_from_existing_files(file_info.file_hash)

        files_info = [{
            "target_file_path" : self._get_path_relative_to_share(file_info),
            "mtime": 0, # mtime == 0 => shared file
            "is_created": None,
            "is_deleted": None}]

        self._download_manager.add_file_download(
            DOWNLOAD_PRIORITY_FILE,
            file_info.event_uuid,
            file_info.size,
            file_info.file_hash,
            download_path,
            'Downloading shared file {}'.format(file_info.name),
            on_success,
            on_failure,
            files_info=files_info,)

    def _finish_task_download(self):
        self._num_files -= 1
        logger.debug("Current not downloaded: %s, num_files: %s",
                     self._tasks[self._current_share_hash],
                     self._num_files)

        if not self._tasks[self._current_share_hash] \
                and self._num_files == 0:
            if self._cancelled_tasks[self._current_share_hash] and \
                    not self._downloaded_tasks[self._current_share_hash]:
                self.cancel_share_download(self._current_share_name)
                return

            # all tasks successfully downloaded
            self.signals.share_download_complete.emit(
                self._current_share_name)
            self._ss_client.send_share_downloaded(self._current_share_hash)
            try:
                self._delete_empty_file_or_folder()
            except Exception as e:
                logger.error("Error deleting empty file or folder %s (%s)",
                             self._current_share_name, e)
            self._move()

            # disconnect from nodes
            self._ss_client.ss_disconnect()

            self._clear_current_share_commons()
            self._sync.send_download_progress(None, None, None)

    def _on_download_success(self, task_id, file_info, download_path):
        logger.info("Download task SUCCESS obj_id='%s'", task_id)

        # remove successful task
        self._tasks[self._current_share_hash].remove(task_id)
        self._downloaded_tasks[self._current_share_hash].add(task_id)
        copy_file(download_path, file_info.fullname)
        self._finish_task_download()

    def _on_download_failure(self, task_id, file_info):
        logger.info("Download task FAILURE obj_id='%s'", task_id)
        if task_id in self._cancelled_tasks[self._current_share_hash]:
            self._tasks[self._current_share_hash].remove(task_id)
            self._finish_task_download()
        else:
            if not self._retry_download_timer:
                self._retry_download_timer = Timer(
                    self._retry_download_timeout,
                    self._redownload)
                self._retry_download_timer.start()

            self._failed_downloads[self._current_share_hash].append(
                file_info)

        self._sync.send_download_progress(None, None, None)

    def _process_share(self, share_hash, passwd=None, dest_dir=None,
                       uuid=None):
        logger.info(
            "Processing share hash='%s'", share_hash)
        self._current_share_hash = share_hash
        self._dest_dirs[share_hash] = dest_dir
        self._dest_uuids[share_hash] = uuid
        self._current_passwd = passwd

        self_hosted = self._cfg.host != REGULAR_URI
        fingerprint = None if self_hosted else \
            "86025017022f6dcf9022d6fb867c3bb3bdc621103ddd8e9ed2c891a46d8dd856"
        self._ss_client.ss_connect(
            self._ss_addr, self._ss_port, use_ssl=True, ssl_cert_verify=True,
            ssl_fingerprint=fingerprint,
            timeout=20)

    def _on_auth_failed(self):
        share_hash = self._current_share_hash
        logger.info(
            "Unable to process share hash='%s'", share_hash)
        self._current_share_hash = None
        self._dest_dirs.pop(share_hash, None)
        self._dest_uuids.pop(share_hash, None)
        self._current_passwd = None

        self._ss_client.ss_disconnect()

        self.signals.share_unavailable.emit()

    def _move(self):
        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        downloads_dir = get_downloads_dir(data_dir=data_dir, create=True)
        download_name = op.join(downloads_dir,
                                self._current_share_hash)
        if not self._renew_dest_dir():
            return

        dest_dir = self._dest_dirs.get(self._current_share_hash, data_dir)
        dest_name = op.join(dest_dir, self._current_share_name)
        dest_name = FilePath(dest_name).longpath
        dest_name = get_next_name(dest_name)
        logger.debug("Move '%s' to '%s'", download_name, dest_name)
        try:
            if FilePath(dest_dir) not in FilePath(data_dir):
                make_dirs(dest_name)
            shutil.move(download_name, dest_name)
        except IOError as e:
            logger.warning("Can't move downloaded shared file to %s. "
                           "Reason: %s", dest_name, e)
            self.cancel_share_download(self._current_share_name,
                                       folder_deleted=True)

    def _clear_share_download(self):
        data_dir = self._cfg.sync_directory if self._cfg else get_data_dir()
        downloads_dir = get_downloads_dir(data_dir=data_dir, create=True)
        download_name = op.join(downloads_dir,
                                self._current_share_hash)
        if self._is_folder:
            remove_dir(download_name)
        else:
            remove_file(download_name)

    def get_share_name(self):
        return self._current_share_name

    def cancel_share_download(self, share_name, folder_deleted=False,
                              folder_excluded=False):
        logger.debug("Cancel share download %s", share_name)
        if not self._current_share_name or \
                self._current_share_name != share_name:
            return

        enabled = self._enabled.is_set()
        if enabled:
            self.disable()
        if self._download_manager:
            self._download_manager.cancel_all_downloads()
        try:
            self._delete_empty_file_or_folder()
            self._clear_share_download()
        except Exception as e:
            logger.error("Error deleting empty file or folder %s (%s)",
                         self._current_share_name, e)

        self._ss_client.ss_disconnect()

        self.signals.share_download_failed.emit(self._current_share_name)
        if not folder_deleted and not folder_excluded:
            self.signals.share_download_cancelled.emit(
                self._current_share_name)
        elif folder_excluded:
            self.signals.share_download_folder_excluded.emit(
                self._current_share_name)
        else:
            self.signals.share_download_folder_deleted.emit(
                self._current_share_name)

        self._clear_current_share_commons()
        if enabled:
            self.enable()

    def cancel_files_downloads(self, files):
        for obj_id in files:
            self._cancel_file_download(obj_id)

    def _cancel_file_download(self, obj_id):
        if self._download_manager:
            self._cancelled_tasks[self._current_share_hash].add(obj_id)
            self._download_manager.cancel_download(obj_id)

    def _clear_current_share_commons(self):
        self._tasks.pop(self._current_share_hash, None)
        self._failed_downloads.pop(self._current_share_hash, None)
        self._dest_dirs.pop(self._current_share_hash, None)
        self._dest_uuids.pop(self._current_share_hash, None)
        self._cancelled_tasks.pop(self._current_share_hash, None)
        self._downloaded_tasks.pop(self._current_share_hash, None)
        self._current_share_hash = None
        self._current_share_name = None

    def _quit_connectivity(self):
        self._connectivity_service.disconnect_ss_slots()
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
        self.start()

    def stop(self, cancel_downloads=False):
        self.disable()
        if self._download_manager and not cancel_downloads:
            self._download_manager.pause_all_downloads(
                disconnect_callbacks=False)

        if cancel_downloads:
            self.cancel_share_download(self._current_share_name)
            self._queue = Queue()
            self._tasks.clear()
            self._failed_downloads.clear()
            self._dest_dirs.clear()
            self._dest_uuids.clear()

    def start(self):
        self._on_special_file_event(self._fullname, None, None)
        if self._download_manager:
                self._download_manager.resume_all_downloads()
        self.enable()

    def exit(self):
        self.stop(cancel_downloads=True)
        self._download_manager.quit.emit()
        self._connectivity_service.quit.emit()
        self._connectivity_service_thread.quit()
        self._connectivity_service_thread.wait()
        self._ss_client.ss_disconnect()
        # stop running thread

    def _on_info_tx(self, info_tx):
        self.signal_info_tx.emit(info_tx)

    def _on_info_rx(self, info_rx):
        self.signal_info_rx.emit(info_rx)

    def _clean_spec_files(self):
        logger.debug("Cleaning spec files from '%s'...", self._filename)
        try:
            with open(self._filename, 'rb') as f:
                self._spec_files = pickle.load(f)
            for share_hash in self._spec_files:
                path, is_directory = self._spec_files[share_hash]
                try:
                    if is_directory:
                        remove_dir(path)
                    else:
                        remove_file(path)
                    logger.debug("Special file (folder) removed %s", path)
                except Exception as e:
                    logger.warning("Can't delete file or folder %s. Reason %s",
                                   path, e)
        except Exception as e:
            logger.warning("Failed to load special files data (%s)", e)
            try:
                remove_file(self._filename)
            except Exception:
                pass

        self._spec_files = dict()
        try:
            with open(self._filename, 'wb') as f:
                pickle.dump(self._spec_files, f, protocol=2)
        except Exception as e:
            logger.warning("Failed to save special files data (%s)", e)

    def _update_spec_files(self, path=None, is_directory=False):
        if not self._current_share_hash:
            return

        current_hash = self._current_share_hash
        if path:
            self._spec_files[current_hash] = (path, is_directory)
        else:
            self._spec_files.pop(current_hash, None)
        try:
            with open(self._filename, 'wb') as f:
                pickle.dump(self._spec_files, f, protocol=2)
            logger.debug("Saved special files data for hashes %s",
                         list(self._spec_files.keys()))
        except Exception as e:
            logger.error("Failed to save special files data (%s)", e)
            try:
                remove_file(self._filename)
            except Exception:
                pass

    def _on_connected_nodes_changed(self, nodes):
        self.signals.connected_nodes_changed.emit(len(nodes))
