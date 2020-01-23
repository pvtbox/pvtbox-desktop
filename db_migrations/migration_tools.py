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
from alembic.config import Config
from alembic import command

from common.file_path import FilePath


def _create_config(env_name, db_filename):
    # create alembic config
    alembic_config = Config()

    # set script location as <module_name>:<environment_dir>
    script_location = "{}:{}".format(__package__, env_name)
    alembic_config.set_main_option("script_location", script_location)

    # set URL template
    alembic_config.set_main_option("sqlalchemy.url", "sqlite:///{filename}")

    if db_filename is not None:
        alembic_config.set_main_option("filename", FilePath(db_filename))
    return alembic_config


def upgrade_db(env_name, db_filename=None):
    """
        The function upgrades database within migration
        @param env_name - directory name contains environment for specified db
                          e.g. "events_db" (see dir db_migrations/.. )
        @param db_filename - full name to database file
                          e.g. "/home/user/.pvtbox/events.db"
    """
    alembic_config = _create_config(env_name, db_filename)
    command.upgrade(alembic_config, "head")


def stamp_db(env_name, db_filename=None):
    """
        The function upgrades database within migration
        @param env_name - directory name contains environment for specified db
                          e.g. "events_db" (see dir db_migrations/.. )
        @param db_filename - full name to database file
                          e.g. "/home/user/.pvtbox/events.db"
    """
    alembic_config = _create_config(env_name, db_filename)
    command.stamp(alembic_config, "head")
