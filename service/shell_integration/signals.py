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

# Setup logging
from PySide2.QtCore import Signal, QObject

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ShellIntegrationSignals(QObject):
    '''
    Contains signals definition for the package
    '''

    share_copy = Signal(list, str)
    share_move = Signal(list, str)

    # Signal to be emitted on shell command 'copy_to_sync_dir'
    # Arguments are: paths [list]
    copy_to_sync_dir = Signal(list)

    # Signal to be emitted on shell command 'share_path'
    # Arguments are: path [unicode]
    share_path = Signal(list)

    share_path_failed = Signal(list)

    # Signal to be emitted on shell command 'email_link'
    # Arguments are: path [unicode]
    email_link = Signal(list)

    # Signal to be emitted on shell command 'open_link'
    # Arguments are: path [unicode]
    open_link = Signal(str)

    # Signal to be emitted on shell command 'block_path'
    # Arguments are: path [unicode]
    block_path = Signal(list)

    # Signal to be emitted when started copying file/dir into sync dir
    # Arguments are: path [unicode]
    copying_started = Signal(str)

    # Signal to be emitted when finished copying file/dir into sync dir
    # Arguments are: path [unicode]
    copying_finished = Signal(str)

    # Signal to be emitted when failed copying file/dir into sync dir
    # Arguments are: path [unicode]
    copying_failed = Signal(str)

    # Signal to be emitted when obtained share link to be downloaded
    # Arguments are: share_link [str]
    download_link = Signal(str)

    # Signal to be emitted when obtained show command
    show = Signal()

    wipe_internal = Signal()

    sync_status_changed = Signal(int, int, int, int, int, int)

    is_saved_to_clipboard = Signal(bool)

    status_subscribe = Signal(str, str)
    status_unsubscribe = Signal(str, str)

    def __init__(self, parent=None):
        QObject.__init__(self, parent=parent)


signals = ShellIntegrationSignals()
