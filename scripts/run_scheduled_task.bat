@echo off
setlocal
cd /d "%~dp0\.."

set "PYTHONPATH=%CD%"
set "PYTHON_EXE=python"
if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
) else (
    where py >nul 2>&1 && set "PYTHON_EXE=py"
)

"%PYTHON_EXE%" scripts\run_background_scan.py --manifest data\authorized_feed\latest.json --db data\alert_state.db
exit /b %ERRORLEVEL%
