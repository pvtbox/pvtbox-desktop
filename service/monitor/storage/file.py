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
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Unicode, Boolean

from common.path_utils import get_signature_path

Base = declarative_base()


class File(Base):
    __tablename__ = 'files'

    id = Column(Integer(), primary_key=True)
    relative_path = Column(Unicode(), nullable=False, unique=True)
    is_folder = Column(Boolean(), nullable=False, index=True)
    file_hash = Column(String(32), nullable=True, index=True)
    mtime = Column(Integer(), nullable=False, default=0)
    size = Column(Integer(), nullable=False, default=0)
    events_file_id = Column(Integer(), nullable=True, index=True)
    was_updated = Column(Boolean(), nullable=False, default=0)

    @property
    def signature_rel_path(self):
        if self.is_folder:
            return None

        return get_signature_path(self.file_hash)

    def __repr__(self):
        return \
            "relative_path={self.relative_path}, " \
            "is_folder={self.is_folder}, " \
            "hash='{self.file_hash}', " \
            "events_file_id='{self.events_file_id}'" \
            .format(self=self)

    def __eq__(self, other):
        if not isinstance(other, File):
            return False

        return self.id == other.id and \
            self.relative_path == other.relative_path and \
            self.is_folder == other.is_folder and \
            self.file_hash == other.file_hash and \
            self.events_file_id == other.events_file_id

    def __ne__(self, other):
        return not self == other
