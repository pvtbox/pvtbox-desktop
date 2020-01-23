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
from PySide2.QtGui import QIcon, QFont
from PySide2.QtWidgets import QDialog, QLabel

from tutorial import Ui_Dialog
from application.tutorial_dialog.slide_show import SlideShow
from common.translator import tr

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class TutorialDialog(object):
    def __init__(self, parent, dp=None):
        self._dialog = QDialog(parent)
        self._dp = dp
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        self._current_index = 0
        self._ui.slides.setCurrentIndex(self._current_index)
        self._slides_count = self._ui.slides.count()

        self._ui.next_button.clicked.connect(self._on_next_button_clicked)
        self._ui.prev_button.clicked.connect(self._on_prev_button_clicked)

        self._point_enabled_style = "background-color:#f9af61; " \
                                    "border: 2px solid; " \
                                    "border-radius: 3px; " \
                                    "border-color:#f9af61;"
        self._point_disabled_style = "background-color:#cccccc; " \
                                     "border: 2px solid; " \
                                     "border-radius: 3px; " \
                                     "border-color:#cccccc;"
        self._points = [self._ui.point]
        self._init_points()

        self._setup_buttons()

        self._set_labels_fonts()
        self._set_controls_font([self._ui.next_button, self._ui.prev_button])

        self._slide_show = SlideShow(self._dialog, self._ui.slides)
        self._slide_show.current_index_changed.connect(
            self._on_current_index_changed)
        self._slide_show.clicked.connect(self._on_next_button_clicked)
        self._slide_show.key_pressed.connect(self._on_key_pressed)

        self._dialog.keyPressEvent = self._on_key_pressed

    def _on_next_button_clicked(self):
        if self._current_index + 1 >= self._slides_count:
            self._close()
            return

        self._slide_show.setCurrentIndex(self._current_index + 1)

    def _on_prev_button_clicked(self):
        if self._current_index - 1 < 0:
            return

        self._slide_show.setCurrentIndex(self._current_index - 1)

    def _on_key_pressed(self, ev):
        if ev.key() == Qt.Key_Left:
            self._on_prev_button_clicked()
        elif ev.key() == Qt.Key_Right:
            self._on_next_button_clicked()

    def _on_current_index_changed(self, new_index):
        self._current_index = new_index
        self._setup_buttons()

    def _setup_buttons(self):
        if self._current_index == 0:
            self._ui.prev_button.setDisabled(True)
            self._ui.prev_button.setStyleSheet(
                "border: 0; color:#ffffff; text-align:left;")
        else:
            self._ui.prev_button.setDisabled(False)
            self._ui.prev_button.setStyleSheet(
                "border: 0; color:#222222; text-align:left;")
        if self._current_index + 1 == self._slides_count:
            self._ui.next_button.setText(tr("GOT IT"))
        else:
            self._ui.next_button.setText(tr("NEXT"))

        self._setup_points()

    def _init_points(self):
        self._points[0].setText(' ')
        self._points[0].setFont(QFont("Noto Sans", 2))
        for i in range(1, self._slides_count):
            new_point = QLabel()
            new_point.setText(' ')
            new_point.setFont(QFont("Noto Sans", 2))
            new_point.setStyleSheet(self._point_disabled_style)
            self._points.append(new_point)
            self._ui.points_layout.addSpacing(8)
            self._ui.points_layout.addWidget(new_point)

    def _setup_points(self):
        for i, point in enumerate(self._points):
            style = self._point_enabled_style if i == self._current_index \
                else self._point_disabled_style
            point.setStyleSheet(style)

    def _set_labels_fonts(self):
        self._set_controls_font(self._ui.slides.findChildren(QLabel))

    def _set_controls_font(self, controls):
        if not self._dp or self._dp == 1:
            return

        for control in controls:
            font = control.font()
            font_size = font.pointSize() * self._dp
            control_font = QFont(font.family(), font_size, italic=font.italic())
            control_font.setBold(font.bold())
            control.setFont(control_font)

    def _close(self):
        self._slide_show.current_index_changed.disconnect(
            self._on_current_index_changed)
        self._dialog.accept()
        self._dialog.close()

    def show(self):
        logger.debug("Opening tutorial dialog")

        # Execute dialog
        self._dialog.exec_()
