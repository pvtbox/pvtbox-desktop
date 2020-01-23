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

import time
import logging
from itertools import chain
from collections import  deque

from os.path import exists, getsize
from contextlib import contextmanager
from threading import RLock

from sqlalchemy import create_engine, func, or_, not_, and_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import exc
from sqlalchemy.orm import aliased
from sqlalchemy.orm.session import Session as Session
from sqlalchemy.sql import text as sql_text
from sqlalchemy.exc import OperationalError, ResourceClosedError
import re

from service.events_db.base import Base
from service.events_db.event import Event
from service.events_db.file import File

from service.network.browser_sharing import ProtoError

from common.constants import MIN_DIFF_SIZE, DB_PAGE_SIZE
from common.signal import Signal
from common.utils import is_db_or_disk_full, benchmark, log_sequence
from common.file_path import FilePath

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class FileEventsDBError(Exception):
    """
    Exception base class for FileEventsDB
    """

    pass


class FolderNotFound(FileEventsDBError):
    def __init__(self, folder_path):
        super(FolderNotFound, self).__init__(
            "Folder not found by path '{}'".format(folder_path))


def raise_folder_not_found(folder_path):
    raise FolderNotFound(folder_path)


class FileNotFound(FileEventsDBError):
    def __init__(self, file_path):
        super(FileNotFound, self).__init__(
            "File not found by path '{}'".format(file_path))


def raise_file_not_found(file_path):
        raise FileNotFound(file_path)


class FileInProcessing(FileEventsDBError):
    def __init__(self, file_path):
        super(FileInProcessing, self).__init__(
            "File (folder) is in processing {}".format(file_path))


class FolderUUIDNotFound(FileEventsDBError):
    def __init__(self, folder_uuid):
        super(FolderUUIDNotFound, self).__init__(
            "Folder not found by uuid '{}'".format(folder_uuid))


class EventsDbBusy(FileEventsDBError):
    def __init__(self):
        super(EventsDbBusy, self).__init__(
            "Events db busy. Can' aquire lock.")


def with_session(func):
    """
    Decorator to make session if it is need

    Args:
        func: decorated function
    """
    def impl(self, *args, **kwargs):
        read_only = kwargs.pop('read_only', True)
        session_arg = 'session'
        if session_arg in kwargs and kwargs[session_arg]:
            return func(self, *args, **kwargs)

        for arg in args:
            if isinstance(arg, Session):
                return func(self, *args, **kwargs)

        with self.create_session(read_only=read_only) as session:
            kwargs[session_arg] = session
            return func(self, *args, **kwargs)

    return impl


class FileEventsDB(object):
    """
    Local file events DB class
    """

    _lock = RLock()

    def __init__(self, count_sessions=False):
        self._engine = None
        self._Session = None
        self._db_file = ''
        self.db_or_disk_full = Signal()

        self.db_lock = RLock()

        self._count_sessions = count_sessions
        self._sessions_count = 0
        self._sessions_count_lock = RLock()

    def open(self, filename=':memory:', echo=False):
        """
        Opens file event DB at path specified. Initializes DB if necessary

        @param filename Filename of DB [unicode]
        @param echo Flag enables SQLAlchemy engine logging [bool]
        @raise FileEventsDBError
        """

        self._db_file = filename
        logger.info("Opening event DB from '%s'...", filename)

        conn_string = 'sqlite:///{}'.format(FilePath(filename))
        try:
            self._engine = create_engine(
                conn_string, echo=echo, connect_args={
                    'timeout': 60*1000,
                    'check_same_thread': False,
                })
            self._engine.pool_timeout = 60*60*1000
            self._Session = sessionmaker(bind=self._engine)
            # Create DB schema if necessary
            Base.metadata.create_all(self._engine, checkfirst=True)
        except Exception as e:
            logger.critical(
                "Failed to open event DB from '%s' (%s)", filename, e)
            raise FileEventsDBError("Failed to open event DB file")

    def clean(self):
        assert self._Session is not None, 'DB has not been opened'
        try:
            self._engine.execute("delete from events")
            self._engine.execute("delete from files")
            logger.info("Cleaned events data base")
        except Exception as e:
            logger.error("Failed to clean DB (%s)", e)
            if not self.db_file_exists():
                raise e

    @contextmanager
    def create_session(self,
                       expire_on_commit=True,
                       enable_logging=True,
                       read_only=False,
                       pre_commit=None,
                       pre_rollback=None):
        """
        Creates new DB session and returns context manager for it

        @param expire_on_commit Flag forcing ORM objects expiration after
            session closing [bool]
        @return Context manager for created DB session
        """

        def commit():
            pre_commit()
            original_commit()

        def rollback():
            pre_rollback()
            original_rollback()

        assert self._Session is not None, 'DB has not been opened'

        if self._count_sessions:
            with self._sessions_count_lock:
                self._sessions_count += 1
        session = self._Session()
        session.expire_on_commit = expire_on_commit
        session.autoflush = False

        if callable(pre_commit):
            original_commit = session.commit
            session.commit = commit

        if callable(pre_rollback):
            original_rollback = session.rollback
            session.rollback = rollback

        if read_only:
            session.flush = lambda: None
        # else:
        #     _flush = session.flush
        #
        #     def locked_flush():
        #         with self._lock:
        #             _flush()
        #     session.flush = locked_flush
        #
        #     _commit = session.commit
        #
        #     def locked_commit():
        #         with self._lock:
        #             _commit()
        #     session.commit = locked_commit
        #
        #     _rollback = session.rollback
        #
        #     def locked_rollback():
        #         with self._lock:
        #             _rollback()
        #     session.rollback = locked_rollback

        if enable_logging:
            logger.debug("DB session %s created", hex(id(session)))

        try:
            yield session

            if session.transaction:
                self._clear_events(session, read_only)
                session.commit()
                if enable_logging:
                    logger.debug("DB session %s commited", hex(id(session)))
        except OperationalError as e:
            if session.transaction:
                try:
                    self._clear_events(session, read_only)
                    session.rollback()
                except Exception:
                    pass
                logger.debug("DB session %s rolled back (%s)",
                             hex(id(session)), e)
            if is_db_or_disk_full(e):
                self.db_or_disk_full.emit()
            else:
                raise
        except Exception as e:
            if session.transaction:
                self._clear_events(session, read_only)
                session.rollback()
                logger.debug("DB session %s rolled back (%s)",
                             hex(id(session)), e)
            raise
        finally:
            session.close()
            if self._count_sessions:
                with self._sessions_count_lock:
                    self._sessions_count -= 1

    def _clear_events(self, session, read_only):
        if read_only:
            session.expunge_all()

    @contextmanager
    def soft_lock(self, timeout_sec=2):
        time_step = 0.2
        num_tries = int(timeout_sec / time_step) + 1

        lock_acquired = False
        try:
            for i in range(num_tries):
                lock_acquired = self.db_lock.acquire(blocking=False)
                if lock_acquired:
                    break
                time.sleep(time_step)
            else:
                raise EventsDbBusy

            yield

        except Exception:
            raise
        finally:
            if lock_acquired:
                self.db_lock.release()

    @with_session
    def get_max_server_event_id(self, session=None):
        """
        Returns maximum server_event_id stored in DB

        @return Value of max known server_event_id [int]
        """

        result = session.query(func.max(Event.server_event_id)).scalar()

        return result if result is not None else 0

    @with_session
    def get_max_server_event_uuid(self, session=None):
        """
        Returns event_uuid for maximum server_event_id stored in DB

        @return Value of event_uuid [str]
        """

        max_id = self.get_max_server_event_id(session=session)
        result = None
        if max_id:
            result = session.query(Event.uuid)\
                .filter(Event.server_event_id == max_id)\
                .scalar()

        return result

    @with_session
    def get_max_checked_server_event_id(self, session=None):
        """
        Returns maximum checked server_event_id stored in DB

        @return Value of max known checked server_event_id [int]
        """

        result = session.query(func.max(Event.server_event_id)) \
            .filter(Event.checked == 1) \
            .scalar()

        return result if result is not None else 0

    @with_session
    def get_events_count(self, from_id, to_id, session=None):
        """
        Returns count of events between from_id and to_id

        @return count of events between from_id and to_id [int]
        """
        result = session.query(func.count()) \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.server_event_id > from_id) \
            .filter(Event.server_event_id < to_id) \
            .scalar()

        return result if result is not None else 0

    def get_min_server_event_id(self):
        """
        Returns minimum server_event_id stored in DB

        @return Value of min known server_event_id [int]
        """

        with self.create_session(read_only=True) as session:
            result = session.query(func.min(Event.server_event_id)).scalar()

        return result if result is not None else 0

    @with_session
    def set_event_checked(self, event_uuid, server_event_id,
                          session=None, read_only=False):
        logger.debug("set event %s checked", event_uuid)
        event = session.query(Event) \
            .filter(Event.uuid == event_uuid) \
            .filter(Event.server_event_id == server_event_id) \
            .one_or_none()
        if event:
            event.checked = True

    def event_exists(self, server_event_id):
        with self.create_session(read_only=True) as session:
            count = session.query(func.count(1)) \
                .select_from(Event) \
                .filter(Event.server_event_id == server_event_id) \
                .scalar()

            return bool(count)

    def is_diff_file_uuid_known(self, uuid):
        """
        Checks whether event with specified diff file uuid is stored in DB

        @return Check result [bool]
        """

        with self.create_session(read_only=True) as session:
            direct_patches = session.query(func.count(Event.id))\
                .filter(Event.diff_file_uuid == uuid).scalar()
            reverse_patches = session.query(func.count(Event.id))\
                .filter(Event.rev_diff_file_uuid == uuid).scalar()

        return (direct_patches + reverse_patches) > 0

    def get_file_hash_by_event_uuid(self, event_uuid,
                                    check_is_file_shared=False,
                                    shared_objects_list=[]):
        """
        - Checks that event with specified event_uuid is stored in DB
        - Checks that file is stored in DB according file_id by the event
        - Checks that DB not contains events which change file after the event
        - Checks that event with specified event_uuid is applied
        - Checks that file is shared (if flag 'check_is_file_shared' is True)
        - Checks that file hash is known
        - Returns file hash or raises exception 'UserWarning'

        @return File hash result [unicode]
        """

        with self.create_session(read_only=True) as session:
            file, target_event = \
                self.get_file_and_event_by_event_uuid(event_uuid,
                                                      session=session)
            # check that file is shared
            if check_is_file_shared:
                res = self.is_file_shared(file.uuid, shared_objects_list,
                                          session=session)
                if not res:
                    raise ProtoError("FILE_NOT_SHARED",
                                     "File UUID '{}'".format(file.uuid))

            file_hash = target_event.file_hash if target_event.file_hash \
                else target_event.file_hash_before_event
            # detect not-applied events
            # event = file.event
            # if (event
            #         and event.server_event_id
            #         and target_event.server_event_id
            #         and event.server_event_id > target_event.server_event_id):
            #     raise ProtoError(
            #         "FILE_NOT_SYNCHRONIZED",
            #         "File with UUID '{}' is not synchronized"
            #         .format(file.uuid))

            if self.is_file_changed(target_event, file, session):
                raise ProtoError(
                    "FILE_CHANGED",
                    "File with UUID '{}' has been changed or deleted"
                    .format(file.uuid))

            return file_hash

    @with_session
    def is_file_changed(self, event, file, session=None):
        # detect changes (1)
        changes = session.query(func.count()) \
            .select_from(Event) \
            .filter(Event.file_id == file.id) \
            .filter(Event.id > event.id) \
            .filter(Event.server_event_id.is_(None)) \
            .filter(or_(Event.type == "update", Event.type == "delete")) \
            .scalar()
        if changes > 0:
            return True

        if event.server_event_id and event.server_event_id > 0:
            # not a dummy delete
            # detect changes (2)
            changes = session.query(func.count()) \
                .select_from(Event) \
                .filter(Event.file_id == file.id) \
                .filter(or_(Event.type == "update", Event.type == "delete")) \
                .filter(Event.server_event_id.isnot(None)) \
                .filter(Event.server_event_id > event.server_event_id) \
                .scalar()

        return changes > 0

    @with_session
    def is_file_shared(self, file_uuid, shared_objects_list, session=None):
        """
        Checks that file is shared directly or by parents

        @return Check result [bool]
        """
        lower_q = session.query(File.folder_id, File.uuid) \
            .filter(File.uuid == file_uuid) \
            .cte(name="lower_q", recursive=True)
        Parent = aliased(File)
        Child = aliased(lower_q)
        next_q = session.query(Parent.folder_id, Parent.uuid) \
            .filter(Parent.id == Child.c.folder_id)

        final_q = lower_q.union_all(next_q)
        try:
            res = session.query(final_q).all()
        except Exception as e:
            logger.debug("While checking file for sharing error occured: '%s'",
                         e)
            return False
        uuids_set = set([x[1] for x in res])
        shared_set = set(shared_objects_list)
        cross_set = uuids_set & shared_set
        # logger.debug(uuids_set)
        return len(cross_set) > 0

    @with_session
    def get_file_and_event_by_event_uuid(self,
                                         event_uuid,
                                         session=None):
        """
        """
        if not event_uuid:
            raise ValueError("Wrong UUID")

        events = session.query(Event) \
            .filter(Event.uuid == event_uuid) \
            .all()
        if not events:
            raise ProtoError("UNKNOWN_EVENT_UUID",
                             "Event UUID '{}'".format(event_uuid))
        target_event = events[0]

        try:
            file = session.query(File) \
                .filter(File.id == target_event.file_id) \
                .filter(File.is_folder == 0) \
                .one()
        except exc.NoResultFound:
            raise ProtoError("UNKNOWN_FILE",
                             "Unknown file for Event UUID '{}'"
                             .format(event_uuid))

        return file, target_event

    @with_session
    def get_file_info_by_event_uuid(self, event_uuid, session=None):
        """
        """
        try:
            file, event = self.get_file_and_event_by_event_uuid(
                event_uuid, session)
        except (ValueError, ProtoError) as e:
            logger.warning("can't get file_info. Reason: %s", e)
            return None, None, None, None, None

        target_path = self.get_path_from_event(event, session)
        size = event.file_size if event.type != 'delete' or \
                                  not event.file_size_before_event \
            else event.file_size_before_event
        timestamp = event.timestamp
        is_deleted = event.type == 'delete'
        is_created = not is_deleted and event.type != 'update'

        return target_path, size, timestamp, is_created, is_deleted

    @with_session
    def get_files_list_by_diff_uuid(self, diff_uuid, not_applied_only=True,
                                    last_only=False, direct_patch_only=False,
                                    session=None):
        """
        """
        if not diff_uuid:
            logger.warning("No diff uuid to find file info")
            return None, None

        query = session.query(Event).filter(Event.type == 'update')
        if direct_patch_only:
            query = query.filter(Event.diff_file_uuid == diff_uuid)
        else:
            query = query.filter(or_(
                Event.diff_file_uuid == diff_uuid,
                Event.rev_diff_file_uuid == diff_uuid)) \

        if not_applied_only:
            query = query.filter(Event.state == 'received')
        events = query.all()
        if not events:
            logger.warning("No events for diff uuid %s", diff_uuid)
            return None, None

        if last_only:
            events = [events[-1]]
        files_list = []
        size = events[0].diff_file_size \
            if diff_uuid == events[0].diff_file_uuid \
            else events[0].rev_diff_file_size
        files_uuids = set()
        for event in events:
            file = event.file
            if file.uuid in files_uuids:
                continue

            files_uuids.add(file.uuid)
            info = (file.path, event.timestamp)
            files_list.append(info)
        return files_list, size

    @with_session
    def get_event_by_id(self, event_id, session=None):
        """
        """
        try:
            return session.query(Event) \
                .filter(Event.id == event_id) \
                .one()
        except exc.NoResultFound:
            logger.debug("NoResultFound while getting event wiht ID=%s",
                         event_id)

    def split_path(self, relative_path):
        if not relative_path:
            return []

        if isinstance(relative_path, str):
            relative_path = re.split(r'[\\/]', relative_path)

        assert isinstance(relative_path, list)
        return [_f for _f in relative_path if _f]

    @with_session
    @benchmark
    def find_folder_by_relative_path(self,
                                     folder_path,
                                     on_not_found=raise_folder_not_found,
                                     session=None,
                                     ignore_processing=True):
        logger.debug("Find folder by relative path: %s", folder_path)
        path_list = self.split_path(folder_path)
        if not path_list:
            return None

        values_str = ','.join(
            ['({0},:x{0})'.format(n) for n in range(len(path_list))])
        values_dict = {
            "x{}".format(n): value for (n, value) in enumerate(path_list)}
        values_dict["n"] = len(path_list)-1

        folder = None
        try:
            folder = session.query(File).from_statement(sql_text("""
                with recursive
                    x (n, name) as (
                        values {}
                    ),
                    y (id, name, n) as (
                        select  f.id, x.name, x.n
                        from x, files f
                        inner join events e on e.file_id = f.id
                        where x.name = f.name and f.folder_id is null and x.n=0
                        --and is_folder=1
                        and not exists (select 1 from events e2
                                        where e2.file_id = f.id
                                        and e2.id > e.id
                        )
                        and e.folder_uuid is null
                        and e.type <> 'delete'
                        union all
                        select f.id, x.name, x.n
                        from x, files f, y
                        where x.name = f.name and f.folder_id = y.id
                        and x.n = y.n+1
                        --and is_folder=1
                        and 'delete' <> (select e.type
                                         from events e where e.file_id = f.id
                                         order by e.id desc limit 1
                                        )
                    )
                select * from files
                where id = (select id from y where n = :n)
                """.format(values_str)).params(**values_dict))\
                .one_or_none()
        except ResourceClosedError as e:
            logger.debug(e)

        if folder is None or not folder.is_folder:
            logger.debug("Folder not found %s", folder_path)
            return on_not_found(folder_path)

        if not folder.uuid and not ignore_processing:
            logger.debug("Folder in processing %s", folder.name)
            raise FileInProcessing(folder.name)

        return folder

    @with_session
    def find_file_by_relative_path(self,
                                   file_path,
                                   on_not_found=raise_file_not_found,
                                   session=None):
        files = self.find_files_by_relative_path(
            file_path, on_not_found=on_not_found, session=session,
            ignore_processing=False)
        existing_files = [f for f in files if f and f.is_existing]
        assert len(existing_files) < 2, \
            'file name is not uniqie in the database'
        if not existing_files:
            return on_not_found(file_path)

        return existing_files[0]

    @with_session
    def find_files_by_relative_path(self,
                                    file_path,
                                    on_not_found=raise_file_not_found,
                                    on_parent_not_found=None,
                                    session=None,
                                    ignore_processing=True,
                                    include_deleted=False):
        assert file_path
        path = self.split_path(file_path)
        folder = self.find_folder_by_relative_path(
            folder_path=path[:-1],
            on_not_found=lambda path: False,
            session=session,
            ignore_processing=ignore_processing)
        if folder is False:
            if on_parent_not_found:
                return on_parent_not_found()
            else:
                return list()

        file_name = path[-1]
        query = session.query(File) \
            .filter(File.name == file_name)
        if folder:
            query = query.filter(File.folder_id == folder.id)
        else:
            query = query.filter(File.folder_id.is_(None))
        files = query.all()
        if not include_deleted:
            files = [f for f in files if f and not f.is_deleted]
        return files

    @with_session
    def find_file_uuid_by_relative_path(self,
                                        file_path,
                                        on_not_found=raise_file_not_found,
                                        session=None):
        return self.find_file_by_relative_path(file_path,
                                               on_not_found=on_not_found,
                                               session=session).uuid

    @with_session
    def find_folder_uuid_by_relative_path(self,
                                          folder_path,
                                          on_not_found=raise_folder_not_found,
                                          session=None):
        folder = self.find_folder_by_relative_path(folder_path,
                                                   on_not_found=on_not_found,
                                                   session=session)
        uuid = folder.uuid if folder else None
        return uuid

    @with_session
    def find_all_folders_by_relative_path(self,
                                          folder_path,
                                          on_not_found=raise_folder_not_found,
                                          session=None,
                                          include_deleted=False):
        logger.debug("Find all folders by relative path: %s", folder_path)
        folders = []    # return empty list for root folder
        for folder_name in self.split_path(folder_path):
            if not folder_name:
                continue
            logger.debug("Searching for folder: %s", folder_name)
            folder_ids = [f.id for f in folders]
            query = session.query(File) \
                .filter(File.is_folder) \
                .filter(File.name == folder_name)
            if folder_ids:
                query = query.filter(File.folder_id.in_(folder_ids))
            else:
                query = query.filter(File.folder_id.is_(None))

            if not include_deleted:
                folders = list(
                    filter(lambda f: f and not f.is_deleted, query.all()))
            else:
                folders = query.all()
            if not folders:
                logger.debug("Folders not found for %s", folder_name)
                return on_not_found(folder_path)

            logger.debug("Found folders %s", folders)

        return folders

    @with_session
    @benchmark
    def find_conflicting_file_or_folder(self,
                                        folder_path,
                                        session=None,
                                        excluded_id=None):
        assert folder_path, "Folder path can't be empty"
        logger.debug("Find conflicting file or folder: %s", folder_path)
        path_list = self.split_path(folder_path)
        name = path_list[-1]
        path = '/'.join(path_list[:-1])

        folder = self.find_folder_by_relative_path(
            folder_path=path,
            on_not_found=lambda path: False,
            session=session,
            ignore_processing=True)
        if folder is False:
            logger.debug("Folder not found")
            return None, None

        excluded_str = "and last_e.file_id <> {0}".format(excluded_id) \
            if excluded_id else ""
        folder_str = " = '{}'".format(folder.uuid) \
            if folder else " is null "

        events = session.query(Event).from_statement(sql_text("""
            select last_e.* from events last_e
                where 1=1
                and last_e.id = (select max(e.id) from events e
                                 where e.file_id=last_e.file_id
                                 and e.server_event_id is not null)
                {0}
                and last_e.folder_uuid {1}
                and last_e.type in ('create', 'move', 'update')
                and last_e.file_name = :file_name
            """.format(excluded_str, folder_str)))\
            .params(file_name=name).all()
        logger.debug("Conflicting candidates: %s", log_sequence(events))

        files = [e.file for e in events]
        files = [f for f in files if f and not f.is_deleted]

        assert len(files) < 2, \
            'Conflicting file or folder name is not uniqie in the database'
        result = files[0] if files else None
        event = events[0] if events else None

        if not result:
            logger.debug("Conflicting file or folder not found: %s",
                         folder_path)
            return None, None,

        return result, event

    @with_session
    @benchmark
    def find_folders_by_future_path(self,
                                    folder_path,
                                    session=None,
                                    include_deleted=False):
        assert folder_path, "Folder path can't be empty"
        logger.debug("Find folder by future path: %s", folder_path)
        path_list = self.split_path(folder_path)
        folders_uuids = []
        for folder_name in path_list:
            logger.debug("folder_name %s", folder_name)
            folder_str = " in ({})".format(", ".join(
                map(lambda fu: "'{}'".format(fu), folders_uuids))) \
                if folders_uuids else " is null "
            delete_str = "or (last_e.type = 'delete' and " \
                         "last_e.file_name_before_event = :file_name)" \
                if include_deleted else ""

            events = session.query(Event).from_statement(sql_text("""
                select last_e.* from events last_e
                    where 1=1
                    and last_e.id = (select max(e.id) from events e
                                     where e.file_id=last_e.file_id
                                     and e.server_event_id is not null 
                                     and e.is_folder)
                    and last_e.folder_uuid {0}
                    and ((last_e.type != 'delete'
                    and last_e.file_name = :file_name)
                    {1})
                """.format(folder_str, delete_str))) \
                .params(file_name=folder_name).all()
            folders_uuids = [e.file_uuid for e in events]
            logger.debug("folders_uuids %s", folders_uuids)
            if not folders_uuids:
                break

        if not folders_uuids:
            return []

        assert include_deleted or len(folders_uuids) < 2, \
            "Folder has to be unique in db {}".format(folders_uuids)

        folders = session.query(File) \
            .filter(File.uuid.in_(folders_uuids)) \
            .all()
        logger.debug("Folders by future path %s", folders)
        return folders

    @with_session
    def get_path_from_event(self, event, session=None):
        file_name = event.file_name if event.type != 'delete' \
            else event.file_name_before_event
        assert file_name, "Name of file can't be empty"

        if not event.folder_uuid:
            path = file_name
        else:
            folder = session.query(File)\
                .filter(File.uuid == event.folder_uuid)\
                .one()
            path = "{}/{}".format(folder.path, file_name)
        return path

    @with_session
    def get_path_by_events(self, event, session=None):
        path = ""
        max_event = event
        sep = ''
        while True:
            file_name = max_event.file_name \
                if max_event.type != 'delete' \
                   or not max_event.file_name_before_event \
                else max_event.file_name_before_event
            assert file_name, "Name of file can't be empty"

            path = "{}{}".format(file_name, sep) + path
            sep = '/'

            folder_uuid = max_event.folder_uuid
            if not folder_uuid:
                break

            events = session.query(Event) \
                .filter(Event.server_event_id.isnot(None)) \
                .filter(Event.is_folder) \
                .filter(Event.file_uuid == folder_uuid) \
                .all()
            assert events, "Can't get path by events. " \
                           "No max event for uuid {}".format(folder_uuid)
            max_event = events[-1]

        return path

    @staticmethod
    def _get_files_size(files):
        result = 0
        for f in files:
            if f.is_folder or not f.event:
                continue
            result += f.event.file_size
        return result

    @staticmethod
    def _get_folder_children(folder_id, session):
        query = session.query(File)
        if folder_id is not None:
            query = query.filter(File.folder_id == folder_id)
        else:
            query = query.filter(File.folder_id.is_(None))

        for f in query.all():
            if not f.event or f.event.type == 'delete':
                continue
            yield f

    @with_session
    def get_share_size(self, session=None):

        def get_folder_descendants(folder_id=None):
            children = tuple(self._get_folder_children(folder_id, session))
            iter_child_folders = filter(lambda f: f.is_folder, children)
            iter_grandchildren = chain(*map(
                lambda f: get_folder_descendants(f.id),
                iter_child_folders))

            for c in children:
                yield c
            for c in iter_grandchildren:
                yield c

        share_size = self._get_files_size(get_folder_descendants())
        logger.debug(
            "Share size is %s byte(s)", share_size)
        return share_size

    @with_session
    def get_min_event_ids_for_not_ready_pathes(self, session=None):
        event_id = session.query(func.min(Event.server_event_id)) \
            .select_from(Event) \
            .filter(Event.file_id == File.id) \
            .filter(File.is_folder == 0) \
            .filter(Event.type == "update") \
            .filter(Event.state == "received") \
            .filter(Event.diff_file_size == 0) \
            .filter(Event.file_size >= MIN_DIFF_SIZE) \
            .scalar()

        direct_patch_event_id = event_id if event_id is not None else 0

        event_id = session.query(func.min(Event.server_event_id)) \
            .select_from(Event) \
            .filter(Event.file_id == File.id) \
            .filter(File.is_folder == 0) \
            .filter(Event.type == "update") \
            .filter(Event.state.in_(["received", "downloaded"])) \
            .filter(Event.rev_diff_file_size == 0) \
            .filter(Event.file_size_before_event > 0) \
            .scalar()

        reversed_patch_event_id = event_id if event_id is not None else 0

        return direct_patch_event_id, reversed_patch_event_id

    @with_session
    def get_files_by_folder_uuid(self, folder_uuid,
                                 files_page_processor_cb, session=None,
                                 include_folders=False, include_self=False,
                                 include_deleted=True):
        """
        Finds all files in folder folder_uuid and its subfolders
        Result is passed to files_page_processor_cb by pages
        until there are no files anymore

        """
        for files_page, folders_uuids in self._file_pages_generator(
                folder_uuid, session, include_folders, include_self,
                include_deleted):
            files_page_processor_cb(files_page, folders_uuids, session)

    def _file_pages_generator(self, folder_uuid, session, include_folders,
                              include_self, include_deleted):
        folder = session.query(File) \
            .filter(File.uuid == folder_uuid).one_or_none()
        if not folder:
            return

        files = [] if not include_self else [folder]
        folders_uuids_for_search = [folder_uuid]
        folders_uuids = [folder_uuid]
        next_level_folders = []
        while folders_uuids_for_search:
            folder_uuids_to_query = folders_uuids_for_search[:DB_PAGE_SIZE]
            folders_uuids_for_search = folders_uuids_for_search[DB_PAGE_SIZE:]
            subfolders = session.query(Event.file_uuid).from_statement(
                sql_text(
                """
                    select last_e.file_uuid from events last_e
                    where last_e.folder_uuid in ({0})
                    and last_e.id = (select max(e.id) from events e
                                     where e.file_id=last_e.file_id)
                    and last_e.is_folder
                    {1}
                """.format(
                    ','.join(["'{}'".format(uuid) for uuid in folder_uuids_to_query]),
                    '' if include_deleted
                    else "and last_e.type <> 'delete'")
            )).all()
            logger.debug("Subfolders %s", subfolders)
            if subfolders:
                subfolders = list(zip(*subfolders))[0]
                folders_uuids.extend(subfolders)
                next_level_folders.extend(subfolders)

            if not folders_uuids_for_search:
                folders_uuids_for_search = next_level_folders[:]
                next_level_folders = []

            folder_files = session.query(File).from_statement(sql_text(
                """
                    select result_f.* from files result_f, events last_e
                    where result_f.id = last_e.file_id
                    and last_e.folder_uuid in ({0})
                    and last_e.id = (select max(e.id) from events e
                                     where e.file_id=last_e.file_id)
                    {1}
                    {2}
                """.format(
                    ','.join(["'{}'".format(uuid) for uuid in folder_uuids_to_query]),
                    '' if include_folders else 'and not last_e.is_folder',
                    '' if include_deleted else "and last_e.type <> 'delete'")
                )).all()

            files.extend(folder_files[:])
            for i in range(0, len(files), DB_PAGE_SIZE):
                files_page = files[:DB_PAGE_SIZE]
                files = files[DB_PAGE_SIZE:]
                yield files_page, folders_uuids
                logger.debug("Processed files page of %s files",
                             len(files_page))

    @with_session
    def is_collaborated(self, folder_name, session=None):
        if not folder_name:
            return False

        events = session.query(Event) \
            .filter(Event.file_id == File.id) \
            .filter(File.is_folder) \
            .filter(File.folder_id.is_(None)) \
            .filter(File.name == folder_name) \
            .filter(or_(File.is_collaborated == 1,
                        Event.erase_nested == 1)) \
            .all()
        return len(events) > 0

    @with_session
    def all_local_events_processsed(self, session=None):
        result = session.query(func.count()) \
            .filter(Event.state.in_(('occured', 'conflicted'))) \
            .scalar()
        return result == 0

    @with_session
    def get_folder_path_deleted_excluded_by_uuid(self, uuid, session=None):
        if not uuid:
            path = ''
            deleted = excluded = False
        else:
            folder = session.query(File)\
                .filter(File.uuid == uuid).one_or_none()
            if not folder:
                return None, None, None
            path = folder.path
            deleted = folder.is_deleted
            excluded = folder.excluded
        return path, deleted, excluded

    def db_file_exists(self):
        return exists(self._db_file) and getsize(self._db_file) > 0

    @with_session
    def conflicting_name_exists(
            self, folder_id, file_id, file_name, session=None):
        """
            Check for conflicting name exist for the file_name in given folder.
            Checking implemented with case-insensitive.
        """

        files = session.query(File)\
            .filter(File.id != file_id)\
            .filter(or_(and_(File.folder_id.is_(None), folder_id is None),
                        and_(File.folder_id == folder_id)))\
            .all()
        for _file in files:
            if not _file.is_deleted \
                    and _file.name.upper() == file_name.upper():
                return True
        return False

    @with_session
    def show_compile_options(self, session=None):
        compile_opts = session.execute(sql_text(
            """pragma compile_options;
            """))\
            .fetchall()
        logger.info("db driver compile_options: %s", compile_opts)

    def get_mapping(self, db_object):
        mapping = {a: db_object.__dict__[a] for a in db_object.__dict__
                   if not a.startswith('_') and
                   not callable(db_object.__dict__[a])}
        logger.debug("Mapping of %s is %s",
                     db_object.__class__.__name__, mapping)
        return mapping

    @with_session
    def mark_child_excluded(self, file_id, session=None, is_excluded=True,
                            read_only=False):
        not_excluded = int(not is_excluded)
        session.query(File) \
            .filter(File.is_folder == 0) \
            .filter(File.excluded == not_excluded) \
            .filter(File.folder_id == file_id) \
            .update(dict(excluded=is_excluded,
                         event_id=None,
                         last_skipped_event_id=None),
                    synchronize_session=False)
        if is_excluded:
            event_ids = session.query(func.max(Event.id)) \
                .filter(Event.file_id == File.id) \
                .filter(File.is_folder == 0) \
                .filter(File.excluded == is_excluded) \
                .filter(File.folder_id == file_id) \
                .filter(Event.state.notin_(('occured', 'conflicted'))) \
                .group_by(File.id) \
                .having(Event.type != 'delete') \
                .all()
            if event_ids:
                event_ids = list(zip(*event_ids))[0]
                session.bulk_update_mappings(
                    Event, [{'id': e_id, 'state': 'received'}
                            for e_id in event_ids])
            folder_event_ids = session.query(func.max(Event.id)) \
                .filter(Event.file_id == File.id) \
                .filter(File.is_folder == 1) \
                .filter(File.excluded == is_excluded) \
                .filter(File.folder_id == file_id) \
                .filter(Event.state.notin_(('occured', 'conflicted'))) \
                .group_by(File.id) \
                .having(Event.type != 'delete') \
                .all()
            if folder_event_ids:
                folder_event_ids = zip(*folder_event_ids)[0]
                session.bulk_update_mappings(
                    Event, [{'id': e_id, 'state': 'downloaded'}
                            for e_id in folder_event_ids])

        folders_query = session.query(File) \
            .filter(File.is_folder) \
            .filter(File.excluded == not_excluded) \
            .filter(File.folder_id == file_id)
        folders = folders_query.all()
        folders_query \
            .update(dict(excluded=is_excluded, event_id=None),
                    synchronize_session=False)
        for folder in folders:
            self.mark_child_excluded(
                folder.id, session, is_excluded)

    @with_session
    def get_folder_by_uuid(self, uuid, session=None):
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

    def get_sessions_count(self):
        with self._sessions_count_lock:
            return self._sessions_count

    def expunge_parents(self, file, session):
        logger.debug("Expunge parents for file %s", file)
        files_to_expunge = []
        events_to_expunge = []

        try:
            expunge_file = file
            while True:
                expunge_file = expunge_file.folder
                if not expunge_file:
                    break

                events_to_expunge.extend(expunge_file.events)
                files_to_expunge.append(expunge_file)

            list(map(session.expunge, events_to_expunge))
            list(map(session.expunge, files_to_expunge))
        except Exception as e:
            logger.warning("Can't expunge parents for file %s. Reason: %s",
                           file, e)

    @with_session
    def get_file_path_by_event_uuid(self, uuid, session=None):
        try:
            file, event = self.get_file_and_event_by_event_uuid(
                uuid, session=session)
            if file.event_id and event.id == file.event_id:
                return file.path
        except (ValueError, ProtoError) as e:
            logger.warning("Can't find file and event by uuid. Reason: %s", e)
        return ''

    @with_session
    def get_registered_event_by_event_uuid(self, uuid, session=None):
        if not uuid:
            return None

        event = session.query(Event) \
            .select_from(Event) \
            .filter(Event.uuid == uuid) \
            .filter(Event.server_event_id > 0) \
            .one_or_none()
        return event

    @with_session
    def get_file_ids_by_event_uuids(self, uuids, session=None):
        files = session.query(File) \
            .select_from(Event) \
            .join(File.events) \
            .filter(Event.uuid.in_(tuple(uuids))) \
            .all()
        return [f.id for f in files]

    def delete_remote_events_not_applied(self):
        with self.create_session(read_only=False) as session:
            remotes_to_delete = session.query(Event) \
                .filter(Event.file_id == File.id) \
                .filter(Event.erase_nested == 0) \
                .filter(
                or_(File.excluded == 1,
                    and_(
                        File.event_id.is_(None),
                        File.last_skipped_event_id.is_(None)
                    ),
                    and_(
                        Event.id > File.event_id,
                        File.last_skipped_event_id.is_(None)
                    ),
                    and_(
                        File.last_skipped_event_id.isnot(None),
                        File.last_skipped_event_id < Event.id,
                        File.event_id.is_(None)),
                    and_(
                        File.last_skipped_event_id.isnot(None),
                        File.last_skipped_event_id < Event.id,
                        File.event_id <= File.last_skipped_event_id
                    )
                )) \
                .filter(Event.state.in_(['received', 'downloaded'])) \
                .all()

            files_created = {e.file for e in remotes_to_delete
                             if e.type == 'create'}
            list(map(session.delete, remotes_to_delete))
            logger.debug("Deleted %s remote not applied events",
                         len(remotes_to_delete))
            session.query(File) \
                .filter(File.excluded == 1) \
                .delete(synchronize_session=False)
            for file in files_created:
                if file.excluded:
                    continue

                try:
                    session.delete(file)
                except Exception as e:
                    logger.warning("Can't delete file %s. Reason: %s",
                                   file, e)

    @with_session
    def get_files_with_deletes_prior_to(self, server_event_id, session=None):
        query = session.query(File) \
            .filter(Event.file_id == File.id) \
            .filter(Event.type == 'delete') \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.server_event_id > 0) \
            .filter(Event.server_event_id < server_event_id)

        return list(query.all())

    @with_session
    def get_updates_registered_prior_to(self, server_event_id, session=None):
        updates = session.query(Event) \
            .filter(Event.type == 'update') \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.server_event_id < server_event_id) \
            .all()
        return updates

    @with_session
    def get_files_events_form_subdirs(self, folder_ids, exclude_ids,
                                      purpose='', session=None):
        folder_ids = deque(folder_ids)
        exclude_ids = set(exclude_ids)
        files = list()
        events = list()
        while folder_ids:
            f_id = folder_ids.popleft()
            folder_files = session.query(File) \
                .filter(File.folder_id == f_id).all()
            folder_ids.extend([f.id for f in folder_files
                               if f.is_folder and f.id not in exclude_ids])
            for file in folder_files:
                if file.id in exclude_ids:
                    continue

                exclude_ids.add(file.id)
                files.append(file)
                logger.debug("added file %s%s", purpose, file)
                events.extend(file.events)
                logger.debug("added events %s%s",
                             purpose, log_sequence(file.events))
        return files, events

    @with_session
    def get_previous_events_prior_to(self, server_event_id, session=None):
        events = session.query(Event) \
            .filter(Event.file_id == File.id) \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.server_event_id > 0) \
            .filter(Event.server_event_id < server_event_id) \
            .filter(or_(
            and_(File.event_id.isnot(None),
                 File.event_id != Event.id),
            Event.type == 'delete')) \
            .all()
        return events

    @with_session
    def get_backups_events(self, session=None):
        events = session.query(Event) \
            .filter(Event.type == 'delete') \
            .filter(not_(Event.is_folder)) \
            .filter(Event.server_event_id.isnot(None)) \
            .filter(Event.state == 'downloaded') \
            .filter(not_(Event.outdated)) \
            .all()
        return events
