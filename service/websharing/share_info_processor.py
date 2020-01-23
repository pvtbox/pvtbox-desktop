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
import shutil
import os
import os.path as op
from collections import namedtuple

from common.utils import get_downloads_dir
from common.file_path import FilePath


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FileInfo(namedtuple(
        'FileInfo', ['name', 'fullname', 'event_uuid', 'size', 'file_hash'])):
    """
    Stores necessary info for file downloading
    """

    pass


class ShareInfoProcessor(object):
    """
    Implements processing of 'share_info' API call results
    """

    def __init__(self, config=None):
        """
        Constructor
        """

        self._cfg = config

        # Info for files found
        self._files_info = []

        # Share name - root name from share_info
        self._share_name = ""

    @staticmethod
    def is_folder(share_info):
        return 'childs' in share_info

    def process(self, share_info):
        """
        Recursively process 'share_info' API call result.
        Extracts and stores info on files.
        Returns info on files extracted from 'share_info' API call results

        @param share_info  'share_info' API call result [dict]
        @return [FileInfo, ]
        """

        self._files_info = []

        # Empty share_info
        if not share_info:
            return []

        share_hash = share_info['share_hash']
        logger.info(
            "Processing share info for share hash '%s'...", share_hash)
        self._process(share_info)
        file_count = len(self._files_info)
        if file_count > 0:
            logger.info(
                "Found %s files for share hash '%s'...",
                len(self._files_info), share_hash)
        else:
            logger.warning(
                "Empty share info obtained")

        return self._files_info

    def _process(self, share_info, parent_dirname=None):
        """
        Recursively process 'share_info' API call result.
        Extracts and stores info on files.
        Appends given parent directory name to filenames

        @param share_info  'share_info' API call result [dict]
        @param parent_dirname Name of parent directory [unicode]
        """

        # Empty share_info
        if not share_info:
            return
        # It is folder
        elif self.is_folder(share_info):
            # Recursively process all nested folders
            if parent_dirname is None:
                # It is root folder
                self._share_name = share_info['name']
                downloads_dir = get_downloads_dir(
                    data_dir=self._cfg.sync_directory if self._cfg else None,
                    create=True)
                folder_name = op.join(downloads_dir, share_info["share_hash"])
                shutil.rmtree(folder_name, ignore_errors=True)
            else:
                folder_name = op.join(parent_dirname, share_info["name"])
            folder_name = FilePath(folder_name).longpath
            logger.debug("folder_name: '%s'", folder_name)
            os.mkdir(folder_name)
            for child in share_info['childs']:
                # FIXME: change to iterative approach
                self._process(child, parent_dirname=folder_name)
        # It is file
        else:
            if parent_dirname:
                fullname = op.join(parent_dirname, share_info['name'])
                fullname = FilePath(fullname).longpath
            else:
                self._share_name = share_info['name']
                fullname = op.join(
                    get_downloads_dir(
                        data_dir=self._cfg.sync_directory
                        if self._cfg else None,
                        create=True),
                    share_info['share_hash'])
                if op.exists(fullname):
                    os.remove(fullname)
            logger.debug("file_name: '%s'", fullname)
            self._files_info.append(FileInfo(
                name=share_info['name'],
                fullname=fullname,
                event_uuid=share_info['event_uuid'],
                size=int(share_info['file_size']),
                file_hash=share_info.get('file_hash', None),
            ))

    def get_name(self):
        return self._share_name
