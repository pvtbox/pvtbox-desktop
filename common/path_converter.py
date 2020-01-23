# coding=utf-8
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
from os import path as op

class PathConverter(object):

    def __init__(self, root):
        self._root = root

    def create_relpath(self, fullpath):
        """
        Convert absolute path to relative path from root.
        :param fullpath: absolute path [str]
        :return: relative path [FilePath]
        """
        from common.file_path import FilePath

        return FilePath(op.relpath(FilePath(fullpath), start=self._root))

    def create_abspath(self, relpath):
        """
        Convert relative path from root to absolute path
        :param relpath: relative path [str]
        :return: absolute path [str]
        """
        from common.file_path import FilePath

        return FilePath(op.join(self._root, relpath)).longpath
