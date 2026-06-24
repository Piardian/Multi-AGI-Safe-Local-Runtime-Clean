@echo off
chcp 65001 > nul
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" bridge.py %*
  pause
  exit /b %ERRORLEVEL%
)

set "PY_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if exist "%PY_EXE%" (
  "%PY_EXE%" bridge.py %*
  pause
  exit /b %ERRORLEVEL%
)

where py > nul 2> nul
if %ERRORLEVEL%==0 (
  py bridge.py %*
  pause
  exit /b %ERRORLEVEL%
)

where python > nul 2> nul
if %ERRORLEVEL%==0 (
  python bridge.py %*
  pause
  exit /b %ERRORLEVEL%
)

echo Python bulunamadi. Python 3.11+ kurun veya PATH'e ekleyin.
pause
exit /b 9009
