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
from sqlalchemy import Column, Integer, String, Boolean

Base = declarative_base()


class Patch(Base):
    __tablename__ = 'patches'

    id = Column(Integer(), primary_key=True)
    uuid = Column(String(32), nullable=False, unique=True)
    new_hash = Column(String(32), nullable=False, unique=False)
    old_hash = Column(String(32), nullable=False, unique=False)
    size = Column(Integer(), nullable=True, unique=False)
    direct_count = Column(Integer(), nullable=False, default=0)
    reverse_count = Column(Integer(), nullable=False, default=0)
    active = Column(Boolean(), nullable=False, default=True)
    exist = Column(Boolean(), nullable=False, default=False)

    def __repr__(self):
        return \
            "uuid='{self.uuid}' " \
            "new_hash='{self.new_hash}' " \
            "old_hash='{self.old_hash}' " \
            "direct_count='{self.direct_count}' " \
            "reverse_count='{self.reverse_count}' " \
            "active='{self.active}'" \
            .format(self=self)

    def __eq__(self, other):
        return self.id == other.id and \
            self.uuid == other.uuid

    def __hash__(self):
        return self.uuid.__hash__()
