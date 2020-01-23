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
import random
import shutil
import math
from sortedcontainers import SortedDict
SortedDict.iteritems = SortedDict.items
import pickle as pickle

import logging
import errno
from time import time

from PySide2.QtCore import QObject, Signal, QTimer
from os.path import exists

from service.network.leakybucket import LeakyBucketException

from service.monitor.rsync import Rsync
from common.utils import remove_file, get_free_space_by_filepath, \
    get_signature_file_size
from common.constants import DOWNLOAD_PART_SIZE, DOWNLOAD_CHUNK_SIZE

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DownloadTask(QObject):
    download_ready = Signal(QObject)
    download_not_ready = Signal(QObject)
    download_complete = Signal(QObject)
    download_failed = Signal(QObject)
    download_error = Signal(str)
    download_ok = Signal()

    download_finishing = Signal()
    copy_added = Signal(str)
    chunk_downloaded = Signal(str,  # obj_id
                              str,  # str(offset) to fix offset >= 2**31
                              int)     # length
    chunk_aborted = Signal()
    request_data = Signal(str,  # node_id
                          str,  # obj_id
                          str,  # str(offset) to fix offset >= 2**31
                          int)     # length
    abort_data = Signal(str,  # node_id
                        str,  # obj_id
                        str)  # str(offset) to fix offset >= 2**31
    possibly_sync_folder_is_removed = Signal()
    no_disk_space = Signal(QObject,     # task
                           str,     # display_name
                           bool)        # is error
    wrong_hash = Signal(QObject)        # task)
    signal_info_rx = Signal(tuple)

    default_part_size = DOWNLOAD_PART_SIZE
    receive_timeout = 20      # seconds
    retry_limit = 2
    timeouts_limit = 2
    max_node_chunk_requests = 128
    end_race_timeout = 5.       # seconds

    def __init__(self, tracker, connectivity_service,
                 priority, obj_id, obj_size, file_path,
                 display_name, file_hash=None, parent=None, files_info=None):
        QObject.__init__(self, parent=parent)
        self._tracker = tracker
        self._connectivity_service = connectivity_service

        self.priority = priority
        self.size = obj_size
        self.id = obj_id
        self.file_path = file_path
        self.file_hash = file_hash
        self.download_path = file_path + '.download'
        self._info_path = file_path + '.info'
        self.display_name = display_name
        self.received = 0
        self.files_info = files_info


        self.hash_is_wrong = False
        self._ready = False
        self._started = False
        self._paused = False
        self._finished = False
        self._no_disk_space_error = False

        self._wanted_chunks = SortedDict()
        self._downloaded_chunks = SortedDict()
        self._nodes_available_chunks = dict()
        self._nodes_requested_chunks = dict()
        self._nodes_last_receive_time = dict()
        self._nodes_downloaded_chunks_count = dict()
        self._nodes_timeouts_count = dict()
        self._total_chunks_count = 0

        self._file = None
        self._info_file = None

        self._started_time = time()

        self._took_from_turn = 0
        self._received_via_turn = 0
        self._received_via_p2p = 0

        self._retry = 0

        self._limiter = None

        self._init_wanted_chunks()

        self._on_downloaded_cb = None
        self._on_failed_cb = None
        self.download_complete.connect(self._on_downloaded)
        self.download_failed.connect(self._on_failed)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setInterval(15 * 1000)
        self._timeout_timer.setSingleShot(False)
        self._timeout_timer.timeout.connect(self._on_check_timeouts)

        self._leaky_timer = QTimer(self)
        self._leaky_timer.setInterval(1000)
        self._leaky_timer.setSingleShot(True)
        self._leaky_timer.timeout.connect(self._download_chunks)

        self._network_limited_error_set = False

    def __lt__(self, other):
        if not isinstance(other, DownloadTask):
            return object.__lt__(self, other)

        if self == other:
            return False

        if self.priority == other.priority:
            if self.size - self.received == other.size - other.received:
                return self.id < other.id

            return self.size - self.received < other.size - other.received

        return self.priority > other.priority

    def __le__(self, other):
        if not isinstance(other, DownloadTask):
            return object.__le__(self, other)

        if self == other:
            return True

        if self.priority == other.priority:
            if self.size - self.received == other.size - other.received:
                return self.id < other.id

            return self.size - self.received < other.size - other.received

        return self.priority >= other.priority

    def __gt__(self, other):
        if not isinstance(other, DownloadTask):
            return object.__gt__(self, other)

        if self == other:
            return False

        if self.priority == other.priority:
            if self.size - self.received == other.size - other.received:
                return self.id > other.id

            return self.size - self.received > other.size - other.received

        return self.priority <= other.priority

    def __ge__(self, other):
        if not isinstance(other, DownloadTask):
            return object.__ge__(self, other)

        if self == other:
            return True

        if self.priority == other.priority:
            if self.size - self.received == other.size - other.received:
                return self.id > other.id

            return self.size - self.received > other.size - other.received

        return self.priority <= other.priority

    def __eq__(self, other):
        if not isinstance(other, DownloadTask):
            return object.__eq__(self, other)

        return self.id == other.id

    def on_availability_info_received(self, node_id, obj_id, info):
        if obj_id != self.id or self._finished or not info:
            return

        logger.info("availability info received, "
                    "node_id: %s, obj_id: %s, info: %s",
                    node_id, obj_id, info)

        new_chunks_stored = self._store_availability_info(node_id, info)
        if not self._ready and new_chunks_stored:
            if self._check_can_receive(node_id):
                self._ready = True
                self.download_ready.emit(self)
            else:
                self.download_error.emit('Turn limit reached')

        if self._started and not self._paused \
                and not self._nodes_requested_chunks.get(node_id, None):
            logger.debug("Downloading next chunk")
            self._download_next_chunks(node_id)
            self._clean_nodes_last_receive_time()
            self._check_download_not_ready(self._nodes_requested_chunks)

    def on_availability_info_failure(self, node_id, obj_id, error):
        if obj_id != self.id or self._finished:
            return

        logger.info("availability info failure, "
                    "node_id: %s, obj_id: %s, error: %s",
                    node_id, obj_id, error)
        try:
            if error["err_code"] == "FILE_CHANGED":
                self.download_failed.emit(self)
        except Exception as e:
            logger.warning("Can't parse error message. Reson: %s", e)

    def start(self, limiter):
        if exists(self.file_path):
            logger.info("download task file already downloaded %s",
                        self.file_path)
            self.received = self.size
            self.download_finishing.emit()
            self.download_complete.emit(self)
            return

        self._limiter = limiter

        if self._started:
            # if we swapped task earlier
            self.resume()
            return

        self._no_disk_space_error = False
        if not self.check_disk_space():
            return

        logger.info("starting download task, obj_id: %s", self.id)
        self._started = True
        self._paused = False
        self.hash_is_wrong = False
        self._started_time = time()
        self._send_start_statistic()
        if not self._open_file():
            return

        self._read_info_file()

        for downloaded_chunk in self._downloaded_chunks.items():
            self._remove_from_chunks(
                downloaded_chunk[0], downloaded_chunk[1], self._wanted_chunks)

        self.received = sum(self._downloaded_chunks.values())
        if self._complete_download():
            return

        self._download_chunks()
        if not self._timeout_timer.isActive():
            self._timeout_timer.start()

    def check_disk_space(self):
        if self.size * 2 + get_signature_file_size(self.size) > \
                get_free_space_by_filepath(self.file_path):
            self._emit_no_disk_space()
            return False

        return True

    def pause(self, disconnect_cb=True):
        self._paused = True
        if disconnect_cb:
            self.disconnect_callbacks()
        self.stop_download_chunks()

    def resume(self, start_download=True):
        self._started_time = time()
        self._paused = False
        self.hash_is_wrong = False
        if start_download:
            self._started = True
            self._download_chunks()
            if not self._timeout_timer.isActive():
                self._timeout_timer.start()

    def cancel(self):
        self._close_file()
        self._close_info_file()
        self.stop_download_chunks()

        self._finished = True

    def clean(self):
        logger.debug("Cleaning download files %s", self.download_path)
        try:
            remove_file(self.download_path)
        except:
            pass
        try:
            remove_file(self._info_path)
        except:
            pass

    def connect_callbacks(self, on_downloaded, on_failed):
        self._on_downloaded_cb = on_downloaded
        self._on_failed_cb = on_failed

    def disconnect_callbacks(self):
        self._on_downloaded_cb = None
        self._on_failed_cb = None

    @property
    def ready(self):
        return self._ready

    @property
    def paused(self):
        return self._paused

    @property
    def no_disk_space_error(self):
        return self._no_disk_space_error

    def _init_wanted_chunks(self):
        self._total_chunks_count = math.ceil(
            float(self.size) / float(DOWNLOAD_CHUNK_SIZE))

        self._wanted_chunks[0] = self.size

    def _on_downloaded(self, task):
        if callable(self._on_downloaded_cb):
            self._on_downloaded_cb(task)
            self._on_downloaded_cb = None

    def _on_failed(self, task):
        if callable(self._on_failed_cb):
            self._on_failed_cb(task)
            self._on_failed_cb = None

    def on_data_received(self, node_id, obj_id, offset, length, data):
        if obj_id != self.id or self._finished:
            return

        logger.debug(
            "on_data_received for objId: %s, offset: %s, from node_id: %s",
            self.id, offset, node_id)

        now = time()
        last_received_time = self._nodes_last_receive_time.get(node_id, 0.)
        if node_id in self._nodes_last_receive_time:
            self._nodes_last_receive_time[node_id] = now

        self._nodes_timeouts_count.pop(node_id, 0)

        downloaded_count = \
            self._nodes_downloaded_chunks_count.get(node_id, 0) + 1
        self._nodes_downloaded_chunks_count[node_id] = downloaded_count

        # to collect traffic info
        node_type = self._connectivity_service.get_self_node_type()
        is_share = node_type == "webshare"
        # tuple -> (obj_id, rx_wd, rx_wr, is_share)
        if self._connectivity_service.is_relayed(node_id):
            # relayed traffic
            info_rx = (obj_id, 0, length, is_share)
        else:
            # p2p traffic
            info_rx = (obj_id, length, 0, is_share)
        self.signal_info_rx.emit(info_rx)

        if not self._is_chunk_already_downloaded(offset):
            if not self._on_new_chunk_downloaded(
                    node_id, offset, length, data):
                return

        else:
            logger.debug("chunk %s already downloaded", offset)

        requested_chunks = self._nodes_requested_chunks.get(
            node_id, SortedDict())
        if not requested_chunks:
            return

        self._remove_from_chunks(offset, length, requested_chunks)

        if not requested_chunks:
            self._nodes_requested_chunks.pop(node_id, None)

        requested_count = sum(requested_chunks.values()) // DOWNLOAD_CHUNK_SIZE
        if downloaded_count * 4 >= requested_count \
                and requested_count < self.max_node_chunk_requests:
            self._download_next_chunks(node_id, now - last_received_time)
            self._clean_nodes_last_receive_time()
            self._check_download_not_ready(self._nodes_requested_chunks)

    def _is_chunk_already_downloaded(self, offset):
        if self._downloaded_chunks:
            chunk_index = self._downloaded_chunks.bisect_right(offset)
            if chunk_index > 0:
                chunk_index -= 1

                chunk = self._downloaded_chunks.peekitem(chunk_index)
                if offset < chunk[0] + chunk[1]:
                    return True

        return False

    def _on_new_chunk_downloaded(self, node_id, offset, length, data):
        if not self._write_to_file(offset, data):
            return False

        self.received += length
        if self._connectivity_service.is_relayed(node_id):
            self._received_via_turn += length
        else:
            self._received_via_p2p += length

        new_offset = offset
        new_length = length

        left_index = self._downloaded_chunks.bisect_right(new_offset)
        if left_index > 0:
            left_chunk = self._downloaded_chunks.peekitem(left_index - 1)
            if left_chunk[0] + left_chunk[1] == new_offset:
                new_offset = left_chunk[0]
                new_length += left_chunk[1]
                self._downloaded_chunks.popitem(left_index - 1)

        right_index = self._downloaded_chunks.bisect_right(
            new_offset + new_length)
        if right_index > 0:
            right_chunk = self._downloaded_chunks.peekitem(right_index - 1)
            if right_chunk[0] == new_offset + new_length:
                new_length += right_chunk[1]
                self._downloaded_chunks.popitem(right_index - 1)

        self._downloaded_chunks[new_offset] = new_length

        assert self._remove_from_chunks(offset, length, self._wanted_chunks)

        logger.debug("new chunk downloaded from node: %s, wanted size: %s",
                     node_id,
                     sum(self._wanted_chunks.values()))

        part_offset = (offset / DOWNLOAD_PART_SIZE) * DOWNLOAD_PART_SIZE
        part_size = min([DOWNLOAD_PART_SIZE, self.size - part_offset])
        if new_offset <= part_offset \
                and new_offset + new_length >= part_offset + part_size:
            if self._file:
                self._file.flush()
            self._write_info_file()

            self.chunk_downloaded.emit(self.id, str(part_offset), part_size)

        if self._complete_download():
            return False

        return True

    def _remove_from_chunks(self, offset, length, chunks):
        if not chunks:
            return False

        chunk_left_index = chunks.bisect_right(offset)
        if chunk_left_index > 0:
            left_chunk = chunks.peekitem(chunk_left_index - 1)
            if offset >= left_chunk[0] + left_chunk[1] \
                    and len(chunks) > chunk_left_index:
                left_chunk = chunks.peekitem(chunk_left_index)
            else:
                chunk_left_index -= 1
        else:
            left_chunk = chunks.peekitem(chunk_left_index)

        if offset >= left_chunk[0] + left_chunk[1] or \
                offset + length <= left_chunk[0]:
            return False

        chunk_right_index = chunks.bisect_right(offset + length)
        right_chunk = chunks.peekitem(chunk_right_index - 1)

        if chunk_right_index == chunk_left_index:
            to_del = [right_chunk[0]]
        else:
            to_del = list(chunks.islice(chunk_left_index, chunk_right_index))

        for chunk in to_del:
            chunks.pop(chunk)

        if left_chunk[0] < offset:
            if left_chunk[0] + left_chunk[1] >= offset:
                chunks[left_chunk[0]] = offset - left_chunk[0]

        if right_chunk[0] + right_chunk[1] > offset + length:
            chunks[offset + length] = \
                right_chunk[0] + right_chunk[1] - offset - length
        return True

    def on_data_failed(self, node_id, obj_id, offset, error):
        if obj_id != self.id or self._finished:
            return

        logger.info("data request failure, "
                    "node_id: %s, obj_id: %s, offset: %s, error: %s",
                    node_id, obj_id, offset, error)

        self.on_node_disconnected(node_id)

    def get_downloaded_chunks(self):
        if not self._downloaded_chunks:
            return None

        return self._downloaded_chunks

    def on_node_disconnected(
            self, node_id, connection_alive=False, timeout_limit_exceed=True):
        requested_chunks = self._nodes_requested_chunks.pop(
            node_id, None)
        logger.info("node disconnected %s, chunks removed from requested: %s",
                    node_id, requested_chunks)
        if timeout_limit_exceed:
            self._nodes_available_chunks.pop(node_id, None)
            self._nodes_timeouts_count.pop(node_id, None)
            if connection_alive:
                self._connectivity_service.reconnect(node_id)
        self._nodes_last_receive_time.pop(node_id, None)
        self._nodes_downloaded_chunks_count.pop(node_id, None)

        if connection_alive:
            self.abort_data.emit(node_id, self.id, None)

        if self._nodes_available_chunks:
            self._download_chunks(check_node_busy=True)
        else:
            chunks_to_test = self._nodes_requested_chunks \
                if self._started and not self._paused \
                else self._nodes_available_chunks
            self._check_download_not_ready(chunks_to_test)

    def complete(self):
        if self._started and not self._finished:
            self._complete_download(force_complete=True)
        elif not self._finished:
            self._finished = True
            self.clean()
            self.download_complete.emit(self)

    def _download_chunks(self, check_node_busy=False):
        if not self._started or self._paused or self._finished:
            return

        logger.debug("download_chunks for %s", self.id)

        node_ids = list(self._nodes_available_chunks.keys())
        random.shuffle(node_ids)
        for node_id in node_ids:
            node_free = not check_node_busy or \
                        not self._nodes_requested_chunks.get(node_id, None)
            if node_free:
                self._download_next_chunks(node_id)
        self._clean_nodes_last_receive_time()
        self._check_download_not_ready(self._nodes_requested_chunks)

    def _check_can_receive(self, node_id):
        return True

    def _write_to_file(self, offset, data):
        self._file.seek(offset)
        try:
            self._file.write(data)
        except EnvironmentError as e:
            logger.error("Download task %s can't write to file. Reason: %s",
                         self.id, e)
            self._send_error_statistic()
            if e.errno == errno.ENOSPC:
                self._emit_no_disk_space(error=True)
            else:
                self.download_failed.emit(self)
                self.possibly_sync_folder_is_removed.emit()
            return False

        return True

    def _open_file(self, clean=False):
        if not self._file or self._file.closed:
            try:
                if clean:
                    self._file = open(self.download_path, 'wb')
                else:
                    self._file = open(self.download_path, 'r+b')
            except IOError:
                try:
                    self._file = open(self.download_path, 'wb')
                except IOError as e:
                    logger.error("Can't open file for download for task %s. "
                                 "Reason: %s", self.id, e)
                    self.download_failed.emit(self)
                    return False

        return True

    def _close_file(self):
        if not self._file:
            return True

        try:
            self._file.close()
        except EnvironmentError as e:
            logger.error("Download task %s can't close file. Reason: %s",
                         self.id, e)
            self._send_error_statistic()
            if e.errno == errno.ENOSPC:
                self._emit_no_disk_space(error=True)
            else:
                self.download_failed.emit(self)
                self.possibly_sync_folder_is_removed.emit()
            self._file = None
            return False

        self._file = None
        return True

    def _write_info_file(self):
        try:
            self._info_file.seek(0)
            self._info_file.truncate()
            pickle.dump(
                self._downloaded_chunks, self._info_file,
                pickle.HIGHEST_PROTOCOL)
            self._info_file.flush()
        except EnvironmentError as e:
            logger.debug("Can't write to info file for task id %s. Reason: %s",
                         self.id, e)

    def _read_info_file(self):
        try:
            if not self._info_file or self._info_file.closed:
                self._info_file = open(self._info_path, 'a+b')
                self._info_file.seek(0)
            try:
                self._downloaded_chunks = pickle.load(self._info_file)
            except:
                pass
        except EnvironmentError as e:
            logger.debug("Can't open info file for task id %s. Reason: %s",
                         self.id, e)

    def _close_info_file(self, to_remove=False):
        if not self._info_file:
            return

        try:
            self._info_file.close()
            if to_remove:
                remove_file(self._info_path)
        except Exception as e:
            logger.debug("Can't close or remove info file "
                         "for task id %s. Reason: %s", self.id, e)
        self._info_file = None

    def _complete_download(self, force_complete=False):
        if (not self._wanted_chunks or force_complete) and \
                not self._finished:
            logger.debug("download %s completed", self.id)
            self._nodes_requested_chunks.clear()
            for node_id in self._nodes_last_receive_time.keys():
                self.abort_data.emit(
                    node_id, self.id, None)

            if not force_complete:
                self.download_finishing.emit()

            if not force_complete and self.file_hash:
                hash_check_result = self._check_file_hash()
                if hash_check_result is not None:
                    return hash_check_result

            self._started = False
            self._finished = True
            self.stop_download_chunks()
            self._close_info_file(to_remove=True)
            if not self._close_file():
                return False

            try:
                if force_complete:
                    remove_file(self.download_path)
                    self.download_complete.emit(self)
                else:
                    shutil.move(self.download_path, self.file_path)
                    self._send_end_statistic()
                    self.download_complete.emit(self)
                    if self.file_hash:
                        self.copy_added.emit(self.file_hash)
            except EnvironmentError as e:
                logger.error("Download task %s can't (re)move file. "
                             "Reason: %s", self.id, e)
                self._send_error_statistic()
                self.download_failed.emit(self)
                self.possibly_sync_folder_is_removed.emit()
                return False

            result = True
        else:
            result = not self._wanted_chunks
        return result

    def _check_file_hash(self):
        self._file.flush()
        try:
            hash = Rsync.hash_from_block_checksum(
                Rsync.block_checksum(self.download_path))
        except IOError as e:
            logger.error("download %s error: %s", self.id, e)
            hash = None
        if hash != self.file_hash:
            logger.error(
                "download hash check failed objId: %s, "
                "expected hash: %s, actual hash: %s",
                self.id, self.file_hash, hash)
            if not self._close_file() or not self._open_file(clean=True):
                return False

            self._downloaded_chunks.clear()
            self._nodes_downloaded_chunks_count.clear()
            self._nodes_last_receive_time.clear()
            self._nodes_timeouts_count.clear()
            self._write_info_file()
            self._init_wanted_chunks()

            self.received = 0
            if self._retry < self.retry_limit:
                self._retry += 1
                self.resume()
            else:
                self._retry = 0
                self._nodes_available_chunks.clear()
                self.hash_is_wrong = True
                self.wrong_hash.emit(self)
            return True

        return None

    def _download_next_chunks(self, node_id, time_from_last_received_chunk=0.):
        if (self._paused or not self._started
            or not self._ready or self._finished
            or not self._wanted_chunks
            or self._leaky_timer.isActive()):
            return

        total_requested = sum(map(
            lambda x: sum(x.values()), self._nodes_requested_chunks.values()))

        if total_requested + self.received >= self.size:
            if self._nodes_requested_chunks.get(node_id, None) and \
                    time_from_last_received_chunk <= self.end_race_timeout:
                return

            available_chunks = \
                self._get_end_race_chunks_to_download_from_node(node_id)
        else:
            available_chunks = \
                self._get_available_chunks_to_download_from_node(node_id)

        if not available_chunks:
            logger.debug("no chunks available for download %s", self.id)
            logger.debug("downloading from: %s nodes, length: %s, wanted: %s",
                         len(self._nodes_requested_chunks),
                         total_requested,
                         self.size - self.received)
            return

        available_offset = random.sample(available_chunks.keys(), 1)[0]
        available_length = available_chunks[available_offset]
        logger.debug("selected random offset: %s", available_offset)

        parts_count = math.ceil(
            float(available_length) / float(DOWNLOAD_PART_SIZE)) - 1
        logger.debug("parts count: %s", parts_count)

        part_to_download_number = random.randint(0, parts_count)
        offset = available_offset + \
                 part_to_download_number * DOWNLOAD_PART_SIZE
        length = min(DOWNLOAD_PART_SIZE,
                     available_offset + available_length - offset)
        logger.debug("selected random part: %s, offset: %s, length: %s",
                     part_to_download_number, offset, length)

        self._request_data(node_id, offset, length)

    def _get_end_race_chunks_to_download_from_node(self, node_id):
        available_chunks = self._nodes_available_chunks.get(node_id, None)
        if not available_chunks:
            return []

        available_chunks = available_chunks.copy()
        logger.debug("end race downloaded_chunks: %s", self._downloaded_chunks)
        logger.debug("end race requested_chunks: %s",
                     self._nodes_requested_chunks)
        logger.debug("end race available_chunks before excludes: %s",
                     available_chunks)
        if self._downloaded_chunks:
            for downloaded_chunk in self._downloaded_chunks.items():
                self._remove_from_chunks(
                    downloaded_chunk[0], downloaded_chunk[1], available_chunks)
        if not available_chunks:
            return []

        available_from_other_nodes = available_chunks.copy()
        for requested_offset, requested_length in \
                self._nodes_requested_chunks.get(node_id, dict()).items():
            self._remove_from_chunks(
                requested_offset, requested_length, available_from_other_nodes)

        result = available_from_other_nodes if available_from_other_nodes \
            else available_chunks

        if result:
            logger.debug("end race available_chunks after excludes: %s",
                         available_chunks)
        return result

    def _get_available_chunks_to_download_from_node(self, node_id):
        available_chunks = self._nodes_available_chunks.get(node_id, None)
        if not available_chunks:
            return []

        available_chunks = available_chunks.copy()
        logger.debug("downloaded_chunks: %s", self._downloaded_chunks)
        logger.debug("requested_chunks: %s", self._nodes_requested_chunks)
        logger.debug("available_chunks before excludes: %s", available_chunks)
        for _, requested_chunks in self._nodes_requested_chunks.items():
            for requested_offset, requested_length in requested_chunks.items():
                self._remove_from_chunks(
                    requested_offset, requested_length, available_chunks)
        if not available_chunks:
            return []

        for downloaded_chunk in self._downloaded_chunks.items():
            self._remove_from_chunks(
                downloaded_chunk[0], downloaded_chunk[1], available_chunks)
        logger.debug("available_chunks after excludes: %s", available_chunks)
        return available_chunks

    def _request_data(self, node_id, offset, length):
        logger.debug("Requesting date from node %s, request_chunk (%s, %s)",
                     node_id, offset, length)
        if self._limiter:
            try:
                self._limiter.leak(length)
            except LeakyBucketException:
                if node_id not in self._nodes_requested_chunks:
                    self._nodes_last_receive_time.pop(node_id, None)
                    if not self._network_limited_error_set:
                        self.download_error.emit('Network limited.')
                        self._network_limited_error_set = True
                if not self._leaky_timer.isActive():
                    self._leaky_timer.start()
                return

        if self._network_limited_error_set:
            self._network_limited_error_set = False
            self.download_ok.emit()

        requested_chunks = self._nodes_requested_chunks.get(node_id, None)
        if not requested_chunks:
            requested_chunks = SortedDict()
            self._nodes_requested_chunks[node_id] = requested_chunks
        requested_chunks[offset] = length
        logger.debug("Requested chunks %s", requested_chunks)
        self._nodes_last_receive_time[node_id] = time()
        self.request_data.emit(node_id, self.id, str(offset), length)

    def _clean_nodes_last_receive_time(self):
        for node_id in list(self._nodes_last_receive_time.keys()):
            if node_id not in self._nodes_requested_chunks:
                self._nodes_last_receive_time.pop(node_id, None)

    def _on_check_timeouts(self):
        if self._paused or not self._started \
                or self._finished or self._leaky_timer.isActive():
            return

        timed_out_nodes = set()
        cur_time = time()
        logger.debug("Chunk requests check %s",
                     len(self._nodes_requested_chunks))
        if self._check_download_not_ready(self._nodes_requested_chunks):
            return

        for node_id in self._nodes_last_receive_time:
            last_receive_time = self._nodes_last_receive_time.get(node_id)
            if cur_time - last_receive_time > self.receive_timeout:
                timed_out_nodes.add(node_id)

        logger.debug("Timed out nodes %s, nodes last receive time %s",
                     timed_out_nodes, self._nodes_last_receive_time)
        for node_id in timed_out_nodes:
            timeout_count = self._nodes_timeouts_count.pop(node_id, 0)
            timeout_count += 1
            if timeout_count >= self.timeouts_limit:
                retry = False
            else:
                retry = True
                self._nodes_timeouts_count[node_id] = timeout_count
            logger.debug("Node if %s, timeout_count %s, retry %s",
                         node_id, timeout_count, retry)
            self.on_node_disconnected(
                node_id, connection_alive=True, timeout_limit_exceed=not retry)

    def _get_chunks_from_info(self, chunks, info):
        new_added = False
        for part_info in info:
            logger.debug("get_chunks_from_info part_info %s", part_info)
            if part_info.length == 0:
                continue

            if not chunks:
                chunks[part_info.offset] = part_info.length
                new_added = True
                continue

            result_offset = part_info.offset
            result_length = part_info.length
            left_index = chunks.bisect_right(part_info.offset)
            if left_index > 0:
                left_chunk = chunks.peekitem(left_index - 1)
                if (left_chunk[0] <= part_info.offset
                        and left_chunk[0] + left_chunk[1]
                        >= part_info.offset + part_info.length):
                    continue

                if part_info.offset <= left_chunk[0] + left_chunk[1]:
                    result_offset = left_chunk[0]
                    result_length = part_info.offset + \
                                    part_info.length - result_offset
                    left_index -= 1

            right_index = chunks.bisect_right(
                part_info.offset + part_info.length)
            if right_index > 0:
                right_chunk = chunks.peekitem(right_index - 1)
                if part_info.offset + part_info.length <= \
                        right_chunk[0] + right_chunk[1]:
                    result_length = right_chunk[0] + \
                                    right_chunk[1] - result_offset

            to_delete = list(chunks.islice(left_index, right_index))

            for to_del in to_delete:
                chunks.pop(to_del)

            new_added = True
            chunks[result_offset] = result_length

        return new_added

    def _store_availability_info(self, node_id, info):
        known_chunks = self._nodes_available_chunks.get(node_id, None)
        if not known_chunks:
            known_chunks = SortedDict()
            self._nodes_available_chunks[node_id] = known_chunks
        return self._get_chunks_from_info(known_chunks, info)

    def _check_download_not_ready(self, checkable):
        if not self._wanted_chunks and self._started:
            self._complete_download(force_complete=False)
            return False

        if self._leaky_timer.isActive():
            if not self._nodes_available_chunks:
                self._make_not_ready()
                return True

        elif not checkable:
            self._make_not_ready()
            return True

        return False

    def _make_not_ready(self):
        if not self._ready:
            return

        logger.info("download %s not ready now", self.id)
        self._ready = False
        self._started = False
        if self._timeout_timer.isActive():
            self._timeout_timer.stop()
        if self._leaky_timer.isActive():
            self._leaky_timer.stop()
        self.download_not_ready.emit(self)

    def _clear_globals(self):
        self._wanted_chunks.clear()
        self._downloaded_chunks.clear()
        self._nodes_available_chunks.clear()
        self._nodes_requested_chunks.clear()
        self._nodes_last_receive_time.clear()
        self._nodes_downloaded_chunks_count.clear()
        self._nodes_timeouts_count.clear()
        self._total_chunks_count = 0

    def stop_download_chunks(self):
        if self._leaky_timer.isActive():
            self._leaky_timer.stop()
        if self._timeout_timer.isActive():
            self._timeout_timer.stop()

        for node_id in self._nodes_requested_chunks:
            self.abort_data.emit(
                node_id, self.id, None)

        self._nodes_requested_chunks.clear()
        self._nodes_last_receive_time.clear()

    def _emit_no_disk_space(self, error=False):
        self._no_disk_space_error = True
        self._nodes_available_chunks.clear()
        self._clear_globals()
        self._make_not_ready()
        file_name = self.display_name.split()[-1] \
            if self.display_name else ""
        self.no_disk_space.emit(self, file_name, error)

    def _send_start_statistic(self):
        if self._tracker:
            self._tracker.download_start(self.id, self.size)

    def _send_end_statistic(self):
        if self._tracker:
            time_diff = time() - self._started_time
            if time_diff < 1e-3:
                time_diff = 1e-3

            self._tracker.download_end(
                self.id, time_diff,
                websockets_bytes=0,
                webrtc_direct_bytes=self._received_via_p2p,
                webrtc_relay_bytes=self._received_via_turn,
                chunks=len(self._downloaded_chunks),
                chunks_reloaded=0,
                nodes=len(self._nodes_available_chunks))

    def _send_error_statistic(self):
        if self._tracker:
            time_diff = time() - self._started_time
            if time_diff < 1e-3:
                time_diff = 1e-3

            self._tracker.download_error(
                self.id, time_diff,
                websockets_bytes=0,
                webrtc_direct_bytes=self._received_via_p2p,
                webrtc_relay_bytes=self._received_via_turn,
                chunks=len(self._downloaded_chunks),
                chunks_reloaded=0,
                nodes=len(self._nodes_available_chunks))
