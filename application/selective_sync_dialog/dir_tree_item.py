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
import os
import os.path as op
from itertools import chain

from .params import LOGGING_ENABLED


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class DirTreeItem(object):
    """
    Class incapsulating directory item data
    """

    def __init__(
            self, fullpath, dirname=None, parent_item=None, checked=None,
            tristate=False, tree_model=None, is_root=False):

        self._parent_item = parent_item
        self._fullpath = fullpath
        self._dirname = dirname
        if fullpath and dirname is None:
            self._dirname = op.basename(fullpath)

        self._tree_model = tree_model
        if tree_model is None and parent_item:
            self._tree_model = parent_item._tree_model

        self._child_items = []
        self._child_paths = {}

        self._tristate = tristate
        self._checked = checked
        self._is_root = is_root

        self._children_fetched = False
        if LOGGING_ENABLED:
            logger.debug("Added %s", self)

    def set_tristate(self, tristate):
        self._tristate = tristate

    def set_checked(self, checked, update_ancestors=True):
        # Do not modify root item
        if self._is_root:
            return
        self._checked = checked
        if update_ancestors:
            self._update_checked()

    def _update_checked(self):
        # Initial item init
        if self._checked is None:
            # Item path is excluded
            if self._tree_model.is_path_excluded(self._fullpath):
                self._checked = False
                self._update_ancestors()
            elif self._parent_item:
                self._checked = self._parent_item.is_checked() or \
                    self._parent_item.is_tristate()
        # Item checked state changed
        else:
            self.set_tristate(False)
            self._update_ancestors()
            # Update all descendants if any
            for child_item in self.descendants():
                child_item.set_checked(self._checked, update_ancestors=False)
                child_item.set_tristate(False)
                self._tree_model.emit_data_changed(child_item)
        self._tree_model.emit_data_changed(self)

    def _update_ancestors(self):
        # Update parent items
        for parent_item in self.ancestors():
            if parent_item.check_all_children_checked():
                # Set parent to be checked
                parent_item.set_checked(True, update_ancestors=False)
                parent_item.set_tristate(False)
            else:
                # Set parent to be tristate
                parent_item.set_checked(False, update_ancestors=False)
                parent_item.set_tristate(True)
            self._tree_model.emit_data_changed(parent_item)

    def __repr__(self):
        return \
            "{self.__class__.__name__}(" \
            "fullpath='{self._fullpath}', " \
            "checked={self._checked}, " \
            "tristate={self._tristate}" \
            ")"\
            .format(self=self)

    def get_parent_item(self):
        return self._parent_item

    def get_dirname(self):
        return self._dirname

    def get_fullpath(self):
        return self._fullpath

    def get_child_count(self):
        return len(self._child_items)

    def get_child_row(self, child_item):
        return self._child_items.index(child_item)

    def get_child_by_row(self, row):
        return self._child_items[row]

    def get_child_by_fullpath(self, fullpath):
        return self._child_paths.get(fullpath, None)

    def get_children(self):
        return self._child_items

    def get_row(self):
        if self._parent_item is None:
            return 0

        return self._parent_item.get_child_row(self)

    def is_checked(self):
        return self._checked

    def is_tristate(self):
        return self._tristate

    def append_child_item(self, item):
        self._child_items.append(item)
        self._child_paths[item.get_fullpath()] = item
        item._update_checked()

    def can_fetch_more(self):
        return not self._children_fetched

    def children_can_fetch_more(self):
        return any(map(lambda c: c.can_fetch_more(), self._child_items))

    def fetch_subdirs(self):

        if LOGGING_ENABLED:
            logger.info("Fetching subdirs for %s...", self)

        def get_subdirs(path):
            for root, dirs, files in os.walk(path):
                return dirs
            else:
                return ()

        for subdir in get_subdirs(self._fullpath):
            if self._tree_model.hide_dotted() and subdir[0] == '.':
                continue
            fullpath = op.join(self._fullpath, subdir)
            if fullpath not in self._child_paths:
                self.append_child_item(DirTreeItem(fullpath, subdir, self))

        self._children_fetched = True
        self._tree_model.emit_data_changed(self)

    def ancestors(self):
        """
        Returns iterator to obtain all ancestors of the item
        """

        item = self
        while item._parent_item:
            yield item._parent_item
            item = item._parent_item

    def descendants(self):
        """
        Returns iterator to obtain all descendants of the item
        """

        iter_children = iter(self._child_items)
        iter_grandchildren = \
            chain(*map(lambda c: c.descendants(), self._child_items))

        for c in iter_children:
            yield c
        for c in iter_grandchildren:
            yield c

    def check_all_children_checked(self):
        """
        Returns True if all children items are checked

        @return Flag value [bool]
        """

        checked = [c for c in self._child_items if c.is_checked() and not c.is_tristate()]
        return len(checked) == len(self._child_items)
