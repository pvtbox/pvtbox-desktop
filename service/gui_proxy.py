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
from PySide2.QtCore import Signal

from common.message_proxy import MessageProxy

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GuiProxy(MessageProxy):
    update_status = Signal()
    start_sync = Signal()
    stop_sync = Signal()
    received_download_link = Signal(str, str)
    share_path_requested = Signal(str)
    gui_settings_changed = Signal(dict)
    gui_logged_in = Signal(dict, bool, bool)
    exit_service = Signal()
    remote_action = Signal(dict)
    file_list_ready = Signal()
    is_saved_to_clipboard = Signal(bool)
    revert_downloads = Signal(list, list, list)
    add_to_sync_folder = Signal(list)

    def __init__(self, parent=None, receivers=(), socket_client=None):
        self._receivers = list(receivers)
        self._receivers.append(self)
        MessageProxy.__init__(self,
                              parent=parent,
                              receivers=self._receivers,
                              socket_client=socket_client,
                              verbose=True)

        self._dialog_id = 0
        self._dialogs = dict()

        # May be some other time
        # self._allowed_actions = ["login_failed",
        #                          "registered",
        #                          "registration_failed",
        #                          "show_auth_page",
        #                          "show_network_error_page",
        #                          "show_main_page",
        #                          "show_tray_notification",
        #                          "request_to_user",
        #                          "save_to_clipboard",
        #                          "on_share_changed",
        #                          "lost_folder_dialog",
        #                          "disk_space_status_changed",
        #                          "upload_speed_changed",
        #                          "download_speed_changed",
        #                          "download_progress",
        #                          "on_network_error",
        #                          "sync_started",
        #                          "sync_stopped",
        #                          "logged_in",
        #                          "open_webfm",
        #                          "sync_status_changed",
        #                          "on_file_moved",
        #                          "init_file_list",
        #                          "download_link_handler",
        #                          "show",                         # rename???
        #                          "new_language",
        #                          "exit_request",
        #                          ]

    def init(self, is_logged_in, config, config_filename):
        self.send_message("init", [is_logged_in, config, config_filename])

    # # May be some other time
    # def __getattr__(self, item):
    #     if item not in self._allowed_actions:
    #         logger.error("Action not allowed %s", item)
    #         raise AttributeError
    #
    #     self._last_name = item
    #     return self._send
    #
    # def _send(self, *args):
    #     if not self._last_name:
    #         logger.error("No action name")
    #         raise AttributeError
    #
    #     data = list(args) if args else None
    #     self.send_message(self._last_name, data)
    #     self._last_name = None

    def dialog_clicked(self, dialog_id, button_index):
        try:
            callbacks, finish, indeces_to_hold = self._dialogs[dialog_id]
        except KeyError:
            logger.error("No such dialog %s", dialog_id)
            return
        try:
            callback = callbacks[button_index]
        except IndexError:
            logger.error("No button with index %s for dialog %s",
                         button_index, dialog_id)
            return

        if finish is None and button_index not in indeces_to_hold:
            self._dialogs.pop(dialog_id)
        if callable(callback):
            callback()

    def dialog_finished(self, dialog_id):
        try:
            _, finish, _i = self._dialogs[dialog_id]
        except KeyError:
            logger.error("No such dialog %s", dialog_id)
            return

        self._dialogs.pop(dialog_id)
        if callable(finish):
            finish()

    def login_failed(self, errcode, info):
        self.send_message("login_failed", [errcode, info])

    def registration_failed(self, errcode, info):
        self.send_message("registration_failed", [errcode, info])

    def show_auth_page(self, show_registration, clean_error):
        self.send_message("show_auth_page",
                          [show_registration, clean_error])

    def show_network_error_page(self):
        self.send_message("show_network_error_page")

    def show_main_page(self):
        self.send_message("show_main_page")

    def show_tray_notification(self, text, title):
        self.send_message("show_tray_notification", [text, title])

    def request_to_user(self, text, buttons, title,
                        close_button_index, close_button_off, details=''):
        button_texts = []
        button_callbacks = []
        for button in buttons:
            try:
                assert isinstance(button, (list, tuple))
                button_text, button_callback = button
            except (TypeError, ValueError, AssertionError):
                button_text, button_callback = button, None
            button_texts.append(button_text)
            button_callbacks.append(button_callback)

        self._dialog_id += 1
        self._dialogs[self._dialog_id] = (button_callbacks, None, ())
        self.send_message("request_to_user",
                          [self._dialog_id, text, button_texts, title,
                           close_button_index, close_button_off, None, details])

    def save_to_clipboard(self, text):
        self.send_message("save_to_clipboard", [text])

    def on_share_changed(self, shared):
        self.send_message("on_share_changed", [shared])

    def lost_folder_dialog(self, path, restore_folder, finish):
        self._dialog_id += 1
        self._dialogs[self._dialog_id] = ([restore_folder], finish, ())
        self.send_message("lost_folder_dialog",
                          [self._dialog_id, path])

    def disk_space_status_changed(self, disk_space_low,
                                  cfg_orange, cfg_red,
                                  data_orange, data_red, same_volume,
                                  cfg_drive, data_drive,
                                  cfg_space, data_space):
        self.send_message("disk_space_status_changed",
                          [disk_space_low, cfg_orange, cfg_red,
                           data_orange, data_red, same_volume,
                           cfg_drive, data_drive,
                           cfg_space, data_space])

    def upload_speed_changed(self, speed):
        self.send_message("upload_speed_changed", [speed])

    def download_speed_changed(self, speed):
        self.send_message("download_speed_changed", [speed])

    def download_progress(self, display_text,
                          current_downloading_percent, total_downloads):
        self.send_message("download_progress",
                          [display_text,
                           current_downloading_percent, total_downloads])

    def downloads_status(self, display_text,
                         current_downloading_percent, total_downloads,
                         downloads_info, uploads_info):
        self.send_message("downloads_status",
                          [display_text,
                           current_downloading_percent, total_downloads,
                           downloads_info, uploads_info])

    def on_network_error(self, error):
        self.send_message("on_network_error", [error])

    def on_clear_network_error(self):
        self.send_message("on_clear_network_error")

    def sync_started(self):
        self.send_message("sync_started")

    def sync_stopped(self):
        self.send_message("sync_stopped")

    def open_webfm(self):
        self.send_message("open_webfm")

    def sync_status_changed(self, status, substatus,
                            local_events_count, remote_events_count,
                            fs_events_count, events_erased):
        self.send_message("sync_status_changed", [status, substatus,
                                                  local_events_count,
                                                  remote_events_count,
                                                  fs_events_count,
                                                  events_erased])

    def on_file_moved(self, old_file, new_file):
        self.send_message("on_file_moved", [old_file, new_file])

    def init_file_list(self, file_list):
        self.send_message("init_file_list", [file_list])

    def download_link_handler(self, link):
        self.send_message("download_link_handler", [link])

    def share_path_failed(self, path):
        self.send_message("share_path_failed", [path])

    def show(self):                                     # TODO rename???
        self.send_message("show")

    def new_language(self, language):
        self.send_message("new_language", [language])

    def exit_request(self):
        self.send_message("exit_request")

    def nodes_info(self, node_info):
        self.send_message("nodes_info", [node_info])

    def upload_size_changed(self, size):
        self.send_message("upload_size_changed", [size])

    def download_size_changed(self, size):
        self.send_message("download_size_changed", [size])

    def sync_dir_size_changed(self, size):
        self.send_message("sync_dir_size_changed", [size])

    def set_config(self, config):
        self.send_message("set_config", [config])

    def open_webfm_window(self, res):
        self.send_message("open_webfm_window", [res])

    def is_wiping_all(self):
        self.send_message("is_wiping_all")

    def wiped_all(self):
        self.send_message("wiped_all")

    def autologin(self, is_silent):
        self.send_message("autologin", [is_silent])

    def long_paths_ignored(self, long_paths):
        self.send_message("long_paths_ignored", [long_paths])

    def license_type_changed(self, license_type):
        self.send_message("license_type_changed", [license_type])

    def restart_me(self):
        self.send_message("restart_me")

    def revert_failed(self, failed_uuids):
       self.send_message("revert_failed", [failed_uuids])

    def connected_nodes_changed(self, nodes_num):
        self.send_message("connected_nodes_changed", [nodes_num])

    def signalserver_address(self, address):
        self.send_message("signalserver_address", [address])

    def service_exited(self):
        self.send_message("service_exited")

    def new_notifications_count(self, count):
        self.send_message("new_notifications_count", [count])
