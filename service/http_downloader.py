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

import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor
from service.network.leakybucket import LeakyBucketException
from requests import Session

from common.constants import NETWORK_HTTP, REGULAR_URI
from common.ssl_pinning_adapter import SslPinningAdapter

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Params(object):

    def __init__(self):
        self._params = {
            'chunksize': 65536,
            'download_completed_cb': None,
            'download_error_cb': None,
            'download_progress_cb': None,
            'download_auth_data_cb': None,
            'get_upload_state_cb': None,
            'http_downloader_workers_count': 2,
        }

    def get(self, name):
        """
        Returns parameter with the name specified
        @param name Parameter name [string]
        @return Parameter value or None
        """

        try:
            return self._params[name]
        except KeyError:
            logger.error("Unknown parameter '%s'", name)
            return None

    def set_callbacks(
            self,
            on_download_completed=None, on_download_error=None,
            on_download_progress=None, on_download_auth_data_cb=None,
            on_get_upload_state_cb=None):
        '''
        Sets up callback function to be called on some events

        @param on_download_auth_data_cb Callback to be called on HTTP download
            start to obtain data to be sent to the server to confirm node auth.
            Arguments: upload_id [str]
        '''

        # Save callback if any
        if on_download_completed:
            self._params['download_completed_cb'] = on_download_completed
        if on_download_error:
            self._params['download_error_cb'] = on_download_error
        if on_download_progress:
            self._params['download_progress_cb'] = on_download_progress
        if on_download_auth_data_cb:
            self._params['download_auth_data_cb'] = on_download_auth_data_cb
        if on_get_upload_state_cb:
            self._params['get_upload_state_cb'] = on_get_upload_state_cb

    def get_chunk_size(self):
        '''
        Returns current setting for size of data chunk to be used
        when sending data via webrtc datachannel

        @return Size of chunk in bytes [int]
        '''

        return self._params['chunksize']


class HttpDownloader(object):
    def __init__(self, download_limiter=None, network_speed_calculator=None):
        self._params = Params()
        self._closing = False
        self._download_limiter = download_limiter
        self._limit = 1024 if self._download_limiter else 0
        workers_count = self._params.get('http_downloader_workers_count')
        self._executor = ThreadPoolExecutor(max_workers=workers_count)
        self._session = None
        self._host = REGULAR_URI

        self._network_speed_calculator = network_speed_calculator

    def download(self, id, url, path, timeout, do_post_request=False,
                 proceed=None, host=REGULAR_URI):
        self._host=host
        try:
            logger.verbose(
                'adding http download task. id: %s, url: %s, file: %s',
                id, url, path)
        except AttributeError:
            pass
        fut = self._executor.submit(
            self._download_task, id, url, path, do_post_request, timeout,
            proceed)
        fut.id = id
        fut.add_done_callback(self._download_task_done_cb)

    def close(self, immediately=True):
        self._closing = immediately
        self._executor.shutdown(not immediately)

    def _get_timeout(self, timeout, chunk_size):
        if self._limit:
            timeout = max(timeout, chunk_size // self._limit * 2)
        return timeout

    def set_download_limiter(self, download_speed_limiter):
        self._download_limiter = download_speed_limiter
        self._limit = 1024 if self._download_limiter else 0

    def _download_task(self, id, url, path, do_post_request, timeout, proceed):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            loop.set_debug(True)
            asyncio.set_event_loop(loop)
        coro = self._download_task_coroutine(id,
                                             url,
                                             path,
                                             do_post_request,
                                             timeout,
                                             proceed)
        return loop.run_until_complete(coro)

    @asyncio.coroutine
    def _download_task_coroutine(self,
                                 id,
                                 url,
                                 path,
                                 do_post_request,
                                 timeout,
                                 proceed):

        chunk_size = self._params.get_chunk_size()
        headers=dict()
        offset = 0
        if proceed:
            offset, size = proceed
            headers = {'Range': '{}-{}'.format(offset, size)}
        coro = self._make_request(do_post_request, id, url, headers)

        num_tries = 10
        for i in range(num_tries):
            try:
                request = yield from asyncio.wait_for(coro, timeout=timeout)
                if request.status_code < 400:
                    break
                elif request.status_code == 404:
                    raise Exception("Not found")
            except asyncio.TimeoutError:
                logger.warning(
                    "Connection to http server timed out")
                if i == num_tries - 1:
                    raise Exception("Timeout")
            except requests.ConnectionError:
                logger.warning(
                    "Error connecting to http server")
                if i == num_tries - 1:
                    raise Exception("Connection error")
        else:
            raise Exception("Request to http server failed")

        loaded = offset
        started = asyncio.get_event_loop().time()
        total = int(request.headers.get('Content-Length', 0))
        chunk_size = chunk_size if chunk_size < total else total
        try:
            logger.verbose('http download task started. '
                        'id: %s, url: %s, length: %s, file: %s',
                        id, url, total, path)
        except AttributeError:
            pass
        elapsed = 0.0
        mode = 'wb' if not proceed or offset == 0 else 'ab'
        with open(path, mode) as f:
            while True:
                get_upload_state_cb = \
                    self._params.get('get_upload_state_cb')
                if callable(get_upload_state_cb):
                    state = get_upload_state_cb(id)
                    if not state:
                        raise Exception("Unknown upload state")
                    elif state in ('cancelled', 'paused'):
                        logger.debug("Download task %s interrupted. State %s",
                                     id, state)
                        return elapsed, total
                coro = self._load_chunk(chunk_size, request)
                timeout = self._get_timeout(timeout, chunk_size)
                try:
                    chunk = yield from asyncio.wait_for(coro, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "Chunk receiving from http server timed out")
                    raise Exception("Timeout")

                elapsed = asyncio.get_event_loop().time() - started

                if chunk is None:
                    return elapsed, total

                loaded_chunk_size = len(chunk)
                f.write(chunk)
                loaded += loaded_chunk_size
                left = total - loaded
                chunk_size = chunk_size if chunk_size < left else int(left)

                if not chunk_size:
                    return elapsed, total

                progress_cb = self._params.get('download_progress_cb')
                if callable(progress_cb):
                    progress_cb(id, loaded, total, elapsed)
                self._network_speed_calculator.on_data_downloaded(
                    loaded_chunk_size, NETWORK_HTTP)

    async def _load_chunk(self, chunk_size, request):
        await self._wait_till_can_download(chunk_size)
        for chunk in request.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            else:
                return chunk

    @asyncio.coroutine
    def _make_request(self, do_post_request, id, url, headers=None):
        # Requested using of POST request
        if self._session is None:
            self._session = Session()
            if self._host == REGULAR_URI:
                self._session.mount(self._host, SslPinningAdapter())
        if do_post_request:
            auth_data = None
            # Try to obtain auth data for download request
            download_auth_data_cb = self._params.get('download_auth_data_cb')
            if callable(download_auth_data_cb):
                try:
                    auth_data = download_auth_data_cb(id)
                except Exception as e:
                    logger.error("download_auth_data_cb() with exception '%s'",
                                 e)
                # No auth data obtained
                if auth_data is None:
                    logger.warning(
                        "No auth data obtained from download_auth_data_cb()")

            # Do HTTP POST request
            if not headers:
                headers = dict()
            r = self._session.post(
                url, data=auth_data, stream=True, headers=headers)
        else:
            # Do HTTP GET request
            r = self._session.get(
                url, stream=True)
        return r

    def _download_task_done_cb(self, fut):
        id = fut.id
        try:
            elapsed, total = fut.result()
        except Exception as e:
            logger.error("Download task %s failed", id, exc_info=True)
            error_cb = self._params.get('download_error_cb')
            if callable(error_cb):
                error_cb(id, str(e))
        else:
            logger.info('download task completed. id: %s, '
                        'downloaded: %s bytes in %s seconds',
                        id, total, elapsed)
            completed_cb = self._params.get('download_completed_cb')
            if callable(completed_cb):
                completed_cb(id, elapsed, total)

    async def _wait_till_can_download(self, chunk_size):
        while self._download_limiter is not None:
            try:
                self._download_limiter.leak(chunk_size)
                break
            except LeakyBucketException:
                logger.debug("Can't download chunk due network limits, "
                             "waiting...")
                await asyncio.sleep(0.01)

    def set_callbacks(self, *args, **kwargs):
        self._params.set_callbacks(*args, **kwargs)


if __name__ == '__main__':
    from unittest.mock import Mock

    def progress(id, loaded, total, elapsed):
        print((">> progress: {} {} {} {}".format(id, loaded, total, elapsed)))
        pass

    def completed(id, total, elapsed):
        print((">> completed: {} {} {}".format(id, total, elapsed)))
        clean(id)

    def error(id, message):
        print((">> error: {} {}".format(id, message)))
        clean(id)

    def clean(id):
        import os
        os.remove('download_{}'.format(id))

    http_downloader = HttpDownloader(None, Mock())
    http_downloader.set_callbacks(
        on_download_completed=completed,
        on_download_error=error,
        on_download_progress=progress)

    for i in range(9):
        url = 'http://download.qt.io/official_releases/' \
              'online_installers/qt-unified-linux-x86-online.run'
        http_downloader.download(i, url, 'download_{}'.format(i), 3)

    http_downloader.close(immediately=False)
