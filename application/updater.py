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
import shutil
import zipfile
from tempfile import NamedTemporaryFile
import hashlib
import time
import glob
import os

import requests

import subprocess
from PySide2.QtCore import QObject, Signal
from os.path import isfile, split, join, normpath

from common.application import Application
from common.async_qt import qt_run
from common.utils import get_platform, is_os_64bit, remove_file, \
    get_bases_filename, is_portable, get_application_path, remove_dir, get_cfg_filename
from common.constants import UPDATER_STATUS_ACTIVE, UPDATER_STATUS_READY, \
    UPDATER_STATUS_DOWNLOADING, UPDATER_STATUS_CHECK_ERROR, \
    UPDATER_STATUS_DOWNLOAD_ERROR, UPDATER_STATUS_INSTALL_ERROR, \
    UPDATER_STATUS_INSTALLED, UPDATER_STATUS_UP_TO_DATE, \
    UPDATER_STATUS_INSTALLING
from __version import __version__

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Updater(QObject):
    updater_status_changed = Signal(int)
    update_ready = Signal(bool)
    downloading_update = Signal(int,  # downloaded (Mb)
                                int)  # size (Mb)

    def __init__(self,
                 update_branch,
                 updates_server_addr='https://installer.pvtbox.net',
                 parent=None):
        QObject.__init__(self, parent)
        self._update_ready = False
        self._downloading_update = False
        self._update_file_path = None
        self._md5 = None
        self._status = UPDATER_STATUS_ACTIVE

        self._update_branch = update_branch
        self._updates_server_addr = updates_server_addr

        self._old_updates_cleaned = False
        self._stopped = False

    def emit_status(self):
        self.updater_status_changed.emit(self._status)

    def check_for_update(self):
        logger.info("Checking for update")
        version_file_uri = self._get_version_file_uri()
        logger.debug("Version file uri: %s", version_file_uri)

        self._status = UPDATER_STATUS_CHECK_ERROR
        try:
            response = requests.get(version_file_uri)
        except Exception:
            logger.warning('Version file get request failed')
            self.emit_status()
            return False

        if not response.ok:
            logger.warning('Version file get request not ok')
            self.emit_status()
            return False

        version = response.text.strip()

        if version and version != __version__:
            logger.debug('Available version: %s, current version: %s',
                         version, __version__)
            if not self._old_updates_cleaned:
                self._clean_old_updates()
            self._download_update()
            return True

        logger.info("Updates not found")
        self._status = UPDATER_STATUS_UP_TO_DATE
        self.emit_status()
        return False

    @qt_run
    def install_update(self):
        if not self._update_ready or self._status == UPDATER_STATUS_INSTALLING:
            return False
        self._status = UPDATER_STATUS_INSTALLING
        self.emit_status()
        logger.info('Installing update')
        try:
            assert self._update_file_path and isfile(self._update_file_path)
            logger.debug("self._update_file_path %s", self._update_file_path)
            path, name = split(self._update_file_path)
            old_cwd = os.getcwd()
            os.chdir(path)
            system = get_platform()
            if system == 'Windows':
                from common.config import load_config

                config = load_config()
                root = config.sync_directory
                log_basename = time.strftime('%Y%m%d_%H%M%S.log')
                log_filename = get_bases_filename(root, log_basename)
                if not self._is_ascii(log_filename):
                    log_filename = log_basename
                args = [name, '/verysilent', '/Log={}'.format(log_filename)]
                if is_portable():
                    args.append('/PATH={}'.format(get_application_path()))
                subprocess.Popen(
                    args,
                    creationflags=0x00000200  # CREATE_NEW_PROCESS_GROUP
                    | 0x00000008,  # DETACHED_PROCESS
                    close_fds=True)
            elif system == 'Darwin':
                bundle_path = normpath(join(
                    get_application_path(), '..', '..', '..', '..'))
                logger.debug("bundle_path: %s", bundle_path)
                subprocess.call(['ditto', '-xk', self._update_file_path, bundle_path])
                subprocess.call(['xattr', '-d', '-r', 'com.apple.quarantine', bundle_path])
                logger.debug("Update completed, restart")
                remove_file(get_cfg_filename('lock'))
                if is_portable():
                    launcher_path = normpath(join(bundle_path, "..", "Pvtbox-Mac.command"))
                else:
                    launcher_path = bundle_path
                subprocess.call(['open', launcher_path])
            os.chdir(old_cwd)
            Application.exit()
        except Exception as e:
            logger.warning("Can't install update. Reason: %s", e)
            self._status = UPDATER_STATUS_INSTALL_ERROR
            self.emit_status()
            return False

        self._status = UPDATER_STATUS_INSTALLED
        self.emit_status()
        return True

    def is_update_ready(self):
        return self._update_ready

    def is_downloading_update(self):
        return self._downloading_update

    def _download_update(self):
        if self._downloading_update:
            return False
        update_uri = self._get_update_file_uri()

        self._get_md5(update_uri)

        logger.info("Downloading update %s", update_uri)
        try:
            req = requests.get(update_uri, stream=True, timeout=30)
            if req.status_code == 200:
                self._downloading_update = True
                self._status = UPDATER_STATUS_DOWNLOADING
                self.emit_status()
                self._download_update_job(req)
                return True
        except Exception as e:
            logger.error("Update download error: %s", e)
            pass

        logger.warning("Update download failed")
        self._downloading_update = False
        self._status = UPDATER_STATUS_DOWNLOAD_ERROR
        self.emit_status()
        self._update_ready = False
        self.update_ready.emit(False)
        return False

    @qt_run
    def _download_update_job(self, req):
        if self._stopped:
            return

        logger.debug("Update download")
        os = get_platform()
        if os == 'Windows':
            suffix = '.exe'
        elif os == 'Darwin':
            suffix = '.zip'
        else:
            suffix = ''
        update_file = NamedTemporaryFile(
            prefix='Pvtbox_', suffix=suffix, delete=False)

        size = \
            int(float(req.headers.get('content-length', 0)) / 1024 / 1024) + 1
        downloaded = 0
        checksum = hashlib.md5()
        self.downloading_update.emit(downloaded, size)
        logger.debug("Downloading update, %s of %s", downloaded, size)
        try:
            for chunk in req.iter_content(chunk_size=1024 * 1024):
                if self._stopped:
                    break

                if chunk:  # filter out keep-alive new chunks
                    update_file.write(chunk)
                    checksum.update(chunk)
                    downloaded += 1
                    if not self._stopped:
                        self.downloading_update.emit(downloaded, size)
                        logger.debug("Downloading update, %s of %s", downloaded, size)

        except Exception as e:
            logger.error("Error downloading update %s", e)
            self._status = UPDATER_STATUS_DOWNLOAD_ERROR
            if not self._stopped:
                self.emit_status()
        finally:
            update_file.close()
            if self._stopped:
                return

            success = checksum.hexdigest() == self._md5
            if success:
                logger.debug("Update downloaded successfully, hashsum matches")
                self._update_file_path = update_file.name
                self._status = UPDATER_STATUS_READY
            else:
                logger.warning(
                    "Update download failed: hashsum mismatch, expected: %s, actual: %s",
                    checksum.hexdigest(), self._md5)
                self._status = UPDATER_STATUS_DOWNLOAD_ERROR
                remove_file(update_file.name)
            self.emit_status()
            self._downloading_update = False
            self._update_ready = success
            self.update_ready.emit(success)

    def _get_md5(self, update_uri):
        md5_uri = update_uri + '.md5'
        try:
            response = requests.get(md5_uri)
        except Exception:
            logger.warning('Checksum file get request failed')
            self._md5 = None
            return

        if not response.ok:
            self._md5 = None
            return

        self._md5 = response.text.split('=')[1].strip()

    def _get_version_file_uri(self):
        os = get_platform()
        if os == 'Windows':
            return '{}/{}/win/version'.format(
                self._updates_server_addr,
                self._update_branch,
            )
        elif os == 'Darwin':
            return '{}/{}/osx/version'.format(
                self._updates_server_addr,
                self._update_branch,
            )
        else:
            return ''

    def _get_update_file_uri(self):
        os = get_platform()
        if os == 'Windows':
            machine = 'x64' if is_os_64bit() else 'x86'
            return '{}/{}/win/PvtboxSetup-offline_{}.exe'.format(
                self._updates_server_addr,
                self._update_branch,
                machine,
            )
        elif os == 'Darwin':
            if is_portable():
                return '{}/{}/osx/Pvtbox-portable.app.zip'.format(
                    self._updates_server_addr,
                    self._update_branch,
                )
            else:
                return '{}/{}/osx/Pvtbox.app.zip'.format(
                    self._updates_server_addr,
                    self._update_branch,
                )
        else:
            return ''

    def _clean_old_updates(self):
        logger.debug("Cleaning old updates...")
        os = get_platform()
        if os == 'Windows':
            suffix = '.exe'
        elif os == 'Darwin':
            suffix = '.zip'
        else:
            suffix = ''
        prefix = 'Pvtbox_'
        update_file = NamedTemporaryFile(
            prefix=prefix, suffix=suffix, delete=False)
        prefix_len = update_file.name.rfind(prefix) + len(prefix)
        update_file_template = "{}*{}".format(
            update_file.name[:prefix_len], suffix)
        update_file.file.close()
        old_updates = glob.glob(update_file_template)
        try:
            list(map(remove_file, old_updates))
            self._old_updates_cleaned = True
        except Exception as e:
            logger.warning("Can't clean old updates. Reason %s", e)

    def _is_ascii(self, line):
        return all(map(lambda c: ord(c) < 128, line))

    def stop(self):
        self._stopped = True
