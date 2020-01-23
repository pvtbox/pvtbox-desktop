# -*- coding: utf-8 -*-

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
from collections import defaultdict
from copy import copy
import inspect
import logging
import types

from common import async_utils
from common.errors import handle_exception

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SignalNotConnectedError(Exception):
    def __init__(self):
        Exception.__init__(self, self.__class__.__name__)
    pass


class Signal(object):
    def __init__(self, *args):
        super(Signal, self).__init__()
        self._args = []
        self._args.extend(args)
        self._slots = defaultdict(set)
        self._suppress_exceptions = False

    def suppress_exceptions(self, suppress=True):
        self._suppress_exceptions = suppress
        return self

    def connect(self, slot, order=10):
        assert callable(slot)

        if isinstance(slot, Signal):
            args = slot._args
        elif isinstance(slot, types.MethodType):
            args = inspect.getfullargspec(slot)[0][1:]
        elif isinstance(slot, types.FunctionType):
            args = inspect.getfullargspec(slot)[0]
        else:  # callable class instance
            args = inspect.getfullargspec(slot.__call__)[0][1:]

        if len(args) != len(self._args):
            logger.error('prototype: %s\nslot:      %s', self._args, args)
            assert False, 'slot signature do not match with signal'

        self._slots[order].add(slot)

    def disconnect(self, slot):
        assert slot
        for order, slots in self._slots.items():
            slots.discard(slot)

    def disconnect_all(self):
        self._slots.clear()

    def check_connected(self):
        if not self._slots:
            raise SignalNotConnectedError()

    def emit(self, *args, **kwargs):
        for order in sorted(self._slots.keys()):
            for slot in copy(self._slots[order]):
                if self._suppress_exceptions:
                    try:
                        slot(*args, **kwargs)
                    except:
                        handle_exception('exception in the slot')
                else:
                    slot(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        self.emit(*args, **kwargs)


class AsyncSignal(Signal):
    def __init__(self, *args):
        super(AsyncSignal, self).__init__(*args)
        self._suppress_exceptions = True

    def suppress_exceptions(self, suppress=True):
        assert \
            suppress, \
            "It is denied to pass exceptions in the AsyncSignal"

    @async_utils.run_daemon
    def emit(self, *args, **kwargs):
        super(AsyncSignal, self).emit(*args, **kwargs)
