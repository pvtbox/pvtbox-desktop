# -*- coding: utf-8 -*-#
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
from PySide2.QtCore import QObject, QEvent, Qt, QTimer
from PySide2.QtCore import Signal as pyqtSignal
from PySide2.QtGui import QIcon
from PySide2.QtWidgets import QSystemTrayIcon, QMessageBox

from common.constants import STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK, \
    STATUS_INIT, STATUS_DISCONNECTED, STATUS_INDEXING, SUBSTATUS_SHARE
from common.utils import ensure_unicode, get_platform
from common.translator import tr

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SystemTrayIcon(QObject):
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()

    def __init__(self, parent, menu, is_logging=False):
        QObject.__init__(self)

        def getIcon(name):
            return QIcon(':/images/tray_icons/' + name + '.png')

        self._icons = {
            STATUS_INIT: getIcon("disconnected") if is_logging \
                else getIcon("sync"),
            STATUS_DISCONNECTED: getIcon("disconnected"),
            STATUS_WAIT: getIcon("default"),
            STATUS_PAUSE: getIcon("pause"),
            STATUS_IN_WORK: getIcon("sync"),
            STATUS_INDEXING: getIcon("sync"),
        }
        self._statuses = {
            STATUS_INIT: tr("Pvtbox"),
            STATUS_DISCONNECTED: tr("Pvtbox connecting..."),
            STATUS_WAIT: tr("Pvtbox"),
            STATUS_PAUSE: tr("Pvtbox paused"),
            STATUS_IN_WORK: tr("Pvtbox syncing..."),
            STATUS_INDEXING: tr("Pvtbox indexing...")
        }
        self._tray = QSystemTrayIcon(self._icons[STATUS_INIT], parent)
        self.set_tool_tip(self._statuses[STATUS_INIT])
        self._tray.setContextMenu(menu)
        menu.aboutToShow.connect(self.clicked.emit)

        self._tray.activated.connect(self._on_activated)
        self._tray.installEventFilter(self)
        self._tray.setVisible(True)
        self._tray.show()

        self._tray_show_timer = QTimer(self)
        self._tray_show_timer.setInterval(3000)
        self._tray_show_timer.setSingleShot(False)
        self._tray_show_timer.timeout.connect(self.show)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.ToolTip:
            self.clicked.emit()
        return False

    def _on_activated(self, reason):
        '''
        Slot for system tray icon activated signal.
        See http://doc.qt.io/qt-5/qsystemtrayicon.html

        @param reason Tray activation reason
        '''

        if reason == QSystemTrayIcon.Trigger:
            # This is usually when left mouse button clicked on tray icon
            self.clicked.emit()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.double_clicked.emit()

    @property
    def menu(self):
        return self._tray.contextMenu()

    def set_tool_tip(self, tip):
        self._tray.setToolTip(tip)

    def show_tray_notification(self, text, title=""):
        if not title:
            title = tr('Pvtbox')
        # Convert strings to unicode
        if type(text) in (str, str):
            text = ensure_unicode(text)
        if type(title) in (str, str):
            title = ensure_unicode(title)

        logger.info("show_tray_notification: %s, title: %s", text, title)
        if self._tray.supportsMessages():
            self._tray.showMessage(title, text)
        else:
            logger.warning("tray does not supports messages")

    def request_to_user(
            self, dialog_id, text, buttons=("Yes", "No"), title="",
            close_button_index=-1, close_button_off=False, parent=None,
            on_clicked_cb=None,
            details=''):

        msg_box = QMessageBox(parent)
        # msg_box = QMessageBox()
        if not title:
            title = tr('Pvtbox')
        msg_box.setWindowTitle(title)
        msg_box.setText(str(text))

        pvtboxIcon = QIcon(':/images/icon.png')
        msg_box.setWindowIcon(pvtboxIcon)

        if details:
            msg_box.setDetailedText(details)

        if close_button_off:
            if get_platform() == 'Darwin':
                msg_box.setWindowFlags(Qt.Tool)
            else:
                msg_box.setWindowFlags(Qt.Dialog |
                                       Qt.CustomizeWindowHint |
                                       Qt.WindowTitleHint)
        msg_box.setAttribute(Qt.WA_MacFrameworkScaled)
        msg_box.setModal(True)

        buttons = list(buttons)
        for button in buttons:
            msg_box.addButton(button, QMessageBox.ActionRole)
        msg_box.show()
        msg_box.raise_()
        msg_box.exec_()
        try:
            button_index = buttons.index(msg_box.clickedButton().text())
        except (ValueError, AttributeError):
            button_index = -1
           # message box was closed with close button
            if len(buttons) == 1 and close_button_index == -1:
                # if only one button then call callback
                close_button_index = 0

            if len(buttons) > close_button_index >= 0:
                button_index = close_button_index

        if button_index >= 0 and callable(on_clicked_cb):
            on_clicked_cb(dialog_id, button_index)

    def update_status_icon(self, new_status, new_substatus):
        icon = self._icons[STATUS_DISCONNECTED] if new_status == STATUS_INIT \
            else self._icons[new_status]
        self._tray.setIcon(icon)
        tool_tip = self._statuses[new_status]
        if new_status == STATUS_IN_WORK and new_substatus == SUBSTATUS_SHARE:
            tool_tip = tr("Pvtbox downloading share...")
        self.set_tool_tip(tool_tip)
        self.show()

    def show(self):
        self._tray.setVisible(True)
        self._tray.show()

    def hide(self):
        if self._tray_show_timer.isActive():
            self._tray_show_timer.stop()
        self._tray.hide()

    def __del__(self):
        self._tray.removeEventFilter(self)
        self._tray.activated.disconnect()
        self.hide()
