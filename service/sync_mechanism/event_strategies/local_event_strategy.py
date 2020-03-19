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
import traceback
from datetime import datetime
import logging

from service.events_db import Event, File, FileNotFound
from common.constants import MIN_DIFF_SIZE, FREE_LICENSE, \
    UNKNOWN_LICENSE, DB_PAGE_SIZE
from common.utils import generate_uuid
from common.path_utils import is_contained_in_dirs
from common.translator import tr

from .event_strategy import EventStrategy, atomic, db_read
from .exceptions import EventAlreadyAdded
from .utils import rel_path, basename, dirname

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class LocalEventStrategy(EventStrategy):
    """docstring for LocalEventStrategy"""
    def __init__(self, db, event, file_path, get_download_backups_mode,
                 is_smart_sync=False):
        super(LocalEventStrategy, self).__init__(
            db, event, get_download_backups_mode, is_smart_sync=is_smart_sync)
        self._file_path = file_path
        if file_path:
            event.file_name = basename(file_path)
            assert event.file_name and \
                '/' not in event.file_name
        # number of tries to cope with local conflicting events
        self._local_conflict_tries = 3

    ''' Functions to overload in descendants ==================================
    '''
    def _get_file(self, session, events_file_id=None):
        assert not self.event.file_id

        # ToDo check and omit this if unneeded
        if not events_file_id:
            logger.warning("No events_file_id for event %s", self.event)
            return self._get_file_by_relative_path(session)

        file = session.query(File)\
            .filter(File.id == events_file_id).one_or_none()

        if not file:
            logger.warning("Can't get file with id %s", events_file_id)
            raise FileNotFound(self._file_path)

        return file

    def _get_file_by_relative_path(self, session):
        assert not self.event.file_id
        return self.db.find_file_by_relative_path(
            file_path=self._file_path,
            session=session)

    def _assign_operation_specific_event_props(self, session):
        pass

    def _register_in_server(self, web_api, file):
        raise NotImplementedError(
            '_register_in_server method should be '
            'overriden in concrete event types')

    def _process_collaboration_access_error(self, fs, session, copies_storage):
        raise NotImplementedError(
            '_process_collaboration_access_error method should be '
            'overriden for specific event type')

    def _update_db_file_on_register(self, file, session=None):
        assert file.uuid == self.event.file_uuid
        if not file.event_id or file.event_id < self.event.id:
            self._set_actual_file_state_to_this(session)
#        assert file.event_id >= self.event.id

    ''' Public methods templates ==============================================
    '''
    def get_dst_path(self):
        return self._file_path

    def get_src_path(self):
        return self._file_path

    def event_is_known(self, session):
        return False

    @atomic
    def add_to_local_database(self, session, patches_storage=None,
                              copies_storage=None, events_queue=None,
                              fs_event=None, is_offline=True):
        event = self.event
        logger.debug("adding event to local database: %s", event)
        assert not event.id, "Event must not be already saved to db"
        assert not event.state, "status can't be assigned without saving"

        try:
            events_file_id = fs_event.file.events_file_id if fs_event \
                else self.file_id
            file = self._get_file(session, events_file_id)
            file.is_offline = not fs_event.is_link if fs_event else is_offline
            if self._is_smart_sync and file.is_folder and (not file.folder or
                    file.folder and not file.folder.is_offline):
                file.is_offline = False
        except FileNotFound:
            if event.type == 'delete':
                raise EventAlreadyAdded
            else:
                raise
        except AssertionError:
            if event.type == 'create':
                file = self._get_file_by_relative_path(session)
                file.is_offline = not fs_event.is_link if fs_event \
                    else is_offline
                fs_event.file.events_file_id = file.id
                raise EventAlreadyAdded
            else:
                raise

        if event.file_hash is None and file:
            event.file_hash = file.file_hash
        event.uuid = self._generate_uuid(session)
        event.file = file
        event.file_uuid = file.uuid
        event.last_event = file.event
        if self._check_if_event_should_be_ignored(file):
            event.file.ignored = True
            # FIXME: temporally setting event to 'sent' state, to be changed
            event.state = 'sent'
        else:
            event.state = 'occured'

        self._assign_operation_specific_event_props(session)
        session.add(event)
        session.flush()
        self._set_actual_file_state_to_this(session)
        self.file_id = file.id
        self.event_id = event.id
        deleted_count = 0
        if event.type == 'move':
            deleted_count = self._delete_occured_move_events(session)
        if fs_event:
            fs_event.file.events_file_id = self.file_id

        logger.debug("Event is stored in db: %s, file_id %s",
                     event, self.file_id)
        return 1 - deleted_count

    def _delete_occured_move_events(self, session):
        if not self.event.file.events[0].server_event_id:
            prev_event = self.event.file.events[0]
            file_name = self.event.file_name
            session.delete(self.event)
            session.flush()
            prev_event.file_name = file_name
            prev_event.file.event_id = prev_event.id
            session.commit()
            raise EventAlreadyAdded()

        deleted_count = session.query(Event) \
            .filter(Event.file_id == self.file_id) \
            .filter(Event.id != self.event_id) \
            .filter(Event.state == 'occured') \
            .filter(Event.type == 'move') \
            .delete()
        if deleted_count == 0:
            return deleted_count

        prev_events = session.query(Event) \
            .filter(Event.file_id == self.file_id) \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.state.in_(["sent", "downloaded", "received"])) \
            .order_by(Event.id.desc()) \
            .all()
        prev_event = prev_events[0] if prev_events else None
        if prev_event is not None and \
                prev_event.file_name == self.event.file_name and \
                prev_event.folder_uuid == self.event.folder_uuid:
            session.delete(self.event)
            session.flush()
            old_event_id = None
            for ev in prev_events:
                if ev.state in ["sent", "downloaded"]:
                    old_event_id = ev.id
                    break
            prev_event.file.event_id = old_event_id
            session.commit()
            raise EventAlreadyAdded()
        return deleted_count

    def _ready_to_register(self, session):
        if self.event.file.folder:
            if self.event.folder_uuid is None:
                if self.event.file.folder.uuid is None:
                    logger.debug(
                        "Not ready to register %s "
                        "because event's file folder's uuid unknown",
                        self.event)
                    return False
                else:
                    self.event.folder_uuid = self.event.file.folder.uuid
                    logger.debug("Ready to register %s, "
                                 "updating event's file folder's uuid to %s",
                                 self.event, self.event.folder_uuid)
                    return True
            else:
                logger.debug("Ready to register %s, "
                             "event's file folder's uuid is %s",
                             self.event, self.event.folder_uuid)
        else:
            logger.debug("Ready to register %s, event's file in root",
                         self.event)
        return True

    @atomic
    def register(self, session, web_api, fs,
                 copies_storage, patches_storage,
                 tracker=None, event_queue=None,
                 notify_user=None, license=UNKNOWN_LICENSE,
                 excluded_dirs=None):
        event = self.event
        logger.debug("registering local event %s", event)
        assert event.state == 'occured', "Invalid state {}".format(event.state)
        assert event.id, "Event must be saved before"

        if event.last_event and event.last_event.server_event_id is None:
            return  # postpone until previous event has been registered
        if event.type != 'create' and not event.last_event:
            registered_events = filter(
                lambda ev: ev.server_event_id and ev.server_event_id > 0,
                event.file.events)
            registered_events = sorted(
                registered_events, key=lambda ev: ev.server_event_id)
            if registered_events:
                event.last_event = registered_events[-1]

        event.file_uuid = event.file.uuid

        data, error = self._parse_web_server_responce(
            self._register_in_server(web_api, event.file))

        if not data and not error:  # Probably server unavailable
            return  # try again later
        if error:
            self._process_error(error, data, fs, tracker=tracker,
                                session=session, event_queue=event_queue,
                                notify_user=notify_user,
                                copies_storage=copies_storage, license=license)
            return

        self._set_event_attrs(event, data)

        if event.type == 'update':
            self._add_patches(event, data, patches_storage)

        logger.debug("Patches added")

        if not event.is_folder and event.file_hash_before_event and \
                event.file_size_before_event and \
                event.type not in ('move', 'delete'):
            copies_storage.remove_copy_reference(
                self.event.file_hash_before_event,
                reason="register. Event {}. File {}"
                    .format(self.event.uuid, self.event.file_name))

        if event.type in ('update', 'delete') and not event.is_folder:
            event_queue.cancel_file_download(event.file_id, session,
                                             previous_only=True,
                                             server_event_id=
                                             event.server_event_id)

        elif event.type == 'delete' and event.is_folder:
            self._events_queue = event_queue
            applied_deletes = self._apply_local_deletes_for_folder(
                event.file_id, self.event.file.uuid, session)
            self._events_queue.change_processing_events_counts(
                local_inc=-applied_deletes)
        elif event.type == 'create' and event.is_folder:
            fs.reset_collaboration_folder_icon(event.file_name)

        if event.type in ('move', 'delete') and event.is_folder:
            self._update_excluded_dirs(fs, excluded_dirs, session,
                                       signals=event_queue)

        if event.type == 'move':
            remote_inc = self._check_offline(session)
            event_queue.change_processing_events_counts(
                remote_inc=remote_inc)

        if event.file.toggle_offline:
            remote_inc = self.db.make_offline(
                event.file_uuid, session=session,
                is_offline=not event.file.is_offline)
            event_queue.change_processing_events_counts(
                remote_inc=remote_inc)

        self._update_db_file_on_register(event.file, session)
        logger.debug("registered local event %s", event)

    def _set_event_attrs(self, event, data):
        event.file_hash_before_event = data.get('file_hash_before_event', None)
        event.file_size_before_event = int(
            data.get('file_size_before_event', 0))
        event.server_event_id = data['event_id']
        event.timestamp = datetime.utcfromtimestamp(data['timestamp'])
        event.state = 'sent'

        if 'diff_file_uuid' in data:
            event.diff_file_uuid = data['diff_file_uuid']

        if 'rev_diff_file_uuid' in data:
            event.rev_diff_file_uuid = data['rev_diff_file_uuid']

        if 'file_uuid' in data:
            #assert not event.file_uuid
            event.file_uuid = data['file_uuid']

        if 'folder_uuid' in data:
            #assert not event.file_uuid
            event.file_uuid = data['folder_uuid']

    def _add_patches(self, event, data, patches_storage):
        if 'diff_file_uuid' in data:
            assert event.diff_file_uuid
            if event.file_size >= MIN_DIFF_SIZE:
                patches_storage.add_direct_patch(
                    event.diff_file_uuid,
                    event.file_hash,
                    event.file_hash_before_event,
                    reason="register. Event {}. File {}"
                        .format(event.uuid, event.file_name))

        if 'rev_diff_file_uuid' in data:
            assert event.rev_diff_file_uuid
            if event.file_size_before_event:
                patches_storage.add_reverse_patch(
                    event.rev_diff_file_uuid,
                    event.file_hash_before_event,
                    event.file_hash,
                    reason="register. Event {}. File {}"
                        .format(event.uuid, event.file_name))

    @atomic
    def process_conflict(self,
                         session,
                         fs,
                         copies_storage,
                         reenter_event,
                         create_strategy_from_event,
                         change_processing_events_count,
                         excluded_dirs):
        self._restore_last_nonconflicted_state(
            session, fs, copies_storage, create_strategy_from_event,
            change_processing_events_count, excluded_dirs)

        return True

    ''' Utility functions =====================================================
    '''
    def _check_if_event_should_be_ignored(self, file):
        if file.ignored:
            return True
        if file.folder is not None:
            return file.folder.ignored
        return False

    def _parse_web_server_responce(self, responce):
        if not responce:
            return None, None

        if 'error' in responce['result']:
            logger.error(
                "Failed to register event. Server replied: '%s'('%s')",
                responce['errcode'], responce['info'])
            return {
                       'info': responce['info'],
                       'error_data': responce.get('error_data', None),
                   }, responce['errcode']

        assert 'success' in responce['result']
        if not 'data' in responce:
            info = responce.get('info', "")
            return {
                        'info': info,
                        'error_data': "Collaboration folder deleted",
                    }, "LOCAL_COLLABORATION_DELETE"

        return responce['data'], None

    def _process_error(self, error, data, fs,
                       tracker=None, session=None, event_queue=None,
                       notify_user=None,
                       copies_storage=None, license=UNKNOWN_LICENSE):
        error_processing_routines = {
            'LICENSE_ACCESS': self._process_license_access_error,
            'COLLABORATION_ACCESS': self.process_collaboration_access_error,
            'FS_SYNC': self._process_fs_sync_error,
            'FS_SYNC_PARENT_NOT_FOUND': self._process_fs_sync_no_parent_error,
            'FS_SYNC_NOT_FOUND': self._process_fs_sync_no_parent_error,
            'FILE_NOT_CHANGED': self._process_file_not_changed_error,
            'WRONG_DATA': self._process_wrong_data_error,
            'FS_SYNC_COLLABORATION_MOVE':
                self._process_collaboration_move_error,
            'LOCAL_COLLABORATION_DELETE':
                self._process_local_collaboration_delete,
        }
        result = False
        if error in error_processing_routines:
            result = error_processing_routines[error](
                error, data, fs, session=session, event_queue=event_queue,
                notify_user=notify_user,
                copies_storage=copies_storage, license=license)

        if not result:  # error not processed
            logger.error(
                "%s error returned from server when processing sync event",
                error)
            if tracker:
                tb = traceback.format_list(traceback.extract_stack())
                tracker.error(tb, str(error))

    def _process_license_access_error(self, error, data, fs,
                                      session=None,
                                      event_queue=None,
                                      notify_user=None,
                                      copies_storage=None,
                                      license=None):
        self.event.state = 'sent'
        self.event.file.ignored = True
        return True

    def _process_fs_sync_error(self, error, data, fs,
                               session=None,
                               event_queue=None,
                               notify_user=None,
                               copies_storage=None,
                               license=None):
        if license not in (FREE_LICENSE, UNKNOWN_LICENSE) and \
                data and 'error_data' in data and data['error_data'] and \
                self.event.type == "create" \
                and len(self.event.file.events) == 1:
            error_data = data['error_data']
            file_hash = error_data.get('file_hash', None)
            file_name = error_data.get('file_name', None)
            if file_hash and file_hash == self.event.file_hash \
                    and file_name and file_name == self.event.file_name:
                not_registered_deletes = session.query(Event) \
                    .filter(Event.type == 'delete') \
                    .filter(Event.server_event_id.is_(None)) \
                    .filter(Event.file_name == file_name) \
                    .all()
                file_path = self.event.file.path
                for event in not_registered_deletes:
                    if event.file.path == file_path:
                        logger.debug("Same file delete not registered for %s",
                                     file_path)
                        return False

                logger.debug("File with same state already registered, "
                             "deleting local file and event")
                fs.change_events_file_id(self.file_id, None)
                session.delete(self.event.file)
                session.delete(self.event)
                self.event = None
                return True

        # maybe the conflict is conflict for filename
        try:
            file_name = data['error_data'].get('file_name', None)
            if file_name is not None \
                    and file_name != self.event.file_name:
                # resolved name will be determined inside called function
                self._change_conflicting_name(
                    fs, session, resolved_name=None, license=license)
                return True
        except Exception as e:
            pass

        self.event.state = 'conflicted'
        return True

    def _process_fs_sync_no_parent_error(self, error, data, fs,
                                         session=None,
                                         event_queue=None,
                                         notify_user=None,
                                         copies_storage=None,
                                         license=None):
        remote_delete_exist = any(e.type == 'delete' and
                                  e.state in ('recieved', 'downloaded')
                                  for e in self.event.file.events)
        next_local_events = sorted(filter(
            lambda ev: ev.id > self.event.id and
                       ev.state in ('occured', 'sent'),
            self.event.file.events), key=lambda ev: ev.id)

        if self.event.type == 'move' and not next_local_events:
            self.event.type = 'delete'
            self.event.state = 'occured'
            try:
                fs.accept_delete(self.event.file.path,
                                 is_directory=self.event.is_folder,
                                 events_file_id=self.event.file_id,
                                 is_offline=self.event.file.is_offline)
            except Exception as e:
                logger.warning("Can't delete %s. Reason: %s",
                               self.event.file.path, e)
        else:
            if next_local_events:
                if next_local_events[0].type != 'delete':
                    next_local_events[0].type = 'create'
                    next_local_events[0].last_event = None
                    self.event.file.uuid = None
                    self.event.file.event_id = next_local_events[0].id
                    if remote_delete_exist:
                        for event in self.event.file.events:
                            if event.type == 'delete' and \
                                    event.state in ('recieved', 'downloaded'):
                                session.delete(event)
                                break

                session.delete(self.event)
                self.event = None
            else:
                self._events_queue = event_queue
                applied_deletes = 0
                if not remote_delete_exist:
                    self.event.type = 'delete'
                    self.event.state = 'sent'
                    self.event.file.event_id = self.event.id
                    if not self.event.file.uuid:
                        self.event.file.uuid = generate_uuid()
                    self.event.file_uuid = self.event.file.uuid
                    self.event.server_event_id = self._events_queue \
                        .get_min_server_event_id()

                    if self.event.is_folder:
                        applied_deletes += self._apply_local_deletes_for_folder(
                            self.event.file_id, self.event.file_uuid, session)
                if remote_delete_exist:
                    if self.event.file.event_id == self.event.id:
                        self.event.file.event_id = self.event.last_event_id
                    session.delete(self.event)
                    self.event = None

                if applied_deletes:
                    self._events_queue.change_processing_events_counts(
                        local_inc=-applied_deletes)

        return True

    def _process_file_not_changed_error(self, error, data, fs,
                                        session=None,
                                        event_queue=None,
                                        notify_user=None,
                                        copies_storage=None,
                                        license=None):
        assert self.event.file, "File has to be bound to event"
        self.event.file.event_id = self.event.last_event_id
        session.delete(self.event)
        self.event = None
        return True

    def _process_wrong_data_error(self, error, data, fs,
                                  session=None,
                                  event_queue=None,
                                  notify_user=None,
                                  copies_storage=None,
                                  license=None):
        try:
            error_data = data['error_data']
            orig_file_name = error_data['orig_file_name']
            var_file_name = error_data['var_file_name']
            logger.debug(
                "Probably conflict for file name has occurred."
                "orig_file_name: '%s', var_file_name: '%s'",
                orig_file_name, var_file_name)
        except Exception as e:
            logger.error(
                "Cannot parse response '%s' from server. Data: '%s'."
                " Error: '%s'", error, data, e)
            return True
        self._change_conflicting_name(
            fs, session, resolved_name=var_file_name, force_change=True)
        return True

    def _process_collaboration_move_error(self, error, data, fs,
                                          session=None,
                                          event_queue=None,
                                          notify_user=None,
                                          copies_storage=None,
                                          license=None):
        assert self.event.file.is_collaborated, \
            "File has to be collaboration folder"

        old_path = self._get_last_file_path(session)
        new_path = self.event.file.path
        logger.debug("Moving collaboration folder %s back to %s",
                     new_path, old_path)
        try:
            fs.accept_move(new_path, old_path,
                           is_directory=True,
                           events_file_id=self.event.file.id)
        except Exception as e:
            logger.error("Error moving collaboration folder back (%s)", e)
            self.event.type = 'delete'
            self.event.state = 'occured'
            try:
                fs.accept_delete(new_path)
            except Exception:
                pass
            return True

        self.event.file.name = basename(old_path)
        self.event.file.event = self.event.last_event
        session.delete(self.event)
        self.event = None
        fs.reset_collaboration_folder_icon(old_path)
        fs.set_collaboration_folder_icon(old_path)
        event_queue.notify_collaboration_move_error(old_path, new_path)
        return True

    def _process_local_collaboration_delete(self, error, data, fs,
                                            session=None,
                                            event_queue=None,
                                            notify_user=None,
                                            copies_storage=None,
                                            license=None):
        assert self.event.type == 'delete' and self.event.is_folder, \
            "Waited for collaboration folder delete"

        session.delete(self.event)
        self.event = None
        return True

    def _apply_local_deletes_for_folder(self, folder_id, folder_uuid, session):
        logger.debug("Applying local deletes for children of folder id %s",
                     folder_id)
        folder_ids = [folder_id]
        count = 0
        event_mappings = []
        file_mappings = []
        file_uuids_generated = {folder_id: folder_uuid}
        while folder_ids:
            f_id = folder_ids.pop(0)
            files = session.query(File) \
                .filter(File.folder_id == f_id).all()
            folder_ids.extend([f.id for f in files if f.is_folder])
            for file in files:
                registered_events = list(filter(
                    lambda e: e.server_event_id, file.events))
                max_event = registered_events[-1] if registered_events \
                    else file.events[-1]
                if max_event.type == 'delete' and max_event.server_event_id:
                    continue

                if not max_event.folder_uuid:
                    max_event.folder_uuid = file_uuids_generated.get(
                        file.folder_id)
                    if not max_event.folder_uuid:
                        logger.warning("Folder uuid not found for event %s",
                                       max_event.uuid)

                if not file.uuid:
                    file.uuid = generate_uuid()
                    file_mappings.append({'id': file.id,
                                          'uuid': file.uuid})
                    file_uuids_generated[file.id] = file.uuid

                delete_event_mapping = self._get_dummy_local_delete_mapping(
                    file, max_event, session)
                file.event_id = delete_event_mapping['last_event_id']
                event_mappings.append(delete_event_mapping)

                occured_events = list(filter(
                    lambda e: e.state == 'occured', file.events))
                count += len(occured_events)
                if count >= DB_PAGE_SIZE:
                    self._events_queue.change_processing_events_counts(
                        local_inc=-count)
                    count = 0
                for event in occured_events:
                    logger.debug("Deleting occured event %s", event.id)
                    session.delete(event)

        session.bulk_insert_mappings(Event, event_mappings)
        session.bulk_update_mappings(File, file_mappings)
        logger.debug("Applied local deletes for %s events",
                     len(event_mappings))
        return count

    def _get_dummy_local_delete_mapping(self, file, base_event,
                                        session):
        server_event_id = self._events_queue.get_min_server_event_id()
        new_delete_event = self._add_dummy_delete(
            file, base_event, server_event_id, session, add_to_dummies=False)
        self._events_queue.cancel_file_download(file.id, session)
        if not base_event.server_event_id:
            new_delete_event.last_event_id = None
        self._events_queue.change_processing_events_counts(
            remote_inc=1)

        return self.db.get_mapping(new_delete_event)

    def _restore_last_nonconflicted_state(
            self, session, fs, copies_storage, create_strategy_from_event,
            change_processing_events_count, excluded_dirs):
        assert self.event.file_id
        file = session.query(File).filter(File.id == self.event.file_id).one()

        before_conflict_event = self._get_last_nonconflicted_state(session, fs)

        logger.debug("_get_last_nonconflicted_state. event_id %s, file_hash %s",
                     before_conflict_event.id, before_conflict_event.file_hash)
        assert before_conflict_event.id, \
            "Non conflicted state must exist." \
            "It can be absent only for create event. " \
            "See method overload in create"

        conflict_event_type = self.event.type
        is_folder_move = conflict_event_type == 'move' and self.event.is_folder
        conflict_path = file.path if is_folder_move else ""

        delete_event_id = None
        if not is_folder_move:
            delete_event_id = session.query(Event.id) \
                .filter(Event.file_id == self.event.file_id) \
                .filter(Event.type == 'delete').limit(1).scalar()
            self._restore_nonconflicted_for_non_delete(
                fs, file, session, before_conflict_event.file_size,
                before_conflict_event.file_hash, create_strategy_from_event)
            if conflict_event_type == 'move' and before_conflict_event.file_size:
                copies_storage.add_copy_reference(
                    self.event.file_hash,
                    reason="process conflict. Event {}. File {}"
                        .format(self.event.uuid, self.event.file_name))

        file.event_id = before_conflict_event.id
        file.name = before_conflict_event.file_name

        folder = self.find_folder_by_uuid(session, before_conflict_event.folder_uuid)
        file.event = before_conflict_event
        file.folder = folder
        file.folder_id = folder.id if folder else None

        file_path = file.path
        is_path_excluded = is_contained_in_dirs(file_path, excluded_dirs)
        file.excluded = is_path_excluded
        if fs.path_exists(file_path, file.is_offline):
            self._rename_or_delete_dst_path(file_path, session, file.is_offline)
        if is_folder_move:
            if (not folder or not folder.is_deleted) and not is_path_excluded:
                try:
                    # move folder back
                    fs.accept_move(
                        conflict_path, file_path,
                        is_directory=True,
                        events_file_id=file.id)
                except fs.Exceptions.FileNotFound:
                    logger.warning(
                        "File not found while moving folder %s back",
                        conflict_path)
            else:
                fs.accept_delete(
                    conflict_path,
                    is_directory=True,
                    events_file_id=file.id)

        elif ((not folder or not folder.is_deleted) and
              (conflict_event_type in ('update', 'move') and
               not delete_event_id and not is_path_excluded)):
            if before_conflict_event.file_size and file.is_offline:
                try:
                    fs.restore_file_from_copy(
                        file_name=file_path,
                        copy_hash=before_conflict_event.file_hash,
                        events_file_id=file.id)
                except fs.Exceptions.CopyDoesNotExists:
                    logger.warning(
                        "File copy not found when restoring {}, "
                        "make event received".format(conflict_path))
                    before_conflict_event.state = "received"
                    file.event_id = None
                    file.event = None
                    change_processing_events_count(remote_inc=1)
            else:
                fs.create_empty_file(
                    file_name=file_path,
                    file_hash=before_conflict_event.file_hash,
                    events_file_id=file.id,
                    is_offline=file.is_offline
                )

        logger.debug("_restore_last_nonconflicted_state event.file.event %s",
                     file.event)

        if is_folder_move:
            session.delete(self.event)
            self.event = None

    def _restore_nonconflicted_for_non_delete(
            self, fs, file, session, before_conflict_file_size,
            before_conflict_file_hash, create_strategy_from_event):

        new_file = File(
            name=file.name, is_folder=self.event.is_folder,
            is_offline=file.is_offline or
                       file.folder is not None and file.folder.is_offline)
        new_file.folder = file.folder

        for event in list(file.events):
            if event.id >= self.event.id \
                    and event.state in ('occured', 'conflicted'):
                event.file = new_file
                new_file.event = event
                event.file_uuid = None

        if self.event.type == 'move':
            self.event.file_size = before_conflict_file_size
            self.event.file_hash = before_conflict_file_hash
        session.flush()
        new_strategy = create_strategy_from_event(
            self.event, self.event.is_folder)
        new_strategy.event_id = self.event.id
        new_strategy.file_id = new_file.id
        fs.sync_events_file_id(new_file.path, new_file.id,
                               new_file.is_folder)
        new_strategy.make_conflicting_copy(fs, session=session)

    def process_collaboration_access_error(self, error, data, fs,
                                           session=None,
                                           event_queue=None,
                                           notify_user=None,
                                           copies_storage=None,
                                           license=None):

        def notify_user_collaboration_access():
            msg = (tr(
                'You have no rights to edit collaboration folder. '
                'Initial state has been restored. '
                'Changed object(s) have been moved to root.'))
            notify_user(text=msg)

        assert self.event.file, "File has to be already bound to event"
        logger.debug("Processing collaboration access error for event %s",
                     self.event)
        deleted_count, \
        remote_count, \
        folders_to_restore = self._process_collaboration_access_error(
            fs, session, copies_storage)

        if folders_to_restore:
            event_queue.add_restored_folders_to_processing(
                folders_to_restore)

        event_queue.change_processing_events_counts(
            local_inc=-deleted_count, remote_inc=remote_count)
        if self.event.type != 'delete':
            try:
                fs.accept_delete(self.event.file.path,
                                 self.event.is_folder,
                                 events_file_id=self.file_id,
                                 is_offline=self.event.file.is_offline)
            except fs.Exceptions.WrongFileId:
                logger.warning("Wrong file id while deleting %s",
                               self.event.file.path)
                fs.accept_delete(self.event.file.path,
                                 self.event.is_folder,
                                 is_offline=self.event.file.is_offline)

        self.event = None

        logger.debug("collaboration_alert_is_active %s, notify_user %s",
                     event_queue.collaboration_alert_is_active.is_set(), notify_user)
        if not event_queue.collaboration_alert_is_active.is_set() \
                and notify_user:
            event_queue.collaboration_alert_is_active.set()
            notify_user_collaboration_access()
        return True

    def _remove_or_restore_folder_files(self, folder_id,
                                        session, copies_storage):
        logger.debug("Removing or restoring files from folder with id %s",
                     folder_id)
        folder_ids = [folder_id]
        count = remote_count = 0
        folders_to_restore = []
        while folder_ids:
            f_id = folder_ids.pop(0)
            files = session.query(File) \
                .filter(File.folder_id == f_id).all()
            folder_ids.extend([f.id for f in files if f.is_folder])
            for file in files:
                count_adding, remote_count_adding = \
                    self._remove_or_restore_collaboration_file(
                        file.id, session, copies_storage, folders_to_restore)
                count += count_adding
                remote_count += remote_count_adding
        return count, remote_count, folders_to_restore

    def _remove_or_restore_collaboration_file(self, file_id,
                                              session, copies_storage,
                                              folders_to_restore=None):
        file = session.query(File).filter(File.id == file_id).one()
        logger.debug("Removing or restoring file %s", file)
        events = sorted(file.events, key=lambda e: e.server_event_id if e.server_event_id else 0)
        events_count = len(events)
        count = 0
        for event in events:
            if event.state == 'occured':
                if event.type not in ('move', 'delete') and \
                        event.file_size and event.file_hash:
                    copies_storage.remove_copy_reference(
                        event.file_hash,
                        reason="_process_collaboration_access_error. "
                               "Event {}. File {}".format(
                            event.uuid, event.file_name))
                session.delete(event)
                count += 1
        if count == events_count:
            # no more events
            session.delete(file)
            logger.debug("Removed file %s", file)
        else:
            if file.is_folder and folders_to_restore is not None and \
                    file.event_id:
                folders_to_restore.append(events[-1])
            file.event_id = None
            events[-1].state = 'received' if not file.is_folder \
                else 'downloaded'
            logger.debug("Restored file %s", file)

        remote_count = events_count - count
        return count, remote_count

    @db_read
    def _get_free_file_name(self, session, filename, fs):
        same_files = session.query(File)\
            .filter(File.name == filename)\
            .filter(File.folder_id.is_(None))\
            .all()
        same_files = list(filter(lambda f: f and f.is_existing, same_files))

        return fs.generate_conflict_file_name(
            filename, is_folder=self.event.is_folder) if same_files \
            else filename

    @db_read
    def _conflicting_name_exists(self, session=None):
        return self.db.conflicting_name_exists(
            self.event.file.folder_id, self.event.file.id,
            self.event.file_name)

    def _change_conflicting_name(self, fs, session, resolved_name=None,
                                 force_change=False, license=None):
        """
            Changes conflicting name in database and filesystem
        """

        if self.event.type not in ('create', 'move'):
            return

        # save conflicting name
        conflicting_name = self.event.file.path

        if resolved_name is None:
            # conflicting name for 'special-symbols' conflicts resolved name
            # should be passed as input. It fork will be used if the function
            # is used for changing conflicting name for 'case-insensitive'
            # conflicts
            resolved_name = fs.generate_conflict_file_name(
                conflicting_name, is_folder=self.event.is_folder)
        else:
            resolved_name = rel_path(dirname(conflicting_name), resolved_name)

        logger.debug("Change conflicting name '%s', resolved name is '%s'",
                     conflicting_name, resolved_name)

        resolved_basename = basename(resolved_name)
        # change name in database
        self.event.file_name = resolved_basename
        if not force_change or license == FREE_LICENSE:
            return

        self.event.file.name = self.event.file_name

        # find 'move' or 'delete' in next events
        next_events = session.query(Event)\
            .filter(Event.file_id == self.event.file_id)\
            .filter(Event.id > self.event.id)\
            .all()

        for event in next_events:
            if event.type in ('move', 'delete'):
                return

        # change name in filesystem
        while True:
            try:
                fs.accept_move(
                    src=conflicting_name,
                    dst=resolved_name,
                    is_directory=self.event.is_folder,
                    is_offline=self.event.file.is_offline)
                break
            except fs.Exceptions.FileAlreadyExists:
                logger.debug("Resolved name '%s' already exists.",
                             resolved_name)
                resolved_name = fs.generate_conflict_file_name(
                    conflicting_name, is_folder=self.event.is_folder)
                logger.debug("Next Resolved name is '%s'.",
                             resolved_name)
                resolved_basename = basename(resolved_name)
                self.event.file_name = resolved_basename
                self.event.file.name = self.event.file_name

    def _get_last_file_path(self, session):
        assert self.event.last_event

        file_name = self.event.last_event.file_name
        folder = self.find_folder_by_uuid(
            session, self.event.last_event.folder_uuid)
        return rel_path(folder.path, file_name) if folder else file_name
