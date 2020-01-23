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
import shutil
import os.path as op

from PySide2.QtCore import Qt, QRegExp, QTimer
from PySide2.QtGui import QRegExpValidator, QIcon
from PySide2.QtWidgets import QDialog, QFrame, \
    QFileDialog, QProgressDialog

from application.sync_dir_migration import SyncDirMigration
from common.constants import GET_PRO_URI, FREE_LICENSE, FREE_TRIAL_LICENSE
from common.utils \
    import get_available_languages, ensure_unicode, \
    is_in_system_startup, add_to_system_startup, remove_from_system_startup, \
    get_data_dir, make_dir_hidden, get_patches_dir, \
    license_display_name_from_constant, get_parent_dir, get_free_space, \
    get_max_root_len, is_portable

import settings
from .utils import msgbox
from common.translator import tr
from application.selective_sync_dialog import SelectiveSyncDialog
from common.signal import Signal
from common.file_path import FilePath
from common.errors import ExpectedError
from common.path_converter import PathConverter
from common.logging_setup import enable_file_logging, disable_file_logging, \
    set_root_directory


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Settings(object):

    class _MigrationFailed(ExpectedError):
        pass

    def __init__(self, cfg, main_cfg, start_service, exit_service,
                 parent=None, size=None, migrate=False):
        super(Settings, self).__init__()
        self._cfg = cfg
        self._main_cfg = main_cfg
        self._start_service = start_service
        self._exit_service = exit_service
        self._parent = parent
        self._size = size
        self._dialog = QDialog(parent)
        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)
        self._ui = settings.Ui_Dialog()
        self._ui.setupUi(self._dialog)
        self._max_root_len = get_max_root_len(self._cfg)
        self._migrate = migrate
        self._migration = None
        self._migration_cancelled = False

        try:
            self._ui.account_type.setText(
                license_display_name_from_constant(self._cfg.license_type))
            self._ui.account_type.adjustSize()
            self._ui.account_type.setVisible(True)
            self._ui.account_type_header.setVisible(True)
            self._ui.account_upgrade.setVisible(True)
        except KeyError:
            pass
        upgrade_license_types = (FREE_LICENSE, FREE_TRIAL_LICENSE)
        if self._cfg.license_type in upgrade_license_types:
            self._ui.account_upgrade.setText(
                '<a href="{}">{}</a>'.format(GET_PRO_URI, tr('Upgrade')))
            self._ui.account_upgrade.setTextFormat(Qt.RichText)
            self._ui.account_upgrade.setTextInteractionFlags(
                Qt.TextBrowserInteraction)
            self._ui.account_upgrade.setOpenExternalLinks(True)
            self._ui.account_upgrade.setAlignment(Qt.AlignLeft)
        else:
            self._ui.account_upgrade.setText("")

        self._ui.centralWidget.setFrameShape(QFrame.NoFrame)
        self._ui.centralWidget.setLineWidth(1)

        self._ui.language_comboBox.addItem(tr('English'))
        self._ui.language_comboBox.setEnabled(False)

        self._connect_slots()
        self._ui.general_button.click()

        # Selective sync dialog results
        self._excluded_dirs = None
        self._existing_excluded_dirs = self._cfg.get_setting('excluded_dirs')

        self.logged_out = Signal(bool)
        self.logging_disabled_changed = Signal(bool)

        # FIXMe: without line below app crashes on exit after settings opened
        self._dialog.mousePressEvent = self.on_mouse_press_event

    def on_mouse_press_event(self, ev):
        pass

    def _connect_slots(self):
        ui = self._ui

        ui.general_button.clicked.connect(
            lambda: self._set_page(ui.general_button, ui.general_page))
        ui.account_button.clicked.connect(
            lambda: self._set_page(ui.account_button, ui.account_page))
        ui.network_button.clicked.connect(
            lambda: self._set_page(ui.network_button, ui.network_page))
        ui.logout_button.clicked.connect(self._logout)

        ui.download_auto_radioButton.clicked.connect(
            lambda: ui.download_limit_edit.setEnabled(False)
            or ui.download_limit_edit.clear())
        ui.download_limit_radioButton.clicked.connect(
            lambda: ui.download_limit_edit.setEnabled(True))

        ui.upload_auto_radioButton.clicked.connect(
            lambda: ui.upload_limit_edit.setEnabled(False)
            or ui.upload_limit_edit.clear())
        ui.upload_limit_radioButton.clicked.connect(
            lambda: ui.upload_limit_edit.setEnabled(True))

        ui.buttonBox.accepted.connect(self._dialog.accept)
        ui.buttonBox.rejected.connect(self._dialog.reject)

        ui.selective_sync_button.clicked.connect(
            self._on_selective_sync_button_clicked)

        ui.location_button.clicked.connect(
            self._on_sync_folder_location_button_clicked)

    def _logout(self):
        userAnswer = msgbox(
            tr('Keep local files on device?'),
            buttons=[(tr('Clear all'), 'Wipe'),
                     (tr('Keep'), 'Keep'),],
            parent=self._dialog,
            default_index=1,
            enable_close_button=True)

        if userAnswer == '':
            return

        wipe_all = userAnswer == 'Wipe'
        if not wipe_all:
            self._cfg.set_settings({'user_password_hash': ""})

        self.logged_out.emit(wipe_all)

        self._dialog.reject()

    def _set_page(self, button, page):
        ui = self._ui

        for btn in (ui.general_button, ui.account_button, ui.network_button):
            btn.setChecked(btn == button)

        ui.pages.setCurrentWidget(page)

    def show(self, on_finished):
        def finished():
            if self._dialog.result() == QDialog.Accepted:
                self._apply_settings()
            self._dialog.finished.disconnect(finished)
            on_finished()

        self._setup_to_ui()
        if self._migrate:
            self._set_page(self._ui.account_button, self._ui.account_page)
            QTimer.singleShot(100, self._on_sync_folder_location_button_clicked)
        self._dialog.finished.connect(finished)
        self._dialog.raise_()
        self._dialog.setModal(True)
        self._dialog.show()

    def _setup_to_ui(self):
        ui = self._ui
        cfg = self._cfg

        portable = is_portable()

        if cfg.get_setting('lang', None) is None:
            self._ui.language_comboBox.setCurrentIndex(0)
        else:
            lang = cfg.lang if cfg.lang in get_available_languages() else 'en'
            assert lang in get_available_languages()
            for i in range(1, ui.language_comboBox.count()):
                if ui.language_comboBox.itemText(i) == lang:
                    ui.language_comboBox.setCurrentIndex(i)
                    break

        ui.location_edit.setText(FilePath(cfg.sync_directory)
                                 if cfg.sync_directory else '')
        ui.location_button.setEnabled(not portable)
        if portable:
            ui.location_button.setToolTip(tr("Disabled in portable version"))
        ui.email_label.setText(cfg.user_email if cfg.user_email else '')

        def set_limit(limit, auto_btn, manual_btn, edit):
            edit.setValidator(QRegExpValidator(QRegExp("\\d{1,9}")))
            if limit:
                manual_btn.setChecked(True)
                edit.setText(str(limit))
            else:
                auto_btn.setChecked(True)
                auto_btn.click()

        set_limit(limit=cfg.download_limit,
                  auto_btn=ui.download_auto_radioButton,
                  manual_btn=ui.download_limit_radioButton,
                  edit=ui.download_limit_edit)
        set_limit(limit=cfg.upload_limit,
                  auto_btn=ui.upload_auto_radioButton,
                  manual_btn=ui.upload_limit_radioButton,
                  edit=ui.upload_limit_edit)

        self._excluded_dirs = cfg.get_setting('excluded_dirs', None)
        ui.autologin_checkbox.setChecked(self._main_cfg.autologin)
        ui.autologin_checkbox.setEnabled(not portable)
        if portable:
            ui.autologin_checkbox.setToolTip(tr("Disabled in portable version"))
        ui.tracking_checkbox.setChecked(cfg.send_statistics)
        ui.autoupdate_checkbox.setChecked(self._main_cfg.autoupdate)
        ui.download_backups_checkBox.setChecked(cfg.download_backups)
        ui.disable_logging_checkBox.setChecked(self._main_cfg.logging_disabled)

        # Disable selective sync for free license
        if not cfg.license_type or cfg.license_type == FREE_LICENSE:
            ui.selective_sync_label.setText(
                tr("Selective sync is not available for your license"))
            ui.selective_sync_button.setEnabled(False)

        ui.startup_checkbox.setChecked(is_in_system_startup())
        ui.startup_checkbox.setEnabled(not portable)
        if portable:
            ui.startup_checkbox.setToolTip(tr("Disabled in portable version"))

    def _apply_settings(self):
        service_settings, main_settings = self._get_configs_from_ui()
        if main_settings['logging_disabled'] != \
                self._main_cfg.logging_disabled:
            self.logging_disabled_changed.emit(
                main_settings['logging_disabled'])
        self._cfg.set_settings(service_settings)
        self._main_cfg.set_settings(main_settings)
        if self._ui.startup_checkbox.isChecked():
            if not is_in_system_startup():
                add_to_system_startup()
        else:
            if is_in_system_startup():
                remove_from_system_startup()

    def _config_is_changed(self):
        service_settings, main_settings = self._get_configs_from_ui()
        for param, value in service_settings.items():
            if param == 'excluded_dirs' and \
                self._existing_excluded_dirs != value:
                return True
            elif self._cfg.get_setting(param) != value:
                return True
        for param, value in main_settings.items():
            if self._main_cfg.get_setting(param) != value:
                return True

        return False

    def _get_configs_from_ui(self):
        ui = self._ui
        return {
            'lang': (
                str(ui.language_comboBox.currentText())
                if ui.language_comboBox.currentIndex() > 0
                else None),
            'upload_limit': (
                0 if ui.upload_auto_radioButton.isChecked()
                or not ui.upload_limit_edit.text()
                else int(ui.upload_limit_edit.text())),
            'download_limit': (
                0 if ui.download_auto_radioButton.isChecked()
                or not ui.download_limit_edit.text()
                else int(ui.download_limit_edit.text())),
            'excluded_dirs': self._excluded_dirs,
            'send_statistics': bool(ui.tracking_checkbox.isChecked()),
            'download_backups': bool(ui.download_backups_checkBox.isChecked()),
            'autologin': bool(ui.autologin_checkbox.isChecked()),
        }, {
            'autologin': bool(ui.autologin_checkbox.isChecked()),
            'autoupdate': bool(ui.autoupdate_checkbox.isChecked()),
            'logging_disabled': bool(ui.disable_logging_checkBox.isChecked()),
            'download_backups': bool(ui.download_backups_checkBox.isChecked()),
        }

    def _on_selective_sync_button_clicked(self):
        root = str(self._ui.location_edit.text())
        pc = PathConverter(root)
        excluded_dirs_abs_paths = list(map(
            lambda p: pc.create_abspath(p), self._excluded_dirs))
        result = SelectiveSyncDialog(self._dialog).show(
            root_path=root,
            hide_dotted=True,
            excluded_dirs=excluded_dirs_abs_paths)
        if result is not None:
            self._excluded_dirs = list(map(
                lambda p: pc.create_relpath(p), result))
            logger.info(
                "Directories set to be excluded from sync: (%s)",
                ", ".join(map(lambda s: u"'%s'" % s, self._excluded_dirs)))

    def _on_sync_folder_location_button_clicked(self):
        selected_folder = QFileDialog.getExistingDirectory(
            self._dialog,
            tr('Choose Pvtbox folder location'),
            get_parent_dir(FilePath(self._cfg.sync_directory)))
        selected_folder = ensure_unicode(selected_folder)

        try:
            if not selected_folder:
                raise self._MigrationFailed("Folder is not selected")

            if len(selected_folder + "/Pvtbox") > self._max_root_len:
                if not self._migrate:
                     msgbox(tr("Destination path too long. "
                              "Please select shorter path."),
                           tr("Path too long"),
                           parent=self._dialog)
                raise self._MigrationFailed("Destination path too long")

            free_space = get_free_space(selected_folder)
            selected_folder = get_data_dir(
                dir_parent=selected_folder, create=False)
            if FilePath(selected_folder) == FilePath(self._cfg.sync_directory):
                raise self._MigrationFailed("Same path selected")

            if FilePath(selected_folder) in FilePath(self._cfg.sync_directory):
                msgbox(tr("Can't migrate into existing Pvtbox folder.\n"
                          "Please choose other location"),
                       tr("Invalid Pvtbox folder location"),
                       parent=self._dialog)
                raise self._MigrationFailed(
                    "Can't migrate into existing Pvtbox folder")

            if self._size and free_space < self._size:
                logger.debug("No disk space in %s. Free space: %s. Needed: %s.",
                             selected_folder, free_space, self._size)
                msgbox(tr("Insufficient disk space for migration to\n{}.\n"
                          "Please clean disk", selected_folder),
                       tr("No disk space"),
                       parent=self._dialog)
                raise self._MigrationFailed(
                    "Insufficient disk space for migration")

            self._migration_cancelled = False
            dialog = QProgressDialog(self._dialog)
            dialog.setWindowTitle(tr('Migrating to new Pvtbox folder'))
            dialog.setWindowIcon(QIcon(':/images/icon.svg'))
            dialog.setModal(True)
            dialog.setMinimum(0)
            dialog.setMaximum(100)
            dialog.setMinimumSize(400, 80)
            dialog.setAutoClose(False)

            def progress(value):
                logger.debug("Migration dialog progress received: %s", value)
                dialog.setValue(value)

            def migration_failed(error):
                logger.warning("Migration failed with error: %s", error)
                msgbox(error, tr('Migration to new Pvtbox folder error'),
                       parent=dialog)
                dialog.cancel()
                self._migration_cancelled = True
                done()

            def cancel():
                logger.debug("Migration dialog cancelled")
                self._migration_cancelled = True
                self._migration.cancel()

            def done():
                logger.debug("Migration done")
                try:
                    self._migration.progress.disconnect(progress)
                    self._migration.failed.disconnect(migration_failed)
                    self._migration.done.disconnect(done)
                    dialog.canceled.disconnect(cancel)
                except Exception as e:
                    logger.warning("Can't disconnect signal %s", e)
                dialog.hide()
                dialog.done(QDialog.Accepted)
                dialog.close()

            self._migration = SyncDirMigration(self._cfg, parent=self._dialog)
            self._migration.progress.connect(
                progress, Qt.QueuedConnection)
            self._migration.failed.connect(
                migration_failed, Qt.QueuedConnection)
            self._migration.done.connect(done, Qt.QueuedConnection)
            dialog.canceled.connect(cancel)
            self._exit_service()
            old_dir = self._cfg.sync_directory
            self._migration.migrate(old_dir, selected_folder)

            def on_finished():
                logger.info("Migration dialog closed")
                if not self._migration_cancelled:
                    logger.debug("Setting new location")
                    self._ui.location_edit.setText(FilePath(selected_folder))

                    disable_file_logging(logger)
                    shutil.rmtree(op.join(old_dir, '.pvtbox'), ignore_errors=True)
                    set_root_directory(FilePath(selected_folder))
                    enable_file_logging(logger)

                    make_dir_hidden(get_patches_dir(selected_folder))

                self._start_service()

            dialog.finished.connect(on_finished)
            dialog.show()

        except self._MigrationFailed as e:
            logger.warning("Sync dir migration failed. Reason: %s", e)
        finally:
            if self._migrate:
                self._dialog.accept()
