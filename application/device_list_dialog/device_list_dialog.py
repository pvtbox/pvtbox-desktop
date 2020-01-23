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

from PySide2.QtCore import Qt, QObject, Signal, QTimer, QPoint
from PySide2.QtGui import QIcon, QFont
from PySide2.QtWidgets import QDialog, QFrame, QApplication, QMenu, \
    QScrollBar, QAbstractItemView, QToolTip

from device_list import Ui_Dialog
from .table_model import TableModel
from common.translator import tr
from application.utils import msgbox
from common.constants import SS_STATUS_SYNCED, FREE_LICENSE

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DeviceListDialog(QObject):
    show_tray_notification = Signal(str)
    management_action = Signal(str,     # action name
                               str,     # action type
                               str,     # node id
                               bool)    # is_itself
    start_transfers = Signal()
    _update = Signal(list)
    def __init__(self, parent=None, initial_data=(), disk_usage=0,
                 node_status=SS_STATUS_SYNCED, node_substatus=None,
                 dp=1, nodes_actions=(), license_type=None):
        QObject.__init__(self)
        self._update.connect(self._update_data, Qt.QueuedConnection)
        self._dialog = QDialog(parent)
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)
        self._ui.device_list_view.setFont(QFont('Nano', 10 * dp))
        self._license_type = license_type

        self._model = TableModel(disk_usage, node_status, node_substatus)
        QTimer.singleShot(100, lambda: self.update(initial_data))

        self._view = self._ui.device_list_view
        self._view.setModel(self._model)
        self._view.setSelectionMode(QAbstractItemView.NoSelection)

        self._ui.centralWidget.setFrameShape(QFrame.NoFrame)
        self._ui.centralWidget.setLineWidth(1)

        self._nodes_actions = nodes_actions

    def show(self, on_finished):
        def finished():
            self._dialog.finished.disconnect(finished)
            self._view.resizeRowsToContents()
            self._model.beginResetModel()
            on_finished()

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
        if width > screen_width - offset:
            self._dialog.resize(screen_width - offset, self._dialog.height())

        self._view.setMouseTracking(True)
        self._old_mouse_move_event = self._view.mouseMoveEvent
        self._view.mouseMoveEvent = self._mouse_moved
        self._old_mouse_release_event = self._view.mouseMoveEvent
        self._view.mouseReleaseEvent = self._mouse_released

        logger.info(
            "Opening device list dialog...")
        # Execute dialog
        self._dialog.finished.connect(finished)
        self._dialog.raise_()
        self._dialog.show()

    def update(self, nodes_info):
        self._update.emit(nodes_info)

    def _update_data(self, nodes_info):
        changed_nodes, deleted_nodes = self._model.update(nodes_info)
        for node_id in changed_nodes | deleted_nodes:
            self._nodes_actions.pop(node_id, None)
        self._view.resizeRowsToContents()

    def update_download_speed(self, value):
        self._model.update_node_download_speed(value)
        self._view.resizeRowsToContents()

    def update_upload_speed(self, value):
        self._model.update_node_upload_speed(value)
        self._view.resizeRowsToContents()

    def update_sync_dir_size(self, value):
        self._model.update_node_sync_dir_size(int(value))
        self._view.resizeRowsToContents()

    def update_node_status(self, value, substatus):
        self._model.update_node_status(value, substatus)
        self._view.resizeRowsToContents()

    def close(self):
        self._dialog.reject()

    def set_license_type(self, license_type):
        self._license_type = license_type

    def _mouse_moved(self, event):
        pos = event.pos()
        index = self._view.indexAt(pos)
        if index.isValid():
            if self._model.to_manage(index) and \
                    not self._pos_is_in_scrollbar_header(pos):
                self._view.setCursor(Qt.PointingHandCursor)
            else:
                self._view.setCursor(Qt.ArrowCursor)
        else:
            self._view.setCursor(Qt.ArrowCursor)
        self._old_mouse_move_event(event)

    def _mouse_released(self, event):
        pos = event.pos()
        index = self._view.indexAt(pos)
        if index.isValid():
            if self._model.to_manage(index) and \
                    not self._pos_is_in_scrollbar_header(pos):
                self._show_menu(index, pos)
        self._old_mouse_release_event(event)

    def _pos_is_in_scrollbar_header(self, pos):
        # mouse is not tracked as in view when in header or scrollbar area
        # so pretend we are there if we are near
        pos_in_header = pos.y() < 10
        if pos_in_header:
            return True

        scrollbars = self._view.findChildren(QScrollBar)
        if not scrollbars:
            return False

        pos_x = self._view.mapToGlobal(pos).x()
        for scrollbar in scrollbars:
            if not scrollbar.isVisible():
                continue

            scrollbar_x = scrollbar.mapToGlobal(QPoint(0, 0)).x()
            if scrollbar_x - 10 <= pos_x <= scrollbar_x + scrollbar.width():
                return True

        return False

    def _show_menu(self, index, pos):
        node_id, \
        node_name, \
        is_online, \
        is_itself, \
        is_wiped = self._model.get_node_id_online_itself(index)
        if not node_id:
            return

        license_free = self._license_type == FREE_LICENSE

        menu = QMenu(self._view)
        menu.setStyleSheet("background-color: #EFEFF4; ")
        menu.setToolTipsVisible(license_free)
        if license_free:
            menu.setStyleSheet(
                'QToolTip {{background-color: #222222; color: white;}}')
            menu.hovered.connect(lambda a: self._on_menu_hovered(a, menu))


        def add_menu_item(caption, index=None, action_name=None,
                          action_type="", start_transfers=False,
                          disabled=False, tooltip=""):
            action = menu.addAction(caption)
            action.setEnabled(not disabled)
            action.tooltip = tooltip if tooltip else ""
            if not start_transfers:
                    action.triggered.connect(lambda: self._on_menu_clicked(
                        index, action_name, action_type))
            else:
                action.triggered.connect(self.start_transfers.emit)

        tooltip = tr("Not available for free license") \
            if license_free and not is_itself else ""
        if not is_online:
            action_in_progress = ("hideNode", "") in \
                                 self._nodes_actions.get(node_id, set())
            item_text = tr("Remove node") if not action_in_progress \
                else tr("Remove node in progress...")
            add_menu_item(item_text, index, "hideNode",
                          disabled=action_in_progress)
        elif is_itself:
            add_menu_item(tr("Transfers..."), start_transfers=True)
        if not is_wiped:
            wipe_in_progress = ("execute_remote_action", "wipe") in \
                                 self._nodes_actions.get(node_id, set())
            if not wipe_in_progress:
                action_in_progress = ("execute_remote_action", "logout") in \
                                     self._nodes_actions.get(node_id, set())
                item_text = tr("Log out") if not action_in_progress \
                    else tr("Log out in progress...")
                add_menu_item(item_text, index,
                              "execute_remote_action", "logout",
                              disabled=action_in_progress or
                                       license_free and not is_itself,
                              tooltip=tooltip)
            item_text = tr("Log out && wipe") if not wipe_in_progress \
                else tr("Wipe in progress...")
            add_menu_item(item_text, index,
                          "execute_remote_action", "wipe",
                          disabled=wipe_in_progress or
                                   license_free and not is_itself,
                          tooltip=tooltip)

        pos_to_show = QPoint(pos.x(), pos.y() + 20)
        menu.exec_(self._view.mapToGlobal(pos_to_show))

    def _on_menu_clicked(self, index, action_name, action_type):
        node_id, \
        node_name, \
        is_online, \
        is_itself, \
        is_wiped = self._model.get_node_id_online_itself(index)
        if action_name == "hideNode" and is_online:
            self.show_tray_notification.emit(
                tr("Action unavailable for online node"))
            return

        if (action_name == "hideNode" or action_type == "wipe"):
            if action_name == "hideNode":
                alert_str = tr('"{}" node will be removed '
                               'from list of devices. Files will not be wiped.'
                               .format(node_name))
            else:
                alert_str = tr('All files from "{}" node\'s '
                               'pvtbox secured folder will be wiped. '
                               .format(node_name))
            if not self._user_confirmed_action(alert_str):
                return

        if not is_itself:
            self._nodes_actions[node_id].add((action_name, action_type))
        self.management_action.emit(
            action_name, action_type, node_id, is_itself)

    def _on_menu_hovered(self, action, menu):
        if not action.tooltip:
            return

        a_geometry = menu.actionGeometry(action)
        point = menu.mapToGlobal(
            QPoint(a_geometry.x() + 30, a_geometry.y() + 5))
        QToolTip.showText(
            point, action.tooltip, menu, a_geometry, 60 * 60 * 1000)

    def _user_confirmed_action(self, alert_str):
        msg = tr("<b>Are</b> you <b>sure</b>?<br><br>{}". format(alert_str))
        userAnswer = msgbox(
            msg,
            title=' ',
            buttons=[(tr('Cancel'), 'Cancel'),
                     (tr('Yes'), 'Yes'),],
            parent=self._dialog,
            default_index=0,
            enable_close_button=True)

        return userAnswer == 'Yes'

    def on_management_action_in_progress(self, action_name, action_type,
                                         node_id):
        self._nodes_actions[node_id].add((action_name, action_type))
