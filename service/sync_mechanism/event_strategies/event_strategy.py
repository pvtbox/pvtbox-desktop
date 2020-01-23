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
import calendar

from service.events_db import Event, File
from common.path_utils import is_contained_in_dirs
from common.utils import benchmark, generate_uuid
from .exceptions import FolderUUIDNotFound, SkipEventForNow, \
    EventAlreadyAdded, ProcessingAborted, RenameDstPathFailed, \
    SkipExcludedMove
from .event_serializer import deserialize_event
from common.signal import Signal

from sqlalchemy.orm.session import Session

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def with_session(func):

    def impl(self, *args, **kwargs):
        read_only = kwargs.pop('read_only', True)
        session_arg = 'session'
        if session_arg in kwargs and kwargs[session_arg]:
            return func(self, *args, **kwargs)

        for arg in args:
            if isinstance(arg, Session):
                return func(self, *args, **kwargs)

        with self.db.create_session(
                read_only=read_only, expire_on_commit=False) as session:
            kwargs[session_arg] = session
            return func(self, *args, **kwargs)

    return impl


@with_session
def _db_access(self, func, *args, **kwargs):
    event_rollback_copy = self.event
    kwargs_session = kwargs.pop('session', None)
    session = kwargs_session
    if not session:
        for arg in args:
            if isinstance(arg, Session):
                session = arg
                break

    if not session:
        raise Exception("Expected session was none")
    try:
        session.autoflush = False
        self.event = session.merge(self.event)
        if kwargs_session:
            return func(self, session, *args, **kwargs)
        else:
            return func(self, *args, **kwargs)
    except EventAlreadyAdded:
        session.expunge(self.event)
        self.event = event_rollback_copy
        logger.debug("event is rolled back to %s because already added",
                     self.event)
        raise
    except Exception as e:
        self.event = event_rollback_copy
        logger.debug("event is rolled back to %s (%s)", self.event, e)
        raise


def atomic(func):
    def impl(self, *args, **kwargs):
        kwargs['read_only'] = False
        return _db_access(self, func, *args, **kwargs)

    return impl


def db_read(func):
    def impl(self, *args, **kwargs):
        kwargs['read_only'] = True
        return _db_access(self, func, *args, **kwargs)

    return impl


class EventStrategy(object):
    """
    Desribe the handling stratrgy for file events
    """
    DUMMY_PAGE_SIZE = 500

    def __init__(self, db, event, get_download_backups_mode):
        super(EventStrategy, self).__init__()
        self.event = event
        self.event_id = event.id if event else 0
        self.file_id = event.file.id if event and event.file else 0
        self.db = db
        self._cached_file_path = None
        self._events_queue = None
        self._download_backups = get_download_backups_mode()
        self._force_move = False

        self.change_processing_events_counts = Signal(int, int)  # (local, remote)
        self.append_local_event = Signal(Event, str, str, int)
        self.rename_or_delete_dst_path = Signal(str, int, Session)

    ''' Public methods templates ==============================================
    '''
    @atomic
    def apply(self, session=None, fs=None, excluded_dirs=None,
              patches_storage=None, collaborated_folders=(), events_queue=None):
        event = self.event
        assert event.file_id

        logger.debug('applying %s', self)
        change_name = True
        parent_found = True
        if event.type != 'delete':
            self._events_queue = events_queue
            change_name, parent_found = self._apply_move_if_needed(
                session, fs, excluded_dirs, patches_storage, events_queue)

        if parent_found:
            self._apply_event(session, fs, excluded_dirs,
                              patches_storage)
        if event.state == 'received' and not event.file.excluded:
            # update file strategy cannot apply patch
            return

        logger.debug('after _apply_event %s', self)
        self._set_actual_file_state_to_this(
            session, update_file_event=not event.file.excluded,
            change_name=change_name)

        if event.is_folder and event.type != 'delete':
            self.set_collaborated_folder_icon(
                session, fs, collaborated_folders)
        self.db.expunge_parents(event.file, session)
        logger.debug('applied %s', self)

    def _apply_event(self, session, fs, excluded_dirs,
                     patches_storage):
        pass

    def _create_file_from_copy(self, path, fs, search_by_id=False):
        pass

    def _apply_move_if_needed(self, session, fs,
                              excluded_dirs, patches_storage,
                              events_queue):
        event = self.event
        assert event.file_id
        parent_found = True
        folder = self.find_folder_by_uuid(session, event.folder_uuid)
        if folder == event.file.folder and event.file_name == event.file.name:
            return True, parent_found

        move_events = list(
            filter(lambda e: e.server_event_id and e.type == 'move' and
                             (not event.server_event_id or
                             e.server_event_id > event.server_event_id),
                   event.file.events))
        if move_events and not self._force_move and event.is_folder:
            # skip this if we have subsequent moves
            return False, parent_found

        # Calculate object path for further use
        event_path = event.file.path

        if folder and not folder.is_existing and not folder.excluded:
            logger.debug("Parent folder does not exist for %s", event_path)
            parent_found = False
            if self._process_parent_not_found(session):
                fs.accept_delete(event_path,
                                 is_directory=event.is_folder,
                                 events_file_id=event.file_id)
            return True, parent_found

        logger.debug('moving %s', event.file)
        new_path = ('/'.join([folder.path, event.file_name])
                    if folder
                    else event.file_name)

        # Check whether event paths are excluded from sync
        is_path_excluded = is_contained_in_dirs(event_path, excluded_dirs)
        is_new_path_excluded = is_contained_in_dirs(new_path, excluded_dirs)

        # Both source and destination paths are excluded
        if is_path_excluded and is_new_path_excluded:
            assert False, 'Excluded-excluded must never occur'
        # None of source and destination paths are excluded
        elif not is_path_excluded and not is_new_path_excluded:
            # Regular move event processing
            try:
                fs.accept_move(
                    event_path, new_path, is_directory=event.is_folder,
                    events_file_id=event.file_id)
            except fs.Exceptions.FileAlreadyExists:
                if event.file.event_id and not event.file.is_deleted:
                    if not self._rename_or_delete_dst_path(
                            new_path, session):
                        raise SkipEventForNow()
                    else:
                        # retry move after renaming new path
                        return self._apply_move_if_needed(
                            session, fs, excluded_dirs, patches_storage,
                            events_queue)
            except fs.Exceptions.FileNotFound:
                subsequent_local_moves_deletes = list(filter(
                    lambda ev: ev.id > event.id and
                               ev.type in ('delete', 'move') and
                               ev.state in ('occured', 'conflicted', 'sent'),
                    event.file.events))
                if not subsequent_local_moves_deletes and \
                        not self.check_previous_delete(
                            session, events_queue, fs):
                    # file/folder moved or deleted locally and
                    # no events in db for now
                    # so wait
                    logger.warning("Source file (folder) %s not found.",
                                   event_path)
                    raise SkipEventForNow()
            except fs.Exceptions.WrongFileId:
                if not self.event.is_folder or \
                        not self._apply_folder_delete_if_any(session, fs):
                    raise SkipEventForNow()

                # retry move after deleting folder
                return self._apply_move_if_needed(
                    session, fs, excluded_dirs, patches_storage, events_queue)
            except Exception as e:
                # ignore move if file is unavailable
                logger.warning("Can't move file (folder) %s. Reason %s",
                               event_path, e)
                raise SkipEventForNow()

            event.file.name = event.file_name
            event.file.folder = folder
            if folder:
                event.file.folder_id = folder.id
        # Source path is excluded
        elif is_path_excluded and not is_new_path_excluded:
            self.event.file.excluded = False
            self.event.file.folder = folder
            if event.is_folder:
                # Create directory at destination path
                fs.create_directory(new_path, self.event.file_id)
            else:
                # Create file at destination path
                if self.event.file_size:
                    self._create_file_from_copy(new_path, fs)
                else:
                    fs.create_empty_file(
                        new_path, self.event.file_hash, self.event.file_id)
        # Destination path is excluded
        elif not is_path_excluded and is_new_path_excluded:
            if not hasattr(self, '_excluded_ready') or \
                    not self._excluded_ready:
                self._excluded_ready = False
                raise SkipExcludedMove

            self.event.file.excluded = True
            self.event.file.event_id = None
            if not self.event.is_folder:
                self.event.state = 'received'
            else:
                self.db.mark_child_excluded(self.event.file_id, session)

            # Delete object at source path
            fs.accept_delete(event_path, is_directory=event.is_folder)
        return True, parent_found

    def _rename_or_delete_dst_path(self, path, session):
        try:
            self.rename_or_delete_dst_path.emit(
                path, self.event.file_id, session)
        except RenameDstPathFailed:
            return False

        return True

    @db_read
    def ready_to_apply(self, session=None, is_deleted=False):
        return self._ready_to_apply(session, is_deleted=is_deleted)

    @benchmark
    def _ready_to_apply(self, session, is_deleted=False, files=None):
        ready = (
            not self.event.file.is_locally_modified
            and (self.event.is_folder or self.ready_to_skip(session=session))
            and not is_deleted
            and self.event.state in ('registered', 'sent', 'downloaded'))

        return ready

    @atomic
    def ready_to_register(self, session):
        return self._ready_to_register(session)

    def _ready_to_register(self, session):
        return False

    @benchmark
    @db_read
    def ready_to_skip(self, session=None):
        event = self.event
        file = event.file
        file_event = file.event
        if file.event_id and not file.event:
            file_event = session.query(Event) \
                .filter(Event.id == file.event_id) \
                .one_or_none()
        return (
            event.id and (
                not file_event or
                file.event_id == event.last_event_id
                or file_event.type == 'delete'
                or not event.last_event_id
                or (file.last_skipped_event_id
                    and file.last_skipped_event_id == event.last_event_id)))

    @db_read
    def skip_if_file_will_be_deleted(self, session):
        '''Should be overriden in concrete strategy, if it can be skipped'''
        return not self.event.file.is_existing

    @atomic
    def skip(self, session, min_server_event_id=0, fs=None):
        if self.event.type == 'delete':
            self.event.file.event_id = self.event.id
            if fs:
                fs.sync_events_file_id_by_old_id(None, self.event.file_id)
        else:
            self.event.file.last_skipped_event_id = self.event.id
            # add dummy delete if there is no delete event for file
            file = session.query(File) \
                .filter(File.id == self.event.file_id) \
                .one_or_none()
            if min_server_event_id and file:
                delete_events = list(
                    filter(lambda e: e.type == 'delete',
                           file.events))
                if not delete_events:
                    self._add_dummy_delete(
                        file, self.event,
                        min_server_event_id, session)

    def postpone_after_save(self):
        ''' Should be overriden in concrete strategy
            if it must be postponed after save in db'''
        return False

    @db_read
    def file_will_be_deleted(self, session, file=None):
        return self._file_will_be_deleted(
            session=session,
            file=file if file else self.event.file)

    @benchmark
    def _file_will_be_deleted(self, session, file):
        return file.events and file.events[-1].type == 'delete'

    @db_read
    def get_old_uuid(self, session):
        if self.event.last_event:
            return self.event.last_event.uuid
        else:
            return None

    def set_collaborated_folder_icon(self, session, fs, collaborated_folders):
        pass

    def set_excluded_ready(self):
        self._excluded_ready = True

    ''' Utility functions ===================================================
    '''
    def _apply_patch(self, fs, file_path, patch_uuid):
        try:
            fs.apply_patch(
                file_path,
                fs.get_patch_path(patch_uuid),
                self.event.file_hash,
                self.event.file_hash_before_event,
                self.event.file_id)
        except Exception as e:
            logger.error("Can't apply patch %s for file %s. Error %s",
                         patch_uuid, file_path, e)
            return False
        return True

    def _get_last_nonconflicted_state(self, session, fs):
        ''' Also make a conflicted copy of the current file state.'''
        assert self.event.file_id
        assert self.event.last_event_id, \
            'Getting last nonconflicted state for create event'

        event = session.query(Event) \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.file_id == self.event.file_id) \
            .filter(Event.state.in_(['sent', 'downloaded'])) \
            .filter(Event.type != 'delete') \
            .filter(Event.id <= self.event.last_event_id) \
            .order_by(Event.id.desc()).first()

        assert event is not None, \
            'Probably getting last nonconflicted state for create event'

        return event

    def _set_actual_file_state_to_this(self, session, update_file_event=True,
                                       change_name=True):
        file = self.event.file

        if update_file_event:
            file.event = self.event
            file.event_id = self.event.id
            file.last_skipped_event_id = None
        if self.event.type in ('create', 'move') and change_name:
            file.name = self.event.file_name
            if not file.folder:
                folder = self.find_folder_by_uuid(
                    session,
                    self.event.folder_uuid)
                file.folder_id = folder.id if folder else None
        logger.debug("_set_actual_file_state_to_this. "
                     "event_id %s, file.event_id %s, file.name %s",
                     self.event.id, file.event_id, file.name)

    def find_folder_by_uuid(self, session, uuid):
        if not uuid:
            return None

        try:
            folder = session.query(File) \
                .filter(File.is_folder) \
                .filter(File.uuid == uuid) \
                .one()
        except:
            raise FolderUUIDNotFound(uuid)

        return folder

    def _get_target_path(self, session):
        if self.event.state in ('occured', 'sent', 'conflicted'):
            target_path = self.event.file.path
        else:
            target_path = self.db.get_path_from_event(self.event, session)
        return target_path

    def __str__(self):
        return '{self.__class__.__name__}: {self.event}'.format(self=self)

    def check_event_path_excluded(self, excluded_dirs):
        return False

    @db_read
    def get_file_path(self, session):
        if not self._cached_file_path:
            self._cached_file_path = self.event.file.path

        return self._cached_file_path

    @atomic
    def event_newer_than_applied(self, session):
        return not self.event.file.event or \
               self.event.server_event_id and \
               (not self.event.file.event.server_event_id or
                self.event.server_event_id >
                self.event.file.event.server_event_id)

    def make_conflicting_copy(self, fs):
        raise NotImplemented()

    @benchmark
    def is_event_skipped(self, session):
        event = self.event
        try:
            session.expire(event)
        except Exception:
            event = session.query(Event).filter(Event.id == event.id).one()

        return event.file.last_skipped_event_id and \
               event.id < event.file.last_skipped_event_id

    def force_move(self):
        self._force_move = True

    def _add_dummy_delete_events(self, session=None):
        assert self.event.file.is_folder

        self._dummy_deletes = []
        folder_uuid = self.event.file.uuid
        self.db.get_files_by_folder_uuid(
            folder_uuid,
            self._files_page_processor_cb,
            include_folders=True,
            include_deleted=False,
            session=session)
        self._save_dummy_delete_events(session)

    def _files_page_processor_cb(self, files_page, folders_uuids, session):
        file_ids = [f.id for f in files_page]
        all_events = session.query(Event) \
                .filter(Event.file_id.in_(tuple(file_ids))).all()
        for file in files_page:
            if self._events_queue.is_processing_stopped():
                raise ProcessingAborted
            events = filter(lambda e: e.file_id == file.id, all_events)
            events = sorted(
                events, key=lambda e: e.server_event_id if e.server_event_id else 0, reverse=True)
            if events and not (
                            events[0].type == 'delete' or
                            events[-1].type == 'delete' and
                            events[-1].server_event_id and
                            events[-1].server_event_id < 0
                            or
                            events[0].type == 'move' and
                            events[0].server_event_id and
                            events[0].folder_uuid not in folders_uuids
                            ):
                min_server_event_id = self._events_queue\
                    .get_min_server_event_id()
                session.expire(file)
                if not file.uuid:
                    file.uuid = generate_uuid()
                    for one_event in events:
                        one_event.file_uuid = file.uuid
                self._add_dummy_delete(file, events[0],
                                       min_server_event_id, session)
                self._events_queue.cancel_file_download(file.id, session)
                self._events_queue.change_processing_events_counts(
                    remote_inc=1)
                self._events_queue.events_added.set()
                if len(self._dummy_deletes) >= self.DUMMY_PAGE_SIZE:
                    self._save_dummy_delete_events(session)

        self._events_queue.allow_loading_remotes()

    def _save_dummy_delete_events(self, session):
        if self._dummy_deletes:
            logger.debug("Saving %s dummy deletes in db",
                         len(self._dummy_deletes))
            try:
                session.bulk_insert_mappings(Event, self._dummy_deletes)
            finally:
                self._dummy_deletes = []

    def _add_dummy_delete(self, file, event, server_event_id, session, add_to_dummies=True):
        msg = {
            'event_id': server_event_id,
            'event_type': 'delete',
            'is_folder': file.is_folder,
            'uuid': file.uuid,
            'event_uuid': event.uuid,
            'file_name': event.file_name,
            'file_name_before_event': event.file_name,
            'file_size': event.file_size,
            'last_event_id': event.server_event_id,
            'file_hash_before_event': event.file_hash,
            'parent_folder_uuid': event.folder_uuid,
            'timestamp': calendar.timegm(event.timestamp.utctimetuple()),
        }
        logger.debug("Formed dummy delete message '%s'...", msg)

        new_event, _ = deserialize_event(msg)
        new_event.last_event_id = event.id
        new_event.file_id = file.id
        new_event.state = 'downloaded'
        if add_to_dummies:
            if hasattr(self, "_dummy_deletes"):
                # many dummy deletes
                self._dummy_deletes.append(self.db.get_mapping(new_event))
            else:
                # one dummy delete
                session.add(new_event)
        return new_event

    def _update_excluded_dirs(self, fs, excluded_dirs, session=None,
                              signals=None, change_in_db=True):
        assert self.event.is_folder

        if not excluded_dirs:
            return

        logger.debug("Updating excluded dirs")
        if self.event.type == 'delete':
            src_path = self.db.get_path_by_events(self.event, session)
            dst_path = None
        else:   # self.event.type == 'move'
            try:
                prev_event = self.event.file.events[-2]
            except IndexError:
                logger.warning("No prev event for %s", self.event)
                src_path = ""
            else:
                src_path = self.db.get_path_by_events(prev_event, session)
            dst_path = self.db.get_path_by_events(self.event, session)
        dirs_to_delete, dirs_to_add = fs.get_excluded_dirs_to_change(
            excluded_dirs, src_path, dst_path)
        if not dirs_to_delete:
            return

        if dirs_to_add:
            change_in_db = False

        signals.change_excluded_dirs.emit(dirs_to_delete, dirs_to_add)
        if change_in_db:
            for path in dirs_to_delete:
                self._mark_dir_not_excluded(path, session)

    def _mark_dir_not_excluded(self, path, session):
        try:
            folders = self.db.find_folders_by_future_path(
                path, session=session, include_deleted=True)
        except Exception:
            logger.error("Error finding folders %s by path", path)
            return
        assert folders, "Excluded dir has to be in db"

        for folder in folders:
            if folder.excluded:
                folder.excluded = False
                self.db.mark_child_excluded(folder.id, session, is_excluded=False)

    def _apply_folder_delete_if_any(self, session, fs):
        path = self.event.file.path
        actual_file_id = fs.get_actual_events_file_id(
            path, is_folder=True)
        logger.debug("Trying to delete folder %s "
                     "with actual events_file_id %s...",
                     path, actual_file_id)
        delete_events = session.query(Event) \
            .filter(Event.file_id == actual_file_id) \
            .filter(Event.type == 'delete') \
            .filter(Event.state == 'downloaded') \
            .all()
        delete_events = sorted(filter(lambda e: not e.file.event_id or
                                                e.id > e.file.event_id,
                                      delete_events),
                               key=lambda e: e.server_event_id)
        if not delete_events:
            return False

        delete_events[-1].file.event_id = delete_events[-1].id
        fs.accept_delete(path, is_directory=True,
                         events_file_id=actual_file_id)
        self.change_processing_events_counts.emit(0, -1)
        return True

    def _generate_uuid(self, session):
        while True:
            uuid = generate_uuid()
            events = session.query(Event).filter(Event.uuid == uuid).all()
            if not events:
                return uuid
            logger.warning("Events with uuid '%s' exist %s", uuid, events)

    def _process_parent_not_found(self, session):
        with self.db.db_lock:
            next_events = list(filter(
                lambda e: e.id > self.event.id, self.event.file.events))
            move_delete_exists = any(e.type in ('move', 'delete')
                                     for e in next_events)
            if move_delete_exists:
                # don't do anything
                return False

            if self.event.is_folder:
                self._add_dummy_delete_events(session)
                delattr(self, "_dummy_deletes")

            server_event_id = self._events_queue.get_min_server_event_id()
            self._add_dummy_delete(
                self.event.file, self.event, server_event_id, session)
            return True

    def _check_previous_delete(self, event, file_events, session,
                               events_queue, fs):
        return False, False, None

    @atomic
    def check_previous_delete(self, session=None, events_queue=None, fs=None):
        event = session.query(Event) \
            .filter(Event.id == self.event.id) \
            .one_or_none()
        if not event:
            return False

        has_deletes, \
        add_dummy, \
        new_delete_event = self._check_previous_delete(
            event, event.file.events, session, events_queue, fs)

        if not has_deletes:
            return False

        if new_delete_event:
            new_delete_event.file_id = event.file_id
            session.add(new_delete_event)

        if add_dummy:
            min_server_event_id = events_queue.get_min_server_event_id()
            self._add_dummy_delete(
                event.file, event, min_server_event_id, session)
            events_queue.change_processing_events_counts(
                remote_inc=1)

        return True

    @atomic
    def add_dummy_if_parent_deleted(self, session=None, events_queue=None):
        logger.debug("Adding dummy when parent is deleted...")
        event = session.query(Event) \
            .filter(Event.id == self.event.id) \
            .one_or_none()
        has_deletes = any(e.type == 'delete' for e in event.file.events)
        if not event or has_deletes:
            return False

        folder = self.find_folder_by_uuid(session, event.folder_uuid)
        if not folder or not folder.is_deleted_registered:
            return False

        if not self.event.file.folder_id:
            self.event.file.folder_id = folder.id
        min_server_event_id = events_queue.get_min_server_event_id()
        self._add_dummy_delete(
            event.file, event, min_server_event_id, session)
        events_queue.change_processing_events_counts(
            remote_inc=1)
        return True
