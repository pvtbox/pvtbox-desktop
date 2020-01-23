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

from service.events_db import Event

from common.constants import CREATE, MODIFY, DELETE, MOVE, DIRECTORY, FILE

from .event_serializer import deserialize_event
from .exceptions import UnknowEventTypeException
from .exceptions import UnknownEventState
from .create_file_strategy import LocalCreateFileStrategy
from .create_file_strategy import RemoteCreateFileStrategy
from .create_folder_strategy import LocalCreateFolderStrategy
from .create_folder_strategy import RemoteCreateFolderStrategy
from .delete_file_strategy import LocalDeleteFileStrategy
from .delete_file_strategy import RemoteDeleteFileStrategy
from .delete_folder_strategy import LocalDeleteFolderStrategy
from .delete_folder_strategy import RemoteDeleteFolderStrategy
from .move_file_strategy import LocalMoveFileStrategy
from .move_file_strategy import RemoteMoveFileStrategy
from .move_folder_strategy import LocalMoveFolderStrategy
from .move_folder_strategy import RemoteMoveFolderStrategy
from .update_file_strategy import LocalUpdateFileStrategy
from .update_file_strategy import RemoteUpdateFileStrategy
from .remote_restore_file_strategy import RemoteRestoreFileStrategy
from .remote_restore_folder_strategy import RemoteRestoreFolderStrategy

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def _create_remote_stategy_from_event(
        db, event, last_server_event_id, patches_storage, copies_storage,
        get_download_backups_mode):
    '''Create strategy instance from database 'event' table row'''

    logger.debug('create strategy from remote %s', event)

    if (event.type not in ('delete', 'restore')
        and not event.is_folder
        and event.file
        and ((not event.file.event_id
              and not event.file.last_skipped_event_id)
             or event.file.last_skipped_event_id)):
        return RemoteCreateFileStrategy(db, event, copies_storage,
                                        get_download_backups_mode)
    elif event.type != 'delete' and event.is_folder and event.file and \
            not event.file.event_id and not event.file.last_skipped_event_id:
        return RemoteCreateFolderStrategy(db, event, get_download_backups_mode)
    elif event.type == 'create' and event.is_folder:
        return RemoteCreateFolderStrategy(db, event, get_download_backups_mode)
    elif event.type == 'create' and not event.is_folder:
        return RemoteCreateFileStrategy(db, event, copies_storage,
                                        get_download_backups_mode)
    elif event.type == 'update' and not event.is_folder:
        return RemoteUpdateFileStrategy(db, event, last_server_event_id,
                                        patches_storage, copies_storage,
                                        get_download_backups_mode)
    elif event.type == 'delete' and not event.is_folder:
        return RemoteDeleteFileStrategy(db, event, last_server_event_id,
                                        copies_storage,
                                        get_download_backups_mode)
    elif event.type == 'delete' and event.is_folder:
        return RemoteDeleteFolderStrategy(db, event, last_server_event_id,
                                          get_download_backups_mode)
    elif event.type == 'move' and not event.is_folder:
        return RemoteMoveFileStrategy(db, event, last_server_event_id,
                                      copies_storage,
                                      get_download_backups_mode)
    elif event.type == 'move' and event.is_folder:
        return RemoteMoveFolderStrategy(db, event, last_server_event_id,
                                        get_download_backups_mode)
    elif event.type == 'restore' and not event.is_folder:
        return RemoteRestoreFileStrategy(db, event, get_download_backups_mode)
    elif event.type == 'restore' and event.is_folder:
        return RemoteRestoreFolderStrategy(db, event,
                                           get_download_backups_mode)

    raise UnknowEventTypeException({
        'type': event.type,
        'is_folder': event.is_folder})


def create_local_stategy_from_event(db,
                                    event,
                                    file_path,
                                    license_type,
                                    new_file_path,
                                    get_download_backups_mode):
    if event.type == 'create' and event.is_folder:
        return LocalCreateFolderStrategy(db, event, file_path, license_type,
                                         get_download_backups_mode)
    if event.type == 'create' and not event.is_folder:
        return LocalCreateFileStrategy(
            db, event, file_path, license_type, get_download_backups_mode)
    elif event.type == 'update' and not event.is_folder:
        return LocalUpdateFileStrategy(
            db, event, file_path, get_download_backups_mode)
    elif event.type == 'delete' and not event.is_folder:
        return LocalDeleteFileStrategy(db, event, file_path,
                                       get_download_backups_mode)
    elif event.type == 'delete' and event.is_folder:
        return LocalDeleteFolderStrategy(db, event, file_path,
                                         get_download_backups_mode)
    elif event.type == 'move' and not event.is_folder:
        return LocalMoveFileStrategy(db, event, file_path, new_file_path,
                                     get_download_backups_mode)
    elif event.type == 'move' and event.is_folder:
        return LocalMoveFolderStrategy(db, event, file_path, new_file_path,
                                       get_download_backups_mode)

    raise UnknowEventTypeException({
        'type': event.type,
        'is_folder': event.is_folder})


def create_strategy_from_remote_event(db, msg, patches_storage,
                                      copies_storage,
                                      get_download_backups_mode):  # @@
    '''Create strategy instance based on signalling server message'''
    event, last_server_event_id = deserialize_event(msg)
    return _create_remote_stategy_from_event(
        db,
        event,
        last_server_event_id,
        patches_storage,
        copies_storage,
        get_download_backups_mode)


def create_strategy_from_local_event(db, msg, license_type, patches_storage,
                                     get_download_backups_mode):
    '''Create strategy based on file system monitor message'''
    logger.debug('create event strategy by message from filesystem %s', msg)

    event_types = {
        CREATE: "create",
        MODIFY: "update",
        DELETE: "delete",
        MOVE: "move"
    }

    file_name = msg.get('path', None)
    old_file_name = msg.get('src', None)
    new_file_name = msg.get('dst', None)
    assert file_name or (old_file_name and new_file_name)
    assert bool(file_name) != bool(old_file_name and new_file_name)
    assert msg['type'] in (DIRECTORY, FILE)

    new_hash = msg.get('hash', None)
    old_hash = msg.get('old_hash', None)
    diff_uuid, diff_size = patches_storage.get_patch_uuid_and_size(
        new_hash, old_hash)
    rev_diff_uuid, rev_diff_size = patches_storage.get_patch_uuid_and_size(
        old_hash, new_hash)

    event = Event(
        type=event_types[msg['event']],
        is_folder=(msg['type'] == DIRECTORY),
        file_size=int(msg.get('file_size', 0)),
        diff_file_size=diff_size,
        rev_diff_file_size=rev_diff_size,
        file_hash=new_hash,
    )

    return create_local_stategy_from_event(
        db=db,
        event=event,
        file_path=file_name if file_name else old_file_name,
        license_type=license_type,
        new_file_path=file_name if file_name else new_file_name,
        get_download_backups_mode=get_download_backups_mode
    )


def create_strategy_from_database_event(
        db, event, license_type, patches_storage, copies_storage,
        get_download_backups_mode):
    assert event.state, "Event stored in db must have a state"
    if event.state in ('occured', 'conflicted', 'registered', 'sent'):
        return create_local_stategy_from_event(
            db=db,
            event=event,
            file_path=None,
            license_type=license_type,
            new_file_path=None,
            get_download_backups_mode=get_download_backups_mode)

    elif event.state in ('received', 'downloaded'):
        return _create_remote_stategy_from_event(
            db, event, None, patches_storage, copies_storage,
            get_download_backups_mode)

    raise UnknownEventState(event.state, event)


def splt_move_to_create_delete(msg):
    msg_create = msg.copy()
    msg_create['path'] = msg_create['dst']
    msg_create['src'] = ''
    msg_create['dst'] = ''
    msg_create['event'] = CREATE

    msg_delete = msg.copy()
    msg_delete['path'] = msg_delete['src']
    msg_delete['src'] = ''
    msg_delete['dst'] = ''
    msg_delete['event'] = DELETE

    return msg_create, msg_delete
