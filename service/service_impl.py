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

import sys
from PySide2.QtCore import QThread, Qt
from PySide2.QtCore import QCoreApplication

from common import config
from common.utils import wipe_internal
from common.logging_setup import clear_old_logs, set_max_log_size_mb
from service.stat_tracking import Tracker
from common.crash_handler import init_crash_handler
from service.service_worker import ApplicationWorker


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ApplicationService(QCoreApplication):

    def __init__(self):
        QCoreApplication.__init__(self, sys.argv)
        self._cfg = None
        self._tracker = None
        self._tracker_thread = None
        self._worker = None

    def start(self, app_start_ts, args):
        '''
        Performs application initialization and launch

        @raise SystemExit
        '''
        logger.debug("Launching service with args (%s)...", args)

        clear_old_logs(logger)
        # Load configuration file
        self._cfg = config.load_config()
        set_max_log_size_mb(logger, max(self._cfg.max_log_size, 0.02))
        if self._cfg.copies_logging:
            copies_logger = logging.getLogger('copies_logger')
            set_max_log_size_mb(copies_logger, max(self._cfg.max_log_size, 0.02))

        if 'sync_directory' in args and args['sync_directory']:
            self._cfg.set_settings(
                {'sync_directory': args['sync_directory'].decode('utf-8')})

        if 'wipe_internal' in args and args['wipe_internal']:
            try:
                wipe_internal(self._cfg.sync_directory)
            except Exception as e:
                logger.warning("Can't wipe internal info. Reason: %s", e)

            raise SystemExit(0)

        if self._cfg.tracking_address:
            self._tracker = Tracker(
                'service_stats.db', self._cfg.sync_directory,
                self._cfg.tracking_address)
            init_crash_handler(self._tracker)
            self._tracker_thread = QThread()
            self._tracker.moveToThread(self._tracker_thread)
            self._tracker_thread.started.connect(self._tracker.start.emit)
            self._tracker_thread.start(QThread.IdlePriority)
        else:
            init_crash_handler(logger=logger)

        self._worker = ApplicationWorker(self._cfg, self._tracker,
                                         app_start_ts, args)
        self._worker.exited.connect(self._on_exit, Qt.QueuedConnection)

        self._worker.start_work()
        self.exec_()
        logger.debug("Service exiting...")

    def exit(self):
        if self._worker:
            self._worker.exit_worker.emit()
        else:
            self._on_exit()

    def _on_exit(self):
        logger.debug("Worker thread quit")
        if self._tracker_thread:
            self._tracker_thread.quit()
            self._tracker_thread.wait(2)
        QCoreApplication.exit(0)

    def show_tray_notification(self, text, title=""):
        return self._worker.show_notification.emit(text, title)

    def save_to_clipboard(self, text):
        return self._worker.save_to_clipboard_signal.emit(text)

    def request_to_user(self, text,
                        buttons=("Yes", "No"), title="",
                        close_button_index=-1, close_button_off=False,
                        details=''):
        return self._worker.show_request_to_user.emit(text,
                                                      buttons,
                                                      title,
                                                      close_button_index,
                                                      close_button_off,
                                                      details)
