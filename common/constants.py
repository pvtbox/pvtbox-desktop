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
import sys


class _const:
    class ConstError(TypeError):
        pass

    def __setattr__(self, name, value):
        if hasattr(self, '_finalized'):
            raise self.ConstError(
                "Can't declare const(%s) outside the constants module" % name)

        if hasattr(self, name):
            raise self.ConstError("Can't rebind const(%s)" % name)

        self.__dict__[name] = value

    def _finalize(self):
        self._finalized = True


inst = _const()

inst.UNKNOWN_LICENSE = 10
inst.FREE_LICENSE = 1
inst.FREE_TRIAL_LICENSE = 2
inst.PAYED_PROFESSIONAL_LICENSE = 3
inst.PAYED_BUSINESS_LICENSE = 4
inst.PAYED_BUSINESS_ADMIN_LICENSE = 5
inst.license_names = {
    inst.UNKNOWN_LICENSE: "Unknown license",
    inst.FREE_LICENSE: "Free",
    inst.FREE_TRIAL_LICENSE: "Free 14-days trial",
    inst.PAYED_PROFESSIONAL_LICENSE: "Pro account",
    inst.PAYED_BUSINESS_LICENSE: "Business account",
    inst.PAYED_BUSINESS_ADMIN_LICENSE: "Business (Admin) account",
}

inst.STATUS_WAIT = 0
inst.STATUS_PAUSE = 1
inst.STATUS_IN_WORK = 2
inst.STATUS_INIT = 3
inst.STATUS_DISCONNECTED = 4
inst.STATUS_INDEXING = 5
inst.STATUS_LOGGEDOUT = 6

inst.SUBSTATUS_SYNC = 0
inst.SUBSTATUS_SHARE = 1
inst.SUBSTATUS_APPLY = 2

inst.SS_STATUS_SYNCING = 3
inst.SS_STATUS_SYNCED = 4
inst.SS_STATUS_LOGGEDOUT = 5
inst.SS_STATUS_PAUSED = 8
inst.SS_STATUS_INDEXING = 9
inst.SS_STATUS_CONNECTING = 10

inst.UPDATER_STATUS_UNKNOWN = 0
inst.UPDATER_STATUS_DOWNLOADING = 1
inst.UPDATER_STATUS_READY = 2
inst.UPDATER_STATUS_UP_TO_DATE = 3
inst.UPDATER_STATUS_ACTIVE = 4
inst.UPDATER_STATUS_INSTALLED = 5
inst.UPDATER_STATUS_DOWNLOAD_ERROR = 6
inst.UPDATER_STATUS_CHECK_ERROR = 7
inst.UPDATER_STATUS_INSTALL_ERROR = 8
inst.UPDATER_STATUS_INSTALLING = 9

inst.DELETE = 0
inst.MOVE = 1
inst.CREATE = 2
inst.MODIFY = 3
inst.HIDDEN = 4
inst.DIRECTORY = 0
inst.FILE = 1
inst.event_names = ["DELETE", "MOVE", "CREATE", "MODIFY", "HIDDEN"]
inst.DEBUG = True

inst.REGULAR_URI = 'https://pvtbox.net'
inst.PASSWORD_REMINDER_URI = '{}/?reset-password'
inst.GET_PRO_URI = '{}/pricing'
inst.HELP_URI = '{}/faq'
inst.WEB_FM_URI = '{}'
inst.TERMS_URI = '{}/terms'
inst.PRIVACY_URI = '{}/privacy'
inst.API_URI = '{}/api'
inst.API_EVENTS_URI = '{}/api/events'
inst.API_SHARING_URI = '{}/api/sharing'
inst.API_UPLOAD_URI = '{}/api/upload'

inst.MIN_DIFF_SIZE = 11 * 1024
inst.EMPTY_FILE_HASH = "d41d8cd98f00b204e9800998ecf8427e"

inst.DOWNLOAD_PRIORITY_FILE = 10000
inst.IMPORTANT_DOWNLOAD_PRIORITY = 9500
inst.DOWNLOAD_PRIORITY_WANTED_DIRECT_PATCH = 1000
inst.DOWNLOAD_PRIORITY_REVERSED_PATCH = 100
inst.DOWNLOAD_PRIORITY_DIRECT_PATCH = 10

inst.DOWNLOAD_CHUNK_SIZE = 64 * 1024
inst.DOWNLOAD_PART_SIZE = 1024*1024
inst.SIGNATURE_BLOCK_SIZE = inst.DOWNLOAD_PART_SIZE

inst.PATCH_WAIT_TIMEOUT = 5 * 60
inst.RETRY_DOWNLOAD_TIMEOUT = 1 * 60.0
inst.CONNECTIVITY_ALIVE_TIMEOUT = 30  # seconds

inst.DOWNLOAD_NOT_READY = 0
inst.DOWNLOAD_READY = 1
inst.DOWNLOAD_NO_DISK_ERROR = 2
inst.DOWNLOAD_STARTING = 10
inst.DOWNLOAD_LOADING = 11
inst.DOWNLOAD_FINISHING = 12
inst.DOWNLOAD_FAILED = 13

inst.DB_PAGE_SIZE = 100

inst.DISK_LOW_RED = 100         # Mb
inst.DISK_LOW_ORANGE = 1024     # Mb

inst.NETWORK_WEBRTC_RELAY = 0
inst.NETWORK_WEBRTC_DIRECT = 1
inst.NETWORK_HTTP = 2

inst.RESOURCES = {
    "Darwin": {
        "sync_dir": "folder.png",
        "collaboration": "folder_shared.png",
    },
    "Windows": {
        "sync_dir": "logo.ico",
        "collaboration": "folder_shared.ico",
    },
    "Linux": {
        "sync_dir": "pvtbox.png",
        "collaboration": "folder_shared.png",
    },

}

inst.HIDDEN_FILES = ['desktop.ini', '.DS_Store', 'Icon\r', '.directory', '._*']
inst.HIDDEN_DIRS = ['.pvtbox', '._*']

inst.MAX_PATH_LEN = {
    "Darwin": 4096,
    "Windows": 32000,
    "Linux": 4096
}
inst.MAX_FILE_NAME_LEN = 255

inst.WIN10_NAV_PANE_CLSID = '{07fa4a2b-c86d-4b31-925a-3d15d941f98e}'

inst.FILE_LIST_COUNT_LIMIT = 7

inst.FILE_LINK_SUFFIX = '.pvtbox'

inst._finalize()
sys.modules[__name__] = inst
