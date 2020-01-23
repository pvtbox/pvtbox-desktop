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
import os
from webrtc import WebRtc

from common.application import Application
from __version import __version__
from service.service_impl import ApplicationService
from common.logging_setup import logging_setup
from common.utils import get_platform

webrtc_loglevels = (
    'SENSITIVE', 'VERBOSE', 'INFO', 'WARNING', 'ERROR', 'NONE')


def parseArgs(argv=sys.argv[1:]):
    '''
    Parses command line arguments
    Returns parsed argument names and values in form {name: value}

    @return Returns parsed argument names and values [dict]
    '''

    # Possible values for -v option
    LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
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
        '-w', '--webrtc-loglevel', default=webrtc_loglevels[2], type=str,
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
    parser.add_argument(
        '--sync-directory', type=str,
    )
    parser.add_argument(
        '--logging-disabled', type=str,
    )

    # Parse command line args and return as dict
    args = vars(parser.parse_args(argv))

    return args


if __name__ == "__main__":
    # Get process ID
    if get_platform() == "Windows":
        pid = os.getpid()
    else:
        if getattr(sys, "frozen", False):
            pid = os.getppid()
        else:
            pid = os.getpid()
    #fh.set_process_id(pid)

    # for multiprocessing under build pyinstaller
    multiprocessing.freeze_support()

    args = sys.argv[1:]
    # Parse command line arguments
    args = parseArgs(args)

    if not 'logging_disabled' in args or not args['logging_disabled']:
        # As side-effect creates cfg dir here
        try:
            logging_setup(loglevel=args['loglevel'])
        except FileNotFoundError as e:
            print("Error. Config not found.")
            raise SystemExit(0)

        # Set webrtc loglevel
        loglevel = getattr(WebRtc, args['webrtc_loglevel'])
        WebRtc.set_log_level(loglevel)
    # To terminate from console with Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    Application.set_instance_class(ApplicationService)
    Application.start(args)

    print('Exited')
