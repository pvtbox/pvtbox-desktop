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
from sqlalchemy import Column, Integer, String, DateTime, Enum, Unicode, \
    Boolean
from sqlalchemy import ForeignKey
from sqlalchemy.orm import relationship, backref
from service.events_db.base import Base


class Event(Base):
    """
    Class representing record on the file event in the local file events DB
    """

    # Allowed event types
    _event_types = ("create", "update", "delete", "move")
    # Allowed event states
    _event_states = (
        "occured", "conflicted", "registered", "sent",
        "received", "downloaded")

    # Name of table in DB
    __tablename__ = 'events'

    # ID of the record in the table
    id = Column(Integer(), primary_key=True)
    # ID of the file the event is corresponding to
    file_id = Column(Integer(), ForeignKey('files.id'), index=True,
                     nullable=False)

    # UUID of the event
    uuid = Column(String(32), nullable=True, index=True, default=None)
    # Type of the event
    type = Column(Enum(*_event_types), nullable=False)
    # Contain flag if it is a folder
    is_folder = Column(Boolean(), index=True, nullable=False)

    # Name of the file the event is corresponding to
    file_name = Column(Unicode(), nullable=True, index=True)
    # Name of the file the before event
    file_name_before_event = Column(Unicode(), nullable=True, index=True)
    # Size of the file the event is corresponding to
    file_size = Column(Integer(), nullable=False, default=0)
    file_size_before_event = Column(Integer(), nullable=False, default=0)
    # UUID of the file as assigned by API server
    file_uuid = Column(String(32), nullable=True, index=True, default=None)
    # folder_uuid for events which are chenge it
    folder_uuid = Column(String(32), nullable=True, index=True)
    # State of the event
    state = Column(
        Enum(*_event_states), nullable=False, default=_event_states[0],
        index=True)
    # ID of event assigned by API server on registration
    server_event_id = Column(
        Integer(), nullable=True, default=None, index=True, unique=True)
    # Local ID of previous event
    last_event_id = Column(
        Integer(), ForeignKey('events.id'), nullable=True, default=None)
    # UUID of the diff file for the event as assigned by API server
    diff_file_uuid = Column(
        String(32), nullable=True, index=True, default=None)
    # UUID of the diff file for the event as assigned by API server
    patch_file_path = Column(
        Unicode(), nullable=True, index=False, default=None)
    # Size of diff file for the event
    diff_file_size = Column(Integer(), nullable=False, default=0)
    # UUID of the reverse diff file for the event as assigned by API server
    rev_diff_file_uuid = Column(
        String(32), nullable=True, index=True, default=None)
    # UUID of the reverse diff file for the event as assigned by API server
    rev_patch_file_path = Column(
        Unicode(), nullable=True, index=False, default=None)
    # Size of reverse diff file for the event
    rev_diff_file_size = Column(Integer(), nullable=False, default=0)
    # Hash of file after applying this event
    file_hash = Column(String(32), nullable=True, index=True)
    file_hash_before_event = Column(String(32), nullable=True)
    # Timestamp of record modification
    timestamp = Column(
        DateTime(), nullable=False, index=True, default=datetime.now)
    outdated = Column(Boolean(), default=False)
    restore = Column(Boolean(), default=False)
    erase_nested = Column(Boolean(), default=False)
    checked = Column(Boolean(), default=False)

    # Relationship to obtain corresponding File object
    file = relationship(
        "File", foreign_keys='Event.file_id',
        lazy='joined',
        backref=backref('events', order_by=id))

    # Relationship to obtain previous event
    last_event = relationship(
        'Event',
        remote_side=[id])

    def __repr__(self):
        attr_names = [a for a in self.__dict__ if not a.startswith('_')]
        attr_list = ["{}='{}'".format(a, self.__dict__[a])
                     for a in sorted(attr_names)]
        return "{}({})".format(self.__class__.__name__,
                                ', '.join(attr_list))
