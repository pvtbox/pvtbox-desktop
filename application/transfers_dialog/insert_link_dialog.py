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
from urllib.parse import urlparse, urlencode
from urllib.request import urlopen
from urllib.error import URLError, HTTPError
import json
import base64

from PySide2.QtCore import Qt
from PySide2.QtGui import QIcon, QFont
from PySide2.QtWidgets import QDialog, QLabel, QPushButton, QLineEdit

from insert_link import Ui_insert_link_dialog
from common.translator import tr

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class InsertLinkDialog(object):

    def __init__(self, parent, dp=None, signal_server_address=''):
        self._dialog = QDialog(parent)
        self._dp = dp
        self._parent = parent

        self._link = ''
        self._password = ''
        self._is_shared = True
        self._password_mode = False
        self._signal_server_address = signal_server_address

        self._dialog.setWindowIcon(QIcon(':/images/icon.png'))
        self._ui = Ui_insert_link_dialog()
        self._ui.setupUi(self._dialog)

        self._init_ui()

        self._cant_validate = tr("Cannot validate share link")

    def _init_ui(self):
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)

        self._hide_error()

        self._ok_button = self._ui.ok_button
        self._ui.cancel_button.clicked.connect(self._dialog.reject)
        self._ok_button.clicked.connect(self._ok_clicked)
        self._ui.link_line_edit.textChanged.connect(self._text_changed)

        self._set_fonts()

    def _set_fonts(self):
        controls = []
        controls.extend([c for c in self._dialog.findChildren(QLabel)])
        controls.extend(
            [c for c in self._dialog.findChildren(QLineEdit)])
        controls.extend(
            [c for c in self._dialog.findChildren(QPushButton)])

        for control in controls:
            font = control.font()
            font_size = control.font().pointSize() * self._dp
            if font_size > 0:
                control.setFont(QFont(font.family(), font_size))

    def _ok_clicked(self):
        validated = self._validate()
        if validated is None:
            self._change_mode()
        elif validated:
            if self._password_mode:
                self._password = self._ui.link_line_edit.text()
            else:
                self._link = self._ui.link_line_edit.text()
            self._dialog.accept()

    def _text_changed(self, *args, **kwargs):
        self._hide_error()

    def _validate(self):
        if self._is_shared:
            return self._validate_shared()
        else:
            return self._validate_network_file()

    def _validate_shared(self):
        if self._password_mode:
            return self._validate_password()

        if not self._validate_scheme():
            return False

        return self._check_share_link()

    def _validate_scheme(self):
        share_url = self._ui.link_line_edit.text()
        pr = urlparse(share_url)

        success = pr.scheme in ('http', 'https', 'pvtbox') and pr.path
        share_hash = pr.path.split('/')[-1]
        success = success and share_hash and len(share_hash) == 32
        if not success:
            self._show_error()
        return success

    def _check_share_link(self):
        self._lock_screen()
        share_url = self._link if self._password_mode \
            else self._ui.link_line_edit.text()
        pr = urlparse(share_url)
        share_hash = pr.path.split('/')[-1]
        param_str = ''
        if self._password_mode:
            password = self._ui.link_line_edit.text()
            password = base64.b64encode(
                bytes(password, 'utf-8')).decode('utf-8')
            params = {"passwd": password}
            query = urlencode(params, encoding='utf-8')
            param_str = '?{}'.format(query)
        url = 'https://{}/ws/webshare/{}{}'.format(
            self._signal_server_address, share_hash, param_str)
        logger.debug("url %s", url)

        error = ''
        try:
            response = urlopen(url, timeout=1)
            status = response.status
        except HTTPError as e:
            logger.warning("Request to signal server returned error %s", e)
            status = e.code
            response = str(e.read(), encoding='utf-8')
        except URLError as e:
            logger.warning("Request to signal server returned url error %s", e)
            self._show_error(self._cant_validate)
            self._unlock_screen()
            return False

        logger.debug("request status %s", status)
        if status == 400:
            if self._password_mode:
                self._link += param_str
            success = True
        else:
            success, error = self._parse_response(response)

        if success is False:
            self._show_error(error)
        self._unlock_screen()
        return success

    def _parse_response(self, response):
        try:
            data = json.loads(response)
            err_code = data.get("errcode", '')
            info = data.get("info", '')

            if err_code == 'SHARE_WRONG_PASSWORD':
                success = None if not self._password_mode else False
                error = ''
            elif err_code == 'LOCKED_CAUSE_TOO_MANY_BAD_LOGIN':
                success = False
                error = tr('Locked after too many incorrect attempts')
            elif err_code == 'SHARE_NOT_FOUND':
                success = False
                error = ''
            else:
                success = False
                error = info if info else self._cant_validate
        except Exception as e:
            logger.warning("Can't parse response (%s). reason: %s",
                           response, e)
            success = False
            error = self._cant_validate

        return  success, error

    def _validate_password(self):
        if not self._ui.link_line_edit.text():
            self._show_error()
            return False

        return self._check_share_link()

    def _validate_network_file(self):
        # place code to validate network file link here
        return False

    def _change_mode(self):
        assert not self._password_mode, \
            "Must not be in password mode in changing mode"

        logger.debug("Changing to password mode")
        self._password_mode = True
        self._dialog.setWindowTitle(tr("Insert password"))
        self._link = self._ui.link_line_edit.text()
        self._ui.link_line_edit.setText('')
        self._ui.link_line_edit.setPlaceholderText(tr("Insert password here"))
        self._ui.link_line_edit.setEchoMode(QLineEdit.Password)
        self._hide_error()

    def _show_error(self, error_text=''):
        if not error_text:
            link_text = self._ui.link_line_edit.text()
            error_text = tr("Please insert share link") \
                if not self._password_mode and not link_text \
                else tr("Invalid link") if not self._password_mode \
                else tr("Password can not be empty") if not link_text \
                else tr("Wrong password")
        self._ui.error_label.setText(error_text)
        self._ui.link_line_edit.setFocus()

    def _hide_error(self):
        self._ui.error_label.setText('')
        self._ui.link_line_edit.setFocus()

    def _lock_screen(self):
        self._ok_button.setText(tr("Processing..."))
        self._dialog.setEnabled(False)
        self._dialog.repaint()

    def _unlock_screen(self):
        self._ok_button.setText(tr("Ok"))
        self._dialog.setEnabled(True)
        self._dialog.repaint()

    def show(self):
        logger.debug("Opening insert link dialog")

        if self._dialog.exec_() == QDialog.Rejected:
            self._link = ''
            self._password = ''
            self._is_shared = True

        logger.verbose("link (%s), password (%s)", self._link, self._password)
        return self._link, self._is_shared
