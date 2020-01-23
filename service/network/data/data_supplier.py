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
import json
from abc import abstractmethod
import time

import logging
import errno
from threading import RLock

from PySide2.QtCore import QObject, Signal, QTimer

from service.network.browser_sharing import Message, ProtoError

from common.constants import DOWNLOAD_CHUNK_SIZE

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DataSupplier(QObject):
    class DataRequest(object):
        def __init__(self, node_id, msg, node_type):
            self.node_id = node_id
            self.obj_id = msg.obj_id
            if len(msg.info) > 0:
                self.offset = msg.info[0].offset
                self.length = msg.info[0].length
            else:
                self.offset = None
                self.length = None
            self.node_type = node_type

    _data_request = Signal(Message, str)
    _data_abort = Signal(Message, str)
    signal_info_tx = Signal(tuple)
    supplying_finished = Signal()

    processing_requests_limit = 4

    _processing_data_requests = set()
    _queued_data_requests = list()

    _lock = RLock()

    INFO_LIFETIME = 1 * 60      # 1 minute

    def __init__(self, parent, connectivity_service):
        QObject.__init__(self, parent=parent)

        self._connectivity_service = connectivity_service

        self._data_request.connect(self._on_data_request)
        self._data_abort.connect(self._on_data_abort)
        self._connectivity_service.node_incoming_disconnected.connect(
            self._on_node_disconnected)

        self._uploads_info = dict()

    def get_uploads_info(self):
        now = time.time()

        for obj_id in list(self._uploads_info.keys()):
            if now - self._uploads_info[obj_id]["time"] > self.INFO_LIFETIME:
                self._uploads_info.pop(obj_id, None)

        return  self._uploads_info

    def _on_data_request(self, msg, node_id):
        node_type = self._connectivity_service.get_node_type(node_id)
        if not node_type:
            return

        request = self.DataRequest(
            node_id, msg, node_type)

        QTimer.singleShot(100, lambda: self._add_request(request))

    def _add_request(self, request):
        with self._lock:
            processing_requests_size = len(self._processing_data_requests)
            if processing_requests_size < \
                    self.processing_requests_limit:
                logger.debug("Add data request for processing: "
                             "obj_id: %s, offset: %s, "
                             "processing_requests: %s",
                             request.obj_id, request.offset,
                             processing_requests_size)
                self._process_request(request)
            else:
                logger.debug("Add data request to queue: "
                             "obj_id: %s, offset: %s, "
                             "processing_requests: %s, "
                             "queued_requests: %s",
                             request.obj_id, request.offset,
                             processing_requests_size,
                             len(self._queued_data_requests))
                self._put_request_to_queue(request)

    def _process_request(self, request):
        try:
            msgs = self._generate_response_messages(
                request.obj_id, request.offset, request.length,
                request.node_type)
        except ProtoError as e:
            err = json.dumps({"err_code": e.err_code, "err_message":
                              e.err_message}).encode()
            msg = self._generate_failure_message(
                request.obj_id, request.offset, err)
            self._connectivity_service.send(request.node_id, msg, True)
            return
        if not msgs and request.length > 0:
            self._put_request_to_queue(request)
            return
        self._processing_data_requests.add(request)
        logger.debug(
            "Sending data response for obj_id: %s, offset: %s, "
            "length: %s, to node: %s",
            request.obj_id, request.offset, request.length, request.node_id)
        self._connectivity_service.send_messages(
            request.node_id, msgs, request,
            on_sent_callback=self._on_data_sent,
            check_func=self._check_processing,
        )

    def _read_data_by_chunks_from_file(self, path, offset, length):
        try:
            chunks = []
            with open(path, 'rb') as f:
                f.seek(offset)

                while length > 0:
                    size = min([DOWNLOAD_CHUNK_SIZE, length])
                    data = f.read(size)
                    if len(data) != size:
                        raise ProtoError("FILE_READING_ERROR", "")
                    chunks.append((offset, size, data))
                    offset += size
                    length -= size
        except Exception as e:
            if isinstance(e, OSError) and e.errno == errno.EACCES:
                return []
            logger.error(
                "Failed to read data from '%s', offset %s,  "
                "length %s, error: %s",
                path, offset, length, e)
            raise ProtoError("FILE_READING_ERROR", "")

        return chunks

    def _check_processing(self, request):
        with self._lock:
            return request in self._processing_data_requests

    def _on_data_sent(self, request):
        with self._lock:
            logger.debug(
                "Data response for obj_id: %s, offset: %s, to node: %s sent",
                request.obj_id, request.offset, request.node_id)

            # to collect traffic info
            is_share = request.node_type == "webshare"
            # tuple -> (obj_id, tx_wd, tx_wr, is_share)
            if self._connectivity_service.is_relayed(request.node_id):
                # relayed traffic
                info_tx = (request.obj_id, 0, request.length, is_share)
            else:
                # p2p traffic
                info_tx = (request.obj_id, request.length, 0, is_share)
            self.signal_info_tx.emit(info_tx)

            self._processing_data_requests.discard(request)
            if len(self._processing_data_requests) < \
                    self.processing_requests_limit:
                request = self._get_request_from_queue()
                if not request:
                    self.supplying_finished.emit()
                    return
                logger.debug("Processing next data request: "
                             "obj_id: %s, offset: %s, "
                             "processing_requests: %s, "
                             "queued_requests: %s",
                             request.obj_id, request.offset,
                             len(self._processing_data_requests),
                             len(self._queued_data_requests))
                self._process_request(request)

    def _on_data_abort(self, msg, node_id):
        logger.debug("Data abort for obj_id: %s, from node: %s",
                     msg.obj_id, node_id)
        request = self.DataRequest(node_id, msg, None)
        with self._lock:
            processing_old_size = len(self._processing_data_requests)
            queued_old_size = len(self._queued_data_requests)

            self._abort_request(request)

            logger.debug("Data abort processed: "
                         "node: %s, obj_id: %s, offset: %s"
                         "processing_requests: %s-%s, queued_requests: %s-%s",
                         node_id, request.obj_id, request.offset,
                         processing_old_size, len(self._processing_data_requests),
                         queued_old_size, len(self._queued_data_requests))

    def _on_node_disconnected(self, node_id):
        with self._lock:
            for req in self._processing_data_requests.copy():
                if req.node_id == node_id:
                    self._processing_data_requests.discard(req)
            for req in list(self._queued_data_requests):
                if req.node_id == node_id:
                    self._queued_data_requests.remove(req)

    def _put_request_to_queue(self, request):
        self._queued_data_requests.append(request)

    def _get_request_from_queue(self):
        try:
            return self._queued_data_requests.pop(0)
        except IndexError:
            return None

    def _abort_request(self, request):
        for req in self._processing_data_requests.copy():
            if self._is_requests_same(request, req):
                self._processing_data_requests.discard(req)
        for req in list(self._queued_data_requests):
            if self._is_requests_same(request, req):
                self._queued_data_requests.remove(req)

    def _is_requests_same(self, request, req):
        return (request.node_id == req.node_id
                and request.obj_id == req.obj_id
                and (request.offset is None or request.offset == req.offset)
                )

    @abstractmethod
    def _generate_response_messages(self, obj_id, offset, length, node_type):
        raise NotImplemented()

    @abstractmethod
    def _generate_failure_message(self, obj_id, offset, error):
        raise NotImplemented()
