#!/bin/sh

VERSION=`python3 -c "import __version; print(__version.__version__)"`
VERSION_TUPLE=`python3 -c "import __version; print('({})'.format(','.join(__version.__version__.split('.'))))"`

sed "s/\${VERSION}/${VERSION}/" __win_version.py | sed "s/\${VERSION_TUPLE}/${VERSION_TUPLE}/" | sed "s/\${APPLICATION}/application/" | sed "s/\${INTERNAL_NAME}/pvtbox/" | sed "s/\${EXECUTABLE_NAME}/pvtbox.exe/" > app_version.py
sed "s/\${VERSION}/${VERSION}/" __win_version.py | sed "s/\${VERSION_TUPLE}/${VERSION_TUPLE}/" | sed "s/\${APPLICATION}/service/" | sed "s/\${INTERNAL_NAME}/pvtbox-service/" | sed "s/\${EXECUTABLE_NAME}/pvtbox-service.exe/" > service_version.py
