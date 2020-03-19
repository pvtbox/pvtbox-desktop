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
import signal

from common.logging_setup import VerboseLogger
from logging import setLoggerClass
# set custom logger class for VERBOSE log level
setLoggerClass(VerboseLogger)

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
    # Program description printed on -h option
    DESC = '''
    Pvtbox console daemon
    '''

    # Setup parser
    parser = argparse.ArgumentParser(
        description=DESC,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--host', type=str,
        help='Pvtbox server address'
    )
    parser.add_argument(
        '--email', type=str,
        help='Account email'
    )
    parser.add_argument(
        '--password', type=str,
        help='Account password'
    )
    parser.add_argument(
        '-l', '--loglevel', default='DEBUG', type=str, choices=LOG_LEVELS,
        help='Debug output verbosity (default is %(default)s)'
             .format(",".join(LOG_LEVELS)))
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
        '--wipe-internal', type=str,
    )

    # Parse command line args and return as dict
    # args = vars(parser.parse_args(argv))
    namespace, unrecognized = parser.parse_known_args(argv)
    if unrecognized and len(unrecognized) == len(argv):
        # argv possibly contains paths to copy to sync dir
        new_argv = []
        for arg in argv:
            new_argv.append('--copy')
            new_argv.append(arg)
            sys.argv = sys.argv[0: 1] + new_argv
        args = vars(parser.parse_args(new_argv))
    else:
        args = vars(namespace)

    return args


if __name__ == "__main__":
    # for multiprocessing under build pyinstaller
    multiprocessing.freeze_support()

    from common import utils

    utils.is_daemon = True
    utils.get_cfg_dir(create=True)
    utils.get_patches_dir(utils.get_data_dir(create=True), create=True)

    from common.application import Application
    from daemon.application_impl import ApplicationImpl

    args = sys.argv[1:]
    # Parse command line arguments
    args = parseArgs(args)

    # To terminate from console with Ctrl+C
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    Application.set_instance_class(ApplicationImpl)
    Application.start(args)

    print('Exited')
