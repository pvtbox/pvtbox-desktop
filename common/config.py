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
from collections import namedtuple
from hashlib import sha512
from json import loads, dumps
from uuid import UUID, getnode
from uuid import uuid4

from common.signal import Signal
from common.utils import get_data_dir, get_cfg_filename, xor_with_key, is_portable
from common.utils import get_default_lang, get_device_name
from common.constants import UNKNOWN_LICENSE, REGULAR_URI
from common.file_path import FilePath

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ConfigLoader:

    def __init__(self, config_file):
        # Emitted when some parameter values has changed
        # dict: name -> namedtuple(old_value, new_value)
        self.settings_changed = Signal(dict)

        self.encrypt = True
        self.config_file_name = config_file

        checked = False
        while not checked:
            try:
                self.config, need_sync = self.read_config_file()
                self.check()
                checked = True
                if need_sync:
                    self.sync()
            except AssertionError as e:
                logger.warning(
                    "Assertion error while loading config: %s, using default value", e)
                self.config[str(e)] = self.default_config()[str(e)]
                self.sync()
            except (KeyError, ValueError, IOError) as e:
                logger.warning(
                    "Error while loading config: %s, using default config", e)
                self.config = self.default_config()
                self.sync()

    def read_config_file(self):
        need_sync = False
        with open(self.config_file_name, 'rb') as f:
            data = f.read()
        try:
            decrypted_data = xor_with_key(data)
            config = loads(decrypted_data)
            secret = UUID(int=158790876260364472748646807733425668096 + getnode()).bytes
            if is_portable():
                config['user_hash'] = None
                config['user_password_hash'] = None
                config['sync_directory'] = FilePath(get_data_dir())
                config['conflict_file_suffix'] = 'Conflicted copy from {}'\
                    .format(get_device_name())
            else:
                try:
                    if config.get('user_hash', None):
                        config['user_hash'] = xor_with_key(
                            bytes.fromhex(config['user_hash']),
                            secret).decode()
                except Exception as e:
                    logger.debug("Error decoding user hash: %s", e)
                    config["user_hash"] = None
                try:
                    if config.get('user_password_hash', None):
                        config['user_password_hash'] = xor_with_key(
                            bytes.fromhex(config['user_password_hash']),
                            secret).decode()
                except Exception as e:
                    logger.debug("Error decoding user password hash: %s", e)
                    config["user_password_hash"] = None
        except ValueError as e:
            logger.warning("Error: %s", e)
            config = loads(data)
            config["user_hash"] = None
            config["user_password_hash"] = None
            need_sync = True
        return config, need_sync

    @staticmethod
    def default_config():
        return dict(
            autologin=not is_portable(),
            upload_limit=0,
            download_limit=0,
            fs_events_processing_delay=1,
            fs_events_processing_period=1,
            fs_folder_timeout=2,
            sync_directory=FilePath(get_data_dir()),
            conflict_file_suffix='Conflicted copy from {}'
                .format(get_device_name()),
            node_hash=sha512(uuid4().bytes).hexdigest(),
            user_hash=None,
            send_statistics=True,
            license_type=UNKNOWN_LICENSE,  # unknown licence by default
            lang=None,  # System language
            user_email=None,
            last_user_email=None,
            user_password_hash=None,
            http_downloader_timeout=3,
            excluded_dirs=(),   # List of directories excluded from sync
            max_log_size=100,    # Max log file size Mb
            download_backups=False,
            remote_events_max_total=5000,
            max_remote_events_per_request=100,
            max_relpath_len=3096,
            sync_dir_size=0,
            copies_logging=True,
            excluded_dirs_applied=(),  # List of excluded dirs, applied in DB
            host=REGULAR_URI,
            tracking_address='https://tracking.pvtbox.net:443/',
        )

    def sync(self):
        config = self.config.copy()
        if config.get('autologin', True):
            secret = UUID(int=158790876260364472748646807733425668096 + getnode()).bytes
            if config.get('user_hash', None):
                config['user_hash'] = xor_with_key(
                    config['user_hash'].encode(), secret).hex()
            if config.get('user_password_hash', None):
                config['user_password_hash'] = xor_with_key(
                    config['user_password_hash'].encode(), secret).hex()
        else:
            config.pop('user_hash', None)
            config.pop('user_password_hash', None)
        try:
            data = dumps(config).encode()
        except ValueError as e:
            logger.warning("Can't encode config. Reason: %s", e)
            data = dumps(self.default_config()).encode()
        if self.encrypt:
            data = xor_with_key(data)
        with open(self.config_file_name, 'wb') as f:
            f.write(data)

    def check(self):
        """
            Checks config consistency. Raises error if not consistent.
            For new keys see comment below
        """
        assert isinstance(self.config['autologin'], bool), 'autologin'
        assert isinstance(self.config['upload_limit'], (int, float)), \
            'upload_limit'
        assert isinstance(self.config['download_limit'], (int, float)), \
            'download_limit'
        assert isinstance(
            self.config['fs_events_processing_delay'], (int, float)), \
            'fs_events_processing_delay'
        assert isinstance(
            self.config['fs_events_processing_period'], (int, float)), \
            'fs_events_processing_period'
        assert isinstance(
            self.config['fs_folder_timeout'], (int, float)), \
            'fs_folder_timeout'
        assert isinstance(self.config['sync_directory'], str), \
            'sync_directory'
        assert isinstance(self.config['conflict_file_suffix'], str), \
            'conflict_file_suffix'
        assert len(self.config['node_hash']) == 128, 'node_hash'
        assert isinstance(self.config['send_statistics'], bool), \
            'send_statistics'
        assert isinstance(self.config['license_type'], int), \
            'license_type'
        assert isinstance(
            self.config['http_downloader_timeout'], (int, float)), \
            'http_downloader_timeout'
        assert isinstance(
            self.config['excluded_dirs'], (list, tuple)), \
            'excluded_dirs'
        assert isinstance(
            self.config.get('max_log_size'), (int, float)), \
            'max_log_size'
        assert isinstance(
            self.config.get('download_backups'), bool), \
            'download_backups'
        assert isinstance(
            self.config.get('remote_events_max_total'), (int, float)), \
            'remote_events_max_total'
        assert isinstance(
            self.config.get('max_remote_events_per_request'),
            (int, float)), \
            'max_remote_events_per_request'
        assert isinstance(
            self.config.get('max_relpath_len'),
            (int, float)), \
            'max_relpath_len'
        assert isinstance(
            self.config.get('sync_dir_size'),
            (int, float)), \
            'sync_dir_size'
        assert isinstance(
            self.config.get('copies_logging'), bool), \
            'copies_logging'
        assert isinstance(
            self.config.get('excluded_dirs_applied'), (list, tuple)), \
            'excluded_dirs_applied'
        assert isinstance(
            self.config.get('host'), str), \
            'host'
        assert isinstance(
            self.config.get('tracking_address'), str), \
            'tracking_address'

        # if new key is added to config, it's mandatory to use 'get(key)'
        # here, not pure  self.config[key]

    def __getattr__(self, name):
        try:
            return self.config[name]
        except KeyError:
            logger.warning("Can't read config entry for %s", name)
            pass
        try:
            return self.default_config()[name]
        except KeyError:
            logger.warning("Can't read default config entry for %s", name)
            pass
        raise AttributeError(name)

    @property
    def lang(self):
        value = self.config.get('lang', None)
        if not value:
            value = get_default_lang()

        return value

    def enable_crypt(self):
        self.encrypt = True
        self.sync()

    def disable_crypt(self):
        self.encrypt = False
        self.sync()

    def get_setting(self, name, default=None):
        return self.config.get(name, default)

    ChangedParam = namedtuple(
        "ChangedParam",
        ["old_value", "new_value"])

    def set_settings(self, new_values):
        assert isinstance(new_values, dict)
        changed_params = dict()
        default_config = self.default_config()
        for param, new_value in new_values.items():
            assert param in default_config
            old_value = self.config.get(param, None)
            if old_value == new_value:
                continue

            self.config[param] = new_value
            changed_params[param] = self.ChangedParam(
                old_value=old_value,
                new_value=new_value)

        if changed_params:
            self.check()
            self.sync()
            self.settings_changed(changed_params)

    def get_config(self):
        return self.config

    def get_filename(self):
        return self.config_file_name


def load_config(config_file='config.json', config_cls=ConfigLoader):
    '''
    Loads program configuration into ConfigLoader instanse.
    Configuration is assumed to be stored in JSON format.
    Configuration file is assumed to be located in the program configuration
    directory

    @param config_file Configuration file basename [string]
    @return Program configuration [ConfigLoader]
    '''

    global cfg
    config_file = get_cfg_filename(config_file)
    logger.info(
        "Loading program configuration from '%s'", config_file)
    cfg = config_cls(config_file)
    return cfg
