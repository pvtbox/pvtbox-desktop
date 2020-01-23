# -*- mode: python -*-

with open('__version.py', 'rb') as f:
    exec(f.readline())

a = Analysis(['../../app.py'],
             binaries=None,
             hiddenimports=['_nanomsg_ctypes', 'Cocoa'],
             hookspath=[],
             runtime_hooks=['pyinstaller/set_path.py'],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False)

# Add libraries manually
a.binaries.extend((
    ('libnanomsg.dylib', os.path.abspath('so/libnanomsg.5.1.0.dylib'), 'BINARY'),
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
          console=False,
          windowed=True,
          icon='gui/ui/images/logo.icns',
          )

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=True,
               upx=True,
               name=os.path.join('dist', 'app'))

b = BUNDLE(coll,
           name=os.path.join('dist', 'app.app'))
