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
import time
from sortedcontainers import SortedDict
from urllib.parse import quote
import logging
from uuid import uuid4

from PySide2.QtCore import QObject, Signal, Qt, QTimer
from requests import Session, exceptions

from common.async_qt import qt_run
from .stats_db import StatsDB

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
# disable urllib3 debug logging
logging.getLogger('urllib3.connectionpool').setLevel(logging.CRITICAL)


def encode(string):
    return quote(string, safe='')


class Event:
    def __init__(self, name):
        self._name = name
        self._timestamp = time.time()
        self._params = SortedDict()

    def AddParameter(self, name, value):
        self._params[name] = str(value)

    @property
    def name(self):
        return self._name

    @property
    def timestamp(self):
        return self._timestamp

    def get_params(self):
        return self._params

    def __str__(self):
        items = [("e", self._name), ("t", str(self._timestamp))]
        items += list(self._params.items())
        items_strings = ["{}={}".format(encode(item[0]), encode(item[1]))
                         for item in items]
        return "&".join(items_strings)


class Sender:
    request_timeout = 10
    read_timeout = 60

    def __init__(self, server_address, session, on_event_sent_cb):
        self._server_address = server_address
        self._tracking_session = session
        self._on_event_sent_cb = on_event_sent_cb

        self._requests_session = None

    def set_session(self, session):
        self._tracking_session = session

    @qt_run
    def send(self, event_id, event_str):
        success = self._tracking_session is not None
        if success:
            string_to_send = "?{}&{}".format(self._tracking_session, event_str)
            success = self._send(string_to_send)
        self._on_event_sent_cb(event_id, success)

    def _get_or_create_requests_session(self):
        if not self._requests_session:
            self._requests_session = Session()

    def _send(self, string_to_send):
        try:
            logger.verbose("Sending stat request to '%s' with data '%s'",
                           self._server_address, string_to_send)
        except AttributeError:
            pass
        url = "{}{}".format(self._server_address, string_to_send)
        self._get_or_create_requests_session()
        ua = self._tracking_session.user_agent
        headers = {'User-Agent': ua}

        try:
            res = self._requests_session.get(
                url, headers=headers,
                timeout=(self.request_timeout, self.read_timeout))
            try:
                logger.verbose("Server replied: '%s'", res)
            except AttributeError:
                pass
            success = 200 <= res.status_code < 300
        except exceptions.Timeout:
            logger.error("Stat request failed due to timeout")
            success = False

        except Exception as e:
            logger.error("Stat request failed due to %s", e)
            success = False

        return success


class TrackingSession:
    def __init__(self, user_agent="", vext=0, installation_id=""):
        self._user_agent = user_agent
        self._vext = str(vext)
        self._installation_id = installation_id
        self._session_id = str(uuid4())
        self._add_ua_to_str = False

    @property
    def id(self):
        return self._session_id

    @property
    def user_agent(self):
        return self._user_agent

    @property
    def vext(self):
        return self._vext

    @property
    def installation_id(self):
        return self._installation_id

    def add_ua_to_str(self, to_add):
        self._add_ua_to_str = to_add

    def __str__(self):
        items = [("iid", self._installation_id),
                 ("sid", self._session_id),
                 ("vext", self._vext)]
        if self._add_ua_to_str:
            items.append(("ua", self._user_agent))
        items_strings = ["{}={}".format(encode(item[0]), encode(item[1]))
                         for item in items]
        return "&".join(items_strings)


class Tracking(QObject):
    SENDING_INTERVAL = 10 * 1000
    WAIT_INTERVAL = 5 * 60 * 1000

    _event_sent = Signal(int, bool)
    _send_next_event = Signal()

    def __init__(self, parent, data_base_path, server_address,
                 on_session_stopped_cb):
        QObject.__init__(self, parent=parent)

        self._session = None
        self._on_session_stopped_cb = on_session_stopped_cb

        self._db = StatsDB(data_base_path)

        self._sender = Sender(
            server_address, self._session, self.on_event_sent_cb)

        self._connect_slots()

        self._sending_enabled = False
        self._sending_timer = QTimer(self)
        self._sending_timer.setInterval(self.SENDING_INTERVAL)
        self._sending_timer.setSingleShot(True)
        self._sending_timer.timeout.connect(
            self._on_send_next_event)

    def _connect_slots(self):
        self._event_sent.connect(self._on_event_sent, Qt.QueuedConnection)
        self._send_next_event.connect(
            self._on_send_next_event, Qt.QueuedConnection)

    def StartTrackingSession(self, user_agent, version_extension):
        logger.debug("StartTrackingSession")
        if self._session:
            return

        installation_id = self._db.get_installation_id()
        self._session = TrackingSession(
            user_agent, version_extension, installation_id)
        self._sender.set_session(self._session)

    def StopTrackingSession(self):
        if not self._session:
            return

        logger.debug("StopTrackingSession")
        self._session = None
        self._sender.set_session(self._session)
        self.StopSendingEvents()

        self._on_session_stopped_cb()

    def StartSendingEvents(self):
        logger.debug("StartSendingEvents")
        self._sending_enabled = True
        self._start_timer()

    def StopSendingEvents(self):
        logger.debug("StopSendingEvents")
        self._sending_enabled = False
        if self._sending_timer.isActive():
            self._sending_timer.stop()

    def AddEvent(self, event):
        if not self._session:
            return

        try:
            self._db.save_event(str(event))
        except Exception as e:
            logger.warning("Can't save tracking event. Reason: %s", e)

    def _on_send_next_event(self):
        if not self._sending_enabled:
            return

        try:
            event_id, event_str = self._db.load_event()
        except Exception as e:
            logger.warning("Can't load tracking event. Reason: %s", e)
            event_id = None

        if event_id:
            self._sender.send(event_id, event_str)
        else:
            self._sending_timer.setInterval(self.WAIT_INTERVAL)
            self._start_timer()

    def on_event_sent_cb(self, event_id, success):
        if not self._session:
            return

        self._event_sent.emit(event_id, success)

    def _on_event_sent(self, event_id, success):
        if success:
            try:
                self._db.delete_event(event_id)
            except Exception as e:
                logger.warning("Can't delete tracking event %s. Reason: %s",
                               event_id, e)
                self._start_timer()
                return

            self._send_next_event.emit()
        elif self._sending_enabled:
            self._start_timer()

    def _start_timer(self):
        if not self._sending_timer.isActive():
            self._sending_timer.start()
