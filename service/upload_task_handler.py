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
import pickle
import time
import errno


from PySide2.QtCore import QObject, Signal, QTimer

from common.utils import make_dirs, ensure_unicode, \
    get_patches_dir, hashfile, remove_file, get_next_name
from common.file_path import FilePath
from common.constants import DOWNLOAD_STARTING, DOWNLOAD_LOADING, \
    DOWNLOAD_FINISHING, API_EVENTS_URI
from service.http_downloader import HttpDownloader
from service.events_db import EventsDbBusy


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class UploadTaskHandler(QObject):
    """
    Class incapsulating upload task related routines
    """

    progress = Signal(str, int, int)
    download_status = Signal(str, int, int,
                             list,      # download info
                             dict)      # empty dict
    idle = Signal()
    working = Signal()
    upload_cancelled = Signal(str)              # upload file name
    upload_folder_deleted = Signal(str)         # upload file name
    upload_folder_excluded = Signal(str)         # upload file name
    upload_folder_not_synced = Signal(str)         # upload file name
    _on_server_connect_signal = Signal()
    _upload_task_completed_signal = Signal(str,     # str(upload_id)
                                           float,       # elapsed
                                           str)     # str(total)
    _upload_task_error_signal = Signal(str,         # str(upload_id)
                                       str)         # error message
    _upload_task_progress_signal = Signal(str,      # str(upload_id)
                                          str,      # str(loaded)
                                          str,      # str(total)
                                          float)        # elapsed
    _on_upload_added_signal = Signal(dict)
    _on_upload_cancel_signal = Signal(int)

    def __init__(self,
                 cfg,
                 web_api,
                 filename,
                 ss_client,
                 tracker=None,
                 parent=None,
                 network_speed_calculator=None,
                 db=None):
        """
        Constructor

        @param web_api Client_API class instance [Client_API]
        @param ss_client Instance of signalling.SignalServerClient
        @param tracker Instance of stat_tracking.Tracker
        """

        QObject.__init__(self, parent=parent)

        self._cfg = cfg
        # Client_API class instance
        self._web_api = web_api
        # Signalling server client instance
        self._ss_client = ss_client
        self._tracker = tracker
        self._network_speed_calculator = network_speed_calculator
        self._db = db
        self._filename = ensure_unicode(filename)
        self.task_to_report = {}

        self._downloader = HttpDownloader(
            network_speed_calculator=self._network_speed_calculator)
        self._set_callbacks()

        # Download tasks info as task_id: info
        self.download_tasks_info = {}

        self._last_progress_report_time = 0
        self._last_length = 0.0
        self._was_stopped = False
        self._empty_progress = (None, 0, 0)

        self._open_uploads_file()

        self._uploads_deleted = \
            self._uploads_excluded = \
            self._uploads_not_synced = set()

        self._on_server_connect_signal.connect(self._on_server_connect)
        self._upload_task_completed_signal.connect(
            self._on_upload_task_completed)
        self._upload_task_error_signal.connect(self._on_upload_task_error)
        self._upload_task_progress_signal.connect(
            self._on_upload_task_progress)
        self._on_upload_added_signal.connect(self._on_upload_added)
        self._on_upload_cancel_signal.connect(self._on_upload_cancel)

        self._check_upload_path_timer = QTimer(self)
        self._check_upload_path_timer.setInterval(10 * 1000)
        self._check_upload_path_timer.timeout.connect(
            self._check_upload_paths)

    def set_download_limiter(self, download_speed_limiter):
        self._downloader.set_download_limiter(download_speed_limiter)

    def _set_callbacks(self):
        self._downloader.set_callbacks(
            on_download_auth_data_cb=self.on_download_auth_data_cb,
            on_download_completed=self.upload_task_completed_cb,
            on_download_error=self.upload_task_error_cb,
            on_download_progress=self.upload_task_progress_cb,
            on_get_upload_state_cb=self.get_upload_state_cb,
        )

    def _open_uploads_file(self):
        logger.info(
            "Loading upload task data from '%s'...", self._filename)

        # Load previously stored download task data
        try:
            if not op.exists(self._filename):
                with open(self._filename, 'wb') as f:
                    pickle.dump(self.task_to_report, f)
            # Not reported complete download tasks info as task_id: info
            with open(self._filename, 'rb') as f:
                self.task_to_report = pickle.load(f)
        except Exception as e:
            logger.error("Failed to load upload task data (%s)", e)
            self.task_to_report = None
            try:
                remove_file(self._filename)
            except Exception:
                pass

    def _sync__uploads_file(self):
        try:
            with open(self._filename, 'wb') as f:
                pickle.dump(self.task_to_report, f)
        except Exception as e:
            logger.error("Failed to save upload task data (%s)", e)
            try:
                remove_file(self._filename)
            except Exception:
                pass

    def _cleanup(self, upload_id):
        """
        Cleans data related with given upload task

        @param upload_id ID of upload task [string]
        """

        if upload_id not in self.download_tasks_info:
            return

        logger.debug("Doing cleanup for upload task ID '%s'...", upload_id)

        # Remove temporary file
        tmp_fn = self.download_tasks_info[upload_id].get('tmp_fn', None)
        if tmp_fn is not None and op.exists(tmp_fn):
            try:
                os.remove(tmp_fn)
            except Exception as e:
                logger.warning(
                    "Failed to delete temporary file '%s' (%s)", tmp_fn, e)

        # Clear upload info
        del self.download_tasks_info[upload_id]
        if not self.download_tasks_info:
            self.idle.emit()

    def _on_upload_failed(self, upload_id):
        """
        Routines to be executed on upload task fail

        @param upload_id ID of upload task [string]
        """

        # Notify signalling server on upload fail
        self._ss_client.send_upload_failed(upload_id)

        task_info = self.download_tasks_info.get(upload_id, None)
        if task_info:
            self.download_status.emit(
                *self._empty_progress, [{}, {}, [str(upload_id)]], {})

        # Cleanup upload data
        self._cleanup(upload_id)

    def _store_complete_upload_id(self, upload_id):
        """
        Saves upload task ID in the case in could not be reported immediately

        @param upload_id ID of upload task [string]
        """

        if self.task_to_report is None:
            self._open_uploads_file()
            if self.task_to_report is None:
                return

        self.task_to_report[str(upload_id)] = \
            self.download_tasks_info[upload_id]
        self._sync__uploads_file()

    def _clean_complete_upload_id(self, upload_id):
        """
        Remove previously stored upload task ID

        @param upload_id ID of upload task [string]
        """

        if self.task_to_report is None:
            self._open_uploads_file()
            if self.task_to_report is None:
                return

        upload_id = str(upload_id)  # For pickle
        if upload_id in self.task_to_report:
            del self.task_to_report[upload_id]
            self._sync__uploads_file()

    def _report_stored_uploads(self):
        """
        Checks stored complete upload task IDs and report them to signalling
        server
        """

        if self.task_to_report is None:
            self._open_uploads_file()
            if self.task_to_report is None:
                return

        logger.debug(
            "Checking upload tasks haven't been reported...")

        count = 0
        for upload_id in list(self.task_to_report.keys()):
            if self._ss_client.send_upload_complete(int(upload_id)):
                count += 1
                self._clean_complete_upload_id(upload_id)

        if count > 0:
            logger.info(
                "Reported %s upload tasks completion to the signalling server")

    def _on_upload_complete(self, upload_id):
        """
        Routines to be executed on upload task successful completion

        @param upload_id ID of upload task [string]
        """

        # Notify signalling server on upload completetion
        if not self._ss_client.send_upload_complete(upload_id):
            self._store_complete_upload_id(upload_id)

        # Cleanup upload data
        self._cleanup(upload_id)

    def on_download_auth_data_cb(self, upload_id):
        """
        Callback to be called on HTTP download start to obtain data to be sent
        to the server to confirm node auth

        @param upload_id ID of upload task
        @return Download auth data [string]
        """
        # TODO: rework callback with signals/slots (issue # 335)
        auth_data = self._web_api.get_request_data(
            'download', {'upload_id': upload_id}, force_update=True)
        logger.debug(
            "Auth data for upload task ID '%s' is: '%s'", upload_id, auth_data)
        return auth_data

    def on_upload_added_cb(self, upload_info):
        """
        Callback to be called on new upload notification

        @param upload_info Value of 'upload_add' protocol message 'data' field
        """
        self._on_upload_added_signal.emit(upload_info)

    def _on_upload_added(self, upload_info):
        """
        Slot to be called on new upload notification

        @param upload_info Value of 'upload_add' protocol message 'data' field
        """

        upload_id = upload_info['upload_id']

        # Check whether this download is already being processed
        if upload_id in self.download_tasks_info:
            logger.warning(
                "Upload ID '%s' is being downloaded already", upload_id)
            return

        # Save upload data
        upload_info['loaded'] = 0
        upload_info['size'] = 0
        upload_info['state'] = 'running'
        upload_info['elapsed'] = 0.0
        self.download_tasks_info[upload_id] = upload_info

        # Check whether upload path is not ready or is excluded from sync
        # or is deleted
        path = self._check_upload_path(upload_id)
        if path is None:
            return
        else:
            if not self._check_upload_path_timer.isActive():
                self._check_upload_path_timer.start()

        added_info, changed_info = self._get_download_info(
            upload_id, is_first_report=True)
        self.download_status.emit(
            *self._empty_progress, [added_info, changed_info, []], {})

        self.working.emit()

        # Generate filename to save file into
        tmp_fn = self.download_tasks_info[upload_id]['tmp_fn'] = op.join(
            get_patches_dir(self._cfg.sync_directory),
            '.upload_' + str(upload_id))

        self._download(upload_id, tmp_fn)

    def _download(self, upload_id, path, proceed=None):
        """
        Start new download or proceed resumed download

        @param upload_id - id if upload
        @param path - temporary path to save uploaded file
        @param proceed None if new download or tuple (offset, size)
                       if resuming paused download
        """
        self._downloader.download(
            id=upload_id,
            url=API_EVENTS_URI.format(self._cfg.host),
            path=path, do_post_request=True,
            timeout=self._cfg.http_downloader_timeout,
            proceed=proceed,
            host=self._cfg.host)

        self._last_progress_report_time = 0
        self._last_length = 0.0

    def on_upload_cancel_cb(self, upload_id):
        """
        Callback to be called on upload_cancel notification

        @param upload_id Value of 'upload_cancel' protocol message 'data' field
        """
        self._on_upload_cancel_signal.emit(upload_id)

    def _on_upload_cancel(self, upload_id):
        """
        Slot to be called on upload_cancel notification

        @param upload_id Value of 'upload_cancel' protocol message 'data' field
        """
        if upload_id not in self.download_tasks_info:
            return
        self.download_tasks_info[upload_id]["state"] = 'cancelled'

    def get_upload_state_cb(self, upload_id):
        """
        Callback to be called on upload status request
        statuses are
            'running'
            'paused'
            'cancelled'

        @param upload_id id of upload
        """

        task_info = self.download_tasks_info.get(upload_id, None)
        if not task_info:
            return ""
        return task_info.get("state", "")

    def upload_task_progress_cb(self, upload_id, loaded, total, elapsed):
        """
        Callback function to obtain upload task download progress

        @param upload_id ID of upload task
        @param loaded Amount of data downloaded already (in bytes) [long]
        @param total Size of file being downloaded (in bytes) [long]
        @param elapsed Time elapsed from download starting (in seconds) [float]
        """
        task_info = self.download_tasks_info.get(upload_id, None)
        if not task_info:
            return

        self._upload_task_progress_signal.emit(
            str(upload_id), str(loaded), str(total), elapsed)

    def _on_upload_task_progress(self, upload_id_str, loaded_str,
                                 total_str, elapsed):
        """
        Slot to obtain upload task download progress

        @param upload_id_str ID of upload task [str]
        @param loaded_str Amount of data downloaded already (in bytes) [str]
        @param total_str Size of file being downloaded (in bytes) [str]
        @param elapsed Time elapsed from download starting (in seconds) [float]
        """

        upload_id, loaded, total = int(upload_id_str), int(loaded_str), \
            int(total_str)
        self.download_tasks_info[upload_id]['loaded'] = loaded
        self.download_tasks_info[upload_id]['size'] = total
        if not total:
            return

        cur_time = time.time()
        # Report once in 0.5 sec and if downloaded more than 1%
        if cur_time - self._last_progress_report_time < 0.5 or \
                (float(loaded) - self._last_length) / total < 0.01:
            return

        self._last_progress_report_time = cur_time
        self._last_length = float(loaded)

        logger.info(
            "Upload task ID '%s' progress: downloaded %s of %s bytes; "
            "%s seconds elapsed", upload_id, loaded, total, elapsed)
        percent = int(float(loaded) / total * 100)
        if self.download_tasks_info[upload_id]['state'] == 'running' and \
                percent <= 98:
            progress = ('Downloading file {}'.format(
                self.download_tasks_info[upload_id]['upload_name']),
                percent,
                len(self.download_tasks_info))
        else:
            progress = self._empty_progress

        added_info, changed_info = self._get_download_info(upload_id)
        self.download_status.emit(
            *progress, [added_info, changed_info, []], {})

    def upload_task_error_cb(self, upload_id, message):
        """
        Callback function to be called on upload task download error

        @param upload_id ID of upload task
        @param message Error description [string]
        """
        self._upload_task_error_signal.emit(str(upload_id), message)

    def _on_upload_task_error(self, upload_id_str, message):
        """
        Slot to be called on upload task download error

        @param upload_id_str ID of upload task [string]
        @param message Error description [string]
        """
        upload_id = int(upload_id_str)
        logger.error(
            "Upload task ID '%s' failed (%s)", upload_id, message)

        if self._tracker:
            self._tracker.http_error(upload_id)

        self._on_upload_failed(upload_id)


    def upload_task_completed_cb(self, upload_id, elapsed, total):
        """
        Callback function to be called on upload task download completion

        @param upload_id ID of upload task
        @param elapsed Time elapsed from download starting (in seconds) [float]
        @param total Size of file being downloaded (in bytes) [long]
        """
        self._upload_task_completed_signal.emit(str(upload_id), elapsed,
                                                str(total))

    def _on_upload_task_completed(self, upload_id_str, elapsed, total_str):
        """
        Slot to be called on upload task download completion

        @param upload_id_str ID of upload task [string]
        @param elapsed Time elapsed from download starting (in seconds) [float]
        @param total_str Size of file being downloaded (in bytes) [string]
        """

        upload_id = int(upload_id_str)
        state = self.download_tasks_info[upload_id]['state']

        upload_name = self.download_tasks_info[upload_id]['upload_name']
        if state == 'cancelled':
            logger.debug("Upload task %s cancelled", upload_id)
            self._on_upload_failed(upload_id)
            # Tray notification
            self.upload_cancelled.emit(upload_name)
            return
        elif state == 'paused':
            self.download_tasks_info[upload_id]['elapsed'] += elapsed
            return

        elapsed += self.download_tasks_info[upload_id]['elapsed']
        total = int(total_str)
        bps_avg = int(total / elapsed) if elapsed > 0 else 0
        bps_avg = "{:,}".format(bps_avg)
        logger.info(
            "Upload task ID '%s' complete (downloaded %s bytes in %s seconds"
            "(%s Bps))",
            upload_id_str, total_str, elapsed, bps_avg)

        # Calculate checksum
        tmp_fn = self.download_tasks_info[upload_id]['tmp_fn']
        checksum = self.download_tasks_info[upload_id]['upload_md5']
        try:
            logger.debug(
                "Calculating checksum for upload task ID '%s'...", upload_id)
            checksum_calculated = hashfile(tmp_fn)
        except Exception as e:
            logger.error(
                "Failed to calculate checksum of '%s' (%s)", tmp_fn, e)
            self._on_upload_failed(upload_id)
            return

        if self._tracker:
            self._tracker.http_download(upload_id, total, elapsed,
                                        checksum_calculated == checksum)

        # Validate checksum
        if checksum_calculated != checksum:
            logger.error(
                "MD5 checkfum of '%s' is '%s' instead of '%s'",
                tmp_fn, checksum_calculated, checksum)
            self._on_upload_failed(upload_id)
            return

        # Move file to its location
        path = self._check_upload_path(upload_id)
        if path is None:
            return
        path = FilePath(op.join(path, upload_name))
        fullpath = ensure_unicode(op.join(self._cfg.sync_directory, path))
        fullpath = FilePath(fullpath).longpath
        dirname = op.dirname(fullpath)
        if not op.isdir(dirname):
            logger.warning("Destination directory %s"
                           "does not exist for upload %s", dirname, fullpath)
            self._on_upload_failed(upload_id)
            return

        try:
            try:
                logger.info(
                    "Moving downloaded file '%s' to '%s'...", tmp_fn, fullpath)
                # Create necessary directories
                make_dirs(fullpath)
                # Move file
                shutil.move(src=tmp_fn, dst=fullpath)
            except OSError as e:
                if e.errno != errno.EACCES:
                    raise e
                logger.warning(
                    "Can't move downloaded file '%s' into '%s' (%s)",
                    tmp_fn, dirname, e)
                fullpath = get_next_name(fullpath)
                shutil.move(src=tmp_fn, dst=fullpath)
        except Exception as e:
            logger.error(
                "Failed to move downloaded file '%s' into '%s' (%s)",
                tmp_fn, dirname, e)
            self._on_upload_failed(upload_id)
            return

        self.download_status.emit(
            *self._empty_progress, [{}, {}, [upload_id_str]], {})
        self._on_upload_complete(upload_id)

    def on_signal_server_connect_cb(self):
        """
        Callback handling connection to signal server
        """
        self._on_server_connect_signal.emit()

    def _on_server_connect(self):
        """
        Slot handling connection to signal server
        """
        self._report_stored_uploads()

    def _check_upload_path(self, upload_id):
        logger.debug("_check_upload_path")
        upload_info = self.download_tasks_info[upload_id]
        uuid = upload_info['folder_uuid']
        if not uuid:
            path = ''
            deleted = excluded = False
        else:
            try:
                with self._db.soft_lock():
                    path, deleted, excluded = self._db\
                        .get_folder_path_deleted_excluded_by_uuid(
                            upload_info['folder_uuid'])
            except EventsDbBusy:
                logger.debug("Events db busy")
                path = deleted = excluded = None

        upload_name = upload_info['upload_name']
        if path is None or deleted or excluded:
            reason_str = 'not synced' if path is None \
                else 'deleted' if deleted else 'excluded'
            logger.warning("Can't upload file '%s' because dir %s is %s",
                           upload_name,
                           upload_info['folder_uuid'], reason_str)
            self._on_upload_failed(upload_id)
            if path is None:
                if upload_name not in self._uploads_not_synced:
                    self.upload_folder_not_synced.emit(upload_name)
                self._uploads_not_synced.add(upload_name)
            elif deleted:
                if upload_name not in self._uploads_deleted:
                    self.upload_folder_deleted.emit(upload_name)
                self._uploads_deleted.add(upload_name)
            else:
                if upload_name not in self._uploads_excluded:
                    self.upload_folder_excluded.emit(upload_name)
                self._uploads_excluded.add(upload_name)
        else:
            self._uploads_deleted.discard(upload_name)
            self._uploads_excluded.discard(upload_name)
            self._uploads_not_synced.discard(upload_name)

        if deleted or excluded:
            path = None
        return path

    def _check_upload_paths(self):
        running_count = 0
        for upload_id in self.download_tasks_info:
            path = self._check_upload_path(upload_id)
            if path is None:
                self.download_tasks_info[upload_id]['state'] = 'cancelled'
            else:
                running_count += 1
        if running_count:
            self._check_upload_path_timer.start()

    def _get_download_info(self, upload_id, is_first_report=False):
        added_info = dict()
        changed_info = dict()

        obj_id = str(upload_id)
        task_info = self.download_tasks_info[upload_id]
        downloaded = task_info['loaded']
        state = \
            DOWNLOAD_STARTING if downloaded == 0 else \
            DOWNLOAD_LOADING if downloaded < task_info['upload_size'] else \
            DOWNLOAD_FINISHING
        short_info = {"state": state,
                      "downloaded": downloaded,
                      "priority": 0,
                      "is_file": True}

        if is_first_report:
            files_info = [{
            "target_file_path": task_info['upload_path'],
            "mtime": -1, # mtime < 0 => http download
            "is_created": None,
            "is_deleted": None}]

            added_info[obj_id] = \
                {"files_info": files_info,
                 "size": task_info['upload_size'],}
            added_info[obj_id].update(short_info)
        else:
            changed_info[obj_id] = short_info

        return added_info, changed_info

    def stop(self, cancel_downloads=False):
        self._was_stopped = True
        for upload_id in self.download_tasks_info:
            self.download_tasks_info[upload_id]['state'] = 'cancelled' \
                if cancel_downloads or \
                not self.download_tasks_info[upload_id]['size'] \
                else 'paused'
        if self._check_upload_path_timer.isActive():
            self._check_upload_path_timer.stop()

        # deleted_list = [
        #     str(u) for u in self.download_tasks_info
        #     if self.download_tasks_info[u]['state'] == 'cancelled']
        # if deleted_list:
        #     self.download_status.emit(
        #     *self._empty_progress, [{}, {}, deleted_list], {})
        self.idle.emit()

    def start(self):
        if not self._was_stopped:
            return

        self._was_stopped = False
        for upload_id in self.download_tasks_info:
            info = self.download_tasks_info[upload_id]
            info['state'] = 'running'
            self._download(upload_id, info['tmp_fn'],
                           (info['loaded'], info['size'] - 1))
        if self.download_tasks_info:
            self.working.emit()
            self._check_upload_path_timer.start()

        self._uploads_deleted = \
            self._uploads_excluded = \
            self._uploads_not_synced = set()

    def exit(self):
        self.stop(True)
        self._downloader.close(immediately=False)
