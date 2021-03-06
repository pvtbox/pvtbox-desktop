# -*- mode: python -*-
from os import path
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

if get_linux_distro_id() == "opensuse":
    excludes=[
        'tracking',
    ]
else:
    excludes=[
        'tracking',
    ]

a = Analysis(['../../app.py'],
             pathex=['./pyinstaller/linux'],
             binaries=None,
             hiddenimports=['_nanomsg_ctypes'],
             hookspath=[],
             runtime_hooks=['pyinstaller/set_path.py'],
             excludes=excludes,
             win_no_prefer_redirects=False,
             win_private_assemblies=False)

# Add libraries manually
a.binaries.extend((
    ('libnanomsg.so', os.path.abspath('so/libnanomsg.so.5.1.0'), 'BINARY'),
    ('sip.so', os.path.abspath('so/sip.so'), 'BINARY'),
    ))

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(pyz,
          a.scripts,
          name='pvtbox',
          exclude_binaries=True,
          debug=False,
          strip=True,
          onefile=False,
          onedir=True,
          upx=True,
          console=True )

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=True,
               upx=True,
               name=os.path.join('dist', 'app'))
