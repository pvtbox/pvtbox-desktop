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
import time

from PySide2.QtCore import QTimer, Signal, QObject

from common.translator import tr

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class UpdaterWorker(QObject):
    exited = Signal()
    exit = Signal()
    settings_changed = Signal(dict)
    check_for_update = Signal()
    install_update = Signal()
    show_tray_notification = Signal(str, str)     # callback

    _update_install = Signal()
    _update_cancel = Signal()

    def __init__(self, updater, cfg):
        QObject.__init__(self, parent=None)

        self._cfg = cfg

        self._updater = updater
        self._updater.setParent(self)

        self._update_request_pending = False

    def start(self):
        self._updater_timer = QTimer(self)
        self._updater_timer.setSingleShot(True)
        self._updater_timer.timeout.connect(self._periodic_update_check)

        self._connect_signals()

        if self._cfg.autoupdate:
            self._on_start_periodic_update_check()

    def _connect_signals(self):
        self._cfg.settings_changed.connect(
            lambda settings: self.settings_changed.emit(settings))
        self.settings_changed.connect(self._on_settings_changed)
        self.exit.connect(self._on_exit)

        self._update_install.connect(self._on_update_install)
        self._update_cancel.connect(self._on_update_cancel)

        self.install_update.connect(self._updater.install_update)
        self.check_for_update.connect(self._updater.check_for_update)
        self.check_for_update.connect(
            self._on_restart_periodic_update_check)

    def _on_exit(self):
        if self._updater_timer and self._updater_timer.isActive():
            self._updater_timer.stop()
        self._updater.stop()
        try:
            self._updater.updater_status_changed.disconnect()
            self._updater.update_ready.disconnect()
            self._updater.downloading_update.disconnect()
        except RuntimeError as e:
            logger.debug("Can't disconnect signal: %s", e)
        self._updater = None

        self.disconnect(self)
        self.exited.emit()

    def _setup_updater(self):
        try:
            self._updater.update_ready.disconnect(self._on_update_ready)
        except RuntimeError as e:
            logger.debug("Can't disconnect signal update_ready: %s", e)
        self._updater.update_ready.connect(self._on_update_ready)

    def _on_update_ready(self, ready):
        if not ready:
            return
        logger.debug("on_update_ready")
        self.show_tray_notification.emit(
            tr("Application will restart automatically in one minute"),
            tr("Pvtbox is going to update"))
        QTimer.singleShot(60 * 1000, self._update_install.emit)

    def _on_update_install(self):
        self._update_request_pending = False
        if self._updater_timer.isActive():
            self._updater_timer.stop()
        if not self._updater.install_update():
            self._updater_timer.start(60 * 60 * 1000)

    def _on_update_cancel(self):
        self._update_request_pending = False
        one_day = 1 * 24 * 60 * 60
        self._cfg.set_settings(
            dict(next_update_check=time.time() + one_day))
        if self._updater_timer.isActive():
            self._updater_timer.stop()
        self._updater_timer.start(one_day * 1000)

    def change_update_request_pending(self, is_pending):
        self._update_request_pending = is_pending

    def _periodic_update_check(self):
        if self._cfg.autoupdate:
            if time.time() > self._cfg.next_update_check:
                if not self._update_request_pending:
                    self._updater.check_for_update()
                # recheck every hour if no updates found or until cancelled
                self._updater_timer.start(60 * 60 * 1000)
            else:
                self._updater_timer.start(
                    (self._cfg.next_update_check - time.time()) * 1000)

    def _apply_autoupdate(self):
        if self._cfg.autoupdate:
            self._setup_updater()
            self._cfg.set_settings(dict(next_update_check=0))
            self._updater_timer.start(100)
        else:
            try:
                self._updater.update_ready.disconnect(self._on_update_ready)
            except RuntimeError as e:
                logger.debug("Can't disconnect signal update_ready: %s", e)

    def _on_settings_changed(self, changed_params):
        if 'autoupdate' in changed_params:
            self._apply_autoupdate()

    def _on_start_periodic_update_check(self):
        self._setup_updater()
        self._updater_timer.start(
            10 * 60 * 1000 if self._cfg.next_update_check < time.time()
            else (self._cfg.next_update_check - time.time()) * 1000)

    def _on_restart_periodic_update_check(self):
        if self._updater_timer.isActive():
            self._updater_timer.stop()
        if self._cfg.autoupdate:
            self._cfg.set_settings(dict(next_update_check=0))
            self._on_start_periodic_update_check()
