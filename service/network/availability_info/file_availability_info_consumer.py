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
from service.network.browser_sharing import Message

from service.network.availability_info.availability_info_consumer \
    import AvailabilityInfoConsumer


class FileAvailabilityInfoConsumer(AvailabilityInfoConsumer):

    def __init__(self, parent, connectivity_service, node_list):
        AvailabilityInfoConsumer.__init__(
            self, parent, connectivity_service, node_list)

    def _generate_request_message(self, obj_id):
        return Message().availability_info_request(Message.FILE, obj_id)

    def _generate_abort_message(self, obj_id):
        return Message().availability_info_abort(Message.FILE, obj_id)
