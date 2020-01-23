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
from PySide2.QtCore import QRunnable, QThreadPool, QEventLoop, QTimer
from contextlib import contextmanager


def qt_run(func):
    from functools import wraps

    @wraps(func)
    def async_func(*args, **kwargs):
        qr = Runnable(func, *args, **kwargs)
        pool = QThreadPool.globalInstance()
        pool.start(qr)

    return async_func


class Runnable(QRunnable):
    def __init__(self, func, *args, **kwargs):
        QRunnable.__init__(self)
        self._func = func
        self._args = args
        self._kwargs = kwargs

    def run(self):
        self._func(*self._args, **self._kwargs)


@contextmanager
def wait_signal(signal, timeout=5000):
    """Block loop until signal emitted, or timeout (ms) elapses."""
    loop = QEventLoop()
    signal.connect(loop.quit)

    yield

    if timeout:
        timer = QTimer()
        timer.setInterval(timeout)
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start()
    else:
        timer = None
    loop.exec_()
    signal.disconnect(loop.quit)
    if timer and timer.isActive():
        timer.stop()
