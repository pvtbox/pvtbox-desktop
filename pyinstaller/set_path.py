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
import sys
import os
import platform
import re


def get_linux_distro_id():
    file_name = '/etc/os-release'
    distro_id = None
    try:
        with open(file_name, 'r') as f:
            s = f.read()
            res = re.search(r'ID="?(?P<distro_id>\w+)"?', s)
            if res:
                distro_id = res.group('distro_id')
    except IOError:
        pass
    return distro_id


if platform.system() == 'Linux':
    arch_id = platform.architecture()[0]
    distro_id = get_linux_distro_id()

    if distro_id in ('ubuntu', 'debian'):
        pass

    elif distro_id in ('centos', 'rhel', 'fedora'):
        pass

    elif distro_id in ('opensuse', 'suse'):
        if arch_id == "64bit":
            sys.path.append('/usr/lib64/python3.6/site-packages')
        sys.path.append('/usr/lib/python3.6/site-packages')

    else:
        pass

if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

if sys.platform == 'darwin':
    application_path = os.path.dirname(application_path)
    application_path = os.path.join(application_path, 'Frameworks')

sys.path.append(application_path)
os.environ['PATH'] = application_path + os.pathsep + os.environ.get('PATH', '')
os.environ['PYTHONPATH'] = \
    application_path + os.pathsep + os.environ.get('PYTHONPATH', '')

if os.name == 'nt':
    import win32api
    win32api.SetDllDirectory(application_path)


try:
    from PySide2.QtCore import QCoreApplication
    QCoreApplication.addLibraryPath(application_path)
except:
    pass
