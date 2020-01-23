# -*- mode: python -*-

a = Analysis(['../../serv.py'],
             pathex=['pyinstaller/win32'],
             binaries=None,
             datas=[('../../db_migrations/events_db', 'db_migrations/events_db'),
                    ('../../db_migrations/stats_db', 'db_migrations/stats_db'),
                    ('../../db_migrations/patches_db', 'db_migrations/patches_db'),
                    ('../../db_migrations/storage_db', 'db_migrations/storage_db'),
                    ('../../db_migrations/copies_db', 'db_migrations/copies_db'),
					],
             hiddenimports=['_nanomsg_ctypes', 'sqlalchemy.ext.baked', 'wmi'],
             hookspath=[],
             runtime_hooks=['pyinstaller/set_path.py'],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False)

# Add libraries manually
a.binaries.extend((
    ('sip.pyd', os.path.abspath('so\\sip.pyd'), 'BINARY'),
    ('webrtc.pyd', os.path.abspath('so\\webrtc.pyd'), 'BINARY'),
    ('nanomsg.dll', os.path.abspath('so\\nanomsg.dll'), 'BINARY'),
    ))

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(pyz,
          a.scripts,
          name='pvtbox-service',
          exclude_binaries=True,
          debug=False,
          strip=False,
          onefile=False,
          onedir=True,
          windowed=False,
          upx=True,
          console=False ,
          icon=os.path.abspath('application\\ui\\images\\logo.ico'),
          version=os.path.abspath('service_version.py'),
          )

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               name=os.path.join('dist', 'service'))
