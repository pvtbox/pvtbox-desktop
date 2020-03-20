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
import filelock
import logging
import os

import sys
from PySide2.QtCore import QObject

from common.tools.shell_integration_client import send_show_command
from common.utils import get_cfg_filename, register_smart
from common.logging_setup import clear_old_logs

from application import GUI
from common.utils import is_portable, is_already_started


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ApplicationImpl(QObject):

    def __init__(self):
        os.putenv("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
        QObject.__init__(self, parent=None)
        self._tracker = None
        self._tracker_thread = None
        self._gui = None
        self._worker = None
        self.lock = None

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
        logger.debug("Launching application...")
        if is_portable():
            logger.debug("Portable detected")

        # Acquire lock file to prevent multiple app instances launching
        lock_acquired = self.acquire_lock()
        if not lock_acquired or is_already_started():
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
                elif 'offline_on' in args and args['offline_on']:
                    from common.tools import send_offline_on
                    send_offline_on(args['offline_on'])

                send_show_command()
            logger.error("Application started already. Exiting")

            raise SystemExit(0)

        register_smart()

        register_smart()

        sync_folder_removed = args.get('sync_folder_removed', False)
        logging_disabled = args.get('logging_disabled', False)
        if not sync_folder_removed and not logging_disabled:
            clear_old_logs(logger)

        self._gui = GUI(parent=self, args=sys.argv[1:],
                        sync_folder_removed=sync_folder_removed,
                        loglevel=args['loglevel'],
                        logging_disabled=logging_disabled)

        self._gui.run_with_splash()

        logger.debug("ApplicationImpl start returning")

    def exit(self):
        if self._worker:
            self._worker.exit_worker.emit()
        elif self._gui:
            self._gui.exit_request.emit()

    def show_tray_notification(self, text, title=""):
        return self._worker.show_notification.emit(text, title)

    def save_to_clipboard(self, text):
        return self._worker.save_to_clipboard_signal.emit(text)

    def request_to_user(self, text,
                        buttons=("Yes", "No"), title="",
                        close_button_index=-1, close_button_off=False):
        return self._worker.show_request_to_user.emit(text,
                                                      buttons,
                                                      title,
                                                      close_button_index,
                                                      close_button_off)
