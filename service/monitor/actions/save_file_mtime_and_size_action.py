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
import logging

from service.monitor.actions.action_base import ActionBase
from common.constants import MODIFY, FILE_LINK_SUFFIX

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SaveFileMtimeAndSizeAction(ActionBase):
    def __init__(self, storage):
        super(SaveFileMtimeAndSizeAction, self).__init__()
        self._storage = storage

    def _on_new_event(self, fs_event):
        src_path = fs_event.src[: -len(FILE_LINK_SUFFIX)] if fs_event.is_link \
            else fs_event.src
        try:
            with self._storage.create_session(read_only=False,
                                              locked=True) as session:
                file = self._storage.get_known_file(
                    src_path, session=session)
                if file != fs_event.file:
                    return self.event_returned(fs_event)

                if fs_event.file.mtime <= fs_event.mtime:
                    fs_event.file.mtime = fs_event.mtime
                fs_event.file.size = fs_event.file_size
                self._storage.save_file(fs_event.file, session=session)
        except Exception as e:
            logger.warning("Can't save mtime for file %s. Reason: %s",
                           fs_event.src, e)
            return self.event_returned(fs_event)

        self.event_suppressed(fs_event)

    def _is_sutable(self, fs_event):
        return not fs_event.is_dir and \
            fs_event.event_type in (MODIFY, ) and \
            fs_event.in_storage
