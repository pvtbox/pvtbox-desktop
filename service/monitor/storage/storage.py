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

from os.path import exists, getsize, join, relpath
from contextlib import contextmanager
from pickle import load, dump
import threading

from sqlalchemy import create_engine, or_, func, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session as Session
from sqlalchemy.exc import OperationalError

from common.file_path import FilePath
from common.path_utils import is_contained_in, is_contained_in_dirs
from common.signal import Signal
from common.utils import make_dirs, is_db_or_disk_full, remove_file, benchmark

from .file import Base, File
from db_migrations import upgrade_db, stamp_db


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def with_session(read_only, locked=False):
    def _with_session(func):

        """
        Decorator to make session if it is need

        Args:
            func: decorated function
        """

        def impl(self, *args, **kwargs):
            try:
                session_arg = 'session'
                if session_arg in kwargs and kwargs[session_arg]:
                    return func(self, *args, **kwargs)

                for arg in args:
                    if isinstance(arg, Session):
                        return func(self, *args, **kwargs)

                with self.create_session(
                        read_only=read_only, locked=locked) as session:
                    kwargs[session_arg] = session
                    return func(self, *args, **kwargs)
            except OperationalError as e:
                self.possibly_sync_folder_is_removed()
                logger.error("Possibly sync folder is removed %s", e)
                raise

        return impl
    return _with_session


class Storage(object):
    """
    Interface for requesting info on registered files and folders
    """

    def __init__(self, path_converter, db_file_created_cb=None):
        self._pc = path_converter

        self.possibly_sync_folder_is_removed = Signal()
        self.db_or_disk_full = Signal()

        self._db_file = self._pc.create_abspath('.pvtbox/storage.db')
        logger.debug("DB file: %s", self._db_file)
        new_db_file = not exists(self._db_file)
        if new_db_file and callable(db_file_created_cb):
            db_file_created_cb()

        make_dirs(self._db_file)

        if not new_db_file:
            # Database migration. It can be executed before opening db
            try:
                upgrade_db("storage_db", db_filename=self._db_file)
            except Exception as e:
                remove_file(self._db_file)
                new_db_file = True
                logger.warning("Can't upgrade storage db. "
                               "Reason: (%s) Creating...", e)
                if callable(db_file_created_cb):
                    db_file_created_cb()

        self._engine = create_engine(
            'sqlite:///{}'.format(FilePath(self._db_file)),
            connect_args={
                'timeout': 60 * 1000,
                'check_same_thread': False,
            })
        self._engine.pool_timeout = 60 * 60 * 1000
        self._Session = sessionmaker(bind=self._engine)

        Base.metadata.create_all(self._engine, checkfirst=True)

        if new_db_file:
            try:
                stamp_db("storage_db", db_filename=self._db_file)
            except Exception as e:
                logger.error("Error stamping storage db: %s", e)

        self._lock = threading.RLock()

    @contextmanager
    def create_session(self, read_only=True, locked=False):
        session = self._Session()
        session.expire_on_commit = False
        session.autoflush = False
        if read_only:
            session.flush = lambda: None

        if not read_only and locked:
            logger.debug(
                "session %s acquiring lock...", hex(id(session)))
            self._lock.acquire()
            logger.debug(
                "session %s acquired lock.", hex(id(session)))

        try:
            yield session
            session.commit()
        except OperationalError as e:
            logger.warning("OperationalError: %s", e)
            try:
                session.rollback()
            except Exception as e:
                logger.warning("OperationalError, exception while trying to rollback session: %s", e)
                pass
            if is_db_or_disk_full(e):
                self.db_or_disk_full.emit()
            else:
                raise
        except Exception as e:
            logger.warning("Exception: %s", e)
            session.rollback()
            raise
        finally:
            if not read_only and locked:
                self._lock.release()
                logger.debug(
                    "session %s released lock.", hex(id(session)))
            session.close()

    @with_session(True)
    def _get_known_paths(self, is_folder,
                         parent_dir=None, exclude_dirs=None, session=None):
        query = session.query(File.relative_path)
        query = query.filter(File.is_folder == is_folder)
        paths = query.all()
        if parent_dir:
            parent_dir = self._pc.create_relpath(parent_dir)
            result = []
            for path in paths:
                if is_contained_in(path[0], parent_dir):
                    result.append(path)
            paths = result
        if exclude_dirs:
            result = []
            # Optimize perfomance using iterator based solution
            for ed in exclude_dirs:
                for pp in paths:
                    if not is_contained_in(pp[0], ed):
                        result.append(pp)
            paths = result

        return [FilePath(self._pc.create_abspath(x[0])) for x in paths]

    @benchmark
    def get_known_files(
            self, parent_dir=None, exclude_dirs=None, session=None):
        """
        Returns absolute paths of files known at the moment.

        @param parent_dir Name of parent dir to limit results to [unicode]
        @return Known files paths (absolute) [(unicode, )]
        """

        return self._get_known_paths(
            is_folder=False, parent_dir=parent_dir, exclude_dirs=exclude_dirs,
            session=session)

    @benchmark
    def get_known_folders(
            self, parent_dir=None, exclude_dirs=None, session=None):
        """
        Returns absolute paths of folders known at the moment

        @param parent_dir Name of parent dir to limit results to [unicode]
        @return Known folders paths (absolute) [(unicode, )]
        """

        return self._get_known_paths(
            is_folder=True, parent_dir=parent_dir, exclude_dirs=exclude_dirs,
            session=session)

    @with_session(True)
    def get_known_file(self, abs_path, is_folder=None, session=None):
        rel_path = self._pc.create_relpath(abs_path)

        query = session.query(File).filter(File.relative_path == rel_path)
        if is_folder is not None:
            query.filter(File.is_folder == is_folder)

        return query.one_or_none()

    @with_session(True)
    def get_known_file_by_id(self, file_id, session=None):
        return session.query(File)\
            .filter(File.events_file_id == file_id)\
            .one_or_none()

    @with_session(False)
    def get_new_file(self, abs_path, is_folder, session=None):
        rel_path = self._pc.create_relpath(abs_path)

        file = File(relative_path=rel_path,
                    is_folder=is_folder)

        return file

    def update_file_signature(self, file, signature):
        signature_path = self._pc.create_abspath(file.signature_rel_path)
        make_dirs(signature_path)
        with open(signature_path, 'wb') as f:
            dump(signature, f, protocol=2)

    def get_file_signature(self, file):
        abs_path = self._pc.create_abspath(file.signature_rel_path)
        try:
            with open(abs_path, 'rb') as f:
                return load(f)
        except (IOError, OSError, EOFError):
            return None

    @with_session(False)
    def save_file(self, file, session=None):
        return session.merge(file)

    @with_session(False)
    def delete_file(self, file, session=None):
        session.delete(file)

    def clean(self):
        try:
            self._engine.execute("delete from files")
            logger.info("Cleaned storage data base")
        except Exception as e:
            logger.error("Failed to clean DB (%s)", e)
            if not self.db_file_exists():
                raise e

    @with_session(False)
    def delete_directories(self, dirs=[], session=None):
        paths_deleted = []
        if not dirs:
            return paths_deleted

        files = session.query(File).all()
        dirs_rel = [self._pc.create_relpath(p) for p in dirs]
        for file in files:
            if is_contained_in_dirs(file.relative_path, dirs_rel):
                if not file.is_folder:
                    paths_deleted.append(file.relative_path)
                session.delete(file)
        return paths_deleted

    def db_file_exists(self):
        return exists(self._db_file) and getsize(self._db_file) > 0

    @with_session(False, True)
    def change_events_file_id(self, old_id, new_id, session=None):
        file = self.get_known_file_by_id(old_id, session=session)
        if file:
            file.events_file_id = new_id
            logger.debug("Changed events_file_id for %s from %s to %s",
                         file.relative_path, old_id, new_id)
        else:
            logger.warning("Could not find file with events_file_id = %s",
                           old_id)

    @with_session(True)
    def get_known_folder_children(self, parent_dir_rel_path, session=None):
        path_like = parent_dir_rel_path + '/%'
        children = session.query(File)\
            .filter(
            or_(File.relative_path == parent_dir_rel_path,
                File.relative_path.like(path_like)))\
            .all()
        return children

    @with_session(False, True)
    def delete_known_folder_children(self, parent_dir_rel_path, session=None):
        path_like = parent_dir_rel_path + '/%'
        session.query(File)\
            .filter(
            or_(File.relative_path == parent_dir_rel_path,
                File.relative_path.like(path_like)))\
            .delete(synchronize_session=False)

    @with_session(False, True)
    def move_known_folder_children(self, old_dir_rel_path,
                                   new_dir_rel_path, session=None):
        path_like = old_dir_rel_path + '/%'
        files = session.query(File) \
            .filter(
            or_(File.relative_path == old_dir_rel_path,
                File.relative_path.like(path_like))) \
            .all()
        mappings = [{'id': f.id,
                    'relative_path': FilePath(
                        join(new_dir_rel_path, relpath(f.relative_path,
                                                       old_dir_rel_path)))}
                    for f in files]
        session.bulk_update_mappings(File, mappings)

    @with_session(True)
    def hash_in_storage(self, file_hash, session=None):
        if not file_hash:
            return None

        files_count = session.query(func.count())\
            .select_from(File)\
            .filter(File.file_hash == file_hash)\
            .scalar()
        return files_count > 0

    @with_session(False, True)
    def clear_files_hash_mtime(self, session=None):
        session.execute(update(File)
                        .where(File.is_folder == 0)
                        .values(file_hash=None, mtime=0))

    @with_session(True)
    def get_last_files(self, limit, offset=0, session=None):
        files = session.query(File) \
            .filter(File.is_folder == 0) \
            .order_by(File.mtime.desc()) \
            .offset(offset).limit(limit) \
            .all()
        return files

    def get_file_by_hash(self, hash, exclude, session):
        return session.query(File) \
            .filter(File.file_hash == hash) \
            .filter(File.id.notin_(exclude)) \
            .first()

    @with_session(False, True)
    def delete_files_with_empty_events_file_ids(self, session=None):
        files_with_empty_ids = session.query(File) \
            .filter(File.events_file_id.is_(None)) \
            .all()
        for file in files_with_empty_ids:
            if file.is_folder:
                self.delete_known_folder_children(
                    file.relative_path, session=session)
                type_str = "folder"
            else:
                session.delete(file)
                type_str = "file"
            logger.debug("Deleted %s %s with empty events_file_id",
                         type_str, file.relative_path)
        return bool(files_with_empty_ids)
