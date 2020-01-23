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
import time
import threading


def test():
    event = threading.Event()
    event.clear()

    def thread():
        time.sleep(0.1)
        event.set()
        event.clear()

    th = threading.Thread(target=thread)
    th.start()
    return event.wait(1.0)


if not test():

    original_wait = type(threading.Event()).wait

    def fixed_wait(self, timeout=None):
        if timeout is None:
            return original_wait(self, timeout)

        startTime = time.time()
        original_wait(self, timeout)
        endTime = time.time()
        return (endTime - startTime) < timeout

    type(threading.Event()).wait = fixed_wait

    assert test(), "Patched event has a bug too"
