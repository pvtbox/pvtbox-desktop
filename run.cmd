SET VENV_PATH=%CD%\virtualenv\pvtbox
SET PYTHON=%VENV_PATH%\Scripts\python.exe
SET PYTHONPATH=so
SET PATH=;%VENV_PATH%\Scripts;%PATH%;so
%PYTHON% app.py -l DEBUG %*
