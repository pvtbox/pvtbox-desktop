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
import webbrowser
import os.path as op

from common.application import Application
from .signals import signals
from .share_path import share_paths, cancel_sharing,  get_relpath, is_folder, \
    update_sync_status, link_copy_success
from .copy_path import queue_copying
from .collaboration_settings import collaboration_path_settings
from .file_info import file_info
from service.shell_integration import params
from urllib.parse import quote
from common.translator import tr

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def show_copying_failed(path):
    '''
    Shows message when copying of file/dir into sync directory failed

    @param path Filesystem path [unicode]
    '''

    name = op.basename(path)
    Application.show_tray_notification(
        tr("Failed to copy '{}' into sync directory").format(name),
        tr("Shell"))


def share_path_slot(paths):
    '''
    Processes 'share_path' shell command

    @param paths Filesystem paths [list]
    '''

    # Request path sharing
    share_paths(paths, _on_share_paths_cb, save_to_clipboard=True)


def _on_share_paths_cb(paths, share_links, error_info='', move=False,
                       save_to_clipboard=True, context=''):
    '''
    Callback to be called after
    processing of 'share_path' shell command

    @param paths Filesystem paths [list]
    @param share_links Links URLs [list] or None if links getting failed
    '''

    if share_links:
        if save_to_clipboard:
            Application.show_tray_notification(
                tr("URL(s) for downloading copied to clipboard"),
                tr("Sharing"))
        else:
            params.ipc_ws_server.on_paths_links(
                paths, share_links, context, move)
    elif paths:
        try:
            share_names = list(map(get_relpath, paths))
            signals.share_path_failed.emit(share_names)
        except Exception as e:
            logger.warning(
                "on_share_paths_cb, share_path_failed exception: %s", e)

        Application.show_tray_notification(
            tr("Sharing {} file(s) failed: {}").format(
                len(paths), error_info),
            tr("Sharing"))


def _on_open_link_cb(path, share_link):
    '''
    Callback to be called after
    processing 'open_link' shell command

    @param path Filesystem path [unicode]
    @param share_link Link URL [unicode] or None if link getting failed
    '''

    # Open URL in the web browser (if any)
    if share_link:
        webbrowser.open_new(share_link)
    else:
        name = op.basename(path)
        Application.show_tray_notification(
            tr("Failed to share file: {}").format(name),
            tr("Sharing"))


def copy_to_sync_dir_slot(paths):
    '''
    Processes 'copy_to_sync_dir' shell command

    @param path Filesystem path [unicode]
    '''

    # Queue file/dir copying
    queue_copying(paths)


def share_copy_slot(paths, context):
    queue_copying(paths, False, lambda p: share_paths(
        p, _on_share_paths_cb, save_to_clipboard=not context,
        context=context, move=False))


def share_move_slot(paths, context):
    queue_copying(paths, True, lambda p: share_paths(
        p, _on_share_paths_cb, save_to_clipboard=not context,
        context=context, move=True))


def email_link_slot(paths):
    '''
    Processes 'email_link' shell command

    @param path Filesystem path [unicode]
    '''

    # Request path sharing
    share_paths(paths, _on_email_link_cb)


def _on_email_link_cb(paths, share_links, error_info=''):
    '''
    Processes 'email_link' shell command

    @param path Filesystem path [unicode]
    @param share_link Link URL [unicode] or None if link getting failed
    '''

    # Pass share link to default mail client as 'mailto' protocol (if any)
    if share_links:
        subject = "Link for shared files/folders by Pvtbox"
        body = "Shared by Pvtbox:\r\n"
        for path, link in zip(paths, share_links):
            _, share_name = get_relpath(path)
            body += "{} - {}\r\n".format(share_name, link)
        mailto_url = "mailto:?subject={}&body={}" \
            .format(quote(subject.encode("utf-8")),
                    quote(body.encode("utf-8")))
        webbrowser.open_new(mailto_url)
    else:
        share_names = list(map(get_relpath, paths))
        signals.share_path_failed.emit(share_names)
        Application.show_tray_notification(
            tr("Failed to share {} file(s)").format(len(paths)),
            tr("Sharing"))


def block_path_slot(paths):
    '''
    Callback to be called after
    processing 'block_path' shell command

    @param path Filesystem path [unicode]
    '''

    # Request share cancelling
    if cancel_sharing(paths):
        Application.show_tray_notification(
            tr("Sharing cancelled"),
            tr("Sharing"))
    else:
        Application.show_tray_notification(
            tr("Failed to cancel path sharing"),
            tr("Sharing"))


def collaboration_settings_slot(paths):
    '''
    Processes 'collaboration_settings' shell command

    @param paths Filesystem paths [list]
    '''

    # Request opening fo collaboration settings dialog
    collaboration_path_settings(paths)


def file_info_slot(uuids, context):
    '''
    Processes 'file_info' shell command

    @param uuids file uuids (1 element list) [list]
    '''

    file_info(uuids, context)


def connect_slots():
    '''
    Connectes slots to shell commands signals
    '''

    # Shell commands handlers
    signals.share_path.connect(share_path_slot)
    signals.copy_to_sync_dir.connect(copy_to_sync_dir_slot)
    signals.share_copy.connect(share_copy_slot)
    signals.share_move.connect(share_move_slot)
    signals.email_link.connect(email_link_slot)
    signals.block_path.connect(block_path_slot)
    signals.collaboration_settings.connect(collaboration_settings_slot)
    signals.file_info.connect(file_info_slot)

    # User messages
    signals.copying_failed.connect(show_copying_failed)

    # Share path
    signals.sync_status_changed.connect(update_sync_status)
    signals.is_saved_to_clipboard.connect(link_copy_success)

    signals.file_info_reply.connect(params.ipc_ws_server.on_file_info)


def disconnect_slots():
    '''
    Disconnectes slots to shell commands signals
    '''

    # Shell commands handlers
    signals.share_path.disconnect(share_path_slot)
    signals.copy_to_sync_dir.disconnect(copy_to_sync_dir_slot)
    signals.share_copy.disconnect(share_copy_slot)
    signals.share_move.disconnect(share_move_slot)
    signals.email_link.disconnect(email_link_slot)
    signals.block_path.disconnect(block_path_slot)
    signals.collaboration_settings.disconnect(collaboration_settings_slot)
    signals.file_info.disconnect(file_info_slot)

    # User messages
    signals.copying_failed.disconnect(show_copying_failed)

    # Share path
    signals.sync_status_changed.disconnect(update_sync_status)
    signals.is_saved_to_clipboard.disconnect(link_copy_success)

    signals.file_info_reply.disconnect(params.ipc_ws_server.on_file_info)
