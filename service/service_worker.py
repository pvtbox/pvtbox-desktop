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
from collections import deque
import logging
import os
import os.path as op
from types import FunctionType
import shutil
import threading
import time
import collections
import webbrowser
from service.network.network_speed_calculator import NetworkSpeedCalculator

from PySide2.QtCore import QTimer, Signal, QObject, Qt

from common.utils import get_platform

from common.utils import is_portable
from .events_db import File, EventsDbBusy
from .gui_proxy import GuiProxy
from .service_server import ServiceServer
from common.async_qt import qt_run
from common.file_path import FilePath
from .file_status_manager import FileStatusManager
from common.utils import get_cfg_dir, get_data_dir, \
    get_cfg_filename, touch, ensure_unicode, get_downloads_dir,\
    make_dirs, remove_dir, create_shortcuts, get_bases_filename, \
    get_patches_dir, make_dir_hidden, get_dir_size, \
    get_free_space_mb, get_free_space, get_drive_name, \
    wipe_internal, remove_file, benchmark, is_first_launch, init_init_done
from common.constants import FREE_LICENSE, GET_PRO_URI, UNKNOWN_LICENSE
from common.constants import STATUS_WAIT, STATUS_PAUSE, STATUS_IN_WORK, \
    SS_STATUS_SYNCING, SS_STATUS_SYNCED, SS_STATUS_PAUSED, SUBSTATUS_SYNC, \
    STATUS_INDEXING, STATUS_INIT, STATUS_DISCONNECTED, STATUS_LOGGEDOUT, \
    SS_STATUS_INDEXING, SS_STATUS_LOGGEDOUT, SUBSTATUS_APPLY, \
    DISK_LOW_ORANGE, DISK_LOW_RED, FILE_LINK_SUFFIX, \
    NETWORK_WEBRTC_RELAY, NETWORK_WEBRTC_DIRECT, CONNECTIVITY_ALIVE_TIMEOUT
from .upload_task_handler import UploadTaskHandler
from common.logging_setup import set_economode, disable_file_logging
from common.application import Application

from common.translator import tr
from db_migrations import upgrade_db, stamp_db

from .sync_mechanism.sync import Sync
from .websharing import WebshareHandler
from .shell_integration \
    import signals as shell_integration_signals
from service.signalling import SignalServerClient
from service import transport_setup, shell_integration
from common.webserver_client import Client_API
from service.network.traffic_info_collector import TrafficInfoCollector
from common import config

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ApplicationWorker(QObject):
    NETWORK_SPEED_NOTIFICATION_PERIOD = 2.
    FILES_TO_HOLD = ["main.conf", "lock", "init_done"]

    _gui_connected = Signal()
    show_login_page = Signal(bool,  # show registration
                             bool,  # clean errors
                             )
    show_network_error_page = Signal()
    show_notification = Signal(str, str)
    show_request_to_user = Signal(str, tuple, str, int, bool, str)
    show_lost_folder_dialog = Signal(str, FunctionType, FunctionType)
    save_to_clipboard_signal = Signal(str)
    share_changed = Signal(list)
    loggedIn = Signal(dict)
    loggedOut = Signal()
    file_list_changed = Signal(list)
    exit_worker = Signal()
    exited = Signal()
    settings_changed = Signal(dict)
    # settings_changing_finished = Signal()
    upload_speed_changed = Signal(int)
    download_speed_changed = Signal(int)
    disk_space_low_status = Signal(bool,        # disk space low
                                   bool,        # cfg space orange
                                   bool,        # cfg space red
                                   bool,        # data space orange
                                   bool,        # data space red
                                   bool,        # same volume for cfg and data
                                   str,     # cfg_drive
                                   str,     # data_drive
                                   str,         # cfg_space
                                   str)         # data_space
    _possibly_sync_folder_is_removed = Signal()

    def __init__(self, cfg, tracker, app_start_ts, args):
        QObject.__init__(self, parent=None)

        self._cfg = cfg
        self._tracker = tracker
        self._app_start_ts = app_start_ts
        self._args = args

        self._gui = None
        self._service_server = None
        self._web_api = None
        self._events_db = None
        self._ss_client = None
        self._webshare_handler = None

        self._init_file_list_attrs()
        self._init_speed_size()

        self._ss_node_status = SS_STATUS_LOGGEDOUT
        self._ss_sent_node_status = SS_STATUS_LOGGEDOUT
        self._disk_usage_changed = False

        self._ready_to_clean_copies = False

        self._restoring_sync_dir_lock = threading.RLock()
        self._is_restoring_sync_dir = False

        self._disk_space_low = False
        self._disk_full_request_pending = False

        self._init_login_attrs()

        self._gui_changed_settings = False
        self._sync_dir_size_sent = None
        self._dir_size_calculating = False
        self._clear_sent_status()

        self._connectivity_restarted = [False, False]

        self._node_info = {}
        self._node_info_sent = {}

        self._connected = threading.Event()
        self._connected.clear()
        self._connected_nodes = [0, 0]  # sync connectivity, share connectivity

        # Initialize API server client instance
        self._web_api = Client_API(self._cfg, self._tracker, parent=self)

        self._ss_client = SignalServerClient(self)

        self._init_timers()

        # Initialize local event DB
        self._init_events_db()

        transport_setup.signals.setParent(self)
        self._network_speed_calculator = NetworkSpeedCalculator(
            self.NETWORK_SPEED_NOTIFICATION_PERIOD,
            upload_speed_changed_cb=
            transport_setup.signals.upload_speed_changed.emit,
            download_speed_changed_cb=
            transport_setup.signals.download_speed_changed.emit,
            upload_size_changed_cb=
            transport_setup.signals.upload_size_changed.emit,
            download_size_changed_cb=
            transport_setup.signals.download_size_changed.emit
        )
        # Initialize Sync instance
        self._sync = Sync(cfg=self._cfg,
                          web_api=self._web_api,
                          db=self._events_db,
                          ss_client=self._ss_client,
                          get_sync_dir_size=self._get_sync_folder_size,
                          tracker=self._tracker, parent=self,
                          network_speed_calculator=self._network_speed_calculator)
        self._file_status_manager = FileStatusManager(self._sync, self._cfg)

        self._components_to_exit = 1 + int(self._tracker is not None)

        self._upload_handler = UploadTaskHandler(
            cfg=self._cfg,
            web_api=self._web_api,
            filename=get_bases_filename(
                self._cfg.sync_directory, 'uploads.db'),
            ss_client=self._ss_client,
            tracker=self._tracker,
            parent=self,
            network_speed_calculator=self._network_speed_calculator,
            db=self._events_db
        )

        shell_integration_signals.setParent(self)

        # create object 'traffic info collector'
        self._info_collector = TrafficInfoCollector(self._ss_client)

    def _init_speed_size(self):
        self._sync_folder_size = None
        self._avg_download_speed = 0
        self._download_speed_sent = 0
        self._avg_upload_speed = 0
        self._upload_speed_sent = 0

    def _init_login_attrs(self):
        self._logged_in = False
        self._login_data = dict()
        self._auth_failed = False
        self._was_logout = False
        self._last_event_uuid = None

    def _init_file_list_attrs(self):
        self._file_list = collections.OrderedDict()
        self._last_time_files_sent = 0
        self._files_send_timeout = 1.0
        self._file_list_ready = True

    def _init_timers(self):
        self._timers = set()

        self._update_node_info_timer = QTimer(self)
        self._timers.add(self._update_node_info_timer)
        self._update_node_info_timer.setInterval(10 * 1000)

        self._ready_to_clean_copies_timer = QTimer(self)
        self._timers.add(self._ready_to_clean_copies_timer)
        self._ready_to_clean_copies_timer.setSingleShot(True)
        self._ready_to_clean_copies_timer.timeout.connect(
            self._set_ready_to_clean_copies)

        self._file_list_timer = QTimer(self)
        self._timers.add(self._file_list_timer)
        self._file_list_timer.setInterval(500)
        self._file_list_timer.setSingleShot(True)
        self._file_list_timer.timeout.connect(
            self._emit_file_list_changed)

        self._check_disk_space_timer = QTimer(self)
        self._timers.add(self._check_disk_space_timer)
        self._check_disk_space_timer.setInterval(5 * 60 * 1000)
        self._check_disk_space_timer.setSingleShot(True)
        self._check_disk_space_timer.timeout.connect(
            self._on_disk_space_check)

        self._network_speeds_timer = QTimer(self)
        self._timers.add(self._network_speeds_timer)
        self._network_speeds_timer.setInterval(1 * 1000)

        self._share_info_timer = QTimer(self)
        self._timers.add(self._share_info_timer)
        self._share_info_timer.setInterval(3 * 1000)
        self._share_info_timer.setSingleShot(True)
        self._share_info_timer.timeout.connect(
            self._on_sharing_changed)

        self._sync_status_timer = QTimer(self)
        self._timers.add(self._sync_status_timer)
        self._sync_status_timer.setInterval(2000)
        self._sync_status_timer.setSingleShot(False)
        self._sync_status_timer.timeout.connect(
            self._on_get_sync_status)

        self._connectivity_alive_timer = QTimer(self)
        self._timers.add(self._connectivity_alive_timer)
        self._connectivity_alive_timer.setInterval(
            int(CONNECTIVITY_ALIVE_TIMEOUT / 3) * 1000)
        self._connectivity_alive_timer.setSingleShot(False)
        self._connectivity_alive_timer.timeout.connect(
            self._on_check_connectivity_alive)

        self._clean_old_events_timer = QTimer(self)
        self._timers.add(self._clean_old_events_timer)
        self._clean_old_events_timer.setSingleShot(True)
        self._clean_old_events_timer.setInterval(20 * 1000)
        self._clean_old_events_timer.timeout.connect(
            self._on__clean_old_events_timeout)

    def _setup_network_speed(self):
        from service.transport_setup import signals as transport_signals

        self._current_download_speed = 0

        def update_download_speed(value):
            self._current_download_speed = value
        transport_signals.download_speed_changed.connect(update_download_speed)

        self._current_upload_speed = 0

        def update_upload_speed(value):
            self._current_upload_speed = value
        transport_signals.upload_speed_changed.connect(update_upload_speed)

        self._download_speeds = deque(maxlen=10)
        self._upload_speeds = deque(maxlen=10)

        def append_network_speeds():
            self._download_speeds.append(self._current_download_speed)
            self._upload_speeds.append(self._current_upload_speed)
            if self._has_avg_network_speed_changed():
                self.upload_speed_changed.emit(self._avg_upload_speed)
                self.download_speed_changed.emit(self._avg_download_speed)

        self._network_speeds_timer.timeout.connect(append_network_speeds)
        self._network_speeds_timer.start()

    def _has_avg_network_speed_changed(self):
        def remove_leading_zeroes(deq):
            if deq:
                while deq:
                    elem = deq.popleft()
                    if elem:
                        break
                deq.appendleft(elem)

        if not hasattr(self, '_download_speeds'):
            return False

        changed = False
        remove_leading_zeroes(self._download_speeds)
        remove_leading_zeroes(self._upload_speeds)

        avg_download_speed = \
            sum(self._download_speeds) / max(len(self._download_speeds), 1)
        if avg_download_speed != self._avg_download_speed:
            self._avg_download_speed = avg_download_speed
            changed = True
        avg_upload_speed = \
            sum(self._upload_speeds) / max(len(self._upload_speeds), 1)
        if avg_upload_speed != self._avg_upload_speed:
            self._avg_upload_speed = avg_upload_speed
            changed = True
        return changed

    def create_data_dir(self):
        '''
        Creates synchronization directory

        @raise SystemExit
        '''

        # Try to create
        data_dir = self._cfg.sync_directory
        if not op.exists(data_dir):
            try:
                make_dirs(data_dir, is_folder=True)
            except Exception:
                pass

        # Configuration directory creation failed
        if not op.exists(data_dir):
            logger.error(
                "Failed to create default synchronization directory")
            if not self._is_restoring_sync_dir:
                self.show_notification.emit(
                    tr("Failed to create synchronization directory"),
                    tr("Pvtbox"))
                self._on_critical_error(
                    "Failed to create synchronization directory")
            return
        logger.debug("Synchronization directory created")

        return data_dir

    def create_sync_dir(self):
        # Try to create sync directory
        data_dir = self.create_data_dir()
        if not data_dir:
            return False

        # Create directory for patch files
        patches_dir = get_patches_dir(data_dir, create=True)
        make_dir_hidden(patches_dir)

        # Create directory for download shared files
        downloads_dir = get_downloads_dir(data_dir=data_dir, create=True)
        logger.debug("downloads_dir: %s", downloads_dir)
        return True

    def start_work(self):
        self._gui_connected.connect(self._on_gui_connected,
                                    Qt.QueuedConnection)
        self._service_server = ServiceServer(get_cfg_dir(create=True))
        self._service_server.set_on_client_connected_callback(
            self.on_gui_connected)

        self._gui = GuiProxy(socket_client=self._service_server,
                             receivers=(self,))
        self._connect_gui_slots()
        self._connect_slots()

        if not self._create_cfg_dir_if_needed():
            return

        self._webshare_handler = WebshareHandler(
            tracker=self._tracker, config=self._cfg, sync=self._sync,
            network_speed_calculator=self._network_speed_calculator,
            db=self._events_db, parent=self)

        self._connect_sync_signals()

        # Create shortcuts to data directory
        create_shortcuts(self._cfg.sync_directory)

        self._connect_upload_handler_signals()
        self._init_transport_setup()
        self._init_shell_integration()
        self._connect_webshare_signals()
        self._connect_gui_signals()
        self._connect_tx_signals()

        self._setup_network_speed()

        # Add statistics event to be before session/login
        self._tracker.session_ready(self._app_start_ts)

        self._sync.force_apply_config()
        logger.info("apply rate limits")
        self._apply_rate_limits()
        self._apply_send_statistic()
        self._set_language()

        self._ready_to_clean_copies_timer.start(10 * 60 * 1000)
        self._connectivity_alive_timer.start()

    def _connect_slots(self):
        # connect misc signals and slots
        self.exit_worker.connect(self.exit, Qt.QueuedConnection)
        self._cfg.settings_changed.connect(self._on_settings_changing)
        self.settings_changed.connect(self.on_settings_changed)
        self._possibly_sync_folder_is_removed.connect(
            self._sync.check_if_sync_folder_is_removed)
        if self._tracker:
            self._tracker.exited.connect(self._wait_components_exit)

    def _connect_gui_slots(self):
        # connect self signals with gui slots
        self.show_login_page.connect(self._gui.show_auth_page)
        self.show_network_error_page.connect(self._gui.show_network_error_page)
        self.show_notification.connect(self._gui.show_tray_notification)
        self.show_request_to_user.connect(self._gui.request_to_user)
        self.save_to_clipboard_signal.connect(self._gui.save_to_clipboard)
        self.share_changed.connect(self._gui.on_share_changed)
        self.show_lost_folder_dialog.connect(self._gui.lost_folder_dialog)
        self.disk_space_low_status.connect(self._gui.disk_space_status_changed)
        self.upload_speed_changed.connect(self._gui.upload_speed_changed)
        self.download_speed_changed.connect(
            self._gui.download_speed_changed)
        self.file_list_changed.connect(self._gui.init_file_list)

    def _create_cfg_dir_if_needed(self):
        created = True
        # Assume it is first launch if configuration directory does not exist
        new_user = is_first_launch()

        # It is the first launch
        if new_user:
            logger.info(
                "Initializing configuration on the first launch...")
            # Here config init should be complete. Create flag file
            init_init_done()
            self.create_sync_dir()
        elif not op.exists(get_patches_dir(self._cfg.sync_directory)):
            get_patches_dir(self._cfg.sync_directory, create=True)
            self._connected.wait(10)
            if self._connected.is_set():
                self._connected.clear()
                self._on_sync_folder_removed(False, True)
            else:
                self._on_critical_error(
                    "Failed to get synchronization directory")
            created = False
        return created

    def _connect_sync_signals(self):
        self._file_status_manager.connect_sync_signals()
        self._sync.exited.connect(self._wait_components_exit)
        self._sync.error_happens.connect(self._on_critical_error)
        self._sync.sync_folder_is_removed.connect(self._on_sync_folder_removed,
                                                  Qt.QueuedConnection)
        self._sync.sync_dir_size_changed.connect(self._on_disk_usage_changed)
        self._sync.status_changed.connect(self._on_status_changed)
        self._sync.download_progress.connect(self._gui.download_progress)
        self._sync.downloads_status.connect(self._gui.downloads_status)
        self._sync.download_error.connect(self._gui.on_network_error)
        self._sync.clear_download_error.connect(
            self._gui.on_clear_network_error)
        self._sync.db_or_disk_full.connect(self._on_db_or_disk_full)
        self._sync.long_paths_ignored.connect(self._gui.long_paths_ignored)
        self._sync.license_alert.connect(self._on_license_alert)
        self._sync.file_list_changed.connect(self._start_file_list_sending)
        self._sync.sync_start_completed.connect(self._complete_login)
        self._sync.revert_failed.connect(self._gui.revert_failed)
        self._sync.connected_nodes_changed.connect(
            lambda n: self._on_connected_nodes_changed(n, -1))
        self._sync.started.connect(self._gui.sync_started)
        self._sync.stopped.connect(self._gui.sync_stopped,
                                   Qt.QueuedConnection)
        self._sync.started.connect(self._on_sync_started)
        self._sync.sync_stopped.connect(self._on_sync_stopped,
                                   Qt.QueuedConnection)
        self._sync.config_changed.connect(
            lambda: self._gui.set_config(self._cfg.get_config()))

        self._sync.status_changed.connect(
            shell_integration_signals.sync_status_changed)
        self._sync.status_changed.connect(self._on_sync_status_changed)
        self._sync.license_type_changed.connect(self._gui.license_type_changed)
        self._sync.file_moved.connect(self._gui.on_file_moved)
        self._sync.offline_dirs.connect(self._gui.offline_dirs)

    def _connect_upload_handler_signals(self):
        self._upload_handler.progress.connect(self._gui.download_progress)
        self._upload_handler.download_status.connect(
            self._gui.downloads_status)
        self._upload_handler.working.connect(self._sync.on_uploads_downloading)
        self._upload_handler.idle.connect(self._sync.on_uploads_idle)
        self._upload_handler.upload_folder_not_synced.connect(
            self._on_upload_not_synced)
        self._upload_handler.upload_cancelled.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'upload', 'cancelled'))
        self._upload_handler.upload_folder_deleted.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'upload', 'deleted'))
        self._upload_handler.upload_folder_excluded.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'upload', 'excluded'))

    def _init_transport_setup(self):
        # Initializes transport and signalling server connection
        transport_setup.init(events_db=self._events_db,
                             connectivity_service=
                             self._sync._connectivity_service,
                             upload_handler=self._upload_handler,
                             ss_client=self._ss_client,
                             webshare_handler=self._webshare_handler,
                             cfg=self._cfg,
                             logged_in_signal=self.loggedIn,
                             get_sync_folder_size=self._get_sync_folder_size,
                             get_upload_speed=(
                                 lambda: self._avg_upload_speed),
                             get_download_speed=(
                                 lambda: self._avg_download_speed),
                             get_node_status=(
                                 lambda: self._ss_node_status),
                             get_server_event_ids=
                             self._sync.get_server_event_ids,
                             )

        transport_setup.signals.remote_action.connect(self._on_remote_action)
        transport_setup.signals.sharing_changed.connect(
            self._on_sharing_changed)
        transport_setup.signals.auth_failed.connect(self._on_auth_failed)
        transport_setup.signals.known_nodes_changed.connect(
            self._on_known_nodes_changed)
        transport_setup.signals.signalling_connected.connect(
            self._update_status)
        transport_setup.signals.signalling_connected.connect(
            self._set_logged_in)
        transport_setup.signals.signalling_disconnected.connect(
            self._update_status)
        transport_setup.signals.download_size_changed.connect(
            self._gui.download_size_changed)
        transport_setup.signals.upload_size_changed.connect(
            self._gui.upload_size_changed)
        transport_setup.signals.signalserver_address.connect(
            self._gui.signalserver_address)
        transport_setup.signals.new_notifications_count.connect(
            self._gui.new_notifications_count)

    def _init_shell_integration(self):
        # Init integration with OS shell extensions
        shell_integration.init(
            self._web_api, self._ss_client, self._sync,
            self._file_status_manager, self._cfg, self._tracker,
            get_shared_paths=self.get_shared_paths)

        # Connect signals and slots
        shell_integration_signals.open_link.connect(
            lambda p: self._gui.open_webfm())
        shell_integration_signals.download_link.connect(
            self._gui.download_link_handler, Qt.QueuedConnection)
        shell_integration_signals.share_path_failed.connect(
            self._gui.share_path_failed, Qt.QueuedConnection)
        shell_integration_signals.show.connect(self._gui.show)
        shell_integration_signals.wipe_internal.connect(
            lambda: wipe_internal(self._cfg.sync_directory))
        shell_integration_signals.status_subscribe.connect(
            self._file_status_manager.subscribe, Qt.QueuedConnection)
        shell_integration_signals.status_unsubscribe.connect(
            self._file_status_manager.unsubscribe, Qt.QueuedConnection)
        shell_integration_signals.show_collaboration_settings.connect(
            self._gui.show_collaboration_settings)

    def _connect_webshare_signals(self):
        self._webshare_handler.signals.share_download_complete.connect(
            lambda s: self._on_disk_usage_changed())
        self._webshare_handler.signals.share_download_busy.connect(
            lambda new_dir, ex_name: self.show_notification.emit(
                tr("Share will be downloaded to {0} "
                   "after {1} download complete")
                    .format(new_dir, ex_name),
                tr("Pvtbox")))
        self._webshare_handler.signals.share_unavailable.connect(
            lambda: self.show_notification.emit(
                tr("Share unavailable.\n"
                   "Perhaps access was closed or expired."),
                tr("Pvtbox")))
        self._webshare_handler.signals.share_download_cancelled.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'share', 'cancelled'))
        self._webshare_handler.signals.share_download_folder_deleted.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'share', 'deleted'))
        self._webshare_handler.signals.share_download_folder_excluded.connect(
            lambda name: self._on_share_upload_cancelled(
                name, 'share', 'excluded'))
        self._webshare_handler.signals.connected_nodes_changed.connect(
            lambda n: self._on_connected_nodes_changed(-1, n))

    def _connect_gui_signals(self):
        self._gui.gui_settings_changed.connect(self._on_gui_settings_changed)
        self._gui.exit_service.connect(Application.exit)
        self._gui.update_status.connect(self._update_status)
        self._gui.start_sync.connect(
            lambda: self._logged_in and self._start_sync())
        self._gui.stop_sync.connect(self._stop_sync)

        self._gui.received_download_link.connect(
            self._webshare_handler.download_by_url)
        self._gui.share_path_requested.connect(
            lambda path: shell_integration_signals.share_path.emit([path]))
        self._gui.is_saved_to_clipboard.connect(
            shell_integration_signals.is_saved_to_clipboard.emit)
        self._gui.gui_logged_in.connect(self.on_login_slot)
        self._gui.remote_action.connect(self._on_remote_action)
        self._gui.file_list_ready.connect(self._on_file_list_ready)

        self._gui.revert_downloads.connect(self._on_revert_downloads)
        self._gui.add_to_sync_folder.connect(self._on_add_to_sync_folder)

        self._gui.get_offline_dirs.connect(self._sync.get_offline_dirs)
        self._gui.set_offline_dirs.connect(self._set_offline_dirs)

    def _connect_tx_signals(self):
        # connect traffic info signals
        self._sync.signal_info_tx.connect(self._on_info_tx)
        self._sync.signal_info_rx.connect(self._on_info_rx)
        self._webshare_handler.signal_info_tx.connect(self._on_info_tx)
        self._webshare_handler.signal_info_rx.connect(self._on_info_rx)

    def on_gui_connected(self):
        self._connected.set()
        self._gui_connected.emit()

    def _on_gui_connected(self):
        if not self._connected.is_set():
            return

        logger.debug("Worker started")
        self._gui.init(self._logged_in,
                       self._cfg.get_config(),
                       self._cfg.get_filename())
        self._update_node_info_timer.start()
        self._check_disk_space_timer.start()
        self.send_initial_data()
        self._last_time_files_sent = 0
        self._file_list_to_send = None
        self._init_file_list()

        if 'copy' in self._args and self._args['copy']:
            paths = [FilePath(path).longpath for path in self._args['copy']]
            shell_integration_signals.copy_to_sync_dir.emit(paths)
            del self._args['copy']

    def send_initial_data(self):
        self._update_status()
        self._get_sync_folder_size(recalculate=True)
        self.upload_speed_changed.emit(self._avg_upload_speed)
        self.download_speed_changed.emit(self._avg_download_speed)

    def _init_events_db(self):
        from service.events_db import FileEventsDB, FileEventsDBError

        filename = ensure_unicode(
            get_bases_filename(self._cfg.sync_directory ,'events.db'))

        new_db_file = not op.exists(filename)
        if not new_db_file:
            # Database migration. It can be executed before opening db
            try:
                upgrade_db("events_db", db_filename=filename)
            except Exception as e:
                remove_file(filename)
                new_db_file = True
                logger.warning("Can't upgrade events db. "
                               "Reason: (%s) Creating...", e)
        self._events_db = FileEventsDB()
        try:
            self._events_db.open(filename=filename)
            self._events_db.show_compile_options()
        except FileEventsDBError as e:
            logger.error(
                "Failed to load filesystem events database (%s)", e)
            self.show_notification.emit(
                tr("Failed to load filesystem events database"),
                tr("Pvtbox"))
            self._on_critical_error(
                "Failed to load filesystem events database")
            return

        if new_db_file:
            try:
                stamp_db("events_db", db_filename=filename)
            except Exception as e:
                logger.error("Error stamping events db: %s", e)

    @benchmark
    def _init_file_list(self):
        self._emit_file_list_changed()

    def _start_file_list_sending(self):
        if not self._file_list_timer.isActive():
            self._file_list_timer.start()

    def _emit_file_list_changed(self):
        now = time.time()
        if now - self._last_time_files_sent <= self._files_send_timeout or \
                not self._file_list_ready:
            if not self._file_list_timer.isActive():
                self._file_list_timer.start()
            return

        file_list = self._sync.get_file_list()
        if file_list is None:
            return

        self.file_list_changed.emit(file_list)
        self._file_list_ready = False
        self._last_time_files_sent = now

    def _kill_timers(self):
        for timer in self._timers:
            if timer and timer.isActive():
                logger.debug("Stopping timer %s", timer)
                timer.stop()
        self._timers.clear()

    def exit(self):
        self._kill_timers()

        # Stop traffic info collector
        self._info_collector.stop()

        if self._ss_client:
            self._ss_client.ss_disconnect()

        if self._sync:
            self._sync.stop()

        if self._web_api and self._logged_in:
            self._logged_in = False
            try:
                self._web_api.logout()
            except Exception as e:
                logger.warning("Can't logout on exit. Reason: %s", e)

        if self._webshare_handler:
            self._webshare_handler.exit()

        # Send statistics
        self._send_final_statistics(logout=False)
        if self._tracker:
            self._tracker.exit()

        if self._sync:
            self._sync.exit()

        shell_integration.close()
        if self._service_server:
            self._service_server.close()

        self._upload_handler.exit()
        self._gui.service_exited()

    def _send_final_statistics(self, logout=True):
        try:
            if self._tracker:
                if logout:
                    self._tracker.session_logout()
                if self._network_speed_calculator:
                    tx_stat, rx_stat = \
                        self._network_speed_calculator.get_network_statistics()

                    self._tracker.session_end(
                        rx_ws=0,
                        tx_ws=0,
                        rx_wd=rx_stat[NETWORK_WEBRTC_DIRECT],
                        tx_wd=tx_stat[NETWORK_WEBRTC_DIRECT],
                        rx_wr=rx_stat[NETWORK_WEBRTC_RELAY],
                        tx_wr=tx_stat[NETWORK_WEBRTC_RELAY])
                    self._network_speed_calculator.clear_network_statistics()
                self._tracker.StopTrackingSession()
        except Exception:
            logger.warning("Can't send final statistics")

    def _on_sharing_changed(self, shared=None):
        if self._share_info_timer.isActive():
            self._share_info_timer.stop()

        if shared is not None:
            try:
                logger.verbose("Sharing changed. Uuids: %s", shared)
            except AttributeError:
                pass
        paths = self.get_shared_paths(shared, soft_paths=True)
        if paths is None:
            self._share_info_timer.start()
            return

        self.share_changed.emit(paths)

    def get_shared_paths(self, shared=None, soft_paths=False):
        if shared is None:
            shared = self._ss_client.get_sharing_info()

        try:
            with self._events_db.soft_lock(timeout_sec=0.1):
                with self._events_db.create_session(read_only=True) as \
                        session:
                    files = session.query(File) \
                        .filter(File.uuid.in_(shared.keys())) \
                        .all()
                    if len(files) < len(shared) \
                            and self._cfg.license_type != FREE_LICENSE:
                        logger.debug("Some shared uuids not found in db: %s",
                                     files)
                        return None

                    return [
                        FilePath(file.path if file.is_offline or soft_paths
                                 else file.path + FILE_LINK_SUFFIX)
                        for file in files]
        except EventsDbBusy:
            logger.debug("Events db busy")
            return None

    def logout(self, last_user_email=None):
        logger.info("logout, last_user_email: %s", last_user_email)

        if self._clean_old_events_timer.isActive():
            self._clean_old_events_timer.stop()
            self._last_event_uuid = None
        try:
            self._update_node_info_timer.timeout.disconnect(
                self._broadcast_node_status_if_needed)
        except RuntimeError:
            pass
        self._logged_in = False
        self._on_status_changed(STATUS_LOGGEDOUT)
        self._on_sync_status_changed()

        # Stop traffic info collector
        self._info_collector.stop()

        self._ss_client.ss_disconnect()

        if self._sync:
            self._sync.stop()
            if self._sync_status_timer.isActive():
                self._sync_status_timer.stop()
            self._sync.first_start = True

        self._web_api.logout()
        self._clear_sent_status()
        self._node_info.clear()
        self._node_info_sent.clear()

        self._cfg.set_settings({
            'user_hash': None,
            'last_user_email': last_user_email,
            'license_type': UNKNOWN_LICENSE,
        })

        if self._tracker:
            self._tracker.session_logout()

        self._was_logout = True
        self.loggedOut.emit()

    def clean_user_depended_data(self, force_apply_config=False):
        logger.debug("clean_user_depended_data")
        if self._sync:
            # self._sync.stop(cancel_downloads=True)
            data_dir = self._cfg.sync_directory
            if not op.exists(data_dir) or force_apply_config:
                self.create_sync_dir()
                self._sync.force_apply_config()
            else:
                self._sync.reset_all_collaboration_folder_icons()
                self._sync.fs.clean_storage()
                self._sync.clean_patches()
                self._sync.fs.clean_copies(with_files=False)
            self._sync.clear_remote_events_received()

        if self._webshare_handler:
            self._webshare_handler.stop(cancel_downloads=True)

        if self._events_db:
            self._events_db.clean()

    def _set_tracking_session_params(self):
        """
        Makes node_hash and user_hash to be used as tracking session parameters
        """

        self._tracker.set_session_node_id(
            self._cfg.get_setting('node_hash', 'unknown'))
        self._tracker.set_session_user_id(
            self._cfg.get_setting('user_hash', 'unknown'))

    def on_login_slot(self, login_data, new_user, download_backups, smart_sync,
                      check_consistency=True):
        if self._logged_in:
            return

        if self._is_restoring_sync_dir or \
                self._was_logout and check_consistency and \
                self._sync.check_if_sync_folder_is_removed():
            QTimer.singleShot(
                500, lambda: self.on_login_slot(
                    login_data, new_user, download_backups, smart_sync,
                    check_consistency=False))
            logger.debug("Sync folder removed, try login later")
            return

        if self._tracker:
            # Add statistics tracking session parameters
            self._set_tracking_session_params()

            # Add statistics event
            self._tracker.session_login(login_data['license_type'])

        self._auth_failed = False
        try:
            logger.verbose("Login data %s, last_user_email: %s",
                           login_data, self._cfg.last_user_email)
        except AttributeError:
            pass
        self._last_time_files_sent = 0
        self._file_list_to_send = None
        try:
            self._init_file_list()
        except Exception as e:
            logger.warning("can't init file list. Reason: %s", e)

        self._set_changed_settings(login_data, check_consistency,
                                   download_backups, smart_sync, new_user)
        # handle remote actions
        if 'remote_actions' in login_data and login_data['remote_actions']:
            remote_actions = login_data['remote_actions']
            if isinstance(remote_actions, collections.Iterable):
                for remote_action in remote_actions:
                    self._on_remote_action(remote_action)
                return

        # Add statistics tracking session parameters
        if self._tracker:
            self._set_tracking_session_params()
            if login_data['license_type'] == FREE_LICENSE:
                self._cfg.set_settings({'send_statistics': True})
                self._tracker.start_sending()

        self._login_data = login_data
        self._on_sync_status_changed(STATUS_DISCONNECTED)
        self._sync.start()

        if 'last_event_uuid' in login_data:
            self._last_event_uuid = login_data['last_event_uuid']
            self._clean_old_events_timer.start()

        self._update_node_info_timer.timeout.connect(
            self._broadcast_node_status_if_needed)

        if self._ss_client and self._ss_client.is_connected():
            self._logged_in = True

        # Start traffic info collector
        self._info_collector.start()

    def _set_changed_settings(self, login_data, check_consistency,
                              download_backups, smart_sync, new_user):
        user_email = login_data['user_email']
        if user_email != self._cfg.last_user_email or new_user \
                or not check_consistency:
            self.clean_user_depended_data(not check_consistency)
            changed_settings = {
                'download_limit': 0,
                'upload_limit': 0,
                'excluded_dirs': [],
            }
        else:
            changed_settings = {}
        changed_settings.update(dict(
            last_user_email=user_email,
            user_email=user_email,
            user_password_hash=login_data['password_hash'],
            user_hash=login_data['user_hash'],
            node_hash=login_data['node_hash'],
            download_backups=download_backups,
            smart_sync=smart_sync,
        ))

        self._cfg.set_settings(changed_settings)
        self._gui.set_config(self._cfg.get_config())

    def _complete_login(self):
        if self._logged_in:
            return

        self._logged_in = True
        self.loggedIn.emit(self._login_data)
        self._sync_status_timer.start()

        if 'download_link' in self._args and self._args['download_link']:
            shell_integration_signals.download_link.emit(
                self._args['download_link'])
            del self._args['download_link']
        if 'offline_on' in self._args and self._args['offline_on']:
            shell_integration_signals.offline_paths.emit(
                [self._args['offline_on']], True)
            del self._args['offline_on']

    def _on__clean_old_events_timeout(self):
        if not self._last_event_uuid:
            return

        self._sync.save_last_event_uuid(self._last_event_uuid)
        self._last_event_uuid = None

    def _apply_rate_limits(self):
        download_limiter = self._sync.apply_download_limit(
            self._cfg.download_limit)
        upload_limiter = self._sync.apply_upload_limit(
            self._cfg.upload_limit)
        self._upload_handler.set_download_limiter(download_limiter)
        self._webshare_handler.set_download_limiter(download_limiter)

    def _apply_send_statistic(self):
        if self._cfg.send_statistics:
            self._tracker.start_sending()
        else:
            self._tracker.stop_sending()

    def _set_language(self):
        self._gui.new_language(self._cfg.lang)

    def _on_settings_changing(self, settings):
        # with wait_signal(self.settings_changing_finished):
        self.settings_changed.emit(settings)

    def on_settings_changed(self, changed_params):
        def on_user_password_hash_changed():
            if do_logout:
                self.logout(
                    last_user_email=self._cfg.user_email)

        if 'user_password_hash' in changed_params:
            password_hash = changed_params['user_password_hash'].new_value
            do_logout = not password_hash and password_hash is not None and \
                        'user_email' not in changed_params.keys()
        else:
            do_logout = False

        apply_settings_actions = dict(
            download_limit=self._apply_rate_limits,
            upload_limit=self._apply_rate_limits,
            fs_events_processing_delay=self._sync.apply_config,
            fs_events_processing_period=self._sync.apply_config,
            fs_folder_timeout=self._sync.apply_config,
            sync_directory=self._sync.apply_config,
            conflict_file_suffix=self._sync.apply_config,
            send_statistics=self._apply_send_statistic,
            lang=self._set_language,
            user_password_hash=on_user_password_hash_changed,
            excluded_dirs=self._sync.set_excluded_dirs,
            download_backups=self._sync.download_backups_changed,
            smart_sync=self._on_smart_sync_changed,
            host=self._web_api.set_uris(),
            tracking_address=self._apply_tracking_address,
        )

        if 'excluded_dirs' in changed_params.keys():
            self._on_sync_status_changed(
                status=STATUS_DISCONNECTED, substatus=SUBSTATUS_APPLY)

        actions = set()
        for param in changed_params.keys():
            if param in apply_settings_actions:
                actions.add(apply_settings_actions[param])

        for action in actions:
            action()

        # self.settings_changing_finished.emit()
        if not self._gui_changed_settings:
            try:
                logger.verbose("setting config %s", self._cfg.get_config())
            except AttributeError:
                pass
            self._gui.set_config(self._cfg.get_config())
        if do_logout:
            self._cfg.set_settings({'user_password_hash': None})
        self._gui_changed_settings = not do_logout

    def _on_critical_error(self, exception):
        logger.critical("%s", exception)
        Application.exit()

    def _on_disk_usage_changed(self):
        self._disk_usage_changed = True

    def _get_sync_folder_size(self, recalculate=False):
        if not self._dir_size_calculating and \
                (recalculate or self._sync_folder_size is None):
            self._get_dir_size(self._cfg.sync_directory)

        if self._sync_folder_size is None:
            self._sync_folder_size = self._cfg.sync_dir_size

        if self._sync_folder_size != self._sync_dir_size_sent:
            try:
                self._gui.sync_dir_size_changed(float(self._sync_folder_size))
                self._sync_dir_size_sent = self._sync_folder_size
                self._cfg.set_settings(
                    {'sync_dir_size': self._sync_folder_size})
            except Exception as e:
                logger.warning("Can't send sync dir size %s (%s)",
                               self._sync_folder_size, e)
        return self._sync_folder_size

    @qt_run
    def _get_dir_size(self, path):
        self._dir_size_calculating = True
        try:
            self._sync_folder_size = get_dir_size(path)
            # send new value to gui
            self._get_sync_folder_size()
        except Exception:
            self._possibly_sync_folder_is_removed.emit()
        finally:
            self._dir_size_calculating = False

    def _on_sync_folder_removed(self, sync_consistent, cfg_consistent):
        if self._is_restoring_sync_dir or not self._connected.is_set:
            return
        with self._restoring_sync_dir_lock:
            self._is_restoring_sync_dir = True

        if self._sync and self._logged_in:
            self._sync.stop()
        self._sync_folder_restored = False
        if not cfg_consistent:
            self._restore_cfg_folder()

        if not op.isdir(self._cfg.sync_directory):
            self.show_lost_folder_dialog.emit(
                self._cfg.sync_directory,
                lambda: QTimer.singleShot(0, self._restore_folder),
                lambda: QTimer.singleShot(1, self._finish_restore_folder))
        else:
            QTimer.singleShot(1, self._finish_restore_folder)

    def _restore_cfg_folder(self):
        get_cfg_dir(create=True)
        self._cfg.sync()

    def _restore_folder(self):
        if self._ss_client.is_connected():
            self._ss_client.ss_disconnect()
            self._on_sync_status_changed(status=STATUS_DISCONNECTED)
        try:
            self._events_db.clean()
        except Exception:
            logger.warning("No events db, initializing")
            self._init_events_db()
            if self._sync:
                self._sync.set_db(self._events_db)

        if not self.create_sync_dir():
            logger.debug("Creating sync directory in default location...")
            default_dir = FilePath(get_data_dir())
            self._cfg.set_settings({"sync_directory": default_dir})
            self.create_sync_dir()
            self._gui.set_config(self._cfg.get_config())

        self._sync_folder_restored = True

    def _finish_restore_folder(self):
        if not self._logged_in and not self._was_logout:
            self._gui.restart_me()
            return

        try:
            if not self._sync_folder_restored:
                sync_cons, cfg_cons = self._sync.check_consistency()
                if not sync_cons:
                    try:
                        remove_dir(self._cfg.sync_directory)
                    except OSError:
                        pass
                    self._restore_folder()
            if self._logged_in:
                self._sync.force_apply_config()
                self._restart_tracker()
                self._apply_rate_limits()
                self._sync.first_start = True
                self._sync.start()
                self._ss_client.reconnect()
                create_shortcuts(self._cfg.sync_directory)
        finally:
            with self._restoring_sync_dir_lock:
                self._is_restoring_sync_dir = False

    def _restart_tracker(self):
        if self._tracker:
            self._tracker.StopTrackingSession()

            self._tracker.start.emit()
            # Add statistics tracking session parameters
            self._set_tracking_session_params()
            self._apply_send_statistic()

    def _on_status_changed(self, status, substatus=None, l=0, r=0, fs=0, ee=0):
        ss_statuses = {STATUS_WAIT: SS_STATUS_SYNCED,
                       STATUS_PAUSE: SS_STATUS_PAUSED,
                       STATUS_IN_WORK: SS_STATUS_SYNCING,
                       STATUS_INDEXING: SS_STATUS_INDEXING,
                       STATUS_LOGGEDOUT: SS_STATUS_LOGGEDOUT,
                       STATUS_DISCONNECTED: SS_STATUS_LOGGEDOUT,
                       }
        new_status = ss_statuses.get(status, SS_STATUS_SYNCED)
        if new_status != self._ss_node_status:
            self._ss_node_status = new_status
            self._file_status_manager.on_global_status(self._ss_node_status)
            if new_status in (SS_STATUS_SYNCING, SS_STATUS_INDEXING,
                              SS_STATUS_LOGGEDOUT):
                self._broadcast_node_status_if_needed()

    def _broadcast_node_status_if_needed(self):
        if self._disk_usage_changed:
            self._get_sync_folder_size(recalculate=True)

        speed_changed = self._avg_download_speed != self._download_speed_sent \
            or self._avg_upload_speed != self._upload_speed_sent

        if (self._ss_node_status != self._ss_sent_node_status or
                self._disk_usage_changed or speed_changed) and \
                self._ss_client.is_connected():
            self._ss_sent_node_status = self._ss_node_status
            self._download_speed_sent = self._avg_download_speed
            self._upload_speed_sent = self._avg_upload_speed
            self._disk_usage_changed = False
            self._ss_client.update_node_status()

        # send session/info statistics
        if self._tracker and self._network_speed_calculator:
            tx_stat, rx_stat = \
                self._network_speed_calculator.get_network_statistics()
            self._tracker.session_info(
                rx_ws=0,
                tx_ws=0,
                rx_wd=rx_stat[NETWORK_WEBRTC_DIRECT],
                tx_wd=tx_stat[NETWORK_WEBRTC_DIRECT],
                rx_wr=rx_stat[NETWORK_WEBRTC_RELAY],
                tx_wr=tx_stat[NETWORK_WEBRTC_RELAY])

    def _on_remote_action(self, remote_action_data):
        logger.info("Remote action has beed initiated: '%s'",
                    remote_action_data)
        try:
            remote_action_type = remote_action_data["action_type"]
            remote_action_uuid = remote_action_data["action_uuid"]
            if remote_action_type == "logout":
                self._on_remote_action_logout(remote_action_uuid)
            elif remote_action_type == "wipe":
                self._on_remote_action_wipe(remote_action_uuid)
            elif remote_action_type == "credentials":
                remote_action_user_hash = \
                    remote_action_data["action_data"]["user_hash"]
                self._on_remote_action_credentials(remote_action_uuid,
                                                   remote_action_user_hash)
            else:
                logger.warning("Unknown remote action type: '%s'",
                               remote_action_type)
        except KeyError as e:
            logger.error("Invalid remote action data. Error: %s", e)

    def _on_remote_action_logout(self, remote_action_uuid):
        logger.info("Handle remote action 'logout' ..")
        self._logged_in = False

        # call web_api to notify server that remote action has been handled
        self._inform_remote_action_done(remote_action_uuid)

        last_user_email = self._cfg.user_email
        self._cfg.set_settings({
            'last_user_email': last_user_email,
            'user_password_hash': '',
        })

        if self._gui:
            self.show_login_page.emit(False, True)

            self.show_notification.emit(
                tr("Logged out because of user remote action"),
                tr("Pvtbox"))

    def _on_remote_action_wipe(self, remote_action_uuid):
        logger.info("Handle remote action 'wipe' ..")
        files_to_hold = list(map(get_cfg_filename, self.FILES_TO_HOLD))
        if self._gui:
            self._gui.is_wiping_all()

        self._logged_in = False

        # stop signaling client and transport
        self._ss_client.ss_disconnect()

        # stop sync
        if self._sync:
            self._sync.stop()

        self._send_final_statistics()
        self._tracker = None

        # wipe files
        data_dir = self._cfg.sync_directory
        self._wipe_dir(data_dir, files_to_hold)
        config_dir = get_cfg_dir()
        self._wipe_dir(config_dir, files_to_hold)

        # call web_api to notify server that remote action has been handled
        if remote_action_uuid:
            self._inform_remote_action_done(remote_action_uuid)

        disable_file_logging(logger)
        copies_logger = logging.getLogger('copies_logger')
        disable_file_logging(copies_logger, use_root=False)
        # remove dirs
        shutil.rmtree(data_dir, ignore_errors=True)

        # create new config to keep sync directory path
        get_cfg_dir(create=True)
        get_patches_dir(data_dir, True)
        self._cfg = config.load_config()
        self._cfg.set_settings({'sync_directory': data_dir})

        if self._gui:
            self._gui.wiped_all()
            if remote_action_uuid:
                self.show_notification.emit(
                    tr("Wiped information because of user remote action"),
                    tr("Pvtbox"))
        else:
            Application.exit()

    def _wipe_file(self, filename, filesize):
        logger.debug("wipe file '%s', size '%s'", filename, filesize)
        rest_size = filesize
        block_size = 2 * 1024 * 1024  # 2MB
        try:
            with open(filename, "r+b") as f:
                f.seek(0, 0)
                while True:
                    m = block_size if rest_size > block_size else rest_size
                    f.write(b"\x00" * m)
                    rest_size = rest_size - m
                    if rest_size <= 0:
                        break
                f.flush()
            logger.debug("File '%s' has been wiped successfully", filename)
        except Exception as e:
            logger.error("Error occured while wiping file '%s'. "
                         "Error '%s'", filename, e)

    def _wipe_dir(self, dirname, files_to_hold):
        sizes_scale = [
            1024,  # 1 KB
            100 * 1024,  # 100 KB
            1024 * 1024,  # 1 MB
            100 * 1024 * 1024,  # 100 MB
            1024 * 1024 * 1024,  # 1 GB
        ]
        sizes = iter(sizes_scale)
        max_size = 0
        while True:
            if max_size is None:
                break
            try:
                min_size = max_size
                max_size = next(sizes)
                logger.debug("min_size: %s, max_size: %s",
                             min_size, max_size)
            except StopIteration:
                max_size = None
            for root, dirs, files in os.walk(dirname):
                for f in files:
                    _, ext = op.splitext(f)
                    filename = FilePath(op.join(root, f)).longpath
                    if filename in files_to_hold or ext == '.log':
                        continue
                    try:
                        filesize = op.getsize(filename)
                    except OSError as e:
                        logger.error("Error occured while getting "
                                     "file size: '%s'", e)
                        continue
                    if max_size is None or (min_size <= filesize < max_size):
                        self._wipe_file(filename, filesize)
        logger.debug("Dir '%s' has been wiped", dirname)

    def _on_remote_action_credentials(self, remote_action_uuid,
                                      remote_action_user_hash):
        self._cfg.set_settings({'user_hash': remote_action_user_hash})
        self._gui.set_config(self._cfg.get_config())
        self._inform_remote_action_done(remote_action_uuid)
        if self._ss_client:
            self._ss_client.ss_disconnect()
        self._sync.pause()
        self._sync.first_start = True
        self._logged_in = False
        self._gui.autologin(is_silent=True)

    def _inform_remote_action_done(self, remote_action_uuid):
        for i in range(5):
            result = self._web_api.remote_action_done(
                remote_action_uuid=remote_action_uuid)
            logger.debug("result of call 'remote_action_done' "
                         "for remote_action '%s': %s",
                         remote_action_uuid, result)
            try:
                if result['result'] == "success":
                    break
            except Exception:
                pass

    def _set_ready_to_clean_copies(self):
        self._ready_to_clean_copies = True
        self._try_clean_copies()

    def _try_clean_copies(self):
        if not self._ready_to_clean_copies:
            return

        logger.debug("Deleting unnecessary copies...")
        if self._sync.clean_unnecessary_copies():
            logger.debug("Unnecessary copies deleted")
            self._ready_to_clean_copies = False
            self._ready_to_clean_copies_timer.start(60 * 60 * 1000)

    def _on_disk_space_check(self):

        def space_limits_check(directory):
            space = get_free_space_mb(directory)
            space_red = space < DISK_LOW_RED
            space_orange = space < DISK_LOW_ORANGE and not space_red
            return space, space_orange, space_red, space_orange or space_red

        cfg_dir = FilePath(get_cfg_dir())
        data_dir = FilePath(self._cfg.sync_directory)
        cfg_space, cfg_orange, cfg_red, cfg_low = space_limits_check(cfg_dir)
        data_space, data_orange, data_red, data_low = space_limits_check(
            data_dir)

        if cfg_low:
            # clear old logs and leave only current log file
            set_economode(logger)
            copies_logger = logging.getLogger('copies_logger')
            set_economode(copies_logger, use_root=False)
            cfg_space, cfg_orange, cfg_red, cfg_low = space_limits_check(
                cfg_dir)
            data_space, data_orange, data_red, data_low = space_limits_check(
                data_dir)

        logger.debug("Disk free space. Cfg dir: %s, Data dir: %s",
                     cfg_space, data_space)

        cfg_space = get_free_space(cfg_dir)
        data_space = get_free_space(data_dir)
        same_volume = cfg_space == data_space

        if self._disk_space_low or \
                (cfg_low or data_low) ^ self._disk_space_low:
            # disk space status changed
            cfg_drive = get_drive_name(cfg_dir)
            data_drive = get_drive_name(data_dir)
            self._disk_space_low = cfg_low or data_low
            self.disk_space_low_status.emit(
                self._disk_space_low, cfg_orange, cfg_red,
                data_orange, data_red, same_volume, cfg_drive, data_drive,
                str(cfg_space), str(data_space))

        if cfg_red or data_red:
            self._check_disk_space_timer.start(0.5 * 60 * 1000)
        elif self._disk_space_low:
            self._check_disk_space_timer.start(1 * 60 * 1000)
        else:
            self._check_disk_space_timer.start()

    def _on_db_or_disk_full(self):
        def on_ok_cb():
            if self._gui:
                self._gui.exit_request()
            else:
                self.exit()
        if not self._disk_full_request_pending:
            self._disk_full_request_pending = True
            msg = tr('Disk is full. Application exits. Please clean disk')
            self.show_request_to_user.emit(
                msg, [(tr("Ok"), on_ok_cb)], '', 0, False, '')

    def _on_auth_failed(self):
        if self._auth_failed:
            return

        self._auth_failed = True
        self._logged_in = False
        self._gui.autologin(is_silent=True)

    def _on_sync_stopped(self, cancel_downloads=False):
        if self._webshare_handler:
            self._webshare_handler.stop(cancel_downloads)
        if self._upload_handler:
            self._upload_handler.stop(cancel_downloads)

    def _on_sync_started(self):
        self._webshare_handler.start()
        self._upload_handler.start()

    def _on_share_upload_cancelled(self, name, source, reason):
        assert source in ('share', 'upload'), \
            "Source has to be share or upload"
        assert reason in ('cancelled', 'deleted', 'excluded'), \
            "Reason has to be cancelled or deleted or excluded"

        msg_start = tr("Share download") if source == 'share' else tr("Upload")
        msg_reason = tr("cancelled") \
            if reason == 'cancelled' \
            else tr("failed because folder was deleted") \
            if reason == 'deleted' \
            else tr("failed because folder was excluded from sync")
        name = op.basename(name)
        msg = tr("{0} of {1} {2}").format(msg_start, name, msg_reason)
        self.show_notification.emit(msg, tr("Pvtbox"))

    def _on_upload_not_synced(self, name):
        name = op.basename(name)
        msg = tr("Upload of {0} will start after sync completed").format(name)
        self.show_notification.emit(msg, tr("Pvtbox"))

    def _on_gui_settings_changed(self, settings):
        self._gui_changed_settings = True
        self._cfg.set_settings(settings)

    def _start_sync(self):
        self._file_status_manager.on_sync_resuming()
        self._sync.start()

    def _stop_sync(self):
        self._on_status_changed(STATUS_PAUSE)
        self._sync.pause()

    def _on_sync_status_changed(self, status=STATUS_WAIT,
                                substatus=SUBSTATUS_SYNC,
                                local_events_count=0, remote_events_count=0,
                                fs_events_count=0, events_erased=0):
        logger.debug("_on_sync_status_changed. "
                     "status %s, remote_events_count %s",
                     status, remote_events_count)
        if not self._logged_in:
            status = STATUS_INIT
            substatus = SUBSTATUS_SYNC
        elif not self._ss_client or not self._ss_client.is_connected():
            self._ss_node_status = SS_STATUS_LOGGEDOUT
            self._file_status_manager.on_global_status(self._ss_node_status)
            status = STATUS_DISCONNECTED
            substatus = SUBSTATUS_SYNC

        if status != self._sync_status_sent or \
                substatus != self._sync_substatus_sent or \
                local_events_count != self._local_count_sent or \
                remote_events_count != self._remote_count_sent or \
                fs_events_count != self._fs_count_sent or \
                events_erased != self._events_erased_sent:
            self._sync_status_sent = status
            self._sync_substatus_sent = substatus
            self._local_count_sent = local_events_count
            self._remote_count_sent = remote_events_count
            self._fs_count_sent = fs_events_count
            self._events_erased_sent = events_erased
            self._gui.sync_status_changed(
                status, substatus, local_events_count, remote_events_count,
                fs_events_count, events_erased)

            if status == STATUS_WAIT:
                if self._disk_usage_changed:
                    self._get_sync_folder_size(recalculate=True)

                self._emit_file_list_changed()
                self._sync.process_offline_changes(status, substatus,
                                                   local_events_count,
                                                   remote_events_count)
                self._sync.check_long_paths(status, substatus,
                                            local_events_count,
                                            remote_events_count)
                self._try_clean_copies()

    def _update_status(self):
        self._clear_sent_status()
        self._sync.update_status()

    def _clear_sent_status(self):
        self._sync_status_sent = None
        self._sync_substatus_sent = None
        self._local_count_sent = 0
        self._remote_count_sent = 0
        self._fs_count_sent = 0
        self._events_erased_sent = 0

    def _on_file_list_ready(self):
        self._file_list_ready = True

    def _on_license_alert(self, license_name):
        def on_upgrade():
            webbrowser.open(
                GET_PRO_URI.format(self._cfg.host), new=0, autoraise=True)

        buttons = [(tr('Upgrade'), on_upgrade),
                   (tr('OK'), lambda: None)]
        msg = tr('Pvtbox license is free...\n\n'
                 'Syncing across devices disabled\n'
                 'Please upgrade your license.')
        self.show_request_to_user.emit(msg, buttons, '', 1, False, '')

    def _on_get_sync_status(self):
        self._sync.calculate_processing_events_count()
        self._send_node_info()

    def _on_check_connectivity_alive(self):
        connectivity_holders = (self._sync, self._webshare_handler)
        for i in range(len(connectivity_holders)):
            if connectivity_holders[i].is_connectivity_alive():
                logger.debug("Connectivity %s is alive", i)
                self._connectivity_restarted[i] = False
            else:
                logger.warning("Connectivity %s is dead", i)
                if not self._connectivity_restarted[i]:
                    logger.debug("Restarting connectivity %s", i)
                    connectivity_holders[i].restart_connectivity()
                    self._connectivity_restarted[i] = True
                else:
                    logger.debug("Restarting service")
                    self._gui.restart_me()
                    return

    def _on_known_nodes_changed(self, node_info):
        self._node_info = node_info

    def _send_node_info(self):
        if self._node_info != self._node_info_sent:
            self._gui.nodes_info(self._node_info)
            self._node_info_sent = {id: self._node_info[id].copy()
                                    for id in self._node_info}

    def _on_info_tx(self, info_tx):
        self._info_collector.add_info_tx(info_tx)

    def _on_info_rx(self, info_rx):
        self._info_collector.add_info_rx(info_rx)

    def _set_logged_in(self):
        if self._auth_failed:
            self._logged_in = True
            self._auth_failed = False

    def _on_revert_downloads(self, reverted_files, reverted_patches,
                             reverted_shares):
        if reverted_shares:
            self._webshare_handler.cancel_files_downloads(reverted_shares)

        if reverted_files or reverted_patches:
            self._sync.revert_hanged_tasks(reverted_files, reverted_patches)

    def _on_connected_nodes_changed(self, nodes0, nodes1):
        if nodes1 < 0:
            self._connected_nodes[0] = nodes0
        elif nodes0 < 0:
            self._connected_nodes[1] = nodes1
        self._gui.connected_nodes_changed(sum(self._connected_nodes))

    def _on_add_to_sync_folder(self, paths):
        shell_integration_signals.copy_to_sync_dir.emit(paths)

    def _wait_components_exit(self):
        self._components_to_exit -= 1
        if not self._components_to_exit:
            self.exited.emit()

    def _apply_tracking_address(self):
        if self._tracker:
            self._tracker.set_tracking_address(self._cfg.tracking_address)

    def _on_smart_sync_changed(self):
        shell_integration_signals.smart_sync_changed.emit()
        self._sync.smart_sync_changed()

    def _set_offline_dirs(self, offline_dirs, online_dirs):
        shell_integration_signals.offline_paths.emit(
            offline_dirs, True, False)  # not is_recursive
        shell_integration_signals.offline_paths.emit(
            online_dirs, False, False)  # not is_recursive
