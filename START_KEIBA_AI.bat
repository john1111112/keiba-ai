@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    start "" "http://127.0.0.1:8765/"
    py -3 -m scripts.run_beta_dashboard --port 8765
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo Python 3 is not installed or is not on PATH.
        echo Install Python 3 and try again.
        pause
        exit /b 1
    )
    start "" "http://127.0.0.1:8765/"
    python -m scripts.run_beta_dashboard --port 8765
)
if %ERRORLEVEL% NEQ 0 (
    echo Dashboard stopped with an error. Check that port 8765 is free.
    pause
)
endlocal
