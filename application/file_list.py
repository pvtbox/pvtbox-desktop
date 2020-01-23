# -*- coding: utf-8 -*-
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
import mimetypes
import os.path as op

from PySide2.QtCore \
    import QTimer, QSize, QObject, Qt, QFileInfo
from PySide2.QtCore import Signal as pyqtSignal
from PySide2.QtWidgets \
    import QWidget, QListWidgetItem, QHBoxLayout, \
    QLabel, QVBoxLayout, QPushButton, QFileIconProvider
from PySide2.QtGui import QFont, QIcon, QPixmap, QImage

from common.async_qt import qt_run
from common.file_path import FilePath
from common.utils import ensure_unicode
from .utils import elided, qt_reveal_file_in_file_manager, get_added_time_string
from common.translator import tr

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GuiFileList(QObject):
    FILE_LIST_ITEM_SIZE = 60
    FILE_LIST_ICON_SIZE = 48
    MAX_FILES = 7

    _icons_info_ready = pyqtSignal(list, list, set, set)

    def __init__(self, parent, service, config, dp):
        QObject.__init__(self, parent=parent)
        self._parent = parent
        self._service = service
        self._config = config
        self._dp = dp

        self._file_list_changing = False
        self._mime_icons = self._build_mime_icons()
        self._ui = self._parent.get_ui()
        self._files = dict()
        self._shared_files = set()

        self._ui.file_list.setFocusPolicy(Qt.NoFocus)
        self._ui.file_list.setAcceptDrops(True)
        self._ui.file_list.setFont(QFont('Nano', 10 * self._dp))
        self._ui.file_list.setGridSize(QSize(
            self.FILE_LIST_ITEM_SIZE, self.FILE_LIST_ITEM_SIZE))
        self._ui.file_list.setAutoScroll(False)
        self._ui.file_list.setUniformItemSizes(True)
        self._ui.file_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._ui.file_list.dropEvent = self._file_list_drop_event
        self._ui.file_list.dragEnterEvent = self._file_list_drag_enter_event
        self._ui.file_list.dragMoveEvent = self._file_list_drag_enter_event
        self._ui.welcome_label.setAcceptDrops(True)
        self._ui.welcome_label.dropEvent = self._file_list_drop_event
        self._ui.welcome_label.dragEnterEvent = self._file_list_drag_enter_event
        self._ui.welcome_label.dragMoveEvent = self._file_list_drag_enter_event

        self._icons_info_ready.connect(self._on_icons_info_ready)
        self._init_timers()

    def is_changing(self):
        return self._file_list_changing

    def has_files(self):
        return bool(self._files)

    def clear(self, clear_ui=True):
        self._files.clear()
        if clear_ui:
            self._ui.file_list.clear()

    def set_file_list(self, file_list):
        self._file_list_changing = True

        if not file_list:
            self._ui.file_list_views.setCurrentWidget(self._ui.welcome_label)
            self._ui.file_list.clear()
            self._files.clear()
            self._service.file_list_ready()
            self._file_list_changing = False
            return

        new_list = set(
            [FilePath(rel_path)
             for (rel_path, is_dir, created_time, was_updated) in file_list])
        old_list = set(self._files.keys())
        removed = old_list.difference(new_list)
        added = new_list

        self._get_icons_info(file_list, added, removed)

    def share_path_failed(self, paths):
        self._shared_files.difference_update(paths)

    def on_share_changed(self, shared):
        self._shared_files = set(shared)

    def on_file_moved(self, old_file, new_file):
        for path in self._shared_files.copy():
            if FilePath(path) in FilePath(old_file) or \
                    path == old_file:
                new_path = str(FilePath(op.join(new_file, op.relpath(path, old_file))))
                self._shared_files.discard(path)
                self._shared_files.add(new_path)

    def _init_timers(self):
        self._update_time_delta_timer = QTimer(self)
        self._update_time_delta_timer.setInterval(60 * 1000)
        self._update_time_delta_timer.timeout.connect(
            self._update_time_deltas)
        self._update_time_delta_timer.start()

        self._check_shared_timer = QTimer(self)
        self._check_shared_timer.setInterval(2 * 1000)
        self._check_shared_timer.timeout.connect(
            self._check_shared)
        self._check_shared_timer.start()


    def _is_shared(self, path):
        if path in self._shared_files:
            return True
        for shared_path in self._shared_files:
            if FilePath(path) in FilePath(shared_path):
                return True

        return False

    def _on_icons_info_ready(self, icons_info, file_list, added, removed):
        logger.verbose("File list %s, files %s, icons info %s",
                       file_list, self._files, icons_info)

        for rel_path in removed:
            self._files.pop(rel_path, None)

        if added:
            for i, file in enumerate(file_list):
                rel_path, is_dir, created_time, was_updated = file
                if rel_path in added:
                    try:
                        self._add_file_to_file_list(
                            i, rel_path, created_time, was_updated,
                            icons_info[i])
                    except IndexError:
                        logger.error(
                            "Index error in file list. "
                            "Index %s, Icons info: %s.",
                            i, self._icons_info)

        for i in range(len(self._files), self.MAX_FILES):
            item = self._ui.file_list.takeItem(i)
            if not item:
                break

            del item

        if self._ui.file_list_views.currentWidget() == self._ui.welcome_label:
            self._ui.file_list_views.setCurrentWidget(self._ui.file_list)

        self._service.file_list_ready()
        self._file_list_changing = False

    def _add_file_to_file_list(self, index, rel_path,
                               created_time, was_updated, icon_info):
        item = self._ui.file_list.item(index)
        if not item:
            item = QListWidgetItem()
            item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            item.setSizeHint(QSize(
                self.FILE_LIST_ITEM_SIZE, self.FILE_LIST_ITEM_SIZE))
            self._ui.file_list.insertItem(index, item)

        widget = self._ui.file_list.itemWidget(item)
        if not widget:
            widget = self._create_file_list_item_widget(
                rel_path, created_time, was_updated, icon_info)
            self._ui.file_list.setItemWidget(item, widget)
        else:
            self._update_file_list_item_widget(
                widget, rel_path, created_time, was_updated, icon_info)
        self._files[FilePath(rel_path)] = icon_info

    def _file_list_drop_event(self, event):
        data = event.mimeData()
        if data.hasUrls():
            event.acceptProposedAction()
            event.accept()
            urls = data.urls()
            if len(urls) == 1 and not data.text().startswith("file:///"):
                link = data.text()
                logger.verbose("Link dropped: %s", link)
                self.received_download_link.emit(link, None)
            else:
                dropped_files_or_folders = []
                for url in urls:
                    dropped_files_or_folders.append(url.toLocalFile())
                self._service.add_to_sync_folder(dropped_files_or_folders)
        else:
            event.ignore()

    def _file_list_drag_enter_event(self, e):
        if e.mimeData().hasUrls():
            e.accept()
        else:
            e.ignore()

    def _create_file_list_item_widget(self, rel_path, created_time,
                                      was_updated, icon_info):
        abs_path = op.join(self._config.sync_directory, rel_path)
        abs_path = ensure_unicode(abs_path)
        abs_path = FilePath(abs_path).longpath
        widget = QWidget(parent=self._ui.file_list)
        widget.created_time = created_time
        widget.was_updated = was_updated

        widget.mouseReleaseEvent = lambda _: \
            qt_reveal_file_in_file_manager(
                widget.get_link_button.abs_path)

        main_layout = QHBoxLayout(widget)

        icon_label = QLabel(widget)
        widget.icon_label = icon_label
        main_layout.addWidget(icon_label)

        vertical_layout = QVBoxLayout()
        main_layout.addLayout(vertical_layout)

        file_name_label = QLabel(widget)
        widget.file_name_label = file_name_label
        vertical_layout.addWidget(file_name_label)

        horizontal_layout = QHBoxLayout()
        vertical_layout.addLayout(horizontal_layout)

        time_delta_label = QLabel(widget)
        widget.time_delta_label = time_delta_label
        horizontal_layout.addWidget(time_delta_label, alignment=Qt.AlignTop)

        get_link_button = QPushButton(widget)
        widget.get_link_button = get_link_button
        horizontal_layout.addWidget(get_link_button, alignment=Qt.AlignTop)

        self._set_icon_label(icon_info, icon_label)

        file_name_label.setFixedSize(304, 24)
        file_name_label.setFont(QFont('Noto Sans', 10 * self._dp))
        file_name_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        file_name_label.setText(
            elided(rel_path, file_name_label))

        time_delta_label.setText(get_added_time_string(
            created_time, was_updated, False))
        time_delta_label.setFont(QFont('Noto Sans', 8 * self._dp, italic=True))
        time_delta_label.setMinimumSize(time_delta_label.width(), 24)
        time_delta_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        time_delta_label.setStyleSheet('color: #A792A9;')

        get_link_button.setText('   {}  '.format(tr('Get link')))
        get_link_button.setFlat(True)
        get_link_button.setChecked(True)
        get_link_button.setMinimumSize(120, 24)
        get_link_button.setFont(QFont("Noto Sans", 8 * self._dp, italic=True))
        get_link_button.setMouseTracking(True)
        self._setup_get_link_button(get_link_button, rel_path, abs_path)

        return widget

    def _update_file_list_item_widget(self, widget, rel_path, created_time,
                                      was_updated, icon_info):
        abs_path = op.join(self._config.sync_directory, rel_path)
        abs_path = ensure_unicode(abs_path)
        abs_path = FilePath(abs_path).longpath

        widget.created_time = created_time
        widget.was_updated = was_updated
        widget.file_name_label.setText(
            elided(rel_path, widget.file_name_label))
        widget.time_delta_label.setText(get_added_time_string(
            created_time, was_updated, False))

        self._set_icon_label(icon_info, widget.icon_label)
        get_link_button = widget.get_link_button
        get_link_button.rel_path = rel_path
        get_link_button.abs_path = abs_path
        if self._is_shared(rel_path):
            if not get_link_button.icon():
                get_link_button.setIcon(QIcon(':images/getLink.png'))
        elif get_link_button.icon():
            get_link_button.setIcon(QIcon())

    def _set_icon_label(self, icon_info, icon_label):
        image, mime, file_info = icon_info
        pixmap = None
        if image:
            pixmap = QPixmap(self.FILE_LIST_ICON_SIZE,
                             self.FILE_LIST_ICON_SIZE)
            pixmap.convertFromImage(image)
        elif mime:
            icon = self._get_icon(mime)
            if icon:
                pixmap = icon.pixmap(
                    self.FILE_LIST_ICON_SIZE, self.FILE_LIST_ICON_SIZE)
        if not pixmap or pixmap.isNull():
            icon = QFileIconProvider().icon(file_info)
            pixmap = icon.pixmap(
                self.FILE_LIST_ICON_SIZE, self.FILE_LIST_ICON_SIZE)
        if pixmap and not pixmap.isNull():
            icon_label.setPixmap(pixmap)
        icon_label.setScaledContents(True)
        icon_label.setFixedSize(
            self.FILE_LIST_ICON_SIZE, self.FILE_LIST_ICON_SIZE)
        icon_label.setAlignment(Qt.AlignCenter)

    def _get_icon(self, mime):
        if not mime:
            return None

        if mime in self._mime_icons:
            return self._mime_icons[mime]

        for key, icon in self._mime_icons.items():
            if mime.startswith(key):
                return icon

        return None

    @qt_run
    def _get_icons_info(self, file_list, added, removed):
        icons_info = []
        try:
            for path, is_dir, created_time, was_updated in file_list:
                if path not in added:
                    icons_info.append((None, None, None))
                    continue

                elif path in self._files:
                    icons_info.append(self._files[path])
                    continue

                abs_path = op.join(self._config.sync_directory, path)
                abs_path = ensure_unicode(abs_path)
                abs_path = FilePath(abs_path)           # .longpath doesn't work
                mime, _ = mimetypes.guess_type(path)
                image = QImage(abs_path)
                if image.isNull():
                    image = None
                icons_info.append((image, mime, QFileInfo(abs_path)))
        except Exception as e:
            logger.error("Icons info error: %s", e)

        self._icons_info_ready.emit(
            icons_info, file_list, added, removed)

    def _setup_get_link_button(self, get_link_button, rel_path, abs_path):
        get_link_button.setStyleSheet(
            'margin: 0;border: 0; text-align:right center;'
            'color: #A792A9;')
        get_link_button.setCursor(Qt.PointingHandCursor)
        get_link_button.rel_path = rel_path
        get_link_button.abs_path = abs_path
        if self._is_shared(rel_path):
            get_link_button.setIcon(QIcon(':images/getLink.png'))
        else:
            get_link_button.setIcon(QIcon())

        def enter(_):
            get_link_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right center;'
                'color: #f9af61;')
            rel_path = get_link_button.rel_path
            if self._is_shared(rel_path):
                get_link_button.setIcon(
                    QIcon(':images/getLinkActive.png'))

        def leave(_):
            get_link_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right center;'
                'color: #A792A9;')
            rel_path = get_link_button.rel_path
            if self._is_shared(rel_path):
                get_link_button.setIcon(
                    QIcon(':images/getLink.png'))

        get_link_button.enterEvent = enter
        get_link_button.leaveEvent = leave

        def get_link_button_clicked():
            get_link_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right center;'
                'color: #f78d1e;')
            get_link_button.setIcon(
                QIcon(':images/getLinkClicked.png'))
            rel_path = get_link_button.rel_path
            self._shared_files.add(rel_path)
            abs_path = get_link_button.abs_path
            self._parent.share_path_requested.emit(abs_path)
        get_link_button.clicked.connect(get_link_button_clicked)

    def _build_mime_icons(self):
        icons = {
            'application/zip': 'archive.svg',
            'application/x-zip': 'archive.svg',
            'application/x-xz': 'archive.svg',
            'application/x-7z-compressed': 'archive.svg',
            'application/x-gzip': 'archive.svg',
            'application/x-tar': 'archive.svg',
            'application/x-bzip': 'archive.svg',
            'application/x-bzip2': 'archive.svg',
            'application/x-rar': 'archive.svg',
            'application/x-rar-compressed': 'archive.svg',
            'audio': 'music.svg',
            'video': 'video.svg',
            'application/vnd.oasis.opendocument.text': 'word.svg',
            'application/msword': 'word.svg',
            'application/vnd.oasis.opendocument.spreadsheet': 'excel.svg',
            'application/vnd.ms-excel': 'excel.svg',
        }

        result = {}
        for mime, icon_name in icons.items():
            result[mime] = QIcon(':/images/mime/' + icon_name)

        return result

    def _update_time_deltas(self):
        for row in range(len(self._files)):
            item = self._ui.file_list.item(row)
            if not item:
                break

            widget = self._ui.file_list.itemWidget(item)
            if not widget:
                break

            widget.time_delta_label.setText(get_added_time_string(
                widget.created_time, widget.was_updated, False))

    def _check_shared(self):
        for row in range(len(self._files)):
            item = self._ui.file_list.item(row)
            if not item:
                break

            widget = self._ui.file_list.itemWidget(item)
            if not widget:
                break

            get_link_button = widget.get_link_button
            if self._is_shared(get_link_button.rel_path):
                if not get_link_button.icon():
                    get_link_button.setIcon(QIcon(':images/getLink.png'))
            elif get_link_button.icon():
                get_link_button.setIcon(QIcon())
