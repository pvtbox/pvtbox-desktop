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
import logging
import re
import sys
import os.path as op
from threading import RLock
from uuid import uuid4
import time
from collections import defaultdict

from PySide2.QtCore \
    import QTimer, QObject, Qt, QEvent, QThread, QThreadPool
from PySide2.QtCore import Signal as pyqtSignal
from PySide2.QtWidgets \
    import QWidget, QMainWindow, QApplication, QMenu, QFileDialog, QDialog
from PySide2.QtGui import QFont, QFontDatabase, QIcon

from common.application import Application
from .lost_folder_dialog import LostFolderDialog
from common.async_qt import wait_signal, qt_run

from common.constants import PASSWORD_REMINDER_URI, HELP_URI, REGULAR_URI
from common.constants import WEB_FM_URI, PRIVACY_URI, TERMS_URI
from common.constants import STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK, \
    STATUS_INIT, STATUS_DISCONNECTED, STATUS_INDEXING, \
    SS_STATUS_SYNCED, SS_STATUS_PAUSED, \
    SS_STATUS_SYNCING, SS_STATUS_CONNECTING, SS_STATUS_INDEXING, \
    SUBSTATUS_SYNC, SUBSTATUS_SHARE, SUBSTATUS_APPLY
from common.constants import UPDATER_STATUS_ACTIVE
from common.utils \
    import get_available_languages, format_with_units, \
    ensure_unicode, get_bases_filename, get_bases_dir, get_platform, \
    remove_file, get_cfg_filename, get_max_root_len, is_first_launch
from common.webserver_client import Client_API
from application.updater import Updater

from common.logging_setup import enable_file_logging, disable_file_logging, \
    set_max_log_size_mb, logging_setup, enable_console_logging
from __update_branch import __update_branch__

import pvtbox_main
from .system_tray import SystemTrayIcon
from .settings import Settings
from .utils import elided, qt_open_path, open_link, service_cleanup
from common.translator import tr
from .device_list_dialog import DeviceListDialog
from .service_proxy import ServiceProxy
from .service_client import ServiceClient
from .app_config import Config, load_config
from .updater_worker import UpdaterWorker
from .tutorial_dialog import TutorialDialog
from .transfers_dialog import TransfersInfo
from .about_dialog import AboutDialog
from .file_list import GuiFileList
from .notifications_dialog import Notifications


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

SPI_GETWORKAREA = 48
SM_CYSCREEN = 1
errorColor = '#FF9999'


# FIXME: replace with crossplatform version
# See http://stackoverflow.com/questions/3616825/qt-tray-icon-window
def get_taskbar_size():
    from ctypes import windll, wintypes, byref
    SystemParametersInfo = windll.user32.SystemParametersInfoA
    work_area = wintypes.RECT()
    if (SystemParametersInfo(SPI_GETWORKAREA, 0, byref(work_area), 0)):
        GetSystemMetrics = windll.user32.GetSystemMetrics
        full_height = GetSystemMetrics(17)
        height_task_bar = GetSystemMetrics(SM_CYSCREEN) - full_height
        return height_task_bar


# FIXME: replace with crossplatform version
# See http://stackoverflow.com/questions/3616825/qt-tray-icon-window
def get_border_height():
    from ctypes import windll, wintypes, byref
    SystemParametersInfo = windll.user32.SystemParametersInfoA
    GetSystemMetrics = windll.user32.GetSystemMetrics
    work_area = wintypes.RECT()
    if (SystemParametersInfo(SPI_GETWORKAREA, 0, byref(work_area), 0)):
        # ?? WHY +1?
        return GetSystemMetrics(33) + 1


class GUI(QObject):
    received_download_link = pyqtSignal(str, str)
    retranslated = pyqtSignal()
    new_language = pyqtSignal(str)
    download_link_handler = pyqtSignal(str)
    share_path_requested = pyqtSignal(str)
    about_dialog_open = pyqtSignal(bool)
    update_status = pyqtSignal()
    stop_sync = pyqtSignal()
    start_sync = pyqtSignal()
    sync_stopped = pyqtSignal()
    sync_started = pyqtSignal()
    exit_request = pyqtSignal()
    sync_status_changed = pyqtSignal(int, int, int, int, int, int)
    upload_speed_changed = pyqtSignal(float)
    download_speed_changed = pyqtSignal(float)
    upload_size_changed = pyqtSignal(float)
    download_size_changed = pyqtSignal(float)
    download_progress = pyqtSignal(str, int, int)
    downloads_status = pyqtSignal(str, int, int, list, dict)
    dialog_clicked = pyqtSignal(int, int)
    dialog_finished = pyqtSignal(int)
    nodes_info = pyqtSignal(dict)
    sync_dir_size_changed = pyqtSignal(float)
    gui_settings_changed = pyqtSignal(dict)
    settings_of_interest_changed = pyqtSignal(dict)
    starting_service = pyqtSignal()
    management_action_in_progress = pyqtSignal(str, str, str)
    final_exit = pyqtSignal()

    close_about_dialog = pyqtSignal()
    _show_auth_page_signal = pyqtSignal(bool, bool)
    _show_network_error_page_signal = pyqtSignal()
    _start_login_data_timer = pyqtSignal()
    _start_autologin_timer = pyqtSignal(int)
    _logging_disabled_changed = pyqtSignal(bool)
    _set_paused_state = pyqtSignal(bool)

    def __init__(self, parent, args, sync_folder_removed=False,
                 loglevel='DEBUG', logging_disabled=False):
        QObject.__init__(self, parent=parent)

        self._loglevel = loglevel
        self._init_timers()
        self._config = Config(self.gui_settings_changed,
                              settings_of_interest=['autologin',
                                                    'node_hash',
                                                    'user_hash',
                                                    'user_email',
                                                    'user_password_hash',
                                                    'license_type',
                                                    ],
                              settings_of_interest_signal=
                              self.settings_of_interest_changed)
        logger.debug("Loading gui config")
        self._main_cfg = load_config()
        self._is_gui_logging = self._is_logging()
        self._web_api = Client_API(self._main_cfg, parent=self)
        self._set_uris()

        self._sync_folder_removed = sync_folder_removed
        self._logging_disabled = logging_disabled
        self._logging_disabled_from_start = self._logging_disabled
        self._args = args
        self._update_args_with_logging_disabled()

        self._service_client = ServiceClient(
            self._args, start_only=False,
            start_service=not self._sync_folder_removed,
            starting_service_signal=self.starting_service)
        self._service = ServiceProxy(parent=self,
                                     receivers=(self,),
                                     socket_client=self._service_client)

        if get_platform() == 'Windows':
            QApplication.setAttribute(Qt.AA_DisableHighDpiScaling)
        self._app = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)

        self._init_main_window()
        self._gui_file_list = GuiFileList(
            self, self._service, self._config, self._dp)
        self._service.add_receiver(self._gui_file_list)
        self._notifications = Notifications(
            self, self._window, self._web_api, self._config, self._dp)
        self._service.add_receiver(self._notifications)

        self._init_tray()

        self._init_updater()

        self._transfers_info = TransfersInfo(self, self._window, self._dp)
        self._connect_slots()
        self._set_tooltips()
        self._init_status_attrs()

        self._sync_dir_size = 0
        self._set_button_icons()
        self._set_line_edit_style()

        self._logged_in = False
        self._login_data = None
        self._remote_actions = list()
        self._service_started = False
        self._self_hosted = self._init_self_hosted()

        self._sync_first_start = False

        self._exiting = False
        self._service_exited = False
        self._restarting = False

        self._show_timeout_notification = False

        # TODO: fixme
        #self.set_language(config.lang)

        self._network_error_text = ''
        self._network_error_dots = 0

        self._disk_space_low = False
        self._is_wiping_all = False
        self._nodes_actions = defaultdict(set)
        self._init_dialogs_state()

        self._download_backups = self._main_cfg.download_backups

        self._arrange_window()

    def _init_timers(self):
        self._timers = list()
        self._start_stop_processing_timer = QTimer()
        self._start_stop_processing_timer.setSingleShot(True)
        self._start_stop_processing_timer.setInterval(500)
        self._start_stop_processing_timer.timeout.connect(
            self._process_start_stop_sync)
        self._timers.append(self._start_stop_processing_timer)

        self._login_data_timer = QTimer(self)
        self._login_data_timer.setSingleShot(True)
        self._login_data_timer.timeout.connect(self._on_login_data_timeout)
        self._timers.append(self._login_data_timer)

        self._autologin_timer = QTimer(self)
        self._autologin_timer.setSingleShot(True)
        self._autologin_timer.timeout.connect(self.autologin)
        self._timers.append(self._autologin_timer)

        self._network_error_show_timer = QTimer(self)
        self._network_error_show_timer.setSingleShot(True)
        self._network_error_show_timer.setInterval(1000)
        self._network_error_show_timer.timeout.connect(
            self._on_show_network_error)
        self._timers.append(self._network_error_show_timer)

        self.check_update_button_timer = QTimer(self)
        self.check_update_button_timer.setSingleShot(True)
        self.check_update_button_timer.setInterval(5000)
        self._timers.append(self.check_update_button_timer)

        self._final_exit_timer = QTimer(self)
        self._final_exit_timer.setSingleShot(True)
        self._final_exit_timer.setInterval(2000)
        self._final_exit_timer.timeout.connect(
            self._on_final_exit)

    def _init_status_attrs(self):
        self._start_stop_processing_lock = RLock()
        self._should_process_start = False
        self._should_process_stop = False

        self._status = STATUS_INIT
        self._substatus = SUBSTATUS_SYNC
        self._local_events_count = 0
        self._remote_events_count = 0
        self._fs_events_count = 0
        self._events_erased = 0

        self._display_text = None
        self._total_downloads = None
        self._current_downloading_percent = None

    def _init_dialogs_state(self):
        self._devices_list_dialog = None
        self._settings_opened = False
        self._about_dialog_opened = False
        self._lost_folder_opened = False

    def _init_self_hosted(self):
        # Remove line below to make self-hosted button visible
        self._ui.self_hostedButton.setVisible(False)
        return self._main_cfg.host != REGULAR_URI

    def _set_button_icons(self):
        self._button_icons = {
            STATUS_PAUSE: QIcon(':/images/sync_button/run.png'),
            STATUS_IN_WORK: QIcon(':/images/sync_button/pause.png'),
            STATUS_INDEXING: QIcon(':/images/sync_button/pause.png'),
            STATUS_WAIT: QIcon(':/images/sync_button/updated.png'),
            STATUS_DISCONNECTED:
                QIcon(':/images/sync_button/disconnected.png'),
            STATUS_INIT:
                QIcon(':/images/sync_button/disconnected.png'),
        }

    def _set_line_edit_style(self):
        self._line_edit_style = "border: 2px solid; border-radius: 4px; " \
                                "border-color: #777777; background-color: {};"
        self._line_edit_normal_color = "#eeeeee"
        self._line_edit_error_color = errorColor

    def _set_fonts(self):
        def getFont(name):
            fontId = QFontDatabase.addApplicationFont(':/fonts/'+name)
            assert fontId != -1
            family = QFontDatabase.applicationFontFamilies(fontId)[0]
            return QFont(family)

        self._fonts = {
            'OpenSans': getFont('OpenSans-Regular.ttf'),
            'OpenSansBold': getFont('OpenSans-Bold.ttf'),
            'OpenSansSemibold': getFont('OpenSans-Semibold.ttf'),
            'Gotcha': getFont('GothaProBla.otf'),
            'Gargi': getFont('Gargi.ttf'),
        }

        def setControlsFont(font, controls, set_size=False):
            for control in controls:
                if not set_size:
                    control.setFont(self._fonts[font])
                    continue

                font_size = control.font().pointSize() * self._dp
                if font_size > 0:
                    control.setFont(QFont(self._fonts[font].family(),
                                          font_size))
                else:
                    control.setFont(self._fonts[font])

        ui = self._ui
        platform = get_platform()

        setControlsFont('OpenSans', [
            ui.login_radioButton,
            ui.register_radioButton,
            ui.confirm_new_password_lineEdit,
            ui.accept_licence_label,
            ui.email_lineEdit,
            ui.password_lineEdit,
            ui.host_lineEdit,
            ui.self_hostedButton,
            ui.password_reminder_button,
            ui.download_backups_label, ],
            set_size=platform == 'Windows')

        setControlsFont('Gargi', [
            ui.auth_button,
        ])

        if platform == 'Darwin':
            ui.download_backups_label.setText(tr(
                """<html><head/><body><p><b style="font-size: 12pt">
                Let the system make backups of your files.</b>
                <span style="font-size: 12pt">
                This allows to recover previous versions of files 
                but requires additional disc space.
                </span></p></body></html>"""))

    def _init_updater(self):
        self._updater = Updater(__update_branch__)
        self._updater_status = UPDATER_STATUS_ACTIVE
        self._updater_worker = UpdaterWorker(self._updater, self._main_cfg)
        self._updater_thread = QThread()
        self._updater_worker.moveToThread(self._updater_thread)
        self._updater.moveToThread(self._updater_thread)
        self._updater_thread.started.connect(self._updater_worker.start)
        self._updater_thread.start()

    def init(self, is_logged_in, config, config_filename):
        self._restarting = False
        # clear files list if service restarted
        self._gui_file_list.clear()

        self.set_config(config, is_init=True)
        self._config.set_config_filename(config_filename)

        self._shorten_root()
        sync_directory = self._config.get_setting("sync_directory")
        if sync_directory and \
                len(sync_directory) > get_max_root_len(self._config):
                return

        self._logged_in = self._logged_in or is_logged_in
        self._service_started = True
        if self._logged_in and self._login_data:
            if self._login_data_timer.isActive():
                self._login_data_timer.stop()
            self._post_login_ops()

        set_max_log_size_mb(logger, max(self._config.max_log_size, 0.02))

    def set_config(self, config, is_init=False):
        if is_init:
            if self._main_cfg.user_email:
                config["user_email"] = self._main_cfg.user_email
            if self._main_cfg.user_password_hash:
                config["user_password_hash"] = \
                    self._main_cfg.user_password_hash
        config['host'] = self._main_cfg.host
        self._config.set_config(config)

    def _on_reject_shorten_path(self, dialog_id, button_index):
        if button_index == 1:
            self._exiting = True
            self.exit_request.emit()
            return
        self.on_show_settings_click(migrate=True)

    def _shorten_root(self):
        self._exiting = False
        while True:
            sync_directory = self._config.get_setting("sync_directory")
            if self._exiting or not sync_directory or \
                    len(sync_directory) <= get_max_root_len(self._config):
                break
            self.request_to_user(
                0, text=tr("Path to sync folder location is too long.\n"
                           "Please select shorter path or quit."),
                title=tr("Change location"),
                buttons=[tr("Change path"), tr("Quit")],
                on_clicked_cb=self._on_reject_shorten_path)

    def _arrange_window(self):
        self._window.adjustSize()
        screen = self._app.screens()[0]
        widget = self._window.geometry()
        x = screen.size().width() - widget.width()
        if x - 60 > 0:
            x -= 60
        y = screen.size().height() - widget.height()
        if y - 120 > 0:
            y -= 120
        if "win" in sys.platform and sys.platform != "darwin":
            x -= get_border_height()
            y -= get_border_height() + get_taskbar_size()
        self._window.move(x, y)
        if not self._is_gui_logging:
            self._is_gui_logging = True
            self.show_loading_screen()
            if not self._sync_folder_removed:
                self.autologin()
        elif not self._main_cfg.user_email:
            self.show_auth_page(True)
        else:
            self.show_auth_page(False)
        QTimer.singleShot(100, self.show)

        if is_first_launch():
            QTimer.singleShot(200, self.show_intro)
        elif self._sync_folder_removed:
            QTimer.singleShot(200, self._show_lost_folder_dialog)

    def show_intro(self):
        dialog = TutorialDialog(self._window, self._dp)
        dialog.show()

    def _init_main_window(self):
        self._window = QMainWindow()
        self._window.setWindowFlags(Qt.FramelessWindowHint)
        self._window.setAttribute(Qt.WA_TranslucentBackground)
        self._window.setAttribute(Qt.WA_MacFrameworkScaled)
        self._ui = pvtbox_main.Ui_MainWindow()
        self._ui.setupUi(self._window)
        self._window.setCentralWidget(self._ui.centralwidget)
        self._window.closeEvent = self._on_main_window_close_event

        # OS - specific font size coefficient
        self._dp = self._get_dp()

        self._window.setFont(QFont('Nano', 10 * self._dp))
        self._ui.accept_licence_label.setOpenExternalLinks(True)

        self._ui.download_speed_label.setText('\u2193\u0020')
        self._ui.upload_speed_label.setText('\u2191\u0020')
        font = self._ui.download_speed_value.font()
        font.setPointSize(9 * self._dp)
        self._ui.download_speed_label.setFont(font)
        self._ui.upload_speed_label.setFont(font)

        self._ui.download_speed_value.setFont(font)
        self._ui.download_size_value.setFont(font)
        self._ui.upload_speed_value.setFont(font)
        self._ui.upload_size_value.setFont(font)

        font = self._ui.network_error.font()
        font.setPointSize(9 * self._dp)
        self._ui.network_error.setFont(font)
        self._init_network_error_label()

        font = self._ui.status_text_label.font()
        font.setPointSize(10 * self._dp)
        self._ui.status_text_label.setFont(font)

        font = QFont()
        font.setPointSize(14 * self._dp)
        self._ui.welcome_label.setFont(font)
        self._ui.loading_label.setFont(font)

        # for frameless window moving
        self._x_coord = 0
        self._y_coord = 0
        self._window.mousePressEvent = self.on_mouse_press_event
        self._window.mouseMoveEvent = self.on_mouse_move_event

        self._app.installEventFilter(self)

        self._set_settings_button_menu()
        self._set_fonts()
        self._set_welcome_label()
        self._set_accept_license_label()

    def _get_dp(self):
        dpi_100_percent = 96.0
        platform = get_platform()
        dp = 1
        if platform == 'Darwin':
            dp = 1.4
        elif platform == 'Windows':
            qp = QWidget()
            den = qp.logicalDpiX() if qp.logicalDpiX() else dpi_100_percent
            dp = dpi_100_percent / den
            if dp < 1:
                dp += (1 - dp) / 2.0
            logger.debug('DPI: %s, dp: %s', qp.logicalDpiX(), dp)
            logger.debug('Pixel ratio %s', self._window.devicePixelRatio())
        return dp

    def _set_settings_button_menu(self):
        menu = QMenu(self._ui.settings_button)
        menu.setStyleSheet("background-color: #EFEFF4; ")

        def add_menu_item(caption, handler):
            action = menu.addAction(caption)
            action.triggered.connect(handler)

        add_menu_item(tr('My devices'), self.on_show_device_list_click)
        add_menu_item(tr('Settings'), self.on_show_settings_click)
        add_menu_item(tr('Help'), open_link(self._help_uri))
        add_menu_item(tr('About'), self._on_about_click)
        add_menu_item(tr('Exit'), self._on_exit_request)

        self._ui.settings_button.setMenu(menu)
        menu.show = menu.exec_

    def _init_network_error_label(self):
        def on_network_error_mouse_released(ev):
            logger.info("Network err label clicked. _network_err text %s",
                        self._network_error_text)
            self._show_transfers()

        def on_network_error_enter(ev):
            self._ui.network_error.setCursor(Qt.PointingHandCursor)

        def on_network_error_leave(ev):
            self._ui.network_error.setCursor(Qt.ArrowCursor)

        logger.info("Init network err label")
        self._ui.network_error.mouseReleaseEvent = \
            on_network_error_mouse_released
        self._ui.network_error.setMouseTracking(True)
        self._ui.network_error.enterEvent = on_network_error_enter
        self._ui.network_error.leaveEvent = on_network_error_leave

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.ApplicationDeactivate:
            self.hide()

        elif ev.type() == QEvent.ApplicationActivate:
            self.show()

        return False

    def on_mouse_press_event(self, ev):
        self._x_coord = ev.x()
        self._y_coord = ev.y()

    def on_mouse_move_event(self, ev):
        self._window.move(
            ev.globalX() - self._x_coord, ev.globalY()-self._y_coord)

    def show(self):
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def hide(self, force=False):
        def hide():
            if not self._window.isActiveWindow() and \
                    not self._any_dialog_opened() or \
                    force:
                self._window.hide()
                if get_platform() == 'Linux':
                    self._window.setWindowFlags(self._window.windowFlags() & ~Qt.Tool)
        if force:
            hide()
        else:
            QTimer.singleShot(300, hide)

    def _on_main_window_close_event(self, event):
        event.ignore()
        self.hide(force=True)

    def _connect_slots(self):
        ui = self._ui
        ui.login_radioButton.clicked.connect(
            lambda: self.show_auth_page(False, clean_error=True))
        ui.register_radioButton.clicked.connect(
            lambda: self.show_auth_page(True, clean_error=True))
        ui.auth_button.clicked.connect(self.on_auth_button_click)
        ui.email_lineEdit.textEdited.connect(
            lambda _: self._ui.password_lineEdit.setReadOnly(False))
        ui.email_lineEdit.returnPressed.connect(ui.auth_button.click)
        ui.password_lineEdit.returnPressed.connect(ui.auth_button.click)
        ui.confirm_new_password_lineEdit.returnPressed.connect(
            ui.auth_button.click)
        ui.host_lineEdit.returnPressed.connect(ui.auth_button.click)
        ui.password_reminder_button.clicked.connect(
            open_link(self._password_reminder_uri))
        ui.start_stop_button.clicked.connect(self.on_start_stop_click)
        self._ui.open_folder_button.clicked.connect(self._open_path)
        ui.www_button.clicked.connect(
            self.open_webfm)
        ui.self_hostedButton.clicked.connect(self._on_self_hosted_clicked)

        if get_platform() != "Windows":
            self._app.aboutToQuit.connect(Application.exit)
        self._app.aboutToQuit.connect(
            lambda: self._app.removeEventFilter(self))
        self.exit_request.connect(self._on_exit_request)

        ui.devices_button.clicked.connect(self.on_show_device_list_click)

        self._connect_speed_size_slots()

        self.sync_status_changed.connect(self.on_sync_status_changed)
        self.download_progress.connect(self.on_download_progress)
        self.downloads_status.connect(
            lambda dt, p, t, di, upi: self.on_download_progress(dt, p, t))
        self.downloads_status.connect(
            lambda dt, p, t, di, upi:
            self._transfers_info.update_info(di, upi))
        self.retranslated.connect(self._window.adjustSize)
        self.new_language.connect(self.set_language)
        self._show_auth_page_signal.connect(self.show_auth_page)
        self._show_network_error_page_signal.connect(
            self.show_network_error_page)

        self.download_link_handler.connect(
            self.on_download_link_handler, Qt.QueuedConnection)
        self.settings_of_interest_changed.connect(
            self._on_settings_of_interest_changed)

        self._connect_service_slots()

        self.about_dialog_open.connect(
            self._updater_worker.change_update_request_pending)
        self._updater_worker.show_tray_notification.connect(
            self.show_tray_notification)

        self.sync_dir_size_changed.connect(self._on_sync_dir_size_changed)
        self._updater.updater_status_changed.connect(
            self._on_updater_status_changed)

        self._web_api.loggedIn.connect(self._on_gui_logged_in)
        self._web_api.login_failed.connect(self.login_failed)
        self._web_api.registered.connect(self.registered)
        self._web_api.registration_failed.connect(self.registration_failed)
        self._web_api.timeout_error.connect(self.on_web_request_timeout)
        self._start_login_data_timer.connect(
            lambda: self._login_data_timer.start(0))
        self._start_autologin_timer.connect(
            lambda interval: self._autologin_timer.start(interval))

        self.nodes_info.connect(self._save_nodes_info)
        self.sync_started.connect(self._on_sync_started)

        self._logging_disabled_changed.connect(
            self._on_logging_disabled_changed, Qt.QueuedConnection)
        self.final_exit.connect(self._on_final_exit, Qt.QueuedConnection)

        self._connect_transfers_slots()
        self._ui.bell.clicked.connect(self._notifications.show_dialog)

    def _connect_speed_size_slots(self):
        ui = self._ui
        self.upload_speed_changed.connect(
            lambda x: ui.upload_speed_value.setText(
                u"{}/s".format(format_with_units(x))
            ))
        self.upload_speed_changed.connect(
            self._transfers_info.update_upload_speed)
        self.upload_size_changed.connect(
            lambda x: ui.upload_size_value.setText(
                format_with_units(x)
            ))
        self.upload_size_changed.connect(
            self._transfers_info.update_upload_size)
        self.download_speed_changed.connect(
            lambda x: ui.download_speed_value.setText(
                u"{}/s".format(format_with_units(x))
            ))
        self.download_speed_changed.connect(
            self._transfers_info.update_download_speed)
        self.download_size_changed.connect(
            lambda x: ui.download_size_value.setText(
                format_with_units(x)
            ))
        self.download_size_changed.connect(
            self._transfers_info.update_download_size)

    def _connect_service_slots(self):
        self.received_download_link.connect(
            self._service.received_download_link)
        self.share_path_requested.connect(self._service.share_path_requested)
        self.update_status.connect(self._service.update_status)
        self.stop_sync.connect(self._service.stop_sync)
        self.start_sync.connect(self._service.start_sync)
        self.dialog_clicked.connect(self._service.dialog_clicked)
        self.dialog_finished.connect(self._service.dialog_finished)
        self.gui_settings_changed.connect(self._service.gui_settings_changed)

    def _connect_transfers_slots(self):
        self.starting_service.connect(
            self._transfers_info.clear, Qt.QueuedConnection)
        self.starting_service.connect(
            self._notifications.clear, Qt.QueuedConnection)
        self._set_paused_state.connect(
            self._transfers_info.set_paused_state, Qt.QueuedConnection)
        self._transfers_info.pause_resume_clicked.connect(
            self.on_start_stop_click, Qt.QueuedConnection)
        self._transfers_info.revert_downloads.connect(
            self._service.revert_downloads)
        self._transfers_info.add_to_sync_folder.connect(
            self._service.add_to_sync_folder)
        self._transfers_info.download_link_handler.connect(
            self.on_download_link_handler, Qt.QueuedConnection)

    def _set_tooltips(self):
        ui = self._ui
        ui.devices_button.setToolTip(tr("Show devices"))
        ui.www_button.setToolTip(tr("Open web panel"))
        ui.open_folder_button.setToolTip(tr("Open sync folder"))
        ui.start_stop_button.setToolTip(tr("Start / stop sync"))
        ui.settings_button.setToolTip(tr("Open settings"))

    def _init_tray(self):
        logged_in_actions = set()
        status_dependant_actions = list()
        start_stop_texts = {
            STATUS_INIT: [tr("Pvtbox...")],
            STATUS_DISCONNECTED: [tr("Connecting...")],
            STATUS_WAIT: [tr("Stop sync")],
            STATUS_PAUSE: [tr("Start sync")],
            STATUS_IN_WORK: [tr("Stop sync")],
            STATUS_INDEXING: [tr("Stop sync")]
        }
        disabled_start_stop_statuses = {STATUS_INIT, STATUS_DISCONNECTED}

        menu = QMenu()

        def add_menu_item(caption, handler, only_if_logged=False,
                          status_dependant=False):
            action = menu.addAction(caption)
            action.triggered.connect(handler)
            if only_if_logged:
                logged_in_actions.add(action)
            if status_dependant:
                status_dependant_actions.append(action)

        add_menu_item(
            start_stop_texts[STATUS_INIT][0],
            self.on_start_stop_click, True, True)
        add_menu_item(tr('Settings'), self.on_show_settings_click, True)
        add_menu_item(tr('Help'), open_link(self._help_uri))
        add_menu_item(tr('About'), self._on_about_click)
        add_menu_item(tr('Exit'), self._on_exit_request)

        menu.show = menu.exec_

        self.widget = QWidget()
        self._tray = SystemTrayIcon(parent=self.widget, menu=menu,
                                    is_logging=self._is_gui_logging)
        self._tray.clicked.connect(self._on_tray_clicked)
        self._tray.double_clicked.connect(self._on_tray_double_clicked)

        def on_show_menu():
            for action in logged_in_actions:
                action.setEnabled(self._logged_in)
            for i, action in enumerate(status_dependant_actions):
                action.setText(start_stop_texts[self._status][i])
                action.setEnabled(
                    self._status not in disabled_start_stop_statuses)

        self._tray.menu.aboutToShow.connect(on_show_menu)

        self._tray.show()

    def _set_welcome_label(self):
        label_text = tr("""<html><head/><body><p align="center"><span
            style=" color:#515151;">Welcome.</span></p><p
            align="center"><span style=" color:#515151;"><br/></span></p><p
            align="center"><span style=" 
            color:#515151;">To add files into your </span></p><p
            align="center"><span style=" 
            color:#515151;">Pvtbox secured folder on this PC</span></p><p
            align="center"><span style=" 
            color:#515151;">just move them via {}.</span></p></body></html>""")
        platform = get_platform()
        file_manager = "Explorer" if platform == "Windows" \
            else "Finder" if platform == "Darwin" \
            else "File manager"
        self._ui.welcome_label.setText(label_text.format(file_manager))

    def _set_accept_license_label(self):
        label_text = tr("""<html><head/><body><p>I have read and accept 
            <a href=\"{}\"><span style=\" text-decoration: underline; 
            color:#f78d1e;\">Rules</span></a> and <a href=\"{}\">
            <span style=\" text-decoration: underline; color:#f78d1e;\">
            Privacy Policy</span></a></p></body></html>""")
        self._ui.accept_licence_label.setText(
            label_text.format(self._terms_uri, self._privacy_uri))

    def _update_status(self):
        new_status = STATUS_INIT if not self._logged_in else self._status
        if new_status != STATUS_IN_WORK:
            self._display_text = None
            if self._substatus != SUBSTATUS_APPLY:
                self._substatus = SUBSTATUS_SYNC
            self._local_events_count = 0
            self._remote_events_count = 0
            self._events_erased = 0
            if new_status != STATUS_INDEXING:
                self._fs_events_count = 0

        if new_status is not None:
            self._tray.update_status_icon(new_status, self._substatus)
            self._set_start_stop_icon(new_status)
            self._set_status_text(new_status, self._substatus)
            if new_status != STATUS_IN_WORK:
                self._on_network_error_reset()

    def _on_tray_clicked(self):
        if not self._window.isVisible():
            self.show()

    def _on_tray_double_clicked(self):
        self._open_path()

    def _on_updater_status_changed(self, status):
        self._updater_status = status

    def _on_about_click(self):
        if self._dialogs_opened():
            return

        self._about_dialog_opened = True
        dialog = AboutDialog(
            self._window, self, self._updater, self._updater_worker,
            self._config, self._dp)
        dialog.show(self._logged_in, self._updater_status)
        self._about_dialog_opened = False
        return

    def _on_exit_request(self):
        if self._is_wiping_all:
            return

        self._exiting = True
        self.show_loading_screen(tr("Exiting..."), exiting=True)
        try:
            self._app.aboutToQuit.disconnect(Application.exit)
        except RuntimeError:
            pass
        self.exit()

    def _on_self_hosted_clicked(self):
        ui = self._ui
        self._self_hosted = not self._self_hosted
        ui.host_lineEdit.setVisible(self._self_hosted)
        text = tr("I'm regular user") if self._self_hosted \
            else tr("I'm self-hosted user")
        ui.self_hostedButton.setText(text)
        if self._self_hosted:
            ui.auth_views.setCurrentWidget(ui.login_page)
            ui.login_radioButton.setChecked(True)
            ui.register_radioButton.setChecked(False)
        else:
            self._main_cfg.set_settings({'host': REGULAR_URI})
            self._config.set_settings({'host': REGULAR_URI})
            self._set_uris()
        ui.register_radioButton.setEnabled(not self._self_hosted)

    def run_with_splash(self):
        self._app.exec_()
        logger.verbose("Application main loop exited")

    def show_loading_screen(self, text=tr('Loading...'), exiting=False):
        self._ui.loading_label.setText(text)
        self._ui.loading_progress_bar.setVisible(not exiting)
        self._ui.views.setCurrentWidget(self._ui.loading_page)
        self._ui.views.repaint()

    def show_main_page(self):
        self._ui.email_lineEdit.clear()
        self._ui.password_lineEdit.clear()
        self._ui.confirm_new_password_lineEdit.clear()
        self._ui.host_lineEdit.clear()
        self._ui.accept_licence_checkBox.setChecked(False)
        if not self._gui_file_list.has_files() and \
                not self._gui_file_list.is_changing():
            self.init_file_list([])
        self._ui.views.setCurrentWidget(self._ui.main_page)

    def show_auth_page(self, show_registration, clean_error=True):
        ui = self._ui
        self._is_gui_logging = True
        ui.views.setCurrentWidget(ui.auth_page)
        ui.auth_page.setEnabled(True)
        ui.host_lineEdit.setVisible(self._self_hosted)
        if clean_error:
            ui.auth_error_label.clear()
            self._ui.email_lineEdit.setStyleSheet(
                self._line_edit_style.format(self._line_edit_normal_color))
            self._ui.password_lineEdit.setStyleSheet(
                self._line_edit_style.format(self._line_edit_normal_color))
            self._ui.confirm_new_password_lineEdit.setStyleSheet(
                self._line_edit_style.format(self._line_edit_normal_color))
            self._ui.host_lineEdit.setStyleSheet(
                self._line_edit_style.format(self._line_edit_normal_color))

        show_registration = show_registration and not self._self_hosted
        ui.register_radioButton.setEnabled(not self._self_hosted)
        if show_registration:
            ui.register_radioButton.setChecked(True)
            ui.auth_button.setText(tr("Sign up for Free"))
            ui.auth_views.setCurrentWidget(ui.register_page)
        else:
            ui.login_radioButton.setChecked(True)
            ui.auth_button.setText(tr("Sign in"))
            if not self._ui.email_lineEdit.text():
                self._ui.email_lineEdit.setText(self._main_cfg.user_email)
                if self._self_hosted:
                    ui.host_lineEdit.setText(self._main_cfg.host)
                if self._ui.email_lineEdit.text():
                    self._ui.password_lineEdit.setFocus()
            ui.auth_views.setCurrentWidget(ui.login_page)
        ui.auth_button.setAutoDefault(True)
        ui.download_backups_checkBox.setChecked(self._download_backups)

        self._clear_main_window()

    def _clear_main_window(self):
        self._gui_file_list.clear()
        self._display_text = None
        self._ui.status_text_label.clear()
        self._ui.upload_speed_value.setText("0.0 B/s")
        self._ui.upload_size_value.setText("0.0 B")
        self._ui.download_speed_value.setText("0.0 B/s")
        self._ui.download_size_value.setText("0.0 B")
        self._set_regular_transfers_text()

    def show_network_error_page(self):
        self.show_loading_screen(text=tr("Connecting..."))
        self._tray.update_status_icon(STATUS_DISCONNECTED, self._substatus)

    def login_failed(self, errcode, info):
        if errcode in ('USER_NODE_MISMATCH', 'NODEHASH_EXIST', 'NODE_EXIST'):
            self._main_cfg.set_settings({
                'node_hash': sha512(uuid4().bytes).hexdigest()})
            self._login()
            return
        self._logged_in = False
        self._main_cfg.set_settings({
            "user_password_hash": ""})
        self.show_auth_page(False)
        self._ui.auth_error_label.setText(tr("Login failed.\n{}", info))
        self._update_status()

    def registration_failed(self, errcode, info):
        if errcode in ('USER_NODE_MISMATCH', 'NODEHASH_EXIST', 'NODE_EXIST'):
            self._main_cfg.set_settings({
                'node_hash': sha512(uuid4().bytes).hexdigest()})
            self._register()
            return
        self._main_cfg.set_settings({
            "user_password_hash": ""})
        self.show_auth_page(True)
        self._ui.auth_error_label.setText(tr(
            "Registration failed.\n{}", info))

    def _kill_timers(self):
        logger.verbose("Starting killing timers (%s)", len(self._timers))
        for timer in self._timers:
            if timer and timer.isActive():
                logger.verbose("Killing timer %s", timer)
                timer.stop()
            else:
                logger.verbose("Timer is inactive")
        self._timers.clear()

    def exit(self):
        if self._is_wiping_all:
            return

        logger.debug("Exiting gui")
        self._final_exit_timer.start()
        self._service.exit_service()
        self._kill_timers()
        with wait_signal(self._updater_worker.exited):
            self._updater_worker.exit.emit()
        self._updater_thread.quit()
        self._updater_thread.wait()
        pool = QThreadPool.globalInstance()
        pool.waitForDone()

    def _on_final_exit(self):
        if self._final_exit_timer.isActive():
            self._final_exit_timer.stop()
        if not self._service_exited:
            service_cleanup()
        self.exit_service()
        self._tray.hide()
        self._app.closeAllWindows()
        time.sleep(0.5)

        self._app.exit()
        logger.debug("QApplication exited")

    def service_exited(self):
        self._service_exited = True
        if not self._restarting:
            self._service = None
            self.final_exit.emit()

    def exit_service(self):
        if self._service:
            self._service.exit_service()
        self._service_client.stop()
        self._service_started = False

    def start_service(self, args=()):
        if not args:
            args = self._args
        self._service_client.start(args)

    def _is_logging(self):
        return not self._main_cfg.get_setting('user_email') or \
               not self._main_cfg.get_setting('user_password_hash')

    def _validate_email(self, email_control, error):
        email_control.setStyleSheet(
            self._line_edit_style.format(self._line_edit_normal_color))
        regex = '^.+@.{2,}$'

        email_control.setText(email_control.text().strip())
        if not re.match(regex, email_control.text()):
            error.setText(tr("Please enter a valid e-mail"))
            email_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            email_control.setFocus()
            return False

        return True

    def _validate_existing_password(self, password_control, error):
        password_control.setStyleSheet(
            self._line_edit_style.format(self._line_edit_normal_color))
        if not password_control.text():
            error.setText(tr("Enter the password"))
            password_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            password_control.setFocus()
            return False

        return True

    def _validate_host(self, host_control, error):
        host_control.setStyleSheet(
            self._line_edit_style.format(self._line_edit_normal_color))
        if not self._self_hosted:
            return True

        regex = r'https?://.+\..+'
        host_control.setText(host_control.text().strip())
        if not re.match(regex, host_control.text()):
            error.setText(tr("Please enter a valid host address"))
            host_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            host_control.setFocus()
            return False

        return True

    def _validate_before_login(self):
        self._ui.auth_error_label.clear()
        return (
            self._validate_email(
                email_control=self._ui.email_lineEdit,
                error=self._ui.auth_error_label)
            and
            self._validate_existing_password(
                password_control=self._ui.password_lineEdit,
                error=self._ui.auth_error_label)
            and
            self._validate_host(
                host_control=self._ui.host_lineEdit,
                error=self._ui.auth_error_label)
        )

    def _validate_new_password(self,
                               password_control,
                               confirm_control,
                               error):
        password_control.setStyleSheet(
            self._line_edit_style.format(self._line_edit_normal_color))
        confirm_control.setStyleSheet(
            self._line_edit_style.format(self._line_edit_normal_color))
        password = password_control.text()
        max_password_len = 6
        if len(password) < max_password_len:
            error.setText(tr('Password length must be at least {} characters'
                             .format(max_password_len)))
            password_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            password_control.setFocus()
            return False

        patt = r"""^[a-zA-Z0-9!@#$%^&*()_+=[\]{};:"'\\\|\?\/\.\,<>\-`~]+\Z"""
        if not re.match(patt, password):
            error.setText(tr("For the password you can use only letters "
                             "of the Latin alphabet, digits and symbols "
                             "!@#$%^&*()_+-=[]{};:\"'\\|?/.,<>`~"))
            password_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            password_control.setFocus()
            return False

        if password != confirm_control.text():
            error.setText(tr('Passwords did not match'))
            confirm_control.setStyleSheet(
                self._line_edit_style.format(self._line_edit_error_color))
            confirm_control.setFocus()
            return False

        return True

    def _check_accept_rules(self, checkbox, error):
        checkbox.setStyleSheet("")
        if not checkbox.isChecked():
            error.setText(
                tr('You should read and agree with licence agreement'))
            checkbox.setStyleSheet("background-color: " + errorColor)
            checkbox.setFocus()
            return False

        return True

    def _validate_before_register(self):
        self._ui.auth_error_label.clear()
        return (
            self._validate_email(
                email_control=self._ui.email_lineEdit,
                error=self._ui.auth_error_label)
            and
            self._validate_new_password(
                password_control=self._ui.password_lineEdit,
                confirm_control=self._ui.confirm_new_password_lineEdit,
                error=self._ui.auth_error_label)
            and
            self._check_accept_rules(
                checkbox=self._ui.accept_licence_checkBox,
                error=self._ui.auth_error_label))

    def on_auth_button_click(self):
        self.show_waiting_button()
        self._download_backups = self._ui.download_backups_checkBox.isChecked()
        if self._ui.register_radioButton.isChecked():
            self.on_register_click()
        else:
            self.on_login_click()

    def on_register_click(self):
        if not self._validate_before_register():
            self.show_auth_page(True, clean_error=False)
            return

        email = self._ui.email_lineEdit.text()
        password_hash = sha512(
            self._ui.password_lineEdit.text().encode('utf-8')).hexdigest()

        self._main_cfg.set_settings({
            "user_email": email,
            "user_password_hash": password_hash,
            "devices": dict()})
        self._nodes_actions.clear()

        self._register()

    @qt_run
    def _register(self):
        self._web_api.signup(
            fullname='',
            email=self._main_cfg.user_email,
            password=self._main_cfg.user_password_hash
        )

    def registered(self):
        self._login()

    def on_login_click(self):
        if not self._validate_before_login():
            self.show_auth_page(False, clean_error=False)
            return

        if self._ui.password_lineEdit.isReadOnly():
            password_hash = self._ui.password_lineEdit.text()
        else:
            password_hash = sha512(
                self._ui.password_lineEdit.text().encode('utf-8')).hexdigest()

        settings = {
            "user_email": self._ui.email_lineEdit.text(),
            "user_password_hash": password_hash}
        if self._self_hosted:
            settings['host'] = self._ui.host_lineEdit.text()
        else:
            settings['host'] = REGULAR_URI
        self._config.set_settings({'host': settings['host']})

        if settings["user_email"] != self._main_cfg.user_email:
            settings["devices"] = dict()
            self._nodes_actions.clear()
        self._main_cfg.set_settings(settings)

        self._login()

    @qt_run
    def _login(self):
        self._web_api.login(
            self._main_cfg.user_email,
            self._main_cfg.user_password_hash
        )

    def _on_gui_logged_in(self, login_data):
        self._login_data = login_data
        if not self._login_data_timer.isActive():
            self._login_data_timer.start(0)

    def _on_login_data_timeout(self):
        if not self._service_started or self._is_wiping_all:
            self._login_data_timer.start(500)
            return

        if self._remote_actions:
            for action in self._remote_actions:
                self._service.remote_action(action)
            self._remote_actions = list()
            return

        self._logged_in = True
        self._post_login_ops()

    def _post_login_ops(self):
        self._is_gui_logging = False
        self._sync_first_start = False
        user_email = self._login_data['user_email']
        last_user_email = self._config.last_user_email
        new_user = self._main_cfg.host != self._main_cfg.old_host
        self._main_cfg.set_settings({'old_host': self._main_cfg.host})

        self._save_login_settings(self._login_data)
        self._service.gui_logged_in(
            self._login_data, new_user, self._download_backups)
        self._status = STATUS_DISCONNECTED
        self._update_status()
        self.show_main_page()

    def _save_login_settings(self, login_data):
        changed_settings = dict(
            user_email=login_data['user_email'],
            user_password_hash=login_data['password_hash'],
            user_hash=login_data['user_hash'],
            node_hash=login_data['node_hash'],
        )
        if self._download_backups is not None:
            changed_settings['download_backups'] = self._download_backups
            self._main_cfg.set_settings(
                {'download_backups': self._download_backups})

        self._config.set_settings(changed_settings)

    def show_waiting_button(self):
        self._logged_in = True
        self._update_status()
        self._ui.auth_button.setText(tr("Please wait..."))
        self._ui.auth_page.setDisabled(True)
        self._tray.set_tool_tip(tr("Pvtbox connecting..."))

    def set_language(self, lang):
        translations = get_available_languages()
        if not translations:
            return

        if lang not in translations:
            lang = 'en'

        assert lang in translations
        self.retranslated.emit()

    def show_tray_notification(self, text, title=""):
        return self._tray.show_tray_notification(text=text,
                                                 title=title)

    def save_to_clipboard(self, text):
        cb = QApplication.clipboard()
        # cb.clear(mode=cb.Clipboard)
        cb.setText(text, mode=cb.Clipboard)
        self._service.is_saved_to_clipboard(cb.text() == text)

    def request_to_user(self, dialog_id, text,
                        buttons=(tr("Yes"), tr("No")), title="",
                        close_button_index=-1, close_button_off=False,
                        on_clicked_cb=None,
                        details=''):
        if on_clicked_cb is None:
            on_clicked_cb = self._service.dialog_clicked
        return self._tray.request_to_user(dialog_id,
            text=text, buttons=buttons, title=title,
            close_button_index=close_button_index,
            close_button_off=close_button_off,
            parent=self._window, on_clicked_cb=on_clicked_cb,
            details=details)

    def lost_folder_dialog(self, dialog_id, path,
                           dialog_clicked=None, dialog_finished=None):
        if not dialog_clicked:
            dialog_clicked = self._service.dialog_clicked
        if not dialog_finished:
            dialog_finished = self._service.dialog_finished
        logger.info('Showing lost folder dialog to user')
        dialog = LostFolderDialog(
            self._window,
            path=path,
            restoreFolder=dialog_clicked,
            dialog_id=dialog_id)
        self._lost_folder_opened = True
        dialog.show()
        self._lost_folder_opened = False
        dialog_finished(dialog_id)

    def _show_lost_folder_dialog(self):
        from common.config import load_config

        config = load_config()
        root = config.sync_directory

        def dialog_clicked(dialog_id, button_index):
            get_bases_dir(root, create=True)

        self.lost_folder_dialog(
            0, root, dialog_clicked, lambda d_i: None)
        logging_setup(loglevel=self._loglevel, copies_logging=False)
        self._service_client.start(self._args)
        self.autologin()

    def on_network_error(self, error):
        if self._network_error_show_timer.isActive():
            self._network_error_show_timer.stop()
        if self._disk_space_low or self._status != STATUS_IN_WORK:
            return

        self._network_error_text = error
        self._network_error_dots = 0
        self._ui.network_error.setStyleSheet(
            "QLabel {color: red;}"
            "QToolTip { background-color: #222222; color: white;}")
        self._on_show_network_error()

    def _on_show_network_error(self):
        suffix = '.' * self._network_error_dots + \
                 ' ' * (2 - self._network_error_dots)
        self._ui.network_error.setText(
            self._network_error_text + suffix)
        self._network_error_dots = (self._network_error_dots + 1) % 3
        self._network_error_show_timer.start()

    def on_clear_network_error(self):
        self._on_network_error_reset()

    def _on_network_error_reset(self):
        if self._disk_space_low:
            return

        if self._network_error_show_timer.isActive():
            self._network_error_show_timer.stop()

        self._set_regular_transfers_text()
        self._network_error_text = ''

    def on_download_progress(self,
                             display_text,
                             current_downloading_percent,
                             total_downloads):
        self._on_network_error_reset()
        if self._status != STATUS_IN_WORK:
            self._display_text = None
            return
        self._display_text = display_text
        self._total_downloads = total_downloads
        self._current_downloading_percent = current_downloading_percent
        self._update_status()

    def _set_status_text(self, new_status, new_substatus):
        indexing_str2 = tr(' {} files').format(self._fs_events_count) \
            if self._fs_events_count else ''
        indexing_str = tr('Indexing {} files...').format(self._fs_events_count) \
            if self._fs_events_count else ''
        start_syncing_str = tr('Syncing{}') if not self._events_erased else '{}'
        syncing_str = tr('Removing collaboration events...\n{} events removed')\
            .format(self._events_erased) if self._events_erased else \
            tr(' {} local, {} remote changes...\n{}')\
            .format(self._local_events_count, self._remote_events_count,
                    indexing_str) \
            if self._local_events_count or self._remote_events_count or \
               indexing_str else '...\n'
        percent_str = tr('from {} remote changes') \
            .format(self._remote_events_count) \
            if new_substatus != SUBSTATUS_SHARE \
            else tr('from {} files total').format(self._total_downloads)
        status_texts = {
            STATUS_PAUSE: tr('Paused sync') + '\n',
            STATUS_WAIT: tr('Synced') + '\n',
            STATUS_IN_WORK: (tr(
                '{}... {}%\n{}',
                elided(self._display_text,
                       self._ui.status_text_label,
                       self._ui.status_text_label.width() * 0.85),
                self._current_downloading_percent,
                percent_str)
                if self._display_text else tr(
                'Downloading share...') if new_substatus == SUBSTATUS_SHARE
                else start_syncing_str.format(syncing_str)),
            STATUS_DISCONNECTED: tr('Connecting...') + '\n'
            if new_substatus != SUBSTATUS_APPLY
            else tr('Applying new configuration...') + '\n',
            STATUS_INIT: tr('Connecting...') + '\n',
            STATUS_INDEXING: tr('\nIndexing{}...'.format(indexing_str2)),
        }
        status_text = status_texts.get(new_status)

        if status_text is not None:
            self._ui.status_text_label.setText(status_text)
        else:
            logger.warning(
                "No status text for status %s", new_status)

    def _set_start_stop_icon(self, new_status):
        icon = self._button_icons.get(new_status)
        if icon is not None:
            self._ui.start_stop_button.setIcon(icon)
        else:
            logger.warning(
                "No start/stop button icon for status %s", new_status)

    def on_sync_status_changed(self, new_status, new_substatus,
                               local_events_count, remote_events_count,
                               fs_events_count, events_erased):
        assert new_status in \
               (STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK,
                STATUS_INDEXING, STATUS_INIT, STATUS_DISCONNECTED)
        logger.verbose("Sync status changed to %s, substatus %s, "
                       "local_count %s, remote_count %s, "
                       "fs_events %s, events_erased %s",
                       new_status, new_substatus,
                       local_events_count, remote_events_count,
                       fs_events_count, events_erased)
        self._status = new_status
        self._substatus = new_substatus
        self._local_events_count = local_events_count
        self._remote_events_count = remote_events_count
        self._fs_events_count = fs_events_count
        self._events_erased = events_erased
        self._update_status()

    def _on_logged_out(self, wipe_all):
        self._logged_in = False
        # disable sending start/stop to sync
        with self._start_stop_processing_lock:
            if self._should_process_start:
                self._should_process_start = False
                self.sync_started.emit()
            if self._should_process_stop:
                self._should_process_stop = False
                self.sync_stopped.emit()
            if self._start_stop_processing_timer.isActive():
                self._start_stop_processing_timer.stop()

        if wipe_all:
            action = {"action_type": "wipe",
                      "action_uuid": ""}
            self._service.remote_action(action)
            self.show_loading_screen(text=tr("Wiping..."))
        else:
            self._update_status()
            self._download_backups = self._config.get_setting(
                "download_backups", False)
            self.show_auth_page(False, True)
        self._close_opened_dialogs()

    def on_show_settings_click(self, migrate=False):
        if self._dialogs_opened():
            return

        def on_logged_out(wipe_all):
            self._on_logged_out(wipe_all)

        def on_logging_disabled_changed(logging_disabled):
            self._logging_disabled_changed.emit(logging_disabled)

        settings_form = Settings(self._config,
                                 self._main_cfg,
                                 self.start_service,
                                 self.exit_service,
                                 parent=self._window,
                                 size=self._sync_dir_size,
                                 migrate=migrate)
        settings_form.logged_out.connect(on_logged_out)
        settings_form.logging_disabled_changed.connect(
            on_logging_disabled_changed)
        self._settings_opened = True

        def on_finished():
            self._settings_opened = False
            settings_form.logged_out.disconnect(on_logged_out)
            settings_form.logging_disabled_changed.disconnect(
                on_logging_disabled_changed)

        settings_form.show(on_finished)

    def _get_ss_status(self, new_status):
        ss_statuses = {STATUS_WAIT: SS_STATUS_SYNCED,
                       STATUS_PAUSE: SS_STATUS_PAUSED,
                       STATUS_IN_WORK: SS_STATUS_SYNCING,
                       STATUS_INIT: SS_STATUS_CONNECTING,
                       STATUS_DISCONNECTED: SS_STATUS_CONNECTING,
                       STATUS_INDEXING: SS_STATUS_INDEXING,
                       }
        return ss_statuses.get(new_status, SS_STATUS_SYNCED)

    def _on_dialog_sync_status_changed(self, new_status, new_substatus):
        ss_node_status = self._get_ss_status(new_status)
        self._devices_list_dialog.update_node_status(ss_node_status, new_substatus)

    def on_show_device_list_click(self):
        if self._dialogs_opened() or self._devices_list_dialog:
            return

        self._devices_list_dialog = DeviceListDialog(
            initial_data=self._main_cfg.devices,
            disk_usage=self._sync_dir_size,
            parent=self._window, node_status=self._get_ss_status(self._status),
            node_substatus=self._substatus, dp=self._dp,
            nodes_actions=self._nodes_actions,
            license_type=self._main_cfg.license_type)

        self.sync_dir_size_changed.connect(
            self._devices_list_dialog.update_sync_dir_size)
        self.nodes_info.connect(
            self._devices_list_dialog.update)
        self.download_speed_changed.connect(
            self._devices_list_dialog.update_download_speed)
        self.upload_speed_changed.connect(
            self._devices_list_dialog.update_upload_speed)

        self.sync_status_changed.connect(self._on_dialog_sync_status_changed)

        self._devices_list_dialog.show_tray_notification.connect(
            self.show_tray_notification, Qt.QueuedConnection)
        self._devices_list_dialog.management_action.connect(
            self._on_management_action, Qt.QueuedConnection)
        self._devices_list_dialog.start_transfers.connect(
            self._show_transfers, Qt.QueuedConnection)
        self.management_action_in_progress.connect(
            self._devices_list_dialog.on_management_action_in_progress,
            Qt.QueuedConnection)

        def on_finished():
            self.sync_status_changed.disconnect(
                self._on_dialog_sync_status_changed)
            self.nodes_info.disconnect(
                self._devices_list_dialog.update)
            self.download_speed_changed.disconnect(
                self._devices_list_dialog.update_download_speed)
            self.upload_speed_changed.disconnect(
                self._devices_list_dialog.update_upload_speed)
            self.sync_dir_size_changed.disconnect(
                self._devices_list_dialog.update_sync_dir_size)
            self._devices_list_dialog.show_tray_notification.disconnect(
                self.show_tray_notification)
            self._devices_list_dialog.start_transfers.disconnect(
                self._show_transfers, Qt.QueuedConnection)
            self.management_action_in_progress.disconnect(
                self._devices_list_dialog.on_management_action_in_progress)
            self._devices_list_dialog = None

        self._devices_list_dialog.show(on_finished)

    def _save_nodes_info(self, nodes_info):
        try:
            devices = {node_id: nodes_info[node_id] for node_id in nodes_info
                       if nodes_info[node_id]['type'] == 'node' and
                       nodes_info[node_id]['own']}
        except KeyError as e:
            logger.warning("Missing nodes info key '%s'", e)
            return
        self._main_cfg.set_settings({'devices': devices})

    def on_start_stop_click(self):
        if self._status in (
                STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK, STATUS_INDEXING):
            with self._start_stop_processing_lock:
                processing = False
                if self._should_process_start \
                        or self._should_process_stop:
                    processing = True
                else:
                    self.sync_status_changed.disconnect()
                    self.download_progress.disconnect()

                if self._status == STATUS_PAUSE:
                    new_status = STATUS_INDEXING
                    self._should_process_start = True
                    self._should_process_stop = False
                    self._set_paused_state.emit(False)
                else:
                    new_status = STATUS_PAUSE
                    self._should_process_start = False
                    self._should_process_stop = True
                    self._set_paused_state.emit(True)
            self.on_sync_status_changed(new_status, self._substatus,
                                        self._local_events_count,
                                        self._remote_events_count,
                                        self._fs_events_count,
                                        self._events_erased)
            if self._devices_list_dialog is not None:
                self._on_dialog_sync_status_changed(
                    new_status, self._substatus)
            if processing:
                return

            self._process_start_stop_sync()

    def _process_start_stop_sync(self):
        if self._should_process_stop:
            if not self._sync_first_start:
                with wait_signal(self.sync_started, timeout=0):
                    pass
            with wait_signal(self.sync_stopped, timeout=0):
                self.stop_sync.emit()
            with self._start_stop_processing_lock:
                self._should_process_stop = False
        elif self._should_process_start:
            with wait_signal(self.sync_started, timeout=0):
                self.start_sync.emit()
            with self._start_stop_processing_lock:
                self._should_process_start = False

        with self._start_stop_processing_lock:
            if self._should_process_start or \
                    self._should_process_stop:
                if not self._start_stop_processing_timer.isActive():
                    self._start_stop_processing_timer.start()
            else:
                self.sync_status_changed.connect(
                    self.on_sync_status_changed)
                self.download_progress.connect(
                    self.on_download_progress)
                self.update_status.emit()
                if self._devices_list_dialog is not None:
                    self.sync_status_changed.connect(
                        self._on_dialog_sync_status_changed)

    def _on_sync_started(self):
        self._sync_first_start = True

    def init_file_list(self, file_list):
        if self._status == STATUS_PAUSE:
            return
        
        self._gui_file_list.set_file_list(file_list)

    @qt_run
    def open_webfm(self):
        self._show_timeout_notification = True
        logger.info("Opening web fm")
        res = self._web_api.get_token_login_link()
        if res and 'result' in res and res['result'] == 'success':
            data = res.get('data', None)
            logger.info("Token login link got %s", data)
            if data:
                link = data.get('login_link', None)
                if link:
                    open_link(link)()
                    return
        logger.warning("Can't get token login link")
        open_link(self._web_fm_uri)

    def on_download_link_handler(self, link):
        logger.verbose("Handle download link: %s", link)
        self.show()
        if get_platform() == "Windows":
            dialog = QFileDialog(
                self._window,
                tr('Choose folder to download shared object'),
                self._config.sync_directory)
            dialog.setFileMode(dialog.Directory)
            dialog.setWindowModality(Qt.ApplicationModal)
            dialog.setModal(True)
            dialog.setWindowFlags(Qt.WindowStaysOnTopHint)

            if dialog.exec_() == QDialog.Accepted:
                selected_folder = dialog.selectedFiles()[0]
            else:
                selected_folder = ''
        else:
            dialog = QFileDialog()
            dialog.setWindowModality(Qt.ApplicationModal)
            dialog.setModal(True)
            selected_folder = dialog.getExistingDirectory(
                self._window,
                tr('Choose folder to download shared object'),
                self._config.sync_directory)

        selected_folder = ensure_unicode(selected_folder)
        if not selected_folder:
            return
        if not op.exists(selected_folder):
            self.on_download_link_handler(link)
            return

        self.received_download_link.emit(link, selected_folder)

    def disk_space_status_changed(self, disk_space_low,
                                  cfg_orange, cfg_red,
                                  data_orange, data_red, same_volume,
                                  cfg_drive, data_drive,
                                  cfg_space, data_space):
        def set_disk_space_alert():
            low_disk_label.setText(tr("Disk space low"))
            low_disk_label.setMouseTracking(True)
            if is_red:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #ff4646;')
            else:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #ffa275;')
            low_disk_label.enterEvent = enter
            low_disk_label.leaveEvent = leave
            low_disk_label.mouseReleaseEvent = clicked

        def reset_disk_space_alert():
            self._on_network_error_reset()
            self._init_network_error_label()

        def enter(_):
            self._ui.network_error.setCursor(Qt.PointingHandCursor)
            if is_red:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #c40000;')
            else:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #e64a00;')

        def leave(_):
            self._ui.network_error.setCursor(Qt.ArrowCursor)
            if is_red:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #ff4646;')
            else:
                low_disk_label.setStyleSheet(
                    'margin: 0; border: 0; text-align:right center;'
                    'color: #ffa275;')

        def clicked(_):

            def on_message_clicked(_, button_index):
                if button_index == 0:
                    self._show_transfers()

            cfg_space_str = tr (" ({} free)", format_with_units(int(cfg_space)))
            data_space_str = tr (" ({} free)",format_with_units(int(data_space)))
            cfg_str = cfg_drive + cfg_space_str if (cfg_orange or cfg_red) \
                                   and not same_volume else ""
            data_str = data_drive + data_space_str if data_orange or data_red \
                else ""
            disk_str = tr(" on drives {}, {}", cfg_str, data_str) \
                if cfg_str and data_str \
                else tr(" on drive {}{}", cfg_str, data_str) \
                if cfg_str or data_str else ""
            extrem = tr("extremely ") if is_red else ""

            msg = tr("Disk space is {}low.\n"
                     "If disk will be full, file sync may not work.\n"
                     "Please clean disk space{}.",
                     extrem, disk_str)
            buttons = (tr("Open Transfers"), tr("Ok"))
            self._tray.request_to_user(0, msg, buttons,
                                       close_button_off=False,
                                       on_clicked_cb=on_message_clicked)

        low_disk_label = self._ui.network_error
        self._disk_space_low = disk_space_low
        if self._disk_space_low:
            is_red = cfg_red or data_red
            set_disk_space_alert()
        else:
            reset_disk_space_alert()

    def _on_sync_dir_size_changed(self, size):
        self._sync_dir_size = int(size)

    def _on_settings_of_interest_changed(self, settings):
        if not self._is_gui_logging:
            self._main_cfg.set_settings(settings)

    @qt_run
    def autologin(self, is_silent=False):
        def connect_web_api_signals():
            self._web_api.login_failed.connect(self.login_failed)
            self._web_api.registration_failed.connect(
                self.registration_failed)

        try:
            self._web_api.login_failed.disconnect(self.login_failed)
            self._web_api.registration_failed.disconnect(
                self.registration_failed)
        except RuntimeError as e:
            logger.warning("Can't disconnect web_api signals. Reason: %s", e)

        logger.info('Trying to autologin')
        status = False
        if self._main_cfg.autologin or is_silent:
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
                    self._is_gui_logging = False
                    self._start_login_data_timer.emit()
                    connect_web_api_signals()
                    return

                if not is_silent:
                    if res and 'errcode' in res:
                        if res['errcode'] in (
                                'ERROR_SIGNATURE_INVALID',
                                'LICENSE_LIMIT'
                        ):
                            self._show_auth_page_signal.emit(False, False)
                            connect_web_api_signals()
                            return
                        elif res['errcode'] == 'USER_NOT_FOUND':
                            self._main_cfg.set_settings({
                                'node_hash': sha512(uuid4().bytes).hexdigest()})
                        status, _ = self._web_api.signup(
                            fullname='',
                            email=self._main_cfg.user_email,
                            password=self._main_cfg.user_password_hash)
                        if status:
                            status, _ = self._web_api.login(
                                login=self._main_cfg.user_email,
                                password=self._main_cfg.user_password_hash)
                    elif res is None:
                        self._show_network_error_page_signal.emit()
                        self._start_autologin_timer.emit(1000)
                        connect_web_api_signals()
                        return

            if not status and not is_silent:
                node_hash = sha512(uuid4().bytes).hexdigest() \
                    if not self._main_cfg.node_hash \
                    else self._main_cfg.node_hash
                self._main_cfg.set_settings({
                    'node_hash': node_hash,
                    'user_hash': None,
#                    'user_email': None,
                    'user_password_hash': None,
                })
        if not is_silent:
            if not status:
                self._show_auth_page_signal.emit(False, True)

        connect_web_api_signals()

    def is_wiping_all(self):
        self._is_wiping_all = True

    def wiped_all(self):
        self._is_wiping_all = False
        self._logged_in = False
        self.exit_request.disconnect(self._on_exit_request)
        self.exit_service()
        disable_file_logging(logger)
        sync_dir = self._config.sync_directory
        try:
            remove_file(get_cfg_filename('main.conf'))
            remove_file(get_bases_filename(
                self._config.sync_directory, 'service_stats.db'))
        except Exception as e:
            logger.warning("Can't wipe conf and stats files. Reason: %s", e)

        self._config.set_settings(dict(sync_directory=sync_dir))
        enable_file_logging(logger)
        self.exit_request.connect(self._on_exit_request)
        self._service_client._drop_starting_service()
        self.start_service(args=())

        self._is_gui_logging = True
        self._main_cfg = load_config()
        self._restart_web_api()
        logger.verbose("main config: %s", self._main_cfg.config)
        self._update_status()
        self.show_auth_page(False)

    def _restart_web_api(self):
        self._web_api.loggedIn.disconnect(self._on_gui_logged_in)
        self._web_api.login_failed.disconnect(self.login_failed)
        self._web_api.registered.disconnect(self.registered)
        self._web_api.registration_failed.disconnect(self.registration_failed)

        self._web_api = Client_API(self._main_cfg, parent=self)

        self._web_api.loggedIn.connect(self._on_gui_logged_in)
        self._web_api.login_failed.connect(self.login_failed)
        self._web_api.registered.connect(self.registered)
        self._web_api.registration_failed.connect(self.registration_failed)

    def long_paths_ignored(self, long_paths):
        self.request_to_user(
            0, text=tr("Long paths from sync directory "
                       "are excluded from sync"),
            title=tr("Long paths"),
            buttons=[tr("Ok")],
            on_clicked_cb=None,
            details='\n'.join(long_paths))

    def license_type_changed(self, license_type):
        self._config.set_settings({'license_type': license_type})
        if self._devices_list_dialog:
            self._devices_list_dialog.set_license_type(license_type)

    def on_web_request_timeout(self):
        if self._show_timeout_notification:
            self._tray.show_tray_notification(
                tr("Web API server request timeout"), tr("Pvtbox"))
            self._show_timeout_notification = False

    def restart_me(self):
        self._restarting = True
        self.exit_service()
        self.start_service()

    def _dialogs_opened(self):
        return self._settings_opened or \
               self._about_dialog_opened or \
               self._lost_folder_opened

    def _any_dialog_opened(self):
        return self._dialogs_opened() or \
               self._devices_list_dialog is not None or \
               self._transfers_info.dialog_opened()

    def _open_path(self):
        if self._config:
            qt_open_path(self._config.sync_directory)

    def _update_args_with_logging_disabled(self):
        if self._logging_disabled:
            if "--logging-disabled" not in self._args:
                self._args.extend(["--logging-disabled", "TRUE"])
        elif "--logging-disabled" in self._args:
            ind = self._args.index("--logging-disabled")
            self._args = self._args[:ind] + self._args[ind + 2:]

    def _on_logging_disabled_changed(self, logging_disabled):
        self._logging_disabled = logging_disabled
        self._update_args_with_logging_disabled()
        if self._logging_disabled:
            logger.info("Logging is disabled by user")
            disable_file_logging(logger, clear_old=False)
            enable_console_logging(False)
        else:
            if self._logging_disabled_from_start:
                logging_setup(self._loglevel)
                self._logging_disabled_from_start = False
            else:
                enable_file_logging(logger)
                enable_console_logging(True)
            logger.info("Logging is enabled by user")
        self.restart_me()

    def _show_transfers(self):
        self._transfers_info.show_dialog()

    def revert_failed(self, failed_uuids):
        self._tray.show_tray_notification(
            tr("Revert failed"), tr("Pvtbox"))
        self._transfers_info.revert_failed(failed_uuids)

    def _set_regular_transfers_text(self):
        self._ui.network_error.setText(tr("Transfers"))
        self._ui.network_error.setStyleSheet(
            "QLabel {color: darkGreen; }"
            "QToolTip { background-color: #222222; color: white;}")

    def signalserver_address(self, address):
        self._transfers_info.set_signalserver_address(address)

    def connected_nodes_changed(self, nodes_num):
        self._transfers_info.on_connected_nodes_changed(nodes_num)

    def _close_opened_dialogs(self):
        self._transfers_info.close()
        self._notifications.close()
        if self._devices_list_dialog:
            self._devices_list_dialog.close()

    def _on_management_action(self, action_name, action_type, node_id, is_itself):
        if is_itself:
            if action_type in ("logout", "wipe"):
                if action_type == "logout":
                    self._config.set_settings({'user_password_hash': ""})
                self._on_logged_out(action_type=="wipe")
            return

        preliminary_notifications = {
            "hideNode": tr("Removing node…"),
            "execute_remote_action": tr("Sending remote action…"),
        }
        msg = preliminary_notifications.get(action_name, "")
        self.show_tray_notification(msg)
        self._send_management_request(action_name, node_id, action_type)

    @qt_run
    def _send_management_request(self, action_name, node_id, action_type):
        logger.debug("Sending request for remote action %s, "
                     "type %s, node_id (%s)",
                     action_name, action_type, node_id)
        res = self._web_api.node_management_action(
            action_name, node_id, action_type)

        final_notifications = {
            "hideNode": tr("Successfully removed node"),
            "execute_remote_action": tr("Remote action sent successfully "),
        }
        msg = tr("Can't send remote action")
        if res and "result" in res:
            if res["result"] == "success":
                msg = final_notifications.get(action_name, "")
            else:
                if "errcode" in res and \
                    res["errcode"] in ("NODE_LOGOUT_EXIST", "NODE_WIPED"):
                    ac_type = "wipe" if res["errcode"] == "NODE_WIPED" \
                        else action_type
                    self.management_action_in_progress.emit(
                        action_name, ac_type, node_id)
                if "info" in res:
                    msg = res.get("info", "")
        self.show_tray_notification(msg)

    def get_ui(self):
        return self._ui

    def _set_uris(self):
        self._web_api.set_uris()
        host = self._main_cfg.host
        self._password_reminder_uri = PASSWORD_REMINDER_URI.format(host)
        self._help_uri = HELP_URI.format(host)
        self._web_fm_uri = WEB_FM_URI.format(host)
        self._privacy_uri = PRIVACY_URI.format(host)
        self._terms_uri = TERMS_URI.format(host)
