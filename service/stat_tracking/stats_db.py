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
from uuid import uuid4
from os.path import  exists

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, Unicode

from common.utils import remove_file
from common.file_path import FilePath
from db_migrations import upgrade_db, stamp_db

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

Base = declarative_base()


class Event(Base):
    __tablename__ = 'events'

    id = Column(Integer(), primary_key=True)
    name = Column(Unicode(), nullable=False)

    def __repr__(self):
        return \
            "name='{self.name}' " \
            .format(self=self)


class InstallParams(Base):
    __tablename__ = 'install_params'

    name = Column(Unicode(), primary_key=True)
    value = Column(Unicode(), nullable=False)

    def __repr__(self):
        return \
            "name='{self.name}' " \
            "value='{self.value}' " \
            .format(self=self)


class StatsDB(object):
    """
    Interface for statistics database
    """

    def __init__(self, db_file):
        self._db_file = db_file

        self._has_events = True
        new_db_file = not exists(self._db_file)

        if not new_db_file:
            # Database migration. It can be executed before opening db
            try:
                upgrade_db("stats_db", db_filename=self._db_file)
            except Exception as e:
                remove_file(self._db_file)
                new_db_file = True
                logger.warning("Can't upgrade stats db. "
                               "Reason: (%s) Creating...", e)

        self._engine = create_engine('sqlite:///{}'.format(
            FilePath(self._db_file)))
        self._Session = sessionmaker(bind=self._engine)

        Base.metadata.create_all(self._engine, checkfirst=True)

        if new_db_file:
            try:
                stamp_db("stats_db", db_filename=self._db_file)
            except Exception as e:
                logger.error("Error stamping stats db: %s", e)

        logger.debug("Stats DB init")

    @contextmanager
    def create_session(self):
        session = self._Session()
        session.expire_on_commit = False
        session.autoflush = True

        try:
            yield session
            session.commit()
        except Exception as e:
            logger.error("Stats DB session rollback. Reason: %s", e)
            session.rollback()
            raise e
        finally:
            session.close()

    def load_event(self):
        if not self._has_events:
            return None, None

        with self.create_session() as session:
            event = session.query(Event)\
                .order_by(Event.id)\
                .limit(1)\
                .one_or_none()
            if event is None:
                self._has_events = False
                return None, None

            return event.id, event.name

    def save_event(self, event_str):
        self._has_events = True
        with self.create_session() as session:
            event = Event(name=event_str)
            session.add(event)

    def delete_event(self, event_id):
        with self.create_session() as session:
            session.query(Event)\
                .filter(Event.id == event_id)\
                .delete(synchronize_session=False)

    def get_installation_id(self):
        with self.create_session() as session:
            iid_param = session.query(InstallParams)\
                .filter(InstallParams.name == 'installation_id')\
                .one_or_none()
            if not iid_param:
                installation_id = str(uuid4())
                session.add(InstallParams(
                    name='installation_id', value=installation_id))
            else:
                installation_id = iid_param.value
        return installation_id
