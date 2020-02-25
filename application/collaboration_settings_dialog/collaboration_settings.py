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

from PySide2.QtCore import Qt, QObject, Signal, QTimer
from PySide2.QtGui import QIcon

from common.translator import tr
from .collaboration_settings_dialog import CollaborationSettingsDialog
from common.constants import FREE_LICENSE
from common.async_qt import qt_run

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Colleague:

    def __init__(self, colleague_info, your_mail,
                 text_color, highlight_color,
                 you_color, status_color):
        self._text_color = text_color
        self._highlight_color = highlight_color
        self._you_color = you_color
        self._status_color = status_color

        try:
            self._id = colleague_info['colleague_id']
            self._email = colleague_info['email']
            self._is_you = self._email == your_mail
            self._status = colleague_info['status']
            self._access_type = colleague_info['access_type']
            self._is_owner = self._access_type == 'owner'
            self._mail_to_display = self._prepare_mail_to_display()
            self._status_to_display = self._prepare_status_to_display()
            self._access_to_display = self._prepare_access_to_display()
        except KeyError as e:
            logger.error('Invalid colleague format. Info: %s Error: %s',
                         colleague_info, e)

    @property
    def id(self):
        return self._id

    @property
    def is_owner(self):
        return self._is_owner

    @property
    def is_you(self):
        return self._is_you

    @property
    def email(self):
        return self._email

    @property
    def is_deleting(self):
        return self._status.lower().startswith("queued for del")

    @property
    def can_edit(self):
        return self._is_owner or self._access_type == 'edit'

    def get_mail_text(self):
        return self._mail_to_display

    def get_status_text(self):
        return self._status_to_display

    def get_access_text(self):
        return self._access_to_display

    def _enclose_with_color(self, string, color):
        return '<span style="color:{};">{}</span>'.format(color, string)

    def _rich_text(self, string):
        return '<html><head/><body><p>{}</p></body></html>'.format(string)

    def _prepare_mail_to_display(self):
        replaced = self._enclose_with_color(self._email, self._text_color)
        if self._is_you:
            replaced += self._enclose_with_color(
                " ({})".format(tr('You')), self._you_color)
        return self._rich_text(replaced)

    def _prepare_status_to_display(self):
        replaced = self._enclose_with_color(
            "{}: {}".format(tr("Status"), self._status), self._status_color)
        return self._rich_text(replaced)

    def _prepare_access_to_display(self):
        prefix = self._enclose_with_color(
            tr('Can') + ' ', self._status_color) \
            if not self._is_owner else ""
        access_type = self._access_type if not self._is_owner \
            else self._access_type.title()
        replaced = prefix + self._enclose_with_color(
            access_type, self._highlight_color)
        return self._rich_text(replaced)


class CollaborationSettings(QObject):
    TEXT_COLOR = "black"
    HIGHLIGHT_COLOR = "orange"
    STATUS_COLOR = "gray"
    YOU_COLOR = "green"

    _collaboration_info_got = Signal(dict)
    _close_dialog = Signal()

    def __init__(self, parent, parent_window, cfg, dp):
        QObject.__init__(self, parent)

        self._parent = parent
        self._parent_window = parent_window
        self._cfg = cfg
        self._dp = dp

        self._uuid = None
        self._path = None
        self._is_owner = None
        self._owner_id = None

        self._colleagues = list()
        self._collaboration_settings_dialog = None

        self._querying = False

        self._collaboration_info_got.connect(
            self._on_collaboration_info_got, Qt.QueuedConnection)
        self._close_dialog.connect(self.close, Qt.QueuedConnection)

    def show_collaboration_settings(self, path, uuid):
        if self._cfg.license_type == FREE_LICENSE:
            self._parent.show_tray_notification(
                tr("Collaborations not available for free license"))
            return

        if self._collaboration_settings_dialog or \
                not self._parent.is_logged_in() or \
                self._parent.dialogs_opened():
            reason = "dialog already opened" \
                if self._collaboration_settings_dialog else "user logged out" \
                if not self._parent.is_logged_in() else "other dialog opened"
            logger.warning("Can't show collaboration settings. Reason: %s",
                           reason)
            return

        self._path = path
        self._uuid = uuid

        self._collaboration_settings_dialog = CollaborationSettingsDialog(
            self,
            self._parent_window,
            self._colleagues,
            self._path,
            self._dp)

        self.query_collaboration_info()
        # show dialog on top
        self._parent_window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self._parent.show()
        self._parent_window.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        self._parent.show()

        self._collaboration_settings_dialog.show()
        self.clear()

    @property
    def is_querying(self):
        return self._querying

    @qt_run
    def query_collaboration_info(self):
        self._query_info()

    def _query_info(self):
        logger.debug("Querying collaboration info")
        self._querying = True
        collaboration_info = dict()

        res = self._parent.web_api.collaboration_info(self._uuid)
        was_error = True
        msg = tr("Can't get collaboration info")
        if res and "result" in res:
            if res["result"] == "success":
                was_error = False
                collaboration_info = res['data']
                logger.debug("Got collaboration info %s", collaboration_info)
                if not collaboration_info:
                    collaboration_info = {'collaboration_is_owner': True}
            else:
                if "info" in res:
                    msg = res.get("info", msg)
                logger.warning("No collaboration info: %s", res)
        else:
            logger.warning('Result not returned for collaboration info query')
        if was_error:
            self._parent.show_tray_notification(msg)
            return

        self._collaboration_info_got.emit(collaboration_info)
        self._querying = False

    def _on_collaboration_info_got(self, collaboration_info):
        if not self._collaboration_settings_dialog:
            return

        self._colleagues.clear()
        if self._is_owner is None:
            self._is_owner = collaboration_info.get(
                'collaboration_is_owner', False)
            self._collaboration_settings_dialog.set_owner(self._is_owner)

        self._owner_id = collaboration_info.get('collaboration_owner')
        colleagues = collaboration_info.get('colleagues', [])
        your_mail = self._cfg.user_email
        for colleague_info in colleagues:
            self._colleagues.append(
                self._create_colleague_object(colleague_info, your_mail))
        self._collaboration_settings_dialog.show_colleagues()

    def _create_colleague_object(self, colleague_info, your_mail):
        return Colleague(
            colleague_info, your_mail,
            self.TEXT_COLOR, self.HIGHLIGHT_COLOR,
            self.YOU_COLOR, self.STATUS_COLOR)

    @qt_run
    def remove(self, colleague_id):
        self._parent.show_tray_notification(
            tr("Deleting colleague from collaboration..."))
        res = self._parent.web_api.colleague_delete(self._uuid, colleague_id)

        msg = tr("Can't delete colleague from collaboration")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Colleague deleted successfully")
            else:
                msg = str(res.get("info", msg))
        self._parent.show_tray_notification(msg)
        self._query_info()

    @qt_run
    def grant_edit(self, colleague_id, to_edit):
        start_msg = tr("Adding edit permission to collaboration...") \
            if to_edit else tr("Removing edit permission to collaboration...")
        self._parent.show_tray_notification(start_msg)
        access_type = "edit" if to_edit else "view"
        res = self._parent.web_api.colleague_edit(
            self._uuid, colleague_id, access_type)

        msg = tr("Can't change edit permission to collaboration")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Edit permission changed successfully")
            else:
                msg = str(res.get("info", msg))
        self._parent.show_tray_notification(msg)
        self._query_info()

    @qt_run
    def add_colleague(self, colleague_email, to_edit):
        self._parent.show_tray_notification(
            tr("Adding colleague to collaboration..."))
        access_type = "edit" if to_edit else "view"
        res = self._parent.web_api.colleague_add(
            self._uuid, colleague_email, access_type)

        msg = tr("Can't add colleague to collaboration")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Colleague added successfully")
            else:
                msg = str(res.get("info", msg))
        self._parent.show_tray_notification(msg)
        self._query_info()

    @qt_run
    def cancel_collaboration(self):
        self._parent.show_tray_notification(
            tr("Deleting collaboration..."))
        uuid = self._uuid
        self._close_dialog.emit()
        res = self._parent.web_api.collaboration_cancel(uuid)

        msg = tr("Can't delete collaboration")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Collaboration deleted successfully")
            else:
                msg = str(res.get("info", msg))
        self._parent.show_tray_notification(msg)

    @qt_run
    def leave_collaboration(self):
        self._parent.show_tray_notification(
            tr("Leaving collaboration..."))
        uuid = self._uuid
        self._close_dialog.emit()
        res = self._parent.web_api.collaboration_leave(uuid)

        msg = tr("Can't leave collaboration")
        if res and "result" in res:
            if res["result"] == "success":
                msg = tr("Successfully leaved collaboration")
            else:
                msg = str(res.get("info", msg))
        self._parent.show_tray_notification(msg)

    def clear(self):
        self._collaboration_settings_dialog = None
        self._colleagues.clear()
        self._uuid = None
        self._path = None
        self._is_owner = None
        self._owner_id = None

    def close(self):
        if self._collaboration_settings_dialog:
            self._collaboration_settings_dialog.close()
            self.clear()

    def dialog_opened(self):
        return self._collaboration_settings_dialog is not None


