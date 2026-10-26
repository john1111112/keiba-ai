@echo off
setlocal
cd /d "%~dp0\.."

rem The scheduled task must fetch fresh licensed JV-Link data, not reuse
rem yesterday's local snapshot. All alerts still pass provenance safeguards.
set "PYTHONPATH=%CD%"
set "PYTHON_EXE=python"
set "PYTHON_ARGS="
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python not found.
        exit /b 1
    )
)

"%PYTHON_EXE%" %PYTHON_ARGS% scripts\run_background_scan.py --manifest data\authorized_feed\latest_jvlink_feed.json --db data\alert_state.db --use-jvlink
exit /b %ERRORLEVEL%
