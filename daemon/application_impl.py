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
from hashlib import sha512

import sys

import filelock
import logging

from PySide2.QtCore import QCoreApplication, QTimer

from common.service_client import ServiceClient
from common.service_proxy import ServiceProxy
from common.config import load_config
from common.tools.shell_integration_client import send_show_command
from common.utils import get_cfg_filename
from common.logging_setup import clear_old_logs


# Setup logging
from common.webserver_client import Client_API

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ApplicationImpl(QCoreApplication):

    def __init__(self):
        QCoreApplication.__init__(self, sys.argv)
        self.lock = None
        self._service_client = None
        self._service = None
        self._main_cfg = None
        self._web_api = None

        self._login_data = None
        self._remote_actions = list()
        self._service_started = False

        self._login_data_timer = QTimer(self)
        self._login_data_timer.setSingleShot(True)
        self._login_data_timer.timeout.connect(self._on_login_data_timeout)

        self._autologin_timer = QTimer(self)
        self._autologin_timer.setSingleShot(True)
        self._autologin_timer.timeout.connect(self.autologin)

    def acquire_lock(self):
        '''
        Acquires lock on lock file to prevent multiple app instances launching

        @return Success status [bool]
        '''

        # Try to acquire lock file
        logger.debug("Acquiring lock on lock file...")
        self.lock = filelock.FileLock(get_cfg_filename('lock'))
        try:
            self.lock.acquire(timeout=0.05)
        except filelock.Timeout:
            logger.error("Can not acquire lock")
            return False
        logger.debug("Lock acquired")
        return True

    def start(self, app_start_ts, args):
        '''
        Performs application initialization and launch

        @raise SystemExit
        '''
        logger.debug("Launching daemon...")

        # Acquire lock file to prevent multiple app instances launching
        lock_acquired = self.acquire_lock()
        if not lock_acquired:
            if 'wipe_internal' in args and args['wipe_internal']:
                from common.tools import send_wipe_internal
                send_wipe_internal()
            else:
                if 'download_link' in args and args['download_link']:
                    from common.tools import send_download_link
                    send_download_link(args['download_link'])
                elif 'copy' in args and args['copy']:
                    from common.tools import send_copy_to_sync_dir
                    send_copy_to_sync_dir(args['copy'])

                send_show_command()
            logger.error("Daemon started already. Exiting")

            raise SystemExit(0)

        if args.get('host', None) and args.get('email', None) and args.get('password', None):
            self._main_cfg = load_config()
            self._main_cfg.set_settings(dict(
                autologin=True,
                host=args['host'],
                user_email=args['email'],
                last_user_email=args['email'],
                user_password_hash=sha512(args['password'].encode()).hexdigest(),
                download_backups=True,
                smart_sync=False
            ))
            self._main_cfg.sync()
            raise SystemExit(0)

        logging_disabled = args.get('logging_disabled', False)
        if not logging_disabled:
            clear_old_logs(logger)

        self._service_client = ServiceClient(
            start_only=False, starting_service_signal=None)
        self._service = ServiceProxy(
            parent=self, receivers=(self,), socket_client=self._service_client)
        self._main_cfg = load_config()
        self._web_api = Client_API(self._main_cfg, parent=self)
        self._web_api.loggedIn.connect(self._on_logged_in)

        self._autologin_timer.start(1)

        self.exec_()
        logger.debug("ApplicationImpl start returning")

    def autologin(self, is_silent=True):
        logger.info('Trying to autologin')
        if self._main_cfg.user_hash or \
                self._main_cfg.user_email and self._main_cfg.user_password_hash:
            if self._main_cfg.user_hash:
                user_hash = self._main_cfg.user_hash
                user_email = None
                user_password_hash = None
            else:
                user_hash = None
                user_email = self._main_cfg.user_email
                user_password_hash = self._main_cfg.user_password_hash
            status, res = self._web_api.login(
                login=user_email,
                password=user_password_hash,
                user_hash=user_hash)
            if res and 'remote_actions' in res \
                    and res['remote_actions'] and 'errcode' in res:
                self._remote_actions = res['remote_actions']
                self._login_data_timer.start(0)
                return

            if not status:
                self._autologin_timer.start(2000)

    def set_config(self, config, is_init=False):
        if is_init:
            if self._main_cfg.user_email:
                config["user_email"] = self._main_cfg.user_email
            if self._main_cfg.user_password_hash:
                config["user_password_hash"] = \
                    self._main_cfg.user_password_hash
            self._main_cfg.sync()
        self._main_cfg.refresh()

    def init(self, *args, **kwargs):
        self._service_started = True

    def _on_logged_in(self, login_data):
        self._autologin_timer.stop()
        self._login_data = login_data
        if not self._login_data_timer.isActive():
            self._login_data_timer.start(0)

    def _on_login_data_timeout(self):
        if not self._service_started:
            self._login_data_timer.start(500)
            return

        if self._remote_actions:
            for action in self._remote_actions:
                self._service.remote_action(action)
            self._remote_actions = list()
            return

        self._save_login_settings(self._login_data)
        self._service.gui_logged_in(
            self._login_data, False, True, False)

    def _save_login_settings(self, login_data):
        changed_settings = dict(
            user_email=login_data['user_email'],
            user_password_hash=login_data['password_hash'],
            user_hash=login_data['user_hash'],
            node_hash=login_data['node_hash'],
            download_backups=True,
        )

        self._main_cfg.set_settings(changed_settings)

    def exit(self):
        pass

    def show_tray_notification(self, text, title=""):
        pass

    def save_to_clipboard(self, text):
        pass

    def request_to_user(self, text,
                        buttons=("Yes", "No"), title="",
                        close_button_index=-1, close_button_off=False):
        pass
