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

from common.message_proxy import MessageProxy

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ServiceProxy(MessageProxy):

    def __init__(self, parent=None, receivers=(), socket_client=None):
        self._receivers = list(receivers)
        self._receivers.append(self)
        MessageProxy.__init__(self,
                              parent=parent,
                              receivers=self._receivers,
                              socket_client=socket_client)

    def dialog_clicked(self, dialog_id, button_index):
        self.send_message("dialog_clicked", [dialog_id, button_index])

    def dialog_finished(self, dialog_id):
        self.send_message("dialog_finished", [dialog_id])

    def update_status(self):
        self.send_message("update_status")

    def start_sync(self):
        self.send_message("start_sync")

    def stop_sync(self):
        self.send_message("stop_sync")

    def received_download_link(self, link, selected_folder):
        self.send_message("received_download_link",
                          [link, selected_folder])

    def share_path_requested(self, path):
        self.send_message("share_path_requested", [path])

    def gui_settings_changed(self, settings):
        self.send_message("gui_settings_changed", [settings])

    def exit_service(self):
        self.send_message("exit_service")

    def gui_logged_in(self, login_data, new_user, download_backups, smart_sync):
        self.send_message("gui_logged_in", [login_data, new_user,
                                            download_backups, smart_sync])

    def remote_action(self, action):
        self.send_message("remote_action", [action])

    def file_list_ready(self):
        self.send_message("file_list_ready")

    def is_saved_to_clipboard(self, success):
        self.send_message("is_saved_to_clipboard", [success])

    def revert_downloads(self, reverted_files, reverted_patches,
                         reverted_shares):
        self.send_message("revert_downloads",
                          [reverted_files, reverted_patches, reverted_shares])

    def add_to_sync_folder(self, selected_files_or_folders):
        self.send_message("add_to_sync_folder", [selected_files_or_folders])

    def get_offline_dirs(self):
        self.send_message("get_offline_dirs")

    def set_offline_dirs(self, offline_dirs, online_dirs):
        self.send_message("set_offline_dirs", [offline_dirs, online_dirs])
