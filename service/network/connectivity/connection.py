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
from uuid import uuid4


class Connection(object):
    MAX_BUFFER_CAPACITY = 16 * 1024 * 1024

    def __init__(self, node_id, connection_id=None):
        self.node_id = node_id
        self.id = bytes(connection_id) if connection_id else str(uuid4()).encode()
        self.open = False
        self.buffered_amount = 0
        self.used = False

    def is_buffer_overflow(self):
        return self.buffered_amount > self.MAX_BUFFER_CAPACITY / 2.
