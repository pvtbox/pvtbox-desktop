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

import logging

from PySide2 import QtCore
from PySide2.QtCore import QTimer
from PySide2.QtGui import QIcon, QColor, QFont, QFontDatabase

from common.utils import get_device_name, get_platform, get_os_name_and_is_server
from common.translator import tr
from common.utils import format_with_units
from common.constants import SUBSTATUS_SHARE


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class TableModel(QtCore.QAbstractTableModel):
    """
    Model class for device list widget
    """

    _column_count = 9

    COL_ONLINE_STATUS = 0
    COL_DEVICE_TYPE = 1
    COL_OS_TYPE_VERSION = 2
    COL_NODE_NAME = 3
    COL_DISK_USAGE = 4
    COL_NODE_STATUS = 5
    COL_DOWNLOAD_SPEED = 6
    COL_UPLOAD_SPEED = 7
    COL_MANAGE = 8

    _header_data = {
        COL_ONLINE_STATUS: tr("Online"),
        COL_DEVICE_TYPE: tr("Device type"),
        COL_OS_TYPE_VERSION: tr("Operating system"),
        COL_NODE_NAME: tr("Name"),
        COL_DISK_USAGE: tr("In use"),
        COL_NODE_STATUS: tr("Status"),
        COL_DOWNLOAD_SPEED: '\u0044',  # uses _arrow_font
        COL_UPLOAD_SPEED: '\u0055',  # uses _arrow_font
        COL_MANAGE: '',
    }

    _icon_paths = {
        'Windows': ':/images/platform/windows.svg',
        'Linux': ':/images/platform/linux.svg',
        'Darwin': ':/images/platform/apple.svg',
        'Android': ':/images/platform/android.svg',
        'iOS': ':/images/platform/apple.svg',
        'desktop': ':/images/node_type/desktop.png',
        'phone': ':/images/node_type/mobile.png',
        'tablet': ':/images/node_type/mobile.png',
        'online': ':/images/online.svg',
        'offline': ':/images/offline.svg',
    }

    NODE_STATUSES = {
        0: "Deactivated",
        1: "Active",
        2: "Deleted",
        3: "Syncing",
        4: "Synced",
        5: "Logged Out",
        6: "Wiped",
        7: "Power Off",
        8: "Paused",
        9: "Indexing",
        10: "Connecting",
        30: "Downloading"
    }

    OFFLINE_STATUSES = (0, 2, 5, 6, 7, 10)

    STATUSES_COLORS = {
        0: QtCore.Qt.darkGray,
        1: QtCore.Qt.darkGray,
        2: QtCore.Qt.darkGray,
        3: "#EE8641",
        4: QtCore.Qt.darkGreen,
        5: QtCore.Qt.darkGray,
        6: QtCore.Qt.darkGray,
        7: QtCore.Qt.darkGray,
        8: QtCore.Qt.darkGray,
        9: "#EE8641",
        10: QtCore.Qt.darkGray,
        30: "#EE8641"
    }

    def __init__(self, disk_usage, node_status, node_substatus):
        """
        Constructor
        """

        super(TableModel, self).__init__()
        self._data = []
        self._node_id_vs_row_number = {}
        self._icons = {}

        # Initialize icons
        for icon_id, icon_path in list(self._icon_paths.items()):
            if not icon_path:
                continue
            self._icons[icon_id] = QIcon(icon_path)

        # set font for arrow symbols
        font_id = QFontDatabase.addApplicationFont(":/fonts/symbol-signs.otf")
        font_family = QFontDatabase.applicationFontFamilies(font_id)[0]
        self._arrow_font = QFont(font_family, QFont().pointSize()+5)

        # Add the node itself
        node_ostype = get_platform()
        node_osname, is_server = get_os_name_and_is_server()
        # show "Downloading share" instead of "Syncing" if share is downlosded
        status = 30 if node_status == 3 and node_substatus == SUBSTATUS_SHARE \
            else node_status

        self_info = {
            'id': 'itself',
            'node_ostype': node_ostype,
            'node_osname': node_osname,
            'node_devicetype': 'desktop',
            'node_name': get_device_name(),
            'is_online': node_status not in self.OFFLINE_STATUSES,
            'is_itself': True,
            'disk_usage': disk_usage,
            'download_speed': 0.,
            'node_status': status,
            'upload_speed': 0.,
        }
        QTimer.singleShot(10, lambda: self._add_row(self_info))

    def update(self, nodes_info):
        """
        Updates information on displayed nodes

        @param nodes_info Information on nodes as returned by signalling client
            [iterable]
        """

        logger.verbose(
            "Updating with data: %s", nodes_info)

        self_node = self._data[self._node_id_vs_row_number['itself']]
        is_online = self_node['is_online']

        changed_nodes = deleted_nodes = set()
        # Add new/changed node info
        for node_id in nodes_info:
            if not nodes_info[node_id].get("own", False) \
                    or nodes_info[node_id].get("type", '') != 'node':
                continue
            row_data = nodes_info[node_id]
            if not is_online:
                row_data['is_online'] = False
            if not int(nodes_info[node_id].get("node_status", 0)):
                if row_data['id'] in self._node_id_vs_row_number:
                    deleted_nodes.add(node_id)
                    self._remove_row(node_id)
                continue
            node_name = row_data.get('node_name')
            if node_name:
                # Adding new node info
                if row_data['id'] not in self._node_id_vs_row_number:
                    self._add_row(row_data)
                # Updating existing node info
                else:
                    old_row_data = self._data[
                        self._node_id_vs_row_number[node_id]]
                    if old_row_data['is_online'] != row_data['is_online'] or \
                            old_row_data.get("node_status", 0) != \
                            row_data.get("node_status", 0):
                        changed_nodes.add(node_id)
                    self._data[self._node_id_vs_row_number[node_id]] = row_data
                    if not row_data['is_online']:
                        row_data['download_speed'] = 0
                        row_data['upload_speed'] = 0
                    self._emit_row_changed(row_data)

        # Set node offline status
        for node_id in self._node_id_vs_row_number.copy():
            row_data = self._data[self._node_id_vs_row_number[node_id]]
            if node_id not in nodes_info and not row_data.get('is_itself'):
                logger.verbose(
                    "Setting node ID '%s' as offline", node_id)
                row_data['is_online'] = False
                row_data['download_speed'] = 0
                row_data['upload_speed'] = 0
            if not row_data['is_online'] and not row_data.get('is_itself'):
                self._remove_row(node_id)
                self._add_row(row_data)

        return changed_nodes, deleted_nodes

    def update_node_download_speed(self, value):
        row = self._data[self._node_id_vs_row_number['itself']]
        row['download_speed'] = value if row['is_online'] else 0
        self._emit_row_changed(row)

    def update_node_upload_speed(self, value):
        row = self._data[self._node_id_vs_row_number['itself']]
        row['upload_speed'] = value if row['is_online'] else 0
        self._emit_row_changed(row)

    def update_node_sync_dir_size(self, value):
        row = self._data[self._node_id_vs_row_number['itself']]
        row['disk_usage'] = value
        self._emit_row_changed(row)

    def update_node_status(self, value, substatus):
        row = self._data[self._node_id_vs_row_number['itself']]
        # show "Downloading share" instead of "Syncing" if share is downlosded
        row['node_status'] = \
            30 if value == 3 and substatus == SUBSTATUS_SHARE else value
        is_online = value not in self.OFFLINE_STATUSES
        row['is_online'] = is_online
        if not is_online:
            row['download_speed'] = 0
            row['upload_speed'] = 0
        self._emit_row_changed(row)
        if not is_online:
            for row in self._data:
                row['is_online'] = is_online
                if not is_online:
                    row['download_speed'] = 0
                    row['upload_speed'] = 0
                self._emit_row_changed(row)

    def rowCount(self, parent=QtCore.QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return self._column_count

    def _convert_value(self, param_name, value, row_data=None):
        if value is None:
            return "Unknown"

        if param_name == 'device_type':
            if value == 'desktop':
                return tr('PC')
            elif value == 'phone':
                return tr('Mobile')
        elif param_name == 'is_online':
            if value:
                return tr('Yes')
            else:
                return tr('No')
        elif param_name == 'node_name' and row_data.get('is_itself'):
            return "{} ({})".format(value, tr("this node"))
        elif param_name == 'disk_usage':
            return format_with_units(value)
        elif param_name == 'download_speed' or param_name == 'upload_speed':
            return u"{}/s".format(format_with_units(value))
        elif param_name == 'node_status':
            if not isinstance(value, int):
                value = int(value)
            return tr(self.NODE_STATUSES[value])
        elif param_name == 'manage':
            if value:
                return tr('\nmanage...\n')
            else:
                return ''

        return str(value)

    def _get_real_node_status(self, row_data):
        status = row_data.get('node_status')
        if status is not None:
            status = int(status)
            if not row_data.get('is_online') and \
                    not row_data.get('is_itself') and \
                    status != 5 and status != 6:
                # status != LoggedOut or Wiped -> PowerOff
                status = 7
        return status

    def data(self, index, role=QtCore.Qt.DisplayRole):
        if not index.isValid():
            return None

        row_data = self._data[index.row()]
        column = index.column()

        if role == QtCore.Qt.DisplayRole:
            if column == self.COL_DEVICE_TYPE:
                return self._convert_value(
                    'device_type', row_data.get('node_devicetype'))
            elif column == self.COL_OS_TYPE_VERSION:
                return self._convert_value(
                    'node_osname', row_data.get('node_osname'), row_data)
            elif column == self.COL_NODE_NAME:
                return self._convert_value(
                    'node_name', row_data.get('node_name'), row_data)
            elif column == self.COL_ONLINE_STATUS:
                return self._convert_value(
                    'is_online', row_data.get('is_online'))
            elif column == self.COL_DISK_USAGE:
                return self._convert_value(
                    'disk_usage', row_data.get('disk_usage'))
            elif column == self.COL_NODE_STATUS:
                status = self._get_real_node_status(row_data)
                return self._convert_value(
                        'node_status', status)
            elif column == self.COL_DOWNLOAD_SPEED:
                return self._convert_value(
                    'download_speed', row_data.get('download_speed'))
            elif column == self.COL_UPLOAD_SPEED:
                return self._convert_value(
                    'upload_speed', row_data.get('upload_speed'))
            elif column == self.COL_MANAGE:
                self_node = self._data[self._node_id_vs_row_number['itself']]
                is_online = self_node['is_online']
                return self._convert_value(
                    'manage', is_online)

        elif role == QtCore.Qt.TextAlignmentRole:
            return QtCore.Qt.AlignCenter

        elif role == QtCore.Qt.DecorationRole:
            icon_id = None
            if index.column() == self.COL_DEVICE_TYPE:
                icon_id = row_data.get('node_devicetype')
            elif index.column() == self.COL_OS_TYPE_VERSION:
                icon_id = row_data.get('node_ostype')
            elif index.column() == self.COL_ONLINE_STATUS:
                icon_id = 'online' if row_data.get('is_online') else 'offline'
            try:
                if icon_id:
                    return self._icons[icon_id]
            except KeyError:
                logger.warning(
                    "No icon '%s'", icon_id)

        elif role == QtCore.Qt.TextColorRole:
            if index.column() == self.COL_ONLINE_STATUS:
                is_online = row_data.get('is_online')
                return QColor(
                    QtCore.Qt.darkGreen if is_online else QtCore.Qt.darkGray)
            elif index.column() == self.COL_NODE_STATUS:
                status = self._get_real_node_status(row_data)
                if status is not None:
                    return QColor(self.STATUSES_COLORS[int(status)])

        elif role == QtCore.Qt.FontRole:
            if index.column() == self.COL_ONLINE_STATUS:
                is_online = row_data.get('is_online')
                if is_online:
                    font = QFont()
                    font.setBold(True)
                    return font
            elif index.column() == self.COL_MANAGE:
                font = QFont()
                font.setItalic(True)
                return font

        return None

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Horizontal:
            if role == QtCore.Qt.DisplayRole:
                try:
                    return self._header_data[section]
                except (IndexError, KeyError):
                    pass
            elif role == QtCore.Qt.FontRole:
                if section == self.COL_DOWNLOAD_SPEED or \
                        section == self.COL_UPLOAD_SPEED:
                    return self._arrow_font

    def _add_row(self, row_data):
        row = len(self._data)
        self.beginInsertRows(QtCore.QModelIndex(), row, row)
        self._data.append(row_data)
        self._node_id_vs_row_number[row_data['id']] = row
        self.endInsertRows()

    def _emit_row_changed(self, table_row):
        row = self._node_id_vs_row_number[table_row['id']]
        index1 = self.createIndex(row, 0, None)
        index2 = self.createIndex(row, self._column_count - 1, None)
        self.dataChanged.emit(index1, index2)

    def _remove_row(self, node_id):
        row = self._node_id_vs_row_number.pop(node_id)
        for n_id in self._node_id_vs_row_number:
            if self._node_id_vs_row_number[n_id] >= row:
                self._node_id_vs_row_number[n_id] -= 1
        self.beginRemoveRows(QtCore.QModelIndex(), row, row)
        self._data.pop(row)
        self.removeRow(row)
        self.endRemoveRows()

    def to_manage(self, index):
        self_node = self._data[self._node_id_vs_row_number['itself']]
        is_online = self_node['is_online']
        return index.column() == self.COL_MANAGE and is_online

    def get_node_id_online_itself(self, index):
        row_data = self._data[index.row()]
        node_id = row_data.get('id')
        node_name = row_data.get('node_name')
        is_online = row_data.get('is_online')
        is_itself = row_data.get('is_itself')
        is_wiped = int(row_data.get('node_status', '0')) == 6  # status wiped
        return node_id, node_name, is_online, is_itself, is_wiped

