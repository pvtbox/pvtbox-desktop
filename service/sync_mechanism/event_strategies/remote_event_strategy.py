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
import time

from os.path import join, exists

from service.events_db import File, Event
from service.sync_mechanism.event_strategies.exceptions import EventAlreadyAdded, FolderUUIDNotFound
from common.utils import get_copies_dir, benchmark, get_local_time_from_timestamp
from common.constants import MIN_DIFF_SIZE, DOWNLOAD_PRIORITY_FILE, \
    DOWNLOAD_PRIORITY_REVERSED_PATCH

from .event_strategy import EventStrategy, atomic
from .exceptions import ProcessingAborted, ParentDeleted

from common.path_utils import is_contained_in_dirs


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class RemoteEventStrategy(EventStrategy):
    """docstring for RemoteEventStrategy"""

    def __init__(self, db, event, last_server_event_id,
                 get_download_backups_mode, is_smart_sync=False):
        super(RemoteEventStrategy, self).__init__(db, event,
                                                  get_download_backups_mode,
                                                  is_smart_sync)
        self._last_server_event_id = last_server_event_id
        if not hasattr(self, '_must_download_copy'):
            self._must_download_copy = False
        self.file_download = not self._download_backups or self.is_file_download()
        self._download_priority = DOWNLOAD_PRIORITY_FILE

    ''' Functions to overload in descendants ==================================
    '''

    def is_file_download(self):
        return False

    def _ready_to_apply(self, session, is_deleted=False, files=None):
        if self.event.file.folder and not self.event.file.folder.event:
            return False
        if files is None:
            path = self._get_target_path(session)
            files = self.db.find_files_by_relative_path(path,
                                                        on_parent_not_found=lambda: False,
                                                        session=session,
                                                        include_deleted=False)
        else:
            files = [f for f in files if f and not f.is_deleted]

        if files is False:
            from .move_file_strategy import RemoteMoveFileStrategy
            from .move_folder_strategy import RemoteMoveFolderStrategy
            if self.event.type != 'delete' and \
                    not isinstance(self, (RemoteMoveFileStrategy,
                                          RemoteMoveFolderStrategy)):
                raise ParentDeleted
        else:
            for file in files:
                if file.id == self.event.file.id:
                    continue
                if (file.event_id
                        and file.events[-1].id != file.event_id
                        and file.events[-1].state in ('received', 'downloaded')
                        and file.events[-1].type != 'delete'
                        or any(e.state in ('occured', 'conflicted')
                               for e in file.events)):
                    return False

        if (self.file_download and
                not self.event.file.is_locally_modified and
                (not is_deleted or self._must_download_copy)
                and self.event.state == 'downloaded'):
            return True

        return super(RemoteEventStrategy, self)._ready_to_apply(
            session, is_deleted)

    def _get_file(self, session, excluded_dirs=(), initial_sync=False):
        files = self._get_files_by_uuid(session)
        if not files:
            return self.create_file(
                self.event, self.event.is_folder, session,
                excluded_dirs=excluded_dirs, initial_sync=initial_sync,
                check_by_uuid=False)
        return files[0]

    def _get_files_by_uuid(self, session):
        assert self.event.file_uuid
        files = session.query(File) \
            .filter(File.uuid == self.event.file_uuid) \
            .all()
        assert len(files) < 2
        return files

    def create_file(self, event, is_folder, session, excluded_dirs=(),
                    initial_sync=False, check_by_uuid=True):
        if check_by_uuid:
            files = self._get_files_by_uuid(session)
            if files:
                file = files[0]
                file.last_skipped_event_id = None
                return file

        if event.file_id:
            return session.query(File).filter(File.id == event.file_id).one()

        try:
            folder = self.find_folder_by_uuid(session, event.folder_uuid)
        except FolderUUIDNotFound:
            folder = None
            logger.warning("No parent for event's %s file", event)

        file = File(name=event.file_name,
                    uuid=event.file_uuid,
                    is_folder=is_folder)
        file.folder = folder
        if folder:
            file.folder_id = folder.id
        file.excluded = (folder and folder.excluded or
                is_contained_in_dirs(file.path, excluded_dirs))
        if self._is_smart_sync:
            file.is_offline = folder is not None and folder.is_offline
        return file

    def _get_last_event_id(self, session):
        #        assert self._last_server_event_id
        if not self._last_server_event_id:
            return None
        last_event_id = session.query(Event.id) \
            .filter(Event.server_event_id == self._last_server_event_id) \
            .scalar()

        return last_event_id

    def _get_max_file_server_event_id(self, file):
        events = list(filter(lambda e: e.server_event_id, file.events))
        if not events:
            return 0

        max_event = max(events, key=lambda e: e.server_event_id)
        logger.debug("max_event %s", max_event)
        return max_event.server_event_id

    ''' Public methods templates ==============================================
    '''
    def event_is_known(self, session):
        event = self.event
        assert event.server_event_id, "Remote event must already have an ID"
        if event.server_event_id < 0:
            return False

        events = session.query(Event).filter(Event.uuid == event.uuid).all()

        return len(events) > 0

    def postpone_after_save(self):
        ''' Do not try process event immediatelly.
            Possible next events for the file will remove it'''
        return True

    @atomic
    def add_to_local_database(self, session, patches_storage,
                              copies_storage, events_queue=None,
                              excluded_dirs=(),
                              fs=None, initial_sync=False):
        event = self.event
        logger.debug('storing remote event to db: %s', event)
        assert not event.id, "Event must not be already saved to db"
        assert not event.state, "status can't be assigned without saving"
        assert event.server_event_id, "Remote event must already have an ID"

        if self.event_is_known(session):
            logger.debug("Event is already known: %s", event)
            raise EventAlreadyAdded()

        event.file = self._get_file(session, excluded_dirs,
                                    initial_sync=initial_sync)
        assert event.file

        if 0 < event.server_event_id < \
                self._get_max_file_server_event_id(event.file):
            logger.debug("Have newer event in db, than %s", event)
            raise EventAlreadyAdded()

        if event.type == 'delete' and event.file.events \
            and any(map(lambda e: e.server_event_id and
                                  e.server_event_id > event.server_event_id,
                        event.file.events)):
            raise EventAlreadyAdded()

        if event.erase_nested:  # collaboration start/finish
            self._erase_nested(event.file_uuid, patches_storage,
                               copies_storage, fs, session=session,
                               events_queue=events_queue,
                               excluded_dirs=excluded_dirs)
            session.query(Event) \
                .filter(Event.file_uuid == event.file_uuid) \
                .filter(Event.id.isnot(None)) \
                .delete()
            event.file.event_id = None

        if event.file.excluded:
            events_queue.set_recalculate()

        event.last_event_id = self._get_last_event_id(session)
        self._update_copy_referencies(event, copies_storage)

        self._set_event_state(event)

        if not event.erase_nested:
            already_deleted, \
            add_dummy, \
            new_delete_event = self._check_previous_delete(
                event, event.file.events, session, events_queue, fs)
        else:
            already_deleted = add_dummy = False
            new_delete_event = None

        if event.type == 'update' and self._download_backups and \
                event.file.is_offline:
            self._process_update_adding(event, patches_storage)
        elif event.type in ('delete', 'move') and event.is_folder and \
                not event.erase_nested:
            self._process_folder_move_delete_adding(
                event, fs, events_queue, excluded_dirs, already_deleted,
                session)

        if event.type == 'restore':
            self._process_restore_adding(event)
            events_queue.change_processing_events_counts(
                remote_inc=len(event.file.events) - 1)

        session.flush()
        event.file_id = event.file.id
        if event.is_folder and event.type != 'delete':
            self._set_parents(
                event.file_id, event.file_uuid, session,
                event.file.excluded, event.file.is_offline)

        session.add(event)
        if new_delete_event:
            new_delete_event.file_id = event.file_id
            session.add(new_delete_event)
        session.flush()
        if (event.type in ('update', 'delete') or
            event.type == 'move' and self.file_download) and \
                not event.is_folder:
            events_queue.cancel_file_download(event.file_id, session,
                                              to_skip=self.file_download,
                                              previous_only=True,
                                              server_event_id=
                                              event.server_event_id)

        if event.type == 'move':
            remote_inc = self._check_offline(session)
            events_queue.change_processing_events_counts(
                remote_inc=remote_inc)

        if add_dummy:
            min_server_event_id = events_queue.get_min_server_event_id()
            self._add_dummy_delete(
                event.file, event, min_server_event_id, session)
            events_queue.change_processing_events_counts(
                remote_inc=1)

        logger.debug('remote event is stored to db: %s', event)

    def _update_copy_referencies(self, event, copies_storage):
        if event.type != 'delete' and not event.is_folder:
            new_hash = event.file_hash
            old_hash = event.file_hash_before_event \
                if event.file_size_before_event else None
        else:
            new_hash = old_hash = None

        file_size = event.file_size

        if not event.last_event_id:     # have missed events
            if event.type == 'delete' and not event.is_folder:
                new_hash = event.file_hash_before_event
                file_size = event.file_size_before_event
            if event.file.event_id and not event.is_folder:
                old_hash = event.file.event.file_hash \
                    if event.file.event.file_size \
                    else event.file.event.file_hash_before_event \
                    if event.file.event.file_size_before_event else None
            else:
                old_hash = None

        if file_size and new_hash:
            copies_storage.add_copy_reference(
                new_hash,
                reason="add_to_local_database. Event {}. "
                       "File {}".format(event.uuid, event.file_name),
                postponed=True)

        if old_hash:
            copies_storage.remove_copy_reference(
                old_hash,
                reason="add_to_local_database. Event {}. "
                       "File {}".format(event.uuid, event.file_name),
                postponed=True)

    def _set_event_state(self, event):
        event.state = (
            'received' if event.type in ('create', 'update', 'restore')
            and (event.diff_file_size > 0 or event.file_size > 0)
            or event.type == 'delete' and self._must_download_copy
            or event.type == 'move' and not event.file.event_id and
            not event.file.last_skipped_event_id and event.file_size > 0
            else 'downloaded')

    def _process_update_adding(self, event, patches_storage):
        assert event.diff_file_uuid, \
            "Update remote event should have diff_file_uuid"
        assert event.rev_diff_file_uuid, \
            "Update remote event should have rev_diff_file_uuid"
        assert event.file_hash_before_event, \
            "Update remote event should have file_hash_before event"

        if not event.outdated:
            if patches_storage.patch_exists(event.diff_file_uuid):
                event.diff_file_size = patches_storage.get_patch_size(
                    event.diff_file_uuid)
            if patches_storage.patch_exists(event.rev_diff_file_uuid):
                event.rev_diff_file_size = \
                    patches_storage.get_patch_size(
                        event.rev_diff_file_uuid)
            if event.file_size >= MIN_DIFF_SIZE:
                patches_storage.add_direct_patch(
                    event.diff_file_uuid, event.file_hash,
                    event.file_hash_before_event, event.diff_file_size,
                    active=False,
                    reason="add_to_local_database. Event {}. File {}"
                        .format(event.uuid, event.file_name),
                    postponed=True)
            if event.file_size_before_event:
                patches_storage.add_reverse_patch(
                    event.rev_diff_file_uuid, event.file_hash_before_event,
                    event.file_hash, event.rev_diff_file_size,
                    active=False,
                    reason="add_to_local_database. Event {}. File {}"
                        .format(event.uuid, event.file_name),
                    postponed=True)

    def _process_folder_move_delete_adding(self, event, fs, events_queue,
                                           excluded_dirs, already_deleted,
                                           session):
        self._events_queue = events_queue
        if event.type == 'delete' and not already_deleted:
            t = time.time()
            self._add_dummy_delete_events(session=session)
            interval = time.time() - t
            logger.debug("Dummy deletes added in %s seconds", interval)

        self._update_excluded_dirs(fs, excluded_dirs, session,
                                   signals=self._events_queue)

    def _process_restore_adding(self, event):
        event.type = 'create'
        event.file.event = None
        event.file.event_id = None

    def _set_parents(self, folder_id, folder_uuid, session,
                     is_excluded, is_offline):
        files = session.query(File) \
            .filter(File.event_id.is_(None)) \
            .filter(File.folder_id.is_(None)) \
            .filter(Event.file_id == File.id) \
            .filter(Event.folder_uuid == folder_uuid) \
            .group_by(File.id) \
            .all()
        if not files:
            return

        session.bulk_update_mappings(
            File, [
                {'id': f.id, 'folder_id': folder_id}
                for f in files])
        if is_excluded:
            self.db.mark_child_excluded(folder_id, session)
        if is_offline:
            self.db.mark_child_offline(folder_id, session)

    @atomic
    def download(self,
                 session,
                 download_manager,
                 fs,
                 patches_storage,
                 signals,
                 reenter_event):
        if self.event.diff_file_size == 0 and self.event.file_size == 0 or \
                not self.event.file.is_offline:
            self.event.state = 'downloaded'
            return True

        if not hasattr(self, 'download_success'):
            if not self.begin_download(download_manager, fs,
                                       patches_storage,
                                       signals,
                                       reenter_event,
                                       session=session):
                return False

        if self.download_success:
            self.event.state = 'downloaded'

        return True

    @benchmark
    def begin_download(self,
                       download_manager,
                       fs,
                       patches_storage,
                       signals,
                       reenter_event,
                       session=None):

        def on_load_task_success(task):
            assert self.event.state == 'received'
            logger.info(
                "Download task for obj_id '%s' completed successfully",
                task.id)

            duration = time.time() - task_start_time

            signals.downloaded.emit(
                self,
                duration,
                0,  # stats.get('received_webrtc_p2p', 0),
                0)  # stats.get('received_webrtc_relayed', 0))
            self.download_success = True
            reenter_event(self, add_to_start=True)

        def on_load_task_failure(task):
            logger.error(
                "Failed to complete download task for obj_id '%s'", task.id)

            signals.download_failed.emit(self)

        if not self.file_download:
            return self._begin_patch_download(patches_storage)
        else:
            if self._has_newer_updates():
                signals.download_failed.emit(self)
                return False

            task_start_time = time.time()

            return self._begin_file_download(download_manager, fs, signals,
                                             on_load_task_success,
                                             on_load_task_failure,
                                             session)

    def _begin_patch_download(self, patches_storage):
        assert self.event.diff_file_uuid
        if patches_storage.patch_exists(self.event.diff_file_uuid):
            self.download_success = True
        else:
            patches_storage.activate_patch(self.event.diff_file_uuid)
            self.download_success = False
        return self.download_success

    def _begin_file_download(self, download_manager, fs, signals,
                             on_load_task_success, on_load_task_failure,
                             session=None):
        from .update_file_strategy import RemoteUpdateFileStrategy
        from .delete_file_strategy import RemoteDeleteFileStrategy
        assert self.event.file_hash or self.event.file_hash_before_event

        size = self.event.file_size
        hash = self.event.file_hash if self.event.file_hash \
            else self.event.file_hash_before_event
        path = join(get_copies_dir(fs.get_root()), hash)

        if exists(path):
            self.download_success = True
            return self.download_success
        else:
            self.download_success = False

        logger.debug(
            'Starting downloading file: %s into: %s, size:%s (event uuid: %s)',
            self.event.file.name, path, size, self.event.uuid)

        signals.downloading_started.emit(self)

        is_silent = self.event.type == 'delete' or \
            self.file_will_be_deleted()
        priority = DOWNLOAD_PRIORITY_REVERSED_PATCH if is_silent \
            else DOWNLOAD_PRIORITY_FILE
        display_name = 'Syncing backup for file {}' if is_silent \
            else 'Syncing file {}'
        display_name = display_name.format(
            self.event.file_name)
        target_file_path = self.db.get_path_from_event(self.event, session)
        files_info = [{
            "target_file_path" : target_file_path,
            "mtime": get_local_time_from_timestamp(self.event.timestamp),
            "is_created": not isinstance(self, RemoteUpdateFileStrategy),
            "is_deleted": isinstance(self, RemoteDeleteFileStrategy)}]

        download_manager.add_file_download(
            priority, self.event.uuid, self.event.file_size,
            hash, path, display_name,
            on_load_task_success, on_load_task_failure,
            files_info)

        return False

    def _has_newer_updates(self):
        newer_updates = list(filter(
            lambda e: e.type == 'update' and e.id > self.event.id,
            self.event.file.events))
        return newer_updates

    def check_event_path_excluded(self, excluded_dirs):
        event = self.event
        if not event.file:
            return False
        event_path = event.file.path
        return is_contained_in_dirs(event_path, excluded_dirs)

    def _accept_delete_on_restore(self, fs, file, events_queue):
        num_tries = 5
        for i in range(num_tries):
            if not events_queue.file_in_processing(file.id):
                break
            time.sleep(0.5)
        logger.debug("_accept_delete_on_restore file %s", file.path)
        fs.accept_delete(file.path, file.is_folder, file.id,
                         is_offline=file.is_offline)

    def _check_previous_delete(self, event, file_events, session,
                               events_queue, fs):
        add_dummy = False
        new_delete_event = None
        local_inc = 0
        file_event_id = event.file.event_id if event.file.event_id else 0
        events_to_check = list(filter(
            lambda e: e.id, file_events)) if not file_event_id else \
            list(filter(lambda e: e.id and e.id >= file_event_id, file_events))
        # if we have non-local deletes, cause local are processed in conflicts
        has_deletes = [e for e in events_to_check
                       if e.type == 'delete' and
                       e.state not in ('occured', 'conflicted') and
                       e.uuid != event.uuid]
        if not has_deletes:
            return False, add_dummy, new_delete_event

        events_to_check = sorted(events_to_check, key=lambda e: e.id)
        deleted_count = self._delete_deletes(event, events_to_check, session)

        if event.type == 'restore' and events_to_check[0].type != 'delete':
            # we have not accepted delete
            self._accept_delete_on_restore(fs, event.file, events_queue)
        elif event.type in ('update', 'move'):
            file_folder = event.file.folder
            file_folder_deleted = file_folder and file_folder.is_deleted
            if event.type == 'move':
                new_folder = self.find_folder_by_uuid(session, event.folder_uuid)
                new_folder_deleted = new_folder and new_folder.is_deleted
                if file_folder_deleted and not new_folder_deleted:
                    # have remote move after local parent's delete
                    new_delete_event = Event(
                        type='delete',
                        state='occured',
                        is_folder=event.is_folder,
                        file_size=event.file_size,
                        file_size_before_event=event.file_size_before_event,
                        diff_file_size=event.diff_file_size,
                        rev_diff_file_size=event.rev_diff_file_size,
                        file_hash=event.file_hash,
                        file_name=event.file_name,
                        uuid=self._generate_uuid(session),
                        file_uuid=event.file_uuid,
                        last_event=event,
                    )
                    deleted_count += 1
                    local_inc = 1
            else:
                new_folder_deleted = True
            add_dummy = file_folder_deleted and new_folder_deleted

        events_queue.change_processing_events_counts(
            remote_inc=-deleted_count, local_inc=local_inc)
        return True, add_dummy, new_delete_event

    def _delete_deletes(self, event, events_to_check, session):
        last_event_id = None
        deleted_count = 0
        count = 0
        for one_event in events_to_check:
            if one_event.uuid == event.uuid:
                continue

            if one_event.type == 'delete':
                if not one_event.last_event:
                    one_event.last_event_id = None
                if not last_event_id:
                    last_event_id = one_event.last_event_id
                if one_event.id != event.file.event_id:
                    deleted_count += 1
                else:
                    event.file.event_id = one_event.last_event_id
                session.delete(one_event)
                count += 1
            else:
                if last_event_id:
                    one_event.last_event_id = last_event_id
                last_event_id = None
        if last_event_id:
            event.last_event_id = last_event_id
        logger.debug("Deleted %s previuos deletes", count)
        return deleted_count

    def _erase_nested(self, folder_uuid, patches_storage, copies_storage, fs,
                      session=None, events_queue=None, excluded_dirs=()):

        def deleted_files_page_processor_cb(files_page,
                                            folders_uuids, session):
            local_deleted = remote_deleted = 0
            for file in files_page:
                if events_queue.is_processing_stopped():
                    raise ProcessingAborted
                logger.debug("Erasing file %s", file)
                events_queue.cancel_file_download(file.id, session)
                events = sorted(file.events, key=lambda e: e.id)
                logger.debug("Erasing file events %s", events)
                file_event_id = 0 if not file.event_id and \
                                        not file.last_skipped_event_id \
                    else file.event_id if not file.last_skipped_event_id \
                    else file.last_skipped_event_id if not file.event_id \
                    else max(file.event_id, file.last_skipped_event_id)
                hashes_to_remove = []
                local_hash = remote_hash = None
                for event in events:
                    if event.file_size:
                        if event.state in ('received', 'downloaded'):
                            # decrease copy reference for max remote event
                            remote_hash = event.file_hash if event.file_hash \
                                else event.file_hash_before_event
                        elif (event.state in ('occured', 'conflicted') and
                              event.type not in ('move', 'delete')):
                            # and for all non-registered local events
                            hashes_to_remove.append(event.file_hash)
                        elif (event.state == 'sent' and
                              event.type not in ('move', 'delete')):
                            # and for max registered local event
                            local_hash = event.file_hash

                    if event.type == 'update':
                        patches_storage.remove_direct_patch(
                            event.diff_file_uuid,
                            reason="_erase_nested. Event {}. File {}"
                                .format(event.uuid, event.file_name))
                        patches_storage.remove_reverse_patch(
                            event.rev_diff_file_uuid,
                            reason="_erase_nested. Event {}. File {}"
                                .format(event.uuid, event.file_name))
                    if event.id > file_event_id:
                        if event.type in ("occured", "conflicted"):
                            local_deleted +=1
                        else:
                            remote_deleted +=1
                    session.delete(event)
                    logger.debug("Erased event %s", event)
                    events_queue.inc_events_erased()

                if remote_hash:
                    hashes_to_remove.append(remote_hash)
                if local_hash:
                    hashes_to_remove.append(local_hash)
                for file_hash in hashes_to_remove:
                    copies_storage.remove_copy_reference(
                        file_hash,
                        reason="_erase_nested. File uuid {}. File {}"
                            .format(file.uuid, file.name),
                        postponed=True)
                fs.change_events_file_id(file.id, None)
                session.delete(file)
                logger.debug("Erased file %s", file)

            events_queue.change_processing_events_counts(
                local_inc=-local_deleted, remote_inc=-remote_deleted)

        self._update_excluded_dirs(fs, excluded_dirs, session,
                                   signals=events_queue,
                                   change_in_db=True)
        if self.event.type in ("create", "move"):
            path = self.event.file.path
            if (not self.event.file.id or
                not fs.is_file_in_storage(self.event.file.id)) and \
                    fs.path_exists(path, self.event.file.is_offline):
                logger.debug("Moving folder to create collaboration %s", path)
                conflicted_name = fs.generate_conflict_file_name(
                    path, is_folder=True)
                try:
                    fs.move_file(path, conflicted_name)
                except fs.Exceptions.AccessDenied:
                    logger.error("Can't move collaboration folder %s. "
                                   "Reason: Access denied", path)
                    raise
                except Exception as e:
                    logger.warning("Can't move collaboration folder %s. "
                                   "Reason: %s", path, e)
            else:
                fs.accept_delete(self.event.file.path, is_directory=True,
                                 events_file_id=self.event.file.id)
        events_queue.clear_events_erased()
        self.db.get_files_by_folder_uuid(folder_uuid,
                                         deleted_files_page_processor_cb,
                                         include_folders=True,
                                         session=session)
        events_queue.clear_events_erased()

    def _create_file_from_copy(self, path, fs, search_by_id=False):
        try:
            fs.create_file_from_copy(path,
                                     self.event.file_hash,
                                     self.event.file_id,
                                     search_by_id=search_by_id)
            logger.debug("Create file %s from copy", path)
        except Exception as e:
            logger.error("Can't create file from copy. Reason: %s", e)
            if not self._copies_storage.copy_exists(self.event.file_hash):
                self.event.state = 'received'
                self._must_download_copy = True
                self.file_download = self.is_file_download()
                if hasattr(self, 'download_success'):
                    delattr(self, 'download_success')
            else:
                raise e
