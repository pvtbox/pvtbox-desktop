# -*- mode: python -*-

with open('__version.py', 'rb') as f:
    exec(f.readline())

a = Analysis(['../../serv.py'],
             binaries=None,
             datas=[('../../db_migrations/events_db', 'db_migrations/events_db'),
                    ('../../db_migrations/stats_db', 'db_migrations/stats_db'),
                    ('../../db_migrations/patches_db', 'db_migrations/patches_db'),
                    ('../../db_migrations/storage_db', 'db_migrations/storage_db'),
                    ('../../db_migrations/copies_db', 'db_migrations/copies_db'),
					],
             hiddenimports=['_nanomsg_ctypes', 'Cocoa', 'sqlalchemy.ext.baked'],
             hookspath=[],
             runtime_hooks=['pyinstaller/set_path.py'],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False)

# Add libraries manually
a.binaries.extend((
    ('libnanomsg.dylib', os.path.abspath('so/libnanomsg.5.1.0.dylib'), 'BINARY'),
    ('sip.so', os.path.abspath('so/sip.so'), 'BINARY'),
    ('webrtc.so', os.path.abspath('so/webrtc.so'), 'BINARY'),
    ))

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(pyz,
          a.scripts,
          name='pvtbox-service',
          exclude_binaries=True,
          debug=False,
          strip=True,
          onefile=False,
          onedir=True,
          upx=True,
          console=True,
          windowed=False,
          )

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=True,
               upx=True,
               name=os.path.join('dist', 'service'))

b = BUNDLE(coll,
           name=os.path.join('dist', 'service.app'))
