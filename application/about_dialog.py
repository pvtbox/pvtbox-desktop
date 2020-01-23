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
import subprocess
import os.path as op

from PySide2.QtCore import QTimer, Qt
from PySide2.QtGui import QFont
from PySide2.QtWidgets import QDialog, QLabel, QPushButton

from common.constants import GET_PRO_URI, TERMS_URI, REGULAR_URI, \
    UNKNOWN_LICENSE, FREE_LICENSE, FREE_TRIAL_LICENSE
from common.constants import UPDATER_STATUS_ACTIVE, UPDATER_STATUS_READY, \
    UPDATER_STATUS_DOWNLOADING, UPDATER_STATUS_CHECK_ERROR, \
    UPDATER_STATUS_DOWNLOAD_ERROR, UPDATER_STATUS_INSTALL_ERROR, \
    UPDATER_STATUS_INSTALLED, UPDATER_STATUS_UNKNOWN, UPDATER_STATUS_UP_TO_DATE, \
    UPDATER_STATUS_INSTALLING
from common.utils import license_display_name_from_constant, get_platform, \
    get_application_path
from about import Ui_Dialog
from application.utils import open_link
from common.translator import tr
from __version import __version__
from __update_branch import __update_branch__
from common.application import Application


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class AboutDialog(object):

    def __init__(self, parent, gui, updater, updater_worker, config, dp=1):
        self._parent = parent
        self._gui = gui
        self._updater = updater
        self._updater_worker = updater_worker
        self._config = config
        self._logged_in = False
        self._dp = dp
        self._dialog = QDialog(parent)
        self._dialog.setWindowFlags(Qt.Dialog)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)
        self._set_uris()

        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)

        app_version = tr('Version')
        app_version = '{} {}{}'.format(
            app_version, __version__,
            " ({})".format(__update_branch__) if __update_branch__ != "release"
            else '')
        self._ui.version_label.setText(app_version)
        if get_platform() != 'Darwin':
            self._minus_height = self._ui.delete_button.height() + 6
            self._ui.delete_button.hide()
        else:
            self._minus_height = 0
            self._ui.delete_button.clicked.connect(
                self._on_app_delete_button_clicked)

        self._updater_status = UPDATER_STATUS_UNKNOWN
        self._update_button = None

    def show(self, logged_in, updater_status):

        def on_finished():
            self._gui.about_dialog_open.emit(False)
            self._update_button = None
            updater_active = self._updater_status != UPDATER_STATUS_UNKNOWN
            if updater_active:
                try:
                    self._updater.updater_status_changed.disconnect(
                        self._on_update_status)
                    self._updater.downloading_update.disconnect(
                        self._on_update_downloading)
                except RuntimeError:
                    logger.warning("Can't disconnect updater signals "
                                   "in about dialog")
                try:
                    self._gui.close_about_dialog.disconnect(self._dialog.close)
                except RuntimeError:
                    logger.warning("Can't disconnect close dialog signal "
                                   "in about dialog")
                try:
                    if self._gui.check_update_button_timer.isActive():
                        self._gui.check_update_button_timer.stop()
                    self._gui.check_update_button_timer.timeout.connect(
                        self._setup_check_update_button_update)
                except RuntimeError:
                    logger.warning("Can't disconnect timer "
                                   "in about dialog")
            if get_platform() == 'Darwin':
                self._ui.delete_button.clicked.disconnect(
                    self._on_app_delete_button_clicked)

        logger.debug("Opening about dialog...")
        self._logged_in = logged_in
        self._updater_status = updater_status
        self._setup_ui()

        self._gui.about_dialog_open.emit(True)
        self._dialog.finished.connect(on_finished)
        self._gui.check_update_button_timer.timeout.connect(
            self._setup_check_update_button_update)
        self._dialog.show()

    def _setup_ui(self):
        self._get_update_button()
        self._set_fonts()

        minus_height = self._minus_height
        updater_active = self._updater_status != UPDATER_STATUS_UNKNOWN
        if not updater_active:
            minus_height += self._update_button.height() + 6
            self._update_button.hide()
        else:
            self._updater.updater_status_changed.connect(self._on_update_status)
            self._updater.downloading_update.connect(self._on_update_downloading)
            self._gui.close_about_dialog.connect(self._dialog.close)
        try:
            license_type = self._config.license_type
        except AttributeError:
            license_type = UNKNOWN_LICENSE

        if license_type == UNKNOWN_LICENSE or not self._logged_in:
            minus_height += self._ui.license_header_label.height()
            self._ui.license_header_label.hide()
            minus_height += self._ui.license_label.height()
            self._ui.license_label.hide()
            minus_height += self._ui.upgrade_label.height()
            self._ui.upgrade_label.hide()
        else:
            self._ui.license_label.setText(
                license_display_name_from_constant(
                    license_type))
            upgrade_license_types = (FREE_LICENSE, FREE_TRIAL_LICENSE)
            if license_type in upgrade_license_types:
                upgrade_text = '<a href="{}">{}</a>'.format(
                    self._get_pro_uri, tr('Upgrade license'))
                self._ui.upgrade_label.setText(upgrade_text)
                self._ui.upgrade_label.setTextFormat(Qt.RichText)
                self._ui.upgrade_label.setTextInteractionFlags(
                    Qt.TextBrowserInteraction)
                self._ui.upgrade_label.setOpenExternalLinks(True)
                old_mouseReleased = self._ui.upgrade_label\
                    .mouseReleaseEvent

                def on_upgrade_mouse_released(ev):
                    old_mouseReleased(ev)
                    self._gui.close_about_dialog.emit()

                    self._ui.upgrade_label.mouseReleaseEvent = \
                        on_upgrade_mouse_released
            else:
                minus_height += self._ui.upgrade_label.height()
                self._ui.upgrade_label.hide()

        self._ui.intro_button.clicked.connect(self._on_intro_button_clicked)
        self._ui.terms_button.clicked.connect(self._on_terms_button_clicked)
        self._ui.intro_button.enterEvent = lambda _: \
            self._ui.intro_button.setStyleSheet(
                'margin: 0; border: 0; text-align:left;'
                'color: #f9af61;')
        self._ui.intro_button.leaveEvent = lambda _: \
            self._ui.intro_button.setStyleSheet(
                'margin: 0; border: 0; text-align:left;'
                'color: #A792A9;')
        self._ui.terms_button.enterEvent = lambda _: \
            self._ui.terms_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right;'
                'color: #f9af61;')
        self._ui.terms_button.leaveEvent = lambda _: \
            self._ui.terms_button.setStyleSheet(
                'margin: 0; border: 0; text-align:right;'
                'color: #A792A9;')
        host_suffix = self._terms_uri.split('/')[-2]
        self._ui.terms_button.setToolTip(
            tr("Open Terms and Conditions on {}".format(host_suffix)))

        coeff = 1 if get_platform() != 'Darwin' else 1.1
        self._dialog.setFixedHeight(
            int((self._dialog.height() - minus_height) * coeff))

    def _set_fonts(self):
        if not self._dp or self._dp == 1:
            return

        controls = []
        controls.extend([c for c in self._dialog.findChildren(QLabel)])
        controls.extend(
            [c for c in self._dialog.findChildren(QPushButton)])

        for control in controls:
            font = control.font()
            font_size = int(control.font().pointSize() * self._dp)
            if font_size > 0:
                control_font = QFont("Noto Sans", font_size,
                                     italic=font.italic())
                control_font.setBold(font.bold())
                control.setFont(control_font)
                logger.debug("Font size %s for %s",
                             font_size, control.objectName())

    def _on_update_status(self, status):
        self._updater_status = status
        if status == UPDATER_STATUS_READY:
            self._setup_install_update_button(self._update_button)
            self._update_button.clicked.connect(self._dialog.close)
        elif status == UPDATER_STATUS_INSTALLED:
            self._gui.close_about_dialog.emit()
        elif status == UPDATER_STATUS_UP_TO_DATE:
            logger.debug("Statue updated to active")
            self._update_button.setText(tr('Pvtbox is up to date'))
            self._updater_status = UPDATER_STATUS_ACTIVE
            if not self._gui.check_update_button_timer.isActive():
                self._gui.check_update_button_timer.start()
            self._update_button.setEnabled(False)
        elif status in (UPDATER_STATUS_CHECK_ERROR,
                        UPDATER_STATUS_INSTALL_ERROR,
                        UPDATER_STATUS_DOWNLOAD_ERROR):
            if status == UPDATER_STATUS_CHECK_ERROR:
                msg = tr("Update check failed")
            elif status == UPDATER_STATUS_DOWNLOAD_ERROR:
                msg = tr("Update download failed")
            else:
                msg = tr("Update install failed")
            logger.warning("Update failed: %s", msg)
            self._gui.show_tray_notification(msg)
            self._updater_status = UPDATER_STATUS_ACTIVE
            self._setup_check_update_button(self._update_button)

    def _on_update_downloading(self, downloaded, size):
        self._setup_downloading_update_button(self._update_button,
                                              downloaded, size)

    def _on_intro_button_clicked(self):
        QTimer.singleShot(
            1000, lambda: self._unhighlight_button(
                self._ui.intro_button, "left"))
        self._gui.show_intro()

    def _on_terms_button_clicked(self):
        open_link(self._terms_uri)()
        QTimer.singleShot(
            1000, lambda: self._unhighlight_button(
                self._ui.terms_button, "right"))

    def _unhighlight_button(self, button, alignment):
        button.setStyleSheet(
            'margin: 0; border: 0; text-align:{};'
            'color: #A792A9;'. format(alignment))

    def _on_app_delete_button_clicked(self):
        self._gui.request_to_user(
            0,
            text=tr(
                "This will permanently delete Pvtbox application.\n"
                "It's recommended to ensure all local files synced "
                "with other devices"),
            title=tr("Are you sure?"),
            buttons=[
                tr("Cancel"),
                tr("Delete Application")],
            on_clicked_cb=self._on_app_delete_confirmed)

    def _on_app_delete_confirmed(self, dialog_id, button_index):
        if button_index != 1:
            return

        logger.info("Deleting application...")
        os = get_platform()
        if os == 'Darwin':
            path = get_application_path()
            subprocess.call(
                ['open',
                 op.join(path, '..', '..', '..', 'uninstall.app'),
                 ])

        Application.exit()

    def _get_update_button(self):
        self._update_button = self._ui.update_button
        if self._updater_status == UPDATER_STATUS_READY:
            self._setup_install_update_button(self._update_button)
        elif self._updater_status == UPDATER_STATUS_DOWNLOADING:
            self._setup_downloading_update_button(
                self._update_button, 0, 0)
        elif self._updater_status == UPDATER_STATUS_INSTALLING:
            self._setup_installing_update_button(self._update_button)
        else:
            self._setup_check_update_button(self._update_button)

        def update_button_clicked():
            if not self._update_button.isEnabled():
                return
            self._update_button.setEnabled(False)

            if self._updater_status == UPDATER_STATUS_READY:
                self._updater_status = UPDATER_STATUS_INSTALLING
                self._setup_installing_update_button(self._update_button)
                self._updater_worker.install_update.emit()
            elif self._updater_status == UPDATER_STATUS_DOWNLOADING:
                self._setup_downloading_update_button(
                    self._update_button, 0, 0)
            else:
                self._update_button.setText(tr('Checking for update...'))
                self._updater_worker.check_for_update.emit()

        self._update_button.clicked.connect(update_button_clicked)
        self._update_button.adjustSize()

    def _setup_check_update_button_update(self):
        if self._update_button:
            self._setup_check_update_button(self._update_button)

    def _setup_check_update_button(self, check_update):
        check_update.setEnabled(True)
        check_update.setText(tr('Check for update'))
        check_update.setStyleSheet(
            'margin: 0;border: 0; text-align:center;'
            'color: #A792A9;')

        check_update.enterEvent = lambda _: \
            check_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #f9af61;')
        check_update.leaveEvent = lambda _: \
            check_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #A792A9;')

    def _setup_downloading_update_button(self, downloading_update,
                                         downloaded, size):
        downloading_update.setEnabled(False)
        text = tr('Downloading update...')
        if downloaded or size:
            text += ' {}Mb/{}Mb'.format(downloaded, size)
        downloading_update.setText(text)
        downloading_update.setStyleSheet(
            'margin: 0; border: 0; text-align:center;'
            'color: #f9af61;')

    def _setup_install_update_button(self, install_update):
        install_update.setEnabled(True)
        install_update.setText(tr('Install update'))
        install_update.setStyleSheet(
            'margin: 0;border: 0; text-align:center;'
            'color: #66b919;')

        install_update.enterEvent = lambda _: \
            install_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #448e00;')
        install_update.leaveEvent = lambda _: \
            install_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #66b919;')

    def _setup_installing_update_button(self, install_update):
        install_update.setEnabled(False)
        install_update.setText(tr('Installing update...'))
        install_update.setStyleSheet(
            'margin: 0;border: 0; text-align:center;'
            'color: #66b919;')

        install_update.enterEvent = lambda _: \
            install_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #448e00;')
        install_update.leaveEvent = lambda _: \
            install_update.setStyleSheet(
                'margin: 0; border: 0; text-align:center;'
                'color: #66b919;')

    def _set_uris(self):
        try:
            host = self._config.host
        except AttributeError:
            host = REGULAR_URI
        self._terms_uri = TERMS_URI.format(host)
        self._get_pro_uri = GET_PRO_URI.format(host)
