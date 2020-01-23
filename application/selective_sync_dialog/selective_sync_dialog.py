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

from PySide2.QtGui import QIcon
from PySide2.QtCore import Qt, QSortFilterProxyModel
from PySide2.QtWidgets import QDialog, QFrame

from selective_sync import Ui_Dialog
from .tree_model import TreeModel

from .params import LOGGING_ENABLED

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SelectiveSyncDialog(object):
    def __init__(self, parent):
        self._dialog = QDialog(parent)
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._ui.centralWidget.setFrameShape(QFrame.NoFrame)
        self._ui.centralWidget.setLineWidth(1)

        self._model = None
        self._proxy_model = QSortFilterProxyModel()

        self._view = self._ui.folder_list_view
        self._view.setModel(self._proxy_model)
        self._view.expanded.connect(self.on_item_expanded)

        # for frameless window moving
        self._x_coord = 0
        self._y_coord = 0
        self._dialog.mousePressEvent = self.on_mouse_press_event
        self._dialog.mouseMoveEvent = self.on_mouse_move_event

    def on_mouse_press_event(self, ev):
        self._x_coord = ev.x()
        self._y_coord = ev.y()

    def on_mouse_move_event(self, ev):
        self._dialog.move(
            ev.globalX() - self._x_coord, ev.globalY() - self._y_coord)

    def on_item_expanded(self, index):
        if self._model:
            self._model.on_item_expanded(self._proxy_model.mapToSource(index))

    def show(self, root_path, excluded_dirs=(), hide_dotted=False):
        if LOGGING_ENABLED:
            logger.info(
                "Opening selective sync dialog for path '%s'...", root_path)

        self._model = TreeModel(
            root_path, excluded_dirs=excluded_dirs, hide_dotted=hide_dotted)

        self._proxy_model.setSourceModel(self._model)

        self._view.expand(self._proxy_model.mapFromSource(
            self._model.get_root_path_index()))
        self._proxy_model.sort(0, Qt.AscendingOrder)

        # Execute dialog
        result = self._dialog.exec_()
        if result == QDialog.Accepted:
            excluded_dirs = self._model.get_unchecked_paths()
            return excluded_dirs
