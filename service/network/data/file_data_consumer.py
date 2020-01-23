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
from service.network.data.data_consumer import DataConsumer

from service.network.browser_sharing import Message


class FileDataConsumer(DataConsumer):
    def __init__(self, parent, connectivity_service):
        DataConsumer.__init__(self, parent, connectivity_service)

    def _generate_data_request_message(self, obj_id, offset, length):
        return Message().data_request(Message.FILE, obj_id, offset, length)

    def _generate_data_abort_message(self, obj_id, offset):
        return Message().data_abort(Message.FILE, obj_id, offset)
