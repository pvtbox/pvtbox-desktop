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
import re

from PySide2.QtCore import Qt, QObject, QSize, QPoint
from PySide2.QtGui import QIcon, QFont, QMovie
from PySide2.QtWidgets import QDialog, QLabel, QWidget, \
    QVBoxLayout, QHBoxLayout, QApplication, QListWidgetItem, \
    QMenu, QActionGroup

from collaborations import Ui_Dialog
from common.translator import tr
from application.utils import msgbox

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ColleaguesList(QObject):
    COLLEAGUES_LIST_WIDGET_HEIGHT = 60

    def __init__(self, parent, colleagues_list, dp, show_menu):
        QObject.__init__(self, parent)

        self._dp = dp
        self._colleagues_list = colleagues_list
        self._show_menu = show_menu
        self._items = []

    def show_colleagues(self, colleagues):
        items_count = len(self._items)
        for i, colleague in enumerate(colleagues):
            if i < items_count:
                self._update_c_list_item_widget(colleague, self._items[i])
            else:
                self._create_c_list_item_widget(colleague)

        for i in range(items_count - 1, len(colleagues) - 1, -1):
            item = self._colleagues_list.takeItem(i)
            if item:
                del item
            self._items.pop()

    def _create_c_list_item_widget(self, colleague):
        widget = QWidget(parent=self._colleagues_list)
        widget.colleague = colleague

        main_layout = QHBoxLayout(widget)
        main_layout.setSpacing(2)

        icon_label = QLabel(widget)
        icon_label.setPixmap(QIcon(":images/account.svg").pixmap(
            QSize(self.COLLEAGUES_LIST_WIDGET_HEIGHT - 17,
                  self.COLLEAGUES_LIST_WIDGET_HEIGHT - 17)))
        main_layout.addWidget(icon_label)

        vertical_layout = QVBoxLayout()
        main_layout.addLayout(vertical_layout)

        text_label = QLabel(widget)
        widget.text_label = text_label
        text_label.setFont(QFont('Noto Sans', 10 * self._dp))
        text_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        text_label.setText(colleague.get_mail_text())
        vertical_layout.addWidget(text_label)

        status_label = QLabel(widget)
        widget.status_label = status_label
        status_label.setFont(QFont('Noto Sans', 10 * self._dp))
        status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        status_label.setText(colleague.get_status_text())
        vertical_layout.addWidget(status_label)

        main_layout.addStretch()
        access_label = QLabel(widget)
        widget.access_label = access_label
        access_label.setFont(QFont('Noto Sans', 10 * self._dp))
        access_label.setAlignment(Qt.AlignCenter | Qt.AlignLeft)
        access_label.setText(colleague.get_access_text())
        main_layout.addWidget(access_label)

        def clicked(event, control):
            self._show_menu(widget.colleague, control.mapToGlobal(event.pos()))

        widget.mouseReleaseEvent = lambda e: clicked(e, widget)
        widget.text_label.mouseReleaseEvent = lambda e: clicked(
            e, widget.text_label)
        widget.status_label.mouseReleaseEvent = lambda e: clicked(
            e, widget.status_label)
        widget.access_label.mouseReleaseEvent = lambda e: clicked(
            e, widget.access_label)

        item = QListWidgetItem()
        item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
        item.setSizeHint(QSize(
            self._colleagues_list.width(), self.COLLEAGUES_LIST_WIDGET_HEIGHT))

        self._colleagues_list.addItem(item)
        self._items.append(widget)
        self._colleagues_list.setItemWidget(item, widget)

    def _update_c_list_item_widget(self, colleague, widget):
        widget.colleague = colleague
        widget.text_label.setText(colleague.get_mail_text())
        widget.status_label.setText(colleague.get_status_text())
        widget.access_label.setText(colleague.get_access_text())


class CollaborationSettingsDialog(object):
    ADD_BUTTON_ACTIVE_COLOR = "#f78d1e"
    ADD_BUTTON_PASSIVE_COLOR = "#9a9a9a"
    ERROR_COLOR = '#FF9999'
    LINE_EDIT_NORMAL_COLOR = "#EFEFF1"

    def __init__(self, parent, parent_window, colleagues, folder, dp):
        self._dialog = QDialog(parent_window)
        self._dp = dp
        self._colleagues = colleagues
        self._parent = parent
        self._parent_window = parent_window
        self._folder = folder

        self._is_owner = False
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._init_ui()

    def _init_ui(self):
        self._dialog.setWindowFlags(Qt.Dialog)
        self._dialog.setAttribute(Qt.WA_TranslucentBackground)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)
        self._dialog.setWindowTitle(
            self._dialog.windowTitle() + self._folder)

        self._ui.colleagues_list.setAlternatingRowColors(True)
        self._colleagues_list = ColleaguesList(
            self._parent, self._ui.colleagues_list, self._dp, self._show_menu)

        self._loader_movie = QMovie(":/images/loader.gif")
        self._ui.loader_label.setMovie(self._loader_movie)
        self._set_fonts()

        self._ui.add_frame.setVisible(False)
        self._set_add_button_background(self.ADD_BUTTON_PASSIVE_COLOR)
        self._ui.add_button.clicked.connect(
            self._on_add_button_clicked)
        self._ui.add_button.setVisible(False)
        self._ui.close_button.clicked.connect(
            self._on_close_button_clicked)
        self._ui.refresh_button.clicked.connect(self._on_refresh)

        self._line_edit_style = "background-color: {};"
        self._ui.error_label.setStyleSheet(
            "color: {};".format(self.ERROR_COLOR))

    def _set_fonts(self):
        ui = self._ui
        controls = [ui.colleagues_label, ui.mail_edit,
            ui.edit_radio, ui.view_radio, ui.add_button]

        for control in controls:
            font = control.font()
            font_size = control.font().pointSize() * self._dp
            if font_size > 0:
                control_font = QFont(font.family(), font_size)
                control_font.setBold(font.bold())
                control.setFont(control_font)

    def show(self):
        logger.debug("Opening collaboration settings dialog")

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
        self._dialog.raise_()
        self.show_cursor_loading(True)
        self._dialog.exec_()

    def close(self):
        self._dialog.reject()

    def show_cursor_loading(self, show_movie=False):
        if show_movie:
            self._ui.stackedWidget.setCurrentIndex(1)
            self._loader_movie.start()
        else:
            self._dialog.setCursor(Qt.WaitCursor)
            self._parent_window.setCursor(Qt.WaitCursor)

    def show_cursor_normal(self):
        self._dialog.setCursor(Qt.ArrowCursor)
        self._parent_window.setCursor(Qt.ArrowCursor)
        if self._loader_movie.state() == QMovie.Running:
            self._loader_movie.stop()

    def show_colleagues(self):
        if not self._colleagues:
            self._ui.stackedWidget.setCurrentIndex(2)
        else:
            self._ui.stackedWidget.setCurrentIndex(0)
            self._colleagues_list.show_colleagues(self._colleagues)
        self.show_cursor_normal()

    def set_owner(self, is_owner):
        self._is_owner = is_owner
        self._ui.add_button.setVisible(self._is_owner)

    def _on_add_button_clicked(self):
        if self._ui.add_frame.isVisible():
            if not self._validate_email():
                return

            to_edit = self._ui.edit_radio.isChecked()
            self._ui.add_frame.setVisible(False)
            self._set_add_button_background(self.ADD_BUTTON_PASSIVE_COLOR)
            self._parent.add_colleague(self._ui.mail_edit.text(), to_edit)
        else:
            self._ui.add_frame.setVisible(True)
            self._set_add_button_background(self.ADD_BUTTON_ACTIVE_COLOR)
            self._ui.mail_edit.setText("")


    def _set_add_button_background(self, color):
        self._ui.add_button.setStyleSheet(
            'background-color: {}; color: #fff; '
            'border-radius: 4px; font: bold "Gargi"'.format(color))

    def _on_close_button_clicked(self):
        self._ui.add_frame.setVisible(False)
        self._set_add_button_background(self.ADD_BUTTON_PASSIVE_COLOR)
        self._clear_error()
        self._ui.mail_edit.setText("")

    def _validate_email(self):
        email_control = self._ui.mail_edit
        email_control.setStyleSheet(
            self._line_edit_style.format(self.LINE_EDIT_NORMAL_COLOR))
        regex = '^.+@.{2,}$'

        email_control.setText(email_control.text().strip())
        if not re.match(regex, email_control.text()):
            self._ui.error_label.setText(tr("Please enter a valid e-mail"))
            email_control.setStyleSheet(
                self._line_edit_style.format(self.ERROR_COLOR))
            email_control.setFocus()
            return False

        self._clear_error()
        return True

    def _clear_error(self):
        self._ui.error_label.setText("")
        self._ui.mail_edit.setStyleSheet(
            self._line_edit_style.format(self.LINE_EDIT_NORMAL_COLOR))

    def _on_refresh(self):
        self.show_cursor_loading()
        self._parent.query_collaboration_info()

    def _show_menu(self, colleague, pos):
        if not self._is_owner and not colleague.is_you or colleague.is_deleting:
            return

        menu = QMenu(self._ui.colleagues_list)
        menu.setStyleSheet("background-color: #EFEFF4; ")
        if colleague.is_you:
            if colleague.is_owner:
                action = menu.addAction(tr("Quit collaboration"))
                action.triggered.connect(self._on_quit_collaboration)
            else:
                action = menu.addAction(tr("Leave collaboration"))
                action.triggered.connect(self._on_leave_collaboration)
        else:
            rights_group = QActionGroup(menu)
            rights_group.setExclusive(True)

            menu.addSection(tr("Access rights"))
            action = menu.addAction(tr("Can view"))
            action.setCheckable(True)
            rights_action = rights_group.addAction(action)
            rights_action.setData(False)
            rights_action.setChecked(not colleague.can_edit)
            action = menu.addAction(tr("Can edit"))
            action.setCheckable(True)
            rights_action = rights_group.addAction(action)
            rights_action.setChecked(colleague.can_edit)
            rights_action.setData(True)
            rights_group.triggered.connect(
                lambda a: self._on_grant_edit(colleague, a))
            menu.addSeparator()

            action = menu.addAction(tr("Remove user"))
            action.triggered.connect(
                lambda: self._on_remove_user(colleague))

        pos_to_show = QPoint(pos.x(), pos.y() + 10)
        menu.exec_(pos_to_show)

    def _on_quit_collaboration(self):
        alert_str = "Collaboration will be cancelled, " \
                    "collaboration folder will be deleted " \
                    "from all colleagues' Pvtbox secured sync folders " \
                    "on all nodes."
        if self._user_confirmed_action(alert_str):
            self._parent.cancel_collaboration()

    def _on_leave_collaboration(self):
        alert_str = "Collaboration folder will be deleted " \
                    "from Pvtbox secured sync folders " \
                    "on all your nodes."
        if self._user_confirmed_action(alert_str):
            self._parent.leave_collaboration()

    def _on_remove_user(self, colleague):
        alert_str = "Colleague {} will be removed from collaboration. " \
                    "Collaboration folder will be deleted from colleague's " \
                    "Pvtbox secured sync folders on all nodes." \
            .format(colleague.email)
        if self._user_confirmed_action(alert_str):
            self._parent.remove(colleague.id)

    def _on_grant_edit(self, colleague, action):
        to_edit = action.data()
        self._parent.grant_edit(colleague.id, to_edit)

    def _user_confirmed_action(self, alert_str):
        msg = tr("<b>Are</b> you <b>sure</b>?<br><br>{}". format(alert_str))
        user_answer = msgbox(
            msg,
            title=' ',
            buttons=[(tr('Cancel'), 'Cancel'),
                     (tr('Yes'), 'Yes'),],
            parent=self._dialog,
            default_index=0,
            enable_close_button=True)

        return user_answer == 'Yes'
