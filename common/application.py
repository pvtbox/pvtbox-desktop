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


class Application(object):
    __instance = None
    # Application start timestamp used in statistics reporting
    _app_start_ts = time.time()
    _instance_class = None

    @classmethod
    def set_instance_class(cls, instance_class):
        cls._instance_class = instance_class

    @classmethod
    def __get_instance(cls):
        if not cls.__instance:
            cls.__instance = cls._instance_class()

        return cls.__instance

    @classmethod
    def start(cls, args):
        cls.__get_instance().start(cls._app_start_ts, args)
        print('Application start returning')

    @classmethod
    def exit(cls):
        return cls.__get_instance().exit()

    @classmethod
    def show_tray_notification(cls, text, title=""):
        return cls.__instance.show_tray_notification(text=text,
                                                     title=title)

    @classmethod
    def save_to_clipboard(cls, text):
        return cls.__instance.save_to_clipboard(text)

    @classmethod
    def request_to_user(cls, text, buttons=("Yes", "No"), title="",
                        close_button_index=-1, close_button_off=False,
                        details=''):
        return cls.__instance.request_to_user(
            text=text, buttons=buttons, title=title,
            close_button_index=close_button_index,
            close_button_off=close_button_off,
            details=details)
