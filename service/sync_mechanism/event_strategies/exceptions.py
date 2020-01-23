# -*- coding: utf-8 -*-#

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
from common.errors import ExpectedError, EventAlreadyAdded, EventConflicted


class UnknowEventTypeException(RuntimeError):
    def __init__(self, event_type):
        super(UnknowEventTypeException, self).__init__(
            "Unknown type of event is received '{}'".format(event_type))


class UnknownEventState(RuntimeError):
    def __init__(self, state, event):
        super(UnknownEventState, self).__init__(
            "Unknown event status '{}'. event: ".format(state, event))


class EmptyWebServerResponceException(RuntimeError):
    def __init__(self):
        super(EmptyWebServerResponceException, self).__init__(
            "Webserver is return empty responce.")


class SkipEventForNow(ExpectedError):
    def __init__(self):
        super(SkipEventForNow, self).__init__(
            "Will try to process event later.")


class SkipExcludedMove(ExpectedError):
    def __init__(self):
        super(SkipExcludedMove, self).__init__(
            "Will try to process event after local childs processed.")


class FolderUUIDNotFound(ExpectedError):
    def __init__(self, uuid):
        super(FolderUUIDNotFound, self).__init__(
            "Folder with UUID '{}' not found in the database."
            .format(uuid))


class ProcessingAborted(ExpectedError):
    def __init__(self):
        super(ProcessingAborted, self).__init__(
            "Processing of events queue aborted.")


class RenameDstPathFailed(ExpectedError):
    def __init__(self):
        super(RenameDstPathFailed, self).__init__(
            "Can't rename or delete dst path.")


class ParentDeleted(ExpectedError):
    def __init__(self):
        super(ParentDeleted, self).__init__(
            "Parent is deleted")
