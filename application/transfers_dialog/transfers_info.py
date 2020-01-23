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
from copy import deepcopy
from collections import deque

from PySide2.QtCore import Qt, QObject, Signal, QTimer

from application.transfers_dialog import TransfersDialog

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

SPEED_CHART_CAPACITY = 60

class TransfersInfo(QObject):
    downloads_info_changed = Signal(dict, bool)
    uploads_info_changed = Signal(dict)
    downloads_state_changed = Signal(dict)
    uploads_state_changed = Signal(dict)
    speed_size_changed = Signal(float, float, float, float)
    revert_downloads = Signal(list,     # reverted files
                              list,     # reverted patches
                              list)     # reverted shares
    pause_resume_clicked = Signal()
    transfers_ready = Signal()
    add_to_sync_folder = Signal(list)
    download_link_handler = Signal(str)

    def __init__(self, parent, parent_window, dp):
        QObject.__init__(self, parent)
        self._downloads_info = dict()
        self._uploads_info = dict()
        self._init_speed_size()

        self._parent_window = parent_window
        self._dp = dp
        self._transfers_dialog = None

        self._time_delta_timer = QTimer(self)
        self._time_delta_timer.setInterval(1 * 60 * 1000)   # 1 minute
        self._speed_charts_timer = QTimer(self)
        self._speed_charts_timer.setInterval(1000)   # 1 second
        self._speed_charts_timer.timeout.connect(
            self._update_speed_charts, Qt.QueuedConnection)
        self._speed_charts_timer.start()
        self._all_disconnected_timer = QTimer(self)
        self._all_disconnected_timer.setInterval(5 * 1000)
        self._all_disconnected_timer.setSingleShot(True)
        self._all_disconnected_timer.timeout.connect(
            self._on_all_disconnected)

        self._paused = False
        self._resuming = False

        self._transfers_dialog_calls = 0
        self._init_changed_statuses()
        self.transfers_ready.connect(
            self._on_transfers_ready, Qt.QueuedConnection)

        self._nodes_num = 0
        self._signalserver_address = ''

    def _init_speed_size(self):
        self._download_speed = 0
        self._download_size = 0
        self._upload_speed = 0
        self._upload_size = 0
        self._download_speeds = deque(
            [0] * SPEED_CHART_CAPACITY, maxlen=SPEED_CHART_CAPACITY)
        self._upload_speeds = deque(
            [0] * SPEED_CHART_CAPACITY, maxlen=SPEED_CHART_CAPACITY)

    def _init_changed_statuses(self):
        self._downloads_changed = False
        self._uploads_changed = False
        self._reload_downloads = False
        self._reload_uploads = False
        self._changed_info = dict()

    def update_info(self, downloads_info, uploads_info):
        logger.verbose("Updating transfers info")

        added_info, changed_info, deleted_info = downloads_info
        downloads_changed = added_info or changed_info or deleted_info \
                            or self._resuming
        reload_downloads = added_info or deleted_info or self._resuming
        self._resuming = False
        self._downloads_info.update(added_info)
        for obj_id, changed in list(changed_info.items()):
            saved_info = self._downloads_info.get(obj_id)
            if not saved_info:
                changed_info.pop(obj_id, None)
                continue

            old_state = saved_info["state"]
            was_current = old_state in TransfersDialog.CURRENT_TASK_STATES
            new_state = changed["state"]
            is_current = new_state in TransfersDialog.CURRENT_TASK_STATES
            reload_downloads = reload_downloads or not (
                    old_state == new_state or was_current and is_current)
            saved_info.update(changed)
        for obj_id in deleted_info:
            self._downloads_info.pop(obj_id, None)

        reload_uploads = set(self._uploads_info) != set(uploads_info)
        uploads_changed = self._uploads_info != uploads_info
        self._uploads_info = uploads_info

        self._update_info(reload_downloads, reload_uploads,
                          changed_info, downloads_changed, uploads_changed)

    def update_download_speed(self, value):
        self._download_speed = value
        self._update_speed_size()

    def update_download_size(self, value):
        self._download_size = value
        self._update_speed_size()

    def update_upload_speed(self, value):
        self._upload_speed = value
        self._update_speed_size()

    def update_upload_size(self, value):
        self._upload_size = value
        self._update_speed_size()

    def _update_speed_size(self):
        if not self._transfers_dialog:
            return

        self.speed_size_changed.emit(
            self._download_speed, self._download_size,
            self._upload_speed, self._upload_size)

    def _update_info(self, reload_downloads=True, reload_uploads=True,
                     changed_info=(),
                     downloads_changed=True, uploads_changed=True,
                     supress_paused=False):
        if not self._transfers_dialog:
            return

        if self._transfers_dialog_calls:
            logger.verbose("Transfers dialog not ready")
            self._downloads_changed |= bool(downloads_changed)
            self._uploads_changed |= bool(uploads_changed)
            self._reload_downloads |= bool(reload_downloads)
            self._reload_uploads |= bool(reload_uploads)
            self._changed_info.update(changed_info)
            return

        if downloads_changed:
            if reload_downloads:
                self.downloads_info_changed.emit(
                    deepcopy(self._downloads_info), supress_paused)
            else:
                self.downloads_state_changed.emit(deepcopy(changed_info))
            self._transfers_dialog_calls += 1

        if uploads_changed:
            if reload_uploads:
                self.uploads_info_changed.emit(deepcopy(self._uploads_info))
            else:
                self.uploads_state_changed.emit(deepcopy(self._uploads_info))
            self._transfers_dialog_calls += 1

        self._init_changed_statuses()

    def show_dialog(self):
        if self._transfers_dialog:
            self._transfers_dialog.raise_dialog()
            return

        self._transfers_dialog = TransfersDialog(
            self._parent_window,
            self.revert_downloads.emit,
            self.pause_resume_clicked.emit,
            self.add_to_sync_folder.emit,
            self._handle_link,
            self.transfers_ready.emit,
            self._paused,
            self._dp,
            SPEED_CHART_CAPACITY,
            self._download_speeds,
            self._upload_speeds,
            self._signalserver_address)

        self._connect_slots()
        self._time_delta_timer.start()
        self._transfers_dialog.set_nodes_num(self._nodes_num)
        if not self._nodes_num:
            self._all_disconnected_timer.start()
        self._transfers_dialog.show(self.on_dialog_finished)
        self._update_speed_size()
        self._update_info(supress_paused=True)

    def on_dialog_finished(self):
        self._disconnect_slots()
        if self._time_delta_timer.isActive():
            self._time_delta_timer.stop()
        if self._all_disconnected_timer.isActive():
            self._all_disconnected_timer.stop()
        self._transfers_dialog = None
        self._transfers_dialog_calls = 0

    def clear(self):
        self._downloads_info.clear()
        self._uploads_info.clear()
        self._changed_info = dict()
        self._transfers_dialog_calls = 0
        self._update_info(supress_paused=True)
        self._init_speed_size()
        self._update_speed_size()

    def close(self):
        self.clear()
        if not self._transfers_dialog:
            return

        self._transfers_dialog.close()

    def set_paused_state(self, paused):
        self._paused = paused
        if not self._transfers_dialog:
            return

        self._transfers_dialog.set_paused_state(paused)
        self._resuming = not paused

    def revert_failed(self, failed_uuids):
        if not self._transfers_dialog:
            return

        self._transfers_dialog.revert_failed(failed_uuids)
        self._update_info(supress_paused=True)

    def on_connected_nodes_changed(self, nodes_num):
        all_disconnected = self._nodes_num and not nodes_num
        self._nodes_num = nodes_num
        if not self._transfers_dialog:
            return

        if all_disconnected:
            if not self._all_disconnected_timer.isActive():
                self._all_disconnected_timer.start()
        elif nodes_num:
            if self._all_disconnected_timer.isActive():
                self._all_disconnected_timer.stop()
            self._transfers_dialog.set_nodes_num(nodes_num)

    def _on_all_disconnected(self):
        if self._transfers_dialog:
            self._transfers_dialog.show_all_disconnected_alert()

    def _connect_slots(self):
        self.downloads_info_changed.connect(
            self._transfers_dialog.on_downloads_info_changed,
            Qt.QueuedConnection)
        self.uploads_info_changed.connect(
            self._transfers_dialog.on_uploads_info_changed,
            Qt.QueuedConnection)
        self.downloads_state_changed.connect(
            self._transfers_dialog.on_downloads_state_changed,
            Qt.QueuedConnection)
        self.uploads_state_changed.connect(
            self._transfers_dialog.on_uploads_state_changed,
            Qt.QueuedConnection)
        self.speed_size_changed.connect(
            self._transfers_dialog.on_size_speed_changed,
            Qt.QueuedConnection)
        self._time_delta_timer.timeout.connect(
            self._transfers_dialog.refresh_time_deltas,
            Qt.QueuedConnection)

    def _disconnect_slots(self):
        try:
            self.downloads_info_changed.disconnect(
                self._transfers_dialog.on_downloads_info_changed)
            self.uploads_info_changed.disconnect(
                self._transfers_dialog.on_uploads_info_changed)
            self.downloads_state_changed.disconnect(
                self._transfers_dialog.on_downloads_state_changed)
            self.uploads_state_changed.disconnect(
                self._transfers_dialog.on_uploads_state_changed)
            self.speed_size_changed.disconnect(
                self._transfers_dialog.on_size_speed_changed)
            self._time_delta_timer.timeout.disconnect(
                self._transfers_dialog.refresh_time_deltas)
        except Exception as e:
            logger.warning("Can't disconnect transfers signal. Reason: %s", e)

    def _on_transfers_ready(self):
        self._transfers_dialog_calls -= 1
        if self._transfers_dialog_calls < 0:
            logger.warning("More ready signals than transfers dialog calls")
            self._transfers_dialog_calls = 0

        if not self._transfers_dialog_calls:
            logger.verbose("Transfers dialog is ready")
            self._update_info(
                self._reload_downloads, self._reload_uploads,
                self._changed_info, self._downloads_changed, self._uploads_changed,
                supress_paused=True)

    def _update_speed_charts(self):
        self._download_speeds.append(self._download_speed)
        self._upload_speeds.append(self._upload_speed)
        if not self._transfers_dialog:
            return

        self._transfers_dialog.update_speed_charts(
            self._download_speed, self._upload_speed)

    def dialog_opened(self):
        return bool(self._transfers_dialog)

    def _handle_link(self, link, is_shared):
        if is_shared:
            self.download_link_handler.emit(link)

    def set_signalserver_address(self, address):
        self._signalserver_address = address
        if self._transfers_dialog:
            self._transfers_dialog.set_signalserver_address(address)
