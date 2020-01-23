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
import calendar
from datetime import datetime

from service.events_db import Event


def serialize_event(event):
    assert event.server_event_id
    assert event.type
    assert event.file_uuid
    assert event.is_folder is not None

    last_server_event_id = (
        0 if not event.last_event
        else event.last_event.server_event_id)

    return {
        'event_id': event.server_event_id,
        'event_type': event.type,
        'is_folder': event.is_folder,
        'uuid': event.file_uuid,
        'event_uuid': event.uuid,
        'last_event_id': last_server_event_id,
        'diff_file_size': event.diff_file_size,
        'diff_file_uuid': event.diff_file_uuid,
        'rev_diff_file_size': event.rev_diff_file_size,
        'rev_diff_file_uuid': event.rev_diff_file_uuid,
        'file_name': event.file_name,
        'file_name_before_event': event.file_name_before_event,
        'file_size': event.file_size,
        'file_size_before_event': event.file_size_before_event,
        'file_hash': event.file_hash,
        'file_hash_before_event': event.file_hash_before_event,
        'parent_folder_uuid': event.folder_uuid,
        'timestamp': calendar.timegm(event.timestamp.utctimetuple()),
        'hash': event.file_hash,
    }


def deserialize_event(data):

    def required(field):
        return data[field]

    def optional(field, default):
        return data.get(field, default)

    return Event(
        server_event_id=required('event_id'),
        type=required('event_type'),
        is_folder=required('is_folder') not in (None, 0, '0', '', False),
        file_uuid=required('uuid'),
        uuid=required('event_uuid'),
        diff_file_size=int(optional('diff_file_size', 0)),
        diff_file_uuid=optional('diff_file_uuid', None),
        rev_diff_file_size=int(optional('rev_diff_file_size', 0)),
        rev_diff_file_uuid=optional('rev_diff_file_uuid', None),
        file_name=optional(
            'file_name_after_event',
            optional('file_name', None)),
        file_size=int(optional(
            'file_size_after_event',
            optional('file_size', 0))),
        file_size_before_event=int(optional('file_size_before_event', 0)),
        folder_uuid=optional('parent_folder_uuid', None),
        timestamp=datetime.utcfromtimestamp(float(required('timestamp'))),
        file_hash=optional('hash', None),
        file_hash_before_event=optional('file_hash_before_event', None),
        outdated=optional('outdated', False),
        erase_nested=optional('erase_nested', False),
        checked=optional('checked', False),
        file_name_before_event=optional('file_name_before_event', None),
    ), required('last_event_id')
