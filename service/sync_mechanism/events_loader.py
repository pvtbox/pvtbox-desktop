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
from time import time

from sqlalchemy import func, or_, and_
from sqlalchemy.sql import text as sql_text

from service.events_db import Event, File
from common.path_utils import is_contained_in_dirs


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

EVENTS_QUERY_LIMIT = 100

class EventsLoader(object):

    def __init__(self, parent, db, fs, excluded_dirs):
        self._parent = parent
        self._db = db
        self._fs = fs
        self._excluded_dirs = excluded_dirs

    def load_remote_events(self, session, events_count, exclude_files):
        # Count <= EVENTS_QUERY_LIMIT
        # load folder creation/movement events
        folders_events = self.load_folders_events(session,
                                                  exclude_files=exclude_files)
        if folders_events:
            remote_creations_events = []
            remote_not_creations_events = []
            excluded_events = []
        else:
            # Count <= EVENTS_QUERY_LIMIT
            remote_creations_events = \
                self.load_remote_creations_events(
                    session, events_count, exclude_files)
            events_count -= len(remote_creations_events)

            # Count <= EVENTS_QUERY_LIMIT
            remote_not_creations_events = \
                self.load_remote_not_creations_events(
                    remote_creations_events, session,
                    events_count, exclude_files)
            events_count -= len(remote_not_creations_events)

            # Count <= EVENTS_QUERY_LIMIT
            excluded_events = self.load_excluded_events(
                session, events_count, exclude_files)

            if not remote_creations_events and \
                    not remote_not_creations_events and \
                    not excluded_events and \
                    not exclude_files:
                # load folder deletion events
                folders_events = self.load_folders_events(
                    session, deleted=True)
        return list(folders_events
                    + remote_creations_events
                    + remote_not_creations_events
                    + excluded_events
                    )

    def load_local_events(self, session, events_count, exclude_files):
        start_time = time()
        local_events = session.query(Event).from_statement(sql_text(
            """
                select final_e.* from events final_e
                where final_e.id in (
                    select min(e.id) from events e, files f
                    where f.id = e.file_id
                    and not f.excluded
                    and e.file_id not in ({})
                    and e.state in ('occured', 'conflicted')
                    and (
                        f.folder_id is null
                        or f.folder_id in (
                            select processed_f.id from files processed_f, 
                            events processed_e
                            where processed_f.is_folder
                            and not processed_f.excluded
                            and processed_f.uuid is not null
                            and processed_f.event_id = processed_e.id
                            and processed_e.state not in 
                            ('occured', 'conflicted')
                        )
                    )
                    group by f.id
                )
                order by final_e.type != 'delete', final_e.id
                limit {}
            """.format(
                    ','.join(map(str, exclude_files)),
                    min(events_count, EVENTS_QUERY_LIMIT)
            ))).all()
        if local_events:
            logger.debug(
                "local_events queried in %s sec: [%s]",
                time() - start_time,
                ', '.join(map(lambda ev: str(ev.id), local_events)))
        return local_events

    def load_folders_events(self, session, deleted=False, exclude_files=()):
        start_time = time()
        eq_str = '=' if deleted else '<>'
        folders_events = session.query(Event).from_statement(sql_text(
            """
                select final_e.* from events final_e
                where final_e.id in (
                    select max(unhandled_e.id) from events unhandled_e, files unhandled_f
                    where unhandled_f.id = unhandled_e.file_id
                    and not unhandled_f.excluded
                    and unhandled_f.is_folder
                    and unhandled_e.file_id not in ({})
                    and unhandled_e.state in ('received', 'downloaded')
                    and (
                            (
                                unhandled_f.event_id is null
                                and unhandled_f.last_skipped_event_id is null
                            )
                        or (
                                unhandled_f.last_skipped_event_id is null
                                and unhandled_f.event_id < unhandled_e.id
                            )
                        or (
                                unhandled_f.last_skipped_event_id is not null
                                and unhandled_f.last_skipped_event_id < unhandled_e.id
                                and unhandled_f.event_id is Null
                            )
                        or (
                                unhandled_f.last_skipped_event_id is not null
                                and unhandled_f.last_skipped_event_id < unhandled_e.id
                                and unhandled_f.event_id < unhandled_f.last_skipped_event_id
                            )
                    )
                    and unhandled_e.server_event_id is not null
                    group by unhandled_f.id
                )
                and (
                    final_e.folder_uuid is null
                    or final_e.folder_uuid in (
                        select processed_f.uuid from files processed_f, events processed_e
                        where processed_f.is_folder
                        and processed_f.id = processed_e.file_id
                        and not processed_f.excluded
                        and processed_f.event_id in (
                            select max(existing_e.id) from events existing_e
                            where existing_e.is_folder
                            group by existing_e.file_id
                        )
                    )
                    or final_e.folder_uuid in (
                        select excluded_f.uuid from files excluded_f
                        where excluded_f.excluded
                        and excluded_f.is_folder
                    )
                )
                and final_e.type {} 'delete'
                order by final_e.id
                limit {}
            """.format(','.join(map(str, exclude_files)),
            eq_str, EVENTS_QUERY_LIMIT))).all()
        if folders_events:
            logger.debug(
                "folders_events queried in %s sec: [%s]",
                time() - start_time,
                ', '.join(map(lambda ev: str(ev.id), folders_events)))
        return folders_events

    def load_remote_creations_events(self, session,
                                     events_count, exclude_files):
        if events_count <= 0:
            return []

        start_time = time()
        limit = min(EVENTS_QUERY_LIMIT, events_count)
        remote_creations_events = session.query(Event).from_statement(sql_text(
            """
                select p.* from events p
                where p.id in (
                    select max(e.id) from events e, files f
                    where f.id = e.file_id
                    and not f.excluded
                    and not f.is_folder
                    and f.event_id is null
                    and f.last_skipped_event_id is null
                    and e.server_event_id is not null
                    and e.file_id not in ({})
                    group by f.id
                )
                and p.type <> 'delete'
                order by p.id
                limit {}
            """.format(','.join(map(str, exclude_files)),
            limit))).all()
        remote_creations_events = sorted(
            remote_creations_events, key=lambda ev: ev.file_size)
        if remote_creations_events:
            logger.debug(
                "remote_creations_events queried in %s sec: [%s]",
                time() - start_time,
                ', '.join(map(lambda ev: str(ev.id), remote_creations_events)))

        return remote_creations_events

    def load_remote_not_creations_events(
            self, remote_creations_events, session,
            events_count, exclude_files):
        if events_count <= 0:
            return []

        start_time = time()
        limit = min(EVENTS_QUERY_LIMIT, events_count)
        remote_creations_events_files_ids = [
            e.file.id for e in remote_creations_events]
        remote_creations_events_files_ids.extend(exclude_files)
        null_event_id_str = """or (
                                f.event_id is null
                                and f.last_skipped_event_id is null
                            )""" if not remote_creations_events else ""

        remote_not_creations = session.query(Event).from_statement(sql_text(
            """
                select final_e.* from events final_e
                where final_e.id in (
                    select min(e.id) from events e, files f
                    where f.id = e.file_id
                    and not f.excluded
                    and not f.is_folder
                    and e.file_id not in ({})
                    and e.server_event_id is not null
                    and e.state in ('received', 'downloaded')
                    and (
                            (
                                f.last_skipped_event_id is null
                                and f.event_id < e.id
                            )
                            or (
                                f.last_skipped_event_id is not null
                                and f.last_skipped_event_id < e.id
                                and f.event_id is null
                            )
                            or (
                                f.last_skipped_event_id is not null
                                and f.last_skipped_event_id < e.id
                                and f.event_id <= f.last_skipped_event_id
                            )
                            or (
                                f.last_skipped_event_id is not null
                                and f.last_skipped_event_id < e.id
                                and f.event_id is not null
                                and f.event_id < e.id
                            )
                            {}
                    )
                    group by f.id
                )
                order by final_e.id
                limit {}
            """.format(
                ','.join(map(str, remote_creations_events_files_ids)),
                null_event_id_str,
                limit
            ))).all()
        if remote_not_creations:
            logger.debug(
                "remote_not_creations queried in %s sec: [%s]",
                time() - start_time,
                ', '.join(map(lambda ev: str(ev.id), remote_not_creations)))

        return remote_not_creations

    def load_excluded_events(self, session, events_count, exclude_files):
        if events_count <= 0:
            return []

        start_time = time()
        excluded_uuids = session.query(File.uuid) \
            .filter(File.is_folder) \
            .filter(File.excluded).all()
        excluded_uuids = [u.uuid for u in excluded_uuids]

        offset = 0
        excluded_events = []
        limit = min(EVENTS_QUERY_LIMIT, events_count)
        while True:
            excluded_portion = session.query(Event).from_statement(sql_text(
                """
                    select final_e.* from events final_e
                    where final_e.id in (
                        select max(last_event.id) from events last_event 
                        where last_event.file_id in (
                            select moved_file.id from events move_event, files moved_file 
                            where moved_file.id = move_event.file_id
                            and move_event.id in (
                                select max(event.id) from events event, files file
                                where file.id = event.file_id
                                and file.excluded
                                and event.type == 'move'
                                group by file.id
                            )
                            and move_event.file_id not in ({})
                            and (
                                move_event.folder_uuid is null
                                or move_event.folder_uuid not in ({})
                            )
                        )
                        group by last_event.file_id
                    )
                    order by final_e.is_folder desc, final_e.id
                    limit {}, {}
                """.format(
                    ','.join(map(str, exclude_files)),
                    ','.join(["'{}'".format(uuid) for uuid in excluded_uuids]),
                    offset, EVENTS_QUERY_LIMIT))) \
                .all()
            if not excluded_portion:
                break
            excluded_portion_filtered = [e for e in excluded_portion if not is_contained_in_dirs(
                    self._db.get_path_from_event(e, session),
                    self._excluded_dirs)]
            excluded_events.extend(excluded_portion_filtered[:])
            if len(excluded_portion) < EVENTS_QUERY_LIMIT or \
                    len(excluded_events) >= limit:
                break
            offset += EVENTS_QUERY_LIMIT

        if excluded_events:
            logger.debug(
                "excluded_events queried in %s sec: [%s]",
                time() - start_time,
                ', '.join(map(lambda ev: str(ev.id), excluded_events)))

        return excluded_events

    def load_new_files_to_skip(self, limit, session):
        start_time = time()
        events_count = 0
        new_files_to_skip = session.query() \
            .with_entities(Event.id, Event.file_id) \
            .from_statement(sql_text(
            """
                select final_e.id, final_e.file_id
                from events final_e
                inner join files f on final_e.file_id = f.id
                where final_e.id = (
                    select ee.id
                    from events ee
                    where ee.file_id = final_e.file_id
                    order by ee.id desc limit 1
                )
                and not f.excluded
                and final_e.server_event_id is not null
                and (
                       (f.event_id is null
                        and f.last_skipped_event_id is null
                       )
                    or
                       (f.event_id is not null
                        and f.event_id = final_e.last_event_id
                        and final_e.server_event_id < 0
                       )
                )
                and not final_e.erase_nested
                and final_e.type = 'delete'
                limit {}
            """.format(limit))).all()
        if new_files_to_skip:
            events_count = len(new_files_to_skip)
        logger.debug(
            "new_files_to_skip queried in %s sec, "
            "%s files, ~%s events to skip",
            time() - start_time, len(new_files_to_skip), events_count)

        return new_files_to_skip, events_count

    def load_existing_files_to_skip(self, limit, session):
        start_time = time()
        events_count = 0
        existing_files_to_skip = session.query() \
            .with_entities(Event.last_event_id, Event.file_id) \
            .from_statement(sql_text(
            """
                select final_e.last_event_id, final_e.file_id
                from events final_e
                inner join files f on final_e.file_id = f.id
                where final_e.id = (
                    select ee.id
                    from events ee
                    where ee.file_id = final_e.file_id
                    order by ee.id desc limit 1
                )
                and not f.excluded
                and final_e.server_event_id is not null
                and (
                       (f.event_id is not null
                        and f.event_id < final_e.last_event_id
                       )
                     or
                       (f.last_skipped_event_id is not null
                        and f.last_skipped_event_id < final_e.last_event_id
                       )
                )
                and final_e.type = 'delete'
                limit {}
            """.format(limit))).all()
        if existing_files_to_skip:
            events_count = len(existing_files_to_skip)
        logger.debug(
            "existing_files_to_skip queried in %s sec, "
            "%s files, ~%s events to skip",
            time() - start_time, events_count, events_count)

        return existing_files_to_skip, events_count

    def clean_trash_local(self):
        logger.debug("Cleaning local trash in db")
        with self._db.create_session(
                enable_logging=True,
                read_only=False) as session:
            events = session.query(Event) \
                .select_from(File) \
                .join(File.events) \
                .filter(Event.state == 'occured') \
                .filter(File.uuid.is_(None)) \
                .filter(File.folder_id.isnot(None)) \
                .all()
            events = list(filter(lambda e: not e.file.folder, events))
            if not events:
                return

            count = len(events)
            deleted_files = set()
            for event in events:
                if event.file not in deleted_files:
                    self._fs.change_events_file_id(event.file_id, None)
                    deleted_files.add(event.file)
                    session.delete(event.file)
                session.delete(event)
                self._parent.change_processing_events_counts(local_inc=-1)
            logger.debug("Cleaned %s local events", count)

    def clean_trash_remote(self):
        logger.debug("Cleaning remote trash in db")
        with self._db.create_session(
                enable_logging=True,
                read_only=False) as session:
            double_delete_events = session.query(Event)\
                .from_statement(sql_text(
                """select events.* from events where events.file_id in 
                (select double_e.file_id from events double_e where 
                double_e.type = 'delete' 
                and double_e.state in ('downloaded', 'sent') 
                group by double_e.file_id having count(double_e.id) > 1) 
                and events.type = 'delete'""")) \
                .all()
            if not double_delete_events:
                return

            count = 0
            files = {e.file for e in double_delete_events}
            for file in files:
                events = sorted(
                    filter(lambda e: e.file_id == file.id ,double_delete_events),
                    key=lambda e: e.server_event_id)
                max_event = events[-1]
                file.event_id = max_event.id
                for event in events[:-1]:
                    if event.state == 'downloaded':
                        self._parent.change_processing_events_counts(
                            remote_inc=-1)
                    session.delete(event)
                    count += 1
            logger.debug("Cleaned %s remote events", count)

    def _count_excluded_events(self, session):
        excluded_uuids = session.query(File.uuid) \
            .filter(File.is_folder) \
            .filter(File.excluded).all()
        excluded_uuids = [u.uuid for u in excluded_uuids]
        if not excluded_uuids:
            return 0

        excluded_events = session.query(Event).from_statement(sql_text(
            """
                select final_e.* from events final_e
                where final_e.id in (
                    select max(last_event.id) from events last_event 
                    where last_event.file_id in (
                        select moved_file.id from events move_event, files moved_file 
                        where moved_file.id = move_event.file_id
                        and move_event.id in (
                            select max(event.id) from events event, files file
                            where file.id = event.file_id
                            and file.excluded
                            and event.type == 'move'
                            group by file.id
                        )
                        and (
                            move_event.folder_uuid is null
                            or move_event.folder_uuid not in ({})
                        )
                    )
                    group by last_event.file_id
                )
                order by final_e.is_folder desc, final_e.id
            """.format(
                ','.join(["'{}'".format(uuid) for uuid in excluded_uuids]),
                ))) \
            .all()

        if not excluded_events:
            return 0

        excluded_count = len(list(filter(
            lambda e: not is_contained_in_dirs(
                self._db.get_path_from_event(e, session),
                self._excluded_dirs),
            excluded_events)))

        return excluded_count

    def recalculate_processing_events_count(self, session):
        local_count = session.query(func.count(Event.id)) \
            .filter(Event.state.in_(['occured', 'conflicted'])) \
            .scalar()
        remote_count = session.query(func.count()) \
            .select_from(Event) \
            .filter(Event.file_id == File.id) \
            .filter(File.excluded == 0) \
            .filter(
            or_(
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
                ),
                and_(
                    File.last_skipped_event_id.isnot(None),
                    File.last_skipped_event_id < Event.id,
                    File.event_id .isnot(None),
                    File.event_id < Event.id
                )
            )) \
            .filter(Event.state.in_(['received', 'downloaded'])) \
            .scalar()

        remote_count += self._count_excluded_events(session)
        return local_count, remote_count
