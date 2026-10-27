@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONPATH=%CD%"

rem Prefer the isolated project environment built by INSTALL_AUTO_MONITOR.bat.
rem Do not silently run a different global Python missing LightGBM/pywin32.
if exist "%CD%\.venv\Scripts\python.exe" (
    "%CD%\.venv\Scripts\python.exe" scripts\run_background_scan.py --manifest data\authorized_feed\latest_jvlink_feed.json --db data\alert_state.db --use-jvlink
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 scripts\run_background_scan.py --manifest data\authorized_feed\latest_jvlink_feed.json --db data\alert_state.db --use-jvlink
    exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python scripts\run_background_scan.py --manifest data\authorized_feed\latest_jvlink_feed.json --db data\alert_state.db --use-jvlink
    exit /b %ERRORLEVEL%
)
echo [ERROR] Python 3 environment not found.
exit /b 1
