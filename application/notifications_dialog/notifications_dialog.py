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

from PySide2.QtCore import Qt
from PySide2.QtGui import QIcon, QFont, QMovie
from PySide2.QtWidgets import QDialog, QLabel, QWidget, \
    QVBoxLayout, QApplication
from PySide2.QtSvg import QSvgWidget

from notifications import Ui_Dialog

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class NotificationsList(QWidget):
    READ_BACKGROUND_COLOR = "white"
    UNREAD_BACKGROUND_COLOR = "#eeeeee"
    WIDGET_BACKGROUND_COLOR = "darkGray"

    def __init__(self, dp, *args, **kwargs):
        QWidget.__init__(self, *args, **kwargs)

        self._dp = dp
        self._items = []
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setSpacing(2)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.addStretch()
        self.setStyleSheet('background-color: {};'.format(
            self.READ_BACKGROUND_COLOR))

    def show_notifications(self, notifications):
        items_count = len(self._items)
        for i, notification in enumerate(notifications):
            if i < items_count:
                self._update_n_list_item_widget(notification ,self._items[i])
            else:
                self._create_n_list_item_widget(notification)

        for i in range(items_count - 1, len(notifications) - 1, -1):
            self._main_layout.removeWidget(self._items[i])
            self._items.pop()
        self.setStyleSheet('background-color: {};'.format(
            self.WIDGET_BACKGROUND_COLOR))

    def loading_needed(self, limit):
        items_len = len(self._items)
        if items_len < limit:
            return True

        for widget in self._items[-limit:]:
            if not widget.visibleRegion().isEmpty():
                return True

        return False

    def _create_n_list_item_widget(self, notification):
        widget = QWidget(parent=self)
        # widget.setFixedWidth(self.width())
        widget.notification = notification

        main_layout = QVBoxLayout(widget)
        main_layout.setSpacing(2)

        text_label = QLabel(widget)
        widget.text_label = text_label
        text_label.setWordWrap(True)
        text_label.setFont(QFont('Noto Sans', 10 * self._dp))
        text_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        text_label.setText(notification.get_text())
        main_layout.addWidget(text_label)

        time_label = QLabel(widget)
        widget.time_label = time_label
        time_label.setFont(QFont('Noto Sans', 8 * self._dp))
        time_label.setAlignment(Qt.AlignTop | Qt.AlignRight)
        time_label.setText(notification.get_datetime())
        main_layout.addWidget(time_label)

        self._set_background_color(widget, notification)

        def clicked(_):
            widget.notification.read()
            self._set_background_color(
                widget, widget.notification)

        widget.mouseReleaseEvent = clicked
        widget.text_label.mouseReleaseEvent = clicked
        widget.time_label.mouseReleaseEvent = clicked
        self._main_layout.insertWidget(len(self._items), widget)
        self._items.append(widget)

    def _update_n_list_item_widget(self, notification, widget):
        widget.notification = notification
        widget.text_label.setText(notification.get_text())
        widget.time_label.setText(notification.get_datetime())
        self._set_background_color(widget, notification)

    def _set_background_color(self, widget, notification):
        background_color = self.READ_BACKGROUND_COLOR \
            if notification.is_read \
            else self.UNREAD_BACKGROUND_COLOR
        widget.setStyleSheet('background-color: {};'.format(background_color))


class NotificationsDialog(object):

    def __init__(self, parent, parent_window, notifications, dp=None):
        self._dialog = QDialog(parent_window)
        self._dp = dp

        self._notifications = notifications
        self._parent = parent
        self._parent_window = parent_window

        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._init_ui()

    def _init_ui(self):
        self._dialog.setWindowFlags(Qt.Dialog)
        self._dialog.setAttribute(Qt.WA_TranslucentBackground)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)

        self._notifications_list = NotificationsList(self._dp)
        self._ui.notifications_area.setWidget(self._notifications_list)
        self._ui.notifications_area.verticalScrollBar().valueChanged.connect(
            self._on_list_scroll_changed)

        self._old_main_resize_event = self._ui.centralwidget.resizeEvent
        self._ui.centralwidget.resizeEvent = self._main_resize_event

        self._loader_movie = QMovie(":/images/loader.gif")
        self._ui.loader_label.setMovie(self._loader_movie)

    def show(self, on_finished):
        def finished():
            self.show_cursor_normal()
            self._dialog.finished.disconnect(finished)
            on_finished()

        logger.debug("Opening notifications dialog")

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

        # Execute dialog
        self._dialog.finished.connect(finished)
        if not self._parent.load_notifications(show_loading=True):
            self.show_notifications()
        self._dialog.raise_()
        self._dialog.show()

    def raise_dialog(self):
        self._dialog.raise_()

    def close(self):
        self._dialog.reject()

    def show_cursor_loading(self, show_movie=False):
        if show_movie:
            self._ui.notifications_pages.setCurrentIndex(2)
            self._loader_movie.start()
        else:
            self._dialog.setCursor(Qt.WaitCursor)
            self._parent_window.setCursor(Qt.WaitCursor)

    def show_cursor_normal(self):
        self._dialog.setCursor(Qt.ArrowCursor)
        self._parent_window.setCursor(Qt.ArrowCursor)
        if self._loader_movie.state() == QMovie.Running:
            self._loader_movie.stop()

    def show_notifications(self):
        if not self._notifications:
            self._ui.notifications_pages.setCurrentIndex(1)
        else:
            self._ui.notifications_pages.setCurrentIndex(0)
            self._notifications_list.show_notifications(self._notifications)

        self.show_cursor_normal()

    def _on_list_scroll_changed(self, *args, **kwargs):
        # value = self._ui.notifications_area.verticalScrollBar().value()
        # logger.debug("Scroll value %s", value)
        if self._parent.all_loaded or self._parent.is_querying:
            return

        if self._notifications_list.loading_needed(self._parent.limit):
            logger.debug("Loading notifications")
            self._parent.load_notifications()

    def _main_resize_event(self, e):
        self._old_main_resize_event(e)
        self._notifications_list.setFixedWidth(
            self._ui.notifications_pages.width() - 8)

        self._on_list_scroll_changed()
