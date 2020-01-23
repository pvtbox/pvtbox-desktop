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

from PySide2.QtCore import Qt, QObject, Signal, QTimer, QEvent, QRect
from PySide2.QtWidgets import QScrollArea, QFrame, QHBoxLayout, QWidget, QLabel

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SlideShow(QObject):
    current_index_changed = Signal(int)
    clicked = Signal()
    key_pressed = Signal(QEvent)

    def __init__(self, parent, base_widget, is_animated=True):
        QObject.__init__(self, parent)
        self._base_widget = base_widget     # has to be stacked widget
        self._is_animated = is_animated
        self._slides_count = self._base_widget.count()

        self._scroll_area = QScrollArea(parent)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.mouseReleaseEvent = self._on_mouse_release_event
        self._scroll_area.keyPressEvent = self._on_key_pressed

        self._slide_width = None
        self._current_index = 0
        self._is_moving = False
        self._orig_resize_event = self._base_widget.resizeEvent
        self._base_widget.resizeEvent = self._resizeEvent

        self._animation_time = 300
        self._animation_steps = 50

    def _construct_ribbon(self):
        self._ribbon = QWidget()

        self._layout = QHBoxLayout()
        self._ribbon.setLayout(self._layout)

        x = 0
        for i in range(self._slides_count):
            self._base_widget.setCurrentIndex(i)
            widget = self._base_widget.currentWidget()
            if widget:
                new_widget = self._grab(widget)
                self._layout.addWidget(new_widget)
                x += self._slide_width

    def _grab(self, widget):
        new_widget = QLabel()
        pixmap = widget.grab()
        new_widget.setPixmap(pixmap)
        return new_widget

    def _resizeEvent(self, *args, **kwargs):
        self._orig_resize_event(*args, **kwargs)
        if not self._slide_width:
            self._scroll_area.setGeometry(
                self._base_widget.geometry())
            self._slide_width = self._base_widget.widget(0).width()
            QTimer.singleShot(50, self._show)

    def _show(self):
        self._construct_ribbon()
        self._scroll_area.setWidget(self._ribbon)
        self._scroll_area.setAlignment(Qt.AlignCenter)
        self._scroll_area.show()
        self._scroll_area.setFocus()

    def _on_mouse_release_event(self, ev):
        self.clicked.emit()

    def _on_key_pressed(self, ev):
        self.key_pressed.emit(ev)

    def setAnimated(self, is_animated, animation_time=None,
                    animation_steps=None):
        self._is_animated = is_animated
        if animation_time:
            self._animation_time = animation_time
        if animation_steps:
            self._animation_steps = animation_steps

    def is_moving(self):
        return self._is_moving

    def setCurrentIndex(self, new_index):
        new_index = max(new_index, 0)
        new_index = min(new_index, self._slides_count - 1)

        if new_index == self._current_index or self._is_moving:
            return

        is_animated = self._is_animated and \
                      abs(self._current_index - new_index) == 1
        self._move(new_index, is_animated)

    def _move(self, new_index, is_animated):
        self._is_moving = True
        source_x = self._current_index * self._slide_width
        target_x = new_index * self._slide_width

        if not is_animated:
            dx = target_x - source_x
            self._ribbon.scroll(-dx, 0)
            self._finish_moving(new_index)
        else:
            animation_interval = self._animation_time // self._animation_steps
            dx = (target_x - source_x) // self._animation_steps
            self._move_animated(
                source_x, target_x, dx, animation_interval, new_index)

    def _move_animated(self, source_x, target_x, dx,
                       animation_interval, new_index):
        if target_x == source_x:
            self._finish_moving(new_index)
            return

        if target_x > source_x:
            dx = min(dx, target_x  - source_x)
        else:
            dx = -min(-dx, source_x - target_x)
        self._ribbon.scroll(-dx, 0)
        source_x += dx
        QTimer.singleShot(
            animation_interval,
            lambda: self._move_animated(source_x, target_x, dx,
                                        animation_interval, new_index))

    def _finish_moving(self, new_index):
        self._current_index = new_index
        self.current_index_changed.emit(self._current_index)
        self._is_moving = False

    def widget(self):
        return self._scroll_area
