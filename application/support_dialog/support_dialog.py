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
import time
import os
import os.path as op
from glob import glob
import zipfile
import datetime

from PySide2.QtCore import Qt, Signal, QObject, QTimer
from PySide2.QtGui import QFont, QColor, QPalette
from PySide2.QtWidgets import QDialog

from support import Ui_Dialog
from application.utils import open_link
from common.utils import get_bases_dir, remove_file, get_free_space, cwd, \
    get_init_done, clear_init_done
from common.translator import tr
from .progress_pipe import ProgressPipe

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SendingError(Exception):
    pass


class SupportDialog(QObject):
    SHORT_FEEDBACK_INTERVAL = 5 * 60 * 1000
    DAYS_TO_FEEDBACK = 7
    DROPDOWN_BACKGROUND_COLOR = "#f78d1e"
    DROPDOWN_COLOR = "white"

    _sending_error = Signal()

    SUBJECT = {
        1: "TECHNICAL",
        2: "OTHER",
        3: "FEEDBACK",
    }

    def __init__(self, parent, parent_window, config, dp=1, selected_index=0):
        QObject.__init__(self, parent)

        self._parent = parent
        self._parent_window = parent_window
        self._config = config
        self._dp = dp
        self._selected_index = selected_index
        self._dialog = QDialog(parent_window)
        self._dialog.setWindowFlags(Qt.Dialog)
        self._dialog.setAttribute(Qt.WA_MacFrameworkScaled)

        self._is_opened = False
        self._pipe = None
        self._feedback_mode = False

        self._ui = Ui_Dialog()
        self._ui.setupUi(self._dialog)
        self._init_ui()
        self._old_close_event = self._dialog.closeEvent

        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._show_feedback_form)

        self._parent.service_started.connect(self._check_feedback_needed)
        self._parent.exit_request.connect(self._on_exit_request)

    def _init_ui(self):
        self._ui.pushButton.setEnabled(False)
        self._ui.comboBox.addItem(tr("---Select Subject---"))
        self._ui.comboBox.addItem(tr("Technical Question"))
        self._ui.comboBox.addItem(tr("Other Question"))
        self._ui.comboBox.addItem(tr("Feedback"))
        self._ui.comboBox.setCurrentIndex(self._selected_index)

        palette = self._ui.comboBox.palette()
        palette.setColor(
            QPalette.HighlightedText, QColor(self.DROPDOWN_COLOR))
        palette.setColor(
            QPalette.Highlight, QColor(self.DROPDOWN_BACKGROUND_COLOR))
        self._ui.comboBox.setPalette(palette)
        palette = self._ui.comboBox.view().palette()
        palette.setColor(
            QPalette.HighlightedText, QColor(self.DROPDOWN_COLOR))
        palette.setColor(
            QPalette.Highlight, QColor(self.DROPDOWN_BACKGROUND_COLOR))
        self._ui.comboBox.view().setPalette(palette)

        self._set_tooltip()

        self._ui.comboBox.currentIndexChanged.connect(self._on_index_changed)
        self._ui.plainTextEdit.textChanged.connect(self._set_tooltip)
        self._ui.pushButton.clicked.connect(self._on_send_clicked)
        self._ui.text_label.linkActivated.connect(self._on_link_activated)
        self._sending_error.connect(self._clear_pipe_state)

        self._set_fonts()

    def _set_fonts(self):
        ui = self._ui
        controls = [ui.plainTextEdit, ui.pushButton,
            ui.comboBox, ui.text_label, ui.checkBox]

        for control in controls:
            font = control.font()
            font_size = control.font().pointSize() * self._dp
            if font_size > 0:
                control_font = QFont(font.family(), font_size)
                control_font.setBold(font.bold())
                control.setFont(control_font)

    def set_selected_index(self, selected_index):
        self._selected_index = selected_index
        if self._is_opened:
            self._ui.comboBox.setCurrentIndex(self._selected_index)

    def show(self):
        if self._parent.dialogs_opened():
            return

        self._is_opened = True
        logger.debug("Support dialog opening...")
        self._pipe = None
        self.set_selected_index(self._selected_index)
        self._ui.checkBox.setChecked(False)
        self._ui.comboBox.setEnabled(not self._feedback_mode)
        self._dialog.exec_()

        logger.debug("Support dialog closed")
        if self._pipe:
            try:
                self._pipe.stop()
                self._clear_pipe_state()
            except Exception as e:
                logger.error("Unexpected error stopping pipe: (%s)", e)
        self._is_opened = False
        self._selected_index = 0
        self._ui.plainTextEdit.document().clear()
        self._ui.checkBox.setChecked(False)

    def dialog_opened(self):
        return self._is_opened

    def close(self):
        self._dialog.close()

    def _set_tooltip(self):
        if not self._selected_index:
            tooltip = tr("Please select subject")
            self._ui.pushButton.setEnabled(False)
        elif not self._ui.plainTextEdit.document().toPlainText():
            tooltip = tr("Message can't be empty")
            self._ui.pushButton.setEnabled(False)
        else:
            tooltip = tr("Click to send message")
            self._ui.pushButton.setEnabled(True)
        self._ui.pushButton.setToolTip(tooltip)

    def _on_index_changed(self, selected_index):
        self._selected_index = selected_index
        self._set_tooltip()

    def _on_send_clicked(self):
        self._dialog.setEnabled(False)
        self._pipe = ProgressPipe(
            self,
            self._ui.pushButton,
            timeout=1000,
            final_text=tr("Sent"),
            final_timeout=500)
        self._pipe.pipe_finished.connect(self._on_pipe_finished)
        if self._ui.checkBox.isChecked():
            self._pipe.add_task(tr("Compressing"), self._archive_logs())
            self._pipe.add_task(tr("Uploading"), self._upload_file())
        self._pipe.add_task(tr("Sending"), self._send_message())
        self._pipe.start()

    def _on_pipe_finished(self):
        self._clear_feedback_flag()
        self.close()

    def _clear_pipe_state(self):
        self._dialog.setEnabled(True)
        try:
            self._pipe.pipe_finished.disconnect(self._on_pipe_finished)
        except Exception as e:
            logger.warning("Can't disconnect signal: %s", e)
        self._ui.pushButton.setText(tr("SEND"))

    def _send_message(self):
        def send(log_file_name=""):
            logger.debug("Support compressed log_file_name %s", log_file_name)
            if self._selected_index not in self.SUBJECT:
                logger.warning("Attempt to send message to support "
                               "with invalid subject")
                return

            subject = self.SUBJECT[self._selected_index]
            res = self._parent.web_api.send_support_message(
                subject, self._ui.plainTextEdit.document().toPlainText(),
                log_file_name)
            was_error = False
            msg = tr("Can't send message to support")
            if res and "result" in res:
                if res["result"] != "success":
                    was_error = True
                    msg = str(res.get("info", msg))
            else:
                was_error = True
            if was_error:
                self._parent.show_tray_notification(msg)
                self._sending_error.emit()
                raise SendingError(msg)

        return send

    def _archive_logs(self):
        def archive():
            # uses function attributes to track progress
            # archive.size, archive.progress, archive.stop
            logs_dir = get_bases_dir(self._config.sync_directory)
            log_files = glob("{}{}*.log".format(logs_dir, os.sep))
            log_sizes = list(map(os.path.getsize, log_files))
            # mark overall size
            archive.size = sum(log_sizes)

            old_archives = glob("{}{}2*_logs.zip".format(logs_dir, os.sep))
            try:
                list(map(remove_file, old_archives))
            except Exception as e:
                logger.warning("Can't delete old archives. Reason: (%s)", e)

            if get_free_space(logs_dir) < archive.size // 5:
                # archive.size // 5 is approx future archive size
                msg = tr("Insufficient disk space to archive logs. "
                         "Please clean disk")
                self._parent.show_tray_notification(msg)
                self._sending_error.emit()
                raise SendingError(msg)

            archive_name = time.strftime('%Y%m%d_%H%M%S_logs.zip')
            archive_path = "{}{}{}".format(logs_dir, os.sep, archive_name)
            archive_dir = op.dirname(archive_path)
            f = zipfile.ZipFile(
                archive_path, "w", compression=zipfile.ZIP_DEFLATED,
                compresslevel=9)
            try:
                with cwd(archive_dir):
                    for i, log_file in enumerate(log_files):
                        if not op.isfile(log_file):
                            continue

                        f.write(op.basename(log_file))
                        # mark progress
                        archive.progress += log_sizes[i]
                        if archive.stop:
                            return

            except Exception as e:
                msg = tr("Can't archive logs.")
                logger.warning(msg + " Reason: (%s)", e)
                self._parent.show_tray_notification(msg)
                self._sending_error.emit()
                raise SendingError(msg)
            finally:
                f.close()
                if archive.stop:
                    remove_file(archive_path)

            return archive_path

        return archive

    def _upload_file(self):
        def upload(path):
            # uses function attributes to track progress
            # upload.size, upload.progress, upload.stop
            upload.size = op.getsize(path)

            res = self._parent.web_api.upload_file(
                path, "application/zip", callback)
            was_error = False
            msg = tr("Can't upload archive file")
            if res and "result" in res:
                if res["result"] == "success":
                    filename = res.get("file_name", "")
                else:
                    was_error = True
                    msg = str(res.get("info", msg))
            else:
                was_error = True
            if was_error and not upload.stop:
                self._parent.show_tray_notification(msg)
                self._sending_error.emit()
                raise SendingError(msg)

            remove_file(path)
            return filename

        def callback(monitor):
            upload.progress = monitor.bytes_read
            if upload.stop:
                raise SendingError("Stopped")

        return upload

    def _on_link_activated(self):
        open_link(self._parent.get_help_uri())()
        self.close()

    def _check_feedback_needed(self):
        if self._feedback_timer.isActive():
            return

        start_date = get_init_done()
        now = datetime.datetime.now()
        logger.debug("Start date is %s", start_date)
        if start_date is None:
            return

        interval = (start_date - now) \
                   + datetime.timedelta(days=self.DAYS_TO_FEEDBACK)

        if interval.total_seconds() <= 0:
            logger.debug("Feedback form date time is now")
            self._show_feedback_form()
        else:
            self._feedback_timer.setInterval(interval.seconds * 1000)
            self._feedback_timer.start()
            logger.debug("Feedback form date time is %s", now + interval)

    def _show_feedback_form(self):
        if self._is_opened:
            self._feedback_timer.setInterval(self.SHORT_FEEDBACK_INTERVAL)
            self._feedback_timer.start()
            return

        self._feedback_mode = True
        self._selected_index = 3
        self._dialog.closeEvent = self._close_event
        window_title = self._dialog.windowTitle()
        label_text = self._ui.text_label.text()
        feedback_text = tr("Please leave your feedback for Pvtbox")
        self._ui.text_label.setText(
            "<html><head/><body><p>{}</p></body></html>".format(feedback_text))
        self._dialog.setWindowTitle(tr("Feedback"))
        self.show()
        self._dialog.setWindowTitle(window_title)
        self._ui.text_label.setText(label_text)

    def _close_event(self, event):
        if event.spontaneous():
            self._clear_feedback_flag()
        self._old_close_event(event)

    def _clear_feedback_flag(self):
        if self._feedback_mode:
            logger.debug("Feedback flag cleared")
            clear_init_done()
            self._feedback_mode = False
            self._dialog.closeEvent = self._old_close_event

    def _on_exit_request(self):
        if self._is_opened:
            self.close()
