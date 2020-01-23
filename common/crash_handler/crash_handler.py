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
def init_crash_handler(tracker=None, logger=None):
    import sys
    import traceback
    import threading

    excepthook_old = sys.excepthook

    def crash_handler(type, value, tb):
        try:
            extracted_tb = traceback.extract_tb(tb)
            tb_list = traceback.format_list(extracted_tb)
            tb_exception = traceback.format_exception_only(type, value)
            if tracker:
                tracker.crash(tb_list, tb_exception)
            elif logger:
                logger.error("Traceback: %s, Exception: %s",
                             ''.join(tb_list), ''.join(tb_exception))
            else:
                raise Exception("Error while processing crash")
        except Exception as e:
            print("Crash handler exception:", e)
        excepthook_old(type, value, tb)

    sys.excepthook = crash_handler

    def install_thread_excepthook():
        """
        Workaround for sys.excepthook thread bug
        (https://sourceforge.net/tracker/?func=detail&atid=105470&aid=1230540&group_id=5470).
        Call once from __main__ before creating any threads.
        If using psyco, call psycho.cannotcompile(threading.Thread.run)
        since this replaces a new-style class method.
        """
        run_old = threading.Thread.run

        def run(*args, **kwargs):
            try:
                run_old(*args, **kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except:
                crash_handler(*sys.exc_info())

        threading.Thread.run = run

    install_thread_excepthook()
