#!/usr/bin/env python
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
from common.logging_setup import VerboseLogger
from logging import setLoggerClass
# set custom logger class for VERBOSE log level
setLoggerClass(VerboseLogger)

import signal
import argparse
import sys
import multiprocessing

from __version import __version__


def parseArgs(argv=sys.argv[1:]):
    '''
    Parses command line arguments
    Returns parsed argument names and values in form {name: value}

    @return Returns parsed argument names and values [dict]
    '''

    # Possible values for -v option
    LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
    webrtc_loglevels = (
        'SENSITIVE', 'VERBOSE', 'INFO', 'WARNING', 'ERROR', 'NONE')
    # Program description printed on -h option
    DESC = '''
    P2P file sync program
    '''

    # Setup parser
    parser = argparse.ArgumentParser(
        description=DESC,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '-l', '--loglevel', default='DEBUG', type=str, choices=LOG_LEVELS,
        help='Debug output verbosity (default is %(default)s)'
             .format(",".join(LOG_LEVELS)))
    parser.add_argument(
        '-w', '--webrtc-loglevel', default=webrtc_loglevels[-1], type=str,
        choices=webrtc_loglevels,
        help='webrtc debug output verbosity (default is %(default)s)'
             .format(",".join(webrtc_loglevels)))
    parser.add_argument(
        '-v', '--version', action='version',
        version='%(prog)s {}'.format(__version__)
    )
    parser.add_argument(
        '--download-link', type=str,
        help='download content specified by share link'
    )
    parser.add_argument(
        '--copy', action="append",
        help='copy content specified by path to sync directory'
    )
    parser.add_argument(
        '--update-branch', type=str,
        help='update branch for application'
    )

    parser.add_argument(
        '--wipe-internal', type=str,
    )

    # Parse command line args and return as dict
    args = vars(parser.parse_args(argv))

    return args


if __name__ == "__main__":
    # for multiprocessing under build pyinstaller
    multiprocessing.freeze_support()

    from common import utils

    utils.get_cfg_dir(create=True)

    from common.application import Application
    from application.application_impl import ApplicationImpl
    from common.logging_setup import logging_setup

    args = sys.argv[1:]
    # Parse command line arguments
    args = parseArgs(args)

    from application.utils import check_sync_folder_removed, logging_enabled

    if not check_sync_folder_removed():
        if logging_enabled():
            # As side-effect creates cfg dir here
            logging_setup(loglevel=args['loglevel'], copies_logging=False)
        else:
            args['logging_disabled'] = True
    else:
        args['sync_folder_removed'] = True

    # To terminate from console with Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    Application.set_instance_class(ApplicationImpl)
    Application.start(args)

    print('Exited')
