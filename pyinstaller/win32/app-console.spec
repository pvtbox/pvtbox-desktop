# -*- mode: python -*-

a = Analysis(['../../app.py'],
             pathex=['pyinstaller/win32'],
             binaries=None,
             hiddenimports=['_nanomsg_ctypes'],
             hookspath=[],
             runtime_hooks=['pyinstaller/set_path.py'],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False)

# Add libraries manually
a.binaries.extend((
    ('sip.pyd', os.path.abspath('so\\sip.pyd'), 'BINARY'),
    ('nanomsg.dll', os.path.abspath('so\\nanomsg.dll'), 'BINARY'),
    ))

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(pyz,
          a.scripts,
          name='pvtbox-console',
          exclude_binaries=True,
          debug=True,
          strip=False,
          onefile=False,
          onedir=True,
          upx=True,
          console=True ,
          icon=os.path.abspath('application\\ui\\images\\logo.ico'),
          )

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name=os.path.join('dist', 'app-console'))
