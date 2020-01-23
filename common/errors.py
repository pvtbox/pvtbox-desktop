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
import logging
import traceback
import sys

from common.application import Application

# logger = logging.getLogger(__name__)
# logger.addHandler(logging.NullHandler())


class ExpectedError(RuntimeError):
    """This class should be mixed to exception types which is expected
    and should not generate call stack trace"""
    def __init__(self, message):
        super(ExpectedError, self).__init__(message)


class EventConflicted(ExpectedError):
    def __init__(self):
        super(EventConflicted, self).__init__(
            "Event has conflicting event in the database.")


class EventAlreadyAdded(ExpectedError):
    def __init__(self):
        super(EventAlreadyAdded, self).__init__(
            "Event with same id already added to database.")


def handle_exception(message, *args):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    file_name, line_number, module, function_name = traceback.extract_tb(
        exc_traceback,
        1)[0]

    logger = logging.getLogger(module)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())

    if not issubclass(exc_type, ExpectedError):
        logger.critical(message,
                        *args,
                        exc_info=True)
        return

    args = args + (exc_type.__name__, exc_value, function_name, line_number)
    logger.error(
        "{}\n%s: '%s' in %s:%s".format(message),
        *args)


def handle_critical_exception(message, *args):
    handle_exception(message, *args)
    Application.exit()
