# -*- coding: utf-8 -*-#

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
from os.path import exists, isdir, dirname
import os
import time

from PySide2.QtCore import QCoreApplication, Qt, QUrl, QProcess, QDir
from PySide2.QtGui import QIcon, QFontMetrics, QDesktopServices
from PySide2.QtWidgets import QMessageBox

from common.translator import tr
from common.utils import get_platform, remove_socket_file

translate = QCoreApplication.translate


def elided(text, widget, width=None):
    metrix = QFontMetrics(widget.font())
    if width is None:
        width = widget.width()
    elided_text = metrix.elidedText(
        text, Qt.ElideMiddle, width)
    return elided_text


def msgbox(message_text, title=None, icon=None, buttons=None,
           parent=None, default_index=-1, enable_close_button=False):
    '''
    Show message_text to user and wait until window will be closed

    @param icon: message box can also have one of standart icon
        QMessageBox.NoIcon
        QMessageBox.Question
        QMessageBox.Information
        QMessageBox.Warning
        QMessageBox.Critical

    @param buttoons: You can pass list of tuples (caption, result) to specify
        which buttons will be shown on the messagebox window.
        appropriate 'result' value of pushed button is returned as result.
        By default only one OK button is shown with empty string as result

    @return 'result' value of pushed button or empty string
    '''
    mymessage = QMessageBox(parent)
    if title:
        mymessage.setWindowTitle(title)
    else:
        mymessage.setWindowTitle(tr("Pvtbox"))
    mymessage.setText(str(message_text))

    results = {}
    if buttons:
        for i, (caption, result) in enumerate(buttons):
            btn = mymessage.addButton(caption, QMessageBox.ActionRole)
            if i == default_index:
                mymessage.setDefaultButton(btn)
            results[btn] = result

    if enable_close_button:
        close_btn = mymessage.addButton('', QMessageBox.RejectRole)
        close_btn.hide()

    pvtboxIcon = QIcon(':/images/icon.png')
    mymessage.setWindowIcon(pvtboxIcon)

    if icon:
        mymessage.setIcon(icon)

    mymessage.raise_()
    mymessage.exec_()
    return results.get(mymessage.clickedButton(), "")


def qt_open_path(path):
    system = get_platform()
    if system == 'Windows':
        from common.file_path import FilePath
        path = FilePath(path).shortpath

    # code below was added to resolve issue on Linux
    # 'kde-open5: /opt/pvtbox/libQt5Core.so.5:
    #  version `Qt_5.9.7_PRIVATE_API' not found
    #  (required by /usr/lib64/libQt5Xml.so.5)'
    if system == "Linux" and \
            os.environ.get("XDG_CURRENT_DESKTOP", None) == "KDE":
        os.environ["XDG_CURRENT_DESKTOP"] = "X-Generic"

    QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def qt_reveal_file_in_file_manager(path):
    try:
        while not exists(path):
            path = dirname(path)
    except Exception:
        return

    system = get_platform()
    if system == 'Darwin':
        args = ["-e", 'tell application "Finder"',
                "-e", "activate",
                "-e", 'select POSIX file "{}"'.format(path),
                "-e", "end tell",
                "-e", "return"]
        process = QProcess()
        if not process.execute("/usr/bin/osascript", args):
            return

    elif system == 'Windows':
        args = [QDir.toNativeSeparators(path)]
        if not isdir(path):
            args.insert(0, "/select,")
        process = QProcess()
        if process.startDetached("explorer", args):
            return

    qt_open_path(path if isdir(path) else dirname(path))


def open_link(uri):
    assert uri

    def impl():
        # code below was added to resolve issue on Linux
        # 'kde-open5: /opt/pvtbox/libQt5Core.so.5:
        #  version `Qt_5.9.7_PRIVATE_API' not found
        #  (required by /usr/lib64/libQt5Xml.so.5)'
        if get_platform() == "Linux" and \
                os.environ.get("XDG_CURRENT_DESKTOP", None) == "KDE":
            os.environ["XDG_CURRENT_DESKTOP"] = "X-Generic"

        QDesktopServices.openUrl(QUrl(uri))
        # ToDo: delete commented code if works on linux/mac os
        # webbrowser.open(uri, new=0, autoraise=True)

    return impl


def check_sync_folder_removed():
    from application.app_config import load_config as load_main_config
    from common.config import load_config
    from common.file_path import FilePath
    from common.utils import get_bases_dir

    main_cfg = load_main_config()
    if not main_cfg.get_setting('user_email'):
        return False

    config = load_config()
    root = FilePath(config.sync_directory).longpath
    return not isdir(root) or not isdir(get_bases_dir(root))


def logging_enabled():
    from application.app_config import load_config as load_main_config

    main_cfg = load_main_config()
    return not main_cfg.get_setting('logging_disabled')


def get_added_time_string(created_time, was_updated, is_deleted):
    total_sec = int(time.time() - created_time)
    minutes = total_sec // 60
    hours = minutes // 60
    days = hours // 24
    months = days // 30
    years = months // 12
    time_name, time_value = \
        (tr('years'), years) if years > 1 else \
        (tr('year'), 1) if years == 1 else \
        (tr('months'), months) if months > 1 else \
        (tr('month'), 1) if months == 1 else \
        (tr('days'), days) if days > 1 else \
        (tr('day'), 1) if days == 1 else \
        (tr('hours'), hours) if hours > 1 else \
        (tr('hour'), 1) if hours == 1 else \
        (tr('minutes'), minutes) if minutes > 1 else \
        (tr('minute'), 1) if minutes == 1 else \
        (None, None)
    added_modified = tr('Added {} {} ago') if not was_updated \
        else tr('Modified {} {} ago') if not is_deleted \
        else tr('Deleted {} {} ago')
    recently_added_modified = tr('Recently added') if not was_updated \
        else tr('Recently modified') if not is_deleted \
        else tr('Recently deleted')
    if time_name:
        result_str = added_modified.format(time_value, time_name)
    else:
        result_str = recently_added_modified
    return result_str


def service_cleanup():
    remove_socket_file()
