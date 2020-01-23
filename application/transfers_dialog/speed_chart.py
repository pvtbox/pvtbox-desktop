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
from collections import deque

from PySide2.QtCore import QPointF, Qt, QLineF
from PySide2.QtGui import QPen, QGradient, QLinearGradient, \
    QPainter, QBrush, QColor, QFont, QFontMetrics
from PySide2.QtCharts import QtCharts
from PySide2.QtWidgets import QGraphicsLineItem, QGraphicsSimpleTextItem, QGraphicsTextItem

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SpeedChart(object):
    def __init__(self, parent, capacity, color,
                 gradient_start=None, gradient_end=None, speeds=(),
                 is_upload=False, dp=1, max_speed=0):
        self._parent = parent
        self._capacity = capacity
        self._color = color
        self._gradient_start = gradient_start if gradient_start \
            else self._color
        self._gradient_end = gradient_end if gradient_end \
            else self._color.lighter()
        self._last_max_speed = max_speed

        self._chart = QtCharts.QChart()

        self._line_series = QtCharts.QLineSeries()
        self._lower_series = QtCharts.QLineSeries()
        if len(speeds) > self._capacity:
            speeds = speeds[len(speeds) - self._capacity:]
        for i, speed in enumerate(speeds):
            self._line_series.append(i, speed)
            self._lower_series.append(i, 0)
        self._last_index = len(speeds)

        self._series = QtCharts.QAreaSeries(
            self._line_series, self._lower_series)
        pen = QPen(self._color)
        pen.setWidth(1)
        self._series.setPen(pen)

        self._series.lowerSeries().setColor(self._gradient_end)

        gradient = QLinearGradient(QPointF(0, 0), QPointF(0, 1))
        gradient.setColorAt(0.0, self._gradient_start)
        gradient.setColorAt(1.0, self._gradient_end)
        gradient.setCoordinateMode(QGradient.ObjectBoundingMode)
        self._series.setBrush(gradient)

        self._chart.addSeries(self._series)
        self._chart.layout().setContentsMargins(0, 0, 0, 0)
        # make chart look bigger
        margins = [-35, -25, -35, -37]
        self._chart.setContentsMargins(*margins)
        self._chart.setBackgroundRoundness(0)

        grid_pen = QPen(QColor("#EFEFF4"))
        grid_pen.setWidth(1)

        self._chart.createDefaultAxes()
        self._chart.axisX().setLabelsVisible(False)
        # self._chart.axisY().setLabelsVisible(False)
        self._chart.axisX().setTitleVisible(False)
        # self._chart.axisY().setTitleVisible(False)
        self._chart.axisX().setGridLineVisible(True)
        self._chart.axisX().setGridLinePen(grid_pen)
        # self._chart.axisY().setGridLineVisible(False)
        # self._chart.axisY().setGridLinePen(grid_pen)
        self._chart.axisX().setLineVisible(False)
        self._chart.axisY().setLineVisible(False)
        self._chart.axisX().setRange(
            self._last_index - self._capacity, self._last_index - 1)
        self._set_y_range()
        self._chart.axisY().hide()
        # self._chart.setBackgroundBrush(QBrush(QColor("#EFEFF4")))

        self._view = QtCharts.QChartView(self._chart, self._parent)
        self._view.setRenderHint(QPainter.Antialiasing)
        self._view.setFixedSize(self._parent.size())
        self._view.chart().legend().hide()
        self._chart.resize(self._view.width(), self._view.height())

        text_start = ""
        self._scale_line = ScaleLine(
            self._chart, QColor("#777777"), QColor("black"),
            text_start=text_start, margins=margins, dp=dp,
            is_upload=is_upload)
        self._scale_line.set_line(self._last_max_speed, resize=True)

    def update(self, speed, max_speed):
        self._last_max_speed = max_speed
        self._view.setUpdatesEnabled(False)
        self._line_series.append(self._last_index, speed)
        self._lower_series.append(self._last_index, 0)
        self._last_index += 1
        if self._last_index > self._capacity:
            self._line_series.remove(0)
            self._lower_series.remove(0)
        self._chart.axisX().setRange(
            self._last_index - self._capacity, self._last_index - 1)
        self._set_y_range()
        self._scale_line.set_line(max_value=self._last_max_speed)
        self._view.setUpdatesEnabled(True)

    def _set_y_range(self):
        maxel = int(self._last_max_speed)
        maxy = int((maxel + 1) * 1.2)
        # maxy = 10 ** len(str(maxel))
        self._chart.axisY().setRange(0, maxy)

    def resize(self):
        self._view.setFixedSize(self._parent.size())
        self._scale_line.set_line(max_value=self._last_max_speed, resize=True)

class ScaleLine:
    def __init__(self, parent, color, text_color, text_start = "",
                 margins = (0, 0, 0, 0), dp=1, is_upload=False):
        self._parent = parent
        self._color = color
        self._text_color = text_color
        self._text_start = text_start
        self._left, self._top, self._right, self._bottom = margins
        self._dp = dp
        self._is_upload = is_upload

        self._line = QGraphicsLineItem(self._parent)
        self._line.setZValue(12)
        pen = QPen(self._color)
        pen.setWidth(1)
        pen.setStyle(Qt.DashLine)
        self._line.setPen(pen)

        if not self._is_upload:
            self._text_item = QGraphicsTextItem(self._parent)
            self._text_item.setZValue(11)
            self._text_item.setDefaultTextColor(self._text_color)
            font = self._text_item.font()
            font_size = 10 * self._dp
            if font_size > 0:
                self._text_item.setFont(QFont(font.family(), font_size))

    def set_line(self, max_value=0, resize=False):
        height = self._parent.size().height() + self._top + self._bottom
        shift = int(height - height / 1.1)
        y = -self._top + shift

        if not self._is_upload:
            value = 0
            max_value = int(max_value / 1.1)
            megabyte = 1024 * 1024
            if max_value > megabyte:
                value = "{} MB".format(round(max_value / megabyte, 1))
            elif max_value > 1024:
                max_value //= 1024
                if max_value >= 10:
                    max_value = max_value // 10 * 10
                value = "{} KB".format(max_value)
            elif max_value > 0:
                if max_value >= 10:
                    max_value = max_value // 10 * 10
                value = "{} B".format(max_value)
            scale_text =  self._text_start if not value \
                else "{}{}/s".format(self._text_start, value)

            font_height = QFontMetrics(self._text_item.font())\
                .boundingRect(scale_text).height()
            x = 10
            self._text_item.setPos(x, y - font_height - 10)
            self._text_item.setPlainText(scale_text)

        if not resize:
            return

        self._line.setLine(QLineF(0, y, self._parent.size().width() + 30, y))

