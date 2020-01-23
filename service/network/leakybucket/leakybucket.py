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
class LeakyBucketException(Exception):
    pass


class LeakyBucket(object):
    __slots__ = [
        '_capacity', '_rate', '_time_provider', '_last_value', '_last_time']

    def __init__(self, capacity, rate, time_provider):
        self._capacity = float(capacity)
        self._rate = float(rate)
        self._time_provider = time_provider
        self._last_value = self._capacity
        self._last_time = time_provider()

    def check(self, value):
        self._calculate_new_value()
        return value <= self._last_value

    def leak(self, value):
        if self.check(value):
            self._last_value -= value
            return self._last_value
        else:
            raise LeakyBucketException(
                'Not enough tokens, available {}'.format(self._last_value))

    def _calculate_new_value(self):
        if self._last_value == self._capacity:
            return
        current_time = self._time_provider()
        current_value = \
            self._last_value + (current_time - self._last_time) * self._rate
        self._last_time = current_time
        self._last_value = \
            current_value if current_value < self._capacity else self._capacity


class ThreadSafeLeakyBucket(LeakyBucket):
    __slots__ = ['_lock']

    def __init__(self, capacity, rate, time_provider):
        super(ThreadSafeLeakyBucket, self).__init__(
            capacity, rate, time_provider)
        from threading import RLock
        self._lock = RLock()

    def check(self, value):
        with self._lock:
            return super(ThreadSafeLeakyBucket, self).check(value)

    def leak(self, value):
        with self._lock:
            return super(ThreadSafeLeakyBucket, self).leak(value)
