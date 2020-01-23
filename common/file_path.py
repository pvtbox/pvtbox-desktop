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
from os.path import normcase
import platform

from common.utils import normpath


class FilePath(str):
    long_path_windows_prefix = "\\\\?\\"

    def __new__(cls, value):
        value = normpath(value)
        system = platform.system()
        if system == 'Windows':
            value = value.lstrip(cls.long_path_windows_prefix)

        if isinstance(value, FilePath):
            return str.__new__(cls, value)
        value = value.replace('\\', '/')
        return str.__new__(cls, value)

    def _remove_prefix(self, path):
        system = platform.system()
        if system != 'Windows':
            return path

        return path.lstrip(self.long_path_windows_prefix)

    def __eq__(self, other):
        if not isinstance(other, str):
            return False

        s1 = self._remove_prefix(self)
        s2 = self._remove_prefix(other)
        if isinstance(other, FilePath):
            return str.__eq__(normcase(s1), normcase(s2))
        else:
            return str.__eq__(s1, s2)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __contains__(self, item):
        if not isinstance(item, str):
            return False

        s1 = self._remove_prefix(self)
        s2 = self._remove_prefix(item)
        item_list = normcase(s2).replace('\\', '/').split('/')
        self_list = normcase(s1).replace('\\', '/').split('/')
        return len(self_list) <= len(item_list) and \
            all(self_list[i] == item_list[i] for i in range(len(self_list)))

    def __hash__(self):
        return super(FilePath, self).__hash__()

    @property
    def longpath(self):
        system = platform.system()
        if system != 'Windows':
            return self

        return "{}{}".format(self.long_path_windows_prefix,
                              self.replace('/','\\'))

    @property
    def shortpath(self):
        return self._remove_prefix(self.longpath)
