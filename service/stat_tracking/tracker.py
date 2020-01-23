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
import os
import platform
import time
from os.path import exists, getsize

from service.stat_tracking import tracking as statistics_tracking
from PySide2.QtCore import QObject, Signal, Qt, QTimer

from __version import __version__
from common.utils import get_platform, get_os_version, \
    get_linux_distro_name_version, get_bases_filename
from common.constants import UNKNOWN_LICENSE

# Setup logging
logger = logging.getLogger(__name__)


def get_user_agent():
    """
    Returns platform-specific user agent string (to be used for statistics
    reporting

    @return User agent info [str]
    """

    params = []
    # Add program name and version
    params.append(('Pvtbox', __version__))

    os_name = get_platform()
    os_version = get_os_version()
    if not os_version:
        os_version = 'unknown'

    # Add OS name and version
    params.append((os_name, os_version))

    if os_name == 'Linux':
        # Add linux distribution name and version
        params.append(get_linux_distro_name_version())
        # Add desktop enviroment info (if any)
        de_info = os.getenv('DESKTOP_SESSION')
        if de_info:
            params.append(('DE', de_info))

    # Add node type and machine info
    params.append(('desktop', platform.machine()))

    ua = ' '.join(map(lambda p: '/'.join(p), params))
    logger.debug(
        "Node's UA is '%s'", ua)

    return ua


class Tracker(QObject):
    INTERNAL_ERROR = 1
    INCORRECT_SERVER_RESPONSE = 2
    INCORRECT_PATH = 3
    NOT_IN_SYNC = 4

    start = Signal()
    exited = Signal()
    _add_event = Signal(str, dict)
    _start_sending = Signal()
    _stop_sending = Signal()
    _stop_session = Signal()

    def __init__(self, db_name, root='',
                 address=None):
        QObject.__init__(self)

        self._connect_slots()
        self._started = False
        self._db_name = db_name
        self.tracking = None
        self.session_params = dict()
        self._app_start_ts = self._now()
        self._session_login_start_ts = None
        self._session_signup_start_ts = None
        self._license_type = UNKNOWN_LICENSE
        self._db_file = ''
        self._session_info = {"_rx_ws": 0, "_rx_wd": 0, "_rx_wr": 0,
                              "_tx_ws": 0, "_tx_wd": 0, "_tx_wr": 0}
        self._address = address

        self._root = root

        self._user_agent = get_user_agent()
        logger.debug("User agent %s", self._user_agent)

        self._exiting = False

    def _connect_slots(self):
        self.start.connect(self._on_start, Qt.QueuedConnection)
        self._add_event.connect(self._on_add_event, Qt.QueuedConnection)
        self._start_sending.connect(
            self._on_start_sending, Qt.QueuedConnection)
        self._stop_sending.connect(self._on_stop_sending, Qt.QueuedConnection)
        self._stop_session.connect(self._on_stop_session, Qt.QueuedConnection)

    def _on_start(self):
        if not self._address:
            return

        stats_db = get_bases_filename(self._root, self._db_name)
        self._init(stats_db, self._address, 2)

    def StopTrackingSession(self):
        if not self._address:
            return

        self._stop_session.emit()

    def _on_stop_session(self):
        if self.tracking:
            self.tracking.StopTrackingSession()

    def _format_ts(self, t):
        """
        Formats timestamp to fixed decimal precision

        @param t Timestamp to be formatted [float]
        @return Formatted timestamp [str]
        """

        return '{:.2f}'.format(t)

    def _format_bool(self, b):
        return str(int(b))

    def _now(self):
        """
        Returns current timestamp as seconds since the Epoch
        @return Timestamp [float]
        """

        return time.time()

    def _init(self, data_base_path, tracking_server_address,
              version_extension, session_params=()):
        """
        Initializes C++ extension

        @param data_base_path Filename to store stats DB [unicode]
        @param tracking_server_address URL of server to send stats to [str]
        @param version_extension Protocol version [int]
        @param session_params
            Session parameters in the form {name: value} [dict]
        """

        self._db_file = data_base_path
        if not session_params:
            session_params = dict()
        logger.debug("Initializing usage statistics tracker...")
        self.tracking = statistics_tracking.Tracking(
            self, data_base_path, tracking_server_address, self.on_session_stopped)
        self._new_session(self._user_agent, version_extension, session_params)
        self._started = True

    def _on_add_event(self, event_name, event_params={}):
        """
        Adds event with the name and parameters given into local statistics
        events DB

        @param event_name Name of the event [str]
        @param event_params Event parameters in the form {name: value} [dict]
        """

        logger.debug(
            "Adding statistics event '%s' data %s", event_name, event_params)
        if not self._address:
            return

        if not self._started:
            logger.warning("Statistic event received before tracking started")
            QTimer.singleShot(1000, lambda: self._add_event.emit(
                event_name, event_params))
            return

        assert self.tracking
        event = statistics_tracking.Event(event_name)
        event_params.update(self.session_params)
        for key in event_params:
            try:
                event.AddParameter(key, '{}'.format(event_params[key]))
            except Exception as e:
                logger.warning("Can't add parameter %s. reason %s",
                               event_params[key], e)
        self.tracking.AddEvent(event)

    def set_tracking_address(self, new_address):
        self.StopTrackingSession()
        self.start.emit()
        self._address = new_address

    def start_sending(self):
        if not self._address:
            return

        self._start_sending.emit()

    def _on_start_sending(self):
        if not self._started:
            self._start_sending.emit()
            return
        logger.info(
            "Starting statistics sending...")
        self.tracking.StartSendingEvents()

    def stop_sending(self):
        if not self._address:
            return

        self._stop_sending.emit()

    def _on_stop_sending(self):
        logger.info(
            "Stopping statistics sending...")
        self.tracking.StopSendingEvents()

    def exit(self):
        self._exiting = True
        try:
            self._add_event.disconnect(self._on_add_event)
        except Exception as e:
            logger.warning("Can't disconnect adding events. Reason: %s", e)
        self.StopTrackingSession()

    def on_session_stopped(self):
        if self._exiting:
            self._exiting = False
            self.exited.emit()

    def _set_session_param(self, name, value):
        logger.debug(
            "Set tracking session param '%s' to '%s'", name, value)
        self.session_params[name] = value

    def set_session_node_id(self, node_id):
        """
        Adds some node ID string to tracking session parameters

        @param node_id String identificating node [str]
        """

        self._set_session_param('nid', node_id)

    def set_session_user_id(self, user_id):
        """
        Adds some user ID string to tracking session parameters

        @param user_id String identificating user [str]
        """

        self._set_session_param('uid', user_id)

    def _new_session(self, user_agent, version_extension, params):
        logger.debug(
            "Starting new statistics collecting session...")
        self.session_params = params
        self.tracking.StartTrackingSession(user_agent, int(version_extension))
        self.session_start()

    def monitor_start(self, files_in_sync, dirs_in_sync,
                      files_created, files_modified,
                      files_moved, files_deleted,
                      dirs_created, dirs_deleted,
                      sync_size, start_duration):
        self._add_event.emit(
            'monitor/start',
            dict(_f=files_in_sync, _d=dirs_in_sync,
                 _f_cr=files_created, _f_mod=files_modified,
                 _f_mv=files_moved, _f_del=files_deleted,
                 _d_cr=dirs_created, _d_del=dirs_deleted,
                 _size=sync_size,
                 _time=self._format_ts(start_duration)))

    def monitor_stop(self, files_in_sync, dirs_in_sync,
                     files_created, files_modified,
                     files_moved, files_deleted,
                     dirs_created, dirs_moved, dirs_deleted,
                     work_duration, files_ignored_by_checksum):
        self._add_event.emit(
            'monitor/stop',
            dict(_f=files_in_sync, _d=dirs_in_sync,
                 _f_cr=files_created, _f_mod=files_modified,
                 _f_mv=files_moved, _f_del=files_deleted,
                 _d_cr=dirs_created, _d_mv=dirs_moved,
                 _d_del=dirs_deleted,
                 _time=self._format_ts(work_duration),
                 _f_ign=files_ignored_by_checksum))

    def monitor_patch_create(self, file_size, patch_size, duration):
        self._add_event.emit(
            'monitor/patch/create',
            dict(_f_size=file_size, _p_size=patch_size,
                 _time=self._format_ts(duration)))

    def monitor_patch_accept(self, file_size, patch_size, duration, success):
        self._add_event.emit(
            'monitor/patch/accept',
            dict(_f_size=file_size, _p_size=patch_size,
                 _time=self._format_ts(duration),
                 _suc=self._format_bool(success)))

    def sync_start(self, pending_events):
        self._add_event.emit(
            'sync/start',
            dict(_e_pend=pending_events,
                 _roots=1))

    def sync_stop(self, received_events, produced_events,
                  procesed_events, errors):
        params = dict(
            _e_recv=received_events,
            _e_prod=produced_events,
            _e_proc=procesed_events,
            _e_err=errors
        )
        self._add_event.emit('sync/stop', params)

    def sync_event(self, event_id, retries, processed, processing_time,
                   producted_by_node):
        self._add_event.emit(
            'sync/event',
            dict(_id=event_id, _retry=retries,
                 _suc=self._format_bool(processed),
                 _time=self._format_ts(processing_time),
                 _prod=self._format_bool(producted_by_node)))

    def download_start(self, id, size):
        self._add_event.emit(
            'download/start',
            dict(_id=id, _size=size))

    def download_end(self, id, duration,
                     websockets_bytes,
                     webrtc_direct_bytes, webrtc_relay_bytes,
                     chunks, chunks_reloaded,
                     nodes):
        params = dict(
            _id=id, _time=self._format_ts(duration), _rx_ws=websockets_bytes,
            _rx_wd=webrtc_direct_bytes, _rx_wr=webrtc_relay_bytes,
            _ch=chunks, _ch_err=chunks_reloaded, _n=nodes)
        self._add_event.emit(
            'download/end', params)

    def download_error(self, id, duration,
                       websockets_bytes,
                       webrtc_direct_bytes,
                       webrtc_relay_bytes,
                       chunks, chunks_reloaded,
                       nodes):
        self._add_event.emit(
            'download/error',
            dict(_id=id, _time=self._format_ts(duration),
                 _rx_ws=websockets_bytes,
                 _rx_wd=webrtc_direct_bytes, _rx_wr=webrtc_relay_bytes,
                 _ch=chunks, _ch_err=chunks_reloaded, _n=nodes))

    def http_download(self, id, size, duration, checksum_valid):
        self._add_event.emit(
            'http/download',
            dict(_id=id, _size=size,
                 _time=self._format_ts(duration),
                 _valid=self._format_bool(checksum_valid)))

    def http_error(self, id):
        self._add_event.emit(
            'http/error',
            dict(_id=id))

    def session_start(self):
        """
        Saves 'session/start' event into statistics DB.
        """

        self._add_event.emit('session/start', dict())

    def session_ready(self, app_start_ts):
        """
        Saves 'session/ready' event into statistics DB.
        Uses timestamp given to calculate application startup time

        @param app_start_ts Application start timestamp [float]
        """

        self._app_start_ts = app_start_ts
        params = dict(
            _time=self._format_ts(self._now() - app_start_ts)
        )
        self._add_event.emit('session/ready', params)

    def session_end(self, rx_ws, tx_ws, rx_wd, tx_wd, rx_wr, tx_wr):
        """
        Saves 'session/end' event into statistics DB.
        Calculates session duration using previously saved application start
        timestamp

        @param rx_ws Number of bytes received via websocket protocol [long]
        @param tx_ws Number of bytes transmitted via websocket protocol [long]
        @param rx_wd Number of bytes received via webrtc protocol P2P [long]
        @param tx_wd Number of bytes transmitted via webrtc protocol P2P [long]
        @param rx_wr Number of bytes received via webrtc protocol using
            relay server [long]
        @param tx_wr Number of bytes transmitted via webrtc protocol using
            relay server [long]
        """

        params = dict(
            _time=self._format_ts(self._now() - self._app_start_ts),
            _rx_ws=rx_ws,
            _tx_ws=tx_ws,
            _rx_wd=rx_wd,
            _tx_wd=tx_wd,
            _rx_wr=rx_wr,
            _tx_wr=tx_wr
        )
        self._add_event.emit('session/end', params)

    def session_info(self, rx_ws, tx_ws, rx_wd, tx_wd, rx_wr, tx_wr):
        """
        Saves 'session/info' event into statistics DB.

        @param rx_ws Number of bytes received via websocket protocol [long]
        @param tx_ws Number of bytes transmitted via websocket protocol [long]
        @param rx_wd Number of bytes received via webrtc protocol P2P [long]
        @param tx_wd Number of bytes transmitted via webrtc protocol P2P [long]
        @param rx_wr Number of bytes received via webrtc protocol using
            relay server [long]
        @param tx_wr Number of bytes transmitted via webrtc protocol using
            relay server [long]
        """

        params = dict(
            _rx_ws=rx_ws,
            _tx_ws=tx_ws,
            _rx_wd=rx_wd,
            _tx_wd=tx_wd,
            _rx_wr=rx_wr,
            _tx_wr=tx_wr
        )

        for key in params.keys():
            if params[key] > self._session_info[key]:
                break
        else:
            # nothing changed
            return

        self._session_info.update(params)
        self._add_event.emit('session/info', params)

    def session_login_start(self):
        """
        Signalize that login attempt started
        """

        self._session_login_start_ts = self._now()

    def session_login(self, license_type):
        """
        Saves 'session/login' event into statistics DB
        """
        self._license_type = license_type
        login_time = self._now() - self._session_login_start_ts \
            if self._session_login_start_ts else 0

        params = dict(
            _time=self._format_ts(login_time),
            _lic=self._license_type
        )
        self._add_event.emit('session/login', params)

    def session_login_failed(self, error):
        """
        Saves 'session/login_failed' event into statistics DB
        """

        params = dict(
            _time=self._format_ts(self._now() - self._session_login_start_ts),
            _lic=self._license_type,
            _err=error,
        )
        self._add_event.emit('session/login_failed', params)

    def session_signup_start(self):
        """
        Signalize that login attempt started
        """

        self._session_signup_start_ts = self._now()

    def session_signup(self):
        """
        Saves 'session/login' event into statistics DB
        """

        params = dict(
            _time=self._format_ts(self._now() - self._session_signup_start_ts),
        )
        self._add_event.emit('session/signup', params)

    def session_signup_failed(self, error):
        """
        Saves 'session/login_failed' event into statistics DB
        """

        params = dict(
            _time=self._format_ts(self._now() - self._session_signup_start_ts),
            _err=error,
        )
        self._add_event.emit('session/signup_failed', params)

    def session_logout(self):
        """
        Saves 'session/logout' event into statistics DB.
        """

        self._add_event.emit('session/logout', dict())

    def crash(self, traceback, exception):
        self._add_event.emit('crash',
                             dict(_t=''.join(traceback),
                                  _e=''.join(exception)))

    def error(self, traceback, error):
        self._add_event.emit('error',
                             dict(_t=''.join(traceback),
                                  _err=error))

    def share_add(self, is_file, uuid, link, time):
        self._add_event.emit('share/add',
                             dict(_is_f=self._format_bool(is_file),
                                  _id=uuid,
                                  _link=link,
                                  _time=self._format_ts(time)))

    def share_error(self, is_file, reason, time):
        self._add_event.emit('share/error',
                             dict(_is_f=self._format_bool(is_file),
                                  _why=reason,
                                  _time=self._format_ts(time)))

    def share_cancel(self, uuid, success):
        self._add_event.emit('share/del',
                             dict(_id=uuid,
                                  _suc=self._format_bool(success)))

    def db_file_exists(self):
        return not self._db_file or exists(self._db_file) and \
               getsize(self._db_file) > 0
