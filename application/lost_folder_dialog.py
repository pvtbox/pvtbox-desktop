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
from os import path as op

from PySide2.QtCore import Qt
from PySide2.QtGui import QIcon
from PySide2.QtWidgets import QDialog


import lost_folder_dialog


class LostFolderDialog(object):
    def __init__(self, parent, path, restoreFolder, dialog_id=0):
        super(LostFolderDialog, self).__init__()
        self._path = path
        self._restoreFolder = restoreFolder
        self._dialog_id = dialog_id

        self._dialog = QDialog(
            parent,
            Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = lost_folder_dialog.Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._ui.textLabel.setText(
            self._ui.textLabel.text().replace('{PATH}', path))

        self._connect_slots()

    def _connect_slots(self):
        ui = self._ui
        ui.tryAgainButton.clicked.connect(self._on_tryAgain)
        ui.restoreFolderButton.clicked.connect(self._on_restoreFolder)

    def show(self):
        self._dialog.raise_()
        self._dialog.exec_()

    def _on_tryAgain(self):
        if op.isdir(self._path):
            self._dialog.accept()

    def _on_restoreFolder(self):
        self._dialog.accept()
        self._restoreFolder(self._dialog_id, 0)
