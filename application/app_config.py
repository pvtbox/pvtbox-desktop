# -*- coding: utf-8 -*-

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
from hashlib import sha512
from uuid import uuid4

from common import config
from common.constants import UNKNOWN_LICENSE, REGULAR_URI

# Setup logging
from common.utils import is_portable

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Config(config.ConfigLoader):
    def __init__(self, settings_changed_signal,
                 settings_of_interest=None,
                 settings_of_interest_signal=None):
        object.__init__(self)
        # Emitted when some parameter values has changed
        self._settings_changed_signal = settings_changed_signal
        self._settings_of_interest = settings_of_interest \
            if settings_of_interest else list()
        self._settings_of_interest_signal = settings_of_interest_signal
        self.config = dict()
        self.config_file_name = ""
        self.encrypt = True

    def is_empty(self):
        return not self.config

    def __getattr__(self, name):
        try:
            return self.config[name]
        except KeyError:
            raise AttributeError(name)

    def get_setting(self, name, default=None):
        return self.config.get(name, default)

    def set_settings(self, new_values):
        assert isinstance(new_values, dict)
        self.config.update(new_values)
        self._settings_changed_signal.emit(new_values)
        self._check_settings_of_interest(new_values)

    def set_config(self, config):
        assert isinstance(config, dict)
        self.config.clear()
        self.config.update(config)
        self._check_settings_of_interest(config)

    def set_config_filename(self, filename):
        self.config_file_name = filename

    def _check_settings_of_interest(self, new_values):
        if self._settings_of_interest_signal:
            si = {s: new_values[s] for s in new_values
                  if s in self._settings_of_interest}
            self._settings_of_interest_signal.emit(si)

    def sync(self):
        if not self.config_file_name:
            return
        config.ConfigLoader.sync(self)


class MainConfigLoader(config.ConfigLoader):

    @staticmethod
    def default_config():
        return dict(
            autologin=not is_portable(),
            license_type=UNKNOWN_LICENSE,  # unknown licence by default
            node_hash=sha512(str(uuid4()).encode()).hexdigest(),
            user_hash=None,
            user_email=None,
            user_password_hash=None,
            devices=dict(),
            autoupdate=True,
            next_update_check=0,
            logging_disabled=False,
            download_backups=False,
            host=REGULAR_URI,
            old_host=REGULAR_URI,
            smart_sync=True,
        )

    def check(self):
        """
            Checks config consistency. Raises error if not consistent.
            For new keys see comment below
        """
        assert isinstance(self.config.get('autologin'), bool), 'autologin'
        assert len(self.config.get('node_hash','')) == 128, 'node_hash'
        assert isinstance(self.config.get('license_type'), int), \
            'license_type'
        assert isinstance(self.config.get('devices'), dict), 'devices'
        assert isinstance(self.config.get('autoupdate'), bool), 'autoupdate'
        assert isinstance(
            self.config.get('next_update_check'), (int, float)), \
            'next_update_check'
        assert isinstance(self.config.get('logging_disabled'), bool), \
            'logging_disabled'
        assert isinstance(self.config.get('download_backups'), bool), \
            'download_backups'
        assert isinstance(self.config.get('host'), str), \
            'host'
        assert isinstance(self.config.get('old_host'), str), \
            'old_host'
        assert isinstance(self.config.get('smart_sync'), bool), \
            'smart_sync'
        # if new key is added to config, it's mandatory to use 'get(key)'
        # here, not pure  self.config[key]


def load_config(config_file='main.conf', check=True):
    return config.load_config(config_file, MainConfigLoader, check=check)
