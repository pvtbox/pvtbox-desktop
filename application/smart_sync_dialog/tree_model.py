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
import os.path as op

from PySide2 import QtCore
from PySide2.QtGui import QIcon

from .dir_tree_item import DirTreeItem
from .params import LOGGING_ENABLED
from common.file_path import FilePath


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class TreeModel(QtCore.QAbstractItemModel):
    """
    Model class for smart sync directory view
    """

    def __init__(self, root_path, hide_dotted=False):
        """
        Constructor

        @param root_path Root directory full path [unicode]
        @param excluded_dirs List of directory paths (absolute) to be
            initially excluded [iterable]
        @param hide_dotted Flag indicating not to show directories which names
            start with dot [bool]
        """

        super(TreeModel, self).__init__()
        self._column_count = 1
        self._checking_column = 0
        self._offline_dirs = None
        self._added_to_offline = set()
        self._removed_from_offline = set()
        self._hide_dotted = hide_dotted
        self._root_path = root_path
        self._root_item = DirTreeItem(
            None, None, parent_item=None, checked=False, tree_model=self)
        self._changed_items = set()
        self._add_root_path_item(FilePath(self._root_path).longpath)

    def set_offline_dirs(self, offline_dirs):
        self._offline_dirs = offline_dirs

    def hide_dotted(self):
        return self._hide_dotted

    def _get_tree_item_index(self, item, column=0):
        return self.createIndex(item.get_row(), column, item)

    def emit_data_changed(self, item):
        index = self._get_tree_item_index(item)
        self.dataChanged.emit(index, index)

    def _add_root_path_item(self, directory_path):
        if LOGGING_ENABLED:
            logger.info(
                "Adding root directory '%s'...", directory_path)

        self._root_path_item = DirTreeItem(
            directory_path, dirname=op.basename(directory_path),
            parent_item=self._root_item, checked=False, is_root=True)
        self._root_item.append_child_item(self._root_path_item)

    def get_root_path_index(self):
        return self._get_tree_item_index(self._root_path_item)

    def is_path_offline(self, path):
        return self._offline_dirs and path in self._offline_dirs

    def rowCount(self, parent_index):
        if not parent_index.isValid():
            parentItem = self._root_item
        else:
            parentItem = parent_index.internalPointer()

        return parentItem.get_child_count()

    def columnCount(self, parent_index):
        return self._column_count

    def index(self, row, column, parent_index):
        if not self.hasIndex(row, column, parent_index):
            return QtCore.QModelIndex()

        if not parent_index.isValid():
            parentItem = self._root_item
        else:
            parentItem = parent_index.internalPointer()

        childItem = parentItem.get_child_by_row(row)
        if childItem:
            return self._get_tree_item_index(childItem, column=column)
        else:
            return QtCore.QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QtCore.QModelIndex()

        childItem = index.internalPointer()
        parentItem = childItem.get_parent_item()

        if parentItem == self._root_item or parentItem is None:
            return QtCore.QModelIndex()

        return self._get_tree_item_index(parentItem)

    def data(self, index, role):
        if not index.isValid():
            return None

        item = index.internalPointer()

        if LOGGING_ENABLED:
            logger.debug(
                "Requested '%s' role data for item '%s'", role, item)

        if role == QtCore.Qt.DisplayRole:
            return item.get_dirname()
        elif role == QtCore.Qt.CheckStateRole and \
                index.column() == self._checking_column:
            if item == self._root_path_item:
                return None
            elif item.is_tristate():
                value = QtCore.Qt.PartiallyChecked
            elif item.is_checked():
                value = QtCore.Qt.Checked
            else:
                value = QtCore.Qt.Unchecked
            return int(value)
        elif role == QtCore.Qt.DecorationRole and \
                index.column() == self._checking_column:
            if item.is_offline() and \
                    not item.will_be_online():
                return QIcon(":/images/status_synced.svg")
            elif item.will_be_offline():
                return QIcon(":/images/status_syncing.svg")
            else:
                return QIcon(":/images/status_online.svg")

    def setData(self, index, value, role=QtCore.Qt.EditRole):
        if index.column() == self._checking_column:
            if role == QtCore.Qt.EditRole:
                return False
            if role == QtCore.Qt.CheckStateRole:
                item = index.internalPointer()
                self._item_offline_changed(item, value)
                item.set_checked(value)

                return True

        return super(TreeModel, self).setData(index, value, role)

    def flags(self, index):
        if not index.isValid():
            return QtCore.Qt.NoItemFlags

        item = index.internalPointer()
        if LOGGING_ENABLED:
            logger.debug(
                "Requested flags for item '%s'", item)

        if index.column() == self._checking_column:
            flags = QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled
            if item != self._root_item:
                flags = flags | QtCore.Qt.ItemIsUserCheckable
        else:
            flags = super(TreeModel, self).flags(index)

        return flags

    def headerData(self, section, orientation, role):
        if orientation == QtCore.Qt.Horizontal and \
                role == QtCore.Qt.DisplayRole and section == 0:
            return 'Directory name'

        return None

    def canFetchMore(self, parent_index):
        if not parent_index.isValid():
            return super(TreeModel, self).canFetchMore(parent_index)

        parent_item = parent_index.internalPointer()
        return parent_item.can_fetch_more() or \
            parent_item.children_can_fetch_more()

    def hasChildren(self, parent_index):
        if not parent_index.isValid():
            return super(TreeModel, self).hasChildren(parent_index)

        parent_item = parent_index.internalPointer()
        if parent_item.can_fetch_more():
            return True
        else:
            return super(TreeModel, self).hasChildren(parent_index)

    def fetchMore(self, parent_index):
        if not parent_index.isValid():
            return

        parent_item = parent_index.internalPointer()

        if parent_item.can_fetch_more():
            parent_item.fetch_subdirs()

        for child in parent_item.get_children():
            if child.can_fetch_more():
                child.fetch_subdirs()

    def get_added_to_offline_paths(self):
        return self._added_to_offline

    def get_removed_from_offline_paths(self):
        return self._removed_from_offline

    def _item_offline_changed(self, item, value):
        path = item.get_fullpath()
        if value:
            item.set_will_be_offline(True)
            item.set_will_be_online(False)
            self._added_to_offline.add(path)
            self._removed_from_offline.discard(path)
        else:
            item.set_will_be_online(True)
            item.set_will_be_offline(False)
            self._removed_from_offline.add(path)
            self._added_to_offline.discard(path)

        for child in item.get_children():
            self._item_offline_changed(child, value)

    def on_item_expanded(self, index):
        if not index.isValid():
            return

        item = index.internalPointer()
        if item != self._root_path_item:
            return
