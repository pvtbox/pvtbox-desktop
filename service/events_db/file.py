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
from datetime import datetime

from sqlalchemy import Column, Integer, String, Unicode, Boolean, DateTime
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.hybrid import hybrid_property
from service.events_db.base import Base
from common.utils import benchmark


class File(Base):
    """
    Class representing record on the file in the local file events DB
    """

    # Name of table in DB
    __tablename__ = 'files'

    # ID of the record in the table
    id = Column(Integer(), primary_key=True)
    # Foreign key to folder containig this file. Null for the top-level folders
    folder_id = Column(Integer(), ForeignKey('files.id'), nullable=True,
                       index=True)
    # Name of the file
    name = Column(Unicode(), nullable=False, index=True)
    # UUID of the file as assigned by API server
    uuid = Column(
        String(32), nullable=True, index=True, default=None, unique=True)
    # ID of most recent record on the file stored in 'events' table
    event_id = Column(
        Integer(), ForeignKey('events.id'), nullable=True, default=None,
        index=True)
    # ID of event which has been skipped
    last_skipped_event_id = Column(
        Integer(), ForeignKey('events.id'), nullable=True, default=None)
    # Flag to distinct files and folders
    is_folder = Column(Boolean(), nullable=False, index=True)
    # Flag to ignore events
    ignored = Column(Boolean(), nullable=False, default=False)
    # Flag to mark files in excluded dirs
    excluded = Column(Boolean(), nullable=False, default=False, index=True)
    # File created timestamp
    created_timestamp = Column(
        DateTime(), nullable=False, index=True, default=datetime.now)
    # Flag to mark folder as collaborated
    is_collaborated = Column(Boolean(), nullable=True, default=False,
                             index=True)

    def __repr__(self):
        return \
            "{self.__class__.__name__}(" \
            "id={self.id}, " \
            "name='{self.name}', " \
            "uuid='{self.uuid}', " \
            "event_id='{self.event_id}', " \
            "is_folder={self.is_folder}, " \
            "folder_id={self.folder_id}, " \
            "last_skipped_event_id={self.last_skipped_event_id}, " \
            "ignored={self.ignored} " \
            "excluded={self.excluded} "\
            "is_collaborated={self.is_collaborated}"\
            ")"\
            .format(self=self)

    # Relationship to obtain most recent Event object for the file
    event = relationship("Event",
                         foreign_keys='File.event_id',
                         uselist=False,
                         lazy='joined',
                         post_update=True)

    # Relationship to obtain folder containing this
    folder = relationship("File",
                          lazy='joined',
                          remote_side=[id])

    @hybrid_property
    def is_existing(self):
        '''True if the file should now exists in the file system'''
        return (
            self.event_id and
            self.event and
            self.event.type != 'delete' and
            (self.folder_id is None or
             (self.folder and self.folder.is_existing))
        )

    @hybrid_property
    @benchmark
    def is_deleted(self):
        '''True if the file is already deleted.'''
        return (
            (self.event_id and (not self.event or self.event.type == 'delete'))
            or
            (self.last_skipped_event_id and self.events
             and self.last_skipped_event_id == self.events[-1].id)
            or
            (self.folder_id and (not self.folder or self.folder.is_deleted)))

    @hybrid_property
    @benchmark
    def is_deleted_registered(self):
        '''True if the file is already deleted and delete is registered.'''
        return (
            (self.event_id and
             (not self.event or
              (self.event.type == 'delete' and self.event.server_event_id)))
            or
            (self.last_skipped_event_id and self.events
             and self.last_skipped_event_id == self.events[-1].id)
            or
            (self.folder_id and (not self.folder or self.folder.is_deleted)))

    @hybrid_property
    def is_new(self):
        '''file from remote peer but is not created yet'''
        return not self.event_id

    @hybrid_property
    def is_locally_modified(self):
        '''file has modificaions which are not registered yet'''
        return self.event_id and self.event and not self.event.server_event_id

    @hybrid_property
    def file_hash(self):
        '''Hash of current version of the file in the file system'''
        return self.event.file_hash if self.event_id else None

    @hybrid_property
    def file_old_hash(self):
        '''Hash of previous version of the file in the file system'''
        return self.event.file_hash_before_event if self.event_id else None

    @hybrid_property
    def path(self):
        if not self.folder_id or not self.folder:
            return self.name

        return "/".join([self.folder.path, self.name])
