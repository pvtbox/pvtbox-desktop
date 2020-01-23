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

import glob
import logging.config
from logging.handlers import RotatingFileHandler
import logging
import os
import sys
import time
import errno
from threading import RLock
from os.path import split, join

from common.utils import make_dirs, get_bases_filename
from common.config import load_config
from __update_branch import __update_branch__


DEFAULT_LOGS_COUNT = 19
DEFAULT_COPIES_LOGS_COUNT = 5
VERBOSE = logging.DEBUG // 2

root_directory = ''
console_logging_disabled = False

set_verbose = __update_branch__ != 'release'


class VerboseLogger(logging.getLoggerClass()):
    def __init__(self, name, level=logging.NOTSET):
        super().__init__(name, level)

        logging.addLevelName(VERBOSE, "VERBOSE")

    def verbose(self, msg, *args, **kwargs):
        if self.isEnabledFor(VERBOSE):
            self._log(VERBOSE, msg, args, **kwargs)


class EconoRotatingFileHandler(RotatingFileHandler):

    def __init__(self, filename, maxBytes=0, logsCount=1,
                 file_name_prefix=''):
        self._logs_count = logsCount
        self._old_logs_count = self._logs_count
        self._enabled = True
        self._logging_lock = RLock()
        RotatingFileHandler.__init__(self, filename,
                                     maxBytes=maxBytes,
                                     backupCount=1,
                                     encoding='utf-8')
        self.maxBytes = maxBytes
        self._file_name_prefix = file_name_prefix

    def emit(self, record):
        if self._enabled:
            RotatingFileHandler.emit(self, record)

    def flush(self):
        try:
            RotatingFileHandler.flush(self)
        except EnvironmentError as e:
            if e.errno == errno.ENOSPC:
                self.disable_logging()
            else:
                raise

    def doRollover(self):
        old_filename = self.baseFilename
        self.baseFilename = get_bases_filename(
            root_directory,
            time.strftime(self._file_name_prefix + '%Y%m%d_%H%M%S.log'))
        if self.baseFilename == old_filename:
            # log is full in 1 second
            self.baseFilename = self.baseFilename[:-4] + '_1.log'
        try:
            # RotatingFileHandler can't rename file because we changed
            # self.baseFilename. Now it checks file existence.
            # But realization may change, so do try - except
            RotatingFileHandler.doRollover(self)
        except Exception:
            self.baseFilename = old_filename
        self._clear_old_logs()

    def disable_logging(self, clear_old=True):
        with self._logging_lock:
            self._enabled = False
            if clear_old:
                self._logs_count = 3
            self.doRollover()

    def enable_logging(self):
        with self._logging_lock:
            self._enabled = True
            self._logs_count = self._old_logs_count
            self.doRollover()

    def set_economode(self):
        with self._logging_lock:
            self._logs_count = 3
            self._clear_old_logs()

    def set_max_bytes(self, maxBytes):
        with self._logging_lock:
            self.maxBytes = maxBytes

    def clear_old_logs(self):
        self._clear_old_logs()

    def _clear_old_logs(self):
        old_logs = sorted(glob.glob(get_bases_filename(
            root_directory,
            self._file_name_prefix +
            '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
            '_[0-9][0-9][0-9][0-9][0-9][0-9]*.log')),
            reverse=True)[self._logs_count:]
        try:
            list(map(os.remove, old_logs))
        except Exception:
            pass


class ConsoleFilter(logging.Filter):

    def filter(self, record):
        return not console_logging_disabled


def set_economode(logger, use_root=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.set_economode()
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.set_economode()


def disable_file_logging(logger, use_root=True, clear_old=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.disable_logging(clear_old)
            handler.close()
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.disable_logging(clear_old)
            handler.close()


def enable_file_logging(logger, use_root=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.enable_logging()
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.enable_logging()


def set_max_log_size_mb(logger, size, use_root=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.set_max_bytes(size * 1024 * 1024)
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.set_max_bytes(size * 1024 * 1024)


def clear_old_logs(logger, use_root=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.clear_old_logs()
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.clear_old_logs()


def do_rollover(logger, use_root=True):
    for handler in logger.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.doRollover()
    if not use_root:
        return

    for handler in logger.root.handlers:
        if isinstance(handler, EconoRotatingFileHandler):
            handler.doRollover()

def set_root_directory(new_root):
    from common.file_path import FilePath
    global root_directory

    root_directory = FilePath(new_root).longpath


def enable_console_logging(enable):
    global console_logging_disabled

    console_logging_disabled = not enable


def logging_setup(loglevel, logfilename=None, copies_logging=True):
    """
    Configures logging module

    @param loglevel Log level to be used [str]
    @param logfilename Name of file to save log into [str]
    """

    if set_verbose and loglevel == 'DEBUG':
        loglevel = VERBOSE

    config = load_config()
    set_root_directory(config.sync_directory)

    copies_file_prefix = 'copies_'
    if not logfilename:
        logfilename = time.strftime('%Y%m%d_%H%M%S.log')
        logfilename = get_bases_filename(root_directory, logfilename)

    copies_logs = sorted(glob.glob(get_bases_filename(
        root_directory,
        copies_file_prefix +
        '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
        '_[0-9][0-9][0-9][0-9][0-9][0-9]*.log')),
        reverse=True)
    if copies_logs:
        copies_filename = copies_logs[0]
    else:
        log_dir, log_file = split(logfilename)
        copies_filename = join(log_dir, copies_file_prefix + log_file)

    cfg = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'logfile': {
                'format':
                    '[%(asctime)s %(levelname)s %(name)s:%(lineno)d] %(threadName)s(%(thread)d): %(message)s',  # noqa
            },
            'console': {
                'format':
                    '[%(asctime)s %(levelname)s %(name)s:%(lineno)d] %(threadName)s(%(thread)d): %(message)s',  # noqa
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'console',
                'stream': sys.stdout,
                'level': loglevel,
                'filters': ['console_filter'],
            },
            'file': {
                'formatter': 'logfile',
                'class': 'common.logging_setup.EconoRotatingFileHandler',
                'filename': logfilename,
                'logsCount': DEFAULT_LOGS_COUNT,
                'level': loglevel,
            },
        },
        'filters': {
            'console_filter': {
                '()': ConsoleFilter
            }
        },
        'loggers': {
            # for any logger
            '': {
                'handlers': ['console', 'file', ],
                'level': loglevel,
            },
        },
    }

    if copies_logging:
        cfg['handlers']['copies_file'] = {
                'formatter': 'logfile',
                'class': 'common.logging_setup.EconoRotatingFileHandler',
                'filename': copies_filename,
                'logsCount': DEFAULT_COPIES_LOGS_COUNT,
                'file_name_prefix': copies_file_prefix,
                'level': loglevel,
            }
        cfg['loggers']['copies_logger'] = {
                'handlers': ['copies_file', ],
                'level': loglevel,
                'propagate': False,
            }

    make_dirs(logfilename, is_folder=False)

    logging.raiseExceptions = False

    logging.config.dictConfig(cfg)
