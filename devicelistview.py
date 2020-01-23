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
from PySide2.QtWidgets import QTableView


class DeviceListView(QTableView):
    """
    Implements custom column width settings
    """

    def resizeEvent(self, event):
        from application.device_list_dialog import TableModel

        width = event.size().width()
        self.setColumnWidth(TableModel.COL_DEVICE_TYPE, width * 0.11)
        self.setColumnWidth(TableModel.COL_OS_TYPE_VERSION, width * 0.17)
        self.setColumnWidth(TableModel.COL_NODE_NAME, width * 0.19)
        self.setColumnWidth(TableModel.COL_ONLINE_STATUS, width * 0.07)
        self.setColumnWidth(TableModel.COL_DISK_USAGE, width * 0.09)
        self.setColumnWidth(TableModel.COL_NODE_STATUS, width * 0.09)
        self.setColumnWidth(TableModel.COL_DOWNLOAD_SPEED, width * 0.1)
        self.setColumnWidth(TableModel.COL_UPLOAD_SPEED, width * 0.1)
        self.setColumnWidth(TableModel.COL_MANAGE, width * 0.08)
        super(DeviceListView, self).resizeEvent(event)
