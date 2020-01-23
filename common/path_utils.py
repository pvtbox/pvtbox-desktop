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
from os import path as op

from common.file_path import FilePath


def get_signature_path(hash):
    hash = '' if not hash else hash
    return op.join('.pvtbox', 'signatures', hash)


def is_contained_in(child, parent):
    """
    Returns true if child directory is contained in parent.
    Both paths should be either absolute or relative

    @param child Child candidate directory path [unicode]
    @param parent Parent directory path [unicode]
    @return Check result [bool]
    """
    return FilePath(child) in FilePath(parent)


def is_contained_in_dirs(path, dir_list):
    """
    Returns true if given path is contained in one of directories given.
    Both path and directories paths should be either absolute or relative

    @param path Candidate path to be checked [unicode]
    @param dir_list List of unicode directory paths [iterable]
    @return Check result [bool]
    """

    if not dir_list:
        return False
    for excluded_dir in dir_list:
        if is_contained_in(path, excluded_dir):
            return True
    return False
