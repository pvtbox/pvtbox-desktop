SET VENV_PATH=virtualenv\pvtbox
SET PIP=%VENV_PATH%\Scripts\pip.exe
SET PYTHON=c:\python37\python3
%PYTHON% -m pip install virtualenv
%PYTHON% -m virtualenv --no-site-packages %VENV_PATH%
%PIP% install --upgrade -r requirements.txt
