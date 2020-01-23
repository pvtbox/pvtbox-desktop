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
from collections import defaultdict
from threading import RLock, Timer


class NetworkSpeedCalculator(object):
    __slots__ = [
        '_notification_period', '_lock', '_timer',
        '_uploaded', '_downloaded', '_last_uploaded', '_last_downloaded',
        '_uploaded_total', '_downloaded_total',
        '_upload_statistic', '_download_statistic',
        '_upload_speed_changed_cb', '_download_speed_changed_cb',
        '_upload_size_changed_cb', '_download_size_changed_cb']

    def __init__(self,
                 notification_period,
                 upload_speed_changed_cb=None,
                 download_speed_changed_cb=None,
                 upload_size_changed_cb=None,
                 download_size_changed_cb=None):
        self._notification_period = float(notification_period)
        assert self._notification_period >= 1.
        self._upload_speed_changed_cb = upload_speed_changed_cb
        self._download_speed_changed_cb = download_speed_changed_cb
        self._upload_size_changed_cb = upload_size_changed_cb
        self._download_size_changed_cb = download_size_changed_cb

        self._lock = RLock()
        self._timer = None

        self._uploaded = 0.
        self._downloaded = 0.

        self._uploaded_total = 0.
        self._downloaded_total = 0.

        self._last_uploaded = 0.
        self._last_downloaded = 0.

        self._upload_statistic = defaultdict(int)
        self._download_statistic = defaultdict(int)

    def on_data_uploaded(self, bytes, type=None):
        with self._lock:
            self._uploaded += bytes
            if type is not None:
                self._upload_statistic[type] += bytes
            self._run_timer()

    def on_data_downloaded(self, bytes, type=None):
        with self._lock:
            self._downloaded += bytes
            if type is not None:
                self._download_statistic[type] += bytes
            self._run_timer()

    def get_network_statistics(self):
        with self._lock:
            return self._upload_statistic.copy(), self._download_statistic.copy()

    def clear_network_statistics(self):
        with self._lock:
            self._upload_statistic.clear()
            self._download_statistic.clear()

    def _run_timer(self):
        if self._timer is None:
            self._timer = Timer(self._notification_period, self._notify)
            self._timer.start()

    def _notify(self):
        with self._lock:
            self._timer = None

            self._notify_upload()
            self._notify_download()

        if self._last_downloaded or self._last_uploaded:
            self._run_timer()

    def _notify_upload(self):
        if self._upload_speed_changed_cb and \
                self._last_uploaded != self._uploaded:
            self._upload_speed_changed_cb(
                self._calculate_average_speed(self._uploaded))

        self._last_uploaded = self._uploaded
        self._uploaded_total += self._uploaded
        self._uploaded = 0

        if self._upload_size_changed_cb and self._last_uploaded != 0:
            self._upload_size_changed_cb(self._uploaded_total)

    def _notify_download(self):
        if self._download_speed_changed_cb and \
                self._last_downloaded != self._downloaded:
            self._download_speed_changed_cb(
                self._calculate_average_speed(self._downloaded))

        self._last_downloaded = self._downloaded
        self._downloaded_total += self._downloaded
        self._downloaded = 0

        if self._download_size_changed_cb and self._last_downloaded != 0:
            self._download_size_changed_cb(self._downloaded_total)

    def _calculate_average_speed(self, volume):
        return volume / self._notification_period
