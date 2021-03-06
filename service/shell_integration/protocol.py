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
import json
from os.path import join

from common.utils import ensure_unicode
from common.file_path import FilePath
from service.shell_integration import params
from .signals import signals
from .share_path import is_paths_shared
from .offline_path import get_offline_status


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


# Commands allowed in OS shell extension messages
ALLOWED_COMMANDS = (
    'copy_to_sync_dir', 'share_path', 'email_link', 'open_link', 'block_path',
    'sync_dir', 'download_link', 'show', 'is_shared', 'wipe_internal',
    'status_subscribe', 'status_unsubscribe', 'refresh',
    'share_copy', 'share_move', 'collaboration_settings', 'file_info',
    'offline_on', 'offline_off', 'offline_status', 'smart_sync'
)

FILE_NOT_FOUND = 0
FILE_DELETED = 1
FILE_EXCLUDED = 2
INVALID_JSON = 3
FILE_PATH_ERRORS = {
    FILE_NOT_FOUND: "FILE_NOT_FOUND",
    FILE_DELETED: "FILE_DELETED",
    FILE_EXCLUDED: "FILE_EXCLUDED",
    INVALID_JSON: "INVALID_JSON",
}


def parse_message(encoded):
    '''
    Parses JSON-encoded message received from OS shell extension

    @param encoded JSON encoded message [string]
    @return Parsed message data in the form (command, path, link) [tuple]
    @raise ValueError
    @raise KeyError
    '''

    # Decode message from json format
    try:
        decoded = json.loads(encoded)
    except ValueError as e:
        logger.error("Failed to decode message: '%s' (%s)", encoded, e)
        raise

    # Unpack message
    try:
        cmd = decoded['cmd']
        path = decoded.get('path', None)
        link = decoded.get('link', None)
        paths = decoded.get('paths', None)
        context = decoded.get('context', '')
    except KeyError as e:
        logger.error("Wrong format of message: '%s' (%s)", encoded, e)
        raise

    logger.info(
        "Received OS shell command '%s' for path '%s', link '%s', "
        "paths '%s', context '%s'",
        cmd, path, link, paths, context)

    # Validate message
    if cmd not in ALLOWED_COMMANDS:
        logger.error("Invalid command specified '%s'", cmd)
        raise ValueError("Invalid command")

    return cmd, path, link, paths, context


def emit_signal(cmd, *args):
    '''
    Emits signal corresponding to command received from OS shell extension

    @param cmd Command name [string]
    @param *args Signal arguments
    '''

    # Find signal corresponding to the command
    signal = getattr(signals, cmd, None)

    # Signal found
    if signal is not None:
        signal.emit(*args)
    else:
        logger.error(
            "Not signal defined for command '%s'", cmd)


def create_command(cmd, path=None):
    '''
    Creates protocol command using data specified

    @param cmd Command name [string]
    @param path Filesystem path [unicode] or None

    @return JSON encoded protocol command
    '''

    cmd = {"cmd": cmd}
    if path is not None:
        cmd['path'] = path

    return json.dumps(cmd)


def get_shared_reply():
    paths = params.get_shared_paths_func()
    if paths is None:
        return None

    paths = list(map(lambda path: FilePath(ensure_unicode(
        join(params.cfg.sync_directory, path))).longpath, paths))

    cmd = dict(cmd="shared", paths=paths)
    return json.dumps(cmd)


def get_files_status_reply(paths, status):
    cmd = dict(cmd="status", status=status, paths=paths)
    return json.dumps(cmd)


def get_clear_path_reply(path):
    cmd = dict(cmd="clear", path=path)
    return json.dumps(cmd)


def get_file_info_reply(path, error, context):
    cmd = dict(cmd="file_info", path=path, error=error, context=context)
    return json.dumps(cmd)


def get_sync_dir_reply():
    '''
    Creates reply for 'sync_dir' protocol command containing path to
    program sync directory

    @return JSON encoded protocol command
    '''
    # ToDo change shortpath to longpath after context menu changing
    return create_command('sync_dir',
                          FilePath(params.cfg.sync_directory).shortpath)


def get_is_sharing_reply(paths):
    '''
    Creates reply for 'is_sharing' protocol command containing paths

    @return JSON encoded protocol command
    '''
    is_shared_str = 'true' if is_paths_shared(paths) else ''
    return create_command('is_shared', is_shared_str)


def get_share_copy_move_reply(paths, links, context, move=False):
    command = "share_move" if move else "share_copy"
    cmd = dict(cmd=command, paths=paths, links=links, context=context)
    return json.dumps(cmd)


def get_offline_status_reply(paths):
    offline_status = get_offline_status(paths)
    offline_status_str = "offline" if offline_status == 1 \
        else "online" if offline_status == 0 \
        else "no_smart_sync"
    return create_command('offline_status', offline_status_str)


def get_smart_sync_reply():
    cmd = dict(cmd='smart_sync', enabled=params.cfg.smart_sync)
    return json.dumps(cmd)
