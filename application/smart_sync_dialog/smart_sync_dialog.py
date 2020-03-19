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

from PySide2.QtGui import QIcon, QMovie
from PySide2.QtCore import Qt, QSortFilterProxyModel
from PySide2.QtWidgets import QDialog, QFrame

from smart_sync import Ui_Dialog
from .tree_model import TreeModel

from .params import LOGGING_ENABLED

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SmartSyncDialog(object):
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

        self._offline_paths = None

        # for frameless window moving
        self._x_coord = 0
        self._y_coord = 0
        self._dialog.mousePressEvent = self.on_mouse_press_event
        self._dialog.mouseMoveEvent = self.on_mouse_move_event

        self._loader_movie = QMovie(":/images/loader.gif")
        self._ui.loader_label.setMovie(self._loader_movie)

    def on_mouse_press_event(self, ev):
        self._x_coord = ev.x()
        self._y_coord = ev.y()

    def on_mouse_move_event(self, ev):
        self._dialog.move(
            ev.globalX() - self._x_coord, ev.globalY() - self._y_coord)

    def on_item_expanded(self, index):
        if self._model:
            self._model.on_item_expanded(self._proxy_model.mapToSource(index))

    def show(self, root_path, hide_dotted=False):
        if LOGGING_ENABLED:
            logger.info(
                "Opening smart sync dialog for path '%s'...", root_path)

        self._model = TreeModel(
            root_path, hide_dotted=hide_dotted)

        self._proxy_model.setSourceModel(self._model)

        self.show_cursor_loading(show_movie=True)
        # Execute dialog
        result = self._dialog.exec_()
        if result == QDialog.Accepted:
            offline_dirs = self._model.get_added_to_offline_paths()
            new_online = list(self._model.get_removed_from_offline_paths())
            new_offline = list(offline_dirs - self._offline_paths)
            if LOGGING_ENABLED:
                logger.debug("new offline dirs %s, new online dirs %s",
                             new_offline, new_online)
            return new_offline, new_online
        else:
            return [], []

    def set_offline_paths(self, offline_paths):
        self._offline_paths = offline_paths
        logger.debug("offline paths %s", offline_paths)
        self._model.set_offline_dirs(offline_paths)
        self.show_cursor_normal()

        self._view.expand(self._proxy_model.mapFromSource(
            self._model.get_root_path_index()))
        self._proxy_model.sort(0, Qt.AscendingOrder)


    def show_cursor_loading(self, show_movie=False):
        if show_movie:
            self._ui.stackedWidget.setCurrentIndex(1)
            self._loader_movie.start()
        else:
            self._dialog.setCursor(Qt.WaitCursor)

    def show_cursor_normal(self):
        self._dialog.setCursor(Qt.ArrowCursor)
        if self._loader_movie.state() == QMovie.Running:
            self._loader_movie.stop()
        self._ui.stackedWidget.setCurrentIndex(0)



