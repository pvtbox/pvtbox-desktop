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

from contextlib import contextmanager
from os import stat, listdir
from collections import defaultdict

from os.path import join, exists, getsize, isfile
from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from common.signal import Signal
from common.utils import get_copies_dir, is_db_or_disk_full, remove_file
from common.file_path import FilePath
from common.logging_setup import do_rollover
from common.constants import DB_PAGE_SIZE


from .copy import Base, Copy
from db_migrations import upgrade_db, stamp_db

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Copies(object):
    """
    Interface for reference counting of files copies
    """

    def __init__(self, root, db_file_created_cb=None, extended_logging=True,
                 to_upgrade=True):
        self.possibly_sync_folder_is_removed = Signal()
        self.delete_copy = Signal(str,  # copy hash
                                  bool)     # with signature
        self.db_or_disk_full = Signal()

        self._db_file = join(get_copies_dir(root), 'copies.db')
        new_db_file = not exists(self._db_file)
        if new_db_file and callable(db_file_created_cb):
            db_file_created_cb()

        if to_upgrade and not new_db_file:
            # Database migration. It can be executed before opening db
            try:
                upgrade_db("copies_db", db_filename=self._db_file)
            except Exception as e:
                remove_file(self._db_file)
                new_db_file = True
                logger.warning("Can't upgrade copies db. "
                               "Reason: (%s) Creating...", e)
                if callable(db_file_created_cb):
                    db_file_created_cb()

        self._engine = create_engine('sqlite:///{}'.format(
            FilePath(self._db_file)))
        self._Session = sessionmaker(bind=self._engine)

        Base.metadata.create_all(self._engine, checkfirst=True)

        if new_db_file:
            try:
                stamp_db("copies_db", db_filename=self._db_file)
            except Exception as e:
                logger.error("Error stamping copies db: %s", e)

        self._lock = RLock()
        self._root = root

        self._extended_logging = extended_logging

        if not self._extended_logging:
            self._logger = None
        else:
            self._logger = logging.getLogger('copies_logger')
            self._logger.debug("Copies init")

        self._last_changes = defaultdict(int)

    @contextmanager
    def create_session(self):
        with self._lock:
            session = self._Session()
            session.expire_on_commit = False
            session.autoflush = False

            try:
                yield session
                session.commit()
            except OperationalError as e:
                try:
                    session.rollback()
                except Exception:
                    pass
                finally:
                    self.possibly_sync_folder_is_removed()
                    logger.error("Possibly sync folder is removed %s", e)

                if is_db_or_disk_full(e):
                    self.db_or_disk_full.emit()
                else:
                    raise
            except:
                session.rollback()
                raise
            finally:
                session.close()

    def add_copy_reference(self, hash, reason="", postponed=False):
        if postponed:
            self._last_changes[hash] += 1
            copy_count = self._last_changes[hash]
        else:
            with self.create_session() as session:
                copy = session.query(Copy)\
                    .filter(Copy.hash == hash)\
                    .one_or_none()
                if copy is None:
                    copy = Copy(hash=hash, count=0)
                copy.count += 1
                copy_count = copy.count
                session.merge(copy)

        logger.debug("File copy reference added, %s, "
                     "count: %s, postponed is %s",
                     hash, copy_count, postponed)

        if not self._extended_logging:
            return

        copies_dir = get_copies_dir(self._root)
        self._logger.debug("File copy reference added, %s, count: %s. "
                           "postponed is %s. File exists: %s. "
                           "Reason: %s",
                           hash, copy_count, postponed,
                           exists(join(copies_dir, hash)),
                           reason)

    def remove_copy_reference(self, hash, reason="", postponed=False):
        if postponed:
            self._last_changes[hash] -= 1
            copy_count = self._last_changes[hash]
        else:
            with self.create_session() as session:
                copy = session.query(Copy)\
                    .filter(Copy.hash == hash)\
                    .one_or_none()
                if not copy:
                    logger.warning("Trying to remove copy reference "
                                   "for non-existant copy %s", hash)
                    return

                else:
                    copy.count -= 1
                    copy_count = copy.count
                session.merge(copy)

        logger.debug("File copy reference removed, %s, "
                     "count: %s, postponed is %s",
                     hash, copy_count, postponed)

        if not self._extended_logging:
                return

        copies_dir = get_copies_dir(self._root)
        self._logger.debug("File copy reference removed, %s, count: %s, "
                           "postponed is %s. File exists: %s. Reason: %s",
                           hash, copy_count, postponed,
                           exists(join(copies_dir, hash)), reason)

    def commit_last_changes(self):
        if not self._last_changes:
            return

        hashes = list(self._last_changes.keys())
        hashes_len = len(hashes)
        with self.create_session() as session:
            mappings = []
            insert_mappings = []
            for i in range(0, hashes_len, DB_PAGE_SIZE):
                hashes_portion = hashes[:DB_PAGE_SIZE]
                hashes = hashes[DB_PAGE_SIZE:]
                copies = session.query(Copy)\
                    .filter(Copy.hash.in_(tuple(hashes_portion)))\
                    .all()
                mappings.extend([dict(
                    id=copy.id,
                    count=copy.count + self._last_changes[copy.hash])
                    for copy in copies
                ])
                hashes_absent = set(hashes_portion) - {c.hash for c in copies}
                insert_mappings.extend([dict(
                    hash=hash,
                    count=self._last_changes[hash])
                    for hash in hashes_absent
                ])

            session.bulk_update_mappings(Copy, mappings)
            session.bulk_insert_mappings(Copy, insert_mappings)

        logger.debug("Commited last copies changes for %s hashes",
                     len(self._last_changes))
        if self._extended_logging:
            self._logger.debug("Commited last copies changes for %s",
                               self._last_changes)

        self.clear_last_changes()

    def clear_last_changes(self):
        self._last_changes.clear()

    def copy_exists(self, hash):
        return exists(self.get_copy_file_path(hash))

    def get_copy_size(self, hash):
        copy_path = self.get_copy_file_path(hash)
        return stat(copy_path).st_size if exists(copy_path) else 0

    def get_copy_file_path(self, hash):
        return join(get_copies_dir(self._root), hash)

    def clean(self, with_files=True, with_signatures=True):
        with self.create_session() as session:
            self._log_db(session)

            if with_files:
                copies = session.query(Copy).all()
                for copy in copies:
                    if copy.hash:
                        self.delete_copy(copy.hash, with_signatures)

        try:
            self._engine.execute("delete from copies")
            logger.info("Cleaned copies data base")
        except Exception as e:
            logger.error("Failed to clean copies DB (%s)", e)
            if not self.db_file_exists():
                raise e

        if self._extended_logging:
            do_rollover(self._logger, use_root=False)

    def clean_unnecessary(self):
        with self.create_session() as session:
            self._log_db(session)

            copies = session.query(Copy).filter(Copy.count <= 0).all()
            for copy in copies:
                if copy.hash:
                    self.delete_copy(copy.hash, True)

            session.query(Copy).filter(Copy.count <= 0).delete()

    def remove_copies_not_in_db(self):
        with self.create_session() as session:
            copies = session.query(Copy).all()
            exclude_files = {copy.hash for copy in copies}
        exclude_files.add('copies.db')
        copies_dir = get_copies_dir(self._root)
        try:
            files_to_delete = set(listdir(copies_dir)) - exclude_files
            files_to_delete = map(lambda f: join(copies_dir, f),
                                  files_to_delete)
            list(map(remove_file, filter(
                lambda f: isfile(f) and
                          not f.endswith('.download') and
                          not f.endswith('.info'), files_to_delete)))
        except Exception as e:
            self.possibly_sync_folder_is_removed()
            logger.warning("Can't remove copies files. Reason: %s", e)

    def db_file_exists(self):
        return exists(self._db_file) and getsize(self._db_file) > 0

    def _log_db(self, session):
        if not self._extended_logging:
            return

        copies_dir = get_copies_dir(self._root)
        copies = session.query(Copy).all()
        for i, copy in enumerate(copies):
            self._logger.debug("Copy %s: %s. File exists: %s",
                               i, copy, exists(join(copies_dir, copy.hash)))
