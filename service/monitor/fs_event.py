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

from common.constants import event_names
from common.file_path import FilePath


class FsEvent(object):
    count = 0
    _lock = threading.RLock()

    def __init__(self,
                 event_type,
                 src,
                 is_dir,
                 dst=None,
                 is_offline=False,
                 quiet=False,
                 actual_path=None,
                 event_time=None):
        super(FsEvent, self).__init__()

        with self._lock:
            FsEvent.count += 1
            self.id = FsEvent.count

        self.event_type = event_type
        self.src = FilePath(src)
        self.dst = FilePath(dst) if dst else None
        self.is_dir = is_dir
        self.time = event_time if event_time else time.time()
        self.is_offline = is_offline
        self.quiet = quiet
        self.file = None
        self.actual_path = actual_path
        self.old_hash = None
        self.old_signature = None
        self.new_hash = None
        self.new_signature = None
        self.patch = None
        self.rev_patch = None
        self.file_recent_copy = None
        self.file_synced_copy = None
        self.file_size = 0
        self.mtime = 0
        self.old_mtime = 0
        self.old_size = 0
        self.in_storage = False
        self.is_link = False

    @property
    def event_name(self):
        return event_names[self.event_type]

    def __repr__(self):
        return \
            "FsEvent={addr}, " \
            "event_name={self.event_name}, " \
            "src='{self.src}', " \
            "dst='{self.dst}', " \
            "is_dir={self.is_dir}, " \
            "is_offline={self.is_offline}, " \
            "quiet={self.quiet}, " \
            "time={self.time}, " \
            "new_hash={self.new_hash}, " \
            "old_hash={self.old_hash}, " \
            "mtime={self.mtime}, " \
            "old_mtime={self.old_mtime}, " \
            "file_size={self.file_size}, " \
            "old_size={self.old_size}, " \
            "file={self.file}" \
            .format(self=self, addr=hex(id(self)))

    def __eq__(self, other):
        return (
            self.src == other.src
            or self.dst == other.dst
            or (other.dst and self.src == other.dst)
            or (self.dst and other.src == self.dst)
        )

    def __hash__(self):
        return self.id.__hash__()
