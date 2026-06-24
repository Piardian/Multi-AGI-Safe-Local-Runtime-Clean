@echo off
chcp 65001 > nul
cd /d "%~dp0"
rem Experimental/lab-only worker. It is intentionally dry-run only and is not
rem part of the official local technician runtime.
set "EXPERIMENTAL_QUANT_WORKER=true"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" orchestrator.py --dry-run %*
  pause
  exit /b %ERRORLEVEL%
)

set "PY_EXE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if exist "%PY_EXE%" (
  "%PY_EXE%" orchestrator.py --dry-run %*
  pause
  exit /b %ERRORLEVEL%
)

where py > nul 2> nul
if %ERRORLEVEL%==0 (
  py orchestrator.py --dry-run %*
  pause
  exit /b %ERRORLEVEL%
)

where python > nul 2> nul
if %ERRORLEVEL%==0 (
  python orchestrator.py --dry-run %*
  pause
  exit /b %ERRORLEVEL%
)

echo Python bulunamadi. Python 3.11+ kurun veya PATH'e ekleyin.
pause
exit /b 9009
