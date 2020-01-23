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
from sqlalchemy import Column, Integer, String

Base = declarative_base()


class Copy(Base):
    __tablename__ = 'copies'

    id = Column(Integer(), primary_key=True)
    hash = Column(String(32), nullable=False, unique=True)
    count = Column(Integer(), nullable=False, default=1)

    def __repr__(self):
        return \
            "hash='{self.hash}' " \
            "count='{self.count}'" \
            .format(self=self)

    def __eq__(self, other):
        return self.id == other.id and \
            self.hash == other.hash
