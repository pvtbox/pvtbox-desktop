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
import os.path as op
from datetime import datetime
import re
from collections import deque

from PySide2.QtCore import Qt, QObject, Signal, QTimer
from PySide2.QtGui import QIcon

from application.utils import open_link, qt_open_path
from common.translator import tr
from .notifications_dialog import NotificationsDialog
from common.constants import GET_PRO_URI
from common.utils import get_local_time_from_timestamp
from common.async_qt import qt_run

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Notification:

    def __init__(self, notification_info, cfg,
                 on_accept,
                 text_color, highlight_color,
                 you_color, time_color):
        self._cfg = cfg
        self._on_accept = on_accept
        self._text_color = text_color
        self._highlight_color = highlight_color
        self._you_color = you_color
        self._time_color = time_color

        self._init_reactions()

        try:
            self._id = notification_info['notification_id']
            self._substitutes = dict(
                zip(notification_info['search'],
                    notification_info['replace']))
            self._action = notification_info['action']
            self._text = notification_info['text']
            self._text_to_display = self._prepare_text_to_display()
            self._timestamp = notification_info['timestamp']
            self._datetime = self._make_datetime(self._timestamp)
            self._read = notification_info['read']
        except KeyError as e:
            logger.error('Invalid notification format. Info: %s Error: %s',
                         notification_info, e)

    def _init_reactions(self):
        actions = [
            'collaboration_include', 'collaboration_invite',
            'collaboration_join', 'collaboration_about_join_for_admin',
            'collaboration_exclude', 'collaboration_self_exclude',
            'for_owner_colleague_self_exclude', 'collaboration_change_access',
            'collaboration_added_files', 'collaboration_deleted_files',
            'collaboration_moved_files',
            'license_expired', 'license_downgraded',
            'license_upgraded', 'license_changed',
        ]
        self._actions_reactions = dict()
        self._actions_reactions.fromkeys(actions, lambda: None)
        self._actions_reactions['collaboration_include'] = self._open_folder
        self._actions_reactions['collaboration_join'] = self._open_folder
        self._actions_reactions['collaboration_invite'] = \
            self._accept_invitation
        self._actions_reactions['license_expired'] = self._open_link
        self._actions_reactions['license_downgraded'] = self._open_link

    @property
    def is_read(self):
        return self._read

    @property
    def id(self):
        return self._id

    def read(self):
        self._read = True
        self._actions_reactions.get(self._action, lambda: None)()

    def get_text(self):
        return self._text_to_display

    def get_datetime(self):
        return self._datetime

    def _open_folder(self):
        path = op.join(self._cfg.sync_directory,
                       self._substitutes['{folder_name}'])
        qt_open_path(path)

    def _open_link(self):
        open_link(GET_PRO_URI.format(self._cfg.host))()

    def _accept_invitation(self):
        colleague_id = self._substitutes.get('{colleague_id}')
        if colleague_id:
            self._on_accept(colleague_id)
        else:
            logger.warning("No colleague_id for notification %s", self._id)

    def _make_datetime(self, timestamp):
        timestamp = get_local_time_from_timestamp(timestamp)
        dt = datetime.fromtimestamp(timestamp)
        return self._rich_text(self._enclose_with_color(
            dt.strftime("%d %B %Y, %H:%M"), self._time_color))

    def _enclose_with_color(self, string, color):
        return '<span style="color:{};">{}</span>'.format(color, string)

    def _rich_text(self, string):
        return '<html><head/><body><p>{}</p></body></html>'.format(string)

    def _prepare_text_to_display(self):
        pattern = '|'.join([
            r'(\{' + s.strip('{}') + r'\})' for s in self._substitutes])

        def repl(m):
            subst = self._substitutes[m.group()]
            color = self._highlight_color if subst.lower() != 'you' \
                else self._you_color
            return self._enclose_with_color(subst, color)

        replaced = re.sub(pattern, repl, self._text)
        logger.debug("replaced notification text %s", replaced)
        replaced = self._enclose_with_color(replaced, self._text_color)
        return self._rich_text(replaced)


class Notifications(QObject):
    NOTIFICATIONS_NUMBER_LIMIT = 30
    PAGE_SIZE = 10
    TEXT_COLOR = "black"
    HIGHLIGHHT_COLOR = "orange"
    TIME_COLOR = "gray"
    YOU_COLOR = "green"

    _new_notifications_count = Signal(int)
    _notifications_got = Signal(list)

    def __init__(self, parent, parent_window, web_api, cfg, dp):
        QObject.__init__(self, parent)

        self._parent = parent
        self._parent_window = parent_window
        self._web_api = web_api
        self._cfg = cfg
        self._dp = dp

        self._notifications = deque()
        self._notifications_dialog = None

        self._querying = False
        self._all_loaded = False
        self._set_the_bell = self._bell_setter()
        self._set_the_bell(0)

        self._new_notifications_count.connect(
            self._on_new_notifications_count, Qt.QueuedConnection)
        self._notifications_got.connect(
            self._on_notifications_got, Qt.QueuedConnection)

    def new_notifications_count(self, count):
        self._new_notifications_count.emit(count)

    def load_notifications(self, show_loading=False):
        if self._all_loaded:
            return False

        if self._querying:
            return True

        self._query_notifications(
            self.NOTIFICATIONS_NUMBER_LIMIT, self._get_min_id())
        self._notifications_dialog.show_cursor_loading(show_movie=show_loading)
        return True

    @property
    def all_loaded(self):
        return self._all_loaded

    @property
    def is_querying(self):
        return self._querying

    @property
    def limit(self):
        return self.NOTIFICATIONS_NUMBER_LIMIT

    def _on_new_notifications_count(self, count):
        self._set_the_bell(count)
        if count == 0 or not self._notifications_dialog:
            return

        if self._querying:
            QTimer.singleShot(
                200, lambda: self._on_new_notifications_count(count))
            return

        count_to_query = min(count, self.NOTIFICATIONS_NUMBER_LIMIT)
        if count_to_query < count:
            self._notifications.clear()
        self._query_notifications(count_to_query)
        self._notifications_dialog.show_cursor_loading(show_movie=True)

    def _bell_setter(self):
        ui = self._parent.get_ui()
        old_count = -1

        def _set_the_bell(count):
            nonlocal old_count
            if count == old_count:
                return

            old_count = count
            if count:
                ui.bell.setIcon(QIcon(':/images/bell-alert.png'))
            else:
                ui.bell.setIcon(QIcon(':/images/bell.png'))
            ui.bell.setToolTip(tr("{} new notification(s)".format(count)))

        return _set_the_bell

    @qt_run
    def _query_notifications(self, count_to_query, from_id=0):
        logger.debug("query %s notifications from %s", count_to_query, from_id)
        self._querying = True
        notifications = list()

        res = self._web_api.get_notifications(count_to_query, from_id)
        if res and "result" in res:
            if res["result"] == "success":
                notifications = res['data']
                self._all_loaded = len(notifications) < count_to_query
                logger.debug("Got notifications %s", notifications)
            else:
                logger.warning("Can't get notifications: %s", res)
        else:
            logger.warning('Result not returned for notifications query')

        self._notifications_got.emit(notifications)
        self._querying = False

    def _on_notifications_got(self, notifications):
        if not notifications:
            self._notifications_dialog.show_notifications()
            return

        min_id = self._get_min_id()
        max_id = self._get_max_id()
        if notifications[0]['notification_id'] >= max_id and \
                notifications[-1]['notification_id'] <= min_id:
            self._notifications.clear()
        notifications_ids = {n.id for n in self._notifications}

        if max_id and notifications[0]['notification_id'] > max_id:
            notifications.reverse()
            for new_n in notifications:
                if new_n['notification_id'] > max_id:
                    self._notifications.appendleft(
                        self._create_notification_object(new_n))
        else:
            for new_n in notifications:
                if new_n['notification_id'] not in notifications_ids:
                    self._notifications.append(
                        self._create_notification_object(new_n))
        logger.debug("Notifications ids %s",
                     [n.id for n in self._notifications])
        self._notifications_dialog.show_notifications()

    def _create_notification_object(self, new_notification):
        return Notification(
            new_notification, self._cfg,
            self._accept_collaboration_invitation,
            self.TEXT_COLOR, self.HIGHLIGHHT_COLOR,
            self.YOU_COLOR, self.TIME_COLOR)

    def _get_max_id(self):
        return self._notifications[0].id if self._notifications else 0

    def _get_min_id(self):
        return self._notifications[-1].id if self._notifications else 0

    @qt_run
    def _accept_collaboration_invitation(self, colleague_id):
        self._parent.show_tray_notification(tr("Accepting invitation..."))
        res = self._web_api.accept_invitation(colleague_id)

        msg = tr("Can't send invitation accept")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Invitation accepted successfully")
            else:
                if "info" in res:
                    msg = res.get("info", "")
        self._parent.show_tray_notification(msg)

    def show_dialog(self):
        if self._notifications_dialog:
            self._notifications_dialog.raise_dialog()
            return

        self._notifications_dialog = NotificationsDialog(
            self,
            self._parent_window,
            self._notifications,
            self._dp)

        self._notifications_dialog.show(self.on_dialog_finished)

    def on_dialog_finished(self):
        self._notifications_dialog = None
        self.clear()

    def clear(self):
        self._notifications.clear()
        self._all_loaded = False

    def close(self):
        self.clear()
        if not self._notifications_dialog:
            return

        self._notifications_dialog.close()
