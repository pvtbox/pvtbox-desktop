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
import os
import sys
import socket
import locale
import os.path as op
import shutil
import errno
import platform
import time
from itertools import cycle
from operator import xor
from uuid import uuid4
import unicodedata
import subprocess
import ctypes
import stat
import re
from datetime import datetime
import psutil
import getpass
import codecs
from contextlib import contextmanager
import pickle

import sqlalchemy

from common.constants import SIGNATURE_BLOCK_SIZE, MAX_PATH_LEN, \
    WIN10_NAV_PANE_CLSID, FILE_LINK_SUFFIX

# Setup logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Singleton(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(
                Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


def benchmark(f):
    import time, functools

    @functools.wraps(f)
    def _benchmark(*args, **kw):
        t = time.time()
        try:
            rez = f(*args, **kw)
        except Exception:
            raise
        finally:
            t = time.time() - t
            logger.debug('{0}.{1} time elapsed {2:.8f}'.format(f.__module__, f.__name__, t))
        return rez
    return _benchmark


def get_value_or(value, default):
    return value if value else default


def resource_path(relative_path):
    '''
    Returns absolute path for given resource.

    Acts for launching from code and for launching from build.
    @param relative_path relative resource path [str]
    @return absolute resource path
    '''

    # is pyinstaller build running?
    if hasattr(sys, '_MEIPASS'):
        return op.join(sys._MEIPASS, relative_path)

    return op.join(op.abspath("."), relative_path)


def get_application_path():
    if getattr(sys, 'frozen', False):
        return op.dirname(sys.executable)
    else:
        # parent directory of includes
        return op.dirname(op.dirname(os.path.abspath(__file__)))


def get_executable_path():
    app_path = get_application_path()
    if get_platform() == 'Windows':
        app_path = op.join(app_path, 'pvtbox.exe')
    else:
        return op.join(app_path, 'pvtbox')
    return app_path


def get_dir_size(full_path):
    '''
    Gets size of directory tree starting from full_path
    Parameters
    ----------
    full_path full path to directory [str]

    Returns size in bytes [int]
    -------

    '''
    from common.file_path import FilePath

    start_time = time.time()
    full_path = FilePath(full_path).longpath
    try:
        from os import scandir
    except ImportError:
        from scandir import scandir

    def get_tree_size(path):
        total = 0
        for entry in scandir(path):
            if entry.is_dir(follow_symlinks=False):
                total += get_tree_size(entry.path)
            else:
                total += entry.stat(follow_symlinks=False).st_size
        return total
    result = get_tree_size(full_path)
    logger.info("get_dir_size took %s sec", time.time() - start_time)
    return result


def get_platform():
    """
    Returns OS platform name ('Windows'/'Linux'/'Darwin')

    @return Platform name [str]
    """

    return platform.system()


def get_os_version():
    """
    Returns OS version information.
    For Linux kernel version is returned

    @return OS version [str]
    """

    os_platform = get_platform()
    os_version = None
    if os_platform == 'Darwin':
        os_version = platform.mac_ver()[0]
        os_version = '.'.join(os_version.split('.')[:2])
    elif os_platform == 'Linux':
        try:
            os_version = platform.release().split('-')[0]
        except IndexError:
            pass
    elif os_platform == 'Windows':
        os_version = platform.version()

    return os_version


def is_os_64bit():
    """
    Checks if Windows is 64bit.

    @return [bool]
    """

    return platform.machine().endswith('64')


def get_linux_distro_name_version():
    """
    Returns linux distro name and version

    @return (DISTR_NAME, DISTR_VERSION) [tuple]
    """

    def get_linux_os_release():
        file_name = '/etc/os-release'
        distro_id = None
        try:
            with open(file_name, 'r') as f:
                s = f.read()
                res = re.search(r'PRETTY_NAME="(?P<os_release>.+)"', s)
                if res:
                    distro_id = res.group('os_release')
        except IOError:
            pass
        return distro_id

    distro_name = platform.linux_distribution()[:2]
    if ''.join(distro_name):
        return distro_name

    os_release = get_linux_os_release()
    if os_release:
        return (os_release)

    return ("Unknown linux distro")


def get_userfriendly_os_version():
    """
    Returns OS version with some replacements.
    Windows numeric version is replaced with marketing version.
    Linux kernel version is replaced with distro name/version.

    @return OS version [str]
    """

    # Windows real version <> marketing version match
    _win_versions = {
        ('5', '0'): '2000',
        ('5', '1'): 'XP',
        ('5', '2'): 'XP 64bit',
        ('6', '0'): 'Vista',
        ('6', '1'): '7',
        ('6', '2'): '8',
        ('6', '3'): '8.1',
        ('10', '0'): '10',
    }

    platform = get_platform()
    os_version = get_os_version()
    if not os_version:
        return 'unknown'
    if os_version and platform == 'Windows':
        winver = tuple(os_version.split('.')[:2])
        try:
            return _win_versions[winver]
        except KeyError:
            pass
    if platform == 'Linux':
        result = get_linux_distro_name_version()
        if result:
            return ' '.join(result)

    return os_version


@benchmark
def get_os_name_and_is_server():
    global os_name_value, is_server_value

    if os_name_value is not None and is_server_value is not None:
        return os_name_value, is_server_value

    try:
        system = get_platform()
        if system == 'Windows':
            import wmi
            computer = wmi.WMI()
            os_info = computer.Win32_OperatingSystem()[0]
            logger.info("os_info: %s", os_info)
            sku = os_info.OperatingSystemSKU
            caption = os_info.Caption
            if caption:
                caption = caption.replace('Microsoft', '').strip()
            if not caption:
                caption = _get_os_name()
            # see https://docs.microsoft.com/en-us/windows/desktop/cimwin32prov/win32-operatingsystem
            os_name_value = caption
            is_server_value = sku in (
                7, 8, 9, 10, 12, 13, 14, 15, 17, 19, 20, 21, 22, 23, 24, 25, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38,
                39, 40, 41, 42, 43, 44, 45, 46, 50, 51, 52, 53, 54, 55, 56, 59, 60, 61, 62, 63, 64, 72, 76, 77, 79, 80,
                95, 96, 143, 144, 147, 148)
        elif system == 'Darwin':
            os_name = _get_os_name()
            is_server_value = op.exists('/Applications/Server.app')
            os_name_value = os_name + ' Server' if is_server_value else os_name
        else:   # Linux
            os_name_value = _get_os_name()
            is_server_value = False
        return os_name_value, is_server_value
    except Exception as e:
        logger.error("exception: %s", e)
        return _get_os_name(), False


def get_device_name():
    #device_name = str(socket.gethostname(),
    #                      errors='ignore',
    #                      encoding=LOCALE_ENC)
    global is_daemon
    if is_daemon:
        return "Self-Hosted server node"
    device_name = str(socket.gethostname())
    device_name = device_name.replace('?', '')[:30]
    if not device_name:
        device_name = str('{} {}'.format(
            get_platform(), get_userfriendly_os_version()))
    return device_name


def _get_os_name():
    os_platform = get_platform()
    os_version = get_userfriendly_os_version()

    # Replace name for mac
    if os_platform == 'Darwin':
        os_platform = 'macOS X'

    # Use distro's name/version for linux if any
    if os_platform == 'Linux' and get_linux_distro_name_version():
        return os_version

    return ' '.join((os_platform, os_version))


def hashfile(path, hasher=None, blocksize=65536):
    """
    A function to hash files.

    """
    import hashlib

    if hasher is None:
        hasher = hashlib.md5()

    with open(path, "rb") as f:
        buf = f.read(blocksize)
        while len(buf) > 0:
            hasher.update(buf)
            buf = f.read(blocksize)

    return hasher.hexdigest()


def create_hard_link(src, dst):
    if platform.system() == 'Windows':
        import ctypes
        if not ctypes.windll.kernel32.CreateHardLinkW(dst, src, 0):
            raise OSError
    else:
        os.link(src, dst)


def generate_uuid():
    return str(uuid4()).replace('-', '')


def get_locale(fallback=('en_US', 'UTF-8')):
    '''
    Returns system locale name and encoding (if any).
    Otherwise returns fallback encoding specified

    @param fallback
        Name of system locale and encoding to be used as falback [tuple]
    @return Name of system locale and encoding [tuple]
    '''

    try:
        result = locale.getdefaultlocale()
    except ValueError:
        result = None

    if not result or result == (None, None):
        logger.warning(
            "Failed to determine system locale name and encoding. "
            "Falling back to %s %s", *fallback)
        return fallback

    logger.info("System locale is %s, encoding is %s", *result)
    return result


def get_available_languages():
    translations_folder = resource_path('language')
    if not op.exists(translations_folder):
        return dict()

    return dict(
        (f.split('.')[0], op.join(translations_folder, f))
        for f in os.listdir(translations_folder)
        if op.isfile(op.join(translations_folder, f))
    )


def get_default_lang():
    return LOCALE_NAME.split("_")[0]


def _getEnvironmentVariable(name):
    '''
    Retrieves unicode value for environment variable specified on Windows

    @param name Name of variable to be returned [string]
    @return Variable value [unicode]
    '''

    import ctypes
    name = str(name)  # make sure string argument is unicode
    n = ctypes.windll.kernel32.GetEnvironmentVariableW(name, None, 0)
    if n == 0:
        return None
    buf = ctypes.create_unicode_buffer('\0' * n)
    ctypes.windll.kernel32.GetEnvironmentVariableW(name, buf, n)
    return buf.value


def getenv(name):
    '''
    Returns value of environment variable depending on OS type

    @param name Name of variable to be returned [string]
    @return Variable value [unicode]
    '''

    # Python 2.x on windows
    if os.name == 'nt' and sys.version_info < (3, 0):
        res = _getEnvironmentVariable(name)
    else:
        res = os.getenv(name, None)

    return ensure_unicode(res)


def expanduser(path):
    '''
    Patched version of os.path.expanduser() obtaining environment variables
    using getenv()

    @param path Path to be expanded
    @return Expanded path [unicode]
    '''

    if path[:1] != '~':
        return path
    i, n = 1, len(path)
    while i < n and path[i] not in '/\\':
        i = i + 1

    if 'HOME' in os.environ:
        userhome = getenv('HOME')
    elif 'USERPROFILE' in os.environ and getenv('USERPROFILE'):
        userhome = getenv('USERPROFILE')
    elif 'HOMEPATH' not in os.environ:
        return path
    else:
        try:
            drive = getenv('HOMEDRIVE')
        except KeyError:
            drive = ''
        userhome = op.join(drive, getenv('HOMEPATH'))

    if i != 1:  # ~user
        userhome = op.join(op.dirname(userhome), path[1:i])

    return userhome + path[i:]


def ensure_unicode(value, encoding=None):
    '''
    Converts string to unicode using system locale encoding if necessary

    @param value String to convert [string/unicode]
    @param encoding Name of encoding to be used on unicode conversion.
        System locale encoding by default [str]
    @return String converted to unicode
    '''

    global LOCALE_ENC
    if encoding is None:
        encoding = LOCALE_ENC
    if isinstance(value, str):
        return value
    elif isinstance(value, bytes):
        return str(value, encoding=encoding)
    elif isinstance(value, sqlalchemy.Column):
        # @@ fix to allow convert only string columns
        return value.encode('utf-8').decode(encoding)
    else:
        t = type(value)
        logger.error("Can not convert '%s' to unicode: '%s'", t, value)
        raise ValueError(value)


def ensure_locale_enc(value, encoding=None):
    '''
    Encodes value to locale encoding if necessary.
    Non-unicode values are converted to unicode first to ensure encoding is
    possible

    @param value String to convert [string/unicode]
    @param encoding Name of encoding to be used on unicode conversion.
        System locale encoding by default [str]
    @return String converted to unicode
    '''

    global LOCALE_ENC
    if encoding is None:
        encoding = LOCALE_ENC

    # Convert string values into unicode first (assuming locale encoding)
    if isinstance(value, bytes):
        return ensure_unicode(value, encoding)

    # Encode unicode into locale encoding
    if isinstance(value, str):
        return value.encode('utf-8').decode(
            encoding=encoding, errors='replace')
    else:
        logger.error("Can not convert to locale encoding: '%s'", value)
        raise ValueError("Can not convert to locale encoding")


def _get_dir(dir_parent, dir_basename, create, hidden=False):
    '''
    Returns full path for directory specified as two path components.
    Recursively creates directories if required

    @param dir_parent    Parent of directory to be created [string/unicode]
    @param dir_basename  Directory to be created basename [string/unicode]
    @param create    Flag indicating need to create directory [bool]
    @return Full directory path [unicode] or None
    '''

    # Full directory path
    from common.file_path import FilePath

    dir_path = op.join(
        ensure_unicode(dir_parent),
        ensure_unicode(dir_basename))
    dir_path = FilePath(dir_path).longpath

    # Create root if necessary
    try:
        if create and not op.exists(dir_path):
            logger.debug("Creating directory: '%s'...", dir_path)
            make_dirs(dir_path, True)
            if hidden:
                make_dir_hidden(dir_path)
    except Exception as e:
        logger.error("Failed to create directory '%s' (%s)", dir_path, e)
        return

    return dir_path


def get_data_dir(dir_basename='Pvtbox', create=False, dir_parent=None):
    '''
    Returns default path for program data directory.
    Full path is OS dependent. Creates path if required

    @param dir_basename  Directory to be created basename [string]
    @param create    Flag indicating need to create directory [bool]
    @param dir_parent  Parent of directory to be created [string/unicode]
    @return Full directory path [unicode] or None
    '''

    if not dir_parent:
        if is_portable():
            dir_parent = get_portable_root()
        else:
            dir_parent = expanduser('~')

    return _get_dir(dir_parent, dir_basename, create)


def get_patches_dir(data_dir, create=False):
    from common.file_path import FilePath

    patches_dir = op.join(data_dir, '.pvtbox')
    patches_dir = ensure_unicode(patches_dir)
    patches_dir = FilePath(patches_dir).longpath
    if create and not op.exists(patches_dir):
        try:
            make_dirs(patches_dir, True)
        except Exception as e:
            logger.error("Failed to create directory: '%s' (%s)",
                         patches_dir, e)
    return patches_dir


def get_cfg_dir(dir_basename='.pvtbox', create=False):
    '''
    Returns default path for program configuration directory.
    Full path is OS dependent. Creates path if required

    @param dir_basename  Directory to be created basename [string]
    @param create    Flag indicating need to create directory [bool]
    @return Full directory path [unicode] or None
    '''

    if is_portable():
        return _get_dir(get_portable_root(), dir_basename, create, True)

    cfg_location = get_appdata_dir()
    # Config location has been determined?
    if cfg_location is not None:
        return _get_dir(cfg_location, dir_basename, create, True)


def get_appdata_dir():
    if os.name == 'nt':
        # Windows versions newer than XP
        cfg_location = getenv('LOCALAPPDATA')
        # Seems to be Windows XP
        if cfg_location is None:
            cfg_location = \
                op.join(HOME_DIR, 'Local Settings', 'Application Data')
    # linux/osx
    elif os.name == 'posix':
        cfg_location = HOME_DIR
    else:
        raise NotImplemented("Unsupported platform: {}".format(os.name))
    return cfg_location


def get_cfg_filename(filename):
    '''
    Returns full path for the configuration file to be located in program
    configuration directory. Configuration directory assumed to be existing

    @param filename Configuration file basename [unicode/string]
    @return Full configuration file path [unicode]
    '''

    return op.join(CFG_DIR, ensure_unicode(filename))


def get_bases_filename(data_dir, filename):
    '''
    Returns full path for the configuration file to be located in program
    data bases directory. Data bases directory assumed to be existing

    @param data_dir Application sync folder [unicode/string]
    @param filename Configuration file basename [unicode/string]
    @return Full configuration file path [unicode]
    '''

    return op.join(get_bases_dir(data_dir), ensure_unicode(filename))


def get_downloads_dir(data_dir=None, create=False):
    '''
    Returns full path for directory to temporary save files during downloading

    @param create    Flag indicating need to create directory [bool]
    '''
    if not data_dir:
        data_dir = get_data_dir()
    downloads_dir = op.join('.pvtbox', 'downloads')
    return _get_dir(data_dir, downloads_dir, create)


def get_copies_dir(data_dir=None, create=True):
    '''
    Returns full path for directory to temporary save files during downloading

    @param create    Flag indicating need to create directory [bool]
    '''
    if not data_dir:
        data_dir = get_data_dir()
    copies_dir = op.join('.pvtbox', 'copies')
    return _get_dir(data_dir, copies_dir, create)


def get_signatures_dir(data_dir=None, create=False):
    '''
    Returns full path for directory to temporary save files during downloading

    @param create    Flag indicating need to create directory [bool]
    '''
    if not data_dir:
        data_dir = get_data_dir()
    copies_dir = op.join('.pvtbox', 'signatures')
    return _get_dir(data_dir, copies_dir, create)


def get_temp_dir(data_dir=None, create=False):
    '''
    Returns full path for directory to temporary save files during sync

    @param create    Flag indicating need to create directory [bool]
    '''
    if not data_dir:
        data_dir = get_data_dir()
    tmp_dir = op.join('.pvtbox', 'tmp')
    return _get_dir(data_dir, tmp_dir, create)


def get_next_name(filename):
    '''
    Returns next non-existing name for specified filename
    by adding a non-allocated number, e.g.
    file_name.txt -> file_name(2).txt

    @param filename    Name of file for checking and transform [str/unicode]
    '''
    new_name = ensure_unicode(filename)
    counter = 2
    while op.exists(new_name):
        root, ext = op.splitext(filename)
        new_name = "{}({}){}".format(ensure_unicode(root),
                                      counter,
                                      ensure_unicode(ext))
        counter += 1
    return new_name


def normpath(path):
    return unicodedata.normalize('NFC', op.normpath(ensure_unicode(path)))


def unified_path(path):
    return op.normcase(normpath(path))


def same_path(path1, path2):
    return unified_path(path1) == unified_path(path2)


def create_empty_file(filename):
    '''
    Creates the empty file specified.
    Rewrites previous file if necessary.

    @param filename Name of empty file to be created [string/unicode]
    @return Operation success flag [bool]
    '''

    filename = ensure_unicode(filename)
    logger.debug("Creating empty file '%s'...", filename)

    try:
        with open(filename, 'wb'):
            pass
    except Exception as e:
        logger.error("Failed to create empty file '%s' (%s)", filename, e)
        return False
    os.chmod(filename, stat.S_IRWXU)
    return True


def touch(filename):
    '''
    Creates the file specified if necessary.
    Updates its access and modified time

    @param filename Name of file to be touched [string/unicode]
    @return Operation success flag [bool]
    '''

    filename = ensure_unicode(filename)
    logger.debug("Touching '%s'...", filename)
    try:
        with open(filename, 'a'):
            os.utime(filename, None)
    except Exception as e:
        logger.error("Failed to touch file '%s' (%s)", filename, e)
        return False
    return True


def open_path(path):
    if platform.system() == "Windows":
        from common.file_path import FilePath

        os.startfile(FilePath(path).longpath)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def make_dirs(abs_filepath, is_folder=False):
    _dirname = abs_filepath if is_folder else op.dirname(abs_filepath)
    if not op.exists(_dirname):
        logger.debug("Creating directory '%s'...", _dirname)
        try:
            os.makedirs(_dirname)
            platform = get_platform()
            if platform != 'Windows':
                os.chmod(_dirname, stat.S_IRWXU)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


def mkdir(abs_filepath):
    _dirname = abs_filepath
    if not op.exists(_dirname):
        logger.debug("Creating directory '%s'...", _dirname)
        try:
            os.mkdir(_dirname)
            platform = get_platform()
            if platform != 'Windows':
                os.chmod(_dirname, stat.S_IRWXU)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise


def remove_file(abs_filepath):
    abs_filepath = ensure_unicode(abs_filepath)
    try:
        os.chmod(abs_filepath, stat.S_IRWXU)
        os.remove(abs_filepath)
    except OSError as e:
        if e.errno != errno.ENOENT:  # file or directory does not exist
            raise


def remove_dir(
        abs_path, suppress_not_exists_exception=True, ignore_errors=False):

    logger.debug("Removing directory '%s'", abs_path)
    abs_path = ensure_unicode(abs_path)

    if suppress_not_exists_exception and not op.exists(abs_path):
        logger.warning(
            "Directory '%s' is not exist", abs_path)
        return

    try:
        assert op.exists(abs_path) and op.isdir(abs_path)
        if op.islink(abs_path):
            os.unlink(abs_path)  # @@ TODO: Check if this works for MS Windows
        else:
            os.chmod(abs_path, stat.S_IRWXU)
            shutil.rmtree(abs_path, ignore_errors=ignore_errors)
    except OSError as e:
        if not suppress_not_exists_exception or e.errno != errno.ENOENT:
            raise


def is_file_changing(filename):
    """ Checks if file is ready for processing (is file copying finished)
        using sleep(1) on osx
    """
    try:
        size_before = os.stat(filename).st_size
        time.sleep(1)
        size_after = os.stat(filename).st_size
    except Exception:
        return True

    if size_before == size_after:
        return False
    return True


def get_file_size(filename):
    try:
        return os.stat(filename).st_size
    except Exception:
        return 0


@benchmark
def get_filelist(root_dir, exclude_dirs=(), exclude_files=()):
    '''
    Returns list of files containing in the directory

    @param root_dir Root directory to search for files in [unicode]
    @param exclude_dirs
        List of directories not to be included (relative to root_dir) [list]
    @param exclude_files
        List of files not to be included [list]
    @return List of files (full paths including root_dir itself) [list]
    '''
    from common.file_path import FilePath
    logger.debug("Exclude dirs %s", exclude_dirs)
    exclude_dirs = map(ensure_unicode, exclude_dirs)
    # Make paths absolute
    exclude_dirs = list(map(
        lambda p: FilePath(op.join(root_dir, p)).longpath, exclude_dirs))
    exclude_files = list(map(FilePath, exclude_files))

    filelist = []

    logger.debug("Obtaining list of files for '%s'...", root_dir)

    for root, dirs, files in os.walk(root_dir, followlinks=True):
        root = FilePath(root).longpath
        exclude_dirs_status = map(
            lambda ed: FilePath(root) in FilePath(ed), exclude_dirs)
        if any(exclude_dirs_status):
            continue
        for filename in files:
            if FilePath(filename) not in exclude_files:
                full_fn = op.join(root, filename)
                full_fn = FilePath(full_fn).longpath
                filelist.append(full_fn)

    logger.info("Found %s file(s) in '%s'", len(filelist), root_dir)
    return filelist


@benchmark
def get_dir_list(root_dir, exclude_dirs=()):
    from common.file_path import FilePath

    logger.debug("exclude_dirs %s", exclude_dirs)
    exclude_dirs = map(ensure_unicode, exclude_dirs)
    root_dir = FilePath(root_dir).longpath
    exclude_dirs = list(map(lambda p: FilePath(op.join(root_dir, p)).longpath,
                       exclude_dirs))

    dirs = list()
    for fullpath, directory, files in os.walk(root_dir, followlinks=True):
        if fullpath == root_dir:
            continue
        exclude_dirs_status = map(
            lambda ed: FilePath(fullpath) in FilePath(ed), exclude_dirs)
        if any(exclude_dirs_status):
            continue
        dirs.append(FilePath(fullpath).longpath)
    return dirs


@benchmark
def get_files_dir_list(root_dir, exclude_dirs=(), exclude_files=()):
    from common.file_path import FilePath

    exclude_dirs = map(ensure_unicode, exclude_dirs)
    exclude_files = list(map(ensure_unicode, exclude_files))
    root_dir = FilePath(root_dir).longpath
    exclude_dirs = list(map(lambda p: FilePath(op.join(root_dir, p)).longpath,
                            exclude_dirs))
    logger.debug("exclude_dirs %s", exclude_dirs)

    dirs = list()
    filelist = list()

    for fullpath, directory, files in os.walk(root_dir, followlinks=True):
        exclude_dirs_status = map(
            lambda ed: FilePath(fullpath) in FilePath(ed), exclude_dirs)
        if any(exclude_dirs_status):
            continue

        filelist.extend([FilePath(op.join(fullpath, f)).longpath
                         for f in files if f not in exclude_files])
        if fullpath != root_dir:
            dirs.append(FilePath(fullpath).longpath)

    return dirs, filelist


def convert_bytes(bytes):
    bytes_str = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while bytes >= 1024.0 and i < 4:
        bytes /= 1024.0
        i += 1
    return '{:.2f} {}'.format(bytes, bytes_str[i])


def format_with_units(
        value, precision=1, units_scale=1024,
        units=['B', 'KB', 'MB', 'GB', 'TB']):
    i = 0
    while value >= units_scale and i < len(units) - 1:
        value /= float(units_scale)
        i += 1
    format_string = '{:.%sf} {}' % precision
    return format_string.format(value, units[i])


def is_in_system_startup(identifier=None):
    if is_portable():
        return False
    system = get_platform()
    if system == 'Darwin':
        plist = op.join(HOME_DIR, 'Library/LaunchAgents/net.pvtbox.plist')
        return op.isfile(plist)
    elif system == 'Windows':
        from PySide2.QtCore import QSettings

        if identifier is None:
            identifier = 'Pvtbox'

        settings = QSettings(
            'HKEY_CURRENT_USER\\Software\\Microsoft\\'
            'Windows\\CurrentVersion\\Run',
            QSettings.NativeFormat)
        return settings.value(identifier, None) is not None
    else:
        if identifier is None:
            identifier = 'pvtbox.desktop'
        path = op.join(HOME_DIR, '.config', 'autostart', identifier)
        return op.isfile(path)


def add_to_system_startup():
    if is_portable():
        return
    system = get_platform()
    if system == 'Darwin':
        plist = op.join(HOME_DIR, 'Library/LaunchAgents/net.pvtbox.plist')
        with open(plist, 'w') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"')
            f.write(' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n')
            f.write('<plist version="1.0">\n')
            f.write('\t<dict>\n')
            f.write('\t\t<key>Label</key>\n')
            f.write('\t\t<string>net.pvtbox</string>\n')
            f.write('\t\t<key>ProgramArguments</key>\n')
            f.write('\t\t<array>\n')
            f.write('\t\t\t<string>open</string>\n')
            f.write('\t\t\t<string>-a</string>\n')
            f.write('\t\t\t<string>Pvtbox</string>\n')
            f.write('\t\t</array>\n')
            f.write('\t\t<key>ProcessType</key>\n')
            f.write('\t\t<string>Interactive</string>\n')
            f.write('\t\t<key>RunAtLoad</key>\n')
            f.write('\t\t<true/>\n')
            f.write('\t</dict>\n')
            f.write('</plist>\n')
    elif system == 'Windows':
        from PySide2.QtCore import QSettings

        settings = QSettings(
            'HKEY_CURRENT_USER\\Software\\Microsoft\\'
            'Windows\\CurrentVersion\\Run',
            QSettings.NativeFormat)
        settings.setValue('Pvtbox', get_executable_path())
        settings.sync()
    else:
        make_dirs(op.join(HOME_DIR, '.config', 'autostart'), is_folder=True)
        path = op.join(HOME_DIR, '.config', 'autostart', 'pvtbox.desktop')
        iconPath = '/usr/share/icons/hicolor/128x128/apps/pvtbox.png\n'
        with open(path, 'w') as f:
            f.write('[Desktop Entry]\n')
            f.write('Type=Application\n')
            f.write('Name=Pvtbox\n')
            f.write('Exec=' + get_executable_path() + '\n')
            f.write("Icon=" + iconPath)
            f.write('Hidden=false\n')
            f.write('NoDisplay=false\n')
            f.write('Categories=Network;FileTransfer;P2P\n')
            f.write('Keywords=network;file;transfer;p2p;sync\n')


def remove_from_system_startup(identifier=None):
    if is_portable():
        return
    system = get_platform()
    if system == 'Darwin':
        plist = op.join(HOME_DIR, 'Library/LaunchAgents/net.pvtbox.plist')
        remove_file(plist)
    elif system == 'Windows':
        from PySide2.QtCore import QSettings

        if identifier is None:
            identifier = 'Pvtbox'

        settings = QSettings(
            'HKEY_CURRENT_USER\\Software\\Microsoft\\'
            'Windows\\CurrentVersion\\Run',
            QSettings.NativeFormat)
        settings.remove(identifier)
        settings.sync()
    else:
        if identifier is None:
            identifier = 'pvtbox.desktop'
        path = op.join(HOME_DIR, '.config', 'autostart', identifier)
        remove_file(path)


def _get_icon_file(system, resource_name, icons_path):
    from common.constants import RESOURCES

    try:
        icon_file = op.join(icons_path, RESOURCES[system][resource_name])
        logger.debug("Icon file %s", icon_file)
    except KeyError:
        logger.warning("Unknown resource '%s' for platform '%s'",
                       resource_name, system)
        return ""
    return icon_file


def _find_icon_file(file_to_search, pattern, icon_file):
    import re

    try:
        with open(file_to_search, 'r') as f:
            cnt = ' '.join(f.read().split())
        match = re.search(pattern, cnt)
        if not match:
            return False
        else:
            return match.group('PATH') == icon_file
    except Exception:
        return False


def set_custom_folder_icon(resource_name, root_dir="",  folder="",
                           icon_index=0):
    from common.file_path import FilePath

    if root_dir:
        folder_path = op.join(root_dir, folder)
    else:
        folder_path = folder
    folder_path = unified_path(folder_path)
    folder_path = FilePath(folder_path).longpath

    if not op.exists(folder_path):
        logger.warning("Attempt to set custom icon for non existent folder")
        return

    icons_path = get_icons_path()
    logger.debug("Icons path '%s'", icons_path)

    system = get_platform()
    icon_file = _get_icon_file(system, resource_name, icons_path)
    if not icon_file:
        return

    if system == 'Darwin':
        from Cocoa import NSWorkspace
        from Cocoa import NSImage

        image_file = icon_file

        image = NSImage.alloc().initWithContentsOfFile_(image_file)
        NSWorkspace.sharedWorkspace().setIcon_forFile_options_(
            image, folder_path, 0)

    # Do windows-specific things
    elif system == 'Windows':
        import win32file
        import win32con

        # generate desktop.ini
        text = """[.ShellClassInfo]
                    IconResource=path_to_icon,0
                    [ViewState]
                    Mode=
                    Vid=
                    FolderType=Generic"""
        path_to_icon = icon_file
        text = text.replace("path_to_icon", path_to_icon)

        ini_path = op.join(folder_path, "desktop.ini")
        logger.info("ini_path: %s", ini_path)

        remove_file(ini_path)
        try:
            logger.debug("Writing '%s'...", ini_path)
            with open(ini_path, "w", encoding='utf-8') as f:
                f.write(text)

            win32file.SetFileAttributesW(
                ini_path,
                win32con.FILE_ATTRIBUTE_HIDDEN |
                win32con.FILE_ATTRIBUTE_SYSTEM)
            win32file.SetFileAttributesW(
                folder_path, win32con.FILE_ATTRIBUTE_READONLY)
        except Exception as e:
            logger.warning(
                "Can't create '%s' (%s)", ini_path, e)

    # Do linux-specific things
    elif system == "Linux":
        # for GNOME
        try:
            subprocess.Popen([
                "gvfs-set-attribute",
                "-t", "string",
                folder_path,
                "metadata::custom-icon", "file://{}".format(icon_file)])
            # .. and Thunar/XFCE
            subprocess.Popen([
                "gvfs-set-attribute",
                "-t", "stringv",
                folder_path,
                "metadata::emblems", icon_file])
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

        # for KDE
        entry_file = op.join(folder_path, ".directory")
        logger.debug("Desktop entry file: %s", entry_file)
        try:
            with open(entry_file, "w") as f:
                f.write("[Desktop Entry]\n"
                        "Icon={}\n"
                        "Type=Directory".format(icon_file))
        except Exception as e:
            logger.warning(
                "Can't create entry file '%s' (%s)", entry_file, e)

    else:
        pass

    if system != 'Windows':
        os.chmod(folder_path, stat.S_IRWXU)


def reset_custom_folder_icon(root_dir="",  folder="", resource_name=""):
    from common.file_path import FilePath

    if root_dir:
        folder_path = op.join(root_dir, folder)
    else:
        folder_path = folder
    folder_path = unified_path(folder_path)
    folder_path = FilePath(folder_path).longpath

    if not op.exists(folder_path):
        logger.warning("Attempt to reset custom icon for non existent folder")
        return

    if resource_name and \
            not _check_custom_folder_icon(resource_name, folder_path):
        logger.debug("Check custom icon for %s returned False", resource_name)
        return

    system = get_platform()

    if system == 'Darwin':
        from Cocoa import NSWorkspace

        NSWorkspace.sharedWorkspace().setIcon_forFile_options_(
            None, folder_path, 0)

    elif system == 'Windows':
        ini_path = op.join(folder_path, "desktop.ini")
        remove_file(ini_path)

    # Do linux-specific things
    elif system == 'Linux':
        # for GNOME
        try:
            subprocess.Popen([
                "gvfs-set-attribute",
                "-t", "stringv",
                folder_path,
                "metadata::custom-icon", "''"])
            # .. and Thunar/XFCE
            subprocess.Popen([
                "gvfs-set-attribute",
                "-t", "stringv",
                folder_path,
                "metadata::emblems", "''"])
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

        # for KDE
        entry_file = op.join(folder_path, ".directory")
        remove_file(entry_file)

    else:
        pass

    os.chmod(folder_path, stat.S_IRWXU)


def _check_custom_folder_icon(resource_name, folder_path=""):
    icons_path = get_icons_path()
    logger.debug("Icons path '%s'", icons_path)

    system = get_platform()

    icon_file = _get_icon_file(system, resource_name, icons_path)
    if not icon_file:
         return False

    if system == 'Darwin':
        return True

    # Do windows-specific things
    elif system == 'Windows':

        pattern = r'\bIconResource=(?P<PATH>.+?),'
        ini_path = op.join(folder_path, "desktop.ini")
        logger.debug("ini_path: %s", ini_path)
        return _find_icon_file(ini_path, pattern, icon_file)

    # Do linux-specific things
    elif system == "Linux":
        # for GNOME
        try:
            info_proc = subprocess.Popen([
                "gvfs-info",
                "-a", "metadata::custom-icon",
                folder_path], stdout=subprocess.PIPE)
            output = info_proc.stdout.read().decode("utf-8")
            logger.debug("gvfs-info output %s", output)
            ex = r"metadata::custom-icon:\s+file://(?P<icon_file>{})" \
                 .format(icon_file)
            s = re.search(ex, output)
            if s and s.group('icon_file') == icon_file:
                return True

            # .. and Thunar/XFCE
            info_proc = subprocess.Popen([
                "gvfs-info",
                "-a", "metadata::emblems",
                folder_path], stdout=subprocess.PIPE)
            output = info_proc.stdout.read().decode("utf-8")
            logger.debug("gvfs-info output %s", output)
            ex = r"metadata::emblems:\s+\[.*?(?P<icon_file>{})" \
                 .format(icon_file)
            s = re.search(ex, output)
            if s and s.group('icon_file') == icon_file:
                return True
        except OSError as e:
            if e.errno != errno.ENOENT:
                return False

        # for KDE
        entry_file = op.join(folder_path, ".directory")
        logger.debug("Desktop entry file: %s", entry_file)
        pattern = r'\bIcon=(?P<PATH>.+?)\b'
        return _find_icon_file(entry_file, pattern, icon_file)

    else:
        pass


def reset_all_custom_folder_icons(root_dir):
    for folder in os.listdir(root_dir):
        if op.isdir(op.join(root_dir, folder)):
            try:
                reset_custom_folder_icon(
                    root_dir=root_dir, folder=folder)
            except Exception as e:
                logger.warning("Can't reset folder icon for %s. Reason %s",
                               folder, e)


def get_icons_path():
    path = get_application_path()
    if getattr(sys, 'frozen', False):
        system = get_platform()
        if system == 'Darwin':
            icons_dir = '../../../Resources'
        elif system == 'Windows':
            icons_dir = 'Icons'
        else:
            # FIXME: put Linux icons path here
            icons_dir = '/usr/share/icons/hicolor/128x128/apps'
    else:
        icons_dir = 'application/ui/images'
    icons_path = ensure_unicode(op.join(path, icons_dir))
    logger.debug("Path to icons %s", icons_path)
    return icons_path


def get_service_start_command():
    if getattr(sys, 'frozen', False):
        path = get_application_path()
        system = get_platform()
        if system == 'Darwin':
            service = 'Pvtbox-Service'
            command = op.join(
                path, service)
        elif system == 'Windows':
            service = 'pvtbox-service.exe'
            command = op.join(path, service)
        else:
            command = 'pvtbox-service'
        command = [command]
    else:
        command = ['python', 'serv.py']

    return command


def is_launched_from_code():
    return not getattr(sys, 'frozen', False)


def create_shortcuts(sync_dir):
    '''
    Creates shortcuts for sync directory on Windows and MacOS

    @param sync_dir Path to sync directory [unicode]
    '''

    set_custom_folder_icon("sync_dir", "", sync_dir)

    if is_portable():
        return

    system = get_platform()

    # Do windows-specific things
    if system == 'Windows':
        import pythoncom
        from win32com.shell import shell
        from common.file_path import FilePath
        from win32api import GetShortPathName

        pythoncom.CoInitialize()
        versions_to_apply_nav_pane = ['10']

        def create_shortcut(sc_path, sc_target_path):
            '''
            Creates shortcut placed in location specified pointing to
            path specified

            @param sc_path  Shortcut location path [string]
            @param sc_target_path  Shortcut target path [string]
            @return Operation success flag [bool]
            '''

            logger.debug("Creating shortcut '%s'...", sc_path)

            try:
                shortcut = pythoncom.CoCreateInstance(
                    shell.CLSID_ShellLink,
                    None,
                    pythoncom.CLSCTX_INPROC_SERVER,
                    shell.IID_IShellLink
                )
                try:
                    shortcut.SetPath(sc_target_path)
                except Exception as e:
                    if e[0] == -2147024809:
                        logger.warning(
                            "Cannot use normal pathname, using short")
                        shortcut.SetPath(
                            GetShortPathName(sc_target_path))
                    else:
                        raise
                persist_file = shortcut.QueryInterface(
                    pythoncom.IID_IPersistFile)
                persist_file.Save(sc_path, 0)
                return True
            except Exception as e:
                logger.warning(
                    "Failed to create shortcut '%s' (%s)", sc_path, e)
                return False

        # Windows does not support long paths here
        sync_dir = FilePath(sync_dir).shortpath
        # "SEND TO..." to default data directory
        create_shortcut(
            op.join(HOME_DIR, "AppData", "Roaming", "Microsoft",
                    "Windows", "SendTo", "Pvtbox.lnk"),
            sync_dir)

        # Link in favorites for default data directory
        create_shortcut(
            op.join(HOME_DIR, "Links", "Pvtbox.lnk"),
            sync_dir)

        try:
            os_version = int(get_os_version().split('.')[0])
        except Exception as e:
            logger. warning('Unexpected os version %s', get_os_version())
            os_version = 0
        if os_version >= 10:
            win10_nav_pane(sync_dir)
    elif system == 'Darwin':
        try:
            from common.finder_integration import FinderSidebar
            sidebar = FinderSidebar()
            if sidebar.get('Pvtbox') != sync_dir:
                sidebar.remove('Pvtbox')
            sidebar.add(sync_dir)
        except Exception as e:
            logger.error("Failed to create finder shortcut: %s", e)


def remove_shortcuts(dir):
    '''
    Removes shortcuts for sync directory on Windows and MacOS

    @param sync_dir Path to sync directory [unicode]
    '''

    reset_custom_folder_icon("", dir)


def win10_nav_pane(path):
    import ctypes
    from ctypes.wintypes import HANDLE
    import winreg

    is_python_32 = platform.architecture()[0].startswith('32')
    clsid = WIN10_NAV_PANE_CLSID
    subkey = 'Software\\Classes\\CLSID\\{}\\Instance\\InitPropertyBag' \
        .format(clsid)
    sam = winreg.KEY_SET_VALUE
    if is_os_64bit() and is_python_32:
        sam |= winreg.KEY_WOW64_64KEY
    hkcu = HANDLE()
    hkey = HANDLE()

    try:
        err = ctypes.windll.kernel32.RegOpenCurrentUser(
            sam, ctypes.byref(hkcu))
        if err:
            raise Exception("Open current user error {}". format(err))

        err = ctypes.windll.kernel32.RegOpenKeyExW(
                hkcu, subkey, 0, sam, ctypes.byref(hkey))
        if err:
            raise Exception("Open subkey error {}". format(err))

        buffer = ctypes.create_unicode_buffer(path)
        size = len(buffer) * 2 - 1   # very strange but works
        err = ctypes.windll.kernel32.RegSetValueExW(
            hkey, 'TargetFolderPath', 0, winreg.REG_SZ,
            buffer, size)
        if err:
            raise Exception("Set value error {}". format(err))
    except Exception as e:
        logger.warning("Can't deal with registry key. Reason: %s", e)
    finally:
        if hkey != 0:
            ctypes.windll.kernel32.RegCloseKey(hkey)


def make_dir_hidden(dir):
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(dir, 2)


def reveal_file_in_file_manager(path):
    system = get_platform()
    if system == 'Darwin':
        err = subprocess.call(['open', '-R', '{}'.format(path)])
    elif system == 'Windows':
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(
            None, 'open', 'explorer.exe',
            '/n,/select,{}'.format(
                ensure_unicode(path.replace('/', '\\'))),
            None, 1)
        err = False
    else:
        def try_open(params):
            try:
                subprocess.call(
                    params,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
                return True
            except OSError:
                return False

        err = not try_open(['nautilus', '{}'.format(path)])

    if err:
        open_path(op.dirname(path))


def license_type_constant_from_string(license_type):
    from .constants import UNKNOWN_LICENSE, FREE_LICENSE, \
        FREE_TRIAL_LICENSE, PAYED_PROFESSIONAL_LICENSE, \
        PAYED_BUSINESS_LICENSE, PAYED_BUSINESS_ADMIN_LICENSE

    try:
        return dict(
            FREE_DEFAULT=FREE_LICENSE,
            FREE_TRIAL=FREE_TRIAL_LICENSE,
            PAYED_PROFESSIONAL=PAYED_PROFESSIONAL_LICENSE,
            PAYED_BUSINESS_USER=PAYED_BUSINESS_LICENSE,
            PAYED_BUSINESS_ADMIN=PAYED_BUSINESS_ADMIN_LICENSE)[license_type]
    except KeyError:
        return UNKNOWN_LICENSE


def license_display_name_from_constant(license):
    from .constants import license_names

    return license_names[license]


@benchmark
def get_free_space(dirname):
    """ Return folder/drive free space (in bytes). """
    system = get_platform()
    if system == 'Windows':
        from common.file_path import FilePath

        dirname = ensure_unicode(dirname)
        dirname = FilePath(dirname).longpath
        free_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            ctypes.c_wchar_p(dirname), None, None, ctypes.pointer(free_bytes))
        return free_bytes.value
    else:
        dirname = ensure_unicode(dirname)
        st = os.statvfs(dirname)
        return st.f_bavail * st.f_frsize


def get_free_space_mb(dirname):
    """ Return folder/drive free space (in megabytes). """
    return get_free_space(dirname) / 1024 / 1024


def get_free_space_by_filepath(file_path):
    """ Return folder/drive free space for file_path (in bytes). """

    dirname, filename = op.split(file_path)
    return get_free_space(dirname)


def get_signature_file_size(file_size):
    """ Calculate approximate signature file size """
    block_size = SIGNATURE_BLOCK_SIZE
    blocks = int(file_size / block_size) + 1
    approx_size = ((blocks * 32) * 1.5) * 1.12  # accuracy ~ 3% for large files
    return approx_size


def get_drive_name(path):
    """
        Return drive name for given path.
        Currently working on Windows only
    """
    # ToDo make crossplatform version
    abspath = op.abspath(path)
    drive, tail = op.splitdrive(abspath)
    return drive


def get_parent_dir(path):
    try:
        parent = op.dirname(path)
    except Exception:
        parent = ""
    return parent


def is_db_or_disk_full(e):
    """
    Checks if database or disk is full in database OperationalError
    Better to check error code but it seems that sqlalchemy doesn't return
    underlying error code
    :param e: error
    :return: bool
    """
    return "database or disk is full" in str(e).lower()


def copy_file(src, dst, buffer_size=10485760, preserve_file_date=False):
    '''
    Copies a file to a new location. Much faster performance than
    Apache Commons due to use of larger buffer
    @param src:    Source File
    @param dst:    Destination File (not file path)
    @param buffer_size:    Buffer size to use during copy, default 10 Mb
    @param perserveFileDate:    Preserve the original file date
    '''

    src = ensure_unicode(src)
    dst = ensure_unicode(dst)
    file_size = op.getsize(src)

    #    Optimize the buffer for small files
    buffer_size = min(buffer_size, file_size)
    if (buffer_size == 0):
        buffer_size = 1024

    if shutil._samefile(src, dst):
        raise shutil.Error("`%s` and `%s` are the same file" % (src, dst))

    try:
        with open(src, 'rb') as fsrc:
            with open(dst, 'wb') as fdst:
                shutil.copyfileobj(fsrc, fdst, buffer_size)
    except OSError as e:
        if e.errno == errno.ENOSPC:
            try:
                os.remove(dst)
            except Exception:
                pass
        raise

    if (preserve_file_date):
        shutil.copystat(src, dst)

    os.chmod(dst, stat.S_IRWXU)


def get_relative_root_folder(relative_path):
    if not relative_path:
        return None

    return relative_path.split('/')[0]


def get_linux_de():
    if get_platform() != "Linux":
        return
    try:
        if "GNOME" in os.environ["XDG_SESSION_DESKTOP"] \
                or os.environ["GNOME_DESKTOP_SESSION_ID"]:
            return "GNOME"
    except KeyError:
        pass
    return "Unknown"


def wipe_internal(data_dir):
    from common.logging_setup import disable_file_logging
    try:
        disable_file_logging(logger)
    except Exception as e:
        logger.warning("Can't disable file logging. Reason: %s", e)
    try:
        disable_file_logging(logging.getLogger('copies_logger'))
    except Exception as e:
        logger.warning("Can't disable copies file logging. Reason: %s", e)
    try:
        remove_dir(get_patches_dir(data_dir), ignore_errors=True)
    except Exception as e:
        logger.warning("Can't wipe patches dir. Reason: %s", e)
    try:
        remove_dir(get_cfg_dir(), ignore_errors=True)
    except Exception as e:
        logger.warning("Can't wipe config dir. Reason: %s", e)
    raise SystemExit(0)


def get_max_root_len(cfg):
    system = get_platform()
    max_root_len = MAX_PATH_LEN[system]
    max_relpath_len = cfg.max_relpath_len
    max_root_len -= max_relpath_len
    assert max_root_len > 0, "Wrong max_relpath_len {}".format(max_relpath_len)

    return max_root_len


def log_sequence(seq):
    is_dict = False
    if isinstance(seq, list):
        brackets = "list({})"
    elif isinstance(seq, dict):
        is_dict = True
        brackets = "dict({})"
    elif isinstance(seq, set):
        brackets = "set({})"
    else:
        try:
            log_str = str(seq)
        except Exception:
            log_str = "?"
        return log_str

    if not is_dict:
        return brackets.format("".join(map(log_sequence, seq)))
    else:
        return brackets.format(
            "".join(["{}:{}".format(log_sequence(key), log_sequence(seq[key]))
                      for key in seq]))


secret = b'2b0780c3-37e8-41d7-9e1b-4ad26023fe71-d6ee73fc-3af6-4fb7-8c3b-9021e69a9506'


def xor_with_key(s, key=secret):
    assert isinstance(s, bytes)
    return bytes(map(xor, s, cycle(key)))


def is_first_launch():
    init_done_flag_filename = get_cfg_filename('init_done')
    return not op.exists(init_done_flag_filename)


def init_init_done():
    _make_first_launch_initialization()
    init_done_filename = get_cfg_filename('init_done')
    with open(init_done_filename, 'wb') as f:
        pickle.dump(datetime.now(), f)


def clear_init_done():
    init_done_filename = get_cfg_filename('init_done')
    with open(init_done_filename, 'wb') as f:
        pickle.dump(None, f)


def get_init_done():
    init_done_filename = get_cfg_filename('init_done')
    try:
        with open(init_done_filename, 'rb') as f:
                return pickle.load(f)
    except EOFError:
        init_init_done()
        return datetime.now()
    except Exception as e:
        logger.warning("Exception (%s) while getting init_done", e)
        clear_init_done()


def _make_first_launch_initialization():
    if get_platform() == 'Darwin' and not is_portable():
        subprocess.call(
            ['pluginkit', '-a', '/Applications/Pvtbox.app/Contents/PlugIns/PvtboxFinderSync.appex'])
        subprocess.call(
            ['pluginkit', '-a', '/Applications/Pvtbox.app/Contents/PlugIns/PvtboxShareExtension.appex'])
        subprocess.call(
            ['pluginkit', '-e', 'use', '-i', 'net.pvtbox.Pvtbox.PvtboxFinderSync'])
        subprocess.call(
            ['pluginkit', '-e', 'use', '-i', 'net.pvtbox.Pvtbox.PvtboxShareExtension'])
        subprocess.call(
            ['pluginkit', '-e', 'use', '-i', 'net.pvtbox.Pvtbox'])
        try:
            os.makedirs(op.join(HOME_DIR, 'Library', 'Services'), exist_ok=True)
            os.symlink(
                '/Applications/Pvtbox.app/Contents/Resources/Copy to Pvtbox sync folder.workflow',
                op.join(HOME_DIR, 'Library', 'Services', 'Copy to Pvtbox sync folder.workflow'))
        except:
            pass


def is_portable():
    global portable
    if portable is not None:
        return portable
    if is_launched_from_code():
        portable = False
        return portable
    from common.file_path import FilePath
    app_path = FilePath(get_application_path())
    platform = get_platform()
    if platform == 'Windows':
        portable = app_path not in FilePath(get_appdata_dir())
    elif platform == "Darwin":
        portable = app_path not in FilePath('/Applications/Pvtbox.app')
    else:
        portable = app_path not in FilePath('/opt/pvtbox')
    return portable


def get_portable_root():
    from common.file_path import FilePath
    app_path = FilePath(get_application_path())
    platform = get_platform()
    if platform == "Darwin":
        return FilePath(op.join(app_path, '..', '..', '..', '..', '..'))
    else:
        return op.dirname(app_path)


def get_tz_offset():
    millis = 1288483950000
    ts = millis * 1e-3
    # local time == (utc time + utc offset)
    utc_offset = datetime.fromtimestamp(ts) - datetime.utcfromtimestamp(ts)
    return utc_offset


def get_local_time_from_timestamp(timestamp):
    """
    Calculates local time as float seconds from epoque
    :param timestamp: utc timestamp as string "%Y-%m-%d %H:%M:%S.%f"
    or datetime timestamp
    :return: local time as float
    """
    utc_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f") \
        if isinstance(timestamp, str) \
        else timestamp if isinstance(timestamp, datetime) \
        else datetime.fromtimestamp(timestamp)
    return datetime.timestamp(utc_time + get_tz_offset())


def is_already_started():
    try:
        username = getpass.getuser().split('\\')[-1]
        if get_platform() == 'Windows':
            process_name = 'pvtbox.exe'
            pid = os.getpid()
        else:
            process_name = 'pvtbox'
            if getattr(sys, "frozen", False):
                pid = os.getppid()
            else:
                pid = os.getpid()
        for proc in psutil.process_iter():
            if proc.pid != pid and proc.pid != os.getpid() and \
                    proc.name() == process_name and \
                    proc.username().split('\\')[-1] == username:
                return True
        return False
    except Exception as e:
        logger.error(e)
        return False


def kill_all_services(timeout=3):
    try:
        username = getpass.getuser().split('\\')[-1]
        if get_platform() == 'Windows':
            process_name = 'pvtbox-service.exe'
        else:
            process_name = 'pvtbox-service'
        procs = [p for p in psutil.process_iter(
            attrs=["name", "exe", "cmdline", "username"])
                 if p.info['username'] and
                 p.info['username'].split('\\')[-1] == username and
                 (p.info['name'] == process_name or
                  p.info['exe'] and
                  op.basename(p.info['exe']) == process_name or
                  p.info['cmdline'] and
                  p.info['cmdline'][0] == process_name)]
        for p in procs:
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        if alive:
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            gone, alive = psutil.wait_procs(alive, timeout=timeout)
            if alive:
                logger.warning("Services not terminated %s", alive)
    except Exception as e:
        logger.error("Terminating services errror %s", e)


def remove_socket_file():
    if os.name == 'posix':
        socket_file_name = "/tmp/pvtbox_{}.ipc".format(os.getuid())
        try:
            remove_file(socket_file_name)
        except Exception as e:
            logger.warning("Can't remove socket file. Reason: %s", e)


def get_ipc_address():
    ipc_address = ''
    # Socket name depending on OS type
    if os.name == 'posix':
        # Address to receive messages with nanomsg
        ipc_address = 'ipc:///tmp/pvtbox_{}.ipc'.format(os.getuid()).encode()
    elif os.name == 'nt':
        # import win32api
        # username = ensure_unicode(win32api.GetUserName())

        # To prevent problems with encoding of nanomsg socket name
        username = codecs.encode(getenv('USERNAME').encode('utf-8'), 'hex_codec')
        ipc_address = 'ipc:///tmp/pvtbox_'.encode() + username + '.ipc'.encode()
    return ipc_address


@contextmanager
def cwd(path):
    oldpwd=os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(oldpwd)


def set_ext_invisible(path):
    if get_platform() != 'Darwin':
        return

    from Cocoa import NSFileManager

    NSFileManager.defaultManager().setAttributes_ofItemAtPath_error_(
        dict(NSFileExtensionHidden=True), path, None)


def delete_file_links(data_dir):
    file_paths = (p for p in get_filelist(data_dir, exclude_dirs=['.pvtbox'])
                  if p.endswith(FILE_LINK_SUFFIX))
    list(map(remove_file, file_paths))


def copy_time(src_path, dst_path):
    if not op.exists(src_path):
        return

    platform = get_platform()
    try:
        if platform == 'Darwin':
            from Cocoa import NSFileManager
            creation_date = NSFileManager.defaultManager().attributesOfItemAtPath_error_(
                src_path, None)[0]['NSFileCreationDate']
            NSFileManager.defaultManager().setAttributes_ofItemAtPath_error_(
                dict(NSFileCreationDate=creation_date), dst_path, None)
        elif platform == "Windows":
            # https://stackoverflow.com/questions/4996405/
            # how-do-i-change-the-file-creation-date-of-a-windows-file
            from ctypes import windll, wintypes, byref

            source_time = op.getctime(src_path)

            # Convert Unix timestamp to Windows FileTime using some magic numbers
            # See documentation: https://support.microsoft.com/en-us/help/167296
            timestamp = int((source_time * 10000000) + 116444736000000000)
            ctime = wintypes.FILETIME(timestamp & 0xFFFFFFFF, timestamp >> 32)

            # Call Win32 API to modify the file creation date
            wpath = ctypes.c_wchar_p(dst_path)
            handle = windll.kernel32.CreateFileW(wpath, 256, 0, None, 3, 128, None)
            windll.kernel32.SetFileTime(handle, byref(ctime), None, None)
            windll.kernel32.CloseHandle(handle)
    except Exception as e:
        logger.warning("Can't copy creation date from %s to %s. reason: %s",
                       src_path, dst_path, e)

    try:
        shutil.copystat(src_path, dst_path)
    except Exception as e:
        logger.warning("Can't copy time from %s to %s. reason: %s",
                       src_path, dst_path, e)


def add_unreg_key(key, title):
    if get_platform() != 'Windows':
        return

    import winreg
    with winreg.OpenKeyEx(
            winreg.HKEY_CURRENT_USER,
            'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\RunOnce',
            access=winreg.KEY_ALL_ACCESS) \
            as run_once_key:
        command = "reg delete {} /f".format(key)
        winreg.SetValueEx(
            run_once_key, title, 0, winreg.REG_SZ, command)


def register_smart():
    if get_platform() != 'Windows' or is_launched_from_code():
        return

    import winreg
    app_path = get_application_path() + '\\'
    pvtbox_exe = app_path + 'pvtbox.exe'
    software_classes = 'Software\\Classes\\'
    dot_pvtbox = software_classes + '.pvtbox'
    smartfile = 'net.pvtbox.SMARTFILE'
    net_pvtbox_smartfile = software_classes + smartfile
    try:
        # register smart sync entries
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, dot_pvtbox) as key:
            winreg.SetValueEx(
                key, None, 0,  winreg.REG_SZ, smartfile)
        if is_portable():
            add_unreg_key("HKCU\\" + dot_pvtbox, "Unreg .pvtbox")

        with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, net_pvtbox_smartfile) as key:
            winreg.SetValueEx(
                key, None, 0,  winreg.REG_SZ, "Pvtbox SmartSync+ file")
            winreg.SetValueEx(
                key, "NeverShowExt", 0, winreg.REG_SZ, "")
            with winreg.CreateKeyEx(key, 'DefaultIcon') as subkey:
                winreg.SetValueEx(
                    subkey, None, 0, winreg.REG_SZ,
                    app_path + "icons\\file_online.ico")
            with winreg.CreateKeyEx(key, 'shell\\open\\command') as subkey:
                winreg.SetValueEx(
                    subkey, None, 0, winreg.REG_SZ,
                    '"{}" "--offline-on" "%1"'.format(pvtbox_exe))
        if is_portable():
            add_unreg_key(
                "HKCU\\" + net_pvtbox_smartfile, "Unreg net.pvtbox.SMARTFILE")

    except Exception as e:
        logger.warning("Can't register for portable. Reason: (%s)", e)


# System locale encoding name
LOCALE_NAME, LOCALE_ENC = get_locale()

# User's home directory
HOME_DIR = expanduser("~")

portable = None
CFG_DIR = get_cfg_dir(create=False)
get_bases_dir = get_patches_dir

os_name_value = None
is_server_value = None
is_daemon = False
