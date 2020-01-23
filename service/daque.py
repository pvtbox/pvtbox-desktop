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
from threading import RLock, Event
from collections import deque
import time
import logging

# Setup logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Empty(Exception):
    pass


class Daque(object):
    """
    Double added queue.

    Simulates simplified standard Queue behaviour.
    Makes it possible to add objects to the beginning of queue
    """

    _small_timeout = 0.1

    def __init__(self, max_workers=0):
        super(Daque, self).__init__()
        self._deque = deque()
        # self._added = Event() wait for Python 3
        self._lock = RLock()

        self._max_workers = max_workers
        self._tasks_in_processing = 0
        self._enabled = True
        self._postponed = False

    def get(self, block=True, timeout=0, to_process=False):
        tries = int(max(timeout, 0) / self._small_timeout) + 1
        to_wait = timeout == 0
        count = 0
        logger.debug("Starting getting item from daque")
        while count < tries or to_wait:
            try:
                with self._lock:
                    count += 1
                    if not self._postponed and (
                            not self._max_workers or not to_process or
                            (to_process and
                            self._tasks_in_processing < self._max_workers)):
                        item = self._deque.popleft()
                        if to_process:
                            self._tasks_in_processing += 1
                        return item
                    else:
                        raise IndexError
            except IndexError:
                if not block:
                    break
                time.sleep(self._small_timeout)
                # self._added.wait(self._small_timeout) wait for Python 3
            except Exception as e:
                logger.error("Error getting item from daque %s", e)
            # finally:
                # self._added.clear()   wait for Python 3
        else:
            logger.debug("Daque get timeout")

        raise Empty

    def get_nowait(self, to_process=False):
        return self.get(block=False, to_process=to_process)

    def put(self, item):
        if not self._enabled:
            return

        with self._lock:
            self._deque.append(item)
            # self._added.set() wait for Python 3

    def putleft(self, item):
        if not self._enabled:
            return

        with self._lock:
            self._deque.appendleft(item)
            # self._added.set() wait for Python 3

    def empty(self):
        with self._lock:
            return not len(self._deque)

    def task_done(self, future):
        with self._lock:
            if not self._max_workers:
                return

            self._tasks_in_processing -= 1
            if self._tasks_in_processing < 0:
                logger.debug("Processed more tasks, than items got")
                self._tasks_in_processing = 0

    def enable(self):
        self._enabled = True

    def disable(self):
        self._enabled = False

    def clear(self):
        with self._lock:
            self._deque.clear()
            self._tasks_in_processing = 0
            # self._added.clear()   wait for Python 3

    def set_postponed(self, postponed=True):
        with self._lock:
            self._postponed = postponed
            logger.debug("Daque postponed mode is %s", self._postponed)
