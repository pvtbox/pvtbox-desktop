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
from os.path import basename

from service.network.download_task.download_task import DownloadTask


class FileDownloadTask(DownloadTask):
    def __init__(
            self, tracker, connectivity_service,
            priority, obj_id, obj_size, file_path, file_hash,
            display_name, files_info=None, parent=None):
        DownloadTask.__init__(
            self, tracker, connectivity_service,
            priority, obj_id, obj_size, file_path,
            display_name, file_hash, parent=parent, files_info=files_info)