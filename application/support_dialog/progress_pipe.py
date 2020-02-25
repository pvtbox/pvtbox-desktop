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
from PySide2.QtCore import QTimer, QObject, Signal

from common.async_qt import qt_run

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ProgressPipe(QObject):
    pipe_finished = Signal()
    task_finished = Signal()

    def __init__(self, parent, control, timeout,
                 final_text="", final_timeout=0, fill_color=None):
        QObject.__init__(self, parent)
        self._control = control
        self._timeout = timeout
        self._final_text = final_text
        self._final_timeout = final_timeout
        self._fill_color = fill_color

        self._init_tasks()
        self._percent = 0

        self._final_timer = QTimer(self)
        self._final_timer.setInterval(self._final_timeout)
        self._final_timer.setSingleShot(True)
        self._final_timer.timeout.connect(self.pipe_finished.emit)

        self.task_finished.connect(self._on_task_finished)

    def _init_tasks(self):
        self._tasks = list()
        self._tasks_it = iter(self._tasks)
        self._current_task = None

    def add_task(self, text, func, *args):
        self._tasks.append(
            ProgressTask(self, text, self._timeout, func, *args))

    def start(self):
        self.task_finished.emit()

    def stop(self):
        if self._current_task:
            logger.debug("Stopping current task '%s'",
                         self._current_task)
            self._current_task.stop()
            self._init_tasks()
        if self._final_timer.isActive():
            self._final_timer.stop()

    def show_progress(self, text, progress, size):
        logger.debug("Show progress. text %s, progress %s, size %s",
                     text, progress, size)
        if size is None:
            percent = 0
        else:
            percent = min(99, progress * 100 // size)
        # make percent >= previous one
        percent = max(self._percent, percent)
        self._percent = percent

        suffix = "..." if not percent else " {}%".format(percent)
        self._control.setText(text + suffix)

    def _on_task_finished(self):
        if self._current_task and self._current_task.exception:
            logger.warning("Task %s raised exception (%s)",
                           self._current_task, self._current_task.exception)
            self._init_tasks()
            return

        result = self._current_task.result if self._current_task else None
        self._current_task = next(self._tasks_it, None)
        if self._current_task:
            logger.debug("Starting task '%s'...", self._current_task)
            if result is not None:
                self._current_task.add_argument(result)
            self._percent = 0
            self.show_progress(self._current_task.text, 0, None)
            self._current_task.run()
        elif self._final_text and self._final_timeout:
            logger.debug("All tasks done")
            self._control.setText(self._final_text)
            self._final_timer.start()


class ProgressTask(QObject):
    _finished = Signal()

    def __init__(self, parent, text, timeout, func, *args):
        QObject.__init__(self, parent)

        self._parent = parent
        self._func = func
        self._args = args
        self._text = text
        self._timeout = timeout

        self._result = None
        self._exception = None
        self._stopped = False

        self._func.progress = 0
        self._func.size = None
        self._func.stop = False

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(self._timeout)
        self._progress_timer.timeout.connect(self._on_timeout)

        self._finished.connect(self._on_finished)

    @property
    def result(self):
        return self._result

    @property
    def exception(self):
        return self._exception

    @property
    def text(self):
        return self._text

    def add_argument(self, arg):
        args_list = list(self._args)
        args_list.append(arg)
        self._args = tuple(args_list)

    def run(self):
        self._progress_timer.start()
        self._run()

    @qt_run
    def _run(self):
        self._func.progress = 0
        self._func.size = None
        self._func.stop = False
        self._exception = None

        try:
            self._result = self._func(*self._args)
        except Exception as e:
            self._exception = e

        self._func.progress = 0
        self._func.size = None
        self._func.stop = False
        if not self._stopped:
            self._finished.emit()
        else:
            logger.debug("Task %s stopped", self._text)

    def stop(self):
        self._stopped = True
        if self._progress_timer.isActive():
            self._progress_timer.stop()
        self._func.stop = True

    def _on_timeout(self):
        self._parent.show_progress(
            self._text, self._func.progress, self._func.size)

    def _on_finished(self):
        if self._progress_timer.isActive():
            self._progress_timer.stop()
        if not self._stopped:
            self._parent.task_finished.emit()

    def __str__(self):
        return self._text
