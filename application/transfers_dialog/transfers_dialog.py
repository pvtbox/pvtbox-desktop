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
from collections import defaultdict, deque

from PySide2.QtCore import Qt, QSize
from PySide2.QtGui import QIcon, QFont, QColor
from PySide2.QtWidgets import QDialog, QLabel, QListWidgetItem, QWidget, \
    QHBoxLayout, QVBoxLayout, QPushButton, QSpacerItem, \
    QSizePolicy, QListView, QProgressBar, QStackedWidget, QApplication, \
    QFileDialog

from transfers import Ui_Dialog
from application.utils import elided, get_added_time_string, msgbox
from common.translator import tr
from application.transfers_dialog.speed_chart import SpeedChart
from application.transfers_dialog.insert_link_dialog import InsertLinkDialog
from common.utils import format_with_units
from common.constants import DOWNLOAD_NOT_READY, DOWNLOAD_READY, \
    DOWNLOAD_STARTING, DOWNLOAD_LOADING, DOWNLOAD_FINISHING, DOWNLOAD_FAILED, \
    DOWNLOAD_NO_DISK_ERROR

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class TransfersDialog(object):
    FILE_LIST_ITEM_SIZE = 88
    CURRENT_TASK_STATES = {DOWNLOAD_STARTING, DOWNLOAD_LOADING,
                           DOWNLOAD_FINISHING, DOWNLOAD_FAILED}
    ERROR_STATES = {DOWNLOAD_NO_DISK_ERROR}
    STATE_NOTIFICATIONS = {
        DOWNLOAD_NOT_READY: tr("Waiting for nodes..."),
        DOWNLOAD_READY: tr("Waiting for other downloads..."),
        DOWNLOAD_STARTING: tr("Starting download..."),
        DOWNLOAD_LOADING: tr("Downloading..."),
        DOWNLOAD_FINISHING: tr("Finishing download..."),
        DOWNLOAD_FAILED: tr("Download failed"),
        DOWNLOAD_NO_DISK_ERROR: tr("Insufficient disk space"),
    }

    WORKING = 0
    PAUSED = 1
    RESUMING = 2
    PAUSED_NOTIFICATIONS = {
        PAUSED: tr("Paused..."),
        RESUMING: tr("Resuming..."),
    }

    def __init__(self, parent, revert_downloads, pause_resume_clicked,
                 add_to_sync_folder, handle_link, transfers_ready,
                 paused, dp=None,
                 speed_chart_capacity=0, download_speeds=(), upload_speeds=(),
                 signalserver_address=''):
        self._dialog = QDialog(parent)
        self._dp = dp
        self._revert_downloads = revert_downloads
        self._pause_resume_clicked = pause_resume_clicked
        self._add_to_sync_folder = add_to_sync_folder
        self._handle_link = handle_link
        self._transfers_ready = transfers_ready
        self._parent = parent
        self._signalserver_address = signalserver_address

        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._reverted_downloads = set()
        self._downloads_items = defaultdict(list)
        self._uploads_items = defaultdict(list)
        self._http_downloads = set()

        self._paused_state = self.WORKING if not paused else self.PAUSED

        self._total_files = 0
        self._total_size = 0

        self._init_ui()
        self._init_charts(download_speeds, upload_speeds, speed_chart_capacity)


    def _init_ui(self):
        self._dialog.setWindowFlags(Qt.Dialog)
        self._dialog.setAttribute(Qt.WA_TranslucentBackground)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)

        self._set_file_list_options(self._ui.downloads_list)
        self._set_file_list_options(self._ui.uploads_list)
        self._ui.downloads_list.verticalScrollBar().valueChanged.connect(
            self.on_downloads_scroll_changed)
        self._ui.uploads_list.verticalScrollBar().valueChanged.connect(
            self.on_uploads_scroll_changed)

        self._old_main_resize_event = self._ui.centralwidget.resizeEvent
        self._ui.centralwidget.resizeEvent = self._main_resize_event

        self._set_fonts()

        if self._paused_state == self.PAUSED:
            self._ui.pause_all_button.setText(tr("Resume all"))
            self._ui.pause_all_button.setIcon(QIcon(":/images/play.svg"))
        else:
            self._ui.pause_all_button.setText(tr("Pause all   "))
            self._ui.pause_all_button.setIcon(QIcon(":/images/pause.svg"))

    def _init_charts(self, download_speeds, upload_speeds,
                     speed_chart_capacity):
        self._last_downloads_speeds = deque(download_speeds,
            maxlen=speed_chart_capacity)
        self._last_uploads_speeds = deque(upload_speeds,
            maxlen=speed_chart_capacity)
        max_download_speed = max(self._last_downloads_speeds) \
            if self._last_downloads_speeds else 0
        max_upload_speed = max(self._last_uploads_speeds) \
            if self._last_uploads_speeds else 0
        max_speed = max(max_download_speed, max_upload_speed)
        self._download_speed_chart = SpeedChart(
            self._ui.downloads_speed_widget, speed_chart_capacity,
            QColor("green"), speeds=download_speeds,
            dp=self._dp, max_speed=max_speed)
        self._upload_speed_chart = SpeedChart(
            self._ui.uploads_speed_widget, speed_chart_capacity,
            QColor("orange"), speeds=upload_speeds, is_upload=True,
            dp=self._dp, max_speed=max_speed)

    def on_size_speed_changed(self, download_speed, download_size,
                              upload_speed, upload_size):
        self._ui.download_speed_value.setText(
            tr("{}/s").format(format_with_units(download_speed)))
        self._ui.download_size_value.setText(format_with_units(download_size))

        self._ui.upload_speed_value.setText(
            tr("{}/s").format(format_with_units(upload_speed)))
        self._ui.upload_size_value.setText(format_with_units(upload_size))

    def on_downloads_info_changed(self, downloads_info, supress_paused=False):
        logger.verbose("Updating downloads_info")
        self._update_downloads_list(downloads_info, supress_paused)
        self._transfers_ready()

    def on_downloads_state_changed(self, changed_info):
        if self._paused_state == self.PAUSED:
            self._transfers_ready()
            return

        elif self._paused_state == self.RESUMING:
            self._paused_state = self.WORKING

        logger.verbose("Changing downloads state with %s", changed_info)
        for obj_id in changed_info:
            items = self._downloads_items.get(obj_id, [])
            for item in items:
                self._change_item_widget(
                    self._ui.downloads_list,
                    item, changed_info[obj_id]["state"],
                    changed_info[obj_id]["downloaded"])

        self._transfers_ready()

    def on_uploads_info_changed(self, uploads_info):
        logger.verbose("Updating uploads_info")
        self._update_uploads_list(uploads_info)
        self._transfers_ready()

    def on_uploads_state_changed(self, changed_info):
        logger.verbose("Changing uploads state with %s", changed_info)
        for obj_id in changed_info:
            items = self._uploads_items.get(obj_id, [])
            for item in items:
                self._change_item_widget(
                    self._ui.uploads_list,
                    item, changed_info[obj_id]["state"],
                    changed_info[obj_id]["uploaded"])

        self._transfers_ready()

    def refresh_time_deltas(self):
        self._refresh_file_list_time_deltas(
            self._ui.downloads_list, self._downloads_items)
        self._refresh_file_list_time_deltas(
            self._ui.uploads_list, self._uploads_items)

    def show(self, on_finished):
        def finished():
            self._dialog.finished.disconnect(finished)
            self._ui.pause_all_button.clicked.disconnect(pause_all)
            self._ui.revert_all_button.clicked.disconnect(revert_all)
            self._ui.add_button.clicked.disconnect(add)
            self._ui.insert_link_button.clicked.disconnect(insert_link)
            on_finished()

        def pause_all():
            self._toggle_paused_state()

        def revert_all():
            if self._downloads_items and \
                    not self._has_user_confirmed_revert():
                return

            self._revert_all()

        def add():
            self._on_add_to_sync_folder()

        def insert_link():
            self._on_insert_link()

        logger.debug("Opening transfers dialog")

        screen_width = QApplication.desktop().width()
        parent_x = self._dialog.parent().x()
        parent_width = self._dialog.parent().width()
        width = self._dialog.width()
        offset = 16
        if parent_x + parent_width / 2 > screen_width / 2:
            x = parent_x - width - offset
            if x < 0:
                x = 0
        else:
            x = parent_x + parent_width + offset
            diff = x + width - screen_width
            if diff > 0:
                x -= diff
        self._dialog.move(x, self._dialog.parent().y())

        self._dialog.setAcceptDrops(True)
        self._dialog.dragEnterEvent = self._drag_enter_event
        self._dialog.dropEvent = self._drop_event

        # Execute dialog
        self._dialog.finished.connect(finished)
        self._ui.pause_all_button.clicked.connect(pause_all)
        self._ui.revert_all_button.clicked.connect(revert_all)
        self._ui.add_button.clicked.connect(add)
        self._ui.insert_link_button.clicked.connect(insert_link)
        self._dialog.raise_()
        self._dialog.show()

    def raise_dialog(self):
        self._dialog.raise_()

    def close(self):
        self._dialog.reject()

    def revert_failed(self, failed_uuids):
        self._reverted_downloads.difference_update(set(failed_uuids))

    def set_nodes_num(self, nodes_num):
        self._dialog.setWindowTitle(
            tr("Transfers - {} peer(s) connected").format(nodes_num))

    def show_all_disconnected_alert(self):
        self._dialog.setWindowTitle(
            tr("Transfers - Connect more devices to sync"))

    def _get_downloads_obj_ids_sorted(self, downloads_info):
        def sort_key(obj_id):
            info = downloads_info[obj_id]
            return -info['priority'] * 10000 - \
                   (info['downloaded'] - info['size']) // (64 * 1024)

        current_tasks = []
        ready_tasks = []
        not_ready_tasks = []
        for obj_id, info in downloads_info.items():
            state = info["state"]
            if state in self.CURRENT_TASK_STATES:
                current_tasks.append(obj_id)
            elif state == DOWNLOAD_READY:
                ready_tasks.append(obj_id)
            else:
                not_ready_tasks.append(obj_id)
        ready_tasks.sort(key=sort_key)
        not_ready_tasks.sort(key=sort_key)
        obj_ids_sorted = current_tasks + ready_tasks + not_ready_tasks
        return obj_ids_sorted

    def _update_downloads_list(self, downloads_info, supress_paused=False):
        if self._paused_state == self.PAUSED and not supress_paused:
            return

        elif self._paused_state == self.RESUMING:
            self._paused_state = self.WORKING

        obj_ids_sorted = self._get_downloads_obj_ids_sorted(downloads_info)
        self._downloads_items.clear()
        self._http_downloads.clear()
        self._ui.downloads_list.setUpdatesEnabled(False)
        self._total_size = 0
        self._total_files = 0
        index = 0
        for obj_id in obj_ids_sorted:
            if obj_id in self._reverted_downloads:
                continue

            info = downloads_info[obj_id]
            for file_info in info["files_info"]:
                self._add_file_to_file_list(
                    index,
                    self._ui.downloads_list, self._downloads_items, obj_id,
                    rel_path=file_info["target_file_path"],
                    created_time=file_info["mtime"],
                    was_updated=not file_info.get("is_created", True),
                    is_deleted=file_info.get("is_deleted"),
                    transfered=info["downloaded"],
                    size=info["size"],
                    state=info["state"],
                    is_file=info["is_file"])
                self._total_size += info["size"]
                self._total_files += 1
                index += 1

        for i in range(index, self._ui.downloads_list.count()):
            item = self._ui.downloads_list.takeItem(index)
            self._ui.downloads_list.removeItemWidget(item)

        self._reverted_downloads.intersection_update(set(obj_ids_sorted))

        self._update_totals()
        self._set_revert_all_enabled()
        self._set_current_downloads_page()
        self._ui.downloads_list.setUpdatesEnabled(True)

    def _update_totals(self):
        self._ui.total_files_label.setText(tr("{} file(s)").format(
            self._total_files))
        self._ui.total_size_label.setText(format_with_units(self._total_size))

    def _update_uploads_list(self, uploads_info):
        self._uploads_items.clear()
        self._ui.uploads_list.setUpdatesEnabled(False)
        total_files = 0
        index = 0
        for obj_id in uploads_info:
            info = uploads_info[obj_id]
            for file_info in info["files_info"]:
                self._add_file_to_file_list(
                    index,
                    self._ui.uploads_list, self._uploads_items, obj_id,
                    rel_path=file_info["target_file_path"],
                    created_time=file_info["mtime"],
                    was_updated=not file_info.get("is_created", True),
                    is_deleted=file_info.get("is_deleted"),
                    transfered=info["uploaded"],
                    size=info["size"],
                    state=info["state"],
                    is_file=info["is_file"])
                total_files += 1
                index += 1

        for i in range(index, self._ui.uploads_list.count()):
            item = self._ui.uploads_list.takeItem(index)
            self._ui.uploads_list.removeItemWidget(item)

        self._set_current_uploads_page()
        self._ui.uploads_list.setUpdatesEnabled(True)

    def _set_fonts(self):
        ui = self._ui
        controls = [ui.no_downloads_label, ui.no_uploads_label]
        controls.extend([c for c in ui.downloads_frame.findChildren(QLabel)])
        controls.extend(
            [c for c in ui.downloads_bottom.findChildren(QLabel)])
        controls.extend(
            [c for c in ui.downloads_bottom.findChildren(QPushButton)])
        controls.extend([c for c in ui.uploads_frame.findChildren(QLabel)])
        controls.extend(
            [c for c in ui.uploads_bottom.findChildren(QPushButton)])

        for control in controls:
            font = control.font()
            font_size = control.font().pointSize() * self._dp
            if font_size > 0:
                control.setFont(QFont(font.family(), font_size))

    def _set_file_list_options(self, file_list):
        file_list.setFocusPolicy(Qt.NoFocus)
        file_list.setFont(QFont('Nano', 10 * self._dp))
        # file_list.setGridSize(QSize(
        #     self.FILE_LIST_ITEM_SIZE, self.FILE_LIST_ITEM_SIZE - 14))
        file_list.setResizeMode(QListView.Adjust)
        file_list.setAutoScroll(False)
        file_list.setUniformItemSizes(True)

    def _add_file_to_file_list(self, index, file_list, items_dict, obj_id,
                               rel_path, created_time, was_updated,
                               is_deleted, transfered,
                               size=0, state=None, is_file=True):
        item = file_list.item(index)
        if item:
            item.setData(
                Qt.UserRole,
                [rel_path, created_time, size, was_updated, is_deleted,
                 transfered, state, is_file, obj_id])
            self._update_file_list_item_widget(file_list, item)
            items_dict[obj_id].append(item)
            return

        item = QListWidgetItem()
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        item.setSizeHint(QSize(
            file_list.width(), self.FILE_LIST_ITEM_SIZE))
        item.setData(
            Qt.UserRole,
            [rel_path, created_time, size, was_updated, is_deleted,
             transfered, state, is_file, obj_id])

        file_list.addItem(item)
        rect = file_list.viewport().contentsRect()
        top = file_list.indexAt(rect.topLeft())
        if top.isValid():
            bottom = file_list.indexAt(rect.bottomLeft())
            if not bottom.isValid():
                bottom = file_list.model().index(file_list.count() - 1)
            if top.row() <= file_list.row(item) <= bottom.row() + 1:
                widget = self._create_file_list_item_widget(
                    file_list, [rel_path, created_time, size,
                    was_updated, is_deleted, transfered, state, is_file, obj_id])
                file_list.setItemWidget(item, widget)
        if item not in items_dict[obj_id]:
            items_dict[obj_id].append(item)

    def on_downloads_scroll_changed(self, *args, **kwargs):
        self._on_list_scroll_changed(self._ui.downloads_list)

    def on_uploads_scroll_changed(self, *args, **kwargs):
        self._on_list_scroll_changed(self._ui.uploads_list)

    def _on_list_scroll_changed(self, file_list):
        rect = file_list.viewport().contentsRect()
        top = file_list.indexAt(rect.topLeft())
        if top.isValid():
            bottom = file_list.indexAt(rect.bottomLeft())
            if not bottom.isValid():
                bottom = file_list.model().index(file_list.count() - 1)
            for index in range(top.row(), bottom.row() + 1):
                item = file_list.item(index)
                widget = file_list.itemWidget(item)
                if widget:
                    continue
                widget = self._create_file_list_item_widget(
                    file_list, item.data(Qt.UserRole))
                file_list.setItemWidget(item, widget)

    def _create_file_list_item_widget(self, file_list, data):
        rel_path, created_time, \
        size, was_updated, is_deleted, \
        transfered, state, is_file, obj_id = data
        is_upload = state is None   # uploads list
        is_shared = not is_upload and created_time == 0
        is_http_download = not is_upload and created_time < 0
        if is_http_download:
            self._http_downloads.add(obj_id)

        widget = QWidget(parent=file_list)
        widget.setFixedHeight(self.FILE_LIST_ITEM_SIZE)

        main_layout = QVBoxLayout(widget)
        main_layout.setSpacing(2)

        file_name_label = QLabel(widget)
        file_name_label.setObjectName("file_name_label")
        file_name_label.setFixedWidth(max(file_list.width() - 80, 320))
        file_name_label.setFixedHeight(20)
        file_name_label.setFont(QFont('Noto Sans', 10 * self._dp))
        file_name_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        file_name_label.setText(
            elided(rel_path,
                   file_name_label))
        main_layout.addWidget(file_name_label)

        time_size_revert_layout = QHBoxLayout()
        time_size_revert_layout.setSpacing(0)
        main_layout.addLayout(time_size_revert_layout)

        time_size_layout = QVBoxLayout()
        time_size_layout.setSpacing(0)
        time_size_revert_layout.addLayout(time_size_layout)
        time_size_revert_layout.addStretch()

        time_delta_label = QLabel(widget)
        time_delta_label.setObjectName("time_delta_label")
        if is_shared:
            time_delta_label.setText(tr("Shared file"))
        elif is_http_download:
            time_delta_label.setText(tr("Uploaded from web"))
        else:
            try:
                time_delta_label.setText(get_added_time_string(
                    created_time, was_updated, is_deleted))
            except RuntimeError:
                pass
        time_delta_label.setFont(QFont('Noto Sans', 8 * self._dp))
        time_delta_label.setMinimumHeight(14)
        time_delta_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        time_delta_label.setStyleSheet('color: #A792A9;')
        time_size_layout.addWidget(time_delta_label)

        is_created = not was_updated and not is_deleted and not is_shared
        if not is_upload:
            revert_button = QPushButton(widget)
            revert_button.is_entered = False
            revert_button.setObjectName("revert_button")
            revert_button.setFlat(True)
            revert_button.setChecked(True)
            revert_button.setFont(QFont(
                "Noto Sans", 8 * self._dp, italic=True))
            revert_button.setMouseTracking(True)
            revert_button.setCursor(Qt.PointingHandCursor)
            self._set_revert_button_options(
                revert_button, obj_id, is_created, is_shared, is_http_download,
                is_file, rel_path, size)

            time_size_revert_layout.addWidget(
                revert_button, alignment=Qt.AlignVCenter)
            spacerItem = QSpacerItem(
                6, 10, QSizePolicy.Maximum, QSizePolicy.Minimum)
            time_size_layout.addItem(spacerItem)

        size_layout = QHBoxLayout()
        size_layout.setSpacing(0)
        time_size_layout.addLayout(size_layout)
        self._set_size_layout(size_layout, widget, transfered, size, is_upload)

        if is_upload:
            spacerItem = QSpacerItem(6, 6, QSizePolicy.Maximum, QSizePolicy.Minimum)
            main_layout.addItem(spacerItem)
        else:
            progress_layout = QHBoxLayout()
            progress_layout.setSpacing(6)
            main_layout.addLayout(progress_layout)
            self._set_progress_layout(
                progress_layout, widget, transfered, size, state)

        if is_upload:
            return widget

        def enter(_):
            revert_button.is_entered = True
            is_created = revert_button.property("properties")[0]
            color = '#f9af61' if not is_created else 'red'
            revert_button.setStyleSheet(
                'QPushButton {{margin: 0;border: 0; text-align:right center;'
                'color: {0};}} '
                'QPushButton:!enabled {{color: #aaaaaa;}} '
                'QToolTip {{background-color: #222222; color: white;}}'
                    .format(color))
            revert_button.setIcon(
                    QIcon(':images/transfers/{}_active.svg'.format(
                        revert_button.text().strip().lower())))

        def leave(_):
            revert_button.is_entered = False
            revert_button.setStyleSheet(
                'QPushButton {margin: 0;border: 0; text-align:right center;'
                'color: #333333;} '
                'QPushButton:!enabled {color: #aaaaaa;}')
            revert_button.setIcon(
                    QIcon(':images/transfers/{}_inactive.svg'.format(
                        revert_button.text().strip().lower())))

        revert_button.enterEvent = enter
        revert_button.leaveEvent = leave

        def revert_button_clicked():
            is_created, is_shared, is_file, rel_path, obj_id, size = \
                revert_button.property("properties")
            color = '#f78d1e' if not is_created else '#e50000'
            revert_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right center;'
                'color: {};'.format(color))
            revert_button.setIcon(
                    QIcon(':images/transfers/{}_clicked.svg'.format(
                        revert_button.text().strip().lower())))
            if not self._has_user_confirmed_revert(
                    rel_path, is_shared, is_created):
                return

            self._reverted_downloads.add(obj_id)
            reverted_files = reverted_patches = reverted_shares = []
            if is_shared:
                reverted_shares = [obj_id]
            elif is_file:
                reverted_files = [obj_id]
            else:
                reverted_patches = [obj_id]
            self._revert_downloads(
                reverted_files, reverted_patches, reverted_shares)
            items = self._downloads_items.get(obj_id, [])
            for item in items:
                self._ui.downloads_list.takeItem(
                    self._ui.downloads_list.row(item))
            self._total_files = max(self._total_files - len(items), 0)
            self._total_size = max(self._total_size - size, 0)
            self._update_totals()
            self._set_revert_all_enabled()
            self._set_current_downloads_page()

        revert_button.clicked.connect(revert_button_clicked)

        return widget

    def _set_size_layout(self, size_layout, widget, transfered,
                         size, is_upload):
        direction_label = QLabel(widget)
        direction_label.setMinimumHeight(14)
        direction_text = '\u2191\u0020' if is_upload else '\u2193\u0020'
        direction_label.setText(direction_text)
        direction_label.setFont(QFont('Noto Sans', 8 * self._dp))
        direction_label.setAlignment(
            Qt.AlignRight | Qt.AlignTrailing | Qt.AlignVCenter)
        direction_label.setStyleSheet('color: #A792A9;')
        size_layout.addWidget(direction_label)

        transfered_label = QLabel(widget)
        transfered_label.setObjectName("transfered_label")
        transfered_label.setMinimumHeight(14)
        transfered_label.setText(format_with_units(transfered))
        transfered_label.setFont(QFont('Noto Sans', 8 * self._dp))
        transfered_label.setAlignment(
            Qt.AlignLeading | Qt.AlignLeft | Qt.AlignVCenter)
        transfered_label.setStyleSheet('color: #A792A9;')
        size_layout.addWidget(transfered_label)

        if not is_upload:
            slash_label = QLabel(widget)
            slash_label.setMinimumHeight(14)
            slash_label.setText('/')
            slash_label.setFont(QFont('Noto Sans', 8 * self._dp))
            slash_label.setAlignment(
                Qt.AlignRight | Qt.AlignTrailing | Qt.AlignVCenter)
            slash_label.setStyleSheet('color: #A792A9;')
            size_layout.addWidget(slash_label)

            size_label = QLabel(widget)
            size_label.setObjectName("size_label")
            size_label.setMinimumHeight(14)
            size_label.setText(format_with_units(size))
            size_label.setFont(QFont('Noto Sans', 8 * self._dp))
            size_label.setAlignment(
                Qt.AlignLeading | Qt.AlignLeft | Qt.AlignVCenter)
            size_label.setStyleSheet('color: #A792A9;')
            size_layout.addWidget(size_label)

        size_layout.addStretch()

    def _set_progress_layout(self, progress_layout, widget, transfered,
                             size, state):
        is_current = state in self.CURRENT_TASK_STATES
        is_error = state in self.ERROR_STATES

        progress_background = QStackedWidget(widget)
        progress_background.setObjectName("progress_background")
        progress_bar = QProgressBar(progress_background)
        progress_bar.setObjectName("progress_bar")
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(
            size if is_current and state != DOWNLOAD_FAILED and
                    self._paused_state == self.WORKING else 0)
        if is_current:
            progress_bar.setValue(transfered)
        progress_bar.setTextVisible(False)

        progress_label = QLabel(widget)
        progress_label.setObjectName("progress_label")

        self._set_progress_bar_style(
            progress_bar, progress_background, progress_label,
            state, is_current, is_error)

        progress_background.addWidget(progress_bar)
        progress_layout.addWidget(progress_background,
                                  alignment=Qt.AlignVCenter)

        progress_label.setFont(QFont('Noto Sans', 7 * self._dp))
        progress_layout.addWidget(progress_label)
        spacerItem = QSpacerItem(
            6, 10, QSizePolicy.Maximum, QSizePolicy.Minimum)
        progress_layout.addItem(spacerItem)

    def _set_revert_button_options(self, revert_button, obj_id, is_created,
                                   is_shared, is_http_download,
                                   is_file, rel_path, size):
        revert_text = tr("Delete") if is_created \
            else tr('Revert') if not is_shared and not is_http_download \
            else tr("Cancel")
        revert_button.setText(revert_text + '  ')
        revert_button.setIcon(
            QIcon(':images/transfers/{}_{}.svg'.format(
                revert_button.text().strip().lower(),
                'active' if revert_button.is_entered else 'inactive')))
        tooltip_text = tr("Action disabled while sync paused") \
            if not is_http_download and self._paused_state == self.PAUSED \
            else tr("Delete file and cancel download") if is_created \
            else tr("Revert changes and cancel download") \
            if not is_shared and not is_http_download \
            else tr("Cancel shared file download") if is_shared \
            else tr("You can cancel upload from web panel")
        revert_button.setToolTip(tooltip_text)
        revert_button.setStyleSheet(
            'QPushButton {{margin: 0;border: 0; text-align:right center;'
            'color: {0};}} '
            'QPushButton:!enabled {{color: #aaaaaa;}}'.format(
                '#333333' if not revert_button.is_entered else '#f9af61' if not is_created else 'red'
            ))
        revert_button.setEnabled(
            not is_http_download and self._paused_state != self.PAUSED)

        revert_button.setProperty(
            "properties",
            [is_created, is_shared, is_file, rel_path, obj_id, size])

    def _update_file_list_item_widget(self, file_list, item):
        rel_path, \
        created_time, \
        size, \
        was_updated, \
        is_deleted, \
        transfered, \
        state, \
        is_file, \
        obj_id = item.data(Qt.UserRole)

        is_upload = state is None   # uploads list
        is_shared = not is_upload and created_time == 0
        is_created = not was_updated and not is_deleted and not is_shared
        is_http_download = not is_upload and created_time < 0
        if is_http_download:
            self._http_downloads.add(obj_id)

        is_current = state in self.CURRENT_TASK_STATES
        is_error = state in self.ERROR_STATES

        widget = file_list.itemWidget(item)
        if not widget:
            return

        file_name_label = widget.findChildren(QLabel, "file_name_label")[0]
        file_name_label.setText(elided(rel_path, file_name_label))

        time_delta_label = widget.findChildren(QLabel, "time_delta_label")[0]
        if is_shared:
            time_delta_label.setText(tr("Shared file"))
        elif is_http_download:
            time_delta_label.setText(tr("Uploaded from web"))
        else:
            try:
                time_delta_label.setText(get_added_time_string(
                    created_time, was_updated, is_deleted))
            except RuntimeError:
                pass

        transfered_label = widget.findChildren(QLabel, "transfered_label")[0]
        transfered_label.setText(format_with_units(transfered))

        if is_upload:
            return

        size_label = widget.findChildren(QLabel, "size_label")[0]
        size_label.setText(format_with_units(size))

        revert_button = widget.findChildren(QPushButton, "revert_button")[0]
        self._set_revert_button_options(
            revert_button, obj_id, is_created, is_shared, is_http_download,
            is_file, rel_path, size)

        progress_bar = widget.findChildren(QProgressBar, "progress_bar")[0]
        progress_background = widget.findChildren(
            QStackedWidget, "progress_background")[0]
        progress_bar.setValue(transfered)
        progress_bar.setMaximum(
            size if is_current and state != DOWNLOAD_FAILED and
                    self._paused_state == self.WORKING else 0)
        progress_label = widget.findChildren(QLabel,"progress_label")[0]
        self._set_progress_bar_style(
            progress_bar, progress_background, progress_label,
            state, is_current, is_error)

    def _change_item_widget(self, file_list, item, state=None, transfered=None):
        rel_path, \
        created_time, \
        size, \
        was_updated, \
        is_deleted, \
        old_transfered, \
        old_state, \
        is_file, \
        obj_id = item.data(Qt.UserRole)

        if transfered is None:
            state = old_state
            transfered = old_transfered
            is_upload = False
        else:
            is_upload = state is None
            item.setData(
                Qt.UserRole,
                [rel_path, created_time, size, was_updated, is_deleted,
                 transfered, state, is_file, obj_id])

        widget = file_list.itemWidget(item)
        if not widget:
            return

        is_shared = not is_upload and created_time == 0
        is_created = not was_updated and not is_deleted and not is_shared
        is_http_download = not is_upload and created_time < 0

        children = widget.findChildren(QLabel, "transfered_label")
        if not children or len(children) > 1:
            logger.warning("Can't find transfered_label for %s", rel_path)
        else:
            transfered_label = children[0]
            transfered_label.setText(format_with_units(transfered))

        if is_upload:
            return

        is_current = state in self.CURRENT_TASK_STATES
        is_error = state in self.ERROR_STATES

        children = widget.findChildren(QProgressBar, "progress_bar")
        back_children = widget.findChildren(QStackedWidget,
                                            "progress_background")
        if not children or len(children) > 1 or \
                not back_children or len(back_children) > 1:
            logger.warning("Can't find progress_bar for %s", rel_path)
            return

        progress_background = back_children[0]
        progress_bar = children[0]
        progress_bar.setValue(transfered)
        progress_bar.setMaximum(
            size if is_current and state != DOWNLOAD_FAILED and
                    self._paused_state == self.WORKING else 0)

        children = widget.findChildren(QLabel,"progress_label")
        if not children or len(children) > 1:
            logger.warning("Can't find progress_label for %s", rel_path)
            return

        progress_label = children[0]
        self._set_progress_bar_style(
            progress_bar, progress_background, progress_label,
            state, is_current, is_error)

        revert_button = widget.findChildren(QPushButton, "revert_button")[0]
        self._set_revert_button_options(
            revert_button, obj_id, is_created, is_shared, is_http_download,
            is_file, rel_path, size)

    def _set_progress_bar_style(self, progress_bar, progress_background,
                                progress_label, state, is_current, is_error):
        progress_active = is_current and self._paused_state == self.WORKING
        if progress_active:
            progress_background.setStyleSheet("background-color: #cceed6")
            progress_bar.setStyleSheet(
                "QProgressBar::chunk {"
                    "background-color: #01AB33;"
                "}"
            )
            progress_background.setFixedHeight(2)
            progress_bar.setFixedHeight(2)
        elif is_error:
            progress_background.setStyleSheet("background-color: #red")
            progress_bar.setStyleSheet(
                "QProgressBar::chunk {"
                    "background-color: #ffcccb;"
                "}"
            )
            progress_background.setFixedHeight(1)
            progress_bar.setFixedHeight(1)
        else:
            progress_background.setStyleSheet("background-color: #d6d6d6")
            progress_bar.setStyleSheet(
                "QProgressBar::chunk {"
                    "background-color: #777777;"
                "}"
            )
            progress_background.setFixedHeight(1)
            progress_bar.setFixedHeight(1)

        progress_text = self.STATE_NOTIFICATIONS[state] \
            if self._paused_state == self.WORKING or is_error \
            else self.PAUSED_NOTIFICATIONS[self._paused_state]
        progress_label.setText(progress_text)
        progress_label.setStyleSheet(
            "color: #01AB33" if progress_active
            else "color: #A792A9;" if not is_error
            else "color: red;")

    def _refresh_file_list_time_deltas(self, file_list, items):
        for obj_id in items:
            for item in items.get(obj_id, []):
                self._refresh_item_time_delta(file_list, item)

    def _refresh_item_time_delta(self, file_list, item):
        rel_path, \
        created_time, \
        size, \
        was_updated, \
        is_deleted, \
        transfered, \
        state, \
        is_file, \
        obj_id = item.data(Qt.UserRole)

        is_upload = state is None   # uploads list
        is_shared = not is_upload and created_time == 0
        is_http_download = not is_upload and created_time < 0
        if is_shared or is_http_download:
            return

        widget = file_list.itemWidget(item)
        if not widget:
            return
        children = widget.findChildren(QLabel,"time_delta_label")
        if not children or len(children) > 1:
            logger.warning("Can't find time_delta_label for %s", rel_path)
        else:
            time_delta_label = children[0]
            try:
                time_delta_label.setText(get_added_time_string(
                    created_time, was_updated, is_deleted))
            except RuntimeError:
                pass

    def _revert_all(self):
        logger.verbose("Revert downloads")
        reverted_files = reverted_patches = reverted_shares = []
        for obj_id in list(self._downloads_items.keys()):
            if obj_id in self._reverted_downloads:
                continue

            items = self._downloads_items.get(obj_id, [])
            if not items:
                logger.warning("No items for obj_id %s", obj_id)
                continue
            first_item = items[0]

            rel_path, \
            created_time, \
            size, \
            was_updated, \
            is_deleted, \
            transfered, \
            state, \
            is_file, \
            old_obj_id = first_item.data(Qt.UserRole)
            is_shared = created_time == 0
            is_http_download = created_time < 0
            if is_http_download:
                continue

            if is_shared:
                reverted_shares.append(obj_id)
            elif is_file:
                reverted_files.append(obj_id)
            else:
                reverted_patches.append(obj_id)
            self._reverted_downloads.add(obj_id)
            self._total_files = max(self._total_files - len(items), 0)
            self._total_size = max(self._total_size - size, 0)
            for item in self._downloads_items[obj_id]:
                self._ui.downloads_list.takeItem(
                    self._ui.downloads_list.row(item))
            self._downloads_items.pop(obj_id, None)

        logger.verbose("Reverting downloads %s, %s, %s",
                       reverted_files, reverted_patches, reverted_shares)
        self._revert_downloads(
            reverted_files, reverted_patches, reverted_shares)

        self._set_revert_all_enabled()
        self._update_totals()
        self._set_current_downloads_page()

    def _toggle_paused_state(self):
        self._pause_resume_clicked()

    def set_paused_state(self, paused=True):
        if paused:
            self._paused_state = self.PAUSED
            self._ui.pause_all_button.setText(tr("Resume all"))
            self._ui.pause_all_button.setIcon(QIcon(":/images/play.svg"))
        else:
            self._paused_state = self.RESUMING
            self._ui.pause_all_button.setText(tr("Pause all   "))
            self._ui.pause_all_button.setIcon(QIcon(":/images/pause.svg"))

        self._set_revert_all_enabled()

        logger.verbose(
            "Downloads %s", self.PAUSED_NOTIFICATIONS[self._paused_state])
        for obj_id in self._downloads_items:
            items = self._downloads_items.get(obj_id, [])
            for item in items:
                self._change_item_widget(self._ui.downloads_list, item)

    def _has_user_confirmed_revert(self, file_path=None,
                                  is_share=False, is_created=False):
        if file_path:       # 1 file
            if is_share:
                msg_text = tr("Do you want to cancel shared file {} download?") \
                    .format(file_path)
            elif is_created:
                msg_text = tr("Do you want to delete file {} "
                              "from all your devices?").format(file_path)
            else:
                msg_text = tr("Do you want to revert last changes for file {} "
                              "on all your devices?").format(file_path)
        else:               # many files
            msg_text = tr("Do you want to delete new files from all your devices,\n"
                          "revert all last changes on all devices,\n"
                          "and cancel shared files downloads?")

        userAnswer = msgbox(
            msg_text,
            buttons=[(tr('Yes'), 'Yes'),
                     (tr('No'), 'No'),],
            parent=self._dialog,
            default_index=1)
        return userAnswer == 'Yes'

    def _main_resize_event(self, e):
        self._old_main_resize_event(e)
        if e.oldSize().height() != self._ui.centralwidget.height():
            self.on_downloads_scroll_changed()
            self.on_uploads_scroll_changed()

        width = self._ui.centralwidget.width() // 2
        self._ui.downloads_frame.setFixedWidth(width)
        self._ui.uploads_frame.setFixedWidth(width)
        if e.oldSize().width() == self._ui.centralwidget.width():
            return

        speed_charts_height = self._ui.downloads_speed_widget.width() * 0.3
        self._ui.downloads_speed_widget.setFixedHeight(speed_charts_height)
        self._download_speed_chart.resize()
        self._ui.uploads_speed_widget.setFixedHeight(speed_charts_height)
        self._upload_speed_chart.resize()

        self._file_list_resizeEvent(
            self._ui.downloads_list, self._downloads_items)
        self._file_list_resizeEvent(
            self._ui.uploads_list, self._uploads_items)

    def _file_list_resizeEvent(self, file_list, file_list_items):
        for items in file_list_items.values():
            for item in items:
                self._resize_item(item, file_list)

    def _resize_item(self, item, file_list):
        rel_path, \
        created_time, \
        size, \
        was_updated, \
        is_deleted, \
        transfered, \
        state, \
        is_file, \
        obj_id = item.data(Qt.UserRole)

        widget = file_list.itemWidget(item)
        if not widget:
            return
        widget.setMaximumWidth(file_list.width())
        children = widget.findChildren(QLabel,"file_name_label")
        if not children or len(children) > 1:
            logger.warning("Can't find file_name_label for %s", rel_path)
        else:
            file_name_label = children[0]
            file_name_label.setFixedWidth(max(file_list.width() - 80, 320))
            file_name_label.setText(elided(rel_path, file_name_label))

    def _set_revert_all_enabled(self):
        self._ui.revert_all_button.setEnabled(
            bool(set(self._downloads_items) - self._http_downloads) and
            self._paused_state != self.PAUSED)

    def _set_current_downloads_page(self):
        self._ui.downloads_pages.setCurrentIndex(0 if self._downloads_items else 1)

    def _set_current_uploads_page(self):
        self._ui.uploads_pages.setCurrentIndex(0 if self._uploads_items else 1)

    def update_speed_charts(self, download_speed, upload_speed):
        self._last_downloads_speeds.append(download_speed)
        self._last_uploads_speeds.append(upload_speed)
        max_speed = max(max(self._last_downloads_speeds),
                        max(self._last_uploads_speeds))
        self._download_speed_chart.update(download_speed, max_speed)
        self._upload_speed_chart.update(upload_speed, max_speed)

    def _on_add_to_sync_folder(self):
        logger.verbose("Add files to sync directory")
        title = tr('Choose files to copy to sync directory')
        selected_files_or_folders = QFileDialog.getOpenFileNames(
            self._dialog, title)[0]
        self._add_to_sync_folder(selected_files_or_folders)

    def _drag_enter_event(self, event):
        data = event.mimeData()
        if data.hasUrls():
            event.accept()
        else:
            event.ignore()

    def _drop_event(self, event):
        data = event.mimeData()
        dropped_files_or_folders = []
        if data.hasUrls():
            event.acceptProposedAction()
            event.accept()
            for url in data.urls():
                dropped_files_or_folders.append(url.toLocalFile())
            self._add_to_sync_folder(dropped_files_or_folders)
        else:
            event.ignore()

    def _on_insert_link(self):
        insert_link_dialog = InsertLinkDialog(
            self._dialog, self._dp, self._signalserver_address)
        link, is_shared = insert_link_dialog.show()
        logger.debug("link '%s'", link)
        if link:
            self._handle_link(link, is_shared)

    def set_signalserver_address(self, address):
        self._signalserver_address = address
