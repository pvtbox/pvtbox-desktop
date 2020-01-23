SET VENV_PATH=virtualenv\pvtbox
SET INSTALLER=%VENV_PATH%\Scripts\pyinstaller.exe
SET PYTHONPATH=so

call build_resources

bash build_version_files.sh

%INSTALLER% --clean --log-level WARN --noconfirm -F --key=7h3SeCr37key --workpath=build/win32 --distpath=dist/win32 pyinstaller/win32/app-console.spec
%INSTALLER% --clean --log-level WARN --noconfirm -F --key=7h3SeCr37key --workpath=build/win32 --distpath=dist/win32 pyinstaller/win32/app.spec
%INSTALLER% --clean --log-level WARN --noconfirm -F --key=7h3SeCr37key --workpath=build/win32 --distpath=dist/win32 pyinstaller/win32/service-console.spec
%INSTALLER% --clean --log-level WARN --noconfirm -F --key=7h3SeCr37key --workpath=build/win32 --distpath=dist/win32 pyinstaller/win32/service.spec

