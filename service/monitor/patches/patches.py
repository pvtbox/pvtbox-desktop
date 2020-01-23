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
from queue import Queue
from pickle import load

from contextlib import contextmanager
from os import stat

from os.path import join, exists, getsize
from threading import RLock, Timer

from sortedcontainers import SortedDict
SortedDict.iteritems = SortedDict.items
from sqlalchemy import create_engine
from time import time

from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from common.async_utils import run_daemon
from service.monitor.rsync import Rsync
from common.signal import Signal, AsyncSignal
from service.transport_setup import signals as transport_setup_signals
from common.utils import get_patches_dir, get_copies_dir, \
    get_signatures_dir, remove_file, is_db_or_disk_full, \
    get_local_time_from_timestamp
from common.constants import \
    EMPTY_FILE_HASH, \
    DOWNLOAD_PRIORITY_WANTED_DIRECT_PATCH, \
    DOWNLOAD_PRIORITY_REVERSED_PATCH, \
    DOWNLOAD_PRIORITY_DIRECT_PATCH, \
    RETRY_DOWNLOAD_TIMEOUT
from common.file_path import FilePath

from .patch import Base, Patch
from db_migrations import upgrade_db, stamp_db


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Patches(object):
    """
    Interface for working with patches
    """

    def __init__(self, root, copies_storage,
                 tracker=None,
                 retry_download_timeout=RETRY_DOWNLOAD_TIMEOUT,
                 db_file_created_cb=None,
                 extended_logging=True, events_db=None):
        self.possibly_sync_folder_is_removed = Signal()
        self.patch_created = AsyncSignal(str,  # patch uuid
                                         int)  # patch size
        self.patch_deleted = AsyncSignal(str)  # patch_uuid
        self.db_or_disk_full = Signal()

        self._root = root
        self._download_manager = None
        self._copies_storage = copies_storage
        self._tracker = tracker
        self._retry_download_timeout = retry_download_timeout
        self._events_db = events_db
        self._retry_download_timer = None

        self._started = False
        self._patches_on_registration = set()

        self._failed_downloads = set()
        transport_setup_signals.known_nodes_changed.connect(
                self.on_online_nodes_changed)

        self._db_file = join(
            get_patches_dir(self._root, create=True), 'patches.db')
        new_db_file = not exists(self._db_file)
        if new_db_file and callable(db_file_created_cb):
            db_file_created_cb()

        if not new_db_file:
            # Database migration. It can be executed before opening db
            try:
                upgrade_db("patches_db", db_filename=self._db_file)
            except Exception as e:
                remove_file(self._db_file)
                new_db_file = True
                logger.warning("Can't upgrade patches db. "
                               "Reason: (%s) Creating...", e)
                if callable(db_file_created_cb):
                    db_file_created_cb()

        self._engine = create_engine('sqlite:///{}'.format(
            FilePath(self._db_file)))
        self._Session = sessionmaker(bind=self._engine)

        Base.metadata.create_all(self._engine, checkfirst=True)

        if new_db_file:
            try:
                stamp_db("patches_db", db_filename=self._db_file)
            except Exception as e:
                logger.error("Error stamping patches db: %s", e)

        self._lock = RLock()
        self._patch_existance_lock = RLock()
        self._patches_queue = Queue()
        self._thread = None

        self._extended_logging = extended_logging
        if self._extended_logging:
            self._logger = logging.getLogger('copies_logger')
            self._logger.debug("Patches init")
        else:
            self._logger = None

        self._last_changes = dict()

    def start(self):
        self._thread = self._patches_queue_processor()
        self._started = True
        self._patches_on_registration = set()
        logger.debug("Patches started")

    def stop(self):
        if not self._thread:
            return

        self._started = False
        self._patches_on_registration.clear()
        self._patches_queue.put(None)  # stop _patches_queue

        if self._thread:
            self._thread.join()
            self._thread = None
        logger.debug("Patches stopped")

    def set_download_manager(self, download_manager):
        self._download_manager = download_manager

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
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    def _get_patch(self, uuid):
        with self.create_session() as session:
            patch = \
                session.query(Patch).filter(Patch.uuid == uuid).one_or_none()
            return patch

    def add_direct_patch(
            self, uuid, new_hash, old_hash, size=None, active=True,
            reason="", postponed=False):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None:
                patch = Patch(
                    uuid=uuid, new_hash=new_hash, old_hash=old_hash, size=size,
                    direct_count=0, reverse_count=0, active=active)
                self._add_copies_referencies(uuid, old_hash, new_hash,
                                             postponed, "add_direct_patch")
            patch.active = active if active else patch.active
            if postponed:
                direct_count, reverse_count = self._last_changes.get(
                    uuid, (0, 0))
                self._last_changes[uuid] = (direct_count + 1, reverse_count)
            else:
                patch.direct_count += 1
            patch = session.merge(patch)
            logger.debug("Direct patch reference added, %s, postponed is %s",
                         patch, postponed)
            if self._extended_logging:
                patch_path = self.get_patch_path(uuid)
                self._logger.debug("Direct patch reference added, %s, "
                                   "postponed is %s. "
                                   "Patch file exists: %s. Reason: %s",
                                   patch, postponed, exists(patch_path),
                                   reason)

        self._patches_queue.put(patch)

    def add_reverse_patch(
            self, uuid, new_hash, old_hash, size=None, active=True,
            reason="", postponed=False):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None:
                patch = Patch(
                    uuid=uuid, new_hash=new_hash, old_hash=old_hash, size=size,
                    direct_count=0, reverse_count=0, active=active)
                self._add_copies_referencies(uuid, old_hash, new_hash,
                                             postponed, "add_reverse_patch")

            patch.active = active if active else patch.active
            if postponed:
                direct_count, reverse_count = self._last_changes.get(
                    uuid, (0, 0))
                self._last_changes[uuid] = (direct_count, reverse_count + 1)
            else:
                patch.reverse_count += 1
            patch = session.merge(patch)
            logger.debug("Reverse patch reference added, %s, postponed is %s",
                         patch, postponed)
            if self._extended_logging:
                patch_path = self.get_patch_path(uuid)
                self._logger.debug("Reverse patch reference added, %s, "
                                   "postponed is %s. "
                                   "Patch file exists: %s. Reason: %s",
                                   patch, postponed, exists(patch_path),
                                   reason)

        logger.debug("Session ended")
        self._patches_queue.put(patch)

    def remove_direct_patch(self, uuid, reason="", postponed=False):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None or not patch.direct_count and not postponed:
                return

            if postponed:
                direct_count, reverse_count = self._last_changes.get(
                    uuid, (0, 0))
                self._last_changes[uuid] = (direct_count - 1, reverse_count)
            else:
                patch.direct_count -= 1

            logger.debug("Direct patch reference removed, %s, postponed is %s",
                         patch, postponed)

            if patch.direct_count == patch.reverse_count == 0 and \
                    not postponed:
                session.delete(patch)
                self._delete_patch(uuid)
            else:
                session.merge(patch)

            if self._extended_logging:
                patch_path = self.get_patch_path(uuid)
                self._logger.debug("Direct patch reference removed, %s, "
                                   "postponed is %s. "
                                   "Patch file exists: %s. Reason: %s",
                                   patch, postponed, exists(patch_path),
                                   reason)

    def remove_reverse_patch(self, uuid, reason="", postponed=False):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None or not patch.reverse_count and not postponed:
                return

            if postponed:
                direct_count, reverse_count = self._last_changes.get(
                    uuid, (0, 0))
                self._last_changes[uuid] = (direct_count, reverse_count - 1)
            else:
                patch.reverse_count -= 1

            logger.debug("Reverse patch reference removed, %s, postponed is %s",
                         patch, postponed)

            if patch.direct_count == patch.reverse_count == 0 and \
                    not postponed:
                session.delete(patch)
                self._delete_patch(uuid)
            else:
                session.merge(patch)

            if self._extended_logging:
                patch_path = self.get_patch_path(uuid)
                self._logger.debug("Reverse patch reference removed, %s, "
                                   "postponed is %s. "
                                   "Patch file exists: %s. Reason: %s",
                                   patch, postponed, exists(patch_path),
                                   reason)

    def commit_last_changes(self):
        if not self._last_changes:
            return

        with self.create_session() as session:
            patches = session.query(Patch) \
                .filter(Patch.uuid.in_(tuple(self._last_changes.keys()))) \
                .all()
            mappings = []
            for patch in patches:
                mappings.append(dict(
                    id=patch.id,
                    direct_count=patch.direct_count +
                                 self._last_changes[patch.uuid][0],
                    reverse_count=patch.reverse_count +
                                 self._last_changes[patch.uuid][1],
                ))
            session.bulk_update_mappings(Patch, mappings)

            self._delete_patches_not_used(session)

        logger.debug("Commited last patches changes for %s uuids",
                     len(self._last_changes))
        if self._extended_logging:
            self._logger.debug("Commited last patches changes for %s",
                               self._last_changes)

        self.clear_last_changes()

    def _delete_patches_not_used(self, session):
        query = session.query(Patch) \
            .filter(Patch.direct_count == 0) \
            .filter(Patch.reverse_count == 0)

        patches = query.all()
        logger.debug("Deleteing %s unused patches...", len(patches))
        if self._extended_logging:
            self._logger.debug("Deleteing unused patches %s", patches)
        list(map(lambda p: self._delete_patch(p.uuid), patches))
        query.delete()

    def clear_last_changes(self):
        self._last_changes.clear()

    def update_patch(self, uuid, size):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None or patch.size:
                return

            patch.size = size
            patch = session.merge(patch)
        self._patches_queue.put(patch)

    def activate_patch(self, uuid):
        with self.create_session() as session:
            patch = self._get_patch(uuid)
            if patch is None:
                return
            patch.active = True
            patch = session.merge(patch)
        self._patches_queue.put(patch)

    def get_patch_path(self, uuid):
        return join(get_patches_dir(self._root), uuid)

    def patch_exists(self, uuid):
        patch_path = self.get_patch_path(uuid)
        return exists(patch_path) and stat(patch_path).st_size > 0

    def get_patch_size(self, uuid):
        patch_path = self.get_patch_path(uuid)
        return stat(patch_path).st_size if exists(patch_path) else 0

    @run_daemon
    def _patches_queue_processor(self):
        self.check_patches(only_not_exist=False)
        while True:
            patch = self._patches_queue.get(True)
            if patch is None:
                break
            self._check_patch(patch)

    def check_patches(self, only_not_exist=True):
        with self.create_session() as session:
            patches = \
                session.query(Patch)
            if only_not_exist:
                patches = patches.filter(Patch.exist == 0)
            patches = patches.all()
        for patch in patches:
            self._patches_queue.put(patch)

    def clean(self, with_files=True):
        with self.create_session() as session:
            self._log_db(session)

            if with_files:
                patches = session.query(Patch).all()
                for patch in patches:
                    self._delete_patch(patch.uuid, True)

        try:
            self._engine.execute("delete from patches")
            logger.info("Cleaned patches data base")
        except Exception as e:
            logger.error("Failed to clean patches DB (%s)", e)
            if not self.db_file_exists():
                raise e

    def get_patch_uuid_and_size(self, new_hash, old_hash):
        with self.create_session() as session:
            patch = session.query(Patch) \
                .filter(Patch.new_hash == new_hash) \
                .filter(Patch.old_hash == old_hash) \
                .one_or_none()
            if patch:
                return patch.uuid, patch.size
            return None, 0

    def _check_patch(self, patch):
        with self.create_session() as session:
            patch = session.query(Patch)\
                .filter(Patch.uuid == patch.uuid).one_or_none()
        if not patch or not patch.active:
            return

        with self._patch_existance_lock:
            if patch.uuid in self._patches_on_registration:
                return

            if not self.patch_exists(patch.uuid):
                if patch.exist:
                    self._mark_patch_exist(patch, False)
                    # to compensate latter referencies removal
                    self._add_copies_referencies(
                        patch.uuid, patch.old_hash, patch.new_hash,
                        False, "_check_patch")
                try:
                    self._create_patch(patch)
                    if self._download_manager:
                        self._download_manager.cancel_download(patch.uuid)
                except Exception as e:
                    if not patch.size:
                        logger.debug("Waiting for patch update. "
                                     "Exception %s", e)
                        return  # wait for patch to update
                    self._download_patch(patch)
            elif not patch.exist:
                patch.size = self.get_patch_size(patch.uuid)
                self._patches_on_registration.add(patch.uuid)
                self.patch_created(patch.uuid, patch.size)

    def on_patch_registered(self, uuid):
        if not self._started:
            return

        with self._patch_existance_lock:
            self._patches_on_registration.discard(uuid)
            patch = self._get_patch(uuid)
            if patch is None:
                logger.warning("Patch must exist in db for uuid %s", uuid)
                return

            if self.patch_exists(patch.uuid) and not patch.exist:
                self._mark_patch_exist(patch, True)
                self._remove_copies_referencies(
                    patch.uuid, patch.old_hash, patch.new_hash,
                    False, "on_patch_registered")
            else:
                logger.warning("Patch with uuid %s not found in filesysytem",
                               patch.uuid)

    def _mark_patch_exist(self, patch, exist):
        with self.create_session() as session:
            patch = \
                session.query(Patch).filter(Patch.id == patch.id).one_or_none()
            if not patch:
                return
            patch.exist = exist
            session.merge(patch)

    def _create_patch(self, patch):
        new_copy = self._get_copy(patch.new_hash)
        old_signature = self._get_signature(patch.old_hash) \
            if patch.old_hash and patch.old_hash != EMPTY_FILE_HASH else None
        new_signature = self._get_signature(patch.new_hash)
        if self._tracker:
            start_time = time()
            file_size = getsize(new_copy)
        patch_info = Rsync.create_patch(
            uuid=patch.uuid, modify_file=new_copy, root=self._root,
            old_blocks_hashes=old_signature, new_blocks_hashes=new_signature,
            old_file_hash=patch.old_hash, new_file_hash=patch.new_hash)
        patch_size = patch_info['archive_size']
        if not patch_size or not self.patch_exists(patch.uuid):
            return
        if self._tracker:
            self._tracker.monitor_patch_create(
                file_size, patch_size, time() - start_time)
        self.update_patch(patch.uuid, patch_size)
        self._patches_on_registration.add(patch.uuid)
        self.patch_created(patch.uuid, patch_size)

    def _get_copy(self, hash):
        copy_path = join(get_copies_dir(self._root), hash)
        return copy_path

    def _get_signature(self, hash):
        signature_path = join(get_signatures_dir(self._root), hash)
        with open(signature_path, 'rb') as f:
            return SortedDict(load(f))

    def _download_patch(self, patch):
        if not self._download_manager:
            return

        files_info = []
        if self._events_db:
            files_list, _ = self._events_db.get_files_list_by_diff_uuid(
                patch.uuid, direct_patch_only=True)
            if not files_list:
                files_list, _ = self._events_db.get_files_list_by_diff_uuid(
                    patch.uuid)
            if files_list:
                for target_file_path, timestamp in files_list:
                    info = {
                        "target_file_path": target_file_path,
                        "mtime": get_local_time_from_timestamp(timestamp),
                        "is_created": False,
                        "is_deleted": False}
                    files_info.append(info)

        self._download_manager.add_patch_download(
            self._calculate_patch_priority(patch),
            patch.uuid,
            patch.size,
            join(get_patches_dir(self._root), patch.uuid),
            '',
            self._on_patch_downloaded,
            self._on_patch_download_failure,
            files_info
        )

    def _calculate_patch_priority(self, patch):
        references_count = patch.direct_count + patch.reverse_count
        if patch.active and patch.direct_count:
            priority = DOWNLOAD_PRIORITY_WANTED_DIRECT_PATCH
        elif patch.reverse_count:
            priority = DOWNLOAD_PRIORITY_REVERSED_PATCH
        else:
            priority = DOWNLOAD_PRIORITY_DIRECT_PATCH
        return priority + references_count

    def _on_patch_downloaded(self, task):
        patch = self._get_patch(task.id)
        if patch is None:
            return
        if patch.exist:
            return
        self._patches_queue.put(patch)

    def _on_patch_download_failure(self, task):
        patch = self._get_patch(task.id)
        if patch is None:
            return
        if not self._retry_download_timer:
            self._retry_download_timer = Timer(
                self._retry_download_timeout,
                self._redownload)
            self._retry_download_timer.start()

        self._failed_downloads.add(patch)

    def _delete_patch(self, uuid, silently=False):
        patch_path = join(
            get_patches_dir(self._root), uuid)
        try:
            remove_file(patch_path)
            logger.info("Patch deleted %s", uuid)
        except Exception as e:
            logger.error("Can't delete patch. "
                         "Possibly sync folder is removed %s", e)
            self.possibly_sync_folder_is_removed()
            return

        if not silently:
            self.patch_deleted.emit(uuid)

    def on_online_nodes_changed(self, nodes):
        self._redownload()

    def _redownload(self):
        self._retry_download_timer = None
        if not self._failed_downloads:
            return

        logger.debug("Retry failed downloads")
        failed_downloads = self._failed_downloads
        self._failed_downloads = set()
        for patch in failed_downloads:
            self._patches_queue.put(patch)

    def db_file_exists(self):
        return exists(self._db_file) and getsize(self._db_file) > 0

    def _log_db(self, session):
        if not self._extended_logging:
            return

        patches = session.query(Patch).all()
        for i, patch in enumerate(patches):
            patch_path = self.get_patch_path(patch.uuid)
            self._logger.debug("Patch %s: %s. File exists: %s",
                               i, patch, exists(patch_path))

    def _add_copies_referencies(self, uuid, old_hash, new_hash,
                                postponed=False, reason_prefix=""):
        self._copies_storage.add_copy_reference(
            new_hash,
            reason="{} new_hash {}".format(reason_prefix, uuid),
            postponed=postponed)
        self._copies_storage.add_copy_reference(
            old_hash,
            reason="{} old_hash {}".format(reason_prefix, uuid),
            postponed=postponed)

    def _remove_copies_referencies(self, uuid, old_hash, new_hash,
                                   postponed=False, reason_prefix=""):
        self._copies_storage.remove_copy_reference(
            new_hash,
            reason="{} new_hash {}".format(reason_prefix ,uuid),
            postponed=postponed)
        self._copies_storage.remove_copy_reference(
            old_hash,
            reason="{} old_hash {}".format(reason_prefix ,uuid),
            postponed=postponed)
